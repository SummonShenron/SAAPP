import ast
import difflib
import json
import logging
import os
import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage
from backend.utils import handback_check

logger = logging.getLogger("SASS Logger")


async def safe_emit_event(name: str, data: dict):
    """Safely emit a custom event, ignoring errors if called outside an active run context."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # Safely ignored when called from fallback utilities or standalone scripts
        pass


# --- Trace panel facts (docs/trace-panel-verbose-plan.md, slice 1) ---
# Facts are MEASURED values only (tool name, shortened argument values, duration, outcome, size, retries, batch place), never
# prompts, result bodies or user text, and they go only to the requester's browser: nothing here is logged or stored.
TRACE_FACT_MAX_CHARS = 200
TRACE_FACT_MAX_LINES = 10
_TRACE_ARG_MAX_CHARS = 60
_MASKED = "•••"
# Argument names whose values are typed text, message bodies or credentials.
_MASKED_ARG_KEYS = {
    "value", "text", "body", "content", "message", "html", "subject", "code", "token", "password", "secret", "authorization",
    "api_key", "apikey", "key", "credentials", "cookie", "new_text", "old_text", "edits", "summary", "description",
    "to", "cc", "bcc", "email", "recipient", "recipients",
}
_MASKED_ARG_KEY_HINTS = ("token", "secret", "password", "passwd", "auth", "apikey", "api_key", "credential", "cookie")
_SECRET_LOOKING_RE = re.compile(
    r"(?:\b(?:ghp|gho|ghu|ghs|github_pat|sk|pk|xox[abpr]|AIza|ya29)[-_A-Za-z0-9.]{12,}|\bBearer\s+\S+|\beyJ[\w-]{10,}\.[\w-]{10,}|[A-Fa-f0-9]{32,}|[A-Za-z0-9+/_-]{40,}={0,2})"
)


def new_trace_step_id() -> str:
    """A short id that ties a trace step to the facts event that follows it."""
    return uuid.uuid4().hex[:12]


# --- Trace excerpt (docs/trace-panel-verbose-plan.md, pass 1: read_repo_file only) ---
# One capped, redacted excerpt of a read step's text, for the requester's live trace only. Never logged or saved.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|credential)\s*[:=]\s*\S+"
)


TRACE_EXCERPT_MAX_CHARS = 1000
# Files whose text is never shown in a trace: env files, keys, certificates, credential and secrets files, package-manager auth files, state files, and anything inside a secrets folder.
_SENSITIVE_PATH_RE = re.compile(
    r"(?i)(^|/)(\.env(\.[^/]*)?|id_(rsa|dsa|ecdsa|ed25519)(\.[^/]*)?|[^/]*\.(pem|key|p12|pfx|kdbx|keystore|jks|ppk|tfstate)|[^/]*credentials[^/]*|secrets?(\.[^/]*)?|"
    r"\.npmrc|\.pypirc|\.netrc|\.htpasswd|\.dockercfg)$|(^|/)\.(ssh|aws|gnupg|kube|azure)/"
)


def _redact_line(line: str) -> str:
    return _SECRET_LOOKING_RE.sub("[redacted]", _SECRET_ASSIGNMENT_RE.sub("[redacted]", line))


# A search step shows what it found, grouped by file with line numbers: at most this many files, this many matches in all and per file, and this many characters of a matching line.
TRACE_EXCERPT_MAX_FILES = 10
TRACE_EXCERPT_MAX_MATCHES = 25
TRACE_EXCERPT_MAX_PER_FILE = 5
TRACE_EXCERPT_MAX_LINE_CHARS = 160
TRACE_EXCERPT_MAX_TERM_CHARS = 120
_MATCH_LINE_RE = re.compile(r"^([^\s:][^:\n]*):(\d{1,7}): ?(.*)$")          # search_literal prints "path:line: the line"
_PATH_ONLY_RE = re.compile(r"^[\w./@+\-\[\]()~]+$")                          # search_code prints one path per line; a line with a space in it is a message, not a path


def _read_excerpt(args: dict, observation: str) -> dict | None:
    path = str(args.get("path") or "").strip().replace("\\", "/")
    if _SENSITIVE_PATH_RE.search(path):
        return None
    redacted = "\n".join(_redact_line(line) for line in observation.splitlines())
    # Measure the raw observation too: a long run that gets redacted to a short marker still means the file text was cut.
    truncated = len(observation) > TRACE_EXCERPT_MAX_CHARS or len(redacted) > TRACE_EXCERPT_MAX_CHARS
    return {"kind": "read", "path": path, "text": redacted[:TRACE_EXCERPT_MAX_CHARS], "truncated": truncated}


def _search_excerpt(tool_name: str, args: dict, observation: str) -> dict | None:
    """What a search found: the files and, for search_literal, the matching lines with their numbers; for search_code (which returns paths only) just the files. Lines from files that hold secrets are left
    out (and counted), every shown line is redacted, and what is kept is capped. None when it found nothing to show."""
    term = " ".join(str(args.get("term" if tool_name == "search_literal" else "query") or "").split())[:TRACE_EXCERPT_MAX_TERM_CHARS]
    by_file: dict = {}
    hidden: set = set()
    total_matches = 0
    for raw in observation.splitlines():
        text_line = raw.strip()
        if tool_name == "search_literal":
            found = _MATCH_LINE_RE.match(text_line)
            if not found:
                continue
            path, number, text = found.group(1).replace("\\", "/"), int(found.group(2)), found.group(3)
        else:
            if not _PATH_ONLY_RE.match(text_line) or text_line.upper().startswith("ERROR"):
                continue
            path, number, text = text_line.replace("\\", "/"), 0, ""
        if _SENSITIVE_PATH_RE.search(path):
            hidden.add(path)
            continue
        entry = by_file.setdefault(path, [])
        if number:
            total_matches += 1
            entry.append({"line": number, "text": _redact_line(text.strip())[:TRACE_EXCERPT_MAX_LINE_CHARS]})
    if not by_file:
        return None
    files, kept, cut = [], 0, False
    for path, matches in by_file.items():
        if len(files) >= TRACE_EXCERPT_MAX_FILES:
            cut = True
            break
        room = max(TRACE_EXCERPT_MAX_MATCHES - kept, 0)
        shown = matches[:min(TRACE_EXCERPT_MAX_PER_FILE, room)]
        cut = cut or len(shown) < len(matches)
        kept += len(shown)
        files.append({"path": path[:300], "matches": shown})
    return {"kind": "search", "term": term, "files": files, "total_files": len(by_file), "total_matches": total_matches or len(by_file), "hidden": len(hidden), "truncated": cut}


TRACE_EXCERPT_MAX_WEB_RESULTS = 5


def _text_excerpt(args: dict, observation: str) -> dict | None:
    """The start of a fetched page or a library file, redacted and capped to the same size as a file read. It is a "read" excerpt (the trace already draws those), labelled with the address or file name."""
    label = " ".join(str(args.get("url") or args.get("name") or "").split())[:300]
    redacted = "\n".join(_redact_line(line) for line in observation.splitlines())
    if not redacted.strip():
        return None
    truncated = len(observation) > TRACE_EXCERPT_MAX_CHARS or len(redacted) > TRACE_EXCERPT_MAX_CHARS
    return {"kind": "read", "path": label, "text": redacted[:TRACE_EXCERPT_MAX_CHARS], "truncated": truncated}


def _list_excerpt(tool_name: str, lines: list, max_lines: int) -> dict | None:
    """One result or file name per line, redacted and capped; the first max_lines only."""
    lines = [_redact_line(line.strip())[:TRACE_EXCERPT_MAX_LINE_CHARS] for line in lines if line and line.strip()]
    if not lines:
        return None
    shown = lines[:max_lines]
    return {"kind": "list", "tool": tool_name, "lines": shown, "total_lines": len(lines), "truncated": len(lines) > len(shown)}


def _web_search_excerpt(observation: str) -> dict | None:
    """A web search result reaches the loop as JSON, [{"title", "snippet", "link"}, ...], on one line: one "title - address" line per result, the first few only."""
    try:
        rows = json.loads(observation)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    lines = []
    for row in rows:
        if isinstance(row, dict) and row.get("link"):
            title = " ".join(str(row.get("title") or "").split())
            lines.append(f"{title} - {row['link']}" if title else str(row["link"]))
    return _list_excerpt("web_search", lines, TRACE_EXCERPT_MAX_WEB_RESULTS)


_LIBRARY_HIT_RE = re.compile(r"^\[\d+\] \(")                         # search_library prints "[n] (file, library, page)" above each passage


def _library_search_excerpt(observation: str) -> dict | None:
    """Where a library search found passages: the "[n] (file, ...)" line of each hit, not the passages themselves."""
    return _list_excerpt("search_library", [line for line in observation.splitlines() if _LIBRARY_HIT_RE.match(line.strip())], TRACE_EXCERPT_MAX_MATCHES)


_KB_HIT_RE = re.compile(r"^\[\d+\] \S")                           # search_my_knowledge prints "[n] Title" above each card's source line


def _knowledge_excerpt(observation: str) -> dict | None:
    """Which cards of Sonic's knowledge base a search returned: the "[n] Title" line of each, not the card text."""
    return _list_excerpt("search_my_knowledge", [line for line in observation.splitlines() if _KB_HIT_RE.match(line.strip())], TRACE_EXCERPT_MAX_MATCHES)


