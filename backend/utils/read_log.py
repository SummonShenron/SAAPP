"""What the tool agent already looked at in this conversation: a short list of its own earlier reads and searches (places only), put in front of it on later turns.

THE PROBLEM (2026-11-08, a real thread). Earlier turns reach the next one as text only, with no tool results, so a coding turn started its investigation over: six turns in ten minutes each re-ran the same searches and reads, hit the
step limit before it proposed anything, and at one point said "the file reads did run, but their output isn't in what I have in front of me". The working notes (backend/utils/working_state.py) hold what was decided and what is open,
not where things are.

WHAT IT KEEPS. One entry per distinct read or search: the tool, the path or the search term, for a read the line range asked for, and for a search the first few places it found (`path:line`). NEVER file contents, never a result's text:
only where to look. A search term is what Sonic searched for (an identifier or an exact string it chose), cut short. Entries that repeat an earlier one replace it, so the list stays short and ordered by when each was last done; at most
MAX_ENTRIES are kept, the oldest dropped first.

HOW IT IS USED. On a later turn of the same conversation the list is shown as DATA before the first step: "you already read X lines a-b", "your search for T found these places". It says the files may have changed since (the person may
have applied an edit), so a range is read again when its exact text is needed, but a search that is listed is not run again: its places are used. No model call is made: the entries are built from the tool call and its result.

WHERE IT LIVES. `sonic_read_log`, one document per conversation (its `_id` is the person and the conversation together), expiring a week after its last change (like the working notes) and cleared with the conversation. Guests
have none. This module logs no path, term or result.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_read_log"
READ_TOOLS = frozenset({"read_repo_file", "search_code", "search_literal", "find_file", "trace_symbol", "list_repo_tree"})
SEARCH_TOOLS = frozenset({"search_code", "search_literal", "find_file", "trace_symbol"})
MAX_ENTRIES = 30
MAX_TARGET_CHARS = 90
MAX_HITS = 6
TTL_DAYS = 7
MAX_CONTEXT_CHARS = 1600

_PLACE_RE = re.compile(r"([\w./\\-]+\.[A-Za-z0-9]{1,6}):(\d+)")
_PATH_RE = re.compile(r"[\w./\\-]*[\w-]+\.[A-Za-z0-9]{1,6}\b")
_indexed = {"done": False}


def _key(username: str, session_id: str) -> str:
    return f"{username}|{session_id}"


def enabled() -> bool:
    return os.getenv("READ_LOG_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _clip(value: Any, limit: int = MAX_TARGET_CHARS) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def entry_for(tool: str, args: Any, result: Any) -> Optional[Dict[str, Any]]:
    """The entry for one tool call, or None when it is not one to keep (not a reading tool, no target, or the call failed). Pure."""
    if tool not in READ_TOOLS:
        return None
    args = args if isinstance(args, dict) else {}
    text = str(result or "")
    if text.startswith("ERROR"):
        return None
    if tool == "list_repo_tree":
        return {"tool": tool, "target": "(the repo tree)", "range": "", "hits": []}
    if tool == "read_repo_file":
        path = _clip(args.get("path"))
        if not path:
            return None
        start, count = _positive_int(args.get("start_line")), _positive_int(args.get("line_count"))
        span = f"{start}-{start + count - 1}" if start and count else (f"from {start}" if start else "")
        return {"tool": tool, "target": path, "range": span, "hits": []}
    target = _clip(args.get("term") or args.get("query") or args.get("symbol"))
    if not target:
        return None
    if tool == "find_file":
        places = list(dict.fromkeys(_PATH_RE.findall(text)))
    else:
        places = list(dict.fromkeys(f"{path}:{line}" for path, line in _PLACE_RE.findall(text)))
    return {"tool": tool, "target": target, "range": "", "hits": places[:MAX_HITS]}


def merge(entries: List[Dict[str, Any]], new: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`entries` with `new` added as the newest: an earlier entry for the same tool, target and range is replaced, the oldest are dropped past MAX_ENTRIES. Pure."""
    key = (new["tool"], new["target"], new["range"])
    kept = [e for e in entries if (e.get("tool"), e.get("target"), e.get("range")) != key]
    kept.append(new)
    return kept[-MAX_ENTRIES:]


