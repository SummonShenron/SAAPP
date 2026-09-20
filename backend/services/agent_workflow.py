from __future__ import annotations
import asyncio
import base64
import os
import re
import uuid
import json
from typing import List, Any, Dict, Optional
import logging
import requests
import erragent
import urllib.parse
from functools import partial
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from backend.components.time_storage import load_user_time
from backend.models.attachment import Attachment
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.documents import Document
from langchain_core.callbacks.manager import adispatch_custom_event
from settings import PAAPP_BASE_URL
from backend.services.search import get_secure_retriever
from backend.models.models import get_chat_llm, lite_llm, lite_llm_deep
from backend.state import graph_db
from backend.utils.attachment_utils import retrieve_from_session
from backend.utils.isolation_kb_utils import load_directory, load_user_directory_groups
from backend.components.constraints import (
    format_docs,
    SUMMARIZER_PROMPT,
    GRADING_PROMPT,
    REWRITING_PROMPT,
    INSIGHT_QUERY_PROMPT,
    TOOL_AGENT_PROMPT,
    REASONER_PROMPT,
    ISSUE_DRAFT_PROMPT,
    PR_REVIEW_PROMPT,
    DRAFT_PR_PROMPT,
    MEMORY_EXTRACTION_PROMPT
)
from backend.utils.memory_utils import save_user_fact, load_user_facts
from backend.services.memory_search import embed_and_store_memory_chunk, retrieve_user_memory
from backend.components.time_storage import add_time_entry, TimeEntryCreate
from backend.components import taskboard
from backend.state.graph_state import GraphState, route_after_grading
from langgraph.graph import StateGraph, START, END
from backend.utils.db_utils import get_db
from backend.services.python_sandbox import run_python_sandboxed, SAFE_IMPORT_ALLOWLIST
from backend.utils.normalize_utils import ensure_str

