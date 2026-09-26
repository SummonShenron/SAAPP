from __future__ import annotations
import ast
import asyncio
import base64
import difflib
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
from backend.services.browser_tool import (
    BrowserSession, browser_navigate, browser_read_text, browser_click, browser_type, browser_screenshot,
)
from backend.services.ci_test_runner import run_repo_tests, run_python_snippet, DEFAULT_MAX_WAIT_SECONDS as CI_TEST_RUN_MAX_WAIT_SECONDS
from backend.utils.normalize_utils import ensure_str
from backend.utils.agent_utils import parse_definition_index_from_observation, find_mismatched_start_line_note

load_dotenv()
logger = logging.getLogger("SASS Logger")
TOOL_AGENT_MAX_ITERATIONS = int(os.getenv("TOOL_AGENT_MAX_ITERATIONS", "7"))
# Deep thinking (a per-user opt-in setting, see user_settings_utils) raises both the step
# ceiling and how many times the retry-nudge is allowed to reject a premature "final" — a
# higher step cap alone wouldn't help if the loop still only gets one nudge to actually use
# the extra room to double-check itself.
TOOL_AGENT_MAX_ITERATIONS_DEEP = int(os.getenv("TOOL_AGENT_MAX_ITERATIONS_DEEP", "14"))
TOOL_AGENT_MAX_RETRY_NUDGES = int(os.getenv("TOOL_AGENT_MAX_RETRY_NUDGES", "1"))
TOOL_AGENT_MAX_RETRY_NUDGES_DEEP = int(os.getenv("TOOL_AGENT_MAX_RETRY_NUDGES_DEEP", "3"))
# TOOL_AGENT_PROMPT's {question} used to be filled from ONLY state["messages"][-1] — a real
# production trace showed this loses a multi-turn task entirely: the instruction ("click the
# widget icon and verify it works") was given a few turns before the model actually acted on it,
# and by the time it did, that turn's own prompt contained nothing but the latest one-line reply
# ("you can indeed navigate to X"), with no way to recover what it was supposed to check once it
# got there. reasoner_node already builds a similar formatted history for its own prompt with no
# cap; this repeats that pattern but DOES cap it — tool_agent_node's prompt already carries a
# large actions_menu and a growing attempts list, and unlike reasoner_node (built once per turn)
# this prompt gets rebuilt on every single ReAct step, so uncapped history would multiply cost
# across every step of every turn, not just pay for it once.
TOOL_AGENT_HISTORY_MAX_MESSAGES = int(os.getenv("TOOL_AGENT_HISTORY_MAX_MESSAGES", "10"))

# search_code only matches exact literal tokens against GitHub's keyword index — a colloquial
# or descriptive name shares no tokens at all with a differently-named real file, so rewording
# the query never helps. A real production trace showed advisory prompt text alone wasn't
# enough once the model was several steps into repeating this exact mistake (see
# docs/coding-agent-roadmap.md, "Failure A"); this mechanically escalates after 2 consecutive
# misses on search_code specifically, regardless of how differently each one was worded.
TOOL_AGENT_STUCK_ACTION_REDIRECTS = {
    "search_code": (
        2,
        "You have called search_code multiple times in a row with no results. search_code "
        "only matches exact literal tokens — if the term you're looking for doesn't appear "
        "verbatim anywhere in the codebase (a colloquial or descriptive name instead of the "
        "real identifier), rewording the query will not help. Call find_file instead, with "
        "the same concept — do not call search_code again this turn.",
    ),
}

# A second real production trace showed the exact same failure shape recur on a DIFFERENT,
# unlisted action: list_repo_tree (a no-argument action) 404'd because of a bad repo resolution,
# and with no entry here for it, the model just kept calling it again with a differently-worded
# "purpose" each time — technically satisfying "try something different" without being able to
# change anything that actually mattered, since a no-arg action has nothing to vary. Two
# independent real instances of "an unlisted tool gets no mechanical backstop at all" is enough
# to generalize rather than hand-author one more entry and wait for the third: any tool_action not
# explicitly listed above still gets this generic circuit breaker once its own consecutive-miss
# streak crosses this threshold — a real answer or a genuinely different tool switches it off, same
# as the specific entries.
_DEFAULT_STUCK_ACTION_THRESHOLD = 3
_DEFAULT_STUCK_ACTION_MESSAGE = (
    "You have called {tool} multiple times in a row with no useful result. Rewording the stated "
    "purpose each time is not a real retry if the action itself takes no arguments (or the same "
    "ones) to vary — it is the same call bouncing off the same wall. Switch to a genuinely "
    "different action this turn instead of calling {tool} again. If you have real reason to "
    "believe the underlying repo/resource itself is inaccessible (not just this one path/query), "
    "say so honestly in your final answer instead of continuing to retry."
)

# Built after the extensive diagnostic loop in docs/coding-agent-roadmap.md (Sections 4b-4j) —
# every one of those traces burned most of a turn's step budget reading files/searching one at a
# time before ever getting to reason about them. Deliberately restricted to genuinely
# side-effect-free, independent lookups: run_mongo_query/run_repo_tests/run_snippet are writes or
# real slow CI dispatches that must never run concurrently with anything else (see _is_unsafe and
# the admin gates in _act), and every browser_* action is inherently stateful/sequential — a click
# depends on whatever page a prior navigate actually loaded, so "independent" never applies to them.
TOOL_AGENT_BATCHABLE_ACTIONS = frozenset({
    "list_repo_tree", "read_repo_file", "search_code", "find_file", "trace_symbol",
    "search_literal", "diff_branches", "list_commits", "web_search", "run_python",
})
# Caps a single step's real concurrent work — unbounded batching could still hit GitHub's rate
# limits or just be wasteful even though it no longer costs step budget; excess items are dropped
# with their own explicit "retry in a later step" observation rather than silently ignored.
_MAX_BATCH_SIZE = 5

# A real production trace showed a "final" confidently and repeatedly denying browser access
# ("I don't actually have a live browser tool...") while browser_navigate sat in that exact
# turn's own action menu the whole time — a prose rule telling it to check the menu before
# denying a capability already exists and still wasn't enough (see docs/coding-agent-roadmap.md).
# Each entry: (regex matching how a user/model might refer to this capability in plain language,
# the exact menu-line substring proving that tool_action is actually available this turn).
TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST = [
    (
        re.compile(r'\b(browser|navigate (?:to|the)|headless browser|live (?:web|page|site)|screenshot)\b', re.IGNORECASE),
        "- browser_navigate —",
    ),
    (
        re.compile(r'\brun (?:code|python|a (?:script|snippet))\b|\bexecute (?:code|python|javascript)\b', re.IGNORECASE),
        "- run_snippet —",
    ),
    (
        re.compile(r'\b(mongo(?:db)?|the database)\b', re.IGNORECASE),
        "- run_mongo_query —",
    ),
]

# read_repo_file's truncation/paging knobs. A flat character-count cap on a large file (this repo
# has several 2000+ line files) silently cuts off before ever reaching a function defined further
# down — a real fabrication risk, since a model can mistake "I was given the start of the file"
# for "I read the file" and fill the gap it never saw with something plausible instead of real
# code. start_line lets a caller jump straight to a specific line once it knows where to look
# (from _build_definition_index below, or from search_code), the same way a normal editor would.
_READ_FILE_CHAR_CAP = 3500
_READ_FILE_DEFAULT_LINE_WINDOW = 150
_TOP_LEVEL_DEF_RE = re.compile(r'^(?:async\s+)?(def|class)\s+(\w+)')

# search_code rides GitHub's hosted /search/code index — capped at 20 results per query, subject
# to indexing lag, and explicitly not guaranteed complete by GitHub's own docs. That's fine for
# "find a plausible match" but a real production trace showed it fail exactly the task this exists
# for: "find every file that reads GITHUB_TOKEN" needs a guaranteed-complete answer, not a
# best-effort one, since a plan built on a silently-partial result set is worse than one that
# admits it doesn't know. search_literal instead fetches the real file tree and greps actual
# fetched blob content — slower (one API call per candidate file) but exhaustive by construction.
_SEARCH_LITERAL_MAX_FILE_BYTES = 200_000
_SEARCH_LITERAL_MAX_FILES_SCANNED = 300
_SEARCH_LITERAL_MAX_MATCHES = 50
_SEARCH_LITERAL_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".gz", ".tar",
    ".lock", ".map",
)


def _build_definition_index(lines: list) -> str:
    """A lightweight table of contents for a large file: every top-level (module-level, not
    nested inside a class/function) def/class and the line it starts on. Lets the model jump
    straight to the real function it needs via start_line instead of guessing or reading from
    the top of a multi-thousand-line file and hoping the truncated slice happens to reach it."""
    entries = [
        f"  line {i}: {m.group(1)} {m.group(2)}"
        for i, line in enumerate(lines, start=1)
        if (m := _TOP_LEVEL_DEF_RE.match(line))
    ]
    return "\n".join(entries[:150])


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


# Every GraphState field that must NOT persist across turns, with its fresh-per-turn default —
# matching each field's OWN existing `.get(key, default)` fallback used elsewhere, not just
# `None` uniformly. That distinction is load-bearing: setting a key to None makes it PRESENT in
# state, so a downstream `state.get("user_decision", "").lower()` no longer falls back to ""
# (the key exists now) and would crash on None instead — caught by the test suite the first
# time this ran for real. snapshot/classified/analysis_output/patterns/trends/insights are
# deliberately NOT listed: they belong exclusively to the separate insights_workflow.py graph
# (a different compiled graph, no checkpointer, invoked independently via /api/insights) —
# nothing in THIS graph's nodes ever writes them, so there's nothing here to resurrect.
#
# Only meaningful now that create_workflow() can be compiled with a real checkpointer: once a
# checkpointer + thread_id is used, LangGraph silently resurrects any channel absent from a
# turn's input state from whatever was checkpointed on a PRIOR turn, however many turns back
# that was (verified against LangGraph's own Pregel loop). app.py's initial_state never sets
# most of these, so without this reset they'd all leak across turns the moment a checkpointer
# was attached. paused_clarification is the one deliberate exception — it's the only field
# meant to survive, so it's the only transient GraphState field NOT listed here.
_TRANSIENT_STATE_DEFAULTS: Dict[str, Any] = {
    "coordinator_intent": "",
    "coordinator_plan": [],
    "content_to_format": None,
    "raw_generation": None,
    "code_approval_status": None,
    "drafted_code": None,
    "github_query": "",
    "github_results": "",
    "pr_number": None,
    "pr_review_status": None,
    "comment_url": None,
    "voice_payload": None,
    "user_groups": [],
    "pending_action": None,
    "write_action": None,
    "user_decision": "",
    "modified_details": None,
    "last_intent": None,
    "memory_facts": None,
    "memory_hits": None,
    "insight_answer": None,
}


def reset_transient_state(state: GraphState) -> GraphState:
    """Called once, as the very first thing coordinator_node does (the graph's sole entry
    point — see create_workflow's `add_edge(START, "coordinator_node")`), so this always runs
    exactly once per turn regardless of which caller built the initial state dict. Overwrites
    every transient field unconditionally — a field genuinely set earlier THIS SAME turn can't
    exist yet, since this runs before anything else does."""
    state.update(_TRANSIENT_STATE_DEFAULTS)
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
        logger.info("--- COORDINATOR NODE START ---")
        # Logged BEFORE reset_transient_state so this reflects what a checkpointer actually
        # resurrected from a prior turn (if anything) — useful for verifying the checkpointer is
        # really wired up, and the one field it doesn't apply to.
        logger.debug(f"Incoming state pending_action: {state.get('pending_action')}")
        logger.debug(f"Incoming state paused_clarification: {state.get('paused_clarification')}")
        state = reset_transient_state(state)

        node_input = state.copy()
        last_msg = state["messages"][-1].content.lower().strip()

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