_NUMBERED_RE = re.compile(r"^\d+\. ")                                  # search_memory numbers each remembered item: "1. ..."


def _memory_excerpt(observation: str) -> dict | None:
    """Counts only: how many remembered items came back (the numbered lines). Never any remembered text, and nothing when memory had nothing."""
    count = sum(1 for line in observation.splitlines() if _NUMBERED_RE.match(line.strip()))
    if count == 0:
        return None
    return {"kind": "count", "tool": "search_memory", "count": count}


def build_tool_excerpt(tool_name: str, args: dict | None, observation) -> dict | None:
    """A capped, redacted excerpt of what a step found or read, for the requester's trace panel only. Never logged or saved. Reads (read_repo_file) give a piece of the file; searches (search_literal,
    search_code) give the files and matching lines. Any other tool, an error, or nothing found gives None."""
    if not isinstance(args, dict) or not isinstance(observation, str) or observation.startswith("ERROR"):
        return None
    if tool_name == "read_repo_file":
        return _read_excerpt(args, observation)
    if tool_name in ("search_literal", "search_code"):
        return _search_excerpt(tool_name, args, observation)
    if tool_name in ("read_url", "read_library_file"):
        return _text_excerpt(args, observation)
    if tool_name == "web_search":
        return _web_search_excerpt(observation)
    if tool_name == "search_library":
        return _library_search_excerpt(observation)
    if tool_name == "search_my_knowledge":
        return _knowledge_excerpt(observation)
    if tool_name == "list_library_files":
        return _list_excerpt(tool_name, [line for line in observation.splitlines() if line.startswith("- ")], TRACE_EXCERPT_MAX_MATCHES)
    if tool_name == "search_memory":
        return _memory_excerpt(observation)
    return None


def _trace_line(text) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= TRACE_FACT_MAX_CHARS else one_line[: TRACE_FACT_MAX_CHARS - 1] + "…"


def clean_trace_facts(facts) -> list:
    """Whatever the caller built: only non-empty strings, one line each, 200 characters at most, ten lines at most."""
    if not isinstance(facts, (list, tuple)):
        return []
    lines = [_trace_line(fact) for fact in facts if isinstance(fact, str) and fact.strip()]
    return lines[:TRACE_FACT_MAX_LINES]


def mask_trace_arg(key, value) -> str:
    """The display form of one tool argument: typed text and credentials are masked, everything else is shortened."""
    name = str(key).lower()
    if name in _MASKED_ARG_KEYS or any(hint in name for hint in _MASKED_ARG_KEY_HINTS):
        return _MASKED
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return f"{len(value)} item(s)"
    if isinstance(value, dict):
        return f"{len(value)} field(s)"
    text = " ".join(str(value).split())
    if _SECRET_LOOKING_RE.search(text):
        return _MASKED
    return text if len(text) <= _TRACE_ARG_MAX_CHARS else text[: _TRACE_ARG_MAX_CHARS - 1] + "…"


TRACE_SUBJECT_MAX_CHARS = 40
# Which argument names the thing a tool step is about. A tool not listed here (browser_type, the propose_* writes, run_python,
# run_mongo_query, ...) has no subject, so its step keeps the plain label.
_TRACE_SUBJECT_ARGS = {
    "search_code": ("query",),
    "search_library": ("query",),
    "search_my_knowledge": ("query",),
    "propose_kb_entry": ("url",),
    "web_search": ("query",),
    "read_repo_file": ("path", "query"),
    "find_file": ("path", "query"),
    "read_url": ("url",),
    "search_literal": ("term",),
    "mcp__maps__find_places": ("query",),
    "mcp__maps__travel_times": ("origin",),
    "trace_symbol": ("symbol",),
    "read_library_file": ("name",),
}


def _url_host_and_path(value) -> str:
    """Only where the page is, never the query string, fragment or any credentials in the address."""
    parts = urlsplit(str(value).strip() if "://" in str(value) else f"//{str(value).strip()}")
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" if host else ""