load_dotenv()
logger = logging.getLogger("SASS Logger")
TOOL_AGENT_MAX_ITERATIONS = int(os.getenv("TOOL_AGENT_MAX_ITERATIONS", "5"))
# Deep thinking (a per-user opt-in setting, see user_settings_utils) raises both the step
# ceiling and how many times the retry-nudge is allowed to reject a premature "final" — a
# higher step cap alone wouldn't help if the loop still only gets one nudge to actually use
# the extra room to double-check itself.
TOOL_AGENT_MAX_ITERATIONS_DEEP = int(os.getenv("TOOL_AGENT_MAX_ITERATIONS_DEEP", "12"))
TOOL_AGENT_MAX_RETRY_NUDGES = int(os.getenv("TOOL_AGENT_MAX_RETRY_NUDGES", "1"))
TOOL_AGENT_MAX_RETRY_NUDGES_DEEP = int(os.getenv("TOOL_AGENT_MAX_RETRY_NUDGES_DEEP", "3"))
async def safe_emit_event(name: str, data: dict):
    """Safely emit a custom event, ignoring errors if called outside an active run context."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # Safely ignored when called from fallback utilities or standalone scripts
        pass

def ensure_workflow_keys(state: GraphState) -> GraphState:
    state.setdefault("workflowName", "sonic_assistant")
    state.setdefault("requestId", uuid.uuid4().hex)
    return state

# ============================================================
# COORDINATOR_NODE (sync)
# ============================================================

async def coordinator_node(state: GraphState) -> GraphState:
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "coordinator_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        last_msg = state["messages"][-1].content.lower().strip()

        logger.info("--- COORDINATOR NODE START ---")
        logger.debug(f"Incoming state pending_action: {state.get('pending_action')}")

        state = await reasoner_node(state)

        intent = classify_intent(last_msg, state=state)
        plan = build_agent_plan(intent, state)

        state["coordinator_intent"] = intent
        state["coordinator_plan"] = plan["agents"]

        logger.info(f"Final stored plan: {state['coordinator_plan']}")
        logger.info("--- COORDINATOR NODE END ---")

        node_output = state.copy()

        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )

        if "workflowName" not in state:
            logger.error("STATE LOST workflowName HERE: %s", state)

        return state

def coordinator_router(state: GraphState) -> str:
    logger.info("Preparing next step.")

    plan = state.get("coordinator_plan", [])
    intent = state.get("coordinator_intent", [])

    if not plan:
        logger.info("--- COORDINATOR NODE END ---")
        return "conversational_node"

    next_agent = plan.pop(0)
    state["coordinator_plan"] = plan
    state["last_intent"] = intent

    mapping = {
        "retriever": "retrieve_node",
        "reasoner": "reasoner_node",
        "conversational": "conversational_node",
        "formatter": "formatter_node",
        "summarizer": "summarizer_node",
        "paapp": "paapp_node",
        "workflow": "conversational_node",
        "tool": "conversational_node",
        "memory_save": "memory_save_node",
        "memory_recall": "memory_recall_node",
        "insight": "snapshot_node",
        "tool_agent": "tool_agent_node",
        "pr_summary": "pr_summary",
        "propose_write": "propose_write_node",
        "execute_write": "execute_write_node"
    }
    destination = mapping.get(next_agent, "conversational_node")
    logger.debug(f"Next agent from plan: '{next_agent}'")
    logger.debug(f"Router returning destination string: '{destination}'")
    logger.info(f"sending request to {next_agent}")
    logger.info("--- COORDINATOR NODE END ---")
    return mapping.get(next_agent, "conversational_node")

def is_valid_pending_pr(pending_action: Optional[dict]) -> bool:
    """Verifies that pending_action exists AND holds complete PR parameters."""
    if not pending_action or not isinstance(pending_action, dict):
        return False
    
    if pending_action.get("status") != "awaiting_approval":
        return False
        
    params = pending_action.get("params", {})
    # Check for required PR fields
    required_keys = {"repo", "title", "head_branch"}
    return all(k in params and params[k] for k in required_keys)


# Tolerant approval/rejection matching: word-boundary regex instead of exact-whole-message
# equality, so natural phrasing ("yes let's do it", "nah don't bother") is recognized instead
# of only bare canned replies. Only consulted while a HITL approval is actually pending, so
# the broader vocabulary doesn't risk misfiring during normal conversation.
APPROVAL_PATTERN = re.compile(
    r"\b(approve[d]?|confirm(?:ed)?|yes|yeah|yep|yup|lgtm|sure|do it|go ahead|sounds good|let'?s do it)\b",
    re.IGNORECASE,
)
REJECTION_PATTERN = re.compile(
    r"\b(reject(?:ed)?|cancel(?:led|ed)?|no|nah|nope|stop|don'?t|never ?mind|hold off)\b",
    re.IGNORECASE,
)


def classify_intent(message: str, state: dict = None) -> str:
    msg = message.lower().strip()
    msg_clean = msg.strip("!.,")
    state = state or {}
    logger.debug(f"State keys: {list(state.keys())}")
    logger.debug(f"pending_action: {state.get('pending_action')}")
    logger.debug(f"last_intent: {state.get('last_intent')}")

    messages = state.get("messages", []) or []

    user_messages = [m for m in messages if getattr(m, "type", None) == "human"]

    logger.debug(f"User messages count: {len(user_messages)}")
    for i, um in enumerate(user_messages):
        logger.debug(f"user_messages[{i}]: {um.content}")

    # Any registered write action (PR, issue, Mongo write, and whatever gets registered next)
    # is detected from the ASSISTANT'S OWN prior card text, not the user's original phrasing —
    # more reliable (exact text we generated ourselves) and the only signal that generalizes to
    # actions like Mongo writes with no fixed user-typed trigger phrase. pending_action never
    # survives between chat turns in this app (every turn rebuilds state fresh from stored
    # messages — see app.py's initial_state), so this is the only reliable way to know what a
    # bare "approve" two turns later is actually approving.
    if len(messages) >= 2:
        prev_content = _content_of(messages[-2])
        matched_action = next(
            (name for name, action in WRITE_ACTIONS.items() if action.get("card_marker") and action["card_marker"] in prev_content),
            None,
        )
        if matched_action:
            logger.debug(f"Detected pending '{matched_action}' approval card in previous message")
            if APPROVAL_PATTERN.search(msg_clean):
                state["write_action"] = matched_action
                logger.debug("Approval keyword detected — returning execute_write")
                return "execute_write"
            if REJECTION_PATTERN.search(msg_clean):
                return "cancel_action"

        # Same reasoning as the write-action card_marker check above, for tool_agent_node's own
        # clarification pause: a short reply like "the SAAPP one" won't reliably trip the
        # reasoner's needs_github_search/needs_code_interpreter flags on its own, so without this
        # explicit check it would fall through to a fresh, unrelated classification instead of
        # resuming the paused search.
        if CLARIFICATION_CARD_MARKER in prev_content:
            logger.debug("Detected pending tool_agent clarification in previous message — resuming tool_agent")
            return "resume_tool_agent"

    pending_action = state.get("pending_action") or {}
    status = pending_action.get("status")
    logger.debug(f"pending_action.status: {status}")
    if status == "awaiting_approval":
        action_type = pending_action.get("action_type")
        if APPROVAL_PATTERN.search(msg_clean) and action_type in WRITE_ACTIONS:
            return "execute_write"
        if REJECTION_PATTERN.search(msg_clean):
            return "cancel_action"

    # 1. Handle Active HITL Approvals (writes, Web Search, etc.)
    if status in {"awaiting_approval", "hitl_approval_required"}:
        action_type = pending_action.get("action_type") or pending_action.get("type")

        if APPROVAL_PATTERN.search(msg_clean):
            if action_type in WRITE_ACTIONS:
                return "execute_write"
            elif action_type in {"web_search", "web_search_fallback"}:
                return "web_search"
            return action_type or "web_search"

        if REJECTION_PATTERN.search(msg_clean):
            return "cancel_action"
    # 2. Strict Tool Matching (Using regex word boundaries for short terms like 'pr'). Mongo,
    # GitHub, and web all resolve to the same unified multi-tool agent now.
    if any(w in msg for w in ["github", "repository", "commit history", "code search"]):
        return "tool_agent"
    if any(w in msg for w in ["run code", "execute", "query db", "mongodb", "script"]):
        return "tool_agent"

    # Checked BEFORE the generic "pull request" pattern below, so natural creation phrasing
    # like "create a pull request to merge X into Y" isn't swallowed by the broader
    # review/summary match just because it also contains the words "pull request".
    if re.search(r'\b(?:create|open|submit|draft)\s+(?:an?\s+)?(?:pr|pull request)\b', msg) or re.search(r'\bmerge\s+(?:an?\s+)?pr\b', msg):
        return "create_pr"
    # Checked before "pull request"/"pr" matching below since "issue" is unambiguous on its own.
    if re.search(r'\b(?:create|open|file|submit|draft)\s+(?:an?\s+)?(?:issue|bug report)\b', msg):
        return "create_issue"
    # Use word boundary so 'process' or 'provide' won't match 'pr'
    if re.search(r'\b(review pr|pull request|pr summary)\b', msg):
        return "pr_summary"
    # 3. General operational intents
    if "plan my day" in msg or "schedule" in msg:
        return "task_paapp"
    if "summarize" in msg or "tl;dr" in msg:
        return "summarize"
    if any(w in msg for w in ["find", "lookup", "policy", "docs", "search"]):
        return "retrieve"
    # Word boundary on "api" so substrings like "tapioca"/"apiary" don't misfire.
    if any(w in msg for w in ["calculate", "web search", "google"]) or re.search(r'\bapi\b', msg):
        return "tool"
    if any(w in msg for w in ["workflow", "ticket", "request form"]):
        return "workflow"
    if any(w in msg for w in ["remember", "recall", "what did i ask before"]):
        return "memory"
    if any(w in msg for w in ["bullet", "report", "format this"]):
        return "format"

    if any(phrase in msg for phrase in [
        "what did i do", "what was my", "how much time", "how many",
        "most", "least", "trend", "trends", "pattern", "patterns",
        "streak", "productivity", "calendar", "logs", "tasks",
        "insight", "analyze", "review my week", "review my day", "review my month"
    ]):
        return "insight"

    return "conversational"

def build_agent_plan(intent: str, state: dict) -> dict:
    flags = state.get("reasoner_flags", {})
    logger.debug(f"Incoming intent: {intent}")
    logger.debug(f"State.last_intent: {state.get('last_intent')}")
    logger.debug(f"Reasoner flags: {state.get('reasoner_flags')}")
    agents = []

    # 1. Approval execution takes absolute precedence — one generic destination for every
    # registered write action (PR, issue, Mongo write, ...).
    if intent == "execute_write":
        logger.info("[Coordinator] Direct routing to execute_write.")
        state["last_intent"] = "execute_write"
        return {"agents": ["execute_write", "formatter"], "skip": []}

    # 2. Direct write-proposal requests — the reasoner's needs_create_pr/needs_create_issue
    # flags are included here (not just the classify_intent regex) since natural phrasing the
    # regex doesn't catch should still reach this path via the LLM's own semantic
    # classification. Detecting *which* write action is wanted stays per-action (inherently
    # semantic); state["write_action"] tells the shared propose_write_node which one to draft.
    if intent == "create_pr" or flags.get("needs_create_pr"):
        state["last_intent"] = "propose_write"
        state["write_action"] = "create_pr"
        return {"agents": ["propose_write", "formatter"], "skip": []}

    if intent == "create_issue" or flags.get("needs_create_issue"):
        state["last_intent"] = "propose_write"
        state["write_action"] = "create_issue"
        return {"agents": ["propose_write", "formatter"], "skip": []}

    # 2b. Continuation of a non-PR HITL approval (e.g. approving a pending web search) —
    # these used to be classified correctly but silently dropped here, falling back to
    # whatever the reasoner's flags guessed for a bare "yes"/"no" reply. Web search is now
    # one of tool_agent_node's actions, not its own destination.
    if intent == "web_search":
        state["last_intent"] = "tool_agent"
        return {"agents": ["tool_agent", "formatter"], "skip": []}

    # Continuation of a paused tool_agent_node clarification (see classify_intent's
    # CLARIFICATION_CARD_MARKER check) — same reasoning as web_search just above: a short
    # answer to "which repo did you mean?" won't reliably set the reasoner's own
    # needs_github_search/needs_code_interpreter flags, so this routes deterministically
    # instead of leaving it to flags that were never about this reply in the first place.
    if intent == "resume_tool_agent":
        state["last_intent"] = "tool_agent"
        return {"agents": ["tool_agent", "formatter"], "skip": []}

    if intent == "cancel_action":
        state["pending_action"] = None
        state["insight_answer"] = (
            "The user's pending action was just cancelled/rejected. Acknowledge this briefly "
            "and naturally, then continue the conversation normally."
        )
        state["last_intent"] = "cancel_action"
        return {"agents": ["conversational"], "skip": []}

    # 3. Prevent Mutating Actions from standard follow-up sticky logic
    # Follow-up messages MUST re-classify intent so approvals work
    if flags.get("follow_up_intent"):
        logger.debug("[Coordinator] follow_up_intent=True — reclassifying intent")
        intent = classify_intent(state["messages"][-1].content, state=state)
        logger.debug(f"[Coordinator] Reclassified follow-up intent: {intent}")
    # 4. Standard Operational Flag Mapping
    is_pr_request = flags.get("needs_pr_summary") or intent == "pr_summary"
    
    if flags.get("needs_memory_save"):
        agents.append("memory_save")
    elif flags.get("needs_memory_recall") or intent == "memory":
        agents.append("memory_recall")
    if flags.get("needs_retrieval"):
        agents.append("retriever")
    if flags.get("needs_rewrite"):
        agents.append("rewriter")
    if flags.get("needs_summary"):
        agents.append("summarizer")
    if flags.get("needs_paapp"):
        agents.append("paapp")
    # Mongo/GitHub/web all fold into one multi-tool agent — any of the three flags routes
    # there, and the model itself decides which tool(s) the question actually needs.
    if flags.get("needs_web_search") or flags.get("needs_code_interpreter") or flags.get("needs_github_search"):
        agents.append("tool_agent")
    if is_pr_request:
        agents.append("pr_summary")

    if not agents:
        agents.append("conversational")

    if "formatter" not in agents and "code_interpreter" not in agents:
        agents.append("formatter")

    state["last_intent"] = intent
    return {"agents": agents, "skip": []}

def apply_conditional_skips(plan: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    agents = plan["agents"]
    skip = plan["skip"]
    # Example: if no retrieval context configured, drop retriever
    if "retriever" in agents and not state.get("rag_enabled", True):
        agents.remove("retriever")
        skip.append("retriever")
    # Example: if query already clean, drop reasoner
    if "reasoner" in agents and state.get("query_is_clean", False):
        agents.remove("reasoner")
        skip.append("reasoner")
    plan["agents"] = agents
    plan["skip"] = skip
    return plan

# ============================================================
# REASONER NODE (sync)
# ============================================================

async def reasoner_node(state: GraphState) -> GraphState:
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "reasoner_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        msg = state["messages"][-1].content.strip()
        history = state.get("messages", [])

        # Format message history into a clean string for the LLM
        formatted_history = "\n".join([f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', '')}" for m in history[:-1]])

        formatted_prompt = REASONER_PROMPT.format(
            history=formatted_history,
            question=msg
        )

        logger.info("--- REASONER NODE START ---")

        # 1. EMIT THE LIVE THOUGHT (This brings the trace back to the UI!)
        await safe_emit_event(
            "trace_detail",
            {
                "node": "reasoner_node",
                "title": "Analyzing intent and planning route...",
                "detail": f"Classifying workflow intent for: '{msg[:30]}...'"
            }
        )

        try:
            # 2. USE AINVOKE FOR NON-BLOCKING LLAMA CALL
            response = await lite_llm.ainvoke(formatted_prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)

            # Safely handle list vs string response types
            if isinstance(resp_content, list):
                raw_text = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in resp_content])
            else:
                raw_text = str(resp_content)

            # Clean response string if wrapped in markdown codeblocks
            clean_json = raw_text.replace("```json", "").replace("```", "").strip()
            flags = json.loads(clean_json)

        except Exception:
            logger.exception("[Reasoner] LLM classification failed, using fallback rules.")
            # Fallback to standard false flags if JSON parsing fails
            flags = {
                "needs_retrieval": False,
                "needs_rewrite": False,
                "needs_summary": False,
                "needs_formatting": False,
                "needs_conversation": True,  # Safe default to avoid triggering unintended actions
                "needs_memory_save": False,
                "needs_memory_recall": False,
                "needs_paapp": False,
                "follow_up_intent": False,
                "needs_web_search": False,
                "needs_code_interpreter": False,
                "needs_github_search": False,
                "needs_pr_summary": False,
                "needs_create_pr": False,
                "needs_create_issue": False,
            }

        logger.info(f"[Reasoner] Flags: {flags}")
        state["reasoner_flags"] = flags
        logger.info("--- REASONER NODE END ---")
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return state
# ============================================================
# MEMORY NODES (persistent structured memory: save + recall)
# ============================================================

async def memory_save_node(state: GraphState, memory_vector_store=None) -> dict:
    state = ensure_workflow_keys(state)
    logger.info("--- MEMORY SAVE NODE CALLED ---")
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "memory_save_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        username = state.get("username", "default_user")
        user_msg = state["messages"][-1].content.strip()

        await safe_emit_event(
            "trace_detail",
            {
                "node": "memory_save_node",
                "title": "Saving to memory...",
                "detail": "Extracting a durable fact from your message."
            }
        )

        category = "preference"
        fact_text = user_msg
        try:
            response = await lite_llm.ainvoke(MEMORY_EXTRACTION_PROMPT.format(message=user_msg))
            resp_content = response.content if hasattr(response, "content") else str(response)
            if isinstance(resp_content, list):
                raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content])
            else:
                raw_text = str(resp_content)
            clean_json = raw_text.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean_json)
            category = parsed.get("category") or "preference"
            fact_text = parsed.get("fact") or user_msg
        except Exception:
            logger.exception("[MemorySave] Fact extraction failed, storing raw message.")

        saved = save_user_fact(username, fact_text, category=category, source="explicit")
        embed_and_store_memory_chunk(
            memory_vector_store, username, saved.fact,
            source_type="manual", source_ref=state.get("session_id")
        )

        # Feed the confirmation through as an "insight" rather than short-circuiting the
        # response entirely — app.py's prompt-selection weaves insight_answer into
        # CONVERSATIONAL_PROMPT ahead of the plain "conversational" branch, so the LLM can
        # acknowledge the save AND still respond to the rest of the user's message, instead
        # of the reply being nothing but a canned confirmation line.
        confirmation = f"A new fact was just saved to memory: \"{saved.fact}\". Acknowledge this naturally and briefly, then respond to the rest of the user's message normally."
        state["memory_facts"] = [saved.dict()]
        state["raw_generation"] = confirmation
        state["content_to_format"] = confirmation
        state["insight_answer"] = confirmation
        state["relevance_grade"] = "conversational"

        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return state


def memory_recall_node(state: GraphState, memory_vector_store=None) -> dict:
    state = ensure_workflow_keys(state)
    logger.info("--- MEMORY RECALL NODE CALLED ---")
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "memory_recall_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        username = state.get("username", "default_user")
        question = state["messages"][-1].content.strip() if state.get("messages") else ""

        facts = load_user_facts(username)
        state["memory_facts"] = [f.dict() for f in facts]

        semantic_hits = retrieve_user_memory(memory_vector_store, username, question, top_k=4)
        state["memory_hits"] = [h.page_content for h in semantic_hits]

        if not facts and not semantic_hits:
            report = "No saved facts or preferences are on record for this user yet."
        else:
            report_parts = []
            if facts:
                fact_lines = "\n".join(f"- [{f.category}] {f.fact}" for f in facts)
                report_parts.append(f"Saved facts/preferences:\n{fact_lines}")
            if semantic_hits:
                semantic_lines = "\n".join(f"- {h.page_content}" for h in semantic_hits)
                report_parts.append(f"Relevant past context:\n{semantic_lines}")
            report = "\n\n".join(report_parts)

        doc = Document(
            page_content=f"SYSTEM MEMORY REPORT:\n{report}",
            metadata={"source": "user_memory", "priority": True}
        )
        current_docs = state.get("documents", [])
        current_docs.append(doc)

        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return {
            **state,
            "documents": current_docs,
            "relevance_grade": "yes"
        }

async def retrieve_node(state: GraphState, vector_store) -> dict:
    logger.info("--- PARALLEL RETRIEVING DOCUMENTS & GRAPH CONTEXT ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "retrieve_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        question = state["messages"][-1].content
        username = state.get("username")
        target_scope = state.get("target_scope")
        current_loops = state.get("loop_count", 0) or 0
        original_question = state.get("original_question") or question
        session_id = state.get("session_id") or f"{username}_session"

        # 1. First thought line: Starting the search
        await safe_emit_event(
            "trace_detail",
            {
                "node": "retrieve_node",
                "title": "GraphRAG Retrieval in progress...",
                "detail": f"Querying vector index for '{original_question[:30]}...'"
            }
        )

        # 2. EARLY EXIT: Priority attachments
        if state.get("attachment_summaries"):
            logger.info("Attachment detected — skipping vector search and using only priority docs.")
            docs = [Document(page_content=s, metadata={"source": "user_attachment_summary", "priority": True})
                    for s in state.get("attachment_summaries", [])]
        else:
            # Your actual vector retrieval / search execution here
            docs = vector_store.similarity_search(original_question, k=4)

            # 3. Second thought line: Dynamic result update!
            if docs:
                sources = list(set([d.metadata.get("source", "knowledge base") for d in docs]))
                await safe_emit_event(
                    "trace_detail",
                    {
                        "node": "retrieve_node",
                        "title": "GraphRAG Retrieval in progress...",
                        "detail": f"Extracted {len(docs)} chunks from {sources[0]}."
                    }
                )

        # 1. EARLY EXIT: Priority attachments (Only returns early IF attachments exist)
        if state.get("attachment_summaries"):
            logger.info("Attachment detected — skipping vector search and using only priority docs.")
            return {
                **state,
                "documents": [Document(page_content=s, metadata={"source": "user_attachment_summary", "priority": True})
                             for s in state.get("attachment_summaries", [])],
                "loop_count": current_loops + 1
            }

        # Parallel Task 1: Vector Search
        def fetch_vector_docs():
            try:
                retriever = get_secure_retriever(
                    vector_store=vector_store,
                    target_scope=target_scope,
                    query_text=question,
                    top_k=3
                )
                return retriever.invoke(question) or []
            except Exception:
                logger.exception("Vector search failed.")
                return []

        # Parallel Task 2: Session Search
        def fetch_session_docs():
            try:
                session_hits = retrieve_from_session(username, session_id, question)
                if session_hits:
                    return [Document(
                        page_content=f"[Session Document: {hit['metadata']['filename']}]\n{hit['text']}",
                        metadata={"source": "session_vector_store", "priority": True, "filename": hit["metadata"]["filename"]}
                    ) for hit in session_hits]
            except Exception:
                logger.exception("Session retrieval failed.")
            return []

        # Parallel Task 3: Knowledge Graph Search
        def fetch_graph_docs():
            graph_docs = []
            try:
                question_lower = question.lower()
                for entity in graph_db.knowledge_graph.nodes:
                    if str(entity).lower() in question_lower:
                        relations = graph_db.get_dynamic_context(entity, hops=2)
                        for fact in relations:
                            graph_docs.append(Document(
                                page_content=f"Connection: {fact}",
                                metadata={"source": "knowledge_graph_db", "type": "relationship"}
                            ))
            except Exception:
                logger.exception("GraphRAG Entity scanner failed.")
            return graph_docs

        # Execute all 3 fetches concurrently
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_vec = executor.submit(fetch_vector_docs)
            future_sess = executor.submit(fetch_session_docs)
            future_graph = executor.submit(fetch_graph_docs)

            vector_docs = future_vec.result()
            session_docs = future_sess.result()
            graph_docs = future_graph.result()

        # Combine prioritized results
        docs = session_docs + vector_docs + graph_docs

        summaries = state.get("attachment_summaries", [])
        for summary in summaries:
            docs.append(Document(
                page_content=summary,
                metadata={"source": "user_attachment_summary", "priority": True}
            ))
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return {
            **state,
            "documents": docs,
            "loop_count": current_loops + 1,
            "original_question": original_question
        }

# ============================================================
# SUMMARIZER NODE
# ============================================================

def summarizer_node(state: GraphState) -> GraphState:
    logger.info("--- SUMMARIZER NODE CALLED ---")
    docs = state.get("documents", [])
    user_msg = state["messages"][-1].content
    if not docs:
        logger.info("[Summarizer] No documents found in state; skipping summarization.")
        state["summary"] = None
        logger.info("--- SUMMARIZER NODE END ---")
        return state
    # Build a concise context block
    context_chunks = []
    for i, doc in enumerate(docs, start=1):
        page = getattr(doc.metadata, "page", doc.metadata.get("page", "N/A")) if hasattr(doc, "metadata") else "N/A"
        text = doc.page_content if hasattr(doc, "page_content") else str(doc)
        context_chunks.append(f"--- DOCUMENT {i} (Page {page}) ---\n{text}")
    context_block = "\n\n".join(context_chunks)
    prompt = SUMMARIZER_PROMPT
    logger.info("Sending summarization prompt to LLM.")
    # Assuming you have a `llm` or `model` in scope
    summary = get_chat_llm(state.get("username", "")).invoke(prompt)
    # If your LLM returns an object, extract `.content` or similar
    if hasattr(summary, "content"):
        summary_text = summary.content
    else:
        summary_text = str(summary)
    logger.info("Summary generated.")
    state["summary"] = summary_text
    logger.info("--- SUMMARIZER NODE END ---")
    return state

# ============================================================
# FORMATTER NODE
# ============================================================

def formatter_node(state: GraphState) -> dict:
    """Assembles a normalized voice_payload from whatever the upstream node produced — no LLM
    call here. The single unified Voice Composer prompt (built in app.py via
    constraints.build_voice_prompt) consumes this to generate the actual response, applying
    one Sonic Assistant persona regardless of which path produced the underlying data."""
    logger.info("--- FORMATTER NODE CALLED ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "formatter_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()

        relevance_grade = state.get("relevance_grade")
        insight_answer = state.get("insight_answer")

        if relevance_grade == "web_search":
            source_type = "web"
        elif relevance_grade in ("code_interpreter", "github_search", "pr_summary", "tool_agent"):
            source_type = "tool_output"
        elif relevance_grade == "conversational" or insight_answer:
            source_type = "conversational"
        else:
            source_type = "kb_open" if state.get("rag_mode") == "open" else "kb_strict"

        state["voice_payload"] = {
            "source_type": source_type,
            "data": state.get("content_to_format"),
            "insight": insight_answer,
            "relevance_grade": relevance_grade,
        }
        logger.info(f"Voice payload source_type={source_type}")

        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return state

def insight_formatter_node(state: dict) -> dict:
    """
    Passes the structured insights array directly to the endpoint 
    instead of converting it into a chatbot string.
    """
    username = state.get("username")
    insights = state.get("insights", [])

    return {
        "insights": insights,
        "username": username
    }
# ============================================================
# CONVERSATIONAL NODE (sync - pass-through for stream)
# ============================================================

def conversational_node(state: GraphState) -> dict:
    logger.info("--- CONVERSATIONAL NODE (PASS-THROUGH ENFORCED) ---")
    # Mark state so the gateway knows to format conversation rules
    return {**state, "relevance_grade": "conversational"}

# ============================================================
# GENERATE NODE (sync - pass-through for stream)
# ============================================================

def generate_node(state: GraphState) -> dict:
    logger.info("--- GENERATING RESPONSE ---")
    # No-op node. Exits graph instantly so FastAPI can execute the direct stream.
    return state

# ============================================================
# GRADING NODE (sync)
# ============================================================

async def grading_node(state: GraphState) -> dict:
    logger.info("--- GRADING RETRIEVED CONTENT ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "grading_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        # 1. EMIT THE LIVE THOUGHT: Start grading
        await safe_emit_event(
            "trace_detail",
            {
                "node": "grading_node",
                "title": "Evaluating document relevance...",
                "detail": "Checking if the retrieved context contains the answer."
            }
        )

        # Defensive extraction of question
        try:
            raw_question = state.get("messages", [])[-1].content
        except Exception:
            raw_question = state.get("question", "")
        question = ensure_str(raw_question)

        documents = state.get("documents", []) or []
        if not documents:
            logger.info("No documents found; preserving state with relevance_grade=no")
            return {**state, "relevance_grade": "no"}

        # Ensure format_docs returns a string; if it returns list, join it
        combined_docs = format_docs(documents)
        combined_docs = ensure_str(combined_docs)

        formatted_prompt = GRADING_PROMPT.format(
            context=combined_docs,
            question=question,
            history=state.get("history", "") or ""
        )

        try:
            logger.info("Grading response")

            # 2. USE AINVOKE FOR NON-BLOCKING LLM CALL
            response = await lite_llm.ainvoke(formatted_prompt)

            response_text = response.content if hasattr(response, "content") else str(response)
            response_clean = ensure_str(response_text).lower().strip()
            grade = "yes" if "yes" in response_clean else "no"
            logger.info(f"Document grading complete. Grade: {grade}")

            for idx, doc in enumerate(documents, start=1):
                try:
                    src = doc.metadata.get("source", "Unknown")
                    page = doc.metadata.get("page", doc.metadata.get("page_label", "N/A"))
                except Exception:
                    src = "Unknown"
                    page = "N/A"
                logger.info(f"    - Doc {idx}: {src} (Page {page}) → Grade: {grade}")

            # 3. DYNAMIC TRACE: Announce the result to the UI!
            grade_text = "Relevant" if grade == "yes" else "Irrelevant (Triggering fallback...)"
            await safe_emit_event(
                "trace_detail",
                {
                    "node": "grading_node",
                    "title": "Evaluating document relevance...",
                    "detail": f"Evaluation complete. Context marked as: {grade_text}"
                }
            )
            node_output = state.copy()
            logger.info(
                "node executed",
                extra={
                "service": "SAAPP",
                    "erragent_context": {
                        "input": node_input,
                        "output": node_output,
                    }
                }
            )
            return {**state, "relevance_grade": grade}
        except Exception:
            logger.exception("Grading failed. Defaulting to no.")
            node_output = state.copy()
            logger.info(
                "node executed",
                extra={
                "service": "SAAPP",
                    "erragent_context": {
                        "input": node_input,
                        "output": node_output,
                    }
                }
            )
            return {**state, "relevance_grade": "no"}


# ============================================================
# QUERY REWRITE NODE (sync)
# ============================================================

def rewrite_query_node(state: GraphState) -> dict:
    logger.info("--- REWRITING QUERY FOR BETTER RETRIEVAL ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "formatter_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        # Defensive extraction of original question
        try:
            raw_original = state.get("messages", [])[-1].content
        except Exception:
            raw_original = state.get("question", "")
        original_question = ensure_str(raw_original)

        formatted_prompt = REWRITING_PROMPT.format(question=original_question)

        try:
            response = lite_llm.invoke(formatted_prompt)
            rewrite_text = response.content if hasattr(response, "content") else str(response)
            rewrite_clean = ensure_str(rewrite_text).strip()
            logger.info(f"Query rewritten: '{original_question}' -> '{rewrite_clean}'")

            # Replace the last HumanMessage safely
            new_messages = list(state.get("messages", []))
            if new_messages:
                new_messages[-1] = HumanMessage(content=rewrite_clean)
            else:
                new_messages = [HumanMessage(content=rewrite_clean)]
            node_output = state.copy()
            logger.info(
                "node executed",
                extra={
                "service": "SAAPP",
                    "erragent_context": {
                        "input": node_input,
                        "output": node_output,
                    }
                }
            )
            return {
                **state,
                "messages": new_messages,
                "question": rewrite_clean
            }
        except Exception:
            logger.exception("Query rewrite node failed.")
            node_output = state.copy()
            logger.info(
                "node executed",
                extra={
                "service": "SAAPP",
                    "erragent_context": {
                        "input": node_input,
                        "output": node_output,
                    }
                }
            )
            return state

# ============================================================
# PAAPP NODE (sync)
# ============================================================

def paapp_node(state: GraphState) -> GraphState:
    msg = state["messages"][-1].content
    username = state.get("username", "default_user")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "paapp_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        try:
            response = call_paapp_chat(username, msg)
        except Exception:
            logger.exception("PAAPP communication error.")
            fallback = "PAAPP communication error: see logs for traceback"
            state["raw_generation"] = fallback
            state["content_to_format"] = fallback
            return state

        intent = response.get("intent")

        # DEBUG: Always log what the API sends so we can see if the tool name matches
        logger.info(f"DEBUG: PAAPP intent received: {intent}")

        # --- 1. HANDLE CALENDAR EVENT ---
        if intent and intent.get("tool") == "create_google_calendar_event":
            entry_payload = TimeEntryCreate(
                username=username,
                activity=str(intent.get("summary", "Untitled Event")),
                duration_hours=float(intent.get("duration_minutes", 0)) / 60,
                duration_minutes=int(intent.get("duration_minutes", 0)),
                date=str(intent.get("start_time_iso", "").split("T")[0]),
                notes="",
                type="event"
            )

            # Save locally (MongoDB + Mirror)
            add_time_entry(entry_payload)
            logger.info(f"[PAAPP] Successfully mirrored calendar event locally for {username}")

            # FIX: Restore Sync by re-pinging the headless API (The "Zero-Import" Handshake)
            try:
                requests.post(
                    f"{PAAPP_BASE_URL}/api/headless-chat",
                    headers={"x-saapp": "true"},
                    json={"username": username, "question": f"sync event {entry_payload.activity}"}
                )
                logger.info(f"[PAAPP] Sync trigger request sent to headless API.")
            except Exception:
                logger.exception("[PAAPP] Sync trigger failed.")

            # FIX: Update state['snapshot'] so the UI updates without a refresh
            if "snapshot" in state:
                state["snapshot"]["calendar"] = load_user_calendar_events(username)

        # --- 2. HANDLE LOG TIME ---
        if intent and intent.get("tool") == "log_time":
            try:
                entry_payload = TimeEntryCreate(
                    username=username,
                    activity=str(intent.get("activity", "Unknown Activity")),
                    duration_hours=float(intent.get("minutes", 0)) / 60,
                    duration_minutes=int(intent.get("minutes", 0)),
                    date=str(intent.get("date_iso")),
                    notes=str(intent.get("notes", "No description provided")),
                    type="log"
                )

                add_time_entry(entry_payload)
                logger.info(f"[PAAPP] Successfully logged time locally for {username}")

                # FIX: Update state['snapshot'] for logs too
                if "snapshot" in state:
                    state["snapshot"]["logs"] = load_user_time(username)

            except Exception:
                logger.exception("[PAAPP] Time log failed.")
                state["raw_generation"] = "Time log failed: see logs for traceback"
                return state

        # --- 3. RETURN RESPONSE ---
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except:
                pass
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        message = response.get("message", "PAAPP returned no message.")
        state["raw_generation"] = message
        state["content_to_format"] = message
        return state


def call_paapp_chat(username: str, question: str) -> dict:
    url = f"{PAAPP_BASE_URL}/api/headless-chat"
    r = requests.post(
    url,
    headers={"x-saapp": "true"},
    json={
        "username": username,
        "question": question
    }
)
    r.raise_for_status()
    return r.json()

# ============================================================
# Data Snapshot Node
# ============================================================
def load_user_calendar_events(username: str):
    """
    Reads mirrored calendar events created by PAAPP.
    These live in: saapp_data/time/<username>_events.json
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    events_path = os.path.join(project_root, "saapp_data", "time", f"{username}_events.json")

    if not os.path.exists(events_path):
        return []

    try:
        with open(events_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading calendar events: {e}")
        return []


def data_snapshot_node(state: dict) -> dict:
    username = state.get("username")
    logger.info(f"DATA SNAPSHOT - Fetching data for user: {username}")

    # --- Logs ---
    logs = load_user_time(username)
    logger.info(f"DATA SNAPSHOT - Raw Logs Found: {len(logs) if logs else 0}")

    # --- Taskboard (UPDATED FOR MONGO) ---
    db = get_db()
    all_tasks = list(db["tasks"].find({"username": username})) if db is not None else []
    
    # Strip the raw ObjectId to prevent serialization crashes in the graph
    for t in all_tasks:
        t["id"] = str(t["_id"])
        t.pop("_id", None)
    
    # Filter the single list into the expected structure
    taskboard_data = {
        "backlog": [t for t in all_tasks if t.get("lane") == "backlog"],
        "in_progress": [t for t in all_tasks if t.get("lane") == "in_progress"],
        "completed": [t for t in all_tasks if t.get("lane") == "completed"]
    }
    
    logger.info(
        f"DATA SNAPSHOT - Tasks Found -> Backlog: {len(taskboard_data['backlog'])}, "
        f"In Progress: {len(taskboard_data['in_progress'])}, "
        f"Completed: {len(taskboard_data['completed'])}"
    )

    # --- Calendar (local mirror) ---
    calendar_events = load_user_calendar_events(username)
    logger.info(f"DATA SNAPSHOT - Calendar Events Found: {len(calendar_events) if calendar_events else 0}")

    # --- Directory (optional) ---
    directory = load_directory()
    user_entry = directory.get(username, {})
    user_groups = user_entry.get("groups", [])

    snapshot = {
        "calendar": calendar_events,
        "logs": logs,
        "taskboard": taskboard_data,
        "groups": user_groups,
        "timestamp": datetime.utcnow().isoformat()
    }

    return { **state, "snapshot": snapshot }

# ============================================================
# Activity Classifier Node
# ============================================================

# --- Lightweight keyword-based classifier -------------------

CATEGORY_KEYWORDS = {
    "coding": ["code", "coding", "react", "fastapi", "python", "typescript", "debug", "fix", "build"],
    "learning": ["learn", "study", "course", "tutorial", "read", "research"],
    "admin": ["email", "paperwork", "form", "admin", "file", "organize"],
    "job_search": ["apply", "application", "resume", "cover letter", "interview", "linkedin"],
    "creative": ["design", "write", "draft", "create", "brainstorm"],
    "health": ["gym", "workout", "run", "walk", "doctor"],
    "personal": ["clean", "laundry", "errand", "shopping"],
    "meeting": ["meeting", "call", "zoom", "chat"],
}

def classify_text(text: str) -> str:
    """
    Returns the best-fit category based on keyword matching.
    Falls back to 'misc' if nothing matches.
    """
    if not text:
        return "misc"

    text_lower = text.lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return category

    return "misc"


# --- Main Node ------------------------------------------------

def activity_classifier_node(state: dict) -> dict:
    """
    Takes the snapshot and classifies logs, tasks, and calendar events
    into meaningful activity categories.
    """

    snapshot = state.get("snapshot", {})
    username = state.get("username")

    # --- Logs --------------------------------------------------
    logs = snapshot.get("logs", [])
    classified_logs = []

    for entry in logs:
        category = classify_text(entry.activity)
        classified_logs.append({
            "id": entry.id,
            "activity": entry.activity,
            "category": category,
            "duration_hours": entry.duration_hours,
            "duration_minutes": entry.duration_minutes,
            "date": entry.date,
            "type": entry.type,
        })

    # --- Taskboard --------------------------------------------
    tb = snapshot.get("taskboard", {})
    classified_tasks = {
        "backlog": [],
        "in_progress": [],
        "completed": []
    }

    for lane in ["backlog", "in_progress", "completed"]:
        for task in tb.get(lane, []):
            title = task.get("title", "")
            category = classify_text(title)
            classified_tasks[lane].append({
                **task,
                "category": category
            })

    # --- Calendar ----------------------------------------------
    calendar_events = snapshot.get("calendar", [])
    classified_calendar = []

    for event in calendar_events:
        title = event.get("activity", "")
        category = classify_text(title)
        classified_calendar.append({
            **event,
            "category": category
        })

    # --- Output -------------------------------------------------
    classified_snapshot = {
        "classified_logs": classified_logs,
        "classified_tasks": classified_tasks,
        "classified_calendar": classified_calendar,
        "timestamp": snapshot.get("timestamp")
    }

    return { **state, "classified": classified_snapshot }

# ============================================================
# Pattern Detector Node
# ============================================================

def detect_time_patterns(classified_logs):
    """
    Detects patterns in time usage:
    - Most common activity categories
    - Productivity windows (morning/afternoon/evening)
    - Day-of-week activity patterns
    """
    category_counter = Counter()
    hour_buckets = Counter()
    weekday_counter = Counter()

    for entry in classified_logs:
        category_counter[entry["category"]] += 1

        # Productivity windows
        try:
            dt = datetime.fromisoformat(entry["date"])
            hour = dt.hour
            if 5 <= hour < 12:
                hour_buckets["morning"] += 1
            elif 12 <= hour < 17:
                hour_buckets["afternoon"] += 1
            elif 17 <= hour < 22:
                hour_buckets["evening"] += 1
            else:
                hour_buckets["late_night"] += 1

            weekday_counter[dt.strftime("%A")] += 1
        except:
            pass

    return {
        "top_categories": category_counter.most_common(3),
        "productivity_windows": hour_buckets,
        "weekday_activity": weekday_counter
    }


def detect_task_patterns(classified_tasks):
    stagnant = []
    fast = []
    backlog_categories = Counter()

    # 1. Identify Oldest Backlog Tasks
    backlog = classified_tasks.get("backlog", [])
    # Sort by 'createdAt' (oldest first)
    sorted_backlog = sorted(backlog, key=lambda x: x.get("createdAt", ""))
    # Take the top 3 oldest
    stagnant = sorted_backlog[:3] 

    # 2. Calculate category distribution
    for task in backlog:
        backlog_categories[task["category"]] += 1

    # 3. Detect fast-moving tasks (completed within 24 hours)
    for task in classified_tasks.get("completed", []):
        created = task.get("createdAt") # Ensure this matches your JSON key
        completed = task.get("completedAt") # Ensure this key exists or is tracked
        if created and completed:
            try:
                dt_created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                dt_completed = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                if dt_completed - dt_created < timedelta(days=1):
                    fast.append(task)
            except Exception:
                pass

    return {
        "stagnant_tasks": stagnant, # Now contains the oldest backlog tasks
        "fast_tasks": fast,
        "backlog_category_distribution": backlog_categories
    }


def detect_calendar_patterns(classified_calendar):
    """
    Detects patterns in calendar events:
    - Most common event categories
    - Busy vs free days
    - Meeting-heavy days
    """
    category_counter = Counter()
    day_load = Counter()

    for event in classified_calendar:
        category_counter[event["category"]] += 1

        date = event.get("date")
        if date:
            day_load[date] += 1

    return {
        "event_categories": category_counter,
        "busy_days": day_load.most_common(3),
        "free_days": [d for d, count in day_load.items() if count == 0]
    }


def pattern_detector_node(state: dict) -> dict:
    """
    Reads the classified snapshot and extracts behavioral patterns.
    """

    classified = state.get("classified", {})
    logs = classified.get("classified_logs", [])
    tasks = classified.get("classified_tasks", {})
    calendar = classified.get("classified_calendar", [])

    patterns = {
        "time_patterns": detect_time_patterns(logs),
        "task_patterns": detect_task_patterns(tasks),
        "calendar_patterns": detect_calendar_patterns(calendar),
        "timestamp": datetime.utcnow().isoformat()
    }

    return { **state, "patterns": patterns }

# ============================================================
# Trend Analyzer Node
# ============================================================

def compute_daily_totals(logs):
    """
    Returns a dict: { '2026-07-10': total_minutes, ... }
    """
    totals = defaultdict(int)
    for entry in logs:
        try:
            totals[entry["date"]] += entry["duration_minutes"]
        except:
            pass
    return dict(totals)


def compute_category_trends(classified_logs):
    """
    Tracks category frequency over time.
    Example output:
    {
        "coding": { "2026-07-10": 2, "2026-07-11": 1 },
        "learning": { ... }
    }
    """
    trends = defaultdict(lambda: defaultdict(int))

    for entry in classified_logs:
        category = entry["category"]
        date = entry["date"]
        trends[category][date] += 1

    return {cat: dict(days) for cat, days in trends.items()}


def compute_streaks(daily_totals):
    """
    Detects productivity streaks:
    - consecutive days with activity
    - longest streak
    - current streak
    """
    if not daily_totals:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "streak_days": []
        }

    dates = sorted(daily_totals.keys())
    streak = 0
    longest = 0
    streak_days = []

    prev_date = None

    for d in dates:
        dt = datetime.fromisoformat(d)
        if prev_date and dt - prev_date == timedelta(days=1):
            streak += 1
        else:
            streak = 1
        longest = max(longest, streak)
        streak_days.append(d)
        prev_date = dt

    return {
        "current_streak": streak,
        "longest_streak": longest,
        "streak_days": streak_days
    }