# Single source of truth for every agent-name -> node-name mapping in the plan mechanism —
# shared by coordinator_router (the first dispatch) and plan_continue_router (every subsequent
# one), so there's no risk of the two drifting apart on what "tool_agent" or "memory_save" means.
_AGENT_NODE_MAP = {
    "retriever": "retrieve_node",
    "conversational": "conversational_node",
    "formatter": "formatter_node",
    "summarizer": "summarizer_node",
    "paapp": "paapp_node",
    "memory_save": "memory_save_node",
    "memory_recall": "memory_recall_node",
    "tool_agent": "tool_agent_node",
    "pr_summary": "pr_summary",
    "propose_write": "propose_write_node",
    "execute_write": "execute_write_node",
}

# An identity map over every node the plan mechanism can ever dispatch to — used as the
# `mapping` argument for every add_conditional_edges(..., plan_continue_router, ...) call, since
# _dispatch_next_in_plan already resolves an agent name to its final node name before returning,
# so LangGraph's conditional-edge mapping just needs to route each possible return value to
# itself. Covers both on_empty defaults too, since "conversational_node" and "formatter_node"
# are themselves already values in _AGENT_NODE_MAP.
_PLAN_DESTINATIONS = {node_name: node_name for node_name in _AGENT_NODE_MAP.values()}


def _dispatch_next_in_plan(state: GraphState, on_empty: str) -> str:
    """Pops and dispatches the next queued agent from state["coordinator_plan"] (built once by
    build_agent_plan inside coordinator_node). Used by both coordinator_router (the first
    dispatch) and plan_continue_router (every subsequent one) — they differ only in what "the
    plan is empty" should mean at that point in the graph.

    Mutates coordinator_plan in place and reassigns it onto state; coordinator_plan is a plain
    list with no reducer, so this relies on LangGraph aliasing (not copying) channel values
    across sequential nodes within one run. A checkpointer (added since this comment was first
    written — see create_workflow's checkpointer param) persists state at each step boundary but
    doesn't change same-run aliasing between sequential nodes, so this still holds; it would only
    break if this field were ever read inside a parallel/fan-out branch, which it currently never
    is.
    """
    plan = state.get("coordinator_plan", [])
    if not plan:
        logger.info("[Plan] Queue empty — falling through to '%s'.", on_empty)
        return on_empty

    next_agent = plan.pop(0)
    state["coordinator_plan"] = plan
    state["last_intent"] = state.get("coordinator_intent")
    destination = _AGENT_NODE_MAP.get(next_agent, on_empty)
    logger.info(f"[Plan] Dispatching next queued agent: '{next_agent}' -> '{destination}'")
    return destination


def coordinator_router(state: GraphState) -> str:
    """First dispatch, right after coordinator_node builds the plan. An empty plan here means
    nothing was ever queued — a bare conversational turn.

    Deliberately does not log its own "node end"/"preparing" lines — coordinator_node already
    logs "--- COORDINATOR NODE END ---" right before this router runs, and _dispatch_next_in_plan
    already logs exactly where the plan is heading; a second END line here previously made it
    look like the coordinator ran twice per turn."""
    return _dispatch_next_in_plan(state, on_empty="conversational_node")


def plan_continue_router(state: GraphState) -> str:
    """Re-entry point after each plan-driven node finishes. An empty plan here means the whole
    plan is done — proceed to formatting/generation, not back to a bare conversational turn.

    This is what makes a multi-agent plan (e.g. ["memory_save", "retriever"]) actually run every
    step instead of silently dropping everything after the first: previously nothing in the
    graph ever routed back to coordinator_node to pop the rest of the queue, so every plan-driven
    node's static edge straight to formatter_node just discarded whatever else was queued.
    """
    return _dispatch_next_in_plan(state, on_empty="formatter_node")


def route_after_grading_with_plan_continuation(state: GraphState) -> str:
    """Wraps route_after_grading (left completely untouched — still independently unit-tested)
    so retrieval doesn't swallow a queued step after it. A plan like
    ["memory_save", "retriever", "summarizer"] is real whenever multiple reasoner flags fire in
    the same turn; without this, grading's exit always went straight to formatter_node,
    reproducing the exact "silently drops anything after the first agent" bug one hop later.
    "rewrite_query_node" is still an internal retry within the retrieval sub-loop, not "done
    with this plan step", so it's passed through unchanged."""
    outcome = route_after_grading(state)
    if outcome == "rewrite_query_node":
        return "rewrite_query_node"
    return _dispatch_next_in_plan(state, on_empty="formatter_node")

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
    # Was previously preceded by a full `list(state.keys())` dump — a fixed ~40-field list that
    # never varies call to call and never told anyone anything about this specific turn, and
    # this function is called twice per turn whenever build_agent_plan reclassifies a follow-up
    # (see its "follow_up_intent" branch), doubling the noise for zero new information.
    logger.debug(f"pending_action: {state.get('pending_action')}")
    logger.debug(f"last_intent: {state.get('last_intent')}")

    messages = state.get("messages", []) or []

    # Was previously followed by a loop dumping every human message in the entire conversation
    # history at DEBUG, one line each, on every single turn — unbounded per-turn log volume that
    # grows for the rest of the session, and the list itself was never read for anything besides
    # that logging. Nothing downstream in this function needs individual message content.
    logger.debug(f"User messages count: {sum(1 for m in messages if getattr(m, 'type', None) == 'human')}")

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

    # tool_agent_node's own clarification pause: a short reply like "the SAAPP one" won't
    # reliably trip the reasoner's needs_github_search/needs_code_interpreter flags on its own,
    # so without this explicit check it would fall through to a fresh, unrelated classification
    # instead of resuming the paused search. Unlike the write-action check above (still
    # text-based — see its comment), this reads real checkpointed state directly:
    # paused_clarification is the one GraphState field reset_transient_state deliberately
    # leaves alone, so it survives from the turn that raised it into this one.
    if state.get("paused_clarification"):
        logger.debug("Detected paused_clarification in state — resuming tool_agent")
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
    # state["paused_clarification"] check) — same reasoning as web_search just above: a short
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


_EMPTY_OBSERVATION_VALUES = {
    "", "[]", "{}", "none", "null", "no results", "no results found",
    "no matches.", "no matches", "no diff context available.", "no similar file paths found.",
}


def _is_empty_observation(observation: str) -> bool:
    """An empty result (no matches, an empty list, an empty file listing) is not the same as
    "nothing exists" — it's very often a sign the query, path, repo, or collection was wrong,
    not proof of absence (this is exactly what happened with the repo-misresolution bug
    earlier: a wrong repo name didn't error, it just came back empty). Treated the same way
    an outright "ERROR: ..." is by the retry-nudge tracking in run_react_loop, since both are
    "this step didn't actually get you anywhere" — the model just can't tell that from the
    text alone without this check."""
    return observation.strip().lower() in _EMPTY_OBSERVATION_VALUES


def _mentions_unresolved_truncation(observation: str) -> bool:
    """A real production trace showed the model treat a truncated read_repo_file result as if
    it were the whole file: it read agent_workflow.py once (truncated at _READ_FILE_CHAR_CAP,
    nowhere near run_react_loop's actual body), never re-called with start_line despite the
    truncation note explicitly saying to, and confidently proposed a fully fabricated
    reimplementation instead — with 3 full steps of budget still unused, so this wasn't even
    budget pressure. The truncation note already tells it not to guess; this makes that
    mechanical instead of relying on it to comply. Reuses the exact same marker text _read_file
    emits (both no-start_line truncation variants share "truncated — this file has"), so a
    follow-up read_repo_file call with a genuinely different start_line — a different
    args_signature — clears it via the same unretried_inconclusive_tools machinery an ERROR or
    empty result already does, no new tracking dict needed.

    A second real trace immediately exposed a gap in this same fix: forced to retry, the model
    correctly called read_repo_file WITH a start_line — but that's a THIRD, different message
    shape ("N more lines below — re-call with a higher start_line"), not the first two, so it
    wasn't covered and the model declared "final" from lines 400-549 of a 4552-line file with
    the real code (line ~2350) still unread. Same fix, same reasoning, just the missing variant."""
    return "truncated — this file has" in observation or "more lines below" in observation


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


# Catches the exact shape of a real production failure: a confident, detailed "final" answer
# denying a capability that was sitting in that same turn's own action menu the whole time
# (browser_navigate — see docs/coding-agent-roadmap.md, "tool_agent_node had zero conversation
# history"). A prose rule already tells the model to check its own menu before denying a
# capability; this is the mechanical backstop for when it doesn't, checking the actual "final"
# TEXT against the actual menu rather than trusting the model caught its own contradiction. Only
# the denial-shaped phrase is generic/repo-agnostic here — WHICH capabilities to watch for is
# domain knowledge the caller (tool_agent_node) supplies via capability_denial_watchlist, the
# same config-driven shape as stuck_action_redirects.
_CAPABILITY_DENIAL_RE = re.compile(
    r"\b(i (?:don'?t|do not) (?:actually |currently )?have\b|i can'?t\b|i'?m not able to\b|"
    r"i lack\b|no access to\b|i (?:don'?t|do not) have access\b)",
    re.IGNORECASE,
)


# A different failure shape from the same production trace as the capability-denial backstop
# above (docs/coding-agent-roadmap.md, Section 7): three self-drive attempts in a row each
# fabricated a different kind of ground truth (invented code, a false "this doesn't exist" claim,
# and — the one this catches — a confidently-presented diff editing a data structure that was
# never actually verified to exist). Before a "final" containing a diff against an EXISTING file
# is accepted, at least one of that diff's own claimed pre-existing lines (context or removed,
# never a `+` line) must actually appear in a real read_repo_file observation for that same path
# recorded THIS turn — otherwise the diff was composed from a plausible guess, not derived from
# what was actually fetched. String/regex based, not a real diff parser — same accepted
# soft-failure-mode tradeoff already used by trace_symbol/find_file in this file.
_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.MULTILINE)
_DIFF_NEW_FILE_RE = re.compile(r"^new file mode")


def _extract_diff_file_grounding_lines(final_answer: str) -> dict:
    """Maps each existing-file path named in a `diff --git` block inside final_answer to the
    non-added lines (context or removed) inside its hunks — the lines the diff claims already
    existed in that file before this change. A brand-new file (a `new file mode` line before the
    next file header) is skipped entirely, since there's nothing pre-existing to verify."""
    files: dict = {}
    current_path = None
    skip_current = False
    for line in final_answer.splitlines():
        header_match = _DIFF_FILE_HEADER_RE.match(line)
        if header_match:
            current_path = header_match.group(1)
            skip_current = False
            files.setdefault(current_path, [])
            continue
        if current_path is None:
            continue
        if _DIFF_NEW_FILE_RE.match(line):
            skip_current = True
            files.pop(current_path, None)
            continue
        if skip_current or line.startswith(("+++", "---", "index ", "@@")) or line.startswith("+"):
            continue
        if line.startswith("-"):
            files[current_path].append(line[1:].strip())
        elif line.startswith(" "):
            files[current_path].append(line[1:].strip())
    return {path: [l for l in lines if l] for path, lines in files.items()}


