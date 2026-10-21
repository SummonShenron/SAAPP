"""When someone last used Sonic, as one timestamp, so scheduled work can wait for a quiet moment without scanning conversations.

One document per person in `sonic_user_activity`: `{"username": ..., "last_active_at": <UTC datetime>}`, unique on username, so a lookup is an index hit and the
scheduler never reads a transcript to ask "is this person here right now?". It holds no text and no session or conversation id. A turn writes it at most once every
TOUCH_EVERY_SECONDS per person (an in-process memory of the last write), so a busy chat costs one small write every few minutes, not one per message. The timestamp
expires on its own (a person gone for months has nothing to wait for) and is wiped by "forget everything" like the rest of what Sonic keeps about someone.

A person with no record is treated as idle by the caller: nothing says they are here.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_user_activity"
TOUCH_EVERY_SECONDS = 300.0       # how often one person's timestamp is written while they keep chatting
RETENTION_DAYS = 90
_MAX_REMEMBERED = 5000            # the in-process memory of last writes is trimmed past this, so it cannot grow without end

_index_ready = False
_last_write: Dict[str, float] = {}
_lock = threading.Lock()


def reset_memory() -> None:
    """For tests: forget the last writes and the index flag."""
    global _index_ready
    with _lock:
        _last_write.clear()
    _index_ready = False


def _ensure_indexes(db: Any) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        db[COLLECTION].create_index("username", unique=True)
        db[COLLECTION].create_index("last_active_dt", expireAfterSeconds=RETENTION_DAYS * 24 * 3600)
        _index_ready = True
    except Exception:
        logger.warning("[activity] could not create the indexes; will try again later.", exc_info=True)


def _due_for_write(username: str) -> bool:
    now = time.monotonic()
    with _lock:
        last = _last_write.get(username)
        if last is not None and now - last < TOUCH_EVERY_SECONDS:
            return False
        if len(_last_write) >= _MAX_REMEMBERED:
            for stale in sorted(_last_write, key=_last_write.get)[: _MAX_REMEMBERED // 2]:
                _last_write.pop(stale, None)
        _last_write[username] = now
        return True


def touch(db: Any, username: Optional[str], now: Optional[datetime] = None) -> bool:
    """Notes that this person is here now. True when it wrote (False when it was too soon after the last write, or there is nobody or nothing to write to).
    Blocking (a database write): call it off the event loop. Never raises: noting activity must not break a reply."""
    if not username or db is None or not _due_for_write(username):
        return False
    try:
        _ensure_indexes(db)
        moment = now or datetime.now(timezone.utc)
        db[COLLECTION].update_one({"username": username}, {"$set": {"username": username, "last_active_at": moment, "last_active_dt": moment}}, upsert=True)
        return True
    except Exception:
        logger.warning("[activity] could not note activity.", exc_info=True)
        with _lock:
            _last_write.pop(username, None)          # the write did not happen, so the next turn should try again
        return False


def touch_safely(username: Optional[str]) -> bool:
    from backend.utils.db_utils import get_db

    try:
        return touch(get_db(), username)
    except Exception:
        return False


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def idle_minutes(db: Any, username: str, now: Optional[datetime] = None) -> Optional[float]:
    """Minutes since this person was last active, or None when there is no record (nothing says they are here). Blocking."""
    row = db[COLLECTION].find_one({"username": username}, {"_id": 0, "last_active_at": 1})
    seen = _aware((row or {}).get("last_active_at"))
    if seen is None:
        return None
    return max((now or datetime.now(timezone.utc)) - seen, timedelta(0)).total_seconds() / 60.0


def delete_for_user(db: Any, username: str) -> int:
    return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0) or 0)


def forget_all(username: str) -> int:
    """Part of "forget everything" (like the other stores' forget_all)."""
    from backend.utils.db_utils import get_db

    db = get_db()
    with _lock:
        _last_write.pop(username, None)
    return delete_for_user(db, username) if db is not None else 0