def compute_task_velocity(classified_tasks):
    """
    Measures how quickly tasks move from backlog → in-progress → completed.
    """
    velocities = []

    for task in classified_tasks.get("completed", []):
        created = task.get("created_at")
        completed = task.get("completed_at")

        if created and completed:
            try:
                dt_created = datetime.fromisoformat(created)
                dt_completed = datetime.fromisoformat(completed)
                delta = dt_completed - dt_created
                velocities.append(delta.total_seconds() / 3600)  # hours
            except:
                pass

    if not velocities:
        return {
            "average_completion_hours": None,
            "fastest_completion_hours": None,
            "slowest_completion_hours": None
        }

    return {
        "average_completion_hours": sum(velocities) / len(velocities),
        "fastest_completion_hours": min(velocities),
        "slowest_completion_hours": max(velocities)
    }


def compute_calendar_load_trends(classified_calendar):
    """
    Tracks how busy your calendar is over time.
    """
    load = defaultdict(int)

    for event in classified_calendar:
        date = event.get("date")
        if date:
            load[date] += 1

    return dict(load)


def trend_analyzer_node(state: dict) -> dict:
    """
    Computes temporal trends from logs, tasks, and calendar with explicit data step logging.
    """
    snapshot = state.get("snapshot", {})
    username = state.get("username")
    
    logs = snapshot.get("logs", [])
    tb = snapshot.get("taskboard", {})
    calendar_events = snapshot.get("calendar", [])
    
    logger.info(f"TREND ANALYZER - Incoming Raw Logs Count: {len(logs)}")
    logger.info(f"TREND ANALYZER - Incoming Raw Tasks Count: {sum(len(tb.get(k, [])) for k in tb)}")
    logger.info(f"TREND ANALYZER - Incoming Raw Calendar Count: {len(calendar_events)}")

    # --- Process Logs ---
    classified_logs = []
    for entry in logs:
        # FIX: Check if it's a dict first. If not, safely use getattr for the Pydantic model.
        activity_text = entry.get("activity", "") if isinstance(entry, dict) else getattr(entry, "activity", str(entry))
        
        # Test classification call
        try:
            category = classify_text(activity_text) or "Uncategorized"
        except Exception as ce:
            logger.error(f"TREND ANALYZER - classify_text failed on log: {str(ce)}")
            category = "Uncategorized"
            
        classified_logs.append({
            "id": getattr(entry, "id", None),
            "activity": activity_text,
            "category": category,
            "duration_hours": getattr(entry, "duration_hours", 0),
            "duration_minutes": getattr(entry, "duration_minutes", 0),
            "date": getattr(entry, "date", ""),
            "type": getattr(entry, "type", "log"),
        })
    logger.info(f"TREND ANALYZER - Successfully Classified Logs Count: {len(classified_logs)}")

    # --- Process Tasks ---
    classified_tasks = {"backlog": [], "in_progress": [], "completed": []}
    for lane in ["backlog", "in_progress", "completed"]:
        for task in tb.get(lane, []):
            title = task.get("title", "")
            try:
                cat = classify_text(title) or "Uncategorized"
            except Exception:
                cat = "Uncategorized"
            classified_tasks[lane].append({**task, "category": cat})
    logger.info(f"TREND ANALYZER - Successfully Classified Tasks Count: {sum(len(classified_tasks[k]) for k in classified_tasks)}")

    # --- Process Calendar ---
    classified_calendar = []
    for event in calendar_events:
        title = event.get("activity", event.get("title", ""))
        try:
            cat = classify_text(title) or "Uncategorized"
        except Exception:
            cat = "Uncategorized"
        classified_calendar.append({**event, "category": cat})
    logger.info(f"TREND ANALYZER - Successfully Classified Calendar Count: {len(classified_calendar)}")

    # --- Compute Trends & Patterns ---
    daily_totals = compute_daily_totals(classified_logs)
    category_trends = compute_category_trends(classified_logs)
    streaks = compute_streaks(daily_totals)
    task_velocity = compute_task_velocity(classified_tasks)
    calendar_trends = compute_calendar_load_trends(classified_calendar)

    trends = {
        "daily_totals": daily_totals,
        "category_trends": category_trends,
        "streaks": streaks,
        "task_velocity": task_velocity,
        "calendar_trends": calendar_trends,
        "timestamp": datetime.utcnow().isoformat()
    }

    patterns = {
        "time_patterns": detect_time_patterns(classified_logs),
        "task_patterns": detect_task_patterns(classified_tasks),
        "calendar_patterns": detect_calendar_patterns(classified_calendar),
        "timestamp": datetime.utcnow().isoformat()
    }

    logger.info(f"ANALYZER OUTPUT PATTERNS: {patterns}")

    return {
        **state,
        "analysis_output": patterns
    }