def _final_diff_disagrees_with_fetched_content(final_answer: str, attempts: list) -> str | None:
    """Returns the file path of the first diff hunk whose claimed pre-existing lines never
    actually appeared in a real read_repo_file result for that path this turn — a strong signal
    the diff was composed from a guess instead of derived from real fetched content. None if every
    diffed file either has real corroborating evidence or the answer contains no diff at all."""
    for path, grounding_lines in _extract_diff_file_grounding_lines(final_answer).items():
        if not grounding_lines:
            continue
        fetched_text = "\n".join(
            a["observation"] for a in attempts
            if a.get("action_desc", "").startswith("read_repo_file(") and f"path={path}" in a["action_desc"]
        )
        if not fetched_text or not any(line in fetched_text for line in grounding_lines):
            return path
    return None


def _format_react_attempts(attempts: list) -> str:
    if not attempts:
        return "(none yet — this is the first step)"
    return "\n\n".join(
        f"Attempt {i} — Purpose: {a['purpose']}\nAction: {a['action_desc']}\nObservation: {a['observation']}"
        for i, a in enumerate(attempts, 1)
    )


def _format_attempts_steps(attempts: list) -> str:
    """Renders attempts as 'Step N — purpose / Result' blocks for display — used both in a
    completed answer's footer and in a clarification pause message. Purely a display renderer:
    resuming a paused clarification reads attempts back from real checkpointed state
    (state["paused_clarification"]), not by re-parsing this rendered text."""
    return "\n\n".join(
        f"**Step {i} — {a['purpose']}:**\n```\n{a['action_desc']}\n```\n"
        f"**Result:**\n```\n{_format_observation_for_footer(a['observation'])}\n```"
        for i, a in enumerate(attempts, 1)
    )


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
    stuck_action_redirects: dict | None = None,
    capability_denial_watchlist: list | None = None,
    architecture_map: str = "",
    batchable_actions: frozenset | None = None,
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

    "Outstanding failed-or-empty action" is tracked per (tool_action name, args) pair, not just
    "did the last step fail" — a real failure mode this caught: read_repo_file 404s, list_repo_tree
    (a different action, taken to diagnose the 404) then succeeds, and the model concludes right
    there without ever actually retrying the read. Checking only the last observation would
    see the successful list and never nudge, even though the thing the user actually asked for
    was never retrieved. Empty results are tracked the same way as outright errors (see
    _is_empty_observation) — an empty search result is very often a sign of looking in the
    wrong place (wrong repo, wrong collection, wrong query), not proof nothing exists.
    unretried_inconclusive_tools maps every tool_action that has failed or come back empty to the
    exact args it was called with, until it's attempted again with either a real success or
    genuinely different args — calling the same failed action with the identical args again is
    not a retry (it's the same query bouncing off the same wall) and does not clear the flag, so
    it keeps nudging until the model actually changes something. Tracked regardless of what ran
    in between.

    A real production trace showed the above still isn't enough for one specific pattern: the
    model called search_code with several genuinely DIFFERENT (differently-worded) queries in a
    row, ~15 steps total, and never once switched to find_file — each retry was "genuine" by the
    args-signature check above (different wording each time), so it kept clearing the nudge, even
    though the actual problem (an exact-token search tool being used for a colloquial name) never
    changes no matter how the query is reworded. A prose rule in the prompt telling it to use
    find_file after an empty search_code already existed and was already live in production when
    this happened — advisory text alone wasn't enough once it was several steps into a losing
    strategy. `stuck_action_redirects` (optional: {tool_action_name: (consecutive_miss_threshold,
    redirect_message)}) adds a mechanical escalation on top: it tracks a CONSECUTIVE-misses streak
    per tool_action_name regardless of args (reset by any success or any different tool_action),
    and once a listed tool's streak reaches its threshold, injects `redirect_message` into the
    prompt AND actively rejects one further call to that same stuck tool_action (not executed, not
    recorded as an attempt) — forcing a real switch to a different action, the same mechanical
    escalation already used for a premature "final".

    A second real production trace showed the same "prose alone isn't enough" lesson applies even
    to a rule this loop's own prompt already states plainly: told to check its own action menu
    before denying a capability, the model still produced a confident, detailed "final" claiming
    it had no browser tool at all, while browser_navigate sat in that exact turn's menu.
    `capability_denial_watchlist` (optional: a list of (capability_keyword_regex,
    tool_action_menu_substring) pairs) is the mechanical backstop: when a "final" answer matches
    both a generic denial-shaped phrase (_CAPABILITY_DENIAL_RE — "I don't have...", "I can't...",
    etc.) AND one of the watchlist's capability keywords, AND that pair's tool_action_menu_substring
    is actually present in this turn's prompt_template (proving the capability really is
    available), the "final" is rejected once (not executed as a real step, no state to retry — just
    a corrective notice injected into the next step) instead of trusting the model to have caught
    its own contradiction.

    A self-drive experiment (docs/coding-agent-roadmap.md, Section 7) surfaced a third fabrication
    shape unrelated to any tool_action, budget, or capability check above: a confidently-formatted
    diff editing a data structure (a whole dict) that was never verified to exist anywhere in the
    real codebase — presented alongside quotes from OTHER, genuinely-read parts of the same file,
    so it read as thoroughly researched. `_final_diff_disagrees_with_fetched_content` is the
    mechanical backstop: when a "final" answer contains a `diff --git` block against an EXISTING
    file (a new file has nothing pre-existing to verify and is skipped), at least one of that
    hunk's own claimed pre-existing lines (context or removed, never a `+` line) must actually
    appear in a real read_repo_file observation for that same path recorded THIS turn — otherwise
    the "final" is rejected once, the same budget-limited shape as the capability-denial check
    above, so a real diff grounded in an earlier part of the conversation (outside this loop's own
    attempts) still gets through eventually rather than looping forever on a false positive.

    An extensive diagnostic loop (docs/coding-agent-roadmap.md, Sections 4b-4j) showed every one
    of those real traces burn most of a turn's step budget reading files or searching one at a
    time, well before ever reasoning about them — a genuinely multi-file investigation needs N
    reads that don't depend on each other, but the loop only ever let it spend one action per
    step. `batchable_actions` (optional: a frozenset of tool_action names safe to run
    concurrently) lets a single "query" step submit `{"queries": [{"tool_action", "args",
    "purpose"}, ...]}` instead of one `tool_action`/`args` pair — each item runs concurrently via
    asyncio.gather, and every one of them still goes through the exact same real execution
    (is_unsafe, trace emission, error handling) and mechanical bookkeeping (retry-nudge tracking,
    truncation tracking, the stuck-action streak) that a real sequential step would have gotten,
    applied once per item in the order given — a batch never gets WEAKER scrutiny than the
    equivalent sequential steps would have, just fewer round trips to get there. A stuck tool
    slipped into a batch rejects the entire batch (none of it runs) rather than silently dropping
    just that one item, and any tool_action not in `batchable_actions` is rejected individually
    with a synthetic error instead of executed — deliberately excludes writes
    (`run_mongo_query`), real slow CI dispatches (`run_repo_tests`/`run_snippet`), and every
    `browser_*` action (inherently stateful/sequential), regardless of what the caller passes.

    A real trace showed batching alone doesn't stop a different kind of waste: the model re-ran
    the exact same tool_action + args it had already gotten a real, successful result for several
    steps earlier — reading the same file/interface twice for zero new information, on a turn that
    then ran out of its entire step budget without ever producing an answer
    (docs/coding-agent-roadmap.md, Section 9). `succeeded_action_signatures` tracks every
    (tool_action_name, args_signature) pair that has genuinely succeeded (not an ERROR, not empty,
    not an unresolved truncation) at any point THIS turn. Unlike every other mechanical check in
    this loop, this one is unconditional with no budget limit at all — a byte-identical repeat of
    an already-succeeded call can only ever return the same answer again within one turn, so there
    is no genuine case where actually re-running it is the right call. A repeat is skipped before
    ever reaching `act()` (no wasted network/GitHub API call either) and recorded with a message
    pointing back at the matching earlier attempt, for both a lone action and any item inside a
    batch."""
    attempts: list = list(initial_attempts or [])
    final_answer = None
    # Defaults true (show the receipt) whenever a "final" doesn't explicitly say otherwise —
    # a missing/malformed field is more likely a parsing hiccup than a deliberate "hide this",
    # so err toward the more transparent option rather than silently dropping useful context.
    show_work = True
    retry_nudge_count = 0
    unretried_inconclusive_tools: dict = {}  # tool_action_name -> args signature of the failing call
    # A real production trace showed even 3 rejections (deep thinking's full retry_nudge budget)
    # isn't always enough: the model just kept re-submitting a "final" without ever taking the
    # corrective action, and once the budget ran out it walked straight through with 3 different
    # files still truncated and never re-read. Unlike an ERROR/empty result — which might be a
    # genuinely unfixable dead end, so a bounded budget before accepting an honest "I couldn't"
    # is the right call — a truncated file is never actually a dead end: the rest of it is right
    # there. Tracked separately so a "final" is rejected UNCONDITIONALLY (no budget) while any
    # tool_action_name in this set has an unresolved truncation, instead of sharing
    # retry_nudge_count's limited budget with genuinely-unfixable failures.
    truncated_unresolved_tools: set = set()
    # A real production trace (docs/coding-agent-roadmap.md, Section 9) showed the model re-run
    # the exact same tool_action + args it had already gotten a real, successful result for
    # earlier in the SAME turn — re-reading a file/interface it had already read minutes (and
    # several steps) before, burning step budget on zero new information. Unlike every other
    # tracking dict here, this isn't about a failure — it's the opposite: (tool_action_name,
    # args_signature) pairs are added here only on a genuine SUCCESS (see _record_action_result),
    # so a later identical call can be recognized as pure redundant repeat and skipped without
    # ever hitting the network/GitHub API a second time for it.
    succeeded_action_signatures: set = set()
    # A real trace (Section 10) showed a subtler waste than an exact repeat: after read_repo_file
    # truncates a file (no start_line given), its own response already lists every top-level
    # def/class and its real line number — but the model ran two MORE search tools trying to
    # relocate a symbol it had already been told the line number for, then still guessed the
    # wrong start_line anyway. Maps path -> {symbol_name: line_number} from every truncated
    # read_repo_file result seen this turn, so a later start_line guess for a named symbol can be
    # checked against real ground truth instead of trusted blindly (see find_mismatched_start_line_note).
    definition_indexes_by_path: dict = {}
    stuck_action_streak = {"tool": None, "count": 0}  # consecutive misses on ONE tool, any args
    stuck_action_reject_count = 0
    MAX_STUCK_ACTION_REJECTIONS = 1
    capability_denial_reject_count = 0
    MAX_CAPABILITY_DENIAL_REJECTIONS = 1
    pending_capability_denial_notice: str | None = None
    ungrounded_diff_reject_count = 0
    MAX_UNGROUNDED_DIFF_REJECTIONS = 1
    pending_ungrounded_diff_notice: str | None = None

    for step in range(max_iterations):
        forced_final = step == max_iterations - 1
        # Shown on every step with an outstanding failure or empty result, not just once —
        # only the actual rejection of a premature "final" below is budget-limited (via
        # retry_nudge_count), so the model still sees the reminder if it takes an unrelated
        # detour (like listing the repo tree) before eventually trying to conclude.
        needs_retry_nudge = not forced_final and bool(unretried_inconclusive_tools)

        stuck_redirect_entry = None
        if stuck_action_streak["tool"]:
            # A tool-specific entry (like search_code's) always wins when listed — it can give
            # more targeted advice ("call find_file instead") than the generic fallback below can.
            # Every OTHER tool still gets the generic circuit breaker instead of no backstop at
            # all — see _DEFAULT_STUCK_ACTION_MESSAGE's comment for why this is no longer
            # search_code-specific.
            stuck_redirect_entry = (stuck_action_redirects or {}).get(stuck_action_streak["tool"]) or (
                _DEFAULT_STUCK_ACTION_THRESHOLD,
                _DEFAULT_STUCK_ACTION_MESSAGE.format(tool=stuck_action_streak["tool"]),
            )
        stuck_redirect_active = (
            not forced_final
            and stuck_redirect_entry is not None
            and stuck_action_streak["count"] >= stuck_redirect_entry[0]
        )

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
                "nothing exists. Actually retry it with corrected information — a DIFFERENT "
                "query/path/args than the one that just failed (calling it again with the exact "
                "same args does not count as retrying it, and neither does a different "
                "diagnostic action, like listing the repo tree) — before concluding. Only choose "
                "action=\"final\" now if you are certain nothing else could help.)"
            )
        if truncated_unresolved_tools:
            # More insistent than the generic nudge above, and names the actual path when it can
            # — a real trace showed the generic reminder alone wasn't enough to change behavior
            # across 3 rejections in a row. This one keeps firing with NO budget limit (see
            # truncated_unresolved_tools' own comment) because more content is always available
            # here, unlike a genuinely-failed action that might really be a dead end.
            stuck_path = None
            for stuck_tool in truncated_unresolved_tools:
                try:
                    stuck_path = json.loads(unretried_inconclusive_tools.get(stuck_tool, "{}")).get("path")
                except (TypeError, ValueError):
                    stuck_path = None
                if stuck_path:
                    break
            path_hint = f" (the one you left unfinished was {stuck_path})" if stuck_path else ""
            question_for_step += (
                f"\n\n(You have not finished reading a file you started{path_hint} — it was "
                "truncated and you never called read_repo_file again with a start_line to see "
                "the rest. This is NOT a failed or empty result — the rest of the file is right "
                "there waiting to be read. You MUST call read_repo_file with a start_line on that "
                "same path as your very next action. This will keep being rejected, with no "
                "limit, until you actually do this — proposing code changes to a file you have "
                "not fully read is not acceptable.)"
            )
        if stuck_redirect_active:
            question_for_step += f"\n\n({stuck_redirect_entry[1]})"
        if pending_capability_denial_notice:
            question_for_step += f"\n\n({pending_capability_denial_notice})"
            pending_capability_denial_notice = None
        if pending_ungrounded_diff_notice:
            question_for_step += f"\n\n({pending_ungrounded_diff_notice})"
            pending_ungrounded_diff_notice = None

        prompt = prompt_template.format(
            question=question_for_step,
            schema=schema,
            attempts=_format_react_attempts(attempts),
            architecture_map=architecture_map,
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
        if action == "final" and not forced_final and truncated_unresolved_tools:
            # Unconditional — no budget check, unlike the ERROR/empty case below. A real
            # production trace showed the model exhaust the ENTIRE deep-thinking retry budget
            # (3 rejections) re-submitting "final" without ever actually re-reading any of the
            # 3 files it had left truncated, then walk straight through once the budget ran out.
            # A truncated file is never a genuine dead end, so there's no principled reason to
            # ever let this go until it's actually resolved or the loop is forced to conclude.
            continue
        if action == "final" and needs_retry_nudge and retry_nudge_count < max_retry_nudges:
            # Told to retry and it tried to conclude anyway — force one more real step instead
            # of accepting a premature answer. retry_nudge_count only increments here (at the
            # actual rejection), not just when the nudge was shown, so a detour in between
            # (e.g. it lists the repo tree first) doesn't spend the budget for free.
            retry_nudge_count += 1
            continue
        if action == "final" and not forced_final and capability_denial_watchlist and capability_denial_reject_count < MAX_CAPABILITY_DENIAL_REJECTIONS:
            answer_text = decision.get("answer") or ""
            denied_tool_marker = None
            if _CAPABILITY_DENIAL_RE.search(answer_text):
                for capability_re, tool_menu_substring in capability_denial_watchlist:
                    if capability_re.search(answer_text) and tool_menu_substring in prompt_template:
                        denied_tool_marker = tool_menu_substring
                        break
            if denied_tool_marker:
                # A confident, detailed denial isn't more trustworthy than a short one if the
                # capability is sitting right there in the menu — reject once (not executed,
                # not recorded as an attempt) instead of trusting the model caught its own
                # contradiction. Budget-limited for the same reason as every other mechanical
                # rejection here: a genuinely correct "I don't have that" (a capability that
                # really isn't in the menu) must still get through eventually.
                capability_denial_reject_count += 1
                pending_capability_denial_notice = (
                    f"Your last answer denied having a capability, but '{denied_tool_marker}' is "
                    "listed in AVAILABLE ACTIONS THIS TURN above — you do have it right now. Do "
                    "not deny having it; if you haven't actually used it yet this turn, use it "
                    "before answering."
                )
                continue
        if action == "final" and not forced_final and ungrounded_diff_reject_count < MAX_UNGROUNDED_DIFF_REJECTIONS:
            answer_text = decision.get("answer") or ""
            ungrounded_path = _final_diff_disagrees_with_fetched_content(answer_text, attempts)
            if ungrounded_path:
                # Same shape as the capability-denial rejection above, one step later in the same
                # real trace that motivated it: a confidently-formatted diff isn't more trustworthy
                # than a rough one if none of what it claims already exists in the file ever
                # actually came back from a real read this turn. Budget-limited for the same reason
                # as every other mechanical rejection here — a real diff against a file genuinely
                # read earlier in the conversation (outside this loop's own attempts) must still be
                # allowed through eventually rather than looping forever on a false positive.
                ungrounded_diff_reject_count += 1
                pending_ungrounded_diff_notice = (
                    f"Your proposed diff edits {ungrounded_path}, but none of the lines it claims "
                    f"already exist there ever appeared in a real read_repo_file result for that "
                    f"exact path this turn. Call read_repo_file({ungrounded_path}) for real, quote "
                    "the actual current lines you're changing, and rebuild the diff from what's "
                    "really there before answering again — do not guess at the file's structure."
                )
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

        queries = decision.get("queries")
        # >= 1, not > 1 — a "queries" list with exactly one item still has to go through the
        # batch path below, since it puts the real tool_action/args inside that one list item
        # rather than at the top level; the batch machinery already handles any size >= 1
        # correctly (asyncio.gather over a single task works fine), so there's no need for a
        # separate single-item normalization path.
        is_batch = isinstance(queries, list) and len(queries) >= 1
        batch_tool_names = [q.get("tool_action") for q in queries if isinstance(q, dict)] if is_batch else []
        stuck_tool_in_this_step = (
            stuck_action_streak["tool"] in batch_tool_names if is_batch
            else decision.get("tool_action") == stuck_action_streak["tool"]
        )
        if (
            stuck_redirect_active
            and stuck_tool_in_this_step
            and stuck_action_reject_count < MAX_STUCK_ACTION_REJECTIONS
        ):
            # Told to switch away from this tool_action and it tried it again anyway — whether
            # alone or slipped into a batch alongside other actions — reject the WHOLE step
            # outright (nothing in it executes, nothing recorded as an attempt) instead of just
            # hoping the redirect message alone changes its mind, the same mechanical escalation
            # already used for a premature "final". Budget-limited for the same reason: a
            # genuinely doomed switch shouldn't force a second forced rejection on top of the first.
            stuck_action_reject_count += 1
            continue

        async def _execute_one_action(
            tool_action_name: str, args: dict, purpose: str,
            batch_index: int | None = None, batch_size: int | None = None,
        ) -> str:
            # Shared by the single-action and batch paths below so a batched action gets the
            # exact same real execution (trace emission, is_unsafe, error handling) a sequential
            # step would have — no weaker scrutiny just because it ran alongside others.
            # batch_index/batch_size (only passed by the batch path, and only when the batch has
            # more than one item) let the frontend trace panel actually show when concurrent
            # batching happened, instead of the only way to confirm it being to read the backend
            # log and notice several attempts sharing one step number.
            sub_decision = {"tool_action": tool_action_name, "args": args, "purpose": purpose}
            trace_payload = {"node": node_name, "title": "Working...", "detail": purpose}
            if batch_size and batch_size > 1:
                trace_payload["batch_index"] = batch_index
                trace_payload["batch_size"] = batch_size
            await safe_emit_event("trace_detail", trace_payload)
            if is_unsafe(sub_decision):
                raise _UnsafeActionRequested(sub_decision)
            try:
                observation = act(sub_decision)
                if asyncio.iscoroutine(observation):
                    observation = await observation
            except Exception as e:
                observation = f"ERROR: {e}"
            return _truncate_observation(observation)

        def _record_action_result(tool_action_name: str, args: dict, purpose: str, observation: str) -> None:
            # Exactly the bookkeeping a real sequential step already did — extracted so it can be
            # applied once per item in a batch, in order, instead of only ever seeing one
            # tool_action per step.
            nonlocal stuck_action_streak
            if tool_action_name:
                args_signature = json.dumps(args or {}, sort_keys=True, default=str)
                prior_args_signature = unretried_inconclusive_tools.get(tool_action_name)
                is_unresolved_truncation = _mentions_unresolved_truncation(observation)
                still_failing = (
                    observation.startswith("ERROR")
                    or _is_empty_observation(observation)
                    or is_unresolved_truncation
                )
                if is_unresolved_truncation:
                    unretried_inconclusive_tools[tool_action_name] = args_signature
                    truncated_unresolved_tools.add(tool_action_name)
                else:
                    truncated_unresolved_tools.discard(tool_action_name)
                    if prior_args_signature is not None:
                        if not still_failing or args_signature != prior_args_signature:
                            del unretried_inconclusive_tools[tool_action_name]
                    elif still_failing:
                        unretried_inconclusive_tools[tool_action_name] = args_signature

                if not still_failing:
                    succeeded_action_signatures.add((tool_action_name, args_signature))

                if tool_action_name == "read_repo_file":
                    path = (args or {}).get("path")
                    start_line = (args or {}).get("start_line")
                    if path and not start_line:
                        index = parse_definition_index_from_observation(observation)
                        if index:
                            definition_indexes_by_path[path] = index
                    elif path and start_line:
                        note = find_mismatched_start_line_note(
                            purpose, path, start_line, (args or {}).get("line_count"),
                            _READ_FILE_DEFAULT_LINE_WINDOW, definition_indexes_by_path.get(path, {}),
                        )
                        if note:
                            observation = f"{observation}\n\n{note}"

                is_stuck_worthy_miss = still_failing and not is_unresolved_truncation
                if is_stuck_worthy_miss and stuck_action_streak["tool"] == tool_action_name:
                    stuck_action_streak["count"] += 1
                elif is_stuck_worthy_miss:
                    stuck_action_streak = {"tool": tool_action_name, "count": 1}
                else:
                    stuck_action_streak = {"tool": None, "count": 0}
            args_summary = ", ".join(f"{k}={v}" for k, v in (args or {}).items())
            action_desc = f"{tool_action_name}({args_summary})" if tool_action_name else (args_summary or "")
            logger.info(
                "[%s] Step %s (%s) — action=%s | observation=%r",
                node_name, step + 1, purpose, action_desc or "(none)", observation[:200],
            )
            attempts.append({"purpose": purpose, "action_desc": action_desc, "observation": observation})

        def _is_redundant_repeat(tool_action_name: str, args: dict) -> bool:
            if not tool_action_name:
                return False
            args_signature = json.dumps(args or {}, sort_keys=True, default=str)
            return (tool_action_name, args_signature) in succeeded_action_signatures

        _REDUNDANT_REPEAT_MESSAGE = (
            "(Skipped — you already ran this exact action with these exact args earlier this "
            "turn and it succeeded. Re-use that real result from the matching attempt above "
            "instead of running it again.)"
        )

        if is_batch:
            batch_purpose = decision.get("purpose") or "Working..."
            valid_items: list[dict] = []
            for item in queries:
                item = item if isinstance(item, dict) else {}
                tool_name = item.get("tool_action")
                item_purpose = item.get("purpose") or batch_purpose
                item_args = item.get("args") or {}
                if tool_name and batchable_actions and tool_name in batchable_actions:
                    if _is_redundant_repeat(tool_name, item_args):
                        _record_action_result(tool_name, item_args, item_purpose, _REDUNDANT_REPEAT_MESSAGE)
                    elif len(valid_items) < _MAX_BATCH_SIZE:
                        valid_items.append(item)
                    else:
                        _record_action_result(
                            tool_name, item_args, item_purpose,
                            f"ERROR: too many actions in one batch (max {_MAX_BATCH_SIZE}) — "
                            "this one was dropped; retry it in a later step.",
                        )
                elif tool_name:
                    _record_action_result(
                        tool_name, item_args, item_purpose,
                        f"ERROR: '{tool_name}' cannot be batched with other actions this way — "
                        "call it in its own step instead.",
                    )
                else:
                    _record_action_result(
                        "", item_args, item_purpose,
                        "ERROR: a batched item was missing a valid tool_action and was not executed.",
                    )
            if valid_items:
                observations = await asyncio.gather(*(
                    _execute_one_action(
                        item["tool_action"], item.get("args") or {}, item.get("purpose") or batch_purpose,
                        batch_index=i + 1, batch_size=len(valid_items),
                    )
                    for i, item in enumerate(valid_items)
                ))
                for item, observation in zip(valid_items, observations):
                    _record_action_result(
                        item["tool_action"], item.get("args") or {}, item.get("purpose") or batch_purpose, observation,
                    )
            continue

        purpose = decision.get("purpose", "Working...")
        tool_action_name = decision.get("tool_action") or ""
        args = decision.get("args") or {}
        if _is_redundant_repeat(tool_action_name, args):
            observation = _REDUNDANT_REPEAT_MESSAGE
        else:
            observation = await _execute_one_action(tool_action_name, args, purpose)
        _record_action_result(tool_action_name, args, purpose, observation)

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
                architecture_map=architecture_map,
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


# find_file's fuzzy matching, so a colloquial name ("navbar") can still surface a differently
# named real file (menu-navigator.tsx) even though they share no exact token — search_code only
# matches GitHub's literal keyword index, which finds nothing at all in that case. Splits both on
# non-alphanumeric characters AND camelCase boundaries so "MenuNavigator" and "menu-navigator"
# tokenize to the same {"menu", "navigator"} regardless of naming convention.
_PATH_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FUZZY_MATCH_CUTOFF = 0.5
_FUZZY_MATCH_LIMIT = 15


def _tokenize_for_fuzzy_match(text: str) -> set:
    tokens = set()
    for fragment in _PATH_TOKEN_SPLIT_RE.split(text):
        if not fragment:
            continue
        for sub in _CAMEL_BOUNDARY_RE.split(fragment):
            if sub:
                tokens.add(sub.lower())
    return tokens


def _fuzzy_path_score(query_tokens: set, path: str) -> float:
    """Best similarity between any query token and any token in `path` (folder names and
    filename, extension stripped) — an exact token match short-circuits to 1.0, otherwise
    falls back to difflib's character-level ratio so near-misses (navbar/navigator, singular
    vs. plural) still score usefully instead of an all-or-nothing exact match."""
    path_tokens = _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", path))
    if not path_tokens:
        return 0.0
    best = 0.0
    for query_token in query_tokens:
        for path_token in path_tokens:
            if query_token == path_token:
                return 1.0
            ratio = difflib.SequenceMatcher(None, query_token, path_token).ratio()
            if ratio > best:
                best = ratio
    return best


# trace_symbol's write-vs-read classification — regex-based (not a real parser) by design, to
# stay consistent with find_file's own heuristic rather than pull in a per-language AST
# dependency for one tool. Covers both this repo's Python and TypeScript/React conventions:
# a plain assignment, an attribute/dict-style assignment, a def/class that IS the symbol, a
# React state setter call (setSymbol(...)), a useState/useReducer/useRef/useMemo destructuring
# that defines the symbol, and a function returning it — matching exactly the "write/decide
# site" categories described in docs/coding-agent-roadmap.md. Everything else that mentions the
# symbol (a read, a prop being consumed, a log line, a re-export) falls through to "read".
def _classify_symbol_line(symbol: str, line: str) -> str:
    stripped = line.strip()
    escaped = re.escape(symbol)

    if re.match(rf'^(export\s+)?(async\s+)?(def|function|class)\s+{escaped}\b', stripped):
        return "write"

    setter_name = f"set{symbol[0].upper()}{symbol[1:]}" if symbol else ""
    if setter_name and re.search(rf'\b{re.escape(setter_name)}\s*\(', stripped):
        return "write"

    if re.search(rf'\b{escaped}\b[^=]*=\s*use(State|Reducer|Ref|Memo|Context)\s*\(', stripped):
        return "write"

    if re.search(rf'(?<![=!<>]){escaped}\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'\.{escaped}\s*=(?!=)', stripped) or re.search(rf'\[["\']{escaped}["\']\]\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'^return\b.*\b{escaped}\b', stripped):
        return "write"

    return "read"


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


# A real production trace showed a static system-prompt paragraph telling the model to reach
# for browser_navigate on "a real page's current, real content or appearance" wasn't enough —
# asked to "inspect the actual page" to fix a z-index conflict, it did 15 steps of pure repo
# reading and never opened a browser once (see docs/coding-agent-roadmap.md, "Failure B").
# Rather than trust a general system-prompt rule to out-compete many turns of code-reading
# momentum, this detects the specific category of question (layout/rendering/visual state that
# literally cannot be confirmed from source alone) and injects a directive into THIS turn's own
# question text — much harder to deprioritize than a rule buried among many others.
_VISUAL_INSPECTION_RE = re.compile(
    r"\b(inspect|actual page|actual site|how (?:it|this|that) (?:looks|renders|appears)|"
    r"z-?index|stacking|overlap(?:ping)?|visually|on[- ]?screen|in the browser|"
    r"css (?:issue|bug|problem)|layout (?:issue|bug|problem)|rendering (?:issue|bug|problem))\b",
    re.IGNORECASE,
)


def _mentions_visual_inspection(text: str) -> bool:
    return bool(_VISUAL_INSPECTION_RE.search(text or ""))


# A real production trace showed the default step budget forced a premature, fabricated answer
# on a "scan the repo and plan this refactor" ask: it needs one search plus a confirmatory
# trace_symbol per candidate call site to actually be exhaustive, which the flat 7-step (or even
# 14-step deep) budget doesn't leave room for — so it pattern-completed the rest instead of
# admitting it ran out of steps. This detects that task shape from the user's own wording and
# grants it the same higher budget deep_thinking gets, regardless of whether deep_thinking is on.
_AUDIT_TASK_RE = re.compile(
    r"\b(scan the (?:whole |entire )?repo|every (?:file|place|call ?site|usage|occurrence)s?|"
    r"across the (?:whole |entire )?(?:repo|codebase)|cross-cutting|\baudit\b|"
    r"(?:refactor|migration|architecture) plan|which files (?:do we|need to|touch|are)|"
    r"how many files|plan (?:the|this|a) (?:refactor|migration|architecture))\b",
    re.IGNORECASE,
)


def _is_audit_style_task(text: str) -> bool:
    return bool(_AUDIT_TASK_RE.search(text or ""))


# search_literal exists specifically because search_code rides GitHub's hosted search index
# (capped at ~20 results, subject to indexing lag) and can silently miss real matches — but it's
# opt-in, so an audit-style task ("find every place X is used") can still reach for search_code
# out of habit and come back with a plausible-looking but incomplete answer (docs/coding-agent-
# roadmap.md, Section 4c). Prose guidance for this already existed in TOOL_AGENT_PROMPT and wasn't
# enough on its own — same lesson this whole file keeps re-learning — so this rides the same
# mechanical injection already proven for the architecture map below instead of adding a new one.
_AUDIT_TASK_SEARCH_NUDGE = (
    "AUDIT TASK DETECTED: prefer search_literal over search_code for exhaustive results this turn "
    "— search_code rides GitHub's hosted search index (capped, subject to indexing lag) and can "
    "miss real matches; search_literal greps the actual repo tree directly.\n"
)


# Architecture map — an auto-injected, repo-wide internal-import graph for audit-style tasks
# (docs/coding-agent-roadmap.md, Section 4c). search_literal/trace_symbol find where a SYMBOL is
# referenced; this shows which FILES depend on which other files, which is what actually answers
# "what else does this touch" for a cross-file refactor — the exact gap that produced the
# fabricated per-user-token plan in Section 4b. Injected straight into the prompt (like `schema`
# already is) rather than offered as a new opt-in tool_action, because this project has now
# independently shown three times (Sections 0, 0b, 4b) that a capability the model must remember
# to reach for gets skipped under pressure.
_ARCH_MAP_MAX_FILES_SCANNED = 200
_ARCH_MAP_MAX_FILE_BYTES = 200_000
_ARCH_MAP_MAX_ENTRIES = 150
_ARCH_MAP_PY_EXTENSION = ".py"
_ARCH_MAP_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

_JS_IMPORT_RE = re.compile(
    r"""(?:^\s*import\s+['"]([^'"]+)['"])|(?:\bfrom\s+['"]([^'"]+)['"])|(?:\brequire\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)


