"""Asking someone who is having a hard time what they want from Sonic (just listen, advice, or help fixing it), instead of guessing, and keeping their answer for the rest of that stretch.

WHY (Jack, 2026-11-23). The reasoner reads a hidden "need" from every message (venting, solving...), and the reply follows the guess. A friend asks. The coding tool loop already has a choice card
(backend/services/choice_cards.py), but that card pauses a turn that is waiting; a conversational reply has nothing waiting. So here the reply is written first, in full, and the card comes at the end;
a tap sends the option as the person's next message, carrying which option it was (`support_choice` on the chat request), and that sets the mode.

WHEN THE CARD IS OFFERED (`should_offer`, all in code):
- this message was read as low, distressed or frustrated, at least OFFER_MIN_INTENSITY, and not a small lift;
- they have not already said what they want ("I just need to vent", "what should I do?");
- no mode is in force and no card was offered in the last MODE_HOURS;
- the page can show it, it is a conversational turn, not a closing message, and never under a safety plan (the support ladder has its own flow).

WHAT A MODE DOES (`mode_block`): "listen" stays with them (no advice, at most one gentle question), "advice" gives an honest take and one or two suggestions, "fix" gets practical with small next
steps. It lasts MODE_HOURS from the pick, and they can change it any time by saying so (the block says to follow that). The pick is a mark on their own message, saved with the conversation, so
nothing else is stored.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils import view_check
from backend.utils.emotion_utils import NEGATIVE_VALENCES
from backend.utils.time_utils import sent_at

MODE_MARK = "support_mode"          # on their message: the option they picked ("listen", "advice", "fix")
CARD_MARK = "support_card"          # on Sonic's reply: a card was shown with it
MODE_HOURS = 6
LISTEN_TURNS = 10            # how many of their messages "just listen" holds for after the tap
OFFER_MIN_INTENSITY = 0.35
QUESTION = "What would help most right now?"
OPTIONS = (
    ("listen", "Just listen", "Stay with it with me. No advice or fixing."),
    ("advice", "Give me advice", "Tell me what you honestly think and what you'd consider."),
    ("fix", "Help me fix it", "Get practical: small next steps, maybe a plan."),
)
KEYS = tuple(key for key, _label, _description in OPTIONS)
# They have already said what they want: no card.
STATED_RE = re.compile(
    r"\b(?:just|only)\s+(?:need|want(?:ed)?)\s+to\s+(?:vent|talk|rant)\b|\bneed\s+to\s+vent\b|\blet me vent\b|\bjust\s+(?:listen|be here)\b|\bdon'?t\s+(?:want|need)\s+(?:advice|solutions|you to fix)\b"
    r"|\bwhat\s+(?:should|do|would|can)\s+i\s+do\b|\bwhat\s+would\s+you\s+do\b|\bany\s+(?:advice|ideas|suggestions)\b|\bhow\s+(?:do|can|should)\s+i\s+(?:fix|handle|deal\s+with|get\s+past|stop)\b"
    r"|\bgive me (?:advice|your take|your honest)\b|\bhelp me (?:fix|figure|plan|decide)\b|\bwhat do you think i should\b",
    re.IGNORECASE,
)


def stated_what_they_want(message: str) -> bool:
    return bool(STATED_RE.search(message or ""))


def current_mode(messages: Iterable[Any], now: Optional[datetime] = None) -> Optional[str]:
    """The mode they picked in the last MODE_HOURS (the newest pick wins), or None. Pure apart from the clock."""
    moment = now or datetime.now(timezone.utc)
    ordered = list(messages or [])
    for message in reversed(ordered):
        mode = (getattr(message, "additional_kwargs", None) or {}).get(MODE_MARK)
        if getattr(message, "type", "") != "human" or mode not in KEYS:
            continue
        when = sent_at(message)
        if when is None or moment - when > timedelta(hours=MODE_HOURS):
            return None
        if mode == "listen":
            # "Just listen" answers the stretch it was tapped in, not the next six hours of conversation (2026-11-27: 4 taps covered 78 of 313 conversational turns). It ends after
            # LISTEN_TURNS of their messages, and it steps aside for a message that asks Sonic what it thinks: that is them saying they want something different.
            later = [m for m in ordered[ordered.index(message) + 1:] if getattr(m, "type", "") == "human"]
            if len(later) > LISTEN_TURNS or (later and view_check.asks_for_view(str(getattr(later[-1], "content", "") or ""))):
                return None
        return mode
    return None


def card_offered_recently(messages: Iterable[Any], now: Optional[datetime] = None) -> bool:
    """True when one of Sonic's replies in the last MODE_HOURS carried a card. Pure apart from the clock."""
    moment = now or datetime.now(timezone.utc)
    for message in reversed(list(messages or [])):
        if getattr(message, "type", "") == "ai" and (getattr(message, "additional_kwargs", None) or {}).get(CARD_MARK):
            when = sent_at(message)
            if when is not None and moment - when <= timedelta(hours=MODE_HOURS):
                return True
    return False