def insight_generator_node(state: dict) -> dict:
    """
    Converts patterns + trends into readable insights.
    """

    # Extract analysis output
    analysis = state.get("analysis_output", {})

    # Extract classified tasks
    classified_tasks = state.get("classified", {}).get("classified_tasks", {})

    # Initialize insights list
    insights = []

    # -----------------------------
    # EXISTING INSIGHTS
    # -----------------------------
    patterns = {
        "time_patterns": analysis.get("time_patterns", {}),
        "task_patterns": analysis.get("task_patterns", {}),
        "calendar_patterns": analysis.get("calendar_patterns", {})
    }

    # Time-based insights
    insights.extend(generate_time_insights(patterns, analysis))

    # Taskboard insights
    insights.extend(generate_task_insights(patterns, analysis))

    # Calendar insights
    insights.extend(generate_calendar_insights(patterns, analysis))

    return { **state, "insights": insights }



    
# ============================================================
# Insight Generator Node
# ============================================================

def generate_time_insights(patterns, trends):
    insights = []
    time_patterns = patterns.get("time_patterns", {})
    
    # --- Top categories ---
    top = time_patterns.get("top_categories", [])
    if top:
        cat, count = top[0]
        insights.append({
            "title": "Most Frequent Activity Category",
            "description": f"You spend most of your time on **{cat}** ({count} logged entries).",
            "data": top
        })

    # --- Productivity windows ---
    # Fix: Fetch "productivity_windows" from the nested time_patterns dictionary
    windows = time_patterns.get("productivity_windows", {})
    if isinstance(windows, dict) and windows:
        best_window = max(windows, key=windows.get)
        insights.append({
            "title": "Productivity Window",
            "description": f"Your most productive time of day is **{best_window}**.",
            "data": windows
        })

    # --- Streaks ---
    # Fix: Safely fetch streaks and default to an empty dict to prevent KeyError
    streaks = trends.get("streaks", {})
    longest_streak = streaks.get("longest_streak", 0)
    if longest_streak > 1:
        insights.append({
            "title": "Consistency Streak",
            "description": f"You had a **{longest_streak}-day streak** of logged activity.",
            "data": streaks
        })

    return insights


def generate_task_insights(patterns, trends):
    insights = []
    
    # Define task_patterns first so it's available for all blocks
    task_patterns = patterns.get("task_patterns", {})
    
    # --- Oldest Backlog Tasks ---
    # Now this works because task_patterns is already defined
    oldest = task_patterns.get("stagnant_tasks", []) 
    if oldest:
        titles = [t.get("title") for t in oldest]
        insights.append({
            "title": "Oldest Backlog Tasks",
            "description": f"The oldest tasks waiting are: {', '.join(titles)}.",
            "data": oldest
        })

    # --- Stagnant Tasks ---
    stagnant = task_patterns.get("stagnant_tasks", [])
    if stagnant:
        insights.append({
            "title": "Stagnant Tasks",
            "description": f"You have **{len(stagnant)}** tasks that haven't moved recently. Consider breaking them down.",
            "data": stagnant
        })
    # --- Fast Tasks ---
    fast = task_patterns.get("fast_tasks", [])  # Cleaned up to use your task_patterns variable
    if fast:
        insights.append({
            "title": "Fast-Moving Tasks",
            "description": f"You completed **{len(fast)} tasks** within 24 hours — nice momentum.",
            "data": fast
        })

    # --- Task Velocity (Fixed) ---
    velocity = trends.get("task_velocity", {})  # Default to empty dict instead of None
    avg_hours = velocity.get("average_completion_hours")  # Safely check for the key
    
    if avg_hours is not None:  # Ensure it exists and isn't None
        avg = round(avg_hours, 1)
        insights.append({
            "title": "Task Completion Speed",
            "description": f"Your average task completion time is **{avg} hours**.",
            "data": velocity
        })

    return insights

def generate_calendar_insights(patterns, trends):
    insights = []
    
    # Safely get calendar_patterns, defaulting to an empty dict if missing
    calendar_patterns = patterns.get("calendar_patterns", {})
    
    # Fix: Safely fetch busy_days with a default fallback list
    busy = calendar_patterns.get("busy_days", [])
    if busy:
        # Assuming busy is a list of tuples/lists or days like [("Monday", 3)]
        day, count = busy[0] if isinstance(busy[0], (list, tuple)) else (busy[0], "multiple")
        insights.append({
            "title": "Busiest Calendar Day",
            "description": f"Your calendar is most packed on **{day}** with {count} scheduled events.",
            "data": busy
        })

    # Apply the same safe fetching to meeting heavy days or total hours if they exist
    meeting_heavy = calendar_patterns.get("meeting_heavy_days", [])
    if meeting_heavy:
        insights.append({
            "title": "Meeting Heavy Days",
            "description": f"You have **{len(meeting_heavy)}** days upcoming with back-to-back meetings.",
            "data": meeting_heavy
        })

    return insights

# ============================================================
# INSIGHT QUERY NODE
# ============================================================

import json
import re

def llm_json_call(prompt: str) -> dict:
    """
    Calls the LLM and safely extracts JSON from the response.
    Ensures the insight intent interpreter always returns a valid dict.
    """

    raw = lite_llm.invoke(prompt)
    raw_content = raw.content if hasattr(raw, "content") else str(raw)
    if isinstance(raw_content, list):
        text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
    else:
        text = str(raw_content)

    # Extract JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"type": "unknown", "time_range": None, "category": None}

    try:
        return json.loads(match.group(0))
    except Exception:
        return {"type": "unknown", "time_range": None, "category": None}

def interpret_insight_question(question: str) -> dict:
    prompt = INSIGHT_QUERY_PROMPT.format(question=question)
    return llm_json_call(prompt)


def run_insight_query(intent, analysis, classified_tasks, classified_logs, classified_calendar):
    t = intent.get("type")

    if t == "top_category":
        return answer_top_category(analysis)

    if t == "busiest_day":
        return answer_busiest_day(analysis)

    if t == "productivity_window":
        return answer_productivity_window(analysis)

    if t == "streaks":
        return answer_streaks(analysis)

    if t == "category_trend":
        return answer_category_trend(analysis)

    if t == "task_aging":
        return answer_task_aging(classified_tasks)

    if t == "task_velocity":
        return answer_task_velocity(analysis)

    if t == "calendar_load":
        return answer_calendar_load(analysis)

    if t == "weekday_pattern":
        return answer_weekday_pattern(analysis)

    return {
        "answer": "I couldn’t map that question to your insights yet.",
        "details": {}
    }

def answer_top_category(analysis):
    top = analysis.get("time_patterns", {}).get("top_categories", [])
    if not top:
        return {"answer": "You have no logged activity.", "details": {}}

    cat, count = top[0]
    return {
        "answer": f"You spent most of your time on **{cat}** ({count} logs).",
        "details": {"top_categories": top}
    }

def answer_busiest_day(analysis):
    busy = analysis.get("calendar_patterns", {}).get("busy_days", [])
    if not busy:
        return {"answer": "I don’t see any busy days in your calendar.", "details": {}}

    day, count = busy[0]
    return {
        "answer": f"Your busiest day was **{day}** with {count} events.",
        "details": {"busy_days": busy}
    }

def answer_productivity_window(analysis):
    windows = analysis.get("time_patterns", {}).get("productivity_windows", {})
    if not windows:
        return {"answer": "I couldn’t detect a productivity window.", "details": {}}

    best = max(windows, key=windows.get)
    return {
        "answer": f"Your most productive time of day is **{best}**.",
        "details": {"windows": windows}
    }

def answer_streaks(analysis):
    streaks = analysis.get("streaks", {})
    longest = streaks.get("longest_streak", 0)

    if longest <= 1:
        return {"answer": "You don’t have any multi-day streaks yet.", "details": streaks}

    return {
        "answer": f"You had a **{longest}-day streak** of logged activity.",
        "details": streaks
    }

def answer_category_trend(analysis):
    trends = analysis.get("category_trends", {})
    if not trends:
        return {"answer": "I couldn’t detect any category trends.", "details": {}}

    # Find category with most growth
    growth = {}
    for cat, days in trends.items():
        if len(days) >= 2:
            first = days[min(days)]
            last = days[max(days)]
            growth[cat] = last - first

    if not growth:
        return {"answer": "No category shows meaningful change over time.", "details": trends}

    top_cat = max(growth, key=growth.get)
    return {
        "answer": f"Your fastest-growing category is **{top_cat}**.",
        "details": {"category_trends": trends, "growth": growth}
    }

def answer_task_aging(classified_tasks):
    backlog = classified_tasks.get("backlog", [])
    if not backlog:
        return {"answer": "You have no backlog tasks.", "details": {}}

    oldest = sorted(backlog, key=lambda t: t.get("createdAt", ""))

    return {
        "answer": f"Your oldest backlog task is **{oldest[0].get('title')}**.",
        "details": {"oldest_tasks": oldest}
    }

def answer_task_velocity(analysis):
    velocity = analysis.get("task_velocity", {})
    avg = velocity.get("average_completion_hours")

    if avg is None:
        return {"answer": "I couldn’t compute task velocity.", "details": velocity}

    return {
        "answer": f"Your average task completion time is **{avg:.1f} hours**.",
        "details": velocity
    }

def answer_calendar_load(analysis):
    load = analysis.get("calendar_trends", {})
    if not load:
        return {"answer": "Your calendar has no recorded load trends.", "details": {}}

    busiest = max(load, key=load.get)
    return {
        "answer": f"Your busiest calendar day was **{busiest}** with {load[busiest]} events.",
        "details": load
    }

def answer_weekday_pattern(analysis):
    weekday = analysis.get("time_patterns", {}).get("weekday_activity", {})
    if not weekday:
        return {"answer": "I couldn’t detect weekday activity patterns.", "details": {}}

    best = max(weekday, key=weekday.get)
    return {
        "answer": f"You’re most active on **{best}**.",
        "details": weekday
    }

