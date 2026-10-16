"""An open, natural conversation extender: the kind of question a person asks when a chat has gone quiet.

Sonic closes conversations well (backend/utils/wrapup_utils.py), and the rule behind it ("never manufacture a reason to keep
talking") has also trained it never to ask anything like "what else have you got going on?". A person in a text thread does.
This lets Sonic ask exactly one such question, at a real lull, and then stop. It is not engagement bait because it is
clamped in code, not left to the model's judgment:

- ONE PER CONVERSATION. A marker on Sonic's own reply (CURIOSITY_MARK) means it has been asked, and it is not asked twice in that
  conversation. A conversation is a stretch of the saved chat with no pause of CONVERSATION_GAP_HOURS (default 1) in it, so a chat that
  people return to for weeks is not used up by its first offer; the per-person gap below is what keeps it rare.
- NOT IN EVERY CONVERSATION. A person is asked at most once every CURIOSITY_MIN_HOURS (default 2, to be tuned from real use), stored on their
  settings document. This is a time gap, not "never two conversations in a row", because nothing orders conversations
  cheaply; with the defaults a person can be asked at most every couple of hours, and only at a real lull, so in practice far less often.
- ONLY AT A LULL. After a real exchange (CURIOSITY_MIN_USER_TURNS messages from them), when their message is a bare
  acknowledgement ("ok", "huh, interesting", "yeah I guess") and Sonic's last reply did not end on a question of its own.
  A closing message ("thanks", "goodnight") is never a lull: it gets the clean close, whatever else is due.
- NEVER WHEN A QUIET REPLY MATTERS MORE. A safety context, an emotional context still holding the energy down, a route
  other than plain conversation, a guest, or any other proactive item already in this turn (the turn has one slot).
- IF THEY HAVE NOTHING, THE REPLY TO IT IS A CLOSING AS USUAL. Nothing here changes how Sonic wraps up.

This module only decides WHETHER to invite the question and what the invitation says; the model writes it, may skip it, and
the usual reply checks (identity, emotional fit) still run on whatever it writes. Whether it was really asked is read from
the finished reply (asked_a_question), so the counters and the cap record what happened, not what was invited.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, List, Optional

from backend.utils.emotion_checks import reply_body
from backend.utils.wrapup_utils import is_closing_message

logger = logging.getLogger("SASS Logger")

CURIOSITY_MARK = "curiosity_offered"
SETTINGS_COLLECTION = "user_settings"
LAST_OFFER_KEY = "last_curiosity_offer_at"
MIN_HOURS_BETWEEN = float(os.getenv("CURIOSITY_MIN_HOURS", "2"))
# A saved chat can run for weeks, so "a conversation" is a stretch of it with no pause this long in the middle: after the pause, it is a new one.
CONVERSATION_GAP_HOURS = float(os.getenv("CURIOSITY_CONVERSATION_GAP_HOURS", "1"))
MIN_USER_TURNS = int(os.getenv("CURIOSITY_MIN_USER_TURNS", "3"))
MAX_LULL_WORDS = 6
TAKEN_MIN_WORDS = 3
_ROUTES = {"conversational"}

# What a lull is made of: acknowledgements and hedges. A message with any other word in it has content, so it is not a lull.
_LULL_WORDS = {
    "ok", "okay", "k", "kk", "yeah", "yea", "yep", "yup", "yes", "no", "nope", "nah", "hm", "hmm", "hmmm", "mm", "mmm", "mhm",
    "uh", "huh", "oh", "ah", "ahh", "aha", "i", "see", "right", "true", "fair", "interesting", "alright", "aight", "sure", "well",
    "so", "lol", "haha", "heh", "guess", "suppose", "maybe", "that", "thats", "that's", "is", "it", "its", "it's", "kinda", "kind",
    "of", "a", "bit", "makes", "sense", "point", "fine", "just", "idk", "dunno", "not", "much", "really", "very", "pretty", "too",
    "then", "now", "thought", "didnt", "didn't", "know", "wow", "enough", "as", "expected",
}


def curiosity_enabled() -> bool:
    return os.getenv("CURIOSITY_OFFERS", "true").strip().lower() not in ("0", "false", "no", "off")


def is_lull(text: Optional[str]) -> bool:
    """True when the message is only an acknowledgement or a hedge: short, no question, nothing new in it, and not a sign-off
    (a sign-off is a closing, which is its own thing and never gets a question)."""
    if not text or "?" in text or is_closing_message(text):
        return False
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > MAX_LULL_WORDS:
        return False
    return not re.search(r"[0-9]|https?://", text) and all(w in _LULL_WORDS for w in words)


def _type_of(message: Any) -> str:
    return str(getattr(message, "type", "") or "")


def current_stretch(messages: Iterable[Any], gap_hours: Optional[float] = None) -> List[Any]:
    """The part of a saved chat that is the conversation going on now: back from the latest message until a pause of CONVERSATION_GAP_HOURS or more
    between two messages. Without it, one offer would spend the limit for the life of a chat that people keep coming back to for weeks. A
    message with no sent time (saved before messages were stamped) is never taken as a pause: an unknown time is not guessed."""
    from backend.utils.time_utils import sent_at

    limit = timedelta(hours=CONVERSATION_GAP_HOURS if gap_hours is None else gap_hours)
    ordered = list(messages)
    start, later = len(ordered), None
    for index in range(len(ordered) - 1, -1, -1):
        when = sent_at(ordered[index])
        if when is not None and later is not None and later - when >= limit:
            break
        start = index
        if when is not None:
            later = when
    return ordered[start:]


def already_offered(messages: Iterable[Any]) -> bool:
    """Whether Sonic has used its one extender in this conversation (this stretch of the chat, see current_stretch). An item from the reading
    shelf spends it too: it carries the same mark."""
    return any((getattr(m, "additional_kwargs", None) or {}).get(CURIOSITY_MARK) for m in current_stretch(messages))


def user_turns(messages: Iterable[Any]) -> int:
    """How many messages the person has sent in this conversation (this stretch of the chat)."""
    return sum(1 for m in current_stretch(messages) if _type_of(m) == "human")


def last_reply_asked(messages: List[Any]) -> bool:
    """Whether Sonic's most recent reply (the message before the user's latest) ended on a question of its own. If it did, a
    short answer is an answer, not a lull."""
    for message in reversed(messages[:-1]):
        if _type_of(message) == "ai":
            return "?" in reply_body(str(getattr(message, "content", "") or ""))[-200:]
    return False


def slot_open(
    user_message: str,
    messages: List[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    proactive_taken: bool,
    min_turns: int = MIN_USER_TURNS,
) -> bool:
    """The gates every conversation extender shares (the open question here, an item from the reading shelf in
    backend/utils/reading_shelf.py): the right route, nothing that calls for a quiet reply, the turn's one proactive slot still free,
    not a closing message, enough of a conversation, and nothing offered yet in it. `messages` includes the user's latest message."""
    if not curiosity_enabled() or source_type not in _ROUTES:
        return False
    if risk_active or energy_tier != "open" or proactive_taken or is_closing_message(user_message):
        return False
    return user_turns(messages) >= min_turns and not already_offered(messages)


