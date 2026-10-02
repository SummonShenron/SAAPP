from __future__ import annotations
import asyncio
import base64
import os
import re
import threading
import uuid
import json
from typing import List, Any, Dict, Optional
import logging
import requests
import erragent
import urllib.parse
from functools import partial
from pathlib import Path
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from backend.components.time_storage import load_user_time
from backend.models.attachment import Attachment
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.documents import Document
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
    TOOL_AGENT_PROMPT,
    REASONER_PROMPT,
    ISSUE_DRAFT_PROMPT,
    PR_REVIEW_PROMPT,
    DRAFT_PR_PROMPT,
    DRAFT_CALENDAR_EVENT_PROMPT,
    UPDATE_CALENDAR_EVENT_PROMPT,
    DRAFT_SEND_EMAIL_PROMPT,
    MEMORY_EXTRACTION_PROMPT
)
from backend.utils.memory_utils import save_user_fact, load_user_facts, fetch_coding_preferences
from backend.utils.emotion_utils import merge_emotional_state
from backend.services.memory_search import embed_and_store_memory_chunk, retrieve_user_memory
from backend.components.time_storage import add_time_entry, TimeEntryCreate
from backend.components import taskboard
from backend.state.graph_state import GraphState, route_after_grading
from langgraph.graph import StateGraph, START, END
from backend.utils.db_utils import get_db
from backend.utils.user_settings_utils import get_user_timezone, get_user_target_doc_id
from backend.utils.google_calendar_utils import CALENDAR_LOCKED_USERS, get_connection_status as get_calendar_connection_status, has_granted_scope
from backend.services.google_calendar_oauth import GoogleCalendarOAuth, GoogleCalendarConnectionError
from backend.services.google_calendar_service import list_events_for_day, create_event, update_event, find_event_by_summary_on_day
from backend.services.google_gmail_service import search_messages, get_message_detail, get_attachment_text, send_message
from backend.services.google_docs_service import append_text as append_doc_text
from backend.services.google_drive_service import search_files as search_drive_files_fn, read_file as read_drive_file_fn
from backend.services.python_sandbox import run_python_sandboxed, SAFE_IMPORT_ALLOWLIST
from backend.services.browser_tool import (
    BrowserSession, browser_navigate, browser_read_text, browser_click, browser_type, browser_screenshot,
)
from backend.services.ci_test_runner import run_repo_tests, run_python_snippet, DEFAULT_MAX_WAIT_SECONDS as CI_TEST_RUN_MAX_WAIT_SECONDS
from backend.utils.normalize_utils import ensure_str
from backend.utils.agent_utils import (
    parse_definition_index_from_observation, find_mismatched_start_line_note,
    # ReAct-loop pure helpers moved out of this file (docs/coding-agent-roadmap.md — refactor
    # process started per user request: agent_workflow.py keeps graph nodes, agent_utils.py
    # accumulates plain functions). Imported back under their original names so every existing
    # `aw._name` reference (tests, this file's own remaining code) keeps working unchanged.
    _READ_FILE_CHAR_CAP, _READ_FILE_DEFAULT_LINE_WINDOW, _READ_FILE_MIN_SNIPPET_CHARS,
    _DEFINITION_INDEX_WRAPPER, _TOP_LEVEL_DEF_RE, _build_definition_index,
    _parse_agent_json, _EMPTY_OBSERVATION_VALUES, _is_empty_observation,
    _mentions_unresolved_truncation, _MAX_OBSERVATION_CHARS, _truncate_observation,
    _UnsafeActionRequested, _ClarificationNeeded,
    _CAPABILITY_DENIAL_RE, _DIFF_FILE_HEADER_RE, _DIFF_NEW_FILE_RE,
    _extract_diff_file_grounding_lines, _final_diff_disagrees_with_fetched_content,
    _format_observation_for_footer, _format_react_attempts, _format_attempts_steps,
    _FILE_EXTENSION_RE, _PATH_TOKEN_SPLIT_RE, _CAMEL_BOUNDARY_RE,
    _FUZZY_MATCH_CUTOFF, _FUZZY_MATCH_LIMIT, _tokenize_for_fuzzy_match, _fuzzy_path_score,
    _classify_symbol_line,
    _VISUAL_INSPECTION_RE, _mentions_visual_inspection,
    _AUDIT_TASK_RE, _is_audit_style_task, _AUDIT_TASK_SEARCH_NUDGE,
    _find_readme_path, _build_readme_context, _README_MAX_CHARS,
    _ARCH_MAP_MAX_FILES_SCANNED, _ARCH_MAP_MAX_FILE_BYTES, _ARCH_MAP_MAX_ENTRIES,
    _ARCH_MAP_PY_EXTENSION, _ARCH_MAP_JS_EXTENSIONS, _JS_IMPORT_RE,
    _extract_python_imports, _extract_js_imports, _is_internal_python_import,
    _import_scope_key, _repo_top_level_segments, _build_architecture_map,
    resolve_recent_mention, safe_emit_event,
)
from backend.services.react_loop import run_react_loop, _MAX_BATCH_SIZE
from backend.services.repo_checkout import (
    RepoCheckoutError, fetch_and_extract_checkout, cleanup_checkout,
)
from backend.utils.insight_utils import (
    # Productivity-insights analytics moved out of this file (docs/coding-agent-roadmap.md,
    # Section 13) — a self-contained module for activity_classifier_node/pattern_detector_node/
    # trend_analyzer_node/insight_generator_node, which stay here as the actual graph nodes and
    # import everything else back under its original name.
    CATEGORY_KEYWORDS, classify_text,
    detect_time_patterns, detect_task_patterns, detect_calendar_patterns,
    compute_daily_totals, compute_category_trends, compute_streaks,
    compute_task_velocity, compute_calendar_load_trends,
    generate_time_insights, generate_task_insights, generate_calendar_insights,
    llm_json_call, interpret_insight_question, run_insight_query,
    answer_top_category, answer_busiest_day, answer_productivity_window, answer_streaks,
    answer_category_trend, answer_task_aging, answer_task_velocity, answer_calendar_load,
    answer_weekday_pattern,
)


# Pinned to an explicit path rather than a bare load_dotenv() call — python-dotenv's own
# find_dotenv() switches from "walk up from this file's directory" to "walk up from
# os.getcwd()" whenever it detects a debugger attached (sys.gettrace() is non-None), which is
# exactly how local dev is normally run (VS Code/PyCharm debug launches) but never how Render
# starts the process. If the debugger's working directory isn't inside the repo, that silently
# no-ops and every os.getenv() below returns None — reproduced directly, not a guess. Resolving
# from this file's own real location on disk sidesteps CWD/debugger detection entirely.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
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
# as the specific entries. The actual threshold/message constants for this live alongside
# run_react_loop itself now (backend/services/react_loop.py), since nothing outside that loop
# needs them.

# Built after the extensive diagnostic loop in docs/coding-agent-roadmap.md (Sections 4b-4j) —
# every one of those traces burned most of a turn's step budget reading files/searching one at a
# time before ever getting to reason about them. Deliberately restricted to genuinely
# side-effect-free, independent lookups: run_mongo_query/run_repo_tests/run_snippet are writes or
# real slow CI dispatches that must never run concurrently with anything else (see _is_unsafe and
# the admin gates in _act), and every browser_* action is inherently stateful/sequential — a click
# depends on whatever page a prior navigate actually loaded, so "independent" never applies to them.
TOOL_AGENT_BATCHABLE_ACTIONS = frozenset({
    "list_repo_tree", "read_repo_file", "search_code", "find_file", "trace_symbol",
    "search_literal", "diff_branches", "list_commits", "list_pull_requests", "web_search", "run_python",
})
# The actual per-step concurrency cap (_MAX_BATCH_SIZE) lives alongside run_react_loop itself now
# (backend/services/react_loop.py) — nothing outside that loop needs it.

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

