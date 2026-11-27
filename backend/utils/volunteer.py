"""Once in a while Sonic brings up something of its own without being asked.

WHY (2026-11-27). Everything of its own was matched against what the person had just said, so material about its own interests never surfaced in conversations about anything else: its 11
approved takes on things it read were all live, and were offered 0 times in 35 days. An entity with a reading shelf mentions what it read now and then, unprompted, and then lets it go.

WHAT. At most one a day per conversation, at an open moment, the reply ends with one take from its reading shelf (backend/utils/takes.py: approved by a person, and its piece still live on
the shelf), placed by code after a short lead-in, word for word. A take is Sonic's own and shared by everyone: it comes from a public page it read, never from anyone's conversation. A take
this conversation has already heard is not said again.

WHEN (`may`). A conversational turn with the turn's slot free, no safety context, an open mood, not a closing message, the person's offers switch on and the reach dial not at "none", and a
short message that asks for nothing (no question, at most MAX_MESSAGE_WORDS words): never on top of a request, an answer they are waiting for, or anything heavy. The last one must be at
least GAP_HOURS old (read from the saved marks on Sonic's replies).

The mark on the reply (MARK) holds the take's id. Pure, no model call, nothing logged.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils.time_utils import sent_at

MARK = "volunteered"
GAP_HOURS = 20
MAX_MESSAGE_WORDS = 30
LEAD_IN = "Something from my own reading, apart from all this:"


def said_ids(messages: Iterable[Any]) -> List[str]:
    """The ids of the takes this conversation has already heard, oldest first. Pure."""
    return [str((getattr(m, "additional_kwargs", None) or {}).get(MARK)) for m in messages or []
            if getattr(m, "type", "") == "ai" and (getattr(m, "additional_kwargs", None) or {}).get(MARK)]


def recently(messages: Iterable[Any], now: Optional[datetime] = None, gap_hours: float = GAP_HOURS) -> bool:
    """True when one of Sonic's replies in the last `gap_hours` volunteered something. A marked reply with no time on it counts as recent (an unknown time is never guessed). Pure apart from the clock."""
    moment = now or datetime.now(timezone.utc)
    for message in reversed(list(messages or [])):
        if getattr(message, "type", "") == "ai" and (getattr(message, "additional_kwargs", None) or {}).get(MARK):
            when = sent_at(message)
            return when is None or moment - when < timedelta(hours=gap_hours)
    return False


def may(message: str, messages: Iterable[Any], *, source_type: str, risk_active: bool, energy_tier: str, closing: bool, proactive_taken: bool, offers_allowed: bool, reach: str,
        now: Optional[datetime] = None) -> bool:
    """Whether this turn is an open moment for it. Pure apart from the clock."""
    text = str(message or "").strip()
    if source_type != "conversational" or risk_active or closing or proactive_taken or energy_tier != "open" or not offers_allowed or reach == "none":
        return False
    if not text or "?" in text or len(text.split()) > MAX_MESSAGE_WORDS:
        return False
    return not recently(messages, now)


def pick(takes: List[Dict[str, Any]], messages: Iterable[Any]) -> Optional[Dict[str, Any]]:
    """The first live take this conversation has not heard, or None when it has heard them all. Pure."""
    heard = set(said_ids(messages))
    return next((take for take in takes or [] if take.get("id") and take.get("line") and take["id"] not in heard), None)


def spoken(take: Dict[str, Any]) -> str:
    """The words placed at the end of the reply: the lead-in and the take's own approved line. Pure."""
    return f"{LEAD_IN} {str(take.get('line') or '').strip()}"
