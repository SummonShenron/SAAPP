"""Function declarations for the native tool-calling engine (backend/services/native_loop.py).

The old tool agent describes its tools in a long text menu and asks the model to answer with a JSON "decision". The native engine hands
the model real function declarations instead. Two decisions keep this from duplicating anything:

- THE DESCRIPTIONS COME FROM THE LIVE MENU. tool_agent_node already builds the menu for this turn (admin-only tools only for admins,
  the folder tools only with a folder connected), and each line carries guidance learned from real failures ("search_code only matches
  exact tokens, use find_file for a colloquial name"). That text becomes the function's description, so the two engines can't drift
  apart and nothing is offered that the menu would not have offered.
- ONLY THE READ-ONLY SUBSET HAS A SCHEMA HERE. A tool with no entry in REGISTRY is simply not offered natively: the writes, the browser,
  MongoDB, the test runs, the folder edits and the approval flows stay with the old engine, which keeps all of that machinery. A turn that
  points at one of those is routed to the old engine (native_loop.eligible), never run here without the tool.

Argument names must match what tool_agent_node's dispatcher (`_act`) reads, because the native engine calls the same dispatcher.
"""
import re
from typing import Any, Dict, Iterable, List

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}


def _object(properties: Dict[str, Any], required: Iterable[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


REGISTRY: Dict[str, Dict[str, Any]] = {
    # the repository
    "list_repo_tree": _object({}),
    "read_repo_file": _object({
        "path": {"type": "string", "description": "relative file path within the repo"},
        "start_line": {"type": "integer", "description": "1-indexed line to start from"},
        "line_count": {"type": "integer", "description": "how many lines to show from start_line"},
    }, ["path"]),
    "search_code": _object({"query": {"type": "string", "description": "a function, class or variable name, or an exact string"}}, ["query"]),
    "find_file": _object({"query": {"type": "string", "description": "a colloquial or approximate file name"}}, ["query"]),
    "trace_symbol": _object({"symbol": {"type": "string", "description": "an exact function, variable, field or prop name"}}, ["symbol"]),
    "search_literal": _object({"term": {"type": "string", "description": "an exact string or identifier"}}, ["term"]),
    "diff_branches": _object({"base": _STRING, "head": _STRING}, ["base", "head"]),
    "list_commits": _object({"branch": _STRING, "limit": _INTEGER}, ["branch"]),
    "list_pull_requests": _object({
        "state": {"type": "string", "enum": ["open", "closed", "all"]},
        "limit": _INTEGER,
    }),
    # the person's own document libraries (backend/services/library_search.py)
    "search_library": _object({
        "query": {"type": "string", "description": "words to look for in the person's own documents: a name, topic, date or kind of document"},
        "max_results": {"type": "integer", "description": "default 6, max 12"},
    }, ["query"]),
    "list_library_files": _object({}),
    "read_library_file": _object({
        "name": {"type": "string", "description": "a file name exactly as list_library_files wrote it"},
        "part": {"type": "integer", "description": "which part of a long file; default 1"},
    }, ["name"]),
    # the web and a sandbox
    "web_search": _object({"query": {"type": "string", "description": "the exact search query"}}, ["query"]),
    "read_url": _object({"url": {"type": "string", "description": "an http(s) URL"}}, ["url"]),
    "run_python": _object({"code": {"type": "string", "description": "a small self-contained snippet; print what you need to see"}}, ["code"]),
    # an admin's view of the running system (only ever in an admin's menu, and only when errAgent reads are configured)
    "list_app_incidents": _object({
        "since": {"type": "string", "description": "30m, 24h or 7d"},
        "status": {"type": "string"},
        "limit": _INTEGER,
    }),
    "read_app_incident": _object({"incident_id": {"type": "string", "description": "an id from list_app_incidents"}}, ["incident_id"]),
    "read_app_logs": _object({
        "level": {"type": "string", "enum": ["info", "warn", "error"]},
        "since": {"type": "string", "description": "30m, 6h or 1d"},
        "contains": {"type": "string"},
        "request_id": {"type": "string"},
        "limit": _INTEGER,
    }),
    "check_deploy_status": _object({}),
    # the current user's own Google account (read-only; the dispatcher resolves their token and checks their granted scopes)
    "search_gmail": _object({
        "query": {"type": "string", "description": "real Gmail search syntax, e.g. 'from:someone@example.com' or 'has:attachment subject:report'"},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_gmail_message": _object({"message_id": {"type": "string", "description": "an id from a search_gmail result"}}, ["message_id"]),
    "get_gmail_attachment": _object({
        "message_id": {"type": "string", "description": "an id from a search_gmail result"},
        "attachment_id": {"type": "string", "description": "an attachment_id from a read_gmail_message result"},
    }, ["message_id", "attachment_id"]),
    "search_drive_files": _object({
        "query": {"type": "string", "description": "a real Google Drive query, e.g. \"name contains 'Report'\" or \"fullText contains 'budget'\""},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_drive_file": _object({"file_id": {"type": "string", "description": "an id from a search_drive_files result"}}, ["file_id"]),
    "list_google_calendar_events": _object({"date": {"type": "string", "description": "YYYY-MM-DD; today in the user's timezone if left out"}}),
}

_MENU_LINE_RE = re.compile(r"^-\s+([a-z_]+)\s+—\s+(.*)$", re.DOTALL)


def declarations_from_menu(menu_lines: Iterable[str]) -> List[Dict[str, Any]]:
    """Function declarations for the tools this turn's menu offers AND this engine can handle. A tool without arguments has no
    `parameters` (the API rejects an empty object schema)."""
    declarations: List[Dict[str, Any]] = []
    seen = set()
    for line in menu_lines:
        match = _MENU_LINE_RE.match(str(line).strip())
        if not match or match.group(1) not in REGISTRY or match.group(1) in seen:
            continue
        name = match.group(1)
        seen.add(name)
        declaration: Dict[str, Any] = {"name": name, "description": " ".join(match.group(2).split())}
        schema = REGISTRY[name]
        if schema["properties"]:
            declaration["parameters"] = schema
        declarations.append(declaration)
    return declarations