def _extract_python_imports(content: str) -> list[str]:
    # Best-effort, same spirit as _classify_symbol_line's regex heuristic — a file with a real
    # (rare) syntax error just contributes no edges to the map instead of failing the whole thing.
    # Relative imports are kept as their literal dotted form (e.g. ".utils") rather than resolved
    # to a file path — the model can trivially map that back to a real path itself, and it avoids
    # building a second, error-prone module-resolution layer for comparatively little benefit.
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * (node.level or 0)
            if node.module:
                imports.append(f"{dots}{node.module}")
            else:
                # `from . import utils` — module is None, the actual reference (a sibling
                # module) lives in the imported names instead.
                imports.extend(f"{dots}{alias.name}" for alias in node.names)
    return imports


def _extract_js_imports(content: str) -> list[str]:
    # Regex, not a real parser — matches this codebase's own precedent (_classify_symbol_line)
    # for TS/JS specifically. Covers `import x from '...'`, bare `import '...'`, and
    # `require('...')`; does not cover dynamic `import(variable)` or path-aliased imports
    # (e.g. tsconfig `@/`) — an acceptable, documented gap rather than a bundler-grade resolver.
    imports = []
    for match in _JS_IMPORT_RE.finditer(content):
        imports.append(next(g for g in match.groups() if g))
    return imports


