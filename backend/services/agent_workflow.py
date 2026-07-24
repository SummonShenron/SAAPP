from __future__ import annotations
import base64
import os
import re
import json
from typing import List, Any, Dict
import logging
import requests
import urllib.parse
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from backend.components.time_storage import load_user_time
from backend.models.attachment import Attachment
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.documents import Document
from settings import PAAPP_BASE_URL
from backend.services.search import get_secure_retriever
from backend.models.models import llm, lite_llm
from backend.state import graph_db
from backend.utils.attachment_utils import retrieve_from_session
from backend.utils.isolation_kb_utils import load_directory, load_user_directory_groups
from backend.components.constraints import (
    format_docs,
    SUMMARIZER_PROMPT,
    GRADING_PROMPT,
    REWRITING_PROMPT,
    FORMATTER_PROMPT,
    INSIGHT_QUERY_PROMPT,
    CODE_DRAFTING_PROMPT,
    REASONER_PROMPT,
    GITHUB_SEARCH_PROMPT,
    PR_REVIEW_PROMPT,
    DRAFT_PR_PROMPT
)
from backend.components.time_storage import add_time_entry, TimeEntryCreate
from backend.components import taskboard
from backend.state.graph_state import GraphState, route_after_grading
from langgraph.graph import StateGraph, START, END
from backend.utils.db_utils import get_db
from backend.utils.normalize_utils import ensure_str

load_dotenv()
logger = logging.getLogger("SASS Logger")

# ============================================================
# COORDINATOR_NODE (sync)
# ============================================================

def coordinator_node(state: GraphState) -> GraphState:
    last_msg = state["messages"][-1].content.lower().strip()
    logger.info("--- COORDINATOR NODE START ---")
    logger.info(f"User message: {last_msg}")
    # Run reasoner first
    state = reasoner_node(state)
    intent = classify_intent(last_msg, state.get("attachment_summaries", []))
    logger.info(f"Intent classified as: {intent}")
    plan = build_agent_plan(intent, state)
    logger.info(f"Agent plan created: {plan}")
    state["coordinator_intent"] = intent
    state["coordinator_plan"] = plan["agents"]
    logger.info(f"Stored plan list: {state['coordinator_plan']}")
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
        "memory": "memory_node",
        "insight": "snapshot_node",
        "web_search": "web_search_node",
        "code_interpreter": "code_interpreter_node",
        "github_search": "github_search",
        "pr_summary": "pr_summary",
        "draft_pr": "draft_pr_node",      
        "execute_pr": "execute_pr_node"
    }
    destination = mapping.get(next_agent, "conversational_node")
    logger.debug(f"Next agent from plan: '{next_agent}'")
    logger.debug(f"Router returning destination string: '{destination}'")
    logger.info(f"sending request to {next_agent}")
    logger.info("--- COORDINATOR NODE END ---")
    return mapping.get(next_agent, "conversational_node")


def classify_intent(message: str, attachments) -> str:
    msg = message.lower()
    words = msg.split()
    APPROVAL_KEYWORDS = ["approve", "approved", "confirm", "yes", "lgtm", "do it", "reject", "cancel"]
    # 1. Prioritize specific technical tools first
    if any(w in msg for w in ["github", "repo", "repository", "commit history", "code search"]):
        return "github_search"
    if any(w in msg for w in ["run code", "execute", "query db", "database query", "mongodb", "script", "analyze data", "search mongo"]):
        return "code_interpreter"
    if any(w in msg for w in ["review pr", "pull request", "pr", "repo changes"]):
        return "pr_summary"
    if any(w in msg for w in ["create pr", "create a pr", "merge pull request", "merge pr", "merge repos", "create new pr", "create new pull request"]):
        return "create_pr"
    if any(w in words for w in APPROVAL_KEYWORDS) or any(phrase in msg for phrase in ["do it", "lgtm", "looks good"]):
        return "execute_pr"
    # 2. General operational intents
    if "plan my day" in msg or "schedule" in msg:
        return "task_paapp"
    if "summarize" in msg or "tl;dr" in msg:
        return "summarize"
    if any(w in msg for w in ["find", "lookup", "policy", "docs", "search"]):
        return "retrieve"
    if any(w in msg for w in ["calculate", "web search", "google", "api"]):
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