def insight_query_node(state: dict) -> dict:
    question = state.get("original_question")
    analysis = state.get("analysis_output", {})
    classified = state.get("classified", {}).get("classified_tasks", {})
    logs = state.get("classified", {}).get("classified_logs", [])
    calendar = state.get("classified", {}).get("classified_calendar", [])

    if not question:
        return {
            **state,
            "relevance_grade": "conversational",
            "content_to_format": "I didn't receive a question to analyze."
        }

    # 1. Interpret the question
    intent = interpret_insight_question(question)

    # 2. Run the query
    answer = run_insight_query(
        intent=intent,
        analysis=analysis,
        classified_tasks=classified,
        classified_logs=logs,
        classified_calendar=calendar
    )

    # 3. THE FIX: Inject the calculated answer as a high-priority "document"
    doc = Document(
        page_content=f"SYSTEM ANALYTICS REPORT:\n{answer['answer']}",
        metadata={"source": "system_insight", "priority": True}
    )

    current_docs = state.get("documents", [])
    current_docs.append(doc)

    # 4. Return a NEW dictionary so LangGraph strictly registers the update.
    # We set relevance_grade="yes" so app.py uses the permissive RAG prompt.
    return {
        **state,
        "documents": current_docs,
        "relevance_grade": "yes",
        "content_to_format": answer["answer"]
    }

# ============================================================
# SHARED REACT-LOOP INFRASTRUCTURE (Mongo/GitHub/web all fold into tool_agent_node below)
# ============================================================
def resolve_recent_mention(messages: list, extractor, skip_predicate=None):
    """Scans messages most-recent-first, applying `extractor` to each message's content and
    returning the first (i.e. most recent) truthy result. `skip_predicate`, if given, skips
    a message's content entirely without trying to extract from it (e.g. bare approval
    replies like "yes"/"ok" that never carry the real topic). Returns None if nothing in
    history matches — callers apply their own final fallback."""
    for m in reversed(messages or []):
        content = getattr(m, "content", "") if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else str(m))
        if skip_predicate and skip_predicate(content):
            continue
        result = extractor(content)
        if result:
            return result
    return None


def _parse_agent_json(raw_text: str) -> dict:
    """Defensive JSON extraction shared by every ReAct-loop tool: tries direct parsing,
    then a regex-located JSON object, then gives up and returns {} (the loop treats a
    decision with no recognizable "action" as a failed step, not a crash)."""
    clean_text = raw_text.strip()
    clean_text = re.sub(r"^```(?:json|python)?\s*", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"\s*```$", "", clean_text)

    try:
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        json_match = re.search(r"(\{.*\})", clean_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


_EMPTY_OBSERVATION_VALUES = {"", "[]", "{}", "none", "null", "no results", "no results found"}


def _is_empty_observation(observation: str) -> bool:
    """An empty result (no matches, an empty list, an empty file listing) is not the same as
    "nothing exists" — it's very often a sign the query, path, repo, or collection was wrong,
    not proof of absence (this is exactly what happened with the repo-misresolution bug
    earlier: a wrong repo name didn't error, it just came back empty). Treated the same way
    an outright "ERROR: ..." is by the retry-nudge tracking in run_react_loop, since both are
    "this step didn't actually get you anywhere" — the model just can't tell that from the
    text alone without this check."""
    return observation.strip().lower() in _EMPTY_OBSERVATION_VALUES


_MAX_OBSERVATION_CHARS = 4000


def _truncate_observation(observation) -> str:
    """Every other action in this loop self-limits its own output (_read_file caps at 3500
    chars, _list_tree caps at 400 paths) — an unbounded PyMongo query was the one path with no
    cap at all, and a raw find() over a collection that stores embedding vectors is easily
    hundreds of documents with long float arrays each. That observation gets JSON-dumped
    straight into both the next reasoning prompt (_format_react_attempts) and the final
    footer (_format_observation_for_footer), so one huge result set is enough to blow past
    Gemini's 1,048,576-token input limit on the final Voice Composer call. Truncating once,
    right where every tool's observation is captured, protects all of them generically instead
    of special-casing Mongo."""
    text = observation if isinstance(observation, str) else json.dumps(observation, default=str, indent=2)
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    return text[:_MAX_OBSERVATION_CHARS] + f"\n... [truncated — {len(text)} total characters]"


class _UnsafeActionRequested(Exception):
    """Raised by run_react_loop when is_unsafe() flags a step, so the calling node can
    build its own tool-specific approval-required response instead of the loop guessing."""
    def __init__(self, decision: dict):
        self.decision = decision


class _ClarificationNeeded(Exception):
    """Raised by run_react_loop when the model reports genuine uncertainty (action="clarify")
    instead of guessing — carries the question to ask and the attempts made so far, so the
    calling node can pause for a real answer and resume the loop from where it left off
    instead of starting over."""
    def __init__(self, question: str, attempts: list):
        self.question = question
        self.attempts = attempts


# Card-marker text used the same way WRITE_ACTIONS' card_marker is: classify_intent scans the
# assistant's own previous message for this exact string to know a bare-looking reply is
# actually the user answering a paused clarification, not a fresh, unrelated message.
CLARIFICATION_CARD_MARKER = "Need a bit more information to continue"


def _format_react_attempts(attempts: list) -> str:
    if not attempts:
        return "(none yet — this is the first step)"
    return "\n\n".join(
        f"Attempt {i} — Purpose: {a['purpose']}\nAction: {a['action_desc']}\nObservation: {a['observation']}"
        for i, a in enumerate(attempts, 1)
    )


def _format_attempts_steps(attempts: list) -> str:
    """Renders attempts as 'Step N — purpose / Result' blocks — used both in a completed
    answer's footer and in a clarification pause message, so recovering attempts back out of
    either one (see _recover_attempts_from_steps_text) works identically."""
    return "\n\n".join(
        f"**Step {i} — {a['purpose']}:**\n```\n{a['action_desc']}\n```\n"
        f"**Result:**\n```\n{_format_observation_for_footer(a['observation'])}\n```"
        for i, a in enumerate(attempts, 1)
    )


_ATTEMPT_STEP_RE = re.compile(
    r"\*\*Step \d+ — (.+?):\*\*\n```\n(.*?)\n```\n\*\*Result:\*\*\n```\n(.*?)\n```",
    re.DOTALL,
)


def _recover_attempts_from_steps_text(content: str) -> list:
    """Parses 'Step N — purpose / Result' blocks (see _format_attempts_steps) back into the
    {purpose, action_desc, observation} shape run_react_loop works with, so a clarification
    pause's attempts-so-far can seed the loop on resume instead of it starting from zero."""
    return [
        {"purpose": purpose, "action_desc": action_desc, "observation": observation}
        for purpose, action_desc, observation in _ATTEMPT_STEP_RE.findall(content)
    ]


async def run_react_loop(
    *,
    question: str,
    schema: str,
    prompt_template: str,
    act,
    is_unsafe=lambda decision: False,
    max_iterations: int,
    node_name: str,
    initial_attempts: list | None = None,
    max_retry_nudges: int = 1,
    llm=lite_llm,
) -> dict:
    """Generic Reason -> Act -> Observe -> Decide loop shared by every iterative tool
    (MongoDB, GitHub search, ...). Each step asks the model for the next action given
    everything tried so far; the model decides for itself when it has enough to answer,
    or is honest that it doesn't. Returns {"final_answer": str, "attempts": list[dict],
    "show_work": bool} — show_work is the model's own call on whether its step-by-step trace
    is worth repeating in the chat message itself, not just the live trace panel (see
    show_work's schema entry in TOOL_AGENT_PROMPT); defaults true when a "final" doesn't set it.
    Raises _UnsafeActionRequested if is_unsafe() ever flags a proposed action, and
    _ClarificationNeeded if the model reports genuine uncertainty instead of guessing.
    `initial_attempts`, when given, seeds the loop with attempts already made in an earlier
    call — the resume side of a clarification pause, so the loop continues instead of
    starting from zero. `llm` defaults to lite_llm; deep thinking mode passes lite_llm_deep
    instead, so a wider step/nudge budget also comes with more carefully reasoned individual
    step decisions rather than just more of them.

    A prompt-level instruction alone isn't enough to stop the model from giving up right after
    a failed OR empty action even when steps remain — it already has evidence of that ("never
    claim something exists without verifying it" was in the prompt and got broken anyway, in
    the exact scenario this guards). retry_nudge_count enforces it mechanically instead of just
    asking nicely: whenever the loop sees an outstanding failed-or-empty action with steps
    still available, it tells the model so and, if the model tries to conclude anyway, rejects
    that "final" and forces one more real step — up to max_retry_nudges times per call (default
    1). It stops rejecting after that budget is spent, so a genuinely doomed action (a real
    404, a real rate limit, a search that's empty no matter how it's phrased) still gets an
    honest "final" rather than looping forever. Deep thinking mode raises this budget alongside
    max_iterations, since a higher step cap alone doesn't help if the loop only gets to
    second-guess itself once.

    "Outstanding failed-or-empty action" is tracked per tool_action name, not just "did the
    last step fail" — a real failure mode this caught: read_repo_file 404s, list_repo_tree (a
    different action, taken to diagnose the 404) then succeeds, and the model concludes right
    there without ever actually retrying the read. Checking only the last observation would
    see the successful list and never nudge, even though the thing the user actually asked for
    was never retrieved. Empty results are tracked the same way as outright errors (see
    _is_empty_observation) — an empty search result is very often a sign of looking in the
    wrong place (wrong repo, wrong collection, wrong query), not proof nothing exists.
    unretried_inconclusive_tools tracks every tool_action that has failed or come back empty
    and not since been attempted again, regardless of what ran in between."""
    attempts: list = list(initial_attempts or [])
    final_answer = None
    # Defaults true (show the receipt) whenever a "final" doesn't explicitly say otherwise —
    # a missing/malformed field is more likely a parsing hiccup than a deliberate "hide this",
    # so err toward the more transparent option rather than silently dropping useful context.
    show_work = True
    retry_nudge_count = 0
    unretried_inconclusive_tools: set = set()

    for step in range(max_iterations):
        forced_final = step == max_iterations - 1
        # Shown on every step with an outstanding failure or empty result, not just once —
        # only the actual rejection of a premature "final" below is budget-limited (via
        # retry_nudge_count), so the model still sees the reminder if it takes an unrelated
        # detour (like listing the repo tree) before eventually trying to conclude.
        needs_retry_nudge = not forced_final and bool(unretried_inconclusive_tools)

        question_for_step = question
        if forced_final:
            question_for_step += (
                "\n\n(You have used all your steps. You MUST return "
                "action=\"final\" now, honestly summarizing what you tried and found.)"
            )
        if needs_retry_nudge:
            failed_tools = ", ".join(sorted(unretried_inconclusive_tools))
            question_for_step += (
                f"\n\n(One or more of your actions failed or came back empty and was never "
                f"successfully retried ({failed_tools}), and you still have steps remaining — "
                "an empty result often means the query, path, or scope was wrong, not that "
                "nothing exists. Actually retry it with corrected information (a different "
                "diagnostic action, like listing the repo "
                "tree, does not count as retrying it) before concluding. Only choose "
                "action=\"final\" now if you are certain nothing else could help.)"
            )

        prompt = prompt_template.format(
            question=question_for_step,
            schema=schema,
            attempts=_format_react_attempts(attempts),
        )

        try:
            response = await llm.ainvoke(prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content]) if isinstance(resp_content, list) else str(resp_content)
            decision = _parse_agent_json(raw_text)
        except Exception:
            logger.exception("[%s] step %s failed to produce a usable decision.", node_name, step + 1)
            break

        action = decision.get("action")
        if action == "final" and needs_retry_nudge and retry_nudge_count < max_retry_nudges:
            # Told to retry and it tried to conclude anyway — force one more real step instead
            # of accepting a premature answer. retry_nudge_count only increments here (at the
            # actual rejection), not just when the nudge was shown, so a detour in between
            # (e.g. it lists the repo tree first) doesn't spend the budget for free.
            retry_nudge_count += 1
            continue
        if action == "final":
            final_answer = decision.get("answer") or "I wasn't able to find a conclusive answer."
            show_work = decision.get("show_work")
            show_work = show_work if isinstance(show_work, bool) else True
            logger.info(
                "[%s] Step %s: accepted final answer after %s real action(s) — %r",
                node_name, step + 1, len(attempts), final_answer[:200],
            )
            break

        if action == "clarify":
            question_text = decision.get("question") or "I need a bit more information to continue — could you clarify?"
            raise _ClarificationNeeded(question_text, attempts)

        if action != "query":
            attempts.append({
                "purpose": decision.get("purpose", "(unclear)"),
                "action_desc": "(no valid action returned)",
                "observation": "ERROR: model did not return a recognized action",
            })
            continue

        purpose = decision.get("purpose", "Working...")
        logger.info(f"[{node_name}] Step {step+1}: {purpose}")
        await safe_emit_event("trace_detail", {"node": node_name, "title": "Working...", "detail": purpose})

        if is_unsafe(decision):
            raise _UnsafeActionRequested(decision)

        try:
            observation = act(decision)
            if asyncio.iscoroutine(observation):
                observation = await observation
        except Exception as e:
            observation = f"ERROR: {e}"
        observation = _truncate_observation(observation)

        tool_action_name = decision.get("tool_action") or ""
        if tool_action_name:
            if tool_action_name in unretried_inconclusive_tools:
                # Attempting a previously-failed-or-empty tool again satisfies "it was
                # retried" even if this new attempt also fails or comes back empty — a
                # genuinely doomed action (a real 404, a search that's empty no matter how
                # it's phrased) shouldn't force a second forced extra step on top of the
                # honest retry it already got.
                unretried_inconclusive_tools.discard(tool_action_name)
            elif observation.startswith("ERROR") or _is_empty_observation(observation):
                unretried_inconclusive_tools.add(tool_action_name)
        args_summary = ", ".join(f"{k}={v}" for k, v in (decision.get("args") or {}).items())
        action_desc = f"{tool_action_name}({args_summary})" if tool_action_name else (args_summary or "")
        logger.info(
            "[%s] Step %s result — action=%s | observation=%r",
            node_name, step + 1, action_desc or "(none)", observation[:200],
        )
        attempts.append({
            "purpose": purpose,
            "action_desc": action_desc,
            "observation": observation,
        })

    if final_answer is None:
        # Loop ran out of steps without an explicit final action — force one last honest
        # synthesis instead of silently returning the last raw observation.
        try:
            prompt = prompt_template.format(
                question=(
                    f"{question}\n\n(You are out of steps. You MUST return action=\"final\" now, "
                    "honestly summarizing what you tried and found — never invent an answer "
                    "beyond what the attempts above actually show.)"
                ),
                schema=schema,
                attempts=_format_react_attempts(attempts),
            )
            response = await llm.ainvoke(prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content]) if isinstance(resp_content, list) else str(resp_content)
            decision = _parse_agent_json(raw_text)
            final_answer = decision.get("answer") or "I wasn't able to find a conclusive answer after several attempts."
        except Exception:
            logger.exception("[%s] final synthesis step failed.", node_name)
            final_answer = "I wasn't able to find a conclusive answer after several attempts."

    return {"final_answer": final_answer, "attempts": attempts, "show_work": show_work}


# ============================================================
# TOOL AGENT — unified Mongo + GitHub + web research loop
# ============================================================

_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")