# A real production trace: a user asked about a genuinely unreachable repo and the very first
# GitHub call this loop ever makes (_get_default_branch's own repo-info fetch) hung with no
# explicit timeout — requests' own default is to wait forever on a connect that never completes,
# not fail fast. Every GitHub REST call in this file shares this one timeout instead of each
# guessing its own value; tarball downloads (repo_checkout.py) are a real exception with their
# own longer, separate timeout, since a whole-repo download is a fundamentally bigger transfer.
_GITHUB_API_TIMEOUT_SECONDS = 15

# search_code rides GitHub's hosted /search/code index — capped at 20 results per query, subject
# to indexing lag, and explicitly not guaranteed complete by GitHub's own docs. That's fine for
# "find a plausible match" but a real production trace showed it fail exactly the task this exists
# for: "find every file that reads GITHUB_TOKEN" needs a guaranteed-complete answer, not a
# best-effort one, since a plan built on a silently-partial result set is worse than one that
# admits it doesn't know. search_literal instead fetches the real file tree and greps actual
# fetched blob content — exhaustive by construction, and its blob fetches run concurrently (see
# _SEARCH_LITERAL_FETCH_CONCURRENCY) instead of one request at a time.
#
# _SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES replaces a former flat per-file byte cap. That design
# permanently excluded any single file over the cap from every future scan, no matter how much
# budget the call actually had to spare — a real production trace (docs/coding-agent-roadmap.md,
# Section 12) showed this tool confidently report "no occurrences found, exhaustively scanned N
# of N" for a symbol that genuinely existed, in a file that had quietly grown past the cap. The
# files most likely to exceed any fixed per-file cap are exactly the largest, most central,
# most-referenced ones — the ones an audit-style task is most likely to actually need — so a
# structural exclusion by size was penalizing the wrong files. This is a TOTAL bytes-scanned
# budget for the whole call instead: candidates are still considered in their natural (tree)
# order, and a file is skipped only if scanning it would push the running total over budget —
# a single huge file just consumes more of the shared budget for itself rather than being
# permanently blacklisted, and a later, smaller file that still fits still gets scanned even if
# an earlier huge one didn't fit. Any file skipped this way is reported honestly (see
# budget_note below) as "not reached this call," not silently dropped.
_SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES = 8_000_000
_SEARCH_LITERAL_MAX_FILES_SCANNED = 300
_SEARCH_LITERAL_MAX_MATCHES = 50
_SEARCH_LITERAL_FETCH_CONCURRENCY = 8
_SEARCH_LITERAL_SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".gz", ".tar",
    ".lock", ".map",
)


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
# was attached. paused_clarification, paused_code_plan and emotional_state are the deliberate
# exceptions — these are the only fields meant to survive, so they're the only transient
# GraphState fields NOT listed here.
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

    # Same mechanism as paused_clarification just above, for an approved/rejected multi-file
    # code-change plan (see propose_code_plan's _UnsafeActionRequested handling in
    # tool_agent_node) — reuses the same "resume_tool_agent" destination since tool_agent_node's
    # own resume logic is what actually branches on which paused field is present.
    if state.get("paused_code_plan"):
        logger.debug("Detected paused_code_plan in state — resuming tool_agent")
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
    # Checked BEFORE both the "schedule" -> task_paapp catch and the "calendar" -> insight catch
    # below, the same way create_pr/create_issue are checked above the generic patterns they'd
    # otherwise be swallowed by — a real Google Calendar write, not SAAPP's own internal
    # time-log/insights feature or the (deprecated) PAAPP service. Deliberately does NOT match on
    # a bare "google calendar" mention alone — "what's on my google calendar today" is a read, not
    # a write, and must not be swallowed here (it falls through to the "google calendar" ->
    # tool_agent check a few lines down instead).
    if (
        re.search(r'\b(?:schedule|add|create|book|set up)\s+(?:an?\s+)?(?:meeting|event|call|appointment)\b', msg)
        or re.search(r'\b(?:add|put)\b.{0,40}\bto\s+(?:my\s+)?(?:google\s+)?calendar\b', msg)
    ):
        return "create_calendar_event"
    if re.search(r'\b(?:move|reschedule|update|rename|change)\s+(?:my\s+)?(?:the\s+)?(?:meeting|event|call|appointment)\b', msg):
        return "update_calendar_event"
    # A plain "google calendar" mention that wasn't already caught as a create/update above is
    # almost always a read/lookup question ("what's on my google calendar today", "am I free
    # tomorrow on google calendar") — route to the general tool_agent loop, which has
    # list_google_calendar_events as one of its actions.
    if "google calendar" in msg:
        return "tool_agent"
    # Checked before the bare "gmail"/"inbox" read-fallback below, same reasoning as
    # create_pr/create_calendar_event sitting above their own generic catches — an explicit send
    # request must not be swallowed by a read-intent fallback just because it also mentions email.
    if re.search(r'\b(?:send|compose)\s+(?:an?\s+)?email\b', msg) or re.search(r'\bemail\s+[\w.+-]+@[\w.-]+', msg):
        return "send_email"
    # A bare mention of Gmail/inbox that isn't an explicit send request is a read/lookup —
    # route to tool_agent, which has search_gmail/read_gmail_message/get_gmail_attachment.
    if re.search(r'\b(?:gmail|my inbox|my email)\b', msg):
        return "tool_agent"
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

    if intent == "create_calendar_event" or flags.get("needs_create_calendar_event"):
        state["last_intent"] = "propose_write"
        state["write_action"] = "create_calendar_event"
        return {"agents": ["propose_write", "formatter"], "skip": []}

    if intent == "update_calendar_event" or flags.get("needs_update_calendar_event"):
        state["last_intent"] = "propose_write"
        state["write_action"] = "update_calendar_event"
        return {"agents": ["propose_write", "formatter"], "skip": []}

    # A send request that ALSO needs real data gathered first (e.g. "search github for the last
    # 3 PRs and email me a summary") must not draft the email straight from the user's raw
    # message — that hallucinates content, since nothing has actually been looked up yet. Route
    # those through tool_agent instead, which has its own propose_send_email action for composing
    # the send AFTER real results are in hand. Only take the fast upfront path (draft directly
    # from the message, no lookup loop) when nothing else needs gathering first.
    _needs_prior_lookup = any(
        flags.get(f) for f in (
            "needs_web_search", "needs_code_interpreter", "needs_github_search",
            "needs_pr_summary", "needs_calendar_lookup", "needs_gmail_lookup", "needs_drive_lookup",
        )
    )
    if (intent == "send_email" or flags.get("needs_send_email")) and not _needs_prior_lookup:
        state["last_intent"] = "propose_write"
        state["write_action"] = "send_email"
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
    # Mongo/GitHub/web/calendar/Gmail/Drive all fold into one multi-tool agent — any of these
    # flags routes there, and the model itself decides which tool(s) the question actually needs.
    if (
        flags.get("needs_web_search") or flags.get("needs_code_interpreter")
        or flags.get("needs_github_search") or flags.get("needs_calendar_lookup")
        or flags.get("needs_gmail_lookup") or flags.get("needs_drive_lookup")
    ):
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
            flags = None
            logger.exception("[Reasoner] LLM classification failed, using fallback rules.")

        # Pulled out of the flags dict (which stays boolean-only for routing) and merged with the
        # carried, decayed prior — a classification failure or missing key reads as neutral, which
        # leaves a still-significant prior in place rather than erasing it.
        emotion_reading = flags.pop("emotional_state", None) if isinstance(flags, dict) else None
        state["emotional_state"] = merge_emotional_state(
            state.get("emotional_state"), emotion_reading, datetime.now(timezone.utc)
        )
        logger.info("[Reasoner] Emotional state: %s", state["emotional_state"])

        if flags is None:
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
                "needs_create_calendar_event": False,
                "needs_update_calendar_event": False,
                "needs_calendar_lookup": False,
                "needs_gmail_lookup": False,
                "needs_drive_lookup": False,
                "needs_send_email": False,
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

        saved = save_user_fact(
            username, fact_text, category=category, source="explicit",
            memory_vector_store=memory_vector_store,
        )
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
                    json={"username": username, "question": f"sync event {entry_payload.activity}"},
                    timeout=10,
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
        },
        timeout=30,
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
# SHARED REACT-LOOP INFRASTRUCTURE (Mongo/GitHub/web all fold into tool_agent_node below)
# ============================================================

