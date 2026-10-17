"""How many tokens each person has used, by minute: what the per-user limits in budget_guard.py are judged against.

llm_usage.py (the usage meter) deliberately keeps no user: it says what the calls cost, not who made them. A per-user limit cannot be
judged without one, so this is a SEPARATE, small store, so the privacy line stays where it was and is easy to see: one row per person and
UTC minute holding only counts (tokens, calls, turns, dollars), never a prompt, a reply or any text. It is written from the end of a chat turn
(llm_turns.finish), in memory first and in batches with the other meters (llm_usage.flush), and every failure here is swallowed: counting
must never cost a reply.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("SASS Logger")

COLLECTION = "llm_usage_user_minutely"
FIELDS = ("tokens", "calls", "turns", "cost_micro")   # cost_micro: what the person's priced calls cost, in millionths of a dollar
MAX_USER_LENGTH = 200

_lock = threading.Lock()
_pending: Dict[Tuple[str, str], Dict[str, int]] = {}  # (user, minute) -> counts not yet saved


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def minute_key(now: Optional[datetime] = None) -> str:
    """Same minutes as llm_usage.minute_key (UTC, "YYYY-MM-DDTHH:MM"); kept here so this module needs nothing else to be read on its own."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def add(user: Optional[str], tokens: int, calls: int = 0, now: Optional[datetime] = None, cost_micro: int = 0) -> None:
    """Counts one finished turn for a person. A turn with no one attached (or nothing used) is not counted. Never raises."""
    try:
        name = str(user or "").strip()[:MAX_USER_LENGTH]
        used = _count(tokens)
        if not name or not (used or calls or cost_micro):
            return
        with _lock:
            slot = _pending.setdefault((name, minute_key(now)), {field: 0 for field in FIELDS})
            slot["tokens"] += used
            slot["calls"] += _count(calls)
            slot["turns"] += 1
            slot["cost_micro"] += _count(cost_micro)
    except Exception:
        pass


def snapshot() -> Dict[Tuple[str, str], Dict[str, int]]:
    with _lock:
        return {key: dict(value) for key, value in _pending.items()}


def reset() -> None:
    with _lock:
        _pending.clear()


def flush(db: Any = None) -> int:
    """Saves the counts (one `$inc` per person and minute) and returns how many rows. If the write fails the counts go back to be tried again
    with the next flush. Blocking: call it off the event loop. Never raises."""
    with _lock:
        pending = dict(_pending)
        _pending.clear()
    if not pending:
        return 0
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        from backend.utils import llm_usage

        llm_usage.ensure_expiry(db, COLLECTION)
        for (user, minute), counts in pending.items():
            db[COLLECTION].update_one(
                {"user": user, "minute": minute},
                {"$inc": {f: counts.get(f, 0) for f in FIELDS}, "$setOnInsert": {"at": llm_usage.minute_start(minute)}},
                upsert=True,
            )
        return len(pending)
    except Exception:
        logger.warning("[user_usage] could not save the per-person counts; keeping them for the next flush.")
        with _lock:
            for key, counts in pending.items():
                slot = _pending.setdefault(key, {field: 0 for field in FIELDS})
                for field in FIELDS:
                    slot[field] += counts.get(field, 0)
        return 0


def fetch(db: Any, user: str, since_minute: str) -> List[Dict[str, Any]]:
    """One person's saved minute rows from `since_minute` on. Blocking. Raises on a database error (the guard decides what that means)."""
    return list(db[COLLECTION].find({"user": user, "minute": {"$gte": since_minute}}, {"_id": 0}))


def tokens_since(rows: Iterable[Dict[str, Any]], since_minute: str) -> int:
    """Tokens in `rows` (saved or still in memory) from `since_minute` on. Pure."""
    return sum(_count(row.get("tokens")) for row in rows if str(row.get("minute") or "") >= since_minute)


def cost_since(rows: Iterable[Dict[str, Any]], since_minute: str, estimate_rate: Optional[float] = None) -> float:
    """Dollars in `rows` (saved or still in memory) from `since_minute` on. Pure. A row saved before dollars were counted has no `cost_micro` at all (a row
    saved since always has one, even if it is 0); with `estimate_rate` (dollars per token, the day's average) such a row is estimated from its tokens, so a
    person's meter is meaningful from the day it starts counting instead of starting at zero."""
    total = 0.0
    for row in rows:
        if str(row.get("minute") or "") < since_minute:
            continue
        if "cost_micro" in row:
            total += _count(row.get("cost_micro")) / 1_000_000
        elif estimate_rate:
            total += _count(row.get("tokens")) * estimate_rate
    return total


def unsaved_rows(user: str) -> List[Dict[str, Any]]:
    """What this person has used that is not yet saved, shaped like a saved row, so a limit counts the turn that just ended."""
    with _lock:
        return [{"user": u, "minute": minute, **counts} for (u, minute), counts in _pending.items() if u == user]
