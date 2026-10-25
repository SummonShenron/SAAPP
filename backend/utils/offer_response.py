"""How each person responds to what Sonic brings up on its own, as counts only: the measurement behind the "welcome" goal (backend/components/sonic_goals.py).

Sonic sometimes brings something up unprompted: the one open question at a lull, an item from its reading shelf, a callback to an earlier conversation, a leaning
of its own. Every such reply carries a mark (curiosity.CURIOSITY_MARK). The person's NEXT message is the response: it either picks the thing up (some substance,
not a sign-off, not another bare "ok": curiosity.taken) or passes on it. A sign-off after an offer is not counted either way, because ending a conversation says
nothing about the offer.

One small document per person in `sonic_offer_response`: `{"username", "recent": [1, 0, 0, ...], "stretch": 2.0, "updated_at"}`. `recent` holds only the last WINDOW
outcomes as 1 (picked up) or 0 (passed): no text, no time of day, no conversation id, nothing about what was offered. `stretch` is the number the pacing is multiplied
by for this person, worked out from `recent` each time one is added:

- fewer than MIN_SAMPLE outcomes, or none for STALE_DAYS: 1.0 (no evidence, or evidence too old to act on; the window starts over on the next outcome);
- picked up at least TIERS[0] of the time: 1.0; less than that, a longer gap between offers, in steps, up to MAX_STRETCH times the usual one.

Stretching only ever makes Sonic quieter than its default, never pushier, and never silent: the longest gap is still a gap, so an offer that lands later can still
show Sonic it was wrong. Whether the stretch is APPLIED at all depends on the goal being approved (backend/utils/goals.py); it is measured either way. The document is
removed by "forget everything" like the rest of what Sonic keeps about someone.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_offer_response"
WINDOW = 10                 # how many of the latest outcomes are kept
MIN_SAMPLE = 5              # fewer outcomes than this say nothing about a person
STALE_DAYS = 21             # outcomes older than this are not acted on, and the window starts over
RETENTION_DAYS = 90
MAX_STRETCH = 4.0
# (picked-up share at or above, stretch): the first row that fits wins. 4.0 against the usual 2 hours between offers is 8 hours, never silence.
TIERS = ((0.4, 1.0), (0.2, 2.0), (0.0, MAX_STRETCH))

_index_ready = False


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def outcome(previous_marks: Optional[Dict[str, Any]], message: str) -> Optional[str]:
    """"taken", "passed", or None when the previous reply offered nothing or this message is a sign-off. `previous_marks` is the previous reply's additional_kwargs."""
    from backend.utils import curiosity
    from backend.utils.wrapup_utils import is_closing_message

    if not (previous_marks or {}).get(curiosity.CURIOSITY_MARK) or is_closing_message(message):
        return None
    return "taken" if curiosity.taken(message) else "passed"


def stretch_for(recent: List[int]) -> float:
    """The pacing multiplier the window of outcomes calls for."""
    if len(recent) < MIN_SAMPLE:
        return 1.0
    share = sum(recent) / len(recent)
    for threshold, stretch in TIERS:
        if share >= threshold:
            return stretch
    return MAX_STRETCH


def _clean(raw: Any) -> List[int]:
    return [1 if value else 0 for value in (raw if isinstance(raw, list) else [])][-WINDOW:]


def _fresh(row: Optional[Dict[str, Any]], now: datetime) -> bool:
    updated = _aware((row or {}).get("updated_at"))
    return updated is not None and now - updated <= timedelta(days=STALE_DAYS)


def _ensure_indexes(db: Any) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        db[COLLECTION].create_index("username", unique=True)
        db[COLLECTION].create_index("updated_at", expireAfterSeconds=RETENTION_DAYS * 24 * 3600)
        _index_ready = True
    except Exception:
        logger.warning("[offer response] could not create the indexes; will try again later.", exc_info=True)


