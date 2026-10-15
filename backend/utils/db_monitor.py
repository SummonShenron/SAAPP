"""Says out loud when a database command is slow, and how big its answer was: the thing nothing reported during the October 2026 slowdown.

A request that waits on a multi-megabyte read looks like the whole app hanging, and the only trace was a stall in the event loop with no hint of WHICH
read. This listens to every command on the app's database client (db_utils.get_db) and, for any that takes longer than DB_SLOW_COMMAND_SECONDS
(default 2), writes one line: the command, the collection and how long it took and, for a reply, roughly how large it was:

    [db-slow] find user_memory_facts took 38.2s (about 4.1 MB)

- NAMES AND SIZES ONLY. The command name and collection are all that is recorded about what was asked, and the size is a count of bytes: no
  filter, no document, no user text ever reaches the log (the log-hygiene rule, backend/logging).
- FAST COMMANDS COST NEARLY NOTHING: a dict entry when one starts and one removal when it ends. The size is only worked out for a slow reply.
- IT NEVER GETS IN THE WAY: any failure in here is swallowed.
- `slow_counts()` is the running tally (by command and collection since the process started) for an admin page to show later.
"""
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from pymongo import monitoring

logger = logging.getLogger("SASS Logger")

DEFAULT_SLOW_SECONDS = 2.0
MAX_IN_FLIGHT = 2000  # a command that never reports back must not grow this without end

_lock = threading.Lock()
_counts: Dict[Tuple[str, str], Dict[str, float]] = {}


def slow_seconds() -> float:
    try:
        value = float(os.getenv("DB_SLOW_COMMAND_SECONDS", "").strip())
    except ValueError:
        return DEFAULT_SLOW_SECONDS
    return value if value > 0 else DEFAULT_SLOW_SECONDS


def _size_text(reply: Any) -> str:
    """"about 4.1 MB" for a reply, or "" when it cannot be measured. Only called for a slow command."""
    try:
        import bson

        size = len(bson.encode(reply))
    except Exception:
        return ""
    if size >= 1024 * 1024:
        return f" (about {size / (1024 * 1024):.1f} MB)"
    if size >= 1024:
        return f" (about {size / 1024:.0f} KB)"
    return f" ({size} bytes)"


def slow_counts() -> Dict[str, Dict[str, float]]:
    """{"find user_memory_facts": {"count": 3, "seconds": 91.2}}, since the process started."""
    with _lock:
        return {f"{command} {collection}": dict(value) for (command, collection), value in _counts.items()}


def reset() -> None:
    with _lock:
        _counts.clear()


class SlowCommandLogger(monitoring.CommandListener):
    """Registered on the database client in db_utils.get_db."""

    def __init__(self, threshold: Optional[float] = None) -> None:
        self._threshold = threshold
        self._started: Dict[int, Tuple[str, str]] = {}

    def started(self, event: Any) -> None:
        try:
            name = str(event.command_name)
            target = event.command.get(name)
            collection = target if isinstance(target, str) else ""
            with _lock:
                if len(self._started) < MAX_IN_FLIGHT:
                    self._started[event.request_id] = (name, collection or event.database_name)
        except Exception:
            pass

    def _finish(self, event: Any, failed: bool) -> None:
        try:
            reply = None if failed else getattr(event, "reply", None)
            with _lock:
                name, collection = self._started.pop(event.request_id, (str(event.command_name), ""))
            seconds = event.duration_micros / 1_000_000
            if seconds < (self._threshold if self._threshold is not None else slow_seconds()):
                return
            with _lock:
                slot = _counts.setdefault((name, collection), {"count": 0, "seconds": 0.0})
                slot["count"] += 1
                slot["seconds"] = round(slot["seconds"] + seconds, 3)
            size_note = "" if failed else _size_text(reply)  # a byte count, nothing of the reply itself
            logger.warning("[db-slow] %s %s %s %.1fs%s", name, collection or "-", "failed after" if failed else "took", seconds, size_note)
        except Exception:
            pass

    def succeeded(self, event: Any) -> None:
        self._finish(event, False)

    def failed(self, event: Any) -> None:
        self._finish(event, True)