def build_agent_plan(intent, state):
    flags = state.get("reasoner_flags", {})
    agents = []

    # 1. Safely extract last user message from state
    messages = state.get("messages", [])
    user_message = messages[-1].content if messages else ""
    user_words = user_message.lower().split()

    APPROVAL_KEYWORDS = [
        "approve",
        "approved",
        "confirm",
        "yes",
        "lgtm",
        "do it",
        "reject",
        "cancel",
    ]
    is_approval_action = (
        any(w in user_words for w in APPROVAL_KEYWORDS)
        or intent == "execute_pr"
    )

    # 2. Priority 1: Immediate Approval / Rejection Routing
    if is_approval_action:
        logger.info(
            "[Coordinator] Approval/Rejection action detected. Routing to execute_pr."
        )
        state["last_intent"] = "execute_pr"
        return {"agents": ["execute_pr", "formatter"], "skip": []}

    # 3. Priority 2: Standard Follow-up Override
    if flags.get("follow_up_intent"):
        intent = state.get("last_intent", intent)
        logger.info(
            f"[Coordinator] Follow-up detected. Reusing last intent: {intent}"
        )
    # 4. Operational Flag & Intent Mapping
    is_pr_request = flags.get("needs_pr_summary") or intent == "pr_summary"
    if flags.get("needs_memory"):
        agents.append("memory")
    if flags.get("needs_retrieval"):
        agents.append("retriever")
    if flags.get("needs_rewrite"):
        agents.append("rewriter")
    if flags.get("needs_summary"):
        agents.append("summarizer")
    if flags.get("needs_formatting"):
        agents.append("formatter")
    if flags.get("needs_paapp"):
        agents.append("paapp")
    if flags.get("needs_conversation"):
        agents.append("conversational")
    if flags.get("needs_web_search"):
        agents.append("web_search")
    if flags.get("needs_code_interpreter"):
        agents.append("code_interpreter")
    if flags.get("needs_create_pr") or intent == "create_pr":
        agents.append("draft_pr")
    if is_pr_request:
        agents.append("pr_summary")
    elif flags.get("needs_github_search"):
        agents.append("github_search")
    # Always end with formatter (unless code interpreter handles its own formatting)
    if "formatter" not in agents and "code_interpreter" not in agents:
        agents.append("formatter")
    # Override for Code Interpreter
    if flags.get("needs_code_interpreter"):
        state["last_intent"] = "code_interpreter"
        return {"agents": ["code_interpreter"], "skip": ["retriever"]}
    # Fallback to intent only if no specific operational flags were raised
    if not agents or agents == ["formatter"]:
        if intent == "insight":
            state["last_intent"] = intent
            return {"agents": ["insight"], "skip": []}

    # Store last intent for future follow-ups
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

def reasoner_node(state: GraphState) -> GraphState:
    msg = state["messages"][-1].content.strip()
    history = state.get("messages", [])
    
    # Format message history into a clean string for the LLM
    formatted_history = "\n".join([f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', '')}" for m in history[:-1]])

    formatted_prompt = REASONER_PROMPT.format(
        history=formatted_history, 
        question=msg
    )
    
    logger.info("--- REASONER NODE START ---")

    try:
        # Call the LLM to classify intent
        response = lite_llm.invoke(formatted_prompt)
        resp_content = response.content if hasattr(response, "content") else str(response)
        
        # Safely handle list vs string response types
        if isinstance(resp_content, list):
            raw_text = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in resp_content])
        else:
            raw_text = str(resp_content)
        
        # Clean response string if wrapped in markdown codeblocks
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        flags = json.loads(clean_json)

    except Exception as e:
        logger.error(f"[Reasoner] LLM classification failed ({e}), using fallback rules.")
        # Fallback to standard false flags if JSON parsing fails
        flags = {
            "needs_retrieval": False,
            "needs_rewrite": False,
            "needs_summary": False,
            "needs_formatting": False,
            "needs_conversation": True,  # Safe default to avoid triggering unintended actions
            "needs_memory": False,
            "needs_paapp": False,
            "follow_up_intent": False,
            "needs_web_search": False,
            "needs_code_interpreter": False,
            "needs_github_search": False,
            "needs_pr_summary": False,
            "needs_create_pr": False,
        }

    logger.info(f"[Reasoner] Flags: {flags}")
    state["reasoner_flags"] = flags
    logger.info("--- REASONER NODE END ---")
    return state
# ============================================================
# MEMORY NODE
# ============================================================

def memory_node(state: GraphState) -> dict:
    logger.info("--- MEMORY NODE CALLED ---")
    user_msg = state["messages"][-1].content.strip()
    # Extract the memory content
    lower_msg = user_msg.lower()
    # Detect explicit memory commands
    triggers = [
           "remember that",
            "remember this",
            "remember me",
            "remember my",
            "can you remember",
            "save this",
            "store this",
            "keep this",
            "don't forget",
            "my preference is",
            "i prefer",
            "track this",
            "log this",
            "add to memory"
    ]
    extracted = user_msg
    for t in triggers:
        if t in lower_msg:
            extracted = user_msg.lower().split(t, 1)[-1].strip()
            break
    # If extraction fails, fallback to full message
    if not extracted:
        extracted = user_msg
    logger.info(f"Extracted memory content: {extracted}")
    # Store memory in state (later you can move this to a DB)
    memory_store = state.get("memory_store", [])
    memory_store.append(extracted)
    state["memory_store"] = memory_store
    logger.info(f"Updated memory store: {memory_store}")
    # Build confirmation message
    confirmation = f"I’ve saved that preference: {extracted}"
    # Pass to formatter
    state["raw_generation"] = confirmation
    state["content_to_format"] = confirmation
    return state