def build_trace_subject(tool_name: str, args: dict | None) -> str | None:
    """A short measured label for what a tool step is about (a search query, a file name, a page), or None.
    It goes through the same masking as the facts, and is never logged."""
    keys = _TRACE_SUBJECT_ARGS.get(str(tool_name or ""))
    if not keys or not isinstance(args, dict):
        return None
    for key in keys:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key == "url":
            value = _url_host_and_path(value)
        elif key == "path" and tool_name == "read_repo_file":
            value = value.strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        shown = mask_trace_arg(key, value)
        if not shown or shown == _MASKED:
            return None
        return shown if len(shown) <= TRACE_SUBJECT_MAX_CHARS else shown[: TRACE_SUBJECT_MAX_CHARS - 1] + "…"
    return None


def build_tool_facts(
    tool_name: str, args: dict | None, duration_seconds: float, observation, *,
    retries: int = 0, truncated: bool = False,
) -> list:
    """The measured facts for one finished tool step. The result itself is never included, only its outcome and size."""
    text = observation if isinstance(observation, str) else json.dumps(observation, default=str, indent=2) if observation is not None else ""
    # Same order of precedence the loop uses: an error is an error, and an empty result (no matches, an empty list) is not a success.
    if text.startswith("ERROR"):
        outcome = "error"
    elif _is_empty_observation(text):
        outcome = "empty (no results)"
    elif truncated:
        outcome = "truncated"
    else:
        outcome = "ok"
    facts = [f"Tool: {tool_name or '(none)'}"]
    shown = [f"{key}={mask_trace_arg(key, value)}" for key, value in (args or {}).items()]
    if shown:
        facts.append("Args: " + ", ".join(shown))
    facts.append(f"Took: {duration_seconds:.2f} s" if duration_seconds < 10 else f"Took: {duration_seconds:.1f} s")
    facts.append(f"Result: {outcome}, {len(text)} characters")
    if retries > 0:
        facts.append(f"Retries so far this turn: {retries}")
    return clean_trace_facts(facts)


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def clean_trace_excerpt(excerpt) -> dict | None:
    """What the caller built, checked again before it leaves the server. A read: a short path, text of at most TRACE_EXCERPT_MAX_CHARS characters and a yes/no for whether it was cut. A search: the term, at
    most TRACE_EXCERPT_MAX_FILES files (a path never from a secrets file) with at most TRACE_EXCERPT_MAX_MATCHES matches in all, each a line number and a line of at most TRACE_EXCERPT_MAX_LINE_CHARS characters, and
    the counts. None for anything else, or for nothing to show."""
    if not isinstance(excerpt, dict):
        return None
    if excerpt.get("kind") == "search":
        files, kept = [], 0
        for item in excerpt.get("files") if isinstance(excerpt.get("files"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or len(files) >= TRACE_EXCERPT_MAX_FILES:
                continue
            path = item["path"].strip().replace("\\", "/")[:300]
            if not path or _SENSITIVE_PATH_RE.search(path):
                continue
            matches = []
            for match in item.get("matches") if isinstance(item.get("matches"), list) else []:
                if isinstance(match, dict) and isinstance(match.get("text"), str) and _count(match.get("line")) and kept < TRACE_EXCERPT_MAX_MATCHES and len(matches) < TRACE_EXCERPT_MAX_PER_FILE:
                    matches.append({"line": match["line"], "text": match["text"][:TRACE_EXCERPT_MAX_LINE_CHARS]})
                    kept += 1
            files.append({"path": path, "matches": matches})
        if not files:
            return None
        return {"kind": "search", "term": " ".join(str(excerpt.get("term") or "").split())[:TRACE_EXCERPT_MAX_TERM_CHARS], "files": files, "total_files": max(_count(excerpt.get("total_files")), len(files)),
                "total_matches": max(_count(excerpt.get("total_matches")), kept), "hidden": _count(excerpt.get("hidden")), "truncated": bool(excerpt.get("truncated"))}
    if excerpt.get("kind") != "read" or not isinstance(excerpt.get("text"), str) or not excerpt["text"].strip():
        return None
    path = str(excerpt.get("path") or "").strip().replace("\\", "/")[:300]
    if _SENSITIVE_PATH_RE.search(path):
        return None
    return {"kind": "read", "path": path, "text": excerpt["text"][:TRACE_EXCERPT_MAX_CHARS], "truncated": bool(excerpt.get("truncated")) or len(excerpt["text"]) > TRACE_EXCERPT_MAX_CHARS}


async def emit_trace_excerpt(step_id: str | None, excerpt) -> None:
    """Sends a finished read step's excerpt to the requester's browser, next to emit_trace_facts. Never raises and never logs the excerpt."""
    cleaned = clean_trace_excerpt(excerpt)
    if not step_id or cleaned is None:
        return
    await safe_emit_event("trace_excerpt", {"step_id": str(step_id), "excerpt": cleaned})


async def emit_trace_facts(step_id: str | None, facts) -> None:
    """Sends the finished step's facts to the requester's browser, next to safe_emit_event. Never raises and never logs the facts."""
    cleaned = clean_trace_facts(facts)
    if not step_id or not cleaned:
        return
    await safe_emit_event("trace_facts", {"step_id": str(step_id), "facts": cleaned})


def format_error_payload(error_message: str) -> str:
    """Serializes an error message into an SSE-compatible JSON format."""
    error_data = {
        "status": "error",
        "message": error_message,
        "timestamp": str(import_datetime().now()) # or your preferred timestamp
    }
    return f"data: {json.dumps(error_data)}\n\n"

# Helper for the timestamp inside the utility
def import_datetime():
    return datetime

def format_final_payload(data: dict) -> str:
    """Serializes data to a server-sent event (SSE) format."""
    import json
    return f"data: {json.dumps(data)}\n\n"

def update_chat_history(history: str, role: str, message: str) -> str:
    """Appends new messages to the history transcript string."""
    entry = f"{role.upper()}: {message}\n"
    return (history or "") + entry


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


# --- tool loop helpers (backend/services/agent_workflow.py's tool_agent_node loop) ---
# New standalone logic for the ReAct loop goes here rather than as another closure inside
# agent_workflow.py — agent_workflow.py stays the graph nodes, plain functions accumulate here,
# working toward eventually splitting them the way app.py/app_utils.py already are.




# --- read_repo_file's truncation/paging knobs (moved from agent_workflow.py) ---
# A flat character-count cap on a large file (this repo has several 2000+ line files) silently
# cuts off before ever reaching a function defined further down — a real fabrication risk, since
# a model can mistake "I was given the start of the file" for "I read the file" and fill the gap
# it never saw with something plausible instead of real code. start_line lets a caller jump
# straight to a specific line once it knows where to look (from _build_definition_index below, or
# from search_code), the same way a normal editor would.
_READ_FILE_CHAR_CAP = 3500
_READ_FILE_DEFAULT_LINE_WINDOW = 150
# Always show at least this much raw content even when a large file's definition index needs
# most of the observation's budget (docs/coding-agent-roadmap.md, Section 12) — the index takes
# priority, but the snippet never shrinks to nothing.
_READ_FILE_MIN_SNIPPET_CHARS = 500
_DEFINITION_INDEX_WRAPPER = (
    "\n\n... [truncated — this file has {line_count} lines total, too long to show in full. "
    "Top-level definitions found in it:\n{index}\nCall read_repo_file again with start_line set "
    "to the one you actually need — do not assume the file's contents past this point from "
    "general knowledge of what a file like this usually contains.]"
)
_TOP_LEVEL_DEF_RE = re.compile(r'^(?:async\s+)?(def|class)\s+(\w+)')


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


# --- the tool loop's own bookkeeping helpers (moved from agent_workflow.py) ---


_EMPTY_OBSERVATION_VALUES = {
    "", "[]", "{}", "none", "null", "no results", "no results found",
    "no matches.", "no matches", "no diff context available.", "no similar file paths found.",
}


def _is_empty_observation(observation: str) -> bool:
    """An empty result (no matches, an empty list, an empty file listing) is not the same as
    "nothing exists" — it's very often a sign the query, path, repo, or collection was wrong,
    not proof of absence (this is exactly what happened with the repo-misresolution bug
    earlier: a wrong repo name didn't error, it just came back empty). Treated the same way
    an outright "ERROR: ..." is by the retry-nudge tracking in the tool loop, since both are
    "this step didn't actually get you anywhere" — the model just can't tell that from the
    text alone without this check."""
    return observation.strip().lower() in _EMPTY_OBSERVATION_VALUES



_MAX_OBSERVATION_CHARS = 4000



class _UnsafeActionRequested(Exception):
    """Raised by the tool loop when is_unsafe() flags a step, so the calling node can
    build its own tool-specific approval-required response instead of the loop guessing.

    attempts carries whatever the loop had accumulated before this step. Nothing reads it now that no approval
    resumes an investigation (propose_append_target_doc/propose_send_email never did); kept so callers
    that pass it keep working."""
    def __init__(self, decision: dict, attempts: list | None = None):
        self.decision = decision
        self.attempts = attempts or []


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
    r"\b(i (?:don'?t|do not) (?:actually |currently )?have\b|i can'?t\b|i cannot\b|i'?m not able to\b|"
    r"i (?:am|'m) (?:not able|unable) to\b|i (?:am|'m) only (?:able|allowed) to (?:read|view|search)\b|"
    r"read-only\b|i lack\b|no access to\b|i (?:don'?t|do not) have access\b)",
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


EARLIER_CONTEXT_MATCH_CHARS = 120_000  # how much of it the quote test searches
_QUOTE_MIN_LINE_CHARS = 12            # a code line shorter than this ("}", "return x") says nothing about where the code came from
_QUOTE_MATCH_RATIO = 0.6              # a code block counts as quoted when at least this share of its meaningful lines appear in the conversation
_WHITESPACE_RE = re.compile(r"\s+")


def _squash_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()



def _meaningful_code_lines(block: str) -> list:
    lines = []
    for raw in (block or "").splitlines():
        line = _squash_whitespace(raw)
        if line.startswith("```") or len(line) < _QUOTE_MIN_LINE_CHARS or not any(ch.isalnum() for ch in line):
            continue
        lines.append(line)
    return lines


def answer_code_quotes_conversation(final_answer: str, conversation_text: str) -> bool:
    """True when the answer has code blocks and every one of them is, for the most part, code that already appears in the conversation: the answer is
    quoting or discussing what the user showed, not proposing something new for the repo. A block counts when at least 60% of its meaningful lines
    (12+ characters, whitespace ignored) are found in `conversation_text`; a block with no meaningful lines proves nothing, so it does not count.
    New code, or code with an invented name in it, mostly fails the test, so the code-block checks still apply to it."""
    blocks = _CODE_BLOCK_RE.findall(final_answer or "")
    if not blocks or not (conversation_text or "").strip():
        return False
    haystack = _squash_whitespace(conversation_text[-EARLIER_CONTEXT_MATCH_CHARS:])
    for block in blocks:
        lines = _meaningful_code_lines(block)
        if not lines:
            return False
        found = sum(1 for line in lines if line in haystack)
        if found / len(lines) < _QUOTE_MATCH_RATIO:
            return False
    return True



# The grounding check above catches FALSE claims — something stated as fact that never appeared
# in attempts. Its own prompt explicitly says not to flag a reasonable synthesis or a plainly-
# labeled inference. Generic, exampleish proposed code usually isn't factually wrong about
# anything — it doesn't claim a function exists that doesn't, it doesn't misquote a value. It's
# boilerplate that would look identical whether the model had read the repo or read nothing at
# all. That's a different axis entirely — "is this actually derived from what you found" versus
# "is this true" — and no amount of tuning the existing check catches it, since by design it's
# only allowed to object to false statements. These two checks target that separate axis.
_CODE_BLOCK_RE = re.compile(r"```[\w]*\n.*?```", re.DOTALL)
# Kept intentionally narrow to the tools that actually produce real code/content from this repo —
# a successful list_repo_tree or list_commits proves the model looked around, but not that it
# actually read anything resembling an implementation to model new code on.
_CODE_PRECEDENT_RESEARCH_ACTIONS = ("read_repo_file(", "search_code(", "search_literal(", "find_file(")


def _final_answer_has_code_block(final_answer: str) -> bool:
    return bool(_CODE_BLOCK_RE.search(final_answer or ""))


def _has_successful_code_precedent_research(attempts: list) -> bool:
    """True if at least one read_repo_file/search_code/search_literal/find_file attempt this turn
    came back with a real, non-empty, non-error result — not just that one of these was CALLED,
    which says nothing about whether it actually returned anything to model new code on."""
    for attempt in attempts:
        action_desc = attempt.get("action_desc", "") or ""
        if not action_desc.startswith(_CODE_PRECEDENT_RESEARCH_ACTIONS):
            continue
        observation = attempt.get("observation", "") or ""
        if not observation.startswith("ERROR") and not _is_empty_observation(observation):
            return True
    return False






def _format_observation_for_footer(observation) -> str:
    if isinstance(observation, str):
        return observation
    return json.dumps(observation, default=str, indent=2)



def _format_attempts_steps(attempts: list) -> str:
    """Renders attempts as 'Step N — purpose / Result' blocks for display — used both in a
    completed answer's footer. Purely a display renderer."""
    return "\n\n".join(
        f"**Step {i} — {a['purpose']}:**\n```\n{a['action_desc']}\n```\n"
        f"**Result:**\n```\n{_format_observation_for_footer(a['observation'])}\n```"
        for i, a in enumerate(attempts, 1)
    )


# --- earlier messages as they go into a prompt ---

# A past answer can be long, and a tool turn's answer used to end with a transcript of every tool call and its raw result (see
# _format_attempts_steps above). Put into every later prompt unshortened, one such message was thousands of tokens, re-sent on EVERY step of
# every later tool turn. The transcript says nothing the answer above it doesn't, so it is dropped, and what is left is capped.
HISTORY_MESSAGE_MAX_CHARS = int(os.getenv("HISTORY_MESSAGE_MAX_CHARS", "1500"))
HISTORY_LAST_MESSAGE_MAX_CHARS = int(os.getenv("HISTORY_LAST_MESSAGE_MAX_CHARS", "4000"))  # the one just before the new request keeps more room
_STEP_RECEIPT_RE = re.compile(r"\n\s*\*\*Step 1\b")
_SHORTENED_MARK = "\n[... shortened ...]\n"


def compact_history_message(content, limit: int) -> str:
    """One earlier message for a prompt: without the tool-call transcript an answer ends with, and, if still longer than `limit`
    characters, its start and its end with a mark between (the end is where an answer's conclusion and any question to the user are)."""
    text = _STEP_RECEIPT_RE.split(str(content or ""), maxsplit=1)[0].strip()
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = max(limit - head - len(_SHORTENED_MARK), 0)
    return text[:head].rstrip() + _SHORTENED_MARK + (text[-tail:].lstrip() if tail else "")


def format_history(messages, *, limit=None, last_limit=None) -> str:
    """Earlier messages as "type: content" lines, each compacted (see compact_history_message). The final message in the list, the one
    nearest the new request, is allowed `last_limit` characters. Empty string for no messages."""
    limit = HISTORY_MESSAGE_MAX_CHARS if limit is None else limit
    last_limit = HISTORY_LAST_MESSAGE_MAX_CHARS if last_limit is None else last_limit
    items = list(messages or [])
    return "\n".join(
        f"{getattr(m, 'type', 'user')}: {compact_history_message(getattr(m, 'content', ''), last_limit if i == len(items) - 1 else limit)}"
        for i, m in enumerate(items)
    )


# --- find_file's fuzzy matching + trace_symbol's write/read classification (moved) ---

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


def fuzzy_query_tokens(query: str) -> set:
    """Tokens for a find_file query. A trailing file extension is dropped first (a query of
    "local_workspace.py" means the file local_workspace, and "py" would otherwise be a token no
    path ever matches, since paths are scored with their own extension already stripped)."""
    return _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", query.strip()))


def _fuzzy_path_score(query_tokens: set, path: str) -> float:
    """How well `path` (folder names and filename, extension stripped) covers the query: each
    query token is matched to its best path token — an exact match is 1.0, otherwise difflib's
    character-level ratio so near-misses (navbar/navigator, singular vs. plural) still score
    usefully — and the score is the average across ALL query tokens. Averaging matters: scoring
    by the single best token gave 1.0 to every file sharing one word with the query, so a search
    for "local_workspace.py" ranked local_start.ps1 and every other "local*" file level with the
    real local_workspace.py, burying it."""
    path_tokens = _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", path))
    if not path_tokens or not query_tokens:
        return 0.0
    per_token = []
    for query_token in query_tokens:
        best = 0.0
        for path_token in path_tokens:
            if query_token == path_token:
                best = 1.0
                break
            best = max(best, difflib.SequenceMatcher(None, query_token, path_token).ratio())
        per_token.append(best)
    return sum(per_token) / len(per_token)


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


# --- task-shape detectors (moved) ---

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


# --- README-first context for audit-style tasks ---

# A cross-file refactor/audit question benefits from the project's own documented purpose and
# conventions BEFORE diving into individual files — the same reasoning that motivated the
# architecture map below: a capability/context the model must remember to seek out on its own
# (here, thinking to read README.md before investigating) gets skipped under pressure, so this is
# injected automatically into the prompt rather than left as something it has to think to do.
# Root-level only, deliberately — a repo's own top-level README is the one doc almost guaranteed
# to describe what the whole project actually is; a docs/ subfolder's structure varies too much
# project to project to guess at without real signal, and this is meant to be a cheap, safe
# default, not an attempt to discover every doc in the repo.
_README_CANDIDATE_PATHS = ("README.md", "Readme.md", "README.rst", "README.txt", "README")
_README_MAX_CHARS = 6000


def _find_readme_path(tree_items: list) -> str | None:
    paths = {item.get("path", "") for item in tree_items}
    for candidate in _README_CANDIDATE_PATHS:
        if candidate in paths:
            return candidate
    return None


def _build_readme_context(tree_items: list, fetch_content) -> str:
    """Fetches the repo's own top-level README (if any) and returns a prompt-ready block —
    fetch_content(path) must return (content, error) like _fetch_file_content does. Empty string
    if there's no README at the root, the fetch fails, or it's blank — a missing/unreachable
    README isn't an error worth surfacing to the model, just nothing extra to add this turn."""
    readme_path = _find_readme_path(tree_items)
    if not readme_path:
        return ""
    content, error = fetch_content(readme_path)
    if error or not isinstance(content, str) or not content.strip():
        return ""
    truncated = content[:_README_MAX_CHARS]
    if len(content) > _README_MAX_CHARS:
        truncated += f"\n... [truncated — {readme_path} continues past this point]"
    return (
        f"PROJECT README ({readme_path}) — real documented context; read this before individual "
        "files so you understand what this project actually is/does before investigating "
        f"specifics:\n{truncated}\n\n"
    )


# --- architecture map (moved) ---

# An auto-injected, repo-wide internal-import graph for audit-style tasks (docs/coding-agent-
# roadmap.md, Section 4c). search_literal/trace_symbol find where a SYMBOL is referenced; this
# shows which FILES depend on which other files, which is what actually answers "what else does
# this touch" for a cross-file refactor — the exact gap that produced the fabricated per-user-
# token plan in Section 4b. Injected straight into the prompt (like `schema` already is) rather
# than offered as a new opt-in tool_action, because this project has now independently shown
# three times (Sections 0, 0b, 4b) that a capability the model must remember to reach for gets
# skipped under pressure.
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


def _import_scope_key(importing_path: str, import_str: str) -> str:
    """A RELATIVE import string alone (Python's '.utils'/'..models', or JS/TS's './api') is
    ambiguous repo-wide — it resolves relative to the IMPORTING file's own directory, so the
    identical string written in two different directories names two completely different real
    modules. Without this, _build_architecture_map's reverse map (imported_by) would key on the
    bare string alone and silently merge unrelated files from different directories into one
    entry — e.g. every file anywhere in the repo that happens to write `from .utils import x`
    bundled together, even though `.utils` inside backend/services/ and `.utils` inside
    frontend/components/ are unrelated. An ABSOLUTE import (a real top-level package name) has no
    such ambiguity — it names the same real module no matter which file imports it — so it's
    returned unprefixed, keeping the map's normal (non-relative) case exactly as readable as
    before. Deliberately NOT a real module resolver (matches _extract_python_imports' own
    documented tradeoff) — just enough disambiguation to stop two unrelated modules from
    colliding into one entry; scoping by directory, not resolving to the actual target file."""
    if not import_str.startswith("."):
        return import_str
    importer_dir = importing_path.rsplit("/", 1)[0] if "/" in importing_path else "(repo root)"
    return f"{importer_dir}::{import_str}"


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
            # See _import_scope_key's own docstring — a relative import must be scoped by the
            # importing file's directory here (the reverse map), even though the forward map
            # above keeps the raw string exactly as written in the file.
            imported_by.setdefault(_import_scope_key(path, imp), set()).add(path)

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


# ---------------------------------------------------------------------------
# Local-folder editing: recognising an edit request, and keeping stale "I can't write" replies
# from teaching the model that it can't. Everything here is mechanical on purpose — a prompt line
# saying "you can edit files" kept losing to many turns of the model's own earlier refusals.
# ---------------------------------------------------------------------------

# The longer verbs have no trailing \b on purpose: people type "updatethe trace-sidebar" (a real
# message) and still mean it. The short ones keep it so "add" doesn't match "address".
_EDIT_VERB_RE = re.compile(
    r"\b(?:change|modify|update|replace|rename|remove|delete|insert|refactor|rewrite|implement|"
    r"create|adjust|apply|tweak)|\b(?:edit|fix|add|move|swap|make|write)\b|"
    # "can you try making --bg-main a bit lighter", "changing", "bumping": the -ing forms are how people ask politely
    r"\b(?:chang|updat|replac|renam|remov|delet|insert|refactor|rewrit|implement|creat|adjust|tweak|edit|fix|add|mov|swapp|mak|writ|bump|lighten|darken|brighten|dim|shift|nudg)ing\b|"
    r"\b(?:bump|lighten|darken|brighten|dim|nudge|shift)\b|"
    # design verbs: "redesign the integrations page" was not read as a change, so the turn wrote the new layout into its answer instead of proposing it (2026-10-31)
    r"\b(?:redesign|restyle|restructure|reorgani[sz]e|declutter|simplify|collapse)(?:s|d|ing)?\b",
    re.IGNORECASE,
)
# Something code-shaped to act on. A bare verb ("make your replies shorter", "update my memory")
# isn't an edit request; a verb plus a file name / UI element / code term is.
_CODE_TARGET_RE = re.compile(
    r"\b[\w./-]+\.(?:py|tsx?|jsx?|css|json|md|html|ya?ml|toml|sh|ps1)\b|"
    r"\b(?:file|function|component|class|css|stylesheet|line|lines|code|button|header|sidebar|panel|"
    r"menu|navbar|banner|endpoint|route|test|tests|variable|import|prop|style|color|colour|label|"
    r"icon|tooltip|modal|loader|spinner|readme|hero|"
    # things people name when they describe a screen change in words ("add a timestamp to the user message bubbles"), with no file name in the message
    r"bubbles?|timestamps?|badges?|dropdowns?|checkbox(?:es)?|textarea|scrollbars?|avatars?|mascot|hearts?|animations?|layout|hover|pages?|screens?|tabs?|cards?|"
    # design values: a css custom property ("--bg-main"), tokens, a theme, and the things a stylesheet sets
    r"tokens?|theme|font|padding|margin|background|border|radius|shadow|opacity|contrast|spacing|gray|grey)\b|"
    r"(?<![\w-])--[a-z][\w-]*",
    re.IGNORECASE,
)
# "do it", "go ahead", "yes please apply that", "ugh just make the change" — a confirmation or
# command with no target of its own, which only means "edit" given what came just before.
_EDIT_CONFIRMATION_RE = re.compile(
    # "can you apply the changes?", "could you please just do it", "go ahead and apply that": a polite or run-on lead-in is still the same confirmation.
    r"^\W*(?:(?:yes|yep|yeah|ok|okay|sure|please|pls|ugh|come on|just|now)\W+)*"
    r"(?:(?:can|could|would|will) you\W+(?:(?:please|pls|just|go ahead and)\W+)*)?(?:go ahead and\W+)?"
    r"(?:do it|go ahead|apply(?: it| that| this| those| these| the change| the changes| them)?|"
    r"make (?:the|that|those|these|this) (?:changes?|edits?|updates?|fix(?:es)?)|make it so|proceed|ship it|"
    r"fix it|change it|edit it|update it|why (?:won'?t|wont|don'?t|dont) you (?:just )?(?:do|make|apply|change|edit))",
    re.IGNORECASE,
)
# A previous assistant reply that offered to make a change, or said it couldn't.
_OFFERED_OR_DENIED_EDIT_RE = re.compile(
    r"(?:would you like me to|want me to|shall i|should i|do you want me to)[^.?!]{0,80}"
    r"\b(?:make|apply|update|change|edit|modify|implement|go ahead)\b|"
    r"\b(?:copy (?:and|&) paste|paste (?:this|that|the|it|those|these)|replace (?:those|these|the) (?:two )?lines|(?:update|change|edit|replace) lines? \d+)\b|"
    r"\b(?:i (?:can'?t|cannot|don'?t have|do not have|am unable to|'m unable to|lack)|"
    r"i (?:am|'m) only able to)\b[^.]{0,200}\b(?:write|edit|apply|modify|change|read and search)\b",
    re.IGNORECASE,
)
_STALE_DENIAL_RE = re.compile(
    r"\bi (?:can'?t|cannot|don'?t have|do not have|am unable to|'m unable to|lack|am not able to|'m not able to)\b"
    r"[^.]{0,200}\b(?:write|edit|apply|modify|writing|physically|file-writing|write/edit)\b|"
    r"\bi (?:am|'m) only able to (?:read|view|search)\b|"
    r"\bonly (?:have )?the ability to read and search\b",
    re.IGNORECASE,
)


def _message_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _is_human(message) -> bool:
    return getattr(message, "type", "") == "human"


# "can you help me add some code to cope?" (2026-11-08, said right after "it just breaks my heart"): an edit verb and the bare word "code", but the person is asking for help with a feeling, not for a change to a file. It was
# read as an edit request, so the turn searched the repo for four steps, asked a stronger model, and answered with "nothing has changed". The rule is narrow on purpose: the ask must be to cope / feel better / get through
# something personal, AND the only thing "code-shaped" in the message must be the bare word "code" (nothing named: no file, no screen element). "add a retry to the upload code so it can cope with
# timeouts" and "add a journal page so I can cope" name a target or a technical object, so they stay edit requests.
_COPING_ASK_RE = re.compile(
    r"\b(?:to|help me|so (?:that )?i can|so i could)\s+(?:feel better|get through|calm down|calm myself|stop crying|sleep|deal with (?:this|it|that|the (?:pain|grief|loss|hurt)|my (?:feelings|emotions|grief|pain|anxiety)))\b"
    r"|\bcope(?!\s+with\s+(?!it\b|this\b|that\b|my\b|the (?:pain|grief|loss|hurt)\b))\b",
    re.IGNORECASE,
)
_BARE_CODE_WORDS = frozenset({"code"})                     # the only generic word in _CODE_TARGET_RE that names nothing in particular


def _only_bare_code_target(text: str) -> bool:
    """True when every code-shaped thing in `text` is a bare, unnamed word like "code": no file name, component, page, button, setting or other named target. Pure."""
    found = [m.group(0).strip().lower() for m in _CODE_TARGET_RE.finditer(text)]
    return bool(found) and all(word in _BARE_CODE_WORDS for word in found)


def _is_explicit_edit(text: str) -> bool:
    """An edit verb plus something code-shaped to act on. Not a request for help with a feeling that happens to say "some code" (see _COPING_ASK_RE)."""
    if not (_EDIT_VERB_RE.search(text) and _CODE_TARGET_RE.search(text)):
        return False
    return not (_COPING_ASK_RE.search(text) and _only_bare_code_target(text))


# A bare yes ("yes", "yes please.", "finish it!!!", "keep going") answers whatever Sonic offered last. In a coding thread that is usually "should I apply this edit?", and a yes to it was not read as an
# edit request (the confirmation phrases above need "do it", "go ahead", "apply"...), so the turn was not required to call propose_local_edits and spent its steps reading again (2026-10-28 session).
_BARE_YES_RE = re.compile(
    r"^\W*(?:(?:yes|yep|yeah|yup|ok|okay|sure|please|do it|go ahead|go on|keep going|carry on|finish it|finish up|finish|just|now|come on|then|and|all right|alright)\W*)+$",
    re.IGNORECASE,
)
BARE_YES_MAX_WORDS = 6
# An offer to make a change ("Should I remove the duplicate from agent_utils.py?", "Say the word and I'll submit this file edit"): an offer phrase, then a change verb, in the last thing Sonic said.
_OFFER_TO_CHANGE_RE = re.compile(
    r"\b(?:should i|shall i|want me to|would you like me to|do you want me to|ready (?:for me )?to|say the word|i can|i'll|i will|let me know and i'll|go ahead and)\b(?:[^.?!]|\.(?=\w)){0,200}?"
    r"\b(?:apply|submit|propose|make|update|change|edit|modify|implement|remove|delete|drop|patch|fix|wire|add|rename|rewrite|replace|write|define|restore|create|move|put)\b",
    re.IGNORECASE,
)
OFFER_TAIL_CHARS = 320


def offered_a_change(previous_reply: str) -> bool:
    """Whether the end of Sonic's last reply offered to change code (an offer phrase and a change verb, with something code-shaped in the same stretch). Includes the text of its follow-up suggestion."""
    tail = str(previous_reply or "")[-OFFER_TAIL_CHARS:]
    return bool(_OFFER_TO_CHANGE_RE.search(tail) and _CODE_TARGET_RE.search(tail))


# "make a plan", "create a plan for it", "use plan_mode", "plan it": asking for a plan. With plan mode on that IS the change request the plan is for, but it has no code-shaped words of its own,
# so it was not read as one and plan mode was skipped exactly when the person asked for a plan (2026-10-28 session).
_PLAN_REQUEST_RE = re.compile(
    r"\b(?:make|create|write|draft|build|put together|come up with|give me|do|use|submit|start with|need|want)\b[^.?!\n]{0,30}\bplan(?:[_ ]mode)?\b|\bplan (?:it|this|that|the task|the work|first)\b|\bplan[_ ]mode\b",
    re.IGNORECASE,
)
PLAN_REQUEST_MAX_WORDS = 25


def looks_like_plan_request(messages) -> bool:
    """True when the latest message is a short request for a plan and the thread has (or this message has) a real request to change code. Without that ("make a plan for my week") it is not one."""
    humans = [m for m in (messages or []) if _is_human(m)]
    if not humans:
        return False
    last = _message_text(humans[-1]).strip()
    if not last or len(last.split()) > PLAN_REQUEST_MAX_WORDS or not _PLAN_REQUEST_RE.search(last):
        return False
    return _is_explicit_edit(last) or any(_is_explicit_edit(_message_text(m)) for m in humans[:-1])


def recent_user_text(latest_message: str, messages) -> str:
    """The latest message and the ONE human message before it: what decides which engine runs the turn (native_loop.needs_old_engine). One place, so the plan gate and the engine choice cannot disagree
    about which words they looked at."""
    earlier = [str(getattr(m, "content", "")) for m in [m for m in (messages or [])[:-1] if _is_human(m)][-1:]]
    return "\n".join([latest_message] + earlier)


def previous_human_texts(messages) -> list:
    """The human message before the latest one, as a one-item list (or []): what a short follow-up ("MAKE THE CHANGE") is about. Orientation searches the words of both (backend/utils/orientation.py)."""
    earlier = [_message_text(m) for m in [m for m in (messages or [])[:-1] if _is_human(m)][-1:]]
    return [text for text in earlier if text.strip()]


# A follow-up that CONTINUES a change request Sonic has not finished (2026-11-08, Jack's budget request): after "ran out of steps" and a question about which version, "it should show at the 80% mark just like the others", "come on
# just find the files" and "so find it!" were each read as a plain message, so the turn was not an edit turn: it was never offered propose_local_edits (the native loop offers it only on an edit turn), was not nudged to
# propose after reading, and spent its steps reading again, six times in ten minutes. A continuation needs ALL of: an explicit change request among the person's last few messages; Sonic's last reply left it
# unfinished (it said so) or asked something about the change; and a short message that is not a question and either answers that question (it shares a word with it) or says to get on with it (a task word).
# "I just feel so empty" after such a reply shares nothing and has no task word, so it stays a plain message; a question ("what did you change?") is never one.
_UNFINISHED_EDIT_RE = re.compile(
    r"ran out of (?:tool )?steps|out of steps|nothing (?:has )?(?:been )?(?:changed|edited)|(?:haven'?t|have not|hadn'?t|didn'?t|did not) (?:read|changed|made|proposed|edited|found|looked|opened)"
    r"|not (?:yet )?(?:changed|proposed|edited|made)|before i (?:propose|change|edit|make)|i need to know|which (?:file|version|one)|name the file|send (?:me )?the request again"
    r"|(?:paste|send me) (?:the|those|that)|the next step is",
    re.IGNORECASE,
)
_TASK_WORD_RE = re.compile(
    r"\b(?:find|locate|search|read|open|check|apply|fix|update|add|file|files|button|page|line|lines|function|code|tests?|finish|complete|wire|hook up|the task|the rest)\b"        # concrete task words only: "just", "do", "want", "keep going" are in feelings too
    r"|\d+\s*%|\b[\w-]+\.[a-z]{1,4}\b",
    re.IGNORECASE,
)
_FOLLOW_UP_TAG_RE = re.compile(r"<<<.*?>>>", re.DOTALL)
_STOP_WORDS = frozenset("that this with have what your about from they them then than there their would could should just like when where which while been being into over only some more most very much does did doing "
                        "you the and for are but not was were has had how why can will it's i'm you're don't want going need today".split())
CONTINUATION_MAX_WORDS = 25
CONTINUATION_LOOKBACK = 8


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9_']+", str(text or "").lower()) if len(w) >= 4 and w not in _STOP_WORDS}


def continues_unfinished_edit(last: str, previous_ai: str, humans: list) -> bool:
    """Whether `last` (the person's newest message) carries on a change request that is still unfinished. See the comment above. Pure."""
    if "?" in last or len(last.split()) > CONTINUATION_MAX_WORDS:
        return False
    if not any(_is_explicit_edit(_message_text(m)) for m in humans[-(CONTINUATION_LOOKBACK + 1):-1]):
        return False
    reply = _FOLLOW_UP_TAG_RE.sub("", str(previous_ai or "")).strip()
    # A question that lists options ("1. Match on reply ... 2. Judge the turn ... Which approach?") is about the change even when it names no file: the answer "try match on reply" carries the request on (2026-11-10).
    asked = reply.endswith("?") and bool(_CODE_TARGET_RE.search(reply) or _EDIT_VERB_RE.search(reply) or len(handback_check._OPTION_LINE_RE.findall(reply)) >= 2)
    unfinished = bool(_UNFINISHED_EDIT_RE.search(reply)) or handback_check.says_unfinished(reply) or reply == _SCRUBBED_DENIAL_PLACEHOLDER
    if not (asked or unfinished):
        return False
    answers_it = asked and bool(_content_words(last) & _content_words(reply[-300:]))
    # A bare yes ("yes", "yes please", "go on") to a question that offered to carry on with the change ("Should I read SonicHealth.tsx next?"): 2026-11-09, Jack's real run, Sonic stopped with most of its steps left and asked;
    # the yes shares no word with the question and has no task word, so the follow-up was not an edit turn and could read but not edit. Only when the reply BOTH says the change is unfinished and asks about carrying on: a yes to
    # "Should I run the tests to verify?" after a finished change is not a change request (test_plan_and_confirm_routing.py), and a yes after an unfinished reply that asked nothing answers nothing.
    says_yes = asked and unfinished and len(last.split()) <= BARE_YES_MAX_WORDS and bool(_BARE_YES_RE.match(last))
    return answers_it or says_yes or bool(_TASK_WORD_RE.search(last))


def looks_like_edit_request(messages) -> bool:
    """True when the user's latest message is asking for a code/file change — either directly
    (an edit verb plus a code-shaped target: "change the header text in Chat.tsx") or as a
    confirmation of one ("do it", "yes apply that", "why won't you just make the change") that
    follows an earlier edit request or an assistant reply that offered/refused one. Deliberately
    conservative on bare verbs: "make your replies shorter" and "update my memory" are not edits."""
    if not messages:
        return False
    humans = [m for m in messages if _is_human(m)]
    if not humans:
        return False
    last = _message_text(humans[-1]).strip()
    if not last:
        return False

    def explicit(text: str) -> bool:
        return _is_explicit_edit(text)

    if explicit(last):
        return True
    previous_ai = next((_message_text(m) for m in reversed(messages) if not _is_human(m)), "")
    if len(last.split()) <= BARE_YES_MAX_WORDS and _BARE_YES_RE.match(last) and offered_a_change(previous_ai):
        return True
    words = len(last.split())
    if words > 40:
        return False
    # The whole window, not just the last few messages: after several "make the change" /
    # "APPLY THE CHANGE" in a row (the real thread) the original, explicit request is further back.
    recent_explicit = any(explicit(_message_text(m)) for m in humans[:-1])
    is_confirmation = bool(_EDIT_CONFIRMATION_RE.search(last))
    # A scrubbed refusal (see scrub_stale_capability_denials) still counts as "the assistant
    # refused" — the scrub runs before this check, so the placeholder is all that's left of it.
    ai_offered_or_denied = (
        bool(_OFFERED_OR_DENIED_EDIT_RE.search(previous_ai)) or previous_ai.strip() == _SCRUBBED_DENIAL_PLACEHOLDER
        # a reply that gave the change as code is an offer to make it: "can you apply the changes?" right after it means exactly that
        or "```" in previous_ai
    )
    if is_confirmation and words <= 14 and (recent_explicit or ai_offered_or_denied):
        return True
    # A longer complaint at a refusal ("you CAN apply the change but are refusing to") right after
    # the assistant offered or refused an edit that the user had explicitly asked for.
    if ai_offered_or_denied and recent_explicit and bool(_EDIT_VERB_RE.search(last)):
        return True
    return continues_unfinished_edit(last, previous_ai, humans)


def is_stale_capability_denial(text: str) -> bool:
    """An assistant reply claiming it can't write/edit files (or can only read and search)."""
    return bool(_STALE_DENIAL_RE.search(text or ""))


_SCRUBBED_DENIAL_PLACEHOLDER = "(I hadn't made that change yet.)"


def scrub_stale_capability_denials(messages):
    """A copy of `messages` with earlier assistant replies that denied being able to edit files
    swapped for a neutral placeholder. With a local folder connected those replies are false, and a
    thread full of them teaches the model to keep saying it — the exact pattern observed. New
    message objects are built so the persisted transcript is never altered; only what the model
    sees changes."""
    scrubbed = []
    for message in messages:
        if not _is_human(message) and is_stale_capability_denial(_message_text(message)):
            scrubbed.append(AIMessage(content=_SCRUBBED_DENIAL_PLACEHOLDER))
        else:
            scrubbed.append(message)
    return scrubbed




def might_be_edit_request(messages) -> bool:
    """A wider net than looks_like_edit_request, for choosing which engine runs a turn when a local folder is connected: any edit verb or confirmation in the person's latest message.
    The native engine has no way to edit a folder, so a turn it might have to handle as an edit goes to the old engine, which does. Matching too much only costs a dearer turn;
    matching too little leaves the person with an answer that describes a change nothing can apply."""
    humans = [m for m in (messages or []) if _is_human(m)]
    if not humans:
        return False
    last = _message_text(humans[-1]).strip()
    return bool(last) and bool(_EDIT_VERB_RE.search(last) or _EDIT_CONFIRMATION_RE.search(last))
