"""The files Sonic changed and the person applied, waiting to be committed (backend/services/git_commit.py).

A commit is of work the person approved. When they press Apply on one of Sonic's diff cards the browser reports it (POST /api/local-edits/{id}/decision), and the paths that proposal touched are
noted here with the size of the change they approved (lines added and removed). When a commit is approved and made, those paths are cleared.

- ONLY PATHS AND COUNTS, never file text: the text is read from the person's synced folder at commit time, so a commit holds the files as the folder has them then (and the card fingerprints them
  so a change between the card and the approval is caught).
- IN THE PERSON'S WORKSPACE FOLDER on the server, next to their synced snapshot, so it goes when they disconnect the folder or choose "forget everything", and it expires after TTL_DAYS.
- A PATH THAT NO LONGER EXISTS in the snapshot is dropped.
"""
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from backend.services import local_workspace

logger = logging.getLogger("SASS Logger")

TTL_DAYS = 14
MAX_PATHS = 200
_lock = threading.Lock()


def _ledger_path(username: str) -> Path:
    return local_workspace._workspace_dir(username) / "applied.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(username: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_ledger_path(username).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(username: str, data: Dict[str, Dict[str, Any]]) -> None:
    path = _ledger_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def record(username: Optional[str], changes: Dict[str, Tuple[int, int]], now: Optional[datetime] = None) -> int:
    """Notes that these paths were applied, with (lines added, lines removed) for each; a path applied again adds to its counts. Returns how many paths were noted. Never raises."""
    if not username or not changes:
        return 0
    try:
        moment = (now or _now()).isoformat()
        with _lock:
            data = _load(username)
            for path, (adds, dels) in changes.items():
                before = data.get(path, {})
                data[path] = {"at": moment, "additions": int(before.get("additions", 0)) + int(adds), "deletions": int(before.get("deletions", 0)) + int(dels)}
            if len(data) > MAX_PATHS:
                for old in sorted(data, key=lambda p: data[p].get("at", ""))[: len(data) - MAX_PATHS]:
                    data.pop(old)
            _save(username, data)
        return len(changes)
    except Exception:
        logger.warning("[applied changes] could not note an applied change.", exc_info=True)
        return 0


def pending(username: Optional[str], now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """{path: {"at", "additions", "deletions"}} for the applied files still waiting to be committed: not expired, and still in the synced folder."""
    if not username:
        return {}
    cutoff = (now or _now()) - timedelta(days=TTL_DAYS)
    handle = local_workspace.get_workspace_handle(username)
    if handle is None:
        return {}
    root = Path(handle.root)
    out: Dict[str, Dict[str, Any]] = {}
    for path, info in _load(username).items():
        try:
            when = datetime.fromisoformat(str(info.get("at")))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= cutoff and (root / path).is_file():
            out[path] = info
    return out


def clear(username: Optional[str], paths: Iterable[str]) -> None:
    if not username:
        return
    try:
        with _lock:
            data = _load(username)
            for path in paths:
                data.pop(path, None)
            _save(username, data)
    except Exception:
        logger.warning("[applied changes] could not clear committed paths.", exc_info=True)


def read_texts(username: Optional[str], paths: Iterable[str]) -> Dict[str, str]:
    """The current text of each path in the person's synced folder; a path that is missing or is not text is left out."""
    handle = local_workspace.get_workspace_handle(username)
    if handle is None:
        return {}
    root = Path(handle.root)
    out: Dict[str, str] = {}
    for path in paths:
        safe = local_workspace.safe_relative_path(path)
        target = root / safe if safe else None
        if target is not None and target.is_file():
            try:
                out[safe] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out