def curiosity_allowed(
    user_message: str,
    messages: List[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    proactive_taken: bool,
) -> bool:
    """Every check that costs nothing, for the open question at a lull. The per-person gap is separate (gap_clear), since it
    needs a database read and only matters once everything here has passed."""
    return (
        is_lull(user_message)
        and slot_open(user_message, messages, source_type=source_type, risk_active=risk_active, energy_tier=energy_tier,
                      proactive_taken=proactive_taken)
        and not last_reply_asked(messages)
    )


def gap_clear(last_offer_at: Optional[datetime], now: datetime, hours: Optional[float] = None) -> bool:
    if last_offer_at is None:
        return True
    return now - last_offer_at >= timedelta(hours=MIN_HOURS_BETWEEN if hours is None else hours)


def asked_a_question(reply: str) -> bool:
    """Whether the finished reply asks one. A question inside a <<<FOLLOW_UP:...>>> suggestion is the interface's, not Sonic's."""
    return "?" in reply_body(reply or "")


def taken(user_message: str) -> bool:
    """Whether a message that follows the question picks it up: it has some substance and is neither a sign-off nor another
    bare acknowledgement."""
    words = re.findall(r"[A-Za-z0-9']+", user_message or "")
    return len(words) >= TAKEN_MIN_WORDS and not is_closing_message(user_message) and not is_lull(user_message)


def build_curiosity_block() -> str:
    return (
        "\nOPEN QUESTION (optional, this turn only): the conversation has gone quiet and what they asked has been fully "
        "answered. For this one reply, the rule about not adding a question is lifted: after your short reply, you may ask ONE "
        "open, easy question of the kind a friend asks when a chat lulls: what else is on their plate, what's new, how the "
        "rest of their day or week looks, whether anything fun is coming up. These are registers, not scripts: write your own "
        "wording, shaped by this conversation, and do not use a stock phrase. Keep it light and general: not about the topic you "
        "just covered, not a probe into how they feel or a hard part of their life, not a menu of things you could do next, "
        "and not a <<<FOLLOW_UP>>> suggestion. Do not say you are curious, that you were wondering, or that you want to keep "
        "talking. If they have nothing, you will simply wrap up. Skip the question entirely if it would feel forced.\n"
    )


def _with_db(fn: Callable, *args, default=None):
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        return fn(db, *args) if db is not None else default
    except Exception:
        logger.warning("[curiosity] state operation failed.", exc_info=True)
        return default


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_offer_at(username: str) -> Optional[datetime]:
    """When this person was last asked, or None (never, or the database is unavailable: a failed read must not block the
    question forever, and the other gates already make it rare)."""
    return _with_db(
        lambda db, u: _parse((db[SETTINGS_COLLECTION].find_one({"username": u}, {"_id": 0, LAST_OFFER_KEY: 1}) or {}).get(LAST_OFFER_KEY)),
        username,
    )


def record_offer(username: str, now: Optional[datetime] = None) -> None:
    when = (now or datetime.now(timezone.utc)).isoformat()
    _with_db(lambda db, u: db[SETTINGS_COLLECTION].update_one({"username": u}, {"$set": {LAST_OFFER_KEY: when}}, upsert=True), username)


# ---------------------------------------------------------------------------------------------------------------
# Something of its own to steer by: a topic Sonic leans toward
# ---------------------------------------------------------------------------------------------------------------
# The fallback at a lull when the reading shelf has nothing this person has not seen (backend/utils/reading_shelf.py) and no earlier conversation
# connects (the callback in backend/utils/shared_history.py): one of the curated leanings in sonic_profile.INTERESTS, said in leaning language and
# followed by a light question about whether the person shares it. It is a topic, never an activity or a feeling (backend/utils/identity_checks.py
# still checks the reply), and each is offered to a given person once.

LEAN_SEEN_KEY = "lean_seen"


def pick_lean(seen: Iterable[str]) -> Optional[dict]:
    """A curated leaning this person has not been offered, or None when they have heard them all."""
    import random

    from backend.components.sonic_profile import INTERESTS

    taken = set(seen)
    fresh = [entry for entry in INTERESTS if entry["id"] not in taken]
    return random.choice(fresh) if fresh else None


def build_lean_block(entry: dict) -> str:
    return (
        "\nOFFER SOMETHING OF YOUR OWN (this reply only): the conversation has gone quiet and what they asked is answered. For this one reply, "
        "the rule about not adding anything is lifted: after a short reply, say in a sentence that you lean toward a topic, along these lines: "
        f"\"{entry['line']}\" Put it in your own words, then ask, lightly, whether it is something they follow or have thoughts on. It is a "
        "leaning of yours, not an activity or a story: never say you played, watched, read or did anything, never claim a feeling about it, "
        "and never say you were thinking about it. If they have nothing, you will simply wrap up. Skip it if it would feel forced.\n"
    )


def lean_used(reply: str, entry: dict) -> bool:
    """Whether the finished reply really said it: it shares at least two content words with the leaning's line."""
    from backend.utils.reading_shelf import reply_used_item

    return reply_used_item(reply, {"title": "", "summary": entry["line"]})


def lean_seen(username: str) -> List[str]:
    found = _with_db(
        lambda db, u: (db[SETTINGS_COLLECTION].find_one({"username": u}, {"_id": 0, LEAN_SEEN_KEY: 1}) or {}).get(LEAN_SEEN_KEY), username
    )
    return [str(x) for x in (found or [])]


def mark_lean(username: str, entry_id: str) -> None:
    _with_db(
        lambda db, u: db[SETTINGS_COLLECTION].update_one({"username": u}, {"$addToSet": {LEAN_SEEN_KEY: entry_id}}, upsert=True), username
    )