def retrieve_node(state: GraphState, vector_store) -> dict:
    logger.info("--- PARALLEL RETRIEVING DOCUMENTS & GRAPH CONTEXT ---")
    question = state["messages"][-1].content
    username = state.get("username")
    target_scope = state.get("target_scope")
    current_loops = state.get("loop_count", 0) or 0
    original_question = state.get("original_question") or question
    session_id = state.get("session_id") or f"{username}_session"

    # 1. EARLY EXIT: Priority attachments
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
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []

    # Parallel Task 2: Session Search
    def fetch_session_docs():
        try:
            session_hits = retrieve_from_session(username, session_id, question)
            if session_hits:
                return [Document(
                    page_content=f"[Session Document: {hit['filename']}]\nScore: {hit['score']}",
                    metadata={"source": "session_vector_store", "priority": True, "filename": hit["filename"]}
                ) for hit in session_hits]
        except Exception as e:
            logger.error(f"Session retrieval failed: {e}")
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
        except Exception as e:
            logger.error(f"GraphRAG Entity scanner failed: {e}")
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
    summary = llm.invoke(prompt)  # adjust to your LLM interface
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
    logger.info("--- FORMATTER NODE CALLED ---")
    # 1. Choose the correct content source
    messages = state.get("messages")
    if messages:
        user_msg = state["messages"][-1].content
    else:
        user_msg = "Generate system insights"
    content_to_format = state.get("content_to_format")
    lower_msg = user_msg.lower()
    # Fallback if memory/summarizer/generator didn't set content
    if not content_to_format:
        content_to_format = user_msg
    logger.info(f"Reformatting content: {content_to_format}")
    # 2. Implicit formatting signals
    is_long = len(content_to_format.split()) > 120
    is_multi_section = any(word in lower_msg for word in ["explain", "tell me about", "overview", "details"])
    is_list_like = any(word in lower_msg for word in ["types", "kinds", "examples", "steps"])
    is_policy_like = any(word in lower_msg for word in ["policy", "rules", "requirements"])
    is_character_lore = any(word in lower_msg for word in ["race", "lore", "history", "origin"])
    if is_policy_like or is_multi_section:
        format_style = "sections"
    elif is_list_like:
        format_style = "bullets"
    elif is_character_lore:
        format_style = "sections"
    elif is_long:
        format_style = "summary"
    else:
        format_style = "clean"
    # 3. Build the prompt
    prompt = FORMATTER_PROMPT.format(
        format_style=format_style,
        content_to_format=content_to_format
    )
    # 4. Save formatted output
    formatted = llm.invoke(prompt)
    formatted_text = formatted.content if hasattr(formatted, "content") else str(formatted)
    state["formatted_output"] = formatted_text
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

def grading_node(state: GraphState) -> dict:
    logger.info("--- GRADING RETRIEVED CONTENT ---")
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
        response = lite_llm.invoke(formatted_prompt)
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

        return {**state, "relevance_grade": grade}
    except Exception as e:
        logger.exception(f"Grading failed: {e}. Defaulting to no.")
        return {**state, "relevance_grade": "no"}


# ============================================================
# QUERY REWRITE NODE (sync)
# ============================================================

def rewrite_query_node(state: GraphState) -> dict:
    logger.info("--- REWRITING QUERY FOR BETTER RETRIEVAL ---")
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

        return {
            **state,
            "messages": new_messages,
            "question": rewrite_clean
        }
    except Exception as e:
        logger.exception(f"Query rewrite node failed: {e}")
        return state

# ============================================================
# PAAPP NODE (sync)
# ============================================================

def paapp_node(state: GraphState) -> GraphState:
    msg = state["messages"][-1].content
    username = state.get("username", "default_user")

    try:
        response = call_paapp_chat(username, msg)
    except Exception as e:
        fallback = f"PAAPP communication error: {str(e)}"
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
        except Exception as e:
            logger.error(f"[PAAPP] Sync trigger failed: {e}")

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

        except Exception as e:
            logger.error(f"[PAAPP] Time log failed: {e}")
            state["raw_generation"] = f"Time log failed: {str(e)}"
            return state

    # --- 3. RETURN RESPONSE ---
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except:
            pass

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
    text = raw.content if hasattr(raw, "content") else str(raw)

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
# WEB SEARCH NODE
# ============================================================

