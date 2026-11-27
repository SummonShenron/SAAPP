"""What Sonic told a person it thinks, kept for that person, so it stays consistent and can change its mind on the record.

WHY (2026-11-27). A view said once and forgotten is not a view anyone holds: asked the same thing a week later Sonic could say the opposite, with no way to know. With the sentence kept, a later
reply can stand by it, or say "I said X before; I'd put it differently now, because...".

WHAT IS KEPT. One document per view, in `sonic_said_views`: the person, the ONE sentence of Sonic's own reply that stated the view (backend/utils/view_check.view_sentence, at most 220
characters), the distinctive words of that sentence and of the message it answered (so a later message can find it), and when. No message of the person's is stored, only words from it as a
bag. At most MAX_PER_PERSON a person (the oldest go first); the same sentence is never kept twice; a document expires TTL_DAYS after it was written.

PRIVACY. Per person, read only for that person's own turns, never shared, never logged, and erased with everything else of theirs (backend/utils/memory_erase.py). Never for a guest; the
caller keeps it away from safety turns.

WHEN IT IS READ. Only on a turn that asks for a view. `recall` returns the kept view that shares the most distinctive words with the message (at least MIN_SHARED), said at least
MIN_AGE_HOURS ago, so a reply is never told to stay consistent with what it said a minute ago in the same sitting.

No model call anywhere.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.utils import material_use, view_check

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_said_views"
MAX_PER_PERSON = 40
TTL_DAYS = 120
MIN_SHARED = 3
MIN_AGE_HOURS = 6
MAX_WORDS_KEPT = 40
_indexed = {"done": False}


def _aware(moment: Any) -> Optional[datetime]:
    if not isinstance(moment, datetime):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _ensure_indexes(db: Any) -> None:
    if _indexed["done"]:
        return
    try:
        db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
        db[COLLECTION].create_index("username")
        _indexed["done"] = True
    except Exception:
        logger.warning("[own views] could not create the indexes.", exc_info=True)


def entry_for(message: str, reply: str, now: datetime) -> Optional[Dict[str, Any]]:
    """The document to keep for a reply that stated a view, or None when it stated none. Pure."""
    sentence = view_check.view_sentence(reply)
    if not sentence:
        return None
    words = sorted(material_use._words(sentence) | material_use._words(message))[:MAX_WORDS_KEPT]
    if len(words) < MIN_SHARED:
        return None
    return {"id": uuid.uuid4().hex, "text": sentence, "words": words, "at": now, "expires_at": now + timedelta(days=TTL_DAYS)}


def note(db: Any, username: str, message: str, reply: str, now: Optional[datetime] = None) -> bool:
    """Keeps the view this reply stated, for this person. True when one was kept. Blocking; never raises."""
    if db is None or not username:
        return False
    moment = now or datetime.now(timezone.utc)
    entry = entry_for(message, reply, moment)
    if entry is None:
        return False
    try:
        _ensure_indexes(db)
        kept = list(db[COLLECTION].find({"username": username}))
        if any(doc.get("text") == entry["text"] for doc in kept):
            return False
        db[COLLECTION].insert_one({**entry, "_id": entry["id"], "username": username})
        kept.sort(key=lambda doc: _aware(doc.get("at")) or moment)
        for doc in kept[: max(len(kept) + 1 - MAX_PER_PERSON, 0)]:
            db[COLLECTION].delete_one({"username": username, "id": doc.get("id")})
        return True
    except Exception:
        logger.warning("[own views] could not keep a view.", exc_info=True)
        return False


def pick(kept: List[Dict[str, Any]], message: str, now: datetime) -> Optional[Dict[str, Any]]:
    """The kept view that shares the most distinctive words with the message (MIN_SHARED or more), said at least MIN_AGE_HOURS ago; the newer one wins a tie. Pure."""
    said = material_use._words(message)
    best, best_key = None, None
    for doc in kept or []:
        at = _aware(doc.get("at"))
        if at is None or now - at < timedelta(hours=MIN_AGE_HOURS) or not doc.get("text"):
            continue
        shared = len(said & set(doc.get("words") or []))
        if shared >= MIN_SHARED and (best_key is None or (shared, at) > best_key):
            best, best_key = doc, (shared, at)
    return best


def recall(db: Any, username: str, message: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The view Sonic gave this person before that bears on this message, or None. Blocking; never raises."""
    if db is None or not username:
        return None
    try:
        return pick(list(db[COLLECTION].find({"username": username})), message, now or datetime.now(timezone.utc))
    except Exception:
        logger.warning("[own views] could not read the kept views.", exc_info=True)
        return None


def build_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section for a kept view ("" when none). Pure."""
    if not entry:
        return ""
    at = _aware(entry.get("at"))
    when = f" on {at.strftime('%B')} {at.day}" if at else ""
    return (
        f"\nWHAT YOU TOLD THEM YOU THINK ABOUT THIS BEFORE{when}: \"{entry['text']}\"\nIf it bears on what they ask now, stand by it, or, if you see it differently now, say so plainly "
        "(\"I said ... before; I'd put it differently now, because ...\"). Never contradict it silently. Do not quote it back unless that helps them.\n"
    )
