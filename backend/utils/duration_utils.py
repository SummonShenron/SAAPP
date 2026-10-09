"""Honest duration: how long the user and Sonic have been talking, said as a fact and nothing more.

Companion apps lean on "we've been together 47 days" and anniversaries, usually with a feeling attached ("it's been wonderful
getting to know you"). The information is harmless; the framing is the manufactured-closeness mechanism. This keeps the
information and drops the framing:

- ONLY WHEN ASKED ("how long have we been talking?", "when did we first talk?"). Never volunteered, never a milestone, never
  an anniversary, never a count of days or messages that could become a streak.
- ONLY WHAT IS KNOWN. Sonic can see the conversations that are still saved, so the answer is "your earliest saved
  conversation is from June 2026", not "since we met": anything the user deleted is gone, and the prompt says so. If the lookup
  fails, Sonic is told to say it can't check, never to guess.
- NO SENTIMENT. The prompt forbids attaching a feeling or meaning to the number, and identity_checks.py rejects the common
  shapes ("it's been a joy getting to know you", "happy anniversary").
- NOT FOR GUESTS. The shared guest identity's history belongs to many people, so there is no "we" to count.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.utils.time_utils import describe_gap, local_now

logger = logging.getLogger("SASS Logger")

# "how long have we been talking / known each other / chatting", "when did we first talk", "how long have I been using you".
# "how long have we been talking about X" is about a topic, not about the two of them, so it does not count.
# A sentence that goes on to say "about ..." or "regarding ..." is about a topic, whatever its opening says.
_TOPIC_TAIL = r"(?![^.?!]*\b(?:about|regarding|re:)\b)"
_ASKS_RE = re.compile(
    r"\bhow long (?:have|has) (?:we|you and i|i and you)\b(?:\s+(?:both|even|really))?\s+(?:been\s+)?(?:talking|chatting|speaking|known each other|"
    r"known one another|been (?:talking|chatting)|been together)\b" + _TOPIC_TAIL +
    r"|\bhow long have (?:i|you) (?:been (?:using|talking to|chatting with|speaking with)|known) (?:you|me)\b"
    r"|\bwhen did we (?:first |even )?(?:start|begin|meet|talk|chat|speak)\b" + _TOPIC_TAIL +
    r"|\bwhen was (?:our|the) first (?:conversation|chat|time we (?:talked|spoke|chatted))\b"
    r"|\bwhen did i (?:first |even )?(?:start|begin) (?:using|talking to|chatting with) you\b"
    r"|\bhow (?:old|long-?standing) is our (?:conversation|chat|history)\b",
    re.IGNORECASE,
)


def asks_how_long(message: str) -> bool:
    """Whether the user is asking how long they and Sonic have been talking."""
    return bool(_ASKS_RE.search(message or ""))


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def conversation_facts_from_db(db, username: str) -> Dict[str, Any]:
    """`{"first_at": datetime | None, "count": int}` from the saved conversations (blocking: call off the event loop)."""
    first = db["conversations"].find_one(
        {"username": username, "created_at": {"$exists": True}}, {"_id": 0, "created_at": 1}, sort=[("created_at", 1)]
    )
    return {
        "first_at": _parse((first or {}).get("created_at")),
        "count": int(db["conversations"].count_documents({"username": username})),
    }


def conversation_facts(username: str) -> Optional[Dict[str, Any]]:
    """The facts for one user, or None when they can't be read (no database that can answer, or an error). Never raises."""
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is not None:
            return conversation_facts_from_db(db, username)
        from backend.utils.app_utils import load_user_conversations

        stamps = [t for t in (_parse(c.get("created_at")) for c in load_user_conversations(username)) if t]
        return {"first_at": min(stamps) if stamps else None, "count": len(stamps)}
    except Exception:
        logger.warning("[duration] could not read the conversation history.", exc_info=True)
        return None


def month_year(moment: datetime, tz_name: Optional[str]) -> str:
    local = local_now(tz_name, moment)
    return f"{local.strftime('%B')} {local.year}"


_RULES = (
    "Say it plainly, once, as a fact, in a sentence or two. Give it no feeling and no meaning: don't say it's been wonderful, a "
    "joy or special, don't say you've enjoyed it or grown close, don't call it a milestone or an anniversary, don't count "
    "days, and don't add a promise about the future. You can only see conversations that are still saved, so say \"earliest "
    "saved conversation\", not \"since we met\" (anything they deleted is gone). Don't describe what you talked about in "
    "it, since you can't see that here.\n"
)


def build_duration_context(
    facts: Optional[Dict[str, Any]], now: datetime, tz_name: Optional[str]
) -> str:
    """The prompt section for a how-long question ("" is never returned: with no facts, Sonic is told to say it can't check)."""
    if facts is None:
        return (
            "\nHOW LONG YOU HAVE TALKED (they asked): you can't read the conversation history right now. Say so briefly "
            "and don't guess a date or a number.\n"
        )
    first_at, count = facts.get("first_at"), int(facts.get("count") or 0)
    if first_at is None:
        return (
            "\nHOW LONG YOU HAVE TALKED (fact; they asked): there are no earlier saved conversations with this person "
            "besides the current one. " + _RULES
        )
    seconds = max((now - first_at).total_seconds(), 0)
    ago = "today" if seconds < 86400 else f"{describe_gap(seconds)} ago"
    noun, verb = ("conversation", "is") if count == 1 else ("conversations", "are")
    return (
        "\nHOW LONG YOU HAVE TALKED (fact; they asked): your earliest saved conversation with this person is from "
        f"{month_year(first_at, tz_name)} ({ago}), and {count} {noun} {verb} saved in total. " + _RULES
    )