def web_search_node(state: GraphState) -> dict:
    logger.info("--- EXECUTING WEB SEARCH ESCALATION ---")
    
    # Safe question extraction
    question = state.get("original_question")
    if not question and state.get("messages"):
        question = state["messages"][-1].content
    
    web_docs = []
    
    try:
        search = DuckDuckGoSearchAPIWrapper()
        results = search.results(question, max_results=3)
        
        if results:
            web_docs = [
                Document(
                    page_content=f"Title: {r.get('title', 'N/A')}\nSnippet: {r.get('snippet', 'N/A')}",
                    metadata={"source": r.get("link", "web_search"), "type": "web_search"}
                )
                for r in results if isinstance(r, dict)
            ]
    except Exception as e:
        logger.error(f"Web search execution error: {str(e)}", exc_info=True)

    # Fallback document if search returned empty or threw an error
    if not web_docs:
        web_docs = [
            Document(
                page_content="No direct web search results were found for this query.",
                metadata={"source": "web_search", "type": "web_search"}
            )
        ]

    # Return ONLY modified state keys — do NOT spread **state
    return {
        "documents": web_docs,
        "relevance_grade": "web_search"
    }

# ============================================================
# CODE INTERPRETOR NODE
# ============================================================
def code_interpreter_node(state: GraphState) -> Dict[str, Any]:
    username = state.get("username")
    
    # 1. Verify Global Admin access
    user_groups = load_user_directory_groups(username)
    if "Global_Admins" not in user_groups:
        logger.warning(f"Unauthorized code interpreter attempt by non-admin user: {username}")
        return {
            "content_to_format": "Access denied: The code interpreter tool is restricted to Global Administrators.",
            "relevance_grade": "code_interpreter",
            "code_approval_status": "rejected"
        }
        
    approval_status = state.get("code_approval_status")
    existing_draft = state.get("drafted_code")

    # =========================================================================
    # BRANCH 1: Handle User Approvals for Write Operations
    # =========================================================================
    if approval_status == "approved" and existing_draft:
        logger.info(f"Executing approved write operation for user {username}...")
        try:
            db = get_db()
            if hasattr(db, "list_collection_names") is False and hasattr(db, "list_database_names"):
                db = db.get_default_database() or db[list(db.list_database_names())[0]]

            local_scope = {"db": db, "username": username, "result": None}
            
            exec(existing_draft, {"__builtins__": {
                "range": range, "len": len, "str": str, "int": int, 
                "float": float, "list": list, "dict": dict, "set": set, 
                "tuple": tuple, "min": min, "max": max, "sum": sum, "round": round
            }}, local_scope)
            
            execution_result = local_scope.get("result", "Write operation executed successfully.")
            output_msg = f"**Write Operation Executed Successfully:**\n```json\n{json.dumps(execution_result, default=str, indent=2)}\n```"
            
            return {
                **state,
                "drafted_code": None,
                "code_approval_status": "completed",
                "raw_generation": output_msg,
                "content_to_format": output_msg,
                "relevance_grade": "code_interpreter"
            }
        except Exception as e:
            logger.error(f"Write operation execution failed: {e}")
            error_msg = f"**Write Operation Execution Failed:**\n```error\n{str(e)}\n```"
            return {
                **state,
                "drafted_code": None,
                "code_approval_status": "error",
                "raw_generation": error_msg,
                "content_to_format": error_msg,
                "relevance_grade": "code_interpreter"
            }

    # =========================================================================
    # BRANCH 2 & 3: Draft, Safety Check, Execute, and Auto-Retry on Empty Results
    # =========================================================================
    msg = state.get("messages", [])[-1].content.strip()
    max_retries = 2
    execution_result = None
    drafted_code = ""
    purpose = "Database query"
    
    db = get_db()
    if hasattr(db, "list_collection_names") is False and hasattr(db, "list_database_names"):
        db = db.get_default_database() or db[list(db.list_database_names())[0]]

    for attempt in range(max_retries):
        current_msg = msg
        if attempt > 0:
            # Force a completely different query structure on retry
            current_msg = (
                f"{msg} (CRITICAL ERROR: The exact match query returned 0 results "
                f"due to potential hidden whitespace or formatting. You MUST use a MongoDB "
                f"regular expression like: result = list(db['tasks'].find({{'lane': {{'$regex': 'backlog', '$options': 'i'}}}}))"
            )
        # Format drafting prompt safely without triggering curly brace KeyErrors
        if "{msg}" in CODE_DRAFTING_PROMPT:
            prompt = CODE_DRAFTING_PROMPT.replace("{msg}", current_msg)
        else:
            prompt = f"{CODE_DRAFTING_PROMPT}\n\nUser Request: {current_msg}"

        try:
            response = lite_llm.invoke(prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)
            raw_text = "".join([block.get("text", "") if isinstance(block, dict) else str(block) for block in resp_content]) if isinstance(resp_content, list) else str(resp_content)
            
            # Clean markdown code fences if the LLM wrapped it
            clean_text = raw_text.strip()
            clean_text = re.sub(r"^```(?:json|python)?\s*", "", clean_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text)
            
            drafted_code = ""
            purpose = "Database query"

            # 1. Try direct JSON parsing
            try:
                parsed = json.loads(clean_text)
                if isinstance(parsed, dict):
                    drafted_code = parsed.get("code", "")
                    purpose = parsed.get("purpose", "Database query")
            except Exception:
                # 2. Fallback: Search for any JSON object inside the text using regex
                json_match = re.search(r"(\{.*?\})", clean_text, re.DOTALL)
                if json_match:
                    try:
                        parsed = json.loads(json_match.group(1))
                        if isinstance(parsed, dict):
                            drafted_code = parsed.get("code", "")
                            purpose = parsed.get("purpose", "Database query")
                    except Exception:
                        pass

            # 3. Final fallback if JSON parsing completely failed
            if not drafted_code:
                code_match = re.search(r"```(?:python)?\s*(.*?)\s*```", raw_text, re.DOTALL)
                drafted_code = code_match.group(1).strip() if code_match else clean_text
                
        except Exception as e:
            logger.error(f"Code drafting failed on attempt {attempt+1}: {e}")
            continue

        logger.info(f"Attempt {attempt+1} - Extracted Code to Execute: {drafted_code}")

        # Safety Check
        unsafe_keywords = ["insert", "update", "delete", "drop", "remove", "replace", "write"]
        is_safe_read = not any(kw in drafted_code.lower() for kw in unsafe_keywords)

        if is_safe_read:
            try:
                local_scope = {"db": db, "username": username, "result": None}
                exec(drafted_code, {"__builtins__": {
                    "range": range, "len": len, "str": str, "int": int, 
                    "float": float, "list": list, "dict": dict, "set": set, 
                    "tuple": tuple, "min": min, "max": max, "sum": sum, "round": round,
                    "enumerate": enumerate, "zip": zip
                }}, local_scope)
                
                execution_result = local_scope.get("result", None)
                
                # Check if results came back valid and non-empty
                if execution_result is not None and (not isinstance(execution_result, list) or len(execution_result) > 0):
                    logger.info(f"Query succeeded on attempt {attempt+1}.")
                    break
                else:
                    logger.warning(f"Attempt {attempt+1} returned empty/null results. Retrying with broader instructions...")
            except Exception as e:
                logger.error(f"Execution runtime error on attempt {attempt+1}: {e}")
                if attempt == max_retries - 1:
                    error_msg = f"Execution Error: {str(e)}"
                    return {
                        **state,
                        "drafted_code": None,
                        "code_approval_status": "error",
                        "raw_generation": error_msg,
                        "content_to_format": error_msg,
                        "relevance_grade": "code_interpreter"
                    }
        else:
            approval_message = f"**Destructive Operation Requires Approval:**\n\n**Purpose:** {purpose}\n```python\n{drafted_code}\n```"
            return {
                **state,
                "drafted_code": drafted_code,
                "code_approval_status": "pending",
                "raw_generation": approval_message,
                "content_to_format": approval_message,
                "relevance_grade": "code_interpreter"
            }

    # Format final successful results with code transparency
    output_msg = (
        f"**Query Purpose:** {purpose}\n\n"
        f"**Executed Code:**\n```python\n{drafted_code}\n```\n\n"
        f"**MongoDB Results:**\n```json\n{json.dumps(execution_result, default=str, indent=2)}\n```"
    )
    
    doc = Document(
        page_content=f"DATABASE QUERY RESULTS:\n{output_msg}",
        metadata={"source": "mongodb_code_interpreter", "priority": True}
    )
    current_docs = state.get("documents", [])
    current_docs.append(doc)
    
    return {
        "documents": current_docs,
        "drafted_code": None,
        "code_approval_status": "completed",
        "raw_generation": output_msg,
        "content_to_format": output_msg,
        "relevance_grade": "code_interpreter"
    }