# Directory/programming nouns so common across virtually any repo's structure that a bare
# two-segment mention built from one is far more likely to be a path fragment ("the fix is in
# backend/services", "check local/src") than a real GitHub owner/repo — a real repo owner is
# effectively never named "backend" or "services". This has to be repo-agnostic (not just this
# project's own folder names) so pointing the assistant at a genuinely different repo doesn't
# reintroduce the exact same false-positive class against THAT repo's directories instead.
_GENERIC_PATH_SEGMENTS = {
    "backend", "frontend", "local", "src", "lib", "libs", "app", "apps", "services", "service",
    "utils", "util", "components", "component", "models", "model", "tests", "test", "docs",
    "doc", "config", "configs", "scripts", "script", "node_modules", "dist", "build", "assets",
    "public", "static", "vendor", "bin", "include", "source", "sources",
}


def extract_github_repo(text: str | None, fallback: str = "SummonShenron/SAAPP") -> str:
    """Extract an owner/repo from a prompt or state, falling back to SAAPP only when needed.

    A real repo reference needs an unambiguous signal: a github.com URL, an explicit "repo"/
    "repository"/"target repo" cue, a backtick-wrapped slug, or (as a last resort) a bare
    "owner/repo"-shaped token with no such cue at all — e.g. "look at facebook/react instead".
    "for"/"in" used to also count as a trigger, but they're common English prepositions, not
    repo-mention markers — "its in backend/services/agent_workflow.py" matched "in" +
    "backend/services" (stopping at the second "/") and handed that back as a real repo, which
    then 404'd on every GitHub API call built from it — the actual root cause of a 404 that
    persisted across process restarts, because nothing about the environment was ever the
    problem. Dropping them costs nothing: the bare-token pattern below already independently
    catches genuinely-intended mentions like "facebook/react" with no keyword needed at all.

    The lookbehind/lookahead require any match to be a standalone two-segment token, not a
    slice out of a longer path (blocked by a "/" immediately before or after); the
    file-extension check rejects a match whose second segment ends like a filename
    (agent_workflow.py) rather than a repo name; and _GENERIC_PATH_SEGMENTS rejects a candidate
    where either segment is a directory name common enough to belong to any repo's own
    structure rather than actually naming one — belt-and-suspenders against the same class of
    false positive, generalized beyond just this project's own folder names.
    """
    if not text:
        return fallback

    patterns = [
        r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
        r"(?:repo(?:sitory)?|target(?:\s+repo)?)[\s:`]*(?<![\w/.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w/.-])",
        r"`(?<![\w/.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w/.-])`",
        r"(?<![\w/.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w/.-])",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group(1).strip().rstrip("/")
            if candidate.lower().endswith(".git"):
                candidate = candidate[:-len(".git")]
            if _FILE_EXTENSION_RE.search(candidate):
                continue
            segments = [s.lower() for s in candidate.split("/")]
            if any(s in _GENERIC_PATH_SEGMENTS for s in segments):
                continue
            return candidate

    return fallback


def extract_pr_request_details(text: str | None, fallback_repo: str = "SummonShenron/SAAPP") -> dict:
    """Parse repo + merge branch info from a natural-language PR request."""
    details = {
        "repo": fallback_repo,
        "head_branch": None,
        "base_branch": None,
    }

    if not text:
        return details

    # extract_github_repo already applies the anchored, file-path-safe matching — re-matching
    # the same "for/in/repo X/Y" shape here with the old unanchored pattern would just overwrite
    # a correct result with the same false-positive-on-file-paths bug fixed there.
    details["repo"] = extract_github_repo(text, fallback_repo)

    merge_match = re.search(
        r"merge\s+([\w\-/\.]+)\s+into\s+([\w\-/\.]+)",
        text,
        re.IGNORECASE,
    )
    if merge_match:
        details["head_branch"] = merge_match.group(1).strip()
        details["base_branch"] = merge_match.group(2).strip()
        return details

    branch_match = re.search(
        r"(?:from|head(?:\s+branch)?|branch)\s+([\w\-/\.]+)\s+(?:to|into|against)\s+([\w\-/\.]+)",
        text,
        re.IGNORECASE,
    )
    if branch_match:
        details["head_branch"] = branch_match.group(1).strip()
        details["base_branch"] = branch_match.group(2).strip()

    return details

def _format_observation_for_footer(observation) -> str:
    if isinstance(observation, str):
        return observation
    return json.dumps(observation, default=str, indent=2)


async def tool_agent_node(state: GraphState) -> Dict[str, Any]:
    """Unified read-only research agent: Mongo (admin-only), GitHub, and web search all live
    as actions in one ReAct loop, so the model can reach for whichever tool (or sequence of
    tools) a question actually needs — e.g. check the repo, then search the web if the repo
    alone wasn't conclusive — instead of being confined to one tool per turn.

    Phase 1: read-only tools only. Phase 2 unified every write action (PR, issue, and Mongo
    writes proposed by this loop) onto one WRITE_ACTIONS registry — a proposed Mongo write
    here hands off to execute_write_node via the same pending_action mechanism PR/issue use,
    rather than a bespoke approval path of its own."""
    state = ensure_workflow_keys(state)
    username = state.get("username")
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "tool_agent_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        user_groups = load_user_directory_groups(username)
        is_admin = "Global_Admins" in user_groups

        await safe_emit_event(
            "trace_detail",
            {"node": "tool_agent_node", "title": "Investigating...", "detail": "Preparing available tools..."}
        )

        latest_message_content = state.get("messages", [])[-1].content.strip()
        msg = latest_message_content

        # Resuming a paused clarification: the previous assistant message is the question it
        # asked, and the one before that is the original request. Recombine them into one
        # question for the loop and recover the attempts already made, so it continues instead
        # of starting over — pending_action never survives between real turns (see classify_intent's
        # card_marker comment above), so reconstructing from persisted messages is the only way.
        all_messages = state.get("messages", [])
        resumed_attempts: list = []
        if len(all_messages) >= 2 and CLARIFICATION_CARD_MARKER in _content_of(all_messages[-2]):
            resumed_attempts = _recover_attempts_from_steps_text(_content_of(all_messages[-2]))
            original_question = _content_of(all_messages[-3]) if len(all_messages) >= 3 else msg
            msg = (
                f"{original_question}\n\n"
                f"(You previously asked the user for clarification; they answered: \"{msg}\")"
            )

        # Resolve the repo BEFORE folding in attached/pasted code — arbitrary pasted content
        # can contain its own "word/word"-shaped substrings that would otherwise hijack
        # repo detection. Priority: an explicit repo mention in the message the user just sent
        # always wins over a pinned/persisted repo setting (state["repo"], set via the target-repo
        # banner) — a pin is easy to forget about, and it would otherwise silently override even
        # an unambiguous "check facebook/react instead" with no way to tell the model meant it.
        # Only when THIS message says nothing does the pin apply, then the older scan-the-whole
        # -history fallback, then the hardcoded default.
        repo = extract_github_repo(latest_message_content, fallback=None) or state.get("repo") or resolve_recent_mention(
            state.get("messages", []), lambda c: extract_github_repo(c, fallback=None)
        ) or "SummonShenron/SAAPP"

        documents = state.get("documents", []) or []
        attachment_docs = [d for d in documents if d.metadata.get("source") == "user_attachment_summary"]
        if attachment_docs:
            attached_text = "\n\n".join(d.page_content for d in attachment_docs)
            msg = f"{msg}\n\nATTACHED CODE (from this turn):\n{attached_text}"

        token = os.getenv("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Accept": "vnd.github+json"}
        api_base = "https://api.github.com"
        gh_base = "https://github.com"

        def _get_default_branch():
            repo_res = requests.get(f"{api_base}/repos/{repo}", headers=headers)
            if repo_res.status_code != 200:
                logger.warning(
                    "[tool_agent_node] Could not fetch repo metadata for %r (status %s) — "
                    "falling back to branch 'main'. Every GitHub action this turn will target "
                    "this exact repo, so if that's wrong, every one of them will 404.",
                    repo, repo_res.status_code,
                )
                return "main"
            return repo_res.json().get("default_branch", "main")

        default_branch = await asyncio.to_thread(_get_default_branch)
        logger.info("[tool_agent_node] Resolved repo=%r, default_branch=%r", repo, default_branch)

        # Mongo collection names are only resolved (and only ever offered as an action) for
        # admins — skips a needless DB round-trip for everyone else, and means non-admins
        # never see run_mongo_query mentioned in their action menu at all.
        mongo_schema = None
        db = None
        if is_admin:
            db = get_db()
            if hasattr(db, "list_collection_names") is False and hasattr(db, "list_database_names"):
                db = db.get_default_database() or db[list(db.list_database_names())[0]]
            try:
                mongo_schema = ", ".join(db.list_collection_names())
            except Exception:
                logger.exception("Failed to list collections; proceeding without Mongo schema.")
                mongo_schema = "(unable to list collections)"

        schema_parts = [f"repo={repo}", f"default_branch={default_branch}"]
        if is_admin:
            schema_parts.append(f"mongodb_collections={mongo_schema}")
        schema = ", ".join(schema_parts)

        # --- action implementations (reused as-is from the single-tool nodes they replace) ---
        unsafe_keywords = ["insert", "update", "delete", "drop", "remove", "replace", "write"]
        exec_builtins = {
            "range": range, "len": len, "str": str, "int": int,
            "float": float, "list": list, "dict": dict, "set": set,
            "tuple": tuple, "min": min, "max": max, "sum": sum, "round": round,
            "enumerate": enumerate, "zip": zip
        }

        def _run_mongo_code(code: str):
            local_scope = {"db": db, "username": username, "result": None}
            exec(code, {"__builtins__": exec_builtins}, local_scope)
            return local_scope.get("result", None)

        def _list_tree():
            tree_url = f"{api_base}/repos/{repo}/git/trees/{default_branch}?recursive=1"
            res = requests.get(tree_url, headers=headers)
            if res.status_code != 200:
                return f"ERROR: could not fetch tree ({res.status_code})"
            tree_items = res.json().get("tree", [])
            paths = [
                item.get("path") for item in tree_items
                if item.get("type") == "blob"
                and not any(exclude in item.get("path", "") for exclude in ["node_modules", "dist", "__pycache__"])
            ]
            return "\n".join(paths[:400])

        def _read_file(path: str):
            if not path:
                return "ERROR: no path given"
            file_url = f"{api_base}/repos/{repo}/contents/{path}"
            res = requests.get(file_url, headers=headers)
            if res.status_code != 200:
                return f"ERROR: could not fetch {path} ({res.status_code})"
            file_data = res.json()
            try:
                decoded = base64.b64decode(file_data.get("content", "")).decode("utf-8", errors="replace")
            except Exception:
                return f"ERROR: could not decode {path}"
            html_url = f"{gh_base}/{repo}/blob/{default_branch}/{path}"
            snippet = decoded[:3500] + ("\n... [truncated]" if len(decoded) > 3500 else "")
            return f"URL: {html_url}\n{snippet}"

        def _diff_branches(base: str, head: str):
            if not base or not head:
                return "ERROR: base and head branches are required"
            return fetch_branch_diff_summary(repo, base, head)

        def _list_commits(branch: str, limit):
            branch = branch or default_branch
            try:
                limit = min(int(limit or 10), 30)
            except (TypeError, ValueError):
                limit = 10
            res = requests.get(
                f"{api_base}/repos/{repo}/commits", headers=headers,
                params={"sha": branch, "per_page": limit},
            )
            if res.status_code != 200:
                return f"ERROR: could not fetch commits ({res.status_code})"
            commits = res.json()
            return "\n".join(f"{c['sha'][:7]} — {c['commit']['message'].splitlines()[0]}" for c in commits)

        # --- dynamic, per-user action menu — non-admins never even see run_mongo_query ---
        menu_lines = []
        if is_admin:
            menu_lines.append(
                "- run_mongo_query — args: code (a string of executable PyMongo code that assigns "
                "its output to a variable named result; wrap cursor operations like find()/aggregate() "
                "in list(...); for text-field matching prefer a MongoDB regex with case-insensitive "
                "options over strict equality)"
            )
        menu_lines.extend([
            "- list_repo_tree — no args; lists every file path in the repo",
            "- read_repo_file — args: path (relative file path within the repo)",
            "- diff_branches — args: base (branch name), head (branch name)",
            "- list_commits — args: branch (branch name), limit (max number of commits, integer)",
            "- web_search — args: query (the exact search query string to run)",
            "- run_python — args: code (a small, self-contained Python snippet; print(...) "
            "whatever you need to see — no filesystem, network, or subprocess access is "
            "available, and only these stdlib modules can be imported: "
            f"{', '.join(sorted(SAFE_IMPORT_ALLOWLIST))}. Use this for calculations, data "
            "shaping, or checking your own logic — not for anything requiring I/O.)",
        ])
        actions_menu = "\n".join(menu_lines)
        prompt_template = TOOL_AGENT_PROMPT.replace("{actions_menu}", actions_menu.replace("{", "{{").replace("}", "}}"))

        def _is_unsafe(decision: dict) -> bool:
            if decision.get("tool_action") != "run_mongo_query":
                return False
            code = ((decision.get("args") or {}).get("code") or "").lower()
            return any(kw in code for kw in unsafe_keywords)

        async def _act(decision: dict):
            tool_action = decision.get("tool_action")
            args = decision.get("args") or {}

            if tool_action == "run_mongo_query":
                if not is_admin:
                    return "ERROR: not authorized for this action"
                return await asyncio.to_thread(_run_mongo_code, args.get("code", "") or "")
            if tool_action == "web_search":
                query = args.get("query") or msg
                search = DuckDuckGoSearchAPIWrapper()
                results = await asyncio.to_thread(search.results, query, max_results=3)
                return [r for r in results if isinstance(r, dict)] if results else []
            if tool_action == "run_python":
                # run_python_sandboxed always returns {"output", "error"} and never raises —
                # normalized to this loop's own "ERROR: ..." string convention (used by every
                # other action) so a failed run gets picked up by the retry-nudge tracking in
                # run_react_loop the same way a failed GitHub/Mongo call already does.
                sandbox_result = await run_python_sandboxed(args.get("code", "") or "")
                if sandbox_result.get("error"):
                    return f"ERROR: {sandbox_result['error']}"
                return sandbox_result.get("output", "")

            def _dispatch_github():
                if tool_action == "list_repo_tree":
                    return _list_tree()
                if tool_action == "read_repo_file":
                    return _read_file(args.get("path"))
                if tool_action == "diff_branches":
                    return _diff_branches(args.get("base"), args.get("head"))
                if tool_action == "list_commits":
                    return _list_commits(args.get("branch"), args.get("limit"))
                return f"ERROR: unrecognized tool_action '{tool_action}'"

            return await asyncio.to_thread(_dispatch_github)

        deep_thinking = bool(state.get("deep_thinking"))
        try:
            loop_result = await run_react_loop(
                question=msg,
                schema=schema,
                prompt_template=prompt_template,
                act=_act,
                is_unsafe=_is_unsafe,
                max_iterations=TOOL_AGENT_MAX_ITERATIONS_DEEP if deep_thinking else TOOL_AGENT_MAX_ITERATIONS,
                node_name="tool_agent_node",
                initial_attempts=resumed_attempts,
                max_retry_nudges=TOOL_AGENT_MAX_RETRY_NUDGES_DEEP if deep_thinking else TOOL_AGENT_MAX_RETRY_NUDGES,
                llm=lite_llm_deep if deep_thinking else lite_llm,
            )
        except _UnsafeActionRequested as e:
            drafted_code = (e.decision.get("args") or {}).get("code", "") or ""
            purpose = e.decision.get("purpose", "Database query")
            summary_line = f"Ready to run this database operation:\n```python\n{drafted_code}\n```\n**Purpose:** {purpose}"
            approval_message = (
                "**Approval Required**\n\n"
                f"{summary_line}\n\n"
                "*Please Approve, Modify parameters, or Reject this action.*"
            )
            new_messages = list(state.get("messages", [])) + [AIMessage(content=approval_message)]
            return {
                **state,
                "pending_action": {"action_type": "run_mongo_write", "details": {"code": drafted_code, "purpose": purpose}},
                "relevance_grade": "hitl_approval_required",
                "generation": approval_message,
                "content_to_format": approval_message,
                "messages": new_messages,
            }
        except _ClarificationNeeded as e:
            steps_so_far = _format_attempts_steps(e.attempts)
            clarification_message = (
                f"**{CLARIFICATION_CARD_MARKER}:**\n\n{e.question}"
                + (f"\n\n**What I've checked so far:**\n\n{steps_so_far}" if steps_so_far else "")
            )
            new_messages = list(state.get("messages", [])) + [AIMessage(content=clarification_message)]
            return {
                **state,
                "relevance_grade": "needs_clarification",
                "generation": clarification_message,
                "content_to_format": clarification_message,
                "messages": new_messages,
            }

        final_answer = loop_result["final_answer"]
        attempts = loop_result["attempts"]

        # The live trace panel already shows every step in real time — repeating it in the chat
        # message itself is only worth doing when the model says the receipt genuinely adds
        # value (debugging, an inconclusive answer, verifying a specific claim), not on every
        # tool_agent reply regardless of how casual the question was. See show_work's schema
        # entry in TOOL_AGENT_PROMPT.
        steps_text = _format_attempts_steps(attempts) if loop_result.get("show_work", True) else ""
        output_msg = f"{final_answer}\n\n{steps_text}" if steps_text else final_answer

        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {"input": node_input, "output": node_output}
            }
        )
        return {
            **state,
            "drafted_code": None,
            "code_approval_status": "completed",
            "content_to_format": output_msg,
            "relevance_grade": "tool_agent"
        }