def should_offer(emotional_state: Any, message: str, messages: Iterable[Any], *, page_can_show: bool, conversational: bool, closing: bool, safety_active: bool,
                 now: Optional[datetime] = None) -> bool:
    """Whether this reply ends with the card (see the module docstring). Pure apart from the clock."""
    if not page_can_show or not conversational or closing or safety_active or stated_what_they_want(message):
        return False
    state = emotional_state if isinstance(emotional_state, dict) else {}
    try:
        upset = (bool(state.get("read_this_turn")) and not state.get("lift_this_turn") and str(state.get("valence")) in NEGATIVE_VALENCES
                 and float(state.get("intensity", 0.0)) >= OFFER_MIN_INTENSITY)
    except (TypeError, ValueError):
        upset = False
    if not upset:
        return False
    history = list(messages or [])
    return current_mode(history, now) is None and not card_offered_recently(history, now)


def card() -> Dict[str, Any]:
    """The card for the page: the same shape as a tool-loop choice card, with `mode` "message" (a tap is sent as their next message) and each option's `key`. Pure apart from the new id."""
    return {
        "choice_id": uuid.uuid4().hex, "question": QUESTION, "mode": "message", "kind": "support",
        "options": [{"key": key, "label": label, "description": description, "recommended": False} for key, label, description in OPTIONS],
    }


OFFER_NOTE = (
    "\nA CARD WILL FOLLOW YOUR REPLY asking what would help most: just listen, advice, or help fixing it. So write your reply to what they said, in full, and do not end with a question of "
    "your own or ask what they want from you: the card asks that. Do not mention the card.\n"
)

_MODE_TEXT = {
    "listen": (
        "they asked you to JUST LISTEN. Stay with what they say and how it feels for them. No advice, suggestions, fixes, plans or silver linings. Listening is not saying nothing, though: "
        "you may name exactly what is hard about it (not a summary of it), bring in something they told you before that bears on it, or thank them for telling you something hard. Short is "
        "fine. At most one gentle question, and often none."
    ),
    "advice": (
        "they asked for ADVICE. Give your honest take: what you see in what they described and what you would consider, with one or two concrete suggestions grounded in what they told you "
        "and their own history. Stay kind, say it plainly, and do not lecture or pile on."
    ),
    "fix": (
        "they asked for help FIXING IT. Get practical: name the problem in a line, then small, doable next steps (a short plan if it helps). Ask only what you need to make the plan work."
    ),
}


def mode_block(mode: Optional[str]) -> str:
    """The reply prompt's section for the mode in force, or "". Pure."""
    if mode not in _MODE_TEXT:
        return ""
    return (
        f"\nWHAT THEY WANT FROM YOU RIGHT NOW (they chose it): {_MODE_TEXT[mode]} This holds until they say they want something different; if they do, follow that instead.\n"
    )


def adjust_issue(issue: Optional[str], mode: Optional[str], reply: str, safety_active: bool) -> Optional[str]:
    """The emotional check's verdict, adjusted to what they chose: with "advice" or "fix" chosen, giving advice is not a fault ("venting_advice" is dropped); with "listen" chosen, a reply that
    gives advice is one ("venting_advice"), even when this message was not read as venting. Unchanged under a safety plan or with no mode. Pure."""
    if safety_active or mode not in KEYS:
        return issue
    if mode in ("advice", "fix") and issue == "venting_advice":
        return None
    if mode == "listen" and issue is None:
        from backend.utils.emotion_checks import reply_gives_advice

        return "venting_advice" if reply_gives_advice(reply) else None
    return issue


def counts_for_pick(key: Optional[str]) -> Dict[str, int]:
    """The counter for a pick (self_counters SUPPORT_KEYS). Pure."""
    return {f"support_choice_{key}": 1} if key in KEYS else {}
