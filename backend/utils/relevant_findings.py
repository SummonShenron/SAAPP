"""Relevant findings: connecting what the person just said to something Sonic has really read.

THE GAP THIS CLOSES (2026-10-24, from the counters). In 14 days and 1,291 turns the reading shelf was offered 0 times although it held 11 fresh items. Every way for Sonic to
bring something up needed either a lull (a bare "ok" or "nice") or a hand-written keyword ("root cause", "neural network"), and a heavy user sends neither: their messages are
substantive and about what they are building. What was offered was nearly always a memory callback, which takes the turn's one proactive slot.

WHAT IT DOES. On an ordinary, substantive conversational turn it asks one question: is the MEANING of what they just said close to a piece on the shelf that they have not been
offered? Items carry an embedding made when they were read; the message is embedded once and compared. Only a close match (reading_shelf.RELEVANCE_MIN) becomes an offer,
and it is the same offer the shelf always made (a real item, with its link, said only as something read), so every check on that offer still applies: nothing is claimed
that the summary does not hold, and the item is spent only if the reply really used it.

WHAT KEEPS IT FROM BECOMING THE PERSON WHO TURNS EVERY CONVERSATION TO THEIR HOBBY.
- RELEVANCE FIRST. No match above the threshold means no offer. A stretched link is told to be left out even when the match passes.
- LIMITS. At most FINDINGS_PER_DAY a day (3), the usual 2 hour gap between any two offers (the welcome goal's lever still applies), each item once per person.
- THE USUAL QUIET. Never under a safety context, when the person is low or flat (the emotional ceiling), on a closing message, after a question Sonic itself just asked, on a
  guest, with the person's own "no offers" switch off, or on a subject they asked not to hear about.
- IT TAKES THE TURN'S ONE SLOT, ahead of a memory callback, so a real overlap is not lost to a routine follow-up.

No user text is stored or logged: the message is embedded and compared in memory, and only a count and a similarity number are kept.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.utils import curiosity, offer_response, profile_utils, reach
from backend.utils import reading_shelf as shelf
from backend.utils.self_counters import tally

logger = logging.getLogger("SASS Logger")


def select(
    db: Any,
    username: str,
    question: str,
    messages: List[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    proactive_taken: bool,
    user_profile: Optional[Dict[str, Any]],
    embed: Callable[[str], Optional[List[float]]],
    vector: Optional[List[float]] = None,
    counts: Optional[Dict[str, int]] = None,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """{"kind": "finding", "item", "score"} for this turn, or None. Blocking (database reads and one embedding call), so run it off the event loop. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        if db is None or not shelf.shelf_enabled() or not profile_utils.offers_allowed(user_profile):
            return None
        if not curiosity.finding_slot_open(question, messages, source_type=source_type, risk_active=risk_active, energy_tier=energy_tier, proactive_taken=proactive_taken):
            return None
        level = reach.reach_of(user_profile)       # the person's own dial: how many a day, and how long between offers
        if shelf.findings_in_last_day(db, username, now) >= level.findings_per_day:
            return None
        if offer_response.pacing(db, username, now, counts=counts, gap_hours=level.gap_hours) != offer_response.CLEAR:
            return None
        seen = shelf.seen_keys(db, username)
        fresh = [item for item in shelf.live_items(db, now) if item["_id"] not in seen]
        if not fresh:
            return None
        ready = [item for item in fresh if item.get("embedding")]
        if len(ready) < len(fresh):
            shelf.ensure_embeddings(db, embed, limit=3)       # a few per turn at most; the scheduled reading fills in the rest
            ready = [item for item in shelf.live_items(db, now) if item["_id"] not in seen and item.get("embedding")]
        if not ready:
            return None
        if not vector:       # the turn already embeds the whole message for memory recall and passes it in; this is only for a caller that has not
            vector = embed((question or "").strip()[: curiosity.FINDING_MAX_CHARS])
        match = shelf.best_match(ready, vector) if vector else None
        if match is None:
            return None
        item = match["item"]
        if profile_utils.touches_avoided(f"{item.get('title', '')} {item.get('summary', '')} {item.get('topic', '')}", user_profile):
            return None
        if counts is not None:
            tally(counts, "finding_selected")
        logger.info("[findings] a shelf item matched what was said (similarity %.2f).", match["score"])
        return {"kind": "finding", "item": item, "score": match["score"]}
    except Exception:
        logger.warning("[findings] could not look for a relevant finding.", exc_info=True)
        return None