def resolve_pr_number(user_msg: str, repo: str, headers: dict, api_base: str) -> int | None:
    """Parses natural language (first, last, 5th, PR #2) and resolves the target PR number."""
    msg_lower = user_msg.lower()

    # 1. Handle "latest / last / newest / most recent"
    if any(word in msg_lower for word in ["latest", "most recent", "last", "newest", "recent"]):
        logger.info(f"Fetching most recent PR for {repo}...")
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=1", headers=headers)
        if res.status_code == 200 and res.json():
            return res.json()[0].get("number")

    # 2. Handle "first / oldest / initial"
    if any(word in msg_lower for word in ["first", "oldest", "initial"]):
        logger.info(f"Fetching initial/first PR for {repo}...")
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=asc&per_page=1", headers=headers)
        if res.status_code == 200 and res.json():
            return res.json()[0].get("number")

    # 3. Handle Ordinal Words ("second", "5th", "6th", etc.)
    ordinal_map = {
        "first": 1, "1st": 1,
        "second": 2, "2nd": 2,
        "third": 3, "3rd": 3,
        "fourth": 4, "4th": 4,
        "fifth": 5, "5th": 5,
        "sixth": 6, "6th": 6,
        "seventh": 7, "7th": 7,
        "eighth": 8, "8th": 8,
        "ninth": 9, "9th": 9,
        "tenth": 10, "10th": 10,
    }
    
    target_idx = None
    for word, idx in ordinal_map.items():
        if re.search(rf"\b{word}\b", msg_lower):
            target_idx = idx
            break

    # 4. Fallback to general regex digit matching ("#5", "PR 5", "5")
    if target_idx is None:
        match = re.search(r"#?(\d+)", msg_lower)
        if match:
            target_idx = int(match.group(1))

    if target_idx is not None:
        # Check if direct PR #target_idx exists on GitHub
        check_res = requests.get(f"{api_base}/repos/{repo}/pulls/{target_idx}", headers=headers)
        if check_res.status_code == 200:
            return target_idx

        # Otherwise, fetch list by creation index (1-based index)
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=asc&per_page=100", headers=headers)
        if res.status_code == 200 and res.json():
            prs = res.json()
            if 1 <= target_idx <= len(prs):
                return prs[target_idx - 1].get("number")

    return None


async def pr_summarizer_node(state: GraphState) -> dict:
    logger.info("--- PR SUMMARIZER NODE CALLED ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "github_search_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        # 1. Emit live thought to the UI
        await safe_emit_event(
            "trace_detail",
            {
                "node": "pr_summarizer_node",
                "title": "Analyzing Pull Request...",
                "detail": "Fetching PR details and code diffs from GitHub..."
            }
        )

        # Recency-first: a correction turn ("no, I meant a different repo") must win over
        # whatever was mentioned earlier — joining all history into one string and searching
        # once (the old approach) returns the *first* match in reading order, i.e. the
        # earliest-mentioned repo, which is backwards for exactly this pattern.
        repo = state.get("repo") or resolve_recent_mention(
            state.get("messages", []), lambda c: extract_github_repo(c, fallback=None)
        ) or "SummonShenron/SAAPP"
        token = os.getenv("GITHUB_TOKEN")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }
        api_base = "https://api.github.com"

        messages = state.get("messages", [])

        # 2. Offload blocking PR resolution and file fetches to a background thread. PR
        # resolution is resolved the same recency-first way as repo — a PR referenced two
        # turns ago ("review PR #4" ... "any concerns with it?") shouldn't be lost just
        # because only the single latest message used to be checked, and resolve_pr_number
        # itself makes blocking HTTP calls, so the scan has to stay inside this thread.
        def _fetch_pr_data():
            pr_num = state.get("pr_number") or resolve_recent_mention(
                messages, lambda c: resolve_pr_number(c, repo, headers, api_base)
            )
            if not pr_num:
                return None, None

            files_url = f"{api_base}/repos/{repo}/pulls/{pr_num}/files"
            files_res = requests.get(files_url, headers=headers)
            return pr_num, files_res

        pr_number, files_res = await asyncio.to_thread(_fetch_pr_data)

        if not pr_number:
            output_text = f"Could not locate the requested Pull Request for `{repo}`. Please specify a PR number (e.g., 'Review PR #2')."
            return {
                **state,
                "content_to_format": output_text,
                "pr_summary": output_text,
                "relevance_grade": "pr_summary"
            }

        if files_res.status_code != 200:
            output_text = f"Failed to fetch PR #{pr_number} files: {files_res.text}"
            return {
                **state,
                "content_to_format": output_text,
                "pr_summary": output_text,
                "relevance_grade": "pr_summary"
            }

        changed_files = files_res.json()
        diff_context = []
        for f in changed_files[:10]:
            filename = f.get("filename")
            status = f.get("status")
            patch = f.get("patch", "No patch available")
            diff_context.append(f"File: {filename} ({status})\nPatch:\n```diff\n{patch}\n```")

        formatted_diffs = "\n\n".join(diff_context)

        # Generate LLM summary
        if "{diffs}" in PR_REVIEW_PROMPT:
            review_prompt = PR_REVIEW_PROMPT.format(diffs=formatted_diffs)
        else:
            review_prompt = f"{PR_REVIEW_PROMPT}\n\nPull Request Diffs:\n{formatted_diffs}"

        try:
            # 3. Use ainvoke for non-blocking LLM review generation
            review_response = await lite_llm.ainvoke(review_prompt)
            raw_content = getattr(review_response, "content", review_response)

            if isinstance(raw_content, list):
                text_blocks = []
                for block in raw_content:
                    if isinstance(block, str):
                        text_blocks.append(block)
                    elif isinstance(block, dict) and "text" in block:
                        text_blocks.append(block["text"])
                comment_body = "\n".join(text_blocks).strip()
            else:
                comment_body = str(raw_content).strip()
        except Exception as e:
            comment_body = f"Could not generate automated PR summary: {str(e)}"

        output_text = f"### PR Review Summary for {repo} #{pr_number}\n\n{comment_body}"
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={
                "service": "SAAPP",
                "erragent_context": {
                    "input": node_input,
                    "output": node_output,
                }
            }
        )
        return {
            **state,
            "pr_summary": comment_body,
            "content_to_format": output_text,
            "relevance_grade": "pr_summary"
        }

def fetch_branch_diff_summary(repo: str, base: str, head: str) -> str:
    """Fetches recent commit messages and changed files between two branches."""
    token = os.getenv("GITHUB_TOKEN")
    url = f"https://api.github.com/repos/{repo}/compare/{base}...{head}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        return "No diff context available."
    
    data = res.json()
    
    # Extract commit messages
    commits = [c["commit"]["message"].strip() for c in data.get("commits", [])]
    # Extract changed filenames
    files = [f["filename"] for f in data.get("files", [])]
    
    summary = f"Commits ({len(commits)}):\n- " + "\n- ".join(commits[:10])
    summary += f"\n\nFiles Changed ({len(files)}):\n- " + "\n- ".join(files[:15])
    return summary

def _content_of(msg) -> str:
    return getattr(msg, "content", "") if hasattr(msg, "content") else (msg.get("content", "") if isinstance(msg, dict) else str(msg))


# =========================================================================
# WRITE_ACTIONS registry — Phase 2: every write-capable tool (PR, issue,
# Mongo writes, and whatever comes next) registers its draft/execute/recovery
# logic here instead of getting its own draft/execute node pair. The actual
# per-action logic below is reused verbatim from the original draft_pr_node/
# execute_pr_node/draft_issue_node/execute_issue_node — only the boilerplate
# around it (RBAC check, approval parsing, history recovery, card envelope)
# is now shared, in propose_write_node/execute_write_node further down.
# =========================================================================

def _draft_create_pr(state: GraphState) -> tuple[dict, str]:
    messages = state.get("messages", [])
    last_msg = messages[-1].content.strip() if messages else ""
    pending_action = state.get("pending_action") or {}
    existing_details = pending_action.get("details", {})

    repo_fallback = (
        existing_details.get("repo")
        or state.get("repo")
        or resolve_recent_mention(messages, lambda c: extract_github_repo(c, fallback=None))
        or "SummonShenron/SAAPP"
    )
    parsed = extract_pr_request_details(last_msg, repo_fallback)
    repo = existing_details.get("repo") or state.get("repo") or parsed["repo"]

    match = re.search(r"merge\s+([\w\/\-\.]+)\s+into\s+([\w\/\-\.]+)", last_msg, re.IGNORECASE)
    if match:
        head_branch, base_branch = match.group(1), match.group(2)
    elif existing_details.get("head_branch"):
        head_branch, base_branch = existing_details["head_branch"], existing_details["base_branch"]
    elif parsed.get("head_branch") and parsed.get("base_branch"):
        head_branch, base_branch = parsed["head_branch"], parsed["base_branch"]
    else:
        head_branch = state.get("head_branch", "feature-branch")
        base_branch = state.get("base_branch", "main")

    logger.info(f"[propose_write_node:create_pr] Fetching real branch diff for {repo}: {base_branch} <- {head_branch}")
    diff_context = fetch_branch_diff_summary(repo, base_branch, head_branch)

    try:
        formatted_prompt = DRAFT_PR_PROMPT.format(
            user_message=last_msg,
            context=f"Repository: {repo}\nBase Branch: {base_branch}\nHead Branch: {head_branch}\n\n{diff_context}",
        )
        llm_response = get_chat_llm(state.get("username", "")).invoke(formatted_prompt)
        raw_content = getattr(llm_response, "content", "")
        text_content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw_content) if isinstance(raw_content, list) else str(raw_content)
        clean_json = text_content.strip().strip("```json").strip("```").strip()
        parsed_json = json.loads(clean_json)
        title = parsed_json.get("title", f"feat: merge {head_branch} into {base_branch}")
        body = parsed_json.get("body", "### Summary\n- Automated pull request draft.")
    except Exception:
        logger.exception("Failed to parse LLM PR generation, using fallback.")
        title = f"feat: merge {head_branch} into {base_branch}"
        body = f"### Summary\n- Automated pull request draft created for `{head_branch}` -> `{base_branch}`."

    details = {"title": title, "body": body, "head_branch": head_branch, "base_branch": base_branch, "repo": repo}
    summary_line = (
        f"Ready to create a Pull Request for `{repo}`:\n"
        f"- **Title:** {title}\n"
        f"- **Base Branch:** `{base_branch}` <- `{head_branch}`\n\n"
        f"**Proposed Body:**\n{body}"
    )
    return details, summary_line


def _execute_create_pr(username: str, details: dict) -> str:
    repo, title, body = details.get("repo"), details.get("title"), details.get("body")
    head_branch, base_branch = details.get("head_branch"), details.get("base_branch")

    token = os.getenv("GITHUB_TOKEN")
    api_url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"title": title, "body": body or "Automated Pull Request", "head": head_branch, "base": base_branch}

    logger.info(f"[execute_write_node:create_pr] Firing GitHub API POST to {api_url}")
    res = requests.post(api_url, headers=headers, json=payload)

    if res.status_code == 201:
        pr_data = res.json()
        pr_url, pr_num = pr_data.get("html_url"), pr_data.get("number")
        merge_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}/merge"
        merge_payload = {"commit_title": f"Merge pull request #{pr_num} from {head_branch}", "merge_method": "squash"}
        merge_res = requests.put(merge_url, headers=headers, json=merge_payload)
        if merge_res.status_code == 200:
            return f"**Pull Request Created and Merged Successfully!** \n\n[View Merged PR #{pr_num} on GitHub]({pr_url})"
        return (
            f"**Pull Request #{pr_num} Created**, but merge failed (HTTP {merge_res.status_code}):\n"
            f"```json\n{merge_res.text}\n```\n[View PR #{pr_num} on GitHub]({pr_url})"
        )
    return f"**Failed to create Pull Request** (HTTP {res.status_code}):\n```json\n{res.text}\n```"


def _is_complete_create_pr(details: dict) -> bool:
    return bool(details.get("title") and details.get("head_branch") and details.get("base_branch"))


