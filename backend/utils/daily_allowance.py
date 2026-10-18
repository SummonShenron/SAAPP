"""A small per-person allowance that starts over each day: "3 generated pictures a day", "30 cards a day".

One document per (allowance, person, day), taken with a single atomic update, so two requests arriving together cannot both take the last one and a
restart or a second server cannot reset the count. The person's own calendar day is used (the caller passes it), so the allowance does not start over
in the evening for someone in America. Rows expire by themselves (a TTL index), so nothing accumulates.

Fails open: with no database, or if it cannot be reached, the use is allowed and the real cost limits elsewhere (the usage budget guard) still apply.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

logger = logging.getLogger("SASS Logger")

COLLECTION = "daily_allowances"
ROW_KEEP_DAYS = 3
_index_ready = False


def _key(name: str, username: str, day: str) -> str:
    person = hashlib.sha1((username or "").encode("utf-8")).hexdigest()[:12]
    return f"{name}:{day}:{person}"


def _ensure_ttl(db: Any) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
        _index_ready = True
    except Exception:
        logger.warning("[allowance] could not create the expiry index.", exc_info=True)


def take(db: Any, name: str, username: str, limit: int, day: str, now: Optional[datetime] = None) -> Tuple[bool, int]:
    """(taken, used so far today). Takes one use from this person's allowance for `day`, or says there is none left."""
    if db is None:
        return True, 0
    if limit <= 0:
        return False, 0
    moment = now or datetime.now(timezone.utc)
    try:
        from pymongo import ReturnDocument
        from pymongo.errors import DuplicateKeyError

        _ensure_ttl(db)
        try:
            doc = db[COLLECTION].find_one_and_update(
                {"_id": _key(name, username, day), "count": {"$lt": limit}},
                {"$inc": {"count": 1}, "$setOnInsert": {"expires_at": moment + timedelta(days=ROW_KEEP_DAYS)}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False, limit      # the row exists and is already at the limit, so the upsert could not insert it
        return True, int((doc or {}).get("count") or 1)
    except Exception:
        logger.warning("[allowance] could not read or write the allowance.", exc_info=True)
        return True, 0


def give_back(db: Any, name: str, username: str, day: str) -> None:
    """Returns a use that was taken for something that then failed, so a failure never costs the person their allowance."""
    if db is None:
        return
    try:
        db[COLLECTION].update_one({"_id": _key(name, username, day), "count": {"$gt": 0}}, {"$inc": {"count": -1}})
    except Exception:
        logger.warning("[allowance] could not give a use back.", exc_info=True)


def used(db: Any, name: str, username: str, day: str) -> int:
    if db is None:
        return 0
    try:
        return int((db[COLLECTION].find_one({"_id": _key(name, username, day)}) or {}).get("count") or 0)
    except Exception:
        return 0