def record(db: Any, username: Optional[str], taken: bool, now: Optional[datetime] = None) -> Optional[float]:
    """Adds one outcome to this person's window and returns the stretch it now calls for (None when nothing was written). Blocking (database); never raises."""
    if not username or db is None:
        return None
    try:
        now = now or datetime.now(timezone.utc)
        _ensure_indexes(db)
        row = db[COLLECTION].find_one({"username": username}, {"_id": 0, "recent": 1, "updated_at": 1})
        recent = _clean((row or {}).get("recent")) if _fresh(row, now) else []
        recent = (recent + [1 if taken else 0])[-WINDOW:]
        stretch = stretch_for(recent)
        db[COLLECTION].update_one(
            {"username": username},
            {"$set": {"username": username, "recent": recent, "n": len(recent), "stretch": stretch, "updated_at": now}},
            upsert=True,
        )
        return stretch
    except Exception:
        logger.warning("[offer response] could not record an outcome.", exc_info=True)
        return None


def stretch_of(db: Any, username: str, now: Optional[datetime] = None) -> float:
    """This person's current stretch, 1.0 when there is no fresh evidence. Blocking."""
    now = now or datetime.now(timezone.utc)
    row = db[COLLECTION].find_one({"username": username}, {"_id": 0, "recent": 1, "updated_at": 1})
    return stretch_for(_clean((row or {}).get("recent"))) if _fresh(row, now) else 1.0


def people_view(db: Any, now: Optional[datetime] = None) -> Dict[str, int]:
    """How many people there is fresh evidence about, and for how many of them the pacing would be stretched. Counts only. Blocking."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=STALE_DAYS)
    fresh = {"updated_at": {"$gte": since}}
    return {
        "measured": int(db[COLLECTION].count_documents({**fresh, "n": {"$gte": MIN_SAMPLE}})),
        "stretched": int(db[COLLECTION].count_documents({**fresh, "n": {"$gte": MIN_SAMPLE}, "stretch": {"$gt": 1.0}})),
        "any": int(db[COLLECTION].count_documents(fresh)),
    }


# What pacing says about the next offer.
CLEAR, HELD, STRETCHED = "clear", "held", "stretched"


def pacing(db: Any, username: str, now: Optional[datetime] = None, *, apply: Optional[bool] = None, counts: Optional[Dict[str, int]] = None,
           gap_hours: Optional[float] = None) -> str:
    """CLEAR when enough time has passed since this person was last offered something, HELD when the usual gap has not, STRETCHED when the usual gap has passed but the
    longer one this person's responses call for has not. The last case is the lever acting: it is added to `counts` (this turn's tally) as offer_held_back, which is
    how Sonic Health shows what the lever did. `apply` defaults to whether the welcome goal is approved and its guardrail is holding (backend/utils/goals.py). Blocking."""
    from backend.utils import curiosity, goals
    from backend.utils.self_counters import tally

    now = now or datetime.now(timezone.utc)
    base = curiosity.MIN_HOURS_BETWEEN if gap_hours is None else gap_hours      # the person's reach level sets the usual gap (backend/utils/reach.py)
    last = curiosity.last_offer_at(username)
    if not curiosity.gap_clear(last, now, base):
        return HELD
    try:
        if apply is None:
            apply = goals.lever_applies(db, "welcome", now)
        stretch = stretch_of(db, username, now) if apply else 1.0
    except Exception:
        logger.warning("[offer response] could not read the pacing; using the usual gap.", exc_info=True)
        stretch = 1.0                     # a failed read must not hold an offer back: Sonic's usual behaviour is the safe default
    if stretch > 1.0 and not curiosity.gap_clear(last, now, base * stretch):
        if counts is not None:
            tally(counts, "offer_held_back")
        return STRETCHED
    return CLEAR


def person_view(db: Any, username: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What can truthfully be said to this person about their own pacing: whether Sonic has been bringing things up less for them, and because of what. Counts of their
    own responses only. Blocking."""
    from backend.utils import goals

    now = now or datetime.now(timezone.utc)
    stretch = stretch_of(db, username, now)
    return {"stretched": stretch > 1.0, "applied": bool(stretch > 1.0 and goals.lever_applies(db, "welcome", now))}


def delete_for_user(db: Any, username: str) -> int:
    return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0) or 0)


def forget_all(username: str) -> int:
    """Part of "forget everything" (like the other stores' forget_all)."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return delete_for_user(db, username) if db is not None else 0