# Card-marker text used the same way WRITE_ACTIONS' card_marker is: classify_intent scans the
# assistant's own previous message for this exact string to know a bare-looking reply is
# actually the user answering a paused clarification, not a fresh, unrelated message.
CLARIFICATION_CARD_MARKER = "Need a bit more information to continue"


# ============================================================
# TOOL AGENT — unified Mongo + GitHub + web research loop
# ============================================================

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

        # Resuming an approved (or rejected) code-change plan: state["paused_code_plan"] (same
        # deliberate reset_transient_state exemption as paused_clarification above) carries the
        # proposed file list and the attempts already made, so approval resumes the SAME
        # investigation thread to draft the real implementation instead of starting over — see
        # propose_code_plan's _UnsafeActionRequested handling further down this function.
        paused_plan = state.get("paused_code_plan")
        if paused_plan:
            resumed_attempts = paused_plan.get("attempts", [])
            original_question = paused_plan.get("original_question", msg)
            files_desc = ", ".join(f.get("path", "?") for f in paused_plan.get("files", []))
            reply_clean = msg.lower().strip().strip("!.,")
            # Ambiguous or outright-rejecting replies both go to the "revise" branch — only a
            # clear, unambiguous approval proceeds to the expensive drafting pass.
            approved = bool(APPROVAL_PATTERN.search(reply_clean)) and not REJECTION_PATTERN.search(reply_clean)
            if approved:
                msg = (
                    f"{original_question}\n\n(You previously proposed a plan to touch: "
                    f"{files_desc}, and the user approved it. Proceed to draft the complete, real "
                    f"implementation now as your final answer — do not propose the plan again.)"
                )
            else:
                msg = (
                    f"{original_question}\n\n(You previously proposed a plan to touch: "
                    f"{files_desc}. The user did NOT approve it as written — they said: "
                    f"\"{msg}\". Revise your plan or ask a clarifying question; do not draft the "
                    f"full implementation yet.)"
                )
            state["paused_code_plan"] = None

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
            repo_res = requests.get(f"{api_base}/repos/{repo}", headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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

        # Local repo checkout (backend/services/repo_checkout.py) — turn-scoped, same lifetime
        # and cleanup pattern as browser_session_holder above. "attempted" guards against
        # retrying a failed tarball fetch on every single subsequent tool call this turn; once
        # it's been tried once (success or failure), the result is reused for the rest of the
        # turn, same as _turn_tree_cache/_turn_file_cache only ever caching successes.
        checkout_holder: dict = {"handle": None, "attempted": False}
        checkout_lock = threading.Lock()

        def _get_checkout():
            # Deliberately plain/sync, not async — every real caller (_fetch_repo_tree_items,
            # _fetch_file_content, and transitively _list_tree/_find_file/_search_literal) is
            # already a sync function invoked via asyncio.to_thread at its own call site (the
            # same established convention as _fetch_repo_tree_items's own blocking requests.get
            # today), so a blocking call here never touches the event loop. Making this async
            # instead would force list_repo_tree/find_file out of _dispatch_github's sync
            # catch-all into their own dedicated _act branches for no real benefit.
            #
            # The lock is load-bearing, not defensive: a batched "queries" step (react_loop.py)
            # runs several actions concurrently via asyncio.gather, each on its own thread via
            # asyncio.to_thread. Without serializing here, two threads can both read
            # attempted=False before either sets it, each launching its own full tarball
            # download of the same repo at once — observed in production as multiple
            # simultaneous codeload.github.com downloads saturating the connection. Holding the
            # lock across the whole fetch means concurrent callers block and reuse the one
            # result instead of each redundantly re-fetching.
            with checkout_lock:
                if checkout_holder["attempted"]:
                    return checkout_holder["handle"]
                checkout_holder["attempted"] = True
                try:
                    handle = fetch_and_extract_checkout(repo, default_branch, headers, api_base)
                except Exception:
                    # Catches RepoCheckoutError (the expected failure shape) but deliberately not
                    # narrowed to it — this is a pure speed optimization layered on top of an
                    # already-working API path, so ANY unexpected failure here (a truly malformed
                    # response, a library-level surprise) must degrade to that existing path
                    # rather than take down the whole turn.
                    logger.exception("[tool_agent_node] local checkout fetch failed — falling back to the GitHub API.")
                    return None
                # Local-mode and API-mode deliberately produce identical observation text (see
                # the contract-preservation tests in test_tool_agent_node.py), so this is the only
                # place that ever says which path actually ran — without it, a successful checkout
                # is silent and indistinguishable from the API path in the logs.
                logger.info("[tool_agent_node] local checkout ready at %s — using it for this turn's repo reads.", handle.root)
                checkout_holder["handle"] = handle
                return handle

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

        _CHECKOUT_EXCLUDE_DIR_NAMES = ("node_modules", "dist", "__pycache__")

        def _fetch_repo_tree_items():
            # Shared by _fetch_repo_paths and _search_literal — a single real fetch of every
            # blob this repo actually has (path, size, sha), so both path-only consumers and
            # content-scanning consumers work off the same real tree instead of two divergent
            # fetches. Cached per turn (see _turn_tree_cache above) — only the success case is
            # cached, so a transient failure doesn't get "stuck" for the rest of the turn.
            if "items" in _turn_tree_cache:
                return _turn_tree_cache["items"]
            checkout = _get_checkout()
            if checkout is not None:
                # sha is None here (no git blob object to reference) — nothing in local mode
                # needs it, since search_literal's local branch reads files directly instead of
                # fetching a blob by sha through the API.
                root = Path(checkout.root)
                items = [
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size": path.stat().st_size,
                        "type": "blob",
                        "sha": None,
                    }
                    for path in root.rglob("*")
                    if path.is_file()
                    and not any(exclude in path.relative_to(root).as_posix() for exclude in _CHECKOUT_EXCLUDE_DIR_NAMES)
                ]
                _turn_tree_cache["items"] = items
                return items
            tree_url = f"{api_base}/repos/{repo}/git/trees/{default_branch}?recursive=1"
            res = requests.get(tree_url, headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
            if res.status_code != 200:
                return f"ERROR: could not fetch tree ({res.status_code})"
            tree_items = res.json().get("tree", [])
            items = [
                item for item in tree_items
                if item.get("type") == "blob"
                and not any(exclude in item.get("path", "") for exclude in _CHECKOUT_EXCLUDE_DIR_NAMES)
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
            checkout = _get_checkout()
            if checkout is not None:
                # path comes straight from a model-supplied tool_action arg — the Contents API
                # this replaces was inherently safe against a "../../etc/passwd"-style path
                # (GitHub just 404s on it, never touches this server's own disk), but a local
                # file read is NOT, unless resolved-path containment is checked explicitly. Same
                # is_relative_to guard _extract_tarball_safely uses against a malicious tarball
                # member, applied here against a malicious/malformed requested path instead.
                root = Path(checkout.root).resolve()
                local_path = (root / path).resolve()
                if not local_path.is_relative_to(root):
                    return None, f"ERROR: {path} is not a valid path in this repo"
                if not local_path.is_file():
                    return None, f"ERROR: {path} not found in this repo"
                try:
                    decoded = local_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    return None, f"ERROR: could not decode {path}"
                _turn_file_cache[path] = decoded
                return decoded, None
            file_url = f"{api_base}/repos/{repo}/contents/{path}"
            res = requests.get(file_url, headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
            index = _build_definition_index(lines)
            if index:
                # A real production trace (docs/coding-agent-roadmap.md, Section 12) showed this
                # index — real, line-numbered ground truth for exactly where a symbol lives —
                # silently lose its back half. A fixed 3500-char snippet plus the full index could
                # push this whole observation past _MAX_OBSERVATION_CHARS (a later, generic cap
                # applied to every tool's output in _truncate_observation), which slices from the
                # START — meaning the index, appended at the END, was the first thing to get cut,
                # for exactly the large-file case where it matters most. The index is more
                # valuable than the raw preview once the file's already too big to show in full,
                # so it gets first claim on the budget; the snippet shrinks to fit what's left
                # instead of a fixed size regardless of how much room the index needs.
                overhead = len(f"URL: {html_url}\n") + len(_DEFINITION_INDEX_WRAPPER.format(line_count=len(lines), index="")) + len(index)
                snippet_budget = max(
                    _READ_FILE_MIN_SNIPPET_CHARS,
                    min(_READ_FILE_CHAR_CAP, _MAX_OBSERVATION_CHARS - overhead - 100),
                )
                snippet = decoded[:snippet_budget]
                index_note = _DEFINITION_INDEX_WRAPPER.format(line_count=len(lines), index=index)
            else:
                snippet = decoded[:_READ_FILE_CHAR_CAP]
                index_note = (
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
                params={"sha": branch, "per_page": limit}, timeout=_GITHUB_API_TIMEOUT_SECONDS,
            )
            if res.status_code != 200:
                return f"ERROR: could not fetch commits ({res.status_code})"
            commits = res.json()
            return "\n".join(f"{c['sha'][:7]} — {c['commit']['message'].splitlines()[0]}" for c in commits)

        def _list_pull_requests(state_filter: str, limit):
            # GitHub's real PR list, sorted by most-recently-updated — this is what "the last N
            # pull requests" actually means (list_commits covers commit history, not PRs; nothing
            # else in this menu can answer "what were the last few PRs").
            state_filter = (state_filter or "all").lower()
            if state_filter not in {"open", "closed", "all"}:
                state_filter = "all"
            try:
                limit = min(int(limit or 3), 20)
            except (TypeError, ValueError):
                limit = 3
            res = requests.get(
                f"{api_base}/repos/{repo}/pulls", headers=headers,
                params={"state": state_filter, "sort": "updated", "direction": "desc", "per_page": limit},
                timeout=_GITHUB_API_TIMEOUT_SECONDS,
            )
            if res.status_code != 200:
                return f"ERROR: could not fetch pull requests ({res.status_code})"
            prs = res.json()
            if not prs:
                return "No pull requests found."
            return "\n".join(
                f"#{pr['number']} — {pr['title']} ({'merged' if pr.get('merged_at') else pr['state']}, "
                f"by {pr.get('user', {}).get('login', 'unknown')}, updated {pr['updated_at']})"
                for pr in prs
            )

        def _code_search_items(query: str):
            # Shared by _search_code and _trace_symbol — a single real GitHub code-search call,
            # requesting text-match fragments (not just paths) so trace_symbol has real matched
            # LINES to classify, not just filenames. Returns a list of items, or an "ERROR: ..."
            # string on failure — callers only need to check isinstance(result, str).
            res = requests.get(
                f"{api_base}/search/code",
                headers={**headers, "Accept": "application/vnd.github.text-match+json"},
                params={"q": f"{query} repo:{repo}", "per_page": 20},
                timeout=_GITHUB_API_TIMEOUT_SECONDS,
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

        async def _fetch_blob(item: dict):
            # One blob fetch, offloaded to a thread so a batch of these can genuinely run
            # concurrently via asyncio.gather instead of one request at a time — the same
            # asyncio.to_thread + asyncio.gather primitive already used for the top-level
            # batched-actions path (run_react_loop's "queries" batching), pointed at this loop's
            # own blob fetches instead of the model's tool calls.
            try:
                res = await asyncio.to_thread(
                    requests.get, f"{api_base}/repos/{repo}/git/blobs/{item.get('sha')}", headers=headers
                )
            except Exception:
                return item, None
            if res.status_code != 200:
                return item, None
            return item, res.json()

        async def _search_literal(term: str):
            # Exhaustive alternative to search_code — see the module-level comment above
            # _SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES for why this exists. Fetches the real tree,
            # then actually fetches and greps candidate file content (concurrently, in capped
            # batches) instead of trusting GitHub's search index to be complete. Slower than
            # search_code even with concurrency — use search_code first; reach for this only when
            # completeness itself is what matters (e.g. "find every place this env var/config key
            # is read" before proposing a plan).
            if not term:
                return "ERROR: no term given"
            items = await asyncio.to_thread(_fetch_repo_tree_items)
            if isinstance(items, str):
                return items
            extension_ok = [
                item for item in items
                if not item.get("path", "").lower().endswith(_SEARCH_LITERAL_SKIP_EXTENSIONS)
            ][:_SEARCH_LITERAL_MAX_FILES_SCANNED]

            # Greedy first-fit against a TOTAL bytes budget, in the tree's own natural order —
            # not a sort by size. A file is skipped only if scanning it would push the running
            # total over budget; a smaller file later in the list still gets scanned even if an
            # earlier huge one didn't fit, and nothing is excluded by size alone. See the
            # constant's own comment for why this replaced a flat per-file cap.
            to_scan: list = []
            skipped_over_budget: list = []
            running_total = 0
            for item in extension_ok:
                size = item.get("size", 0)
                if running_total + size <= _SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES:
                    to_scan.append(item)
                    running_total += size
                else:
                    skipped_over_budget.append(item)

            matches = []
            scanned = 0

            def _grep_text(item: dict, text: str) -> None:
                nonlocal scanned
                scanned += 1
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if term in line:
                        matches.append(f"{item.get('path')}:{line_no}: {line.strip()}")
                        if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES:
                            break

            checkout = _get_checkout()
            if checkout is not None:
                # No concurrency/network-latency machinery needed here — local disk reads have
                # no per-file round trip to hide. One asyncio.to_thread call offloads the whole
                # blocking walk+grep, same convention as every other blocking loop in this file.
                def _scan_local():
                    nonlocal scanned
                    root = Path(checkout.root)
                    for item in to_scan:
                        if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES:
                            break
                        try:
                            text = (root / item["path"]).read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            # Same "attempted it, counts as scanned" convention as the API
                            # branch's own encoding/decode-failure paths.
                            scanned += 1
                            continue
                        _grep_text(item, text)

                await asyncio.to_thread(_scan_local)
            else:
                for batch_start in range(0, len(to_scan), _SEARCH_LITERAL_FETCH_CONCURRENCY):
                    if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES:
                        break
                    batch = to_scan[batch_start:batch_start + _SEARCH_LITERAL_FETCH_CONCURRENCY]
                    batch_results = await asyncio.gather(*(_fetch_blob(item) for item in batch))
                    for item, blob in batch_results:
                        if blob is None:
                            continue
                        if blob.get("encoding") != "base64":
                            scanned += 1
                            continue
                        try:
                            text = base64.b64decode(blob.get("content", "")).decode("utf-8", errors="ignore")
                        except Exception:
                            scanned += 1
                            continue
                        _grep_text(item, text)

            truncation_note = (
                f" (stopped early at {_SEARCH_LITERAL_MAX_MATCHES} matches — there may be more; "
                "narrow the term if you need to see past this)" if len(matches) >= _SEARCH_LITERAL_MAX_MATCHES
                else ""
            )
            budget_note = ""
            if skipped_over_budget:
                skipped_paths = sorted(item.get("path", "") for item in skipped_over_budget)
                shown = ", ".join(skipped_paths[:10])
                more = f" (+{len(skipped_paths) - 10} more)" if len(skipped_paths) > 10 else ""
                budget_note = (
                    f" WARNING: {len(skipped_over_budget)} file(s) were not reached this call "
                    f"(the {_SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES}-byte total scan budget ran out "
                    f"before reaching them — NOT a permanent exclusion, just this call's cutoff) "
                    f"and this is NOT a guaranteed-complete answer if your target could be in one "
                    f"of them: {shown}{more}. Read those directly, or re-run search_literal if "
                    f"you need them specifically covered."
                )
            if not matches:
                return (
                    f"No occurrences of {term!r} found — exhaustively scanned {scanned} of "
                    f"{len(to_scan)} candidate files (skipped binaries/lockfiles). This is a "
                    f"real, complete answer for the files scanned, not an index-based "
                    f"guess.{budget_note}"
                )
            return (
                f"Exhaustively scanned {scanned} of {len(to_scan)} candidate files — "
                f"{len(matches)} matching line(s){truncation_note}:{budget_note}\n" + "\n".join(matches)
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
            "and greps real file content across the whole repo (concurrently, not one file at a "
            "time), so it is a guaranteed-complete answer for the files it scans. Still slower "
            "than search_code — use search_code first, and reach for this specifically when you "
            "are about to state or rely on 'every place X is used/read' being complete, such as "
            "before proposing a cross-file refactor plan",
            "- diff_branches — args: base (branch name), head (branch name)",
            "- list_commits — args: branch (branch name), limit (max number of commits, integer)",
            "- list_pull_requests — args: state (optional — 'open', 'closed', or 'all', defaults "
            "to 'all'), limit (optional, max number of PRs, defaults to 3); returns the most "
            "recently-updated real pull requests on this repo ({number, title, state, author, "
            "updated_at}). Use this for 'the last N pull requests' — list_commits only covers "
            "commit history, not PRs.",
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
            "- list_google_calendar_events — args: date (YYYY-MM-DD); lists events on the "
            "CURRENT USER's own connected Google Calendar for that day. This is NOT the same "
            "thing as the user's logged time entries/tasks (that's a separate internal feature, "
            "surfaced elsewhere as insights) — use this only when the user is actually asking "
            "about their real Google Calendar. Returns an error telling the user to connect "
            "their calendar under Integrations if they haven't yet.",
            "- search_gmail — args: query (real Gmail search syntax, e.g. "
            "'has:attachment subject:report', 'from:someone@example.com'), max_results "
            "(optional, default 10); returns a short list of matching emails "
            "({id, subject, from, date, snippet}) on the CURRENT USER's own connected Gmail "
            "inbox — use read_gmail_message next on whichever result looks relevant to see its "
            "full content.",
            "- read_gmail_message — args: message_id (from a search_gmail result); returns the "
            "email's subject/from/date/body preview and a list of its attachments "
            "({filename, mime_type, attachment_id, size}). If the content you need is in an "
            "attachment (e.g. a JSON export), call get_gmail_attachment next — do not assume the "
            "content is inline in the body just because it's a short preview.",
            "- get_gmail_attachment — args: message_id, attachment_id (both from a "
            "read_gmail_message result); returns the attachment's decoded content as text "
            "(pretty-printed if it's valid JSON).",
            "- search_drive_files — args: query (a real Google Drive query string, e.g. "
            "\"name contains 'Report'\", \"fullText contains 'budget'\", "
            "\"mimeType='application/vnd.google-apps.document'\"), max_results (optional, "
            "default 10); returns matching files ({id, name, mimeType, modifiedTime}) from the "
            "CURRENT USER's own connected Google Drive.",
            "- read_drive_file — args: file_id (from a search_drive_files result); returns the "
            "file's content as text — works for Google Docs, Google Sheets (as CSV), and plain "
            "text files; returns an error for file types with no text representation (e.g. "
            "Slides, images).",
            "- propose_append_target_doc — args: content (the text you want appended — you must "
            "have already gathered/composed this yourself via other actions first, e.g. after "
            "reading and summarizing a Gmail attachment or Drive file); use the standard "
            "'purpose' field to explain why. Appends to whatever document the CURRENT USER has "
            "configured as their target document under Integrations. This ALWAYS requires the "
            "user's approval before anything is written — call it only once, with your finished "
            "content, not as a way to draft or iterate.",
            "- propose_send_email — args: to (recipient address), subject, body (the FULL email "
            "text you want sent — you must have already gathered/composed this yourself first, "
            "e.g. after searching GitHub/Gmail/Drive for the real information the email is about; "
            "never invent PRs, emails, or file contents you haven't actually looked up). Use this "
            "whenever sending the email depends on something you had to look up first — for a "
            "send request that needs no lookup at all, the app drafts it directly and you will "
            "never see this action offered. This ALWAYS requires the user's approval before "
            "anything is sent — call it only once, with your finished draft, not as a way to "
            "iterate.",
            "- propose_code_plan — args: files (a list of {\"path\", \"reason\"} objects for every "
            "existing file your answer will propose changes to), summary (one or two sentences on "
            "your overall approach). Use this INSTEAD OF 'final' whenever your answer is about to "
            "propose code changes spanning 2 or more existing files — list what you plan to touch "
            "and why, and wait for the user's confirmation before drafting the actual "
            "implementation. Do not use this for a single-file suggestion, a conceptual "
            "explanation, or code that doesn't touch this repo's real files — only when you're "
            "about to commit to a specific real multi-file change.",
        ])
        actions_menu = "\n".join(menu_lines)
        prompt_template = TOOL_AGENT_PROMPT.replace("{actions_menu}", actions_menu.replace("{", "{{").replace("}", "}}"))
        prompt_template = prompt_template.replace("{history}", formatted_history.replace("{", "{{").replace("}", "}}"))
        prompt_template = prompt_template.replace("{batchable_actions}", ", ".join(sorted(TOOL_AGENT_BATCHABLE_ACTIONS)))
        coding_preferences = fetch_coding_preferences(username)
        prompt_template = prompt_template.replace(
            "{coding_preferences}", coding_preferences.replace("{", "{{").replace("}", "}}")
        )

        def _is_unsafe(decision: dict) -> bool:
            if decision.get("tool_action") in {"propose_append_target_doc", "propose_send_email", "propose_code_plan"}:
                return True  # inherently a write — no keyword scan needed, unlike run_mongo_query
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
            if tool_action == "list_google_calendar_events":
                # Dedicated branch (not folded into _dispatch_github) — needs `username` for
                # per-user token resolution, same tier as the run_mongo_query branch above.
                if username in CALENDAR_LOCKED_USERS:
                    return "ERROR: Google Calendar is not available for this account"
                try:
                    token = await asyncio.to_thread(GoogleCalendarOAuth().get_valid_access_token, username)
                except GoogleCalendarConnectionError:
                    return "ERROR: No Google Calendar connected for this user. Connect it under Integrations first."
                date_arg = args.get("date") or datetime.now().strftime("%Y-%m-%d")
                tz = get_user_timezone(username)
                events = await asyncio.to_thread(list_events_for_day, token, date_arg, tz)
                if not events:
                    return f"No events found on the connected Google Calendar for {date_arg}."
                return "\n".join(f"- {e['start']}: {e['summary']}" for e in events)
            if tool_action in {"search_gmail", "read_gmail_message", "get_gmail_attachment"}:
                # Same tier as the calendar branch above — needs username for per-user token
                # resolution, plus a scope check distinct from "no connection at all" (see
                # has_granted_scope's docstring: a user connected before Gmail scopes were added
                # will pass get_valid_access_token but 403 on the real Gmail call).
                if username in CALENDAR_LOCKED_USERS:
                    return "ERROR: Gmail is not available for this account"
                try:
                    token = await asyncio.to_thread(GoogleCalendarOAuth().get_valid_access_token, username)
                except GoogleCalendarConnectionError:
                    return "ERROR: No Google account connected for this user. Connect it under Integrations first."
                if not has_granted_scope(username, "https://www.googleapis.com/auth/gmail.readonly"):
                    return "ERROR: Gmail access not granted. Reconnect Google under Integrations to enable it."
                if tool_action == "search_gmail":
                    results = await asyncio.to_thread(search_messages, token, args.get("query", ""), int(args.get("max_results", 10)))
                    if not results:
                        return "No matching emails found."
                    return "\n".join(f"- [{r['id']}] {r['subject']} — {r['from']} ({r['date']}): {r['snippet']}" for r in results)
                if tool_action == "read_gmail_message":
                    detail = await asyncio.to_thread(get_message_detail, token, args.get("message_id"))
                    attachments_desc = (
                        "; ".join(f"{a['filename']} (id: {a['attachment_id']}, {a['mime_type']})" for a in detail["attachments"])
                        if detail["attachments"] else "none"
                    )
                    return (
                        f"Subject: {detail['subject']}\nFrom: {detail['from']}\nDate: {detail['date']}\n"
                        f"Attachments: {attachments_desc}\n\nBody:\n{detail['body_text']}"
                    )
                return await asyncio.to_thread(get_attachment_text, token, args.get("message_id"), args.get("attachment_id"))
            if tool_action in {"search_drive_files", "read_drive_file"}:
                if username in CALENDAR_LOCKED_USERS:
                    return "ERROR: Google Drive is not available for this account"
                try:
                    token = await asyncio.to_thread(GoogleCalendarOAuth().get_valid_access_token, username)
                except GoogleCalendarConnectionError:
                    return "ERROR: No Google account connected for this user. Connect it under Integrations first."
                if not has_granted_scope(username, "https://www.googleapis.com/auth/drive"):
                    return "ERROR: Google Drive access not granted. Reconnect Google under Integrations to enable it."
                if tool_action == "search_drive_files":
                    results = await asyncio.to_thread(search_drive_files_fn, token, args.get("query", ""), int(args.get("max_results", 10)))
                    if not results:
                        return "No matching files found."
                    return "\n".join(f"- [{f['id']}] {f['name']} ({f['mimeType']}, modified {f.get('modifiedTime', '?')})" for f in results)
                return await asyncio.to_thread(read_drive_file_fn, token, args.get("file_id"))
            if tool_action == "search_literal":
                # Dedicated branch, not folded into _dispatch_github below — _search_literal is
                # itself async now (its blob fetches run concurrently via asyncio.gather), so it
                # must be awaited directly rather than run inside _dispatch_github's synchronous
                # catch-all, which is offloaded to a worker thread as a whole.
                return await _search_literal(args.get("term"))
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
                if tool_action == "diff_branches":
                    return _diff_branches(args.get("base"), args.get("head"))
                if tool_action == "list_commits":
                    return _list_commits(args.get("branch"), args.get("limit"))
                if tool_action == "list_pull_requests":
                    return _list_pull_requests(args.get("state"), args.get("limit"))
                return f"ERROR: unrecognized tool_action '{tool_action}'"

            return await asyncio.to_thread(_dispatch_github)

        is_audit_task = _is_audit_style_task(latest_message_content)
        deep_thinking = bool(state.get("deep_thinking")) or is_audit_task

        architecture_map = ""
        if is_audit_task:
            await safe_emit_event(
                "trace_detail",
                {
                    "node": "tool_agent_node",
                    "title": "Building architecture map...",
                    "detail": "Reading the project README and scanning internal imports before investigating.",
                }
            )
            tree_items = await asyncio.to_thread(_fetch_repo_tree_items)
            if isinstance(tree_items, list):
                # README first — real documented project context before the more tactical
                # import-graph/search-tool guidance below, same "inject it, don't rely on the
                # model remembering to seek it out" reasoning as everything else in this block.
                architecture_map = await asyncio.to_thread(_build_readme_context, tree_items, _fetch_file_content)
            architecture_map += _AUDIT_TASK_SEARCH_NUDGE
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
                if e.decision.get("tool_action") == "propose_append_target_doc":
                    content = (e.decision.get("args") or {}).get("content", "") or ""
                    purpose = e.decision.get("purpose", "Append to target document")
                    doc_id = get_user_target_doc_id(username)
                    if not doc_id:
                        no_doc_message = (
                            "You haven't set a target document yet — add its URL under "
                            "Integrations, then ask me again."
                        )
                        new_messages = list(state.get("messages", [])) + [AIMessage(content=no_doc_message)]
                        return {
                            **state,
                            "pending_action": None,
                            "relevance_grade": "conversational",
                            "generation": no_doc_message,
                            "content_to_format": no_doc_message,
                            "messages": new_messages,
                        }
                    summary_line = f"Ready to append this to your target document:\n\n{content}\n\n**Purpose:** {purpose}"
                    approval_message = (
                        "**Approval Required**\n\n"
                        f"{summary_line}\n\n"
                        "*Please Approve, Modify parameters, or Reject this action.*"
                    )
                    new_messages = list(state.get("messages", [])) + [AIMessage(content=approval_message)]
                    return {
                        **state,
                        "pending_action": {"action_type": "append_target_doc", "details": {"content": content, "doc_id": doc_id}},
                        "relevance_grade": "hitl_approval_required",
                        "generation": approval_message,
                        "content_to_format": approval_message,
                        "messages": new_messages,
                    }

                if e.decision.get("tool_action") == "propose_send_email":
                    email_args = e.decision.get("args") or {}
                    to = (email_args.get("to") or "").strip()
                    subject = (email_args.get("subject") or "").strip()
                    body = (email_args.get("body") or "").strip()
                    if not (to and subject and body):
                        incomplete_message = (
                            "I need a recipient, subject, and body before I can send that — "
                            "could you confirm all three?"
                        )
                        new_messages = list(state.get("messages", [])) + [AIMessage(content=incomplete_message)]
                        return {
                            **state,
                            "pending_action": None,
                            "relevance_grade": "conversational",
                            "generation": incomplete_message,
                            "content_to_format": incomplete_message,
                            "messages": new_messages,
                        }
                    if not has_granted_scope(username, "https://www.googleapis.com/auth/gmail.send"):
                        no_scope_message = (
                            "You haven't connected Gmail send access yet — head to **Integrations** "
                            "to connect or reconnect your Google account, then ask me to send this again."
                        )
                        new_messages = list(state.get("messages", [])) + [AIMessage(content=no_scope_message)]
                        return {
                            **state,
                            "pending_action": None,
                            "relevance_grade": "conversational",
                            "generation": no_scope_message,
                            "content_to_format": no_scope_message,
                            "messages": new_messages,
                        }
                    # Same card format _draft_send_email produces, so _recover_send_email's
                    # regex-based reconstruction (used if the checkpoint is reloaded mid-approval)
                    # matches this path too, without needing a parallel recovery routine.
                    summary_line = (
                        "Ready to send this email:\n"
                        f"- **To:** {to}\n"
                        f"- **Subject:** {subject}\n\n"
                        f"**Body:**\n{body}"
                    )
                    approval_message = (
                        "**Approval Required**\n\n"
                        f"{summary_line}\n\n"
                        "*Please Approve, Modify parameters, or Reject this action.*"
                    )
                    new_messages = list(state.get("messages", [])) + [AIMessage(content=approval_message)]
                    return {
                        **state,
                        "pending_action": {"action_type": "send_email", "details": {"to": to, "subject": subject, "body": body}},
                        "relevance_grade": "hitl_approval_required",
                        "generation": approval_message,
                        "content_to_format": approval_message,
                        "messages": new_messages,
                    }

                if e.decision.get("tool_action") == "propose_code_plan":
                    plan_args = e.decision.get("args") or {}
                    files = plan_args.get("files") or []
                    summary = (plan_args.get("summary") or "").strip()
                    if not files:
                        incomplete_message = (
                            "I need at least one file before proposing a plan — could you clarify "
                            "what you're trying to change?"
                        )
                        new_messages = list(state.get("messages", [])) + [AIMessage(content=incomplete_message)]
                        return {
                            **state,
                            "pending_action": None,
                            "relevance_grade": "conversational",
                            "generation": incomplete_message,
                            "content_to_format": incomplete_message,
                            "messages": new_messages,
                        }
                    file_lines = "\n".join(
                        f"- `{f.get('path', '?')}` — {f.get('reason', 'no reason given')}" for f in files
                    )
                    summary_line = (
                        f"Before I draft the full implementation, here's my plan:\n\n"
                        f"**Files I plan to touch:**\n{file_lines}\n\n**Approach:** {summary}"
                    )
                    approval_message = (
                        "**Approval Required**\n\n"
                        f"{summary_line}\n\n"
                        "*Please Approve, Modify parameters, or Reject this action.*"
                    )
                    new_messages = list(state.get("messages", [])) + [AIMessage(content=approval_message)]
                    return {
                        **state,
                        "pending_action": None,
                        "relevance_grade": "hitl_approval_required",
                        "generation": approval_message,
                        "content_to_format": approval_message,
                        "messages": new_messages,
                        "paused_code_plan": {
                            "original_question": msg, "attempts": e.attempts,
                            "files": files, "summary": summary,
                        },
                    }

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
            cleanup_checkout(checkout_holder.get("handle"))

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
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=1", headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
        if res.status_code == 200 and res.json():
            return res.json()[0].get("number")

    # 2. Handle "first / oldest / initial"
    if any(word in msg_lower for word in ["first", "oldest", "initial"]):
        logger.info(f"Fetching initial/first PR for {repo}...")
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=asc&per_page=1", headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
        check_res = requests.get(f"{api_base}/repos/{repo}/pulls/{target_idx}", headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
        if check_res.status_code == 200:
            return target_idx

        # Otherwise, fetch list by creation index (1-based index)
        res = requests.get(f"{api_base}/repos/{repo}/pulls?state=all&sort=created&direction=asc&per_page=100", headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
            files_res = requests.get(files_url, headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
    
    res = requests.get(url, headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
    res = requests.post(api_url, headers=headers, json=payload, timeout=_GITHUB_API_TIMEOUT_SECONDS)

    if res.status_code == 201:
        pr_data = res.json()
        pr_url, pr_num = pr_data.get("html_url"), pr_data.get("number")
        merge_url = f"https://api.github.com/repos/{repo}/pulls/{pr_num}/merge"
        merge_payload = {"commit_title": f"Merge pull request #{pr_num} from {head_branch}", "merge_method": "squash"}
        merge_res = requests.put(merge_url, headers=headers, json=merge_payload, timeout=_GITHUB_API_TIMEOUT_SECONDS)
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
    res = requests.post(api_url, headers=headers, json=payload, timeout=_GITHUB_API_TIMEOUT_SECONDS)

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


def _draft_create_calendar_event(state: GraphState) -> tuple[dict, str] | tuple[None, str]:
    """Unlike _draft_create_pr/_draft_create_issue, this can decline to propose anything real —
    see propose_write_node's (None, message) short-circuit — when the user has no Google Calendar
    connection yet, since there is nothing an approval card could meaningfully offer to execute."""
    username = state.get("username")
    if not get_calendar_connection_status(username).get("connected"):
        return None, (
            "You haven't connected Google Calendar yet — head to **Integrations** to connect "
            "your account, then ask me to schedule this again."
        )

    messages = state.get("messages", [])
    last_msg = messages[-1].content.strip() if messages else ""
    tz = get_user_timezone(username)
    now = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M (%A)")

    try:
        formatted_prompt = DRAFT_CALENDAR_EVENT_PROMPT.format(user_message=last_msg, timezone=tz, now=now)
        llm_response = get_chat_llm(username).invoke(formatted_prompt)
        raw_content = getattr(llm_response, "content", "")
        text_content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw_content) if isinstance(raw_content, list) else str(raw_content)
        clean_json = text_content.strip().strip("```json").strip("```").strip()
        parsed_json = json.loads(clean_json)
        summary = parsed_json["summary"]
        start_iso = parsed_json["start_iso"]
        duration_minutes = int(parsed_json.get("duration_minutes", 30))
    except Exception:
        logger.exception("Failed to parse LLM calendar event draft.")
        return None, "I couldn't figure out the event details from that — could you rephrase with a clear title and time?"

    details = {"summary": summary, "start_iso": start_iso, "duration_minutes": duration_minutes, "timezone": tz}
    summary_line = (
        "Ready to schedule this event:\n"
        f"- **Title:** {summary}\n"
        f"- **When:** {start_iso} ({tz}), {duration_minutes} minutes"
    )
    return details, summary_line


def _execute_create_calendar_event(username: str, details: dict) -> str:
    try:
        token = GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarConnectionError as error:
        return f"**Failed to schedule event:** {error}"

    try:
        created = create_event(
            token, details["summary"], details["start_iso"],
            details.get("duration_minutes", 30), details.get("timezone") or get_user_timezone(username),
        )
    except Exception as error:
        logger.exception("[execute_write_node:create_calendar_event] Google Calendar API call failed.")
        return f"**Failed to schedule event:** {error}"

    link = f" [View on Google Calendar]({created['html_link']})" if created.get("html_link") else ""
    return f"**Event scheduled:** '{created['summary']}' at {created['start']}.{link}"


def _is_complete_create_calendar_event(details: dict) -> bool:
    return bool(details.get("summary") and details.get("start_iso"))


def _recover_create_calendar_event(messages: list, repo_hint: str) -> dict | None:
    for msg in reversed(messages or []):
        content = _content_of(msg)
        if "Ready to schedule this event" not in content:
            continue
        title_match = re.search(r"-\s*\*\*Title:\*\*\s*(.+)", content)
        when_match = re.search(r"-\s*\*\*When:\*\*\s*(\S+)\s*\(([^)]+)\),\s*(\d+)\s*minutes", content)
        if title_match and when_match:
            return {
                "summary": title_match.group(1).strip(),
                "start_iso": when_match.group(1).strip(),
                "timezone": when_match.group(2).strip(),
                "duration_minutes": int(when_match.group(3)),
            }
    return None


def _draft_update_calendar_event(state: GraphState) -> tuple[dict, str] | tuple[None, str]:
    username = state.get("username")
    if not get_calendar_connection_status(username).get("connected"):
        return None, (
            "You haven't connected Google Calendar yet — head to **Integrations** to connect "
            "your account, then ask me to update this again."
        )

    messages = state.get("messages", [])
    last_msg = messages[-1].content.strip() if messages else ""
    tz = get_user_timezone(username)
    now = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M (%A)")

    try:
        formatted_prompt = UPDATE_CALENDAR_EVENT_PROMPT.format(user_message=last_msg, timezone=tz, now=now)
        llm_response = get_chat_llm(username).invoke(formatted_prompt)
        raw_content = getattr(llm_response, "content", "")
        text_content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw_content) if isinstance(raw_content, list) else str(raw_content)
        clean_json = text_content.strip().strip("```json").strip("```").strip()
        parsed_json = json.loads(clean_json)
        search_summary = parsed_json["search_summary"]
        event_date_iso = parsed_json["event_date_iso"]
    except Exception:
        logger.exception("Failed to parse LLM calendar event update draft.")
        return None, "I couldn't figure out which event to update from that — could you name the event and its date?"

    details = {"search_summary": search_summary, "event_date_iso": event_date_iso, "timezone": tz}
    if parsed_json.get("new_summary"):
        details["new_summary"] = parsed_json["new_summary"]
    if parsed_json.get("start_iso"):
        details["start_iso"] = parsed_json["start_iso"]
        details["duration_minutes"] = int(parsed_json.get("duration_minutes", 30))

    lines = ["Ready to update this calendar event:", f'- **Find:** "{search_summary}" on {event_date_iso}']
    if details.get("new_summary"):
        lines.append(f"- **Rename to:** {details['new_summary']}")
    if details.get("start_iso"):
        lines.append(f"- **Move to:** {details['start_iso']} ({tz})")
    if not details.get("new_summary") and not details.get("start_iso"):
        lines.append("- **Change:** (no changes specified — nothing will happen)")
    summary_line = "\n".join(lines)
    return details, summary_line


def _execute_update_calendar_event(username: str, details: dict) -> str:
    try:
        token = GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarConnectionError as error:
        return f"**Failed to update event:** {error}"

    tz = details.get("timezone") or get_user_timezone(username)
    try:
        event = find_event_by_summary_on_day(token, details["search_summary"], details["event_date_iso"], tz)
    except Exception as error:
        logger.exception("[execute_write_node:update_calendar_event] Google Calendar lookup failed.")
        return f"**Failed to update event:** {error}"
    if not event:
        return (
            f"**Failed to update event:** couldn't find an event matching "
            f"\"{details['search_summary']}\" on {details['event_date_iso']}."
        )

    updates = {}
    if details.get("new_summary"):
        updates["summary"] = details["new_summary"]
    if details.get("start_iso"):
        updates["start_iso"] = details["start_iso"]
        updates["duration_minutes"] = details.get("duration_minutes", 30)

    try:
        updated = update_event(token, event["id"], updates, tz)
    except Exception as error:
        logger.exception("[execute_write_node:update_calendar_event] Google Calendar update failed.")
        return f"**Failed to update event:** {error}"

    link = f" [View on Google Calendar]({updated['html_link']})" if updated.get("html_link") else ""
    return f"**Event updated:** '{updated['summary']}' now at {updated['start']}.{link}"


def _is_complete_update_calendar_event(details: dict) -> bool:
    return bool(details.get("search_summary") and details.get("event_date_iso"))


def _recover_update_calendar_event(messages: list, repo_hint: str) -> dict | None:
    for msg in reversed(messages or []):
        content = _content_of(msg)
        if "Ready to update this calendar event" not in content:
            continue
        find_match = re.search(r'\*\*Find:\*\*\s*"([^"]+)"\s*on\s*(\S+)', content)
        if not find_match:
            continue
        details = {"search_summary": find_match.group(1).strip(), "event_date_iso": find_match.group(2).strip()}
        rename_match = re.search(r"\*\*Rename to:\*\*\s*(.+)", content)
        if rename_match:
            details["new_summary"] = rename_match.group(1).strip()
        move_match = re.search(r"\*\*Move to:\*\*\s*(\S+)\s*\(([^)]+)\)", content)
        if move_match:
            details["start_iso"] = move_match.group(1).strip()
            details["timezone"] = move_match.group(2).strip()
        return details
    return None


def _execute_append_target_doc(username: str, details: dict) -> str:
    # Re-resolves doc_id fresh rather than trusting details["doc_id"] — the setting could have
    # changed between the approval card being drafted and the user actually approving it.
    doc_id = get_user_target_doc_id(username)
    if not doc_id:
        return "**Failed to append:** no target document is configured anymore. Set one under Integrations."

    try:
        token = GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarConnectionError as error:
        return f"**Failed to append:** {error}"

    if not has_granted_scope(username, "https://www.googleapis.com/auth/documents"):
        return "**Failed to append:** Docs access not granted. Reconnect Google under Integrations to enable it."

    result = append_doc_text(token, doc_id, details.get("content", ""))
    if result.startswith("ERROR"):
        return f"**Failed to append:** {result[len('ERROR: '):]}"
    return "**Appended successfully** to your target document."


def _is_complete_append_target_doc(details: dict) -> bool:
    return bool(details.get("content") and details.get("doc_id"))


def _recover_append_target_doc(messages: list, repo_hint: str) -> dict | None:
    for msg in reversed(messages or []):
        content = _content_of(msg)
        if "Ready to append this to your target document" not in content:
            continue
        match = re.search(r"Ready to append this to your target document:\n\n([\s\S]*?)\n\n\*\*Purpose:\*\*", content)
        if match:
            return {"content": match.group(1).strip(), "doc_id": None}
    return None


def _draft_send_email(state: GraphState) -> tuple[dict, str] | tuple[None, str]:
    username = state.get("username")
    if not has_granted_scope(username, "https://www.googleapis.com/auth/gmail.send"):
        return None, (
            "You haven't connected Gmail send access yet — head to **Integrations** to connect "
            "or reconnect your Google account, then ask me to send this again."
        )

    messages = state.get("messages", [])
    last_msg = messages[-1].content.strip() if messages else ""

    try:
        formatted_prompt = DRAFT_SEND_EMAIL_PROMPT.format(user_message=last_msg)
        llm_response = get_chat_llm(username).invoke(formatted_prompt)
        raw_content = getattr(llm_response, "content", "")
        text_content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw_content) if isinstance(raw_content, list) else str(raw_content)
        clean_json = text_content.strip().strip("```json").strip("```").strip()
        parsed_json = json.loads(clean_json)
        to = parsed_json["to"]
        subject = parsed_json["subject"]
        body = parsed_json["body"]
    except Exception:
        logger.exception("Failed to parse LLM email draft.")
        return None, "I couldn't figure out the email details from that — could you give me a recipient, subject, and what to say?"

    details = {"to": to, "subject": subject, "body": body}
    summary_line = (
        "Ready to send this email:\n"
        f"- **To:** {to}\n"
        f"- **Subject:** {subject}\n\n"
        f"**Body:**\n{body}"
    )
    return details, summary_line


def _execute_send_email(username: str, details: dict) -> str:
    try:
        token = GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarConnectionError as error:
        return f"**Failed to send email:** {error}"

    if not has_granted_scope(username, "https://www.googleapis.com/auth/gmail.send"):
        return "**Failed to send email:** Gmail send access not granted. Reconnect Google under Integrations to enable it."

    try:
        send_message(token, details["to"], details["subject"], details["body"])
    except Exception as error:
        logger.exception("[execute_write_node:send_email] Gmail API call failed.")
        return f"**Failed to send email:** {error}"

    return f"**Email sent** to {details['to']}."


def _is_complete_send_email(details: dict) -> bool:
    return bool(details.get("to") and details.get("subject") and details.get("body"))


def _recover_send_email(messages: list, repo_hint: str) -> dict | None:
    for msg in reversed(messages or []):
        content = _content_of(msg)
        if "Ready to send this email" not in content:
            continue
        to_match = re.search(r"-\s*\*\*To:\*\*\s*(.+)", content)
        subject_match = re.search(r"-\s*\*\*Subject:\*\*\s*(.+)", content)
        body_match = re.search(r"\*\*Body:\*\*\n([\s\S]*?)(?=\n\n\*Please|\Z)", content)
        if to_match and subject_match and body_match:
            return {
                "to": to_match.group(1).strip(),
                "subject": subject_match.group(1).strip(),
                "body": body_match.group(1).strip(),
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
    "create_calendar_event": {
        # None, not "Global_Admins" — scheduling a calendar event is personal, not an
        # admin-only capability; every non-guest user may do this on their own behalf. See
        # execute_write_node's RBAC check: a falsy required_role means no role gate at all.
        "required_role": None,
        "draft": _draft_create_calendar_event,
        "execute": _execute_create_calendar_event,
        "is_complete": _is_complete_create_calendar_event,
        "recover_from_history": _recover_create_calendar_event,
        "card_marker": "Ready to schedule this event",
    },
    "update_calendar_event": {
        "required_role": None,
        "draft": _draft_update_calendar_event,
        "execute": _execute_update_calendar_event,
        "is_complete": _is_complete_update_calendar_event,
        "recover_from_history": _recover_update_calendar_event,
        "card_marker": "Ready to update this calendar event",
    },
    "append_target_doc": {
        "required_role": None,
        # No "draft": proposed inline by tool_agent_node's own ReAct loop (same pattern as
        # run_mongo_write) — the model doesn't know what to append until it's already gathered
        # and summarized content itself via other actions.
        "draft": None,
        "execute": _execute_append_target_doc,
        "is_complete": _is_complete_append_target_doc,
        "recover_from_history": _recover_append_target_doc,
        "card_marker": "Ready to append this to your target document",
    },
    "send_email": {
        "required_role": None,
        "draft": _draft_send_email,
        "execute": _execute_send_email,
        "is_complete": _is_complete_send_email,
        "recover_from_history": _recover_send_email,
        "card_marker": "Ready to send this email",
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
        if details is None:
            # A draft can decline to propose anything real (e.g. create_calendar_event when the
            # user has no Google Calendar connection yet) — summary_line is then a plain
            # informational message, not an approval card, so no pending_action is set and the
            # generic "Approve/Modify/Reject" footer below is skipped entirely.
            new_messages = list(messages) + [AIMessage(content=summary_line)]
            return {
                **state,
                "pending_action": None,
                "relevance_grade": "conversational",
                "generation": summary_line,
                "messages": new_messages,
            }
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

        # 1. RBAC check — one site for every registered write action. required_role of None means
        # no role gate (e.g. create_calendar_event/update_calendar_event — personal actions any
        # non-guest user may take on their own behalf, not an admin-only capability).
        user_groups = load_user_directory_groups(username)
        if action["required_role"] and action["required_role"] not in user_groups:
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