def _is_internal_python_import(import_str: str, top_level_segments: set) -> bool:
    if import_str.startswith("."):
        return True
    return import_str.split(".")[0] in top_level_segments


def _repo_top_level_segments(tree_items: list) -> set:
    # Derived from the real tree every time (never hardcoded) so this works identically on any
    # repo, not just this one — matches this session's own multi-repo/multi-user design goal.
    segments = set()
    for item in tree_items:
        path = item.get("path", "")
        parts = path.split("/")
        segments.add(parts[0])
        if len(parts) == 1 and parts[0].endswith(_ARCH_MAP_PY_EXTENSION):
            segments.add(parts[0][: -len(_ARCH_MAP_PY_EXTENSION)])
    return segments


def _build_architecture_map(tree_items: list, fetch_content) -> str:
    """Builds a compact FILE -> internal imports (and its inverse) map from real file content —
    fetch_content(path) must return (content, error) like _fetch_file_content does. Only internal
    (own-repo) imports are kept; a bare package import (os, react, requests) would otherwise
    dominate the reverse map with useless high fan-in and drown out the edges that actually matter
    for scoping a refactor's blast radius."""
    top_level_segments = _repo_top_level_segments(tree_items)
    candidates = [
        item for item in tree_items
        if item.get("path", "").endswith((_ARCH_MAP_PY_EXTENSION,) + _ARCH_MAP_JS_EXTENSIONS)
        and item.get("size", 0) <= _ARCH_MAP_MAX_FILE_BYTES
    ][:_ARCH_MAP_MAX_FILES_SCANNED]

    imports_by_file: dict = {}
    imported_by: dict = {}
    for item in candidates:
        path = item.get("path", "")
        content, error = fetch_content(path)
        if error or not isinstance(content, str):
            continue
        if path.endswith(_ARCH_MAP_PY_EXTENSION):
            internal = [i for i in _extract_python_imports(content) if _is_internal_python_import(i, top_level_segments)]
        else:
            internal = [i for i in _extract_js_imports(content) if i.startswith(".")]
        if not internal:
            continue
        imports_by_file[path] = sorted(set(internal))
        for imp in internal:
            imported_by.setdefault(imp, set()).add(path)

    if not imports_by_file:
        return ""

    forward_lines = [
        f"  {path} -> {', '.join(imports_by_file[path])}"
        for path in sorted(imports_by_file)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    reverse_lines = [
        f"  {imp} <- imported by: {', '.join(sorted(imported_by[imp]))}"
        for imp in sorted(imported_by)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    return (
        "PRE-COMPUTED ARCHITECTURE MAP (real internal-import graph — exhaustive for the files "
        "scanned, not a guess; use this to find every file a change would touch instead of "
        "relying on search_code alone):\n"
        "FILE -> ITS INTERNAL IMPORTS:\n" + "\n".join(forward_lines) +
        "\n\nINTERNAL MODULE -> IMPORTED BY:\n" + "\n".join(reverse_lines)
    )


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

        # See TOOL_AGENT_HISTORY_MAX_MESSAGES's comment: a multi-turn task's real instruction, or
        # what a short reply like "yes"/"go ahead" is actually confirming, often lives a few
        # turns back — {question} alone (just this turn's message) can't recover that.
        prior_messages = state.get("messages", [])[:-1][-TOOL_AGENT_HISTORY_MAX_MESSAGES:]
        formatted_history = "\n".join(
            f"{getattr(m, 'type', 'user')}: {getattr(m, 'content', '')}" for m in prior_messages
        ) or "(no prior messages this conversation)"

        # Resuming a paused clarification: state["paused_clarification"] (real checkpointed
        # state — the one field reset_transient_state deliberately leaves alone) carries the
        # original question and the attempts already made, so the loop continues instead of
        # starting over. Recombine into one question for the loop, then clear it immediately —
        # it must not linger into the turn after this one.
        resumed_attempts: list = []
        paused = state.get("paused_clarification")
        if paused:
            resumed_attempts = paused.get("attempts", [])
            original_question = paused.get("original_question", msg)
            msg = (
                f"{original_question}\n\n"
                f"(You previously asked the user for clarification; they answered: \"{msg}\")"
            )
            state["paused_clarification"] = None

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

        if _mentions_visual_inspection(msg):
            msg += (
                "\n\n(This question is about how the page actually renders or behaves right "
                "now — a layout, z-index, stacking, or visual-appearance question cannot be "
                "answered from source code alone, since the real computed styles and DOM "
                "stacking context only exist at runtime, not in any file. Use browser_navigate "
                "(then browser_screenshot/browser_read_text as needed) to actually look at the "
                "live page before concluding — do not answer from reading CSS/component source "
                "alone just because it looks plausible.)"
            )

        token = os.getenv("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Accept": "vnd.github+json"}
        api_base = "https://api.github.com"
        gh_base = "https://github.com"

        def _get_default_branch():
            # Returns None (not a guessed "main") on failure — a real production trace showed
            # extract_github_repo can still false-positive on an ordinary phrase with a bare slash
            # in it (e.g. "the updated functions/diffs for whatever needs to change" was read as
            # owner/repo "functions/diffs") even with its existing generic-path-segment denylist,
            # since that denylist can never cover every ordinary English word pair. Silently
            # falling back to branch "main" while keeping the WRONG repo meant every single
            # GitHub action that turn 404'd with no way for the model to ever learn why — it just
            # kept retrying list_repo_tree, unable to diagnose a bad premise it never saw. None
            # lets the caller retry against a known-good repo instead of guessing a branch for one
            # that doesn't exist.
            repo_res = requests.get(f"{api_base}/repos/{repo}", headers=headers)
            if repo_res.status_code != 200:
                logger.warning(
                    "[tool_agent_node] Could not fetch repo metadata for %r (status %s).",
                    repo, repo_res.status_code,
                )
                return None
            return repo_res.json().get("default_branch", "main")

        default_branch = await asyncio.to_thread(_get_default_branch)
        repo_resolution_note = ""
        if default_branch is None:
            attempted_repo = repo
            fallback_repo = state.get("repo") or "SummonShenron/SAAPP"
            if fallback_repo != attempted_repo:
                # Reassigned in this same enclosing scope, before any of the closures below
                # (_fetch_repo_tree_items, _read_file, _get_default_branch itself, ...) are ever
                # CALLED — Python resolves a free variable in its enclosing scope at call time,
                # not at def time, so every GitHub action this turn correctly targets the
                # corrected repo without needing to thread a new parameter through each one.
                repo = fallback_repo
                default_branch = await asyncio.to_thread(_get_default_branch)
                if default_branch is not None:
                    repo_resolution_note = (
                        f"NOTE: the repo detected from your message ('{attempted_repo}') could not "
                        f"be found on GitHub — most likely a false-positive extraction from ordinary "
                        f"text containing a '/' rather than a real repo mention, not an environment "
                        f"or token problem. Automatically fell back to '{repo}' instead; every "
                        f"action below targets this repo. If '{repo}' is also wrong, say the "
                        f"correct owner/repo explicitly rather than retrying the same lookup."
                    )
            if default_branch is None:
                default_branch = "main"
                repo_resolution_note = repo_resolution_note or (
                    f"WARNING: could not confirm repo '{repo}' exists or is accessible on GitHub — "
                    f"every action below may 404. This means the repo itself is wrong or "
                    f"inaccessible, not that a specific file/path is missing — if repeated lookups "
                    f"keep failing, say so plainly instead of retrying the same no-argument action "
                    f"again with only the stated purpose reworded."
                )
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
        if repo_resolution_note:
            schema_parts.append(repo_resolution_note)
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

        # One CDP session per tool_agent_node call, lazily connected on the first browser_*
        # action and reused by every subsequent one this turn — the finally block around
        # run_react_loop below closes it exactly once when the loop ends, on every exit path.
        # "live_view_emitted" guards the browser_live_view custom event below so it only ever
        # fires once per turn, the moment a LiveURL first becomes available.
        browser_session_holder: dict = {"session": None, "live_view_emitted": False}

        # Per-turn caches — _fetch_repo_tree_items was previously called fresh by every one of
        # _fetch_repo_paths/_find_file/_search_literal, and _read_file re-fetched from GitHub
        # even for a path already read earlier in the same investigation (search_code pointing
        # back to a file already opened is a common pattern). Scoped to this single
        # tool_agent_node call, same lifetime as browser_session_holder above — never persisted
        # across turns, so this can't go stale the way a session- or process-level cache could.
        _turn_tree_cache: dict = {}
        _turn_file_cache: dict = {}

        async def _get_browser_session() -> BrowserSession:
            if browser_session_holder["session"] is None:
                ws_endpoint = os.getenv("BROWSERLESS_WS_ENDPOINT")
                if not ws_endpoint:
                    raise RuntimeError("BROWSERLESS_WS_ENDPOINT is not configured")
                timeout_ms = int(os.getenv("BROWSER_TOOL_TIMEOUT_SECONDS", "20")) * 1000
                browser_session_holder["session"] = BrowserSession(ws_endpoint, timeout_ms)
            return browser_session_holder["session"]

        def _run_mongo_code(code: str):
            local_scope = {"db": db, "username": username, "result": None}
            exec(code, {"__builtins__": exec_builtins}, local_scope)
            return local_scope.get("result", None)

        def _fetch_repo_tree_items():
            # Shared by _fetch_repo_paths and _search_literal — a single real fetch of every
            # blob this repo actually has (path, size, sha), so both path-only consumers and
            # content-scanning consumers work off the same real tree instead of two divergent
            # fetches. Cached per turn (see _turn_tree_cache above) — only the success case is
            # cached, so a transient failure doesn't get "stuck" for the rest of the turn.
            if "items" in _turn_tree_cache:
                return _turn_tree_cache["items"]
            tree_url = f"{api_base}/repos/{repo}/git/trees/{default_branch}?recursive=1"
            res = requests.get(tree_url, headers=headers)
            if res.status_code != 200:
                return f"ERROR: could not fetch tree ({res.status_code})"
            tree_items = res.json().get("tree", [])
            items = [
                item for item in tree_items
                if item.get("type") == "blob"
                and not any(exclude in item.get("path", "") for exclude in ["node_modules", "dist", "__pycache__"])
            ]
            _turn_tree_cache["items"] = items
            return items

        def _fetch_repo_paths():
            # Shared by _list_tree and _find_file — path-only view of the same tree, so
            # find_file's fuzzy matching runs against the full tree, not just whatever
            # _list_tree happens to truncate its own display to.
            items = _fetch_repo_tree_items()
            if isinstance(items, str):
                return items
            return [item.get("path") for item in items]

        def _list_tree():
            paths = _fetch_repo_paths()
            if isinstance(paths, str):
                return paths  # propagate the "ERROR: ..." string from a failed fetch
            return "\n".join(paths[:400])

        def _find_file(query: str):
            # Local, no-index fuzzy file finder — scores every path list_repo_tree would
            # already return against the query with difflib, entirely client-side. No
            # embeddings, no per-repo setup, no external index to keep in sync — it works
            # identically on any repo/user's tree the moment it's fetched, which is what makes
            # it scale to other users' and other repos without a provisioning step. This is
            # what closes the gap search_code's literal keyword index can't: a colloquial name
            # ("navbar") that shares no exact token with the real file (menu-navigator.tsx).
            if not query:
                return "ERROR: no query given"
            paths = _fetch_repo_paths()
            if isinstance(paths, str):
                return paths
            query_tokens = _tokenize_for_fuzzy_match(query)
            if not query_tokens:
                return "ERROR: no usable query tokens"
            scored = [
                (path, _fuzzy_path_score(query_tokens, path))
                for path in paths
            ]
            scored = [pair for pair in scored if pair[1] >= _FUZZY_MATCH_CUTOFF]
            if not scored:
                return "No similar file paths found."
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return "\n".join(f"{path} (similarity {score:.2f})" for path, score in scored[:_FUZZY_MATCH_LIMIT])

        def _fetch_file_content(path: str):
            # Raw decoded file text, with no URL/truncation formatting applied — the primitive
            # both _read_file's human-facing display and any future machine-parseable consumer
            # (e.g. an AST-based import scan) should build on, instead of each doing its own
            # fetch. Returns (content, error): error is a human-readable "ERROR: ..." string on
            # failure, content is None in that case — kept as two return values rather than one
            # (with an isinstance/prefix check) because unlike _fetch_repo_tree_items's list vs.
            # str split, both success and failure here are strings, so a real file that happened
            # to start with the literal text "ERROR" would be indistinguishable from a failure.
            # Cached per turn (see _turn_file_cache above) — only successes are cached.
            if path in _turn_file_cache:
                return _turn_file_cache[path], None
            file_url = f"{api_base}/repos/{repo}/contents/{path}"
            res = requests.get(file_url, headers=headers)
            if res.status_code != 200:
                return None, f"ERROR: could not fetch {path} ({res.status_code})"
            file_data = res.json()
            try:
                decoded = base64.b64decode(file_data.get("content", "")).decode("utf-8", errors="replace")
            except Exception:
                return None, f"ERROR: could not decode {path}"
            _turn_file_cache[path] = decoded
            return decoded, None

        def _read_file(path: str, start_line=None, line_count=None):
            if not path:
                return "ERROR: no path given"
            decoded, error = _fetch_file_content(path)
            if error:
                return error
            html_url = f"{gh_base}/{repo}/blob/{default_branch}/{path}"

            try:
                start_line = int(start_line) if start_line else None
            except (TypeError, ValueError):
                start_line = None

            if start_line:
                lines = decoded.splitlines()
                if start_line > len(lines):
                    return f"ERROR: {path} only has {len(lines)} lines — start_line {start_line} is past the end"
                try:
                    count = int(line_count) if line_count else _READ_FILE_DEFAULT_LINE_WINDOW
                except (TypeError, ValueError):
                    count = _READ_FILE_DEFAULT_LINE_WINDOW
                start_idx = start_line - 1
                end_idx = start_idx + count
                window = lines[start_idx:end_idx]
                more_note = (
                    f"\n... [{len(lines) - end_idx} more lines below — re-call with a higher "
                    f"start_line to keep reading]" if end_idx < len(lines) else ""
                )
                return (
                    f"URL: {html_url}\nLines {start_line}-{start_idx + len(window)} of {len(lines)} "
                    f"total:\n{chr(10).join(window)}{more_note}"
                )

            if len(decoded) <= _READ_FILE_CHAR_CAP:
                return f"URL: {html_url}\n{decoded}"

            lines = decoded.splitlines()
            snippet = decoded[:_READ_FILE_CHAR_CAP]
            index = _build_definition_index(lines)
            index_note = (
                f"\n\n... [truncated — this file has {len(lines)} lines total, too long to show in "
                f"full. Top-level definitions found in it:\n{index}\nCall read_repo_file again with "
                f"start_line set to the one you actually need — do not assume the file's contents "
                f"past this point from general knowledge of what a file like this usually contains.]"
                if index else
                f"\n... [truncated — this file has {len(lines)} lines total; re-call with a "
                f"start_line to read further into it instead of guessing what comes next.]"
            )
            return f"URL: {html_url}\n{snippet}{index_note}"

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

        def _code_search_items(query: str):
            # Shared by _search_code and _trace_symbol — a single real GitHub code-search call,
            # requesting text-match fragments (not just paths) so trace_symbol has real matched
            # LINES to classify, not just filenames. Returns a list of items, or an "ERROR: ..."
            # string on failure — callers only need to check isinstance(result, str).
            res = requests.get(
                f"{api_base}/search/code",
                headers={**headers, "Accept": "application/vnd.github.text-match+json"},
                params={"q": f"{query} repo:{repo}", "per_page": 20},
            )
            if res.status_code != 200:
                return f"ERROR: could not search code ({res.status_code})"
            return res.json().get("items", [])

        def _search_code(query: str):
            # This is what makes "find every file this feature/bug touches" actually possible —
            # list_repo_tree only gives paths and read_repo_file only gives one file at a time,
            # neither can tell the model where else a function/class/import is referenced across
            # the repo. GitHub's code search covers exactly that gap (a function's callers, a
            # class's other usages, everywhere a config key is read).
            if not query:
                return "ERROR: no query given"
            items = _code_search_items(query)
            if isinstance(items, str):
                return items
            if not items:
                return "No matches."
            return "\n".join(item.get("path", "") for item in items)

        def _trace_symbol(symbol: str):
            # Sorts every real reference search_code would already find into WRITE/DECIDE sites
            # (an assignment, a state setter, a def/class that IS this symbol, a function
            # returning it) vs. READ/PASS-THROUGH sites (everything else — a read, a prop being
            # consumed, a log line, a re-export). search_code already tells you EVERY file that
            # mentions something; this tells you which of those hits is actually worth opening
            # first when tracing a value to where it's really decided, instead of burning steps
            # opening every hit by hand to figure that out one at a time.
            if not symbol:
                return "ERROR: no symbol given"
            items = _code_search_items(symbol)
            if isinstance(items, str):
                return items
            if not items:
                return "No matches."

            write_sites, read_sites = [], []
            for item in items:
                path = item.get("path", "")
                lines_touching_symbol = [
                    line
                    for tm in item.get("text_matches", [])
                    for line in (tm.get("fragment") or "").splitlines()
                    if symbol in line
                ]
                write_lines = [
                    line.strip() for line in lines_touching_symbol
                    if _classify_symbol_line(symbol, line) == "write"
                ]
                if write_lines:
                    write_sites.append(f"{path}: {write_lines[0]}")
                else:
                    read_sites.append(path)

            parts = []
            if write_sites:
                parts.append(
                    "WRITE/DECIDE sites (where the value is actually set/returned — check "
                    "these first):\n" + "\n".join(f"  {w}" for w in write_sites)
                )
            if read_sites:
                parts.append(
                    "READ/PASS-THROUGH sites (reads, prop consumption, re-exports):\n"
                    + "\n".join(f"  {r}" for r in read_sites)
                )
            return "\n\n".join(parts) if parts else "No matches."

        def _search_literal(term: str):
            # Exhaustive alternative to search_code — see the module-level comment above
            # _SEARCH_LITERAL_MAX_FILE_BYTES for why this exists. Fetches the real tree, then
            # actually fetches and greps candidate file content instead of trusting GitHub's
            # search index to be complete. Deliberately slower (one request per file) — use
            # search_code first; reach for this only when completeness itself is what matters
            # (e.g. "find every place this env var/config key is read" before proposing a plan).
            if not term:
                return "ERROR: no term given"
            items = _fetch_repo_tree_items()
            if isinstance(items, str):
                return items
            candidates = [
                item for item in items
                if item.get("size", 0) <= _SEARCH_LITERAL_MAX_FILE_BYTES
                and not item.get("path", "").lower().endswith(_SEARCH_LITERAL_SKIP_EXTENSIONS)
            ][:_SEARCH_LITERAL_MAX_FILES_SCANNED]

            matches = []
            scanned = 0
            for item in candidates:
                if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES:
                    break
                blob_res = requests.get(
                    f"{api_base}/repos/{repo}/git/blobs/{item.get('sha')}", headers=headers
                )
                if blob_res.status_code != 200:
                    continue
                scanned += 1
                blob = blob_res.json()
                if blob.get("encoding") != "base64":
                    continue
                try:
                    text = base64.b64decode(blob.get("content", "")).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if term in line:
                        matches.append(f"{item.get('path')}:{line_no}: {line.strip()}")
                        if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES:
                            break

            truncation_note = (
                f" (stopped early at {_SEARCH_LITERAL_MAX_MATCHES} matches — there may be more; "
                "narrow the term if you need to see past this)" if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES
                else ""
            )
            if not matches:
                return (
                    f"No occurrences of {term!r} found — exhaustively scanned {scanned} of "
                    f"{len(candidates)} candidate files (skipped binaries/lockfiles/oversized "
                    f"files). This is a real, complete answer for the files scanned, not an "
                    f"index-based guess."
                )
            return (
                f"Exhaustively scanned {scanned} of {len(candidates)} candidate files — "
                f"{len(matches)} matching line(s){truncation_note}:\n" + "\n".join(matches)
            )

        def _run_tests(test_commands: str, branch: str = None):
            # Real CI execution, not reasoning about whether code would work — dispatches this
            # repo's own .github/workflows/patchy-tests.yml and blocks (this whole call runs via
            # asyncio.to_thread, same as every other GitHub action here) until it actually
            # finishes, up to CI_TEST_RUN_MAX_WAIT_SECONDS.
            return run_repo_tests(
                repo, branch or default_branch, test_commands or "", headers, api_base,
                max_wait_seconds=CI_TEST_RUN_MAX_WAIT_SECONDS,
            )

        def _run_snippet(code: str, branch: str = None):
            # Tier 1 real-execution path (docs/coding-agent-roadmap.md) — actually imports and
            # runs a proposed function/fix against this repo's real installed dependencies in an
            # ephemeral, secret-free CI runner, instead of just reasoning about whether it would
            # work. Same dispatch/poll machinery as run_repo_tests, same asyncio.to_thread caller.
            return run_python_snippet(
                repo, branch or default_branch, code or "", headers, api_base,
                max_wait_seconds=CI_TEST_RUN_MAX_WAIT_SECONDS,
            )

        # --- dynamic, per-user action menu — non-admins never even see run_mongo_query/run_repo_tests ---
        menu_lines = []
        if is_admin:
            menu_lines.append(
                "- run_mongo_query — args: code (a string of executable PyMongo code that assigns "
                "its output to a variable named result; wrap cursor operations like find()/aggregate() "
                "in list(...); for text-field matching prefer a MongoDB regex with case-insensitive "
                "options over strict equality)"
            )
            menu_lines.append(
                "- run_repo_tests — args: test_commands (one or more lines, each exactly "
                "'pytest <path>[::TestName::test_name]' — no shell operators, this is validated "
                "and will be rejected otherwise), branch (optional, defaults to the resolved "
                "default branch); actually dispatches this repo's real CI test workflow and waits "
                "for a REAL pass/fail result — genuine verified ground truth, not something you "
                "reason about or assume. Slower than every other action (can take a couple of "
                "minutes) since it's really running the test suite — use it to verify a fix or "
                "change actually works before telling the user it does, not for routine lookups"
            )
            menu_lines.append(
                "- run_snippet — args: code (a short, self-contained Python script — real "
                "imports from this repo's actual modules are allowed, unlike run_python's "
                "stdlib-only sandbox), branch (optional, defaults to the resolved default "
                "branch); actually imports and runs the snippet against this repo's real "
                "installed dependencies in the same CI runner run_repo_tests uses, and returns "
                "what it actually printed (or the real traceback if it raised) — genuine "
                "verified behavior, not a guess about whether an import/signature/return value "
                "is correct. Use this to check that a function you're about to propose actually "
                "works — before presenting it as working code. The snippet MUST import and call "
                "the real function/module you are proposing or verifying, using its actual path "
                "in this repo (the one you already read via read_repo_file/search_code) — a fresh, "
                "hand-rolled stand-in that reimplements the idea instead of importing the real "
                "code proves nothing about whether your actual change works, it only proves your "
                "stand-in works. Slower than every other action except run_repo_tests (real CI "
                "dispatch, not instant) — don't reach for it on a routine lookup, only when "
                "actually verifying proposed code works"
            )
        menu_lines.extend([
            "- list_repo_tree — no args; lists every file path in the repo",
            "- read_repo_file — args: path (relative file path within the repo), start_line "
            "(optional, 1-indexed — jump straight to this line instead of reading from the top), "
            "line_count (optional, defaults to 150 — how many lines to show from start_line). A "
            "large file's response gets truncated with a list of its top-level function/class "
            "names and line numbers when read without start_line — use that list to jump straight "
            "to the one you need on your next call, rather than assuming the truncated snippet is "
            "the whole file or guessing what the rest contains",
            "- search_code — args: query (a function/class/variable name or exact string); finds "
            "every file in the repo that references it — use this to find what calls, imports, "
            "or otherwise connects to the file/function you're already looking at. It only matches "
            "exact tokens, so it will find nothing for a colloquial/descriptive name that doesn't "
            "literally appear in the code (e.g. \"navbar\" when the file is menu-navigator.tsx) — "
            "use find_file for that case instead, not a second differently-worded search_code call",
            "- find_file — args: query (a colloquial, descriptive, or approximate name — not "
            "necessarily an exact identifier); fuzzy-matches it against every real file path in "
            "the repo and returns the closest ones with a similarity score. Use this when the user "
            "names something by what it does or looks like rather than its exact identifier, or "
            "when search_code/read_repo_file already came back empty for a guessed name — it finds "
            "near matches (menu-navigator.tsx from a query of \"navbar\") that an exact-token search "
            "cannot",
            "- trace_symbol — args: symbol (an exact function/variable/field/prop name — same "
            "shape as search_code's query); like search_code, but sorts every real hit into "
            "WRITE/DECIDE sites (an assignment, a React state setter call, a def/class that IS "
            "this symbol, a function returning it) versus READ/PASS-THROUGH sites (everything "
            "else — a read, a prop being consumed, a log line). Use this instead of search_code "
            "when tracing WHERE a value actually comes from, not just everywhere it's mentioned — "
            "it tells you which hits are worth opening first, instead of reading every hit by "
            "hand to figure that out one at a time. Still only matches exact tokens like "
            "search_code — for a colloquial name, use find_file first to find the real "
            "identifier, then trace_symbol on that",
            "- search_literal — args: term (an exact string/identifier — e.g. an env var or "
            "config key name); unlike search_code (which queries GitHub's search index and is "
            "NOT guaranteed complete — capped results, indexing lag), this actually fetches "
            "and greps real file content across the whole repo, so it is a guaranteed-complete "
            "answer for the files it scans. Slower than search_code (one request per candidate "
            "file) — use search_code first, and reach for this specifically when you are about "
            "to state or rely on 'every place X is used/read' being complete, such as before "
            "proposing a cross-file refactor plan",
            "- diff_branches — args: base (branch name), head (branch name)",
            "- list_commits — args: branch (branch name), limit (max number of commits, integer)",
            "- web_search — args: query (the exact search query string to run)",
            "- browser_navigate — args: url (a fully-qualified http(s) URL); loads it in a real "
            "headless browser and returns its title/final URL — call this before any other "
            "browser_* action",
            "- browser_read_text — no args; returns the visible text of the CURRENTLY loaded "
            "page — the cheap way to see what a live page actually says; prefer this over "
            "browser_screenshot unless the visual appearance itself matters",
            "- browser_click — args: text (the visible label of the button/link to click) on "
            "the CURRENTLY loaded page — use browser_read_text first if unsure what's clickable",
            "- browser_type — args: label (label or placeholder identifying the input), value "
            "(text to type into it), submit (true/false — press Enter afterward)",
            "- browser_screenshot — no args; describes what the CURRENTLY loaded page visually "
            "looks like (layout, colors, prominent UI) — slower than browser_read_text (one "
            "extra AI vision call), use only when appearance itself is what's being asked about",
            "- run_python — args: code (a small, self-contained Python snippet; print(...) "
            "whatever you need to see — no filesystem, network, or subprocess access is "
            "available, and only these stdlib modules can be imported: "
            f"{', '.join(sorted(SAFE_IMPORT_ALLOWLIST))}. Use this for calculations, data "
            "shaping, or checking your own logic — not for anything requiring I/O.)",
        ])
        actions_menu = "\n".join(menu_lines)
        prompt_template = TOOL_AGENT_PROMPT.replace("{actions_menu}", actions_menu.replace("{", "{{").replace("}", "}}"))
        prompt_template = prompt_template.replace("{history}", formatted_history.replace("{", "{{").replace("}", "}}"))
        prompt_template = prompt_template.replace("{batchable_actions}", ", ".join(sorted(TOOL_AGENT_BATCHABLE_ACTIONS)))

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
            if tool_action == "run_repo_tests":
                if not is_admin:
                    return "ERROR: not authorized for this action"
                return await asyncio.to_thread(_run_tests, args.get("test_commands"), args.get("branch"))
            if tool_action == "run_snippet":
                if not is_admin:
                    return "ERROR: not authorized for this action"
                return await asyncio.to_thread(_run_snippet, args.get("code"), args.get("branch"))
            if tool_action == "web_search":
                query = args.get("query") or msg
                search = DuckDuckGoSearchAPIWrapper()
                results = await asyncio.to_thread(search.results, query, max_results=3)
                return [r for r in results if isinstance(r, dict)] if results else []
            if tool_action in {
                "browser_navigate", "browser_read_text", "browser_click", "browser_type", "browser_screenshot"
            }:
                try:
                    session = await _get_browser_session()
                except RuntimeError as e:
                    return f"ERROR: {e}"
                if tool_action == "browser_navigate":
                    result = await browser_navigate(session, args.get("url"))
                    # Fires once, the moment a LiveURL first becomes available — lets the
                    # frontend embed a real-time (view-only) watch link in the chat bubble,
                    # via the exact same custom-event pipe trace_detail already uses.
                    if session.live_url and not browser_session_holder["live_view_emitted"]:
                        browser_session_holder["live_view_emitted"] = True
                        await safe_emit_event("browser_live_view", {"url": session.live_url})
                    return result
                if tool_action == "browser_read_text":
                    return await browser_read_text(session)
                if tool_action == "browser_click":
                    return await browser_click(session, args.get("text"))
                if tool_action == "browser_type":
                    return await browser_type(session, args.get("label"), args.get("value"), bool(args.get("submit")))
                return await browser_screenshot(session, lite_llm)
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
                    return _read_file(args.get("path"), args.get("start_line"), args.get("line_count"))
                if tool_action == "search_code":
                    return _search_code(args.get("query"))
                if tool_action == "find_file":
                    return _find_file(args.get("query"))
                if tool_action == "trace_symbol":
                    return _trace_symbol(args.get("symbol"))
                if tool_action == "search_literal":
                    return _search_literal(args.get("term"))
                if tool_action == "diff_branches":
                    return _diff_branches(args.get("base"), args.get("head"))
                if tool_action == "list_commits":
                    return _list_commits(args.get("branch"), args.get("limit"))
                return f"ERROR: unrecognized tool_action '{tool_action}'"

            return await asyncio.to_thread(_dispatch_github)

        is_audit_task = _is_audit_style_task(latest_message_content)
        deep_thinking = bool(state.get("deep_thinking")) or is_audit_task

        architecture_map = ""
        if is_audit_task:
            architecture_map = _AUDIT_TASK_SEARCH_NUDGE
            await safe_emit_event(
                "trace_detail",
                {
                    "node": "tool_agent_node",
                    "title": "Building architecture map...",
                    "detail": "Scanning internal imports across the repo before investigating.",
                }
            )
            tree_items = await asyncio.to_thread(_fetch_repo_tree_items)
            if isinstance(tree_items, list):
                architecture_map += await asyncio.to_thread(_build_architecture_map, tree_items, _fetch_file_content)

        # Outer try/finally guarantees the browser session (if any browser_* action opened one
        # this turn) is closed exactly once, regardless of which of the three exit paths below
        # runs — normal completion falls through to the finally before continuing on to build
        # final_answer; both exception handlers return from inside the inner try/except, and the
        # finally still runs before either return completes.
        try:
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
                    stuck_action_redirects=TOOL_AGENT_STUCK_ACTION_REDIRECTS,
                    capability_denial_watchlist=TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST,
                    architecture_map=architecture_map,
                    batchable_actions=TOOL_AGENT_BATCHABLE_ACTIONS,
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
                    # Real, checkpointed resume state — CLARIFICATION_CARD_MARKER in the rendered
                    # message above is now purely cosmetic (a heading users see), not part of how
                    # the next turn detects/recovers this pause; that's classify_intent reading this
                    # field directly, and reset_transient_state is the one place that deliberately
                    # never clears it. NOTE: a live browser session cannot be checkpointed here —
                    # if this pause resumes, tool_agent_node reconnects a fresh session rather
                    # than resuming the old page, since the finally below already closed it.
                    "paused_clarification": {"original_question": msg, "attempts": e.attempts},
                }
        finally:
            browser_session = browser_session_holder.get("session")
            if browser_session is not None:
                try:
                    await browser_session.close()
                except Exception:
                    logger.exception("[tool_agent_node] failed to close browser session cleanly.")

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

def create_workflow(vector_store, user_memory_vector_store=None, checkpointer=None):
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
    workflow.add_node("tool_agent_node", tool_agent_node)
    workflow.add_node("pr_summary", pr_summarizer_node)
    workflow.add_node("propose_write_node", propose_write_node)
    workflow.add_node("execute_write_node", execute_write_node)

    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges("coordinator_node", coordinator_router, _PLAN_DESTINATIONS)

    # Every plan-driven node re-enters the plan queue via plan_continue_router instead of a
    # static edge to formatter_node — this is what makes a multi-agent plan (e.g.
    # ["memory_save", "retriever"]) actually run every queued step instead of silently dropping
    # everything after the first (see plan_continue_router's docstring for the history).
    for plan_driven_node in (
        "paapp_node", "memory_save_node", "memory_recall_node", "summarizer_node",
        "tool_agent_node", "pr_summary", "propose_write_node", "execute_write_node",
        "conversational_node",
    ):
        workflow.add_conditional_edges(plan_driven_node, plan_continue_router, _PLAN_DESTINATIONS)

    workflow.add_edge("formatter_node", "generate_node")
    workflow.add_edge("retrieve_node", "grade_documents_node")
    workflow.add_edge("rewrite_query_node", "retrieve_node")

    # route_after_grading_with_plan_continuation wraps route_after_grading (unchanged, still
    # independently unit-tested) so a queued step after "retriever" in a multi-agent plan isn't
    # silently dropped the same way it used to be for every other plan-driven node above — see
    # its docstring. Also fixes the kb_open grounding gap: both non-loop outcomes now reach
    # formatter_node (the only thing that sets voice_payload.source_type from rag_mode) instead
    # of skipping straight to generate_node.
    workflow.add_conditional_edges(
        "grade_documents_node",
        route_after_grading_with_plan_continuation,
        {**_PLAN_DESTINATIONS, "rewrite_query_node": "rewrite_query_node"},
    )
    workflow.add_edge("generate_node", END)
    return workflow.compile(checkpointer=checkpointer)