def _recover_create_pr(messages: list, repo_hint: str) -> dict | None:
    title = body = head_branch = base_branch = None
    repo = repo_hint
    for msg in reversed(messages or []):
        content = _content_of(msg)
        repo_from_content = extract_github_repo(content, repo)
        if repo_from_content and repo_from_content != "SummonShenron/SAAPP":
            repo = repo_from_content
        if "Ready to create a Pull Request" in content:
            repo_match = re.search(r"for `([^`]+)`", content)
            if repo_match:
                repo = repo_match.group(1)
            title_match = re.search(r"-\s*\*\*Title:\*\*\s*(.+)", content, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
            branch_match = re.search(r"`([^`]+)`\s*<-\s*`([^`]+)`", content)
            if branch_match:
                base_branch, head_branch = branch_match.group(1).strip(), branch_match.group(2).strip()
            body_match = re.search(r"\*\*Proposed Body:\*\*\n([\s\S]*?)(?=\*Please|\Z)", content)
            if body_match:
                body = body_match.group(1).strip()
        elif "merge" in content.lower() and "into" in content.lower():
            prompt_match = re.search(r"merge\s+([\w\/\-\.]+)\s+into\s+([\w\/\-\.]+)", content, re.IGNORECASE)
            if prompt_match:
                head_branch, base_branch = prompt_match.group(1).strip(), prompt_match.group(2).strip()
                title = f"feat: merge {head_branch} into {base_branch}"
                body = f"### Summary\n- Merged `{head_branch}` into `{base_branch}` per user approval."
        if title and head_branch and base_branch:
            break
    if not (title and head_branch and base_branch):
        return None
    return {"title": title, "body": body, "head_branch": head_branch, "base_branch": base_branch, "repo": repo}


def _draft_create_issue(state: GraphState) -> tuple[dict, str]:
    messages = state.get("messages", [])
    last_msg = messages[-1].content.strip() if messages else ""
    pending_action = state.get("pending_action") or {}
    existing_details = pending_action.get("details", {})
    repo = (
        existing_details.get("repo")
        or state.get("repo")
        or extract_github_repo(last_msg, fallback=None)
        or resolve_recent_mention(messages, lambda c: extract_github_repo(c, fallback=None))
        or "SummonShenron/SAAPP"
    )

    try:
        formatted_prompt = ISSUE_DRAFT_PROMPT.format(user_message=last_msg)
        llm_response = get_chat_llm(state.get("username", "")).invoke(formatted_prompt)
        raw_content = getattr(llm_response, "content", "")
        text_content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw_content) if isinstance(raw_content, list) else str(raw_content)
        clean_json = text_content.strip().strip("```json").strip("```").strip()
        parsed = json.loads(clean_json)
        title = parsed.get("title", "New issue")
        body = parsed.get("body", "### Description\n- Automated issue draft.")
    except Exception:
        logger.exception("Failed to parse LLM issue generation, using fallback.")
        title = "New issue"
        body = f"### Description\n- {last_msg}"

    details = {"title": title, "body": body, "repo": repo}
    summary_line = f"Ready to open a GitHub Issue on `{repo}`:\n- **Title:** {title}\n\n**Proposed Body:**\n{body}"
    return details, summary_line


def _execute_create_issue(username: str, details: dict) -> str:
    repo, title, body = details.get("repo"), details.get("title"), details.get("body")
    token = os.getenv("GITHUB_TOKEN")
    api_url = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    payload = {"title": title, "body": body or "Automated issue"}

    logger.info(f"[execute_write_node:create_issue] Firing GitHub API POST to {api_url}")
    res = requests.post(api_url, headers=headers, json=payload)

    if res.status_code == 201:
        issue_data = res.json()
        return f"**Issue Created Successfully!**\n\n[View Issue #{issue_data.get('number')} on GitHub]({issue_data.get('html_url')})"
    return f"**Failed to create Issue** (HTTP {res.status_code}):\n```json\n{res.text}\n```"


def _is_complete_create_issue(details: dict) -> bool:
    return bool(details.get("title"))


def _recover_create_issue(messages: list, repo_hint: str) -> dict | None:
    title = body = None
    repo = repo_hint
    for msg in reversed(messages or []):
        content = _content_of(msg)
        repo_from_content = extract_github_repo(content, repo)
        if repo_from_content and repo_from_content != "SummonShenron/SAAPP":
            repo = repo_from_content
        if "Ready to open a GitHub Issue" in content:
            repo_match = re.search(r"on `([^`]+)`", content)
            if repo_match:
                repo = repo_match.group(1)
            title_match = re.search(r"-\s*\*\*Title:\*\*\s*(.+)", content, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
            body_match = re.search(r"\*\*Proposed Body:\*\*\n([\s\S]*?)(?=\*Please|\Z)", content)
            if body_match:
                body = body_match.group(1).strip()
        if title:
            break
    if not title:
        return None
    return {"title": title, "body": body, "repo": repo}


def _execute_run_mongo_write(username: str, details: dict) -> str:
    code = details.get("code", "") or ""
    exec_builtins = {
        "range": range, "len": len, "str": str, "int": int,
        "float": float, "list": list, "dict": dict, "set": set,
        "tuple": tuple, "min": min, "max": max, "sum": sum, "round": round
    }
    try:
        db = get_db()
        if hasattr(db, "list_collection_names") is False and hasattr(db, "list_database_names"):
            db = db.get_default_database() or db[list(db.list_database_names())[0]]
        local_scope = {"db": db, "username": username, "result": None}
        exec(code, {"__builtins__": exec_builtins}, local_scope)
        execution_result = local_scope.get("result", "Write operation executed successfully.")
        return f"**Write Operation Executed Successfully:**\n```json\n{json.dumps(execution_result, default=str, indent=2)}\n```"
    except Exception:
        logger.exception("Mongo write execution failed.")
        return "**Write Operation Execution Failed:**\n```error\nSee logs for traceback.\n```"


def _is_complete_run_mongo_write(details: dict) -> bool:
    return bool(details.get("code"))


def _recover_run_mongo_write(messages: list, repo_hint: str) -> dict | None:
    """Unlike a title or branch name, drafted code can't be reliably reconstructed from a
    user's *rephrasing* of what they asked for — but it doesn't need to be: the exact code is
    already sitting verbatim in the assistant's own prior approval card, in message history,
    the same way the PR/issue cards carry their own exact title/branches. Re-reading our own
    generated text is reliable in a way re-deriving from new user text never would be."""
    for msg in reversed(messages or []):
        content = _content_of(msg)
        if "Ready to run this database operation" in content:
            code_match = re.search(r"```python\n([\s\S]*?)\n```", content)
            purpose_match = re.search(r"\*\*Purpose:\*\*\s*(.+)", content)
            if code_match:
                return {
                    "code": code_match.group(1).strip(),
                    "purpose": purpose_match.group(1).strip() if purpose_match else "Database operation",
                }
    return None


WRITE_ACTIONS = {
    "create_pr": {
        "required_role": "Global_Admins",
        "draft": _draft_create_pr,
        "execute": _execute_create_pr,
        "is_complete": _is_complete_create_pr,
        "recover_from_history": _recover_create_pr,
        # card_marker is the load-bearing signal for cross-turn approval detection: pending_action
        # never survives between chat turns in this app (each turn rebuilds state fresh from
        # stored messages — see app.py's initial_state), so recognizing "the assistant's last
        # message was proposing this action" from its own card text is the only reliable way to
        # know what a bare "approve" reply two turns later is actually approving.
        "card_marker": "Ready to create a Pull Request",
    },
    "create_issue": {
        "required_role": "Global_Admins",
        "draft": _draft_create_issue,
        "execute": _execute_create_issue,
        "is_complete": _is_complete_create_issue,
        "recover_from_history": _recover_create_issue,
        "card_marker": "Ready to open a GitHub Issue",
    },
    "run_mongo_write": {
        "required_role": "Global_Admins",
        # No "draft": this action is proposed inline by tool_agent_node's own ReAct loop
        # (discovered mid-reasoning, not via an upfront intent), never via propose_write_node.
        "draft": None,
        "execute": _execute_run_mongo_write,
        "is_complete": _is_complete_run_mongo_write,
        "recover_from_history": _recover_run_mongo_write,
        "card_marker": "Ready to run this database operation",
    },
}


def propose_write_node(state: GraphState) -> GraphState:
    """Generic draft step for any intent-triggered write action (PR, issue, ...): looks up
    state["write_action"] in WRITE_ACTIONS, drafts it, and renders one standard approval
    card envelope. Mongo writes never reach this node — see WRITE_ACTIONS["run_mongo_write"]."""
    action_name = state.get("write_action")
    logger.info(f"--- PROPOSE WRITE NODE ({action_name}) CALLED ---")
    state = ensure_workflow_keys(state)
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "propose_write_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()
        messages = state.get("messages", [])
        action = WRITE_ACTIONS.get(action_name)
        if not messages or not action or not action.get("draft"):
            return state

        details, summary_line = action["draft"](state)
        new_pending_action = {"action_type": action_name, "details": details}
        card_msg = (
            "**Approval Required**\n\n"
            f"{summary_line}\n\n"
            "*Please Approve, Modify parameters, or Reject this action.*"
        )
        new_messages = list(messages) + [AIMessage(content=card_msg)]
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={"service": "SAAPP", "erragent_context": {"input": node_input, "output": node_output}}
        )
        return {
            **state,
            "pending_action": new_pending_action,
            "relevance_grade": "hitl_approval_required",
            "generation": card_msg,
            "messages": new_messages,
        }


def execute_write_node(state: dict) -> dict:
    """Generic execute step for every registered write action: one RBAC check site, one
    approval/rejection parser, one recovery-from-history fallback, then dispatch to
    whichever action['execute'] the pending_action names."""
    logger.info("--- EXECUTE WRITE NODE CALLED ---")
    state = ensure_workflow_keys(state)
    username = state.get("username")
    workflow_name = state["workflowName"]
    request_id = state["requestId"]
    node_name = "execute_write_node"
    with erragent.context(workflowName=workflow_name, requestId=request_id, node=node_name):
        node_input = state.copy()

        pending_raw = state.get("pending_action") or {}
        # pending_action is always empty here in real usage (it never survives between chat
        # turns — see the comment above classify_intent's card-marker detection), so
        # state["write_action"] — set from the assistant's own prior card text — is the
        # fallback that actually makes this resolvable in practice, not an edge case.
        action_name = pending_raw.get("action_type") or state.get("write_action")
        action = WRITE_ACTIONS.get(action_name)
        if not action:
            return {
                **state,
                "content_to_format": "No pending write action found. Please re-issue the request.",
                "relevance_grade": "conversational",
                "pending_action": None,
            }

        # 1. RBAC check — one site for every registered write action.
        user_groups = load_user_directory_groups(username)
        if action["required_role"] not in user_groups:
            return {
                **state,
                "content_to_format": f"Access denied: this action is restricted to {action['required_role']}.",
                "relevance_grade": "conversational",
                "pending_action": None,
            }

        # 2. Extract user decision
        decision = state.get("user_decision", "").lower()
        if not decision and state.get("messages"):
            last_msg = _content_of(state["messages"][-1]).strip().lower()
            if APPROVAL_PATTERN.search(last_msg):
                decision = "approve"
            elif REJECTION_PATTERN.search(last_msg):
                decision = "reject"

        if decision in ["reject", "cancel"]:
            return {
                **state,
                "content_to_format": "**Action Cancelled**: The draft was discarded.",
                "relevance_grade": "conversational",
                "pending_action": None,
            }

        # 3. Read details, recovering from history if pending_action was wiped
        details = pending_raw.get("details", pending_raw) if isinstance(pending_raw, dict) else {}
        if not action["is_complete"](details):
            logger.warning(f"[Execute Write Node] pending_action incomplete for '{action_name}'; attempting recovery...")
            recovered = None
            if action.get("recover_from_history"):
                repo_hint = details.get("repo") or state.get("repo") or "SummonShenron/SAAPP"
                recovered = action["recover_from_history"](state.get("messages", []), repo_hint)
            if recovered:
                details = recovered
            else:
                return {
                    **state,
                    "content_to_format": "Unable to execute this action: parameters were lost between turns. Please re-issue the request.",
                    "relevance_grade": "conversational",
                    "pending_action": None,
                }

        # 4. Dispatch
        output_text = action["execute"](username, details)
        node_output = state.copy()
        logger.info(
            "node executed",
            extra={"service": "SAAPP", "erragent_context": {"input": node_input, "output": node_output}}
        )
        return {
            **state,
            "content_to_format": output_text,
            "relevance_grade": "action_complete",
            "pending_action": None,
            "drafted_code": None,
            "code_approval_status": "completed",
        }


# ============================================================
# WORKFLOW ASSEMBLY & COMPILATION
# ============================================================

def create_workflow(vector_store, user_memory_vector_store=None):
    workflow = StateGraph(GraphState)
    async def retrieve_node_with_store(state):
        return await retrieve_node(state, vector_store)

    async def memory_save_node_with_store(state):
        return await memory_save_node(state, user_memory_vector_store)

    def memory_recall_node_with_store(state):
        return memory_recall_node(state, user_memory_vector_store)

    workflow.add_node("memory_save_node", memory_save_node_with_store)
    workflow.add_node("memory_recall_node", memory_recall_node_with_store)
    workflow.add_node("retrieve_node", retrieve_node_with_store)
    workflow.add_node("grade_documents_node", grading_node)
    workflow.add_node("rewrite_query_node", rewrite_query_node)
    workflow.add_node("generate_node", generate_node)
    workflow.add_node("conversational_node", conversational_node)
    workflow.add_node("coordinator_node", coordinator_node)
    workflow.add_node("summarizer_node", summarizer_node)
    workflow.add_node("formatter_node", formatter_node)
    workflow.add_node("paapp_node", paapp_node)
    workflow.add_node("snapshot_node", data_snapshot_node)
    workflow.add_node("classifier_node", activity_classifier_node)
    workflow.add_node("pattern_node", pattern_detector_node)
    workflow.add_node("trend_node", trend_analyzer_node)
    workflow.add_node("insight_query_node", insight_query_node)
    workflow.add_node("tool_agent_node", tool_agent_node)
    workflow.add_node("pr_summary", pr_summarizer_node)
    workflow.add_node("propose_write_node", propose_write_node)
    workflow.add_node("execute_write_node", execute_write_node)

    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges(
        "coordinator_node",
        coordinator_router,  
        {
            "memory_save_node": "memory_save_node",
            "memory_recall_node": "memory_recall_node",
            "retrieve_node": "retrieve_node",
            "rewrite_query_node": "rewrite_query_node",
            "conversational_node": "conversational_node",
            "generate_node": "generate_node",
            "summarizer_node": "summarizer_node",
            "formatter_node": "formatter_node",
            "paapp_node": "paapp_node",
            "insight": "snapshot_node",
            "snapshot_node": "snapshot_node",
            "classifier_node": "classifier_node",
            "pattern_node": "pattern_node",
            "trend_node": "trend_node",
            "insight_query_node": "insight_query_node",
            "tool_agent_node": "tool_agent_node",
            "pr_summary": "pr_summary",
            "propose_write_node": "propose_write_node",
            "execute_write_node": "execute_write_node"
        }
    )
    
    workflow.add_edge("paapp_node", "formatter_node")
    workflow.add_edge("memory_save_node", "formatter_node")
    workflow.add_edge("memory_recall_node", "formatter_node")
    workflow.add_edge("summarizer_node", "formatter_node")
    workflow.add_edge("formatter_node", "generate_node")
    workflow.add_edge("retrieve_node", "grade_documents_node")
    workflow.add_edge("rewrite_query_node", "retrieve_node")
    workflow.add_edge("tool_agent_node", "formatter_node")
    workflow.add_edge("pr_summary", "formatter_node")
    workflow.add_edge("propose_write_node", "formatter_node")
    workflow.add_edge("execute_write_node", "formatter_node")
    # --- PARALLEL FAN-OUT FOR ANALYTICS ---
    workflow.add_edge("snapshot_node", "classifier_node")
    # LangGraph runs pattern_node and trend_node concurrently
    workflow.add_edge("classifier_node", "pattern_node")
    workflow.add_edge("classifier_node", "trend_node")
    # Fan-in back to insight_query_node (waits for both to complete)
    workflow.add_edge("pattern_node", "insight_query_node")
    workflow.add_edge("trend_node", "insight_query_node")
    
    workflow.add_edge("insight_query_node", "formatter_node")
    workflow.add_conditional_edges(
        "grade_documents_node",
        route_after_grading,
        {
            "generate_node": "generate_node",
            "rewrite_query_node": "rewrite_query_node",
            "fallback_empty": "generate_node"
        }
    )
    workflow.add_edge("generate_node", END)
    workflow.add_edge("conversational_node", "formatter_node")
    return workflow.compile()