def _line(entry: Dict[str, Any]) -> str:
    tool, target, span, hits = entry.get("tool"), entry.get("target", ""), entry.get("range", ""), entry.get("hits") or []
    if tool == "read_repo_file":
        return f"- read {target}" + (f" lines {span}" if span else "")
    if tool == "list_repo_tree":
        return "- listed the repo tree"
    verb = {"search_literal": "searched for", "search_code": "searched the code for", "trace_symbol": "traced", "find_file": "looked for a file called"}.get(tool, "searched for")
    return f"- {verb} '{target}': " + (", ".join(hits) if hits else "found nothing")


def build_context(entries: List[Dict[str, Any]]) -> str:
    """The prompt block, or "" when there is nothing: the entries oldest first, newest last, within MAX_CONTEXT_CHARS (the oldest are dropped first). Data, not instructions. Pure."""
    lines = [_line(e) for e in entries or [] if e.get("tool") in READ_TOOLS]
    if not lines:
        return ""
    head = ("WHAT YOU ALREADY LOOKED AT IN THIS CONVERSATION (your own earlier reads and searches, newest last; places only, never contents). The files may have changed since, so read a range again "
            "when you need its exact text, but do not run a search listed here again: use the places it found.\n")
    kept: List[str] = []
    size = len(head)
    for line in reversed(lines):
        if size + len(line) + 1 > MAX_CONTEXT_CHARS and kept:
            break
        kept.append(line)
        size += len(line) + 1
    return head + "\n".join(reversed(kept))


def _ensure_index(db: Any) -> None:
    if _indexed["done"]:
        return
    try:
        db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
        db[COLLECTION].create_index("username")
    except Exception:
        logger.warning("[read_log] could not create the indexes.", exc_info=True)
    _indexed["done"] = True


def record(db: Any, username: str, session_id: str, entry: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Adds one entry to this conversation's list (replacing a repeat), and moves its expiry a week on. Blocking. Never raises; False when nothing was saved."""
    try:
        if not enabled() or db is None or not username or not session_id or not entry:
            return False
        _ensure_index(db)
        moment = now or datetime.now(timezone.utc)
        key = _key(username, session_id)
        doc = db[COLLECTION].find_one({"_id": key}, {"entries": 1}) or {}
        entries = merge(list(doc.get("entries") or []), entry)
        db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"username": username, "session_id": session_id, "entries": entries, "updated_at": moment, "expires_at": moment + timedelta(days=TTL_DAYS)}},
            upsert=True,
        )
        return True
    except Exception:
        logger.warning("[read_log] could not save a read.", exc_info=True)
        return False


def load_context(username: str, session_id: str, get_db_fn: Optional[Callable[[], Any]] = None) -> str:
    """The prompt block for this conversation ("" when there is none). Blocking. Never raises."""
    try:
        if not enabled() or not username or not session_id:
            return ""
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        db = get_db_fn()
        if db is None:
            return ""
        doc = db[COLLECTION].find_one({"_id": _key(username, session_id)}, {"entries": 1}) or {}
        return build_context(list(doc.get("entries") or []))
    except Exception:
        logger.warning("[read_log] could not read the list of earlier reads.", exc_info=True)
        return ""


def clear(db: Any, username: str, session_id: str) -> bool:
    """Forgets one conversation's list. True unless the database could not be reached. Blocking. Never raises."""
    try:
        if db is None or not username or not session_id:
            return False
        db[COLLECTION].delete_many({"_id": _key(username, session_id)})
        return True
    except Exception:
        logger.warning("[read_log] could not clear a conversation's list.", exc_info=True)
        return False


def clear_all(db: Any, username: str) -> int:
    """Forgets every conversation's list for one person. Blocking. Never raises."""
    try:
        if db is None or not username:
            return 0
        return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0))
    except Exception:
        logger.warning("[read_log] could not clear a person's lists.", exc_info=True)
        return 0