# ============================================================
# REPO SEARCH NODES
# ============================================================

def github_search_node(state: dict) -> dict:
    print("--- GITHUB SEARCH NODE (DYNAMIC TREE ROUTER) CALLED ---")
    msg = state.get("messages", [])[-1].content.strip()
    
    repo = "SummonShenron/SAAPP"
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "vnd.github+json"
    }
    
    api_parts = ["https", "api.github.com"]
    api_base = f"{api_parts[0]}://{api_parts[1]}"
    
    # 1. Fetch the actual file tree from GitHub
    repo_res = requests.get(f"{api_base}/repos/{repo}", headers=headers)
    default_branch = repo_res.json().get("default_branch", "main") if repo_res.status_code == 200 else "main"
    
    tree_url = f"{api_base}/repos/{repo}/git/trees/{default_branch}?recursive=1"
    response = requests.get(tree_url, headers=headers)
    
    if response.status_code != 200:
        return {
            **state,
            "github_results": f"GitHub API tree error: {response.status_code} - {response.text}",
            "content_to_format": "Error fetching tree",
            "relevance_grade": "github_search"
        }

    tree_items = response.json().get("tree", [])
    
    # Filter for source code files
    valid_paths = [
        item.get("path") for item in tree_items 
        if item.get("type") == "blob" 
        and item.get("path", "").endswith(".py")
        and not any(exclude in item.get("path", "") for exclude in ["Example_List", "node_modules", "dist", "tests", "__pycache__"])
    ]

    # 2. Let the LLM select from the actual repository tree dynamically
    file_list_str = "\n".join(f"- {p}" for p in valid_paths)
    
    router_prompt = GITHUB_SEARCH_PROMPT

    try:
        router_response = lite_llm.invoke(router_prompt)
        raw_output = (router_response.content if hasattr(router_response, "content") else str(router_response)).replace("```", "").strip()
        selected_paths = [p.strip() for p in raw_output.split(",") if p.strip() in valid_paths]
    except Exception:
        selected_paths = []

    # Fallback to a reasonable default if LLM selection missed
    if not selected_paths and valid_paths:
        selected_paths = [valid_paths[0]]

    # 3. Fetch contents of only the dynamically chosen files
    results = []
    gh_parts = ["https", "github.com"]
    gh_base = f"{gh_parts[0]}://{gh_parts[1]}"
    
    for path in selected_paths:
        file_url = f"{api_base}/repos/{repo}/contents/{path}"
        file_res = requests.get(file_url, headers=headers)
        if file_res.status_code == 200:
            try:
                file_data = file_res.json()
                content_encoded = file_data.get("content", "")
                decoded = base64.b64decode(content_encoded).decode("utf-8")
                html_url = f"{gh_base}/{repo}/blob/{default_branch}/{path}"
                snippet = decoded[:3500] + ("\n... [Code truncated]" if len(decoded) > 3500 else "")
                results.append(f"File: {path}\nURL: {html_url}\nCode Snippet:\n```python\n{snippet}\n```")
            except Exception:
                pass

    formatted_results = "\n\n---\n\n".join(results) if results else "No matching files retrieved."

    return {
        **state,
        "github_results": formatted_results,
        "content_to_format": formatted_results,
        "relevance_grade": "github_search"
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
        logger.info(f"🔍 Fetching initial/first PR for {repo}...")
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


def pr_summarizer_node(state: GraphState) -> dict:
    logger.info("--- PR SUMMARIZER NODE CALLED ---")
    
    repo = state.get("repo", "SummonShenron/SAAPP")
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    api_base = "https://api.github.com"

    # Extract user message to resolve target PR
    user_msg = ""
    if state.get("messages"):
        user_msg = state["messages"][-1].content

    # Resolve PR number dynamically
    pr_number = state.get("pr_number") or resolve_pr_number(user_msg, repo, headers, api_base)

    if not pr_number:
        output_text = f"Could not locate the requested Pull Request for `{repo}`. Please specify a PR number (e.g., 'Review PR #2')."
        return {
            **state,
            "content_to_format": output_text,
            "pr_summary": output_text,
            "relevance_grade": "pr_summary"
        }

    logger.info(f"Resolved target Pull Request: {repo} #{pr_number}")

    # Fetch changed files
    files_url = f"{api_base}/repos/{repo}/pulls/{pr_number}/files"
    files_res = requests.get(files_url, headers=headers)
    
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
        review_response = lite_llm.invoke(review_prompt)
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

def draft_pr_node(state: GraphState) -> GraphState:
    logger.info("--- DRAFT PR NODE (HITL) CALLED ---")

    messages = state.get("messages", [])
    if not messages:
        return state

    last_msg = messages[-1].content.strip()

    # Extract existing pending action details if refining an existing draft
    pending_action = state.get("pending_action") or {}
    existing_details = pending_action.get("details", {})

    # 1. BRANCH RESOLUTION & STATE PRESERVATION
    match = re.search(
        r"merge\s+([\w\/\-\.]+)\s+into\s+([\w\/\-\.]+)",
        last_msg,
        re.IGNORECASE,
    )

    if match:
        head_branch = match.group(1)
        base_branch = match.group(2)
    elif existing_details.get("head_branch"):
        head_branch = existing_details["head_branch"]
        base_branch = existing_details["base_branch"]
    else:
        head_branch = state.get("head_branch", "feature-branch")
        base_branch = state.get("base_branch", "main")

    repo = existing_details.get("repo") or state.get(
        "repo", "SummonShenron/SAAPP"
    )

    # 2. FETCH REAL GIT DIFF CONTEXT FROM GITHUB API
    logger.info(
        f"[Draft PR Node] Fetching real branch diff for {repo}: {base_branch} <- {head_branch}"
    )
    diff_context = fetch_branch_diff_summary(repo, base_branch, head_branch)

    # 3. DYNAMIC TITLE & SUMMARY GENERATION
    logger.info(
        "[Draft PR Node] Invoking LLM for title and body generation..."
    )
    try:
        # Pass the REAL diff_context into the prompt context parameter!
        formatted_prompt = DRAFT_PR_PROMPT.format(
            user_message=last_msg,
            context=f"Repository: {repo}\nBase Branch: {base_branch}\nHead Branch: {head_branch}\n\n{diff_context}",
        )

        llm_response = llm.invoke(formatted_prompt)

        # Safely convert list or string content to text
        raw_content = getattr(llm_response, "content", "")
        if isinstance(raw_content, list):
            text_content = "".join(
                [
                    c.get("text", "") if isinstance(c, dict) else str(c)
                    for c in raw_content
                ]
            )
        else:
            text_content = str(raw_content)

        clean_json = (
            text_content.strip().strip("```json").strip("```").strip()
        )
        parsed = json.loads(clean_json)

        title = parsed.get(
            "title", f"feat: merge {head_branch} into {base_branch}"
        )
        body = parsed.get(
            "body", "### Summary\n- Automated pull request draft."
        )
        logger.info(
            "[Draft PR Node] Successfully generated dynamic PR summary!"
        )

    except Exception as e:
        logger.warning(
            f"Failed to parse LLM PR generation, using fallback: {e}"
        )
        title = f"feat: merge {head_branch} into {base_branch}"
        body = f"### Summary\n- Automated pull request draft created for `{head_branch}` -> `{base_branch}`."

    # 4. SET STRUCTURED PENDING ACTION (Matches execute_pr_node schema)
    new_pending_action = {
        "action_type": "create_pr",
        "details": {
            "title": title,
            "body": body,
            "head_branch": head_branch,
            "base_branch": base_branch,
            "repo": repo,
        },
    }

    # 5. FORMAT HITL ACTION CARD
    card_msg = (
        "**Approval Required**\n\n"
        f"Ready to create a Pull Request for `{repo}`:\n"
        f"- **Title:** {title}\n"
        f"- **Base Branch:** `{base_branch}` <- `{head_branch}`\n\n"
        f"**Proposed Body:**\n{body}\n\n"
        "*Please Approve, Modify parameters, or Reject this action.*"
    )

    # Clean message list update (Single append)
    new_messages = list(state.get("messages", [])) + [AIMessage(content=card_msg)]

    return {
        **state,
        "pending_action": new_pending_action,
        "relevance_grade": "hitl_approval_required",
        "generation": card_msg,
        "messages": new_messages,
    }

def execute_pr_node(state: dict) -> dict:
    """Executes PR creation after human approval with prompt-fallback parameter recovery."""
    logger.info("--- EXECUTE PR NODE CALLED ---")
    username = state.get("username")

    # 1. RBAC Check
    user_groups = load_user_directory_groups(username)
    if "Global_Admins" not in user_groups:
        return {
            **state,
            "content_to_format": "Access denied: Creating Pull Requests is restricted to Global Administrators.",
            "relevance_grade": "conversational",
            "pending_action": None,
        }

    # 2. Extract user decision
    decision = state.get("user_decision", "").lower()
    if not decision and state.get("messages"):
        last_msg_obj = state["messages"][-1]
        last_msg = (
            getattr(last_msg_obj, "content", "")
            if hasattr(last_msg_obj, "content")
            else str(last_msg_obj)
        ).strip().lower()

        if any(
            w in last_msg
            for w in ["approve", "approved", "confirm", "yes", "lgtm", "do it"]
        ):
            decision = "approve"
        elif any(
            w in last_msg
            for w in [
                "reject",
                "cancel",
                "nevermind",
                "abort",
                "stop",
                "no",
            ]
        ):
            decision = "reject"

    if decision in ["reject", "cancel"]:
        return {
            **state,
            "content_to_format": "**Action Cancelled**: The Pull Request draft was discarded.",
            "relevance_grade": "conversational",
            "pending_action": None,
        }

    # 3. Try reading pending_action from State
    pending_raw = state.get("pending_action") or {}
    pending = (
        pending_raw.get("details", pending_raw)
        if isinstance(pending_raw, dict)
        else {}
    )

    title = pending.get("title")
    body = pending.get("body")
    head_branch = pending.get("head_branch") or pending.get("head")
    base_branch = pending.get("base_branch") or pending.get("base")
    repo = pending.get("repo") or state.get("repo", "SummonShenron/SAAPP")

    # 4. RECOVERY LAYER: Extract from history if state was wiped
    if not (title and head_branch and base_branch):
        logger.warning(
            "[Execute PR Node] pending_action missing! Searching message history..."
        )

        for msg in reversed(state.get("messages", [])):
            content = (
                getattr(msg, "content", "")
                if hasattr(msg, "content")
                else (
                    msg.get("content", "")
                    if isinstance(msg, dict)
                    else str(msg)
                )
            )

            # Option A: Parse from Assistant Action Card (if persisted)
            if "Ready to create a Pull Request" in content:
                repo_match = re.search(r"for `([^`]+)`", content)
                if repo_match:
                    repo = repo_match.group(1)

                title_match = re.search(
                    r"-\s*\*\*Title:\*\*\s*(.+)", content, re.IGNORECASE
                )
                if title_match:
                    title = title_match.group(1).strip()

                branch_match = re.search(
                    r"`([^`]+)`\s*<-\s*`([^`]+)`", content
                )
                if branch_match:
                    base_branch = branch_match.group(1).strip()
                    head_branch = branch_match.group(2).strip()

                body_match = re.search(
                    r"\*\*Proposed Body:\*\*\n([\s\S]*?)(?=\*Please|\Z)", content
                )
                if body_match:
                    body = body_match.group(1).strip()

            # Option B: Parse directly from User's prompt ("merge X into Y")
            elif "merge" in content.lower() and "into" in content.lower():
                prompt_match = re.search(
                    r"merge\s+([\w\/\-\.]+)\s+into\s+([\w\/\-\.]+)",
                    content,
                    re.IGNORECASE,
                )
                if prompt_match:
                    head_branch = prompt_match.group(1).strip()
                    base_branch = prompt_match.group(2).strip()
                    title = f"feat: merge {head_branch} into {base_branch}"
                    body = f"### Summary\n- Merged `{head_branch}` into `{base_branch}` per user approval."
                    logger.info(
                        f"[Execute PR Node] Recovered parameters from user prompt: {head_branch} -> {base_branch}"
                    )

            if title and head_branch and base_branch:
                break

    # Final Guardrail
    if not (title and head_branch and base_branch):
        return {
            **state,
            "content_to_format": "Unable to execute PR creation: Pull request parameters were lost between turns. Please re-issue the request.",
            "relevance_grade": "conversational",
            "pending_action": None,
        }

    # 5. POST to GitHub API
    token = os.getenv("GITHUB_TOKEN")
    api_url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    payload = {
        "title": title,
        "body": body or "Automated Pull Request",
        "head": head_branch,
        "base": base_branch,
    }

    logger.info(
        f"[Execute PR Node] Firing GitHub API POST to {api_url} with payload: {payload}"
    )
    res = requests.post(api_url, headers=headers, json=payload)

    if res.status_code == 201:
        pr_data = res.json()
        pr_url = pr_data.get("html_url")
        pr_num = pr_data.get("number")
        output_text = f"**Pull Request Created Successfully!**\n\n[View PR #{pr_num} on GitHub]({pr_url})"
    else:
        output_text = f"**Failed to create Pull Request** (HTTP {res.status_code}):\n```json\n{res.text}\n```"

    return {
        **state,
        "content_to_format": output_text,
        "relevance_grade": "conversational",
        "pending_action": None,
    }

# ============================================================
# WORKFLOW ASSEMBLY & COMPILATION
# ============================================================

def create_workflow(vector_store):
    workflow = StateGraph(GraphState)
    def retrieve_node_with_store(state):
        return retrieve_node(state, vector_store)
        
    workflow.add_node("memory_node", memory_node)
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
    workflow.add_node("web_search_node", web_search_node)
    workflow.add_node("code_interpreter_node", code_interpreter_node)
    workflow.add_node("github_search", github_search_node)
    workflow.add_node("pr_summary", pr_summarizer_node)
    workflow.add_node("draft_pr_node", draft_pr_node)
    workflow.add_node("execute_pr_node", execute_pr_node)

    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges(
        "coordinator_node",
        coordinator_router,  
        {
            "memory_node": "memory_node",
            "retrieve_node": "retrieve_node",
            "rewrite_query_node": "rewrite_query_node",
            "conversational_node": "conversational_node",
            "generate_node": "generate_node",
            "summarizer_node": "summarizer_node",
            "formatter_node": "formatter_node",
            "paapp_node": "paapp_node",
            "insight": "snapshot_node",
            "web_search_node": "web_search_node",
            "snapshot_node": "snapshot_node",
            "classifier_node": "classifier_node",
            "pattern_node": "pattern_node",
            "trend_node": "trend_node",
            "insight_query_node": "insight_query_node",
            "code_interpreter_node": "code_interpreter_node",
            "github_search": "github_search",
            "pr_summary": "pr_summary",
            "draft_pr_node": "draft_pr_node",
            "execute_pr_node": "execute_pr_node"
        }
    )
    
    workflow.add_edge("paapp_node", "formatter_node")
    workflow.add_edge("memory_node", "formatter_node")
    workflow.add_edge("summarizer_node", "formatter_node")
    workflow.add_edge("formatter_node", "generate_node")
    workflow.add_edge("retrieve_node", "grade_documents_node")
    workflow.add_edge("rewrite_query_node", "retrieve_node")
    workflow.add_edge("web_search_node", "formatter_node")
    workflow.add_edge("code_interpreter_node", "formatter_node")
    workflow.add_edge("github_search", "formatter_node")
    workflow.add_edge("pr_summary", "formatter_node")
    workflow.add_edge("draft_pr_node", "formatter_node")
    workflow.add_edge("execute_pr_node", "formatter_node")
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