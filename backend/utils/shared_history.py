"""Callbacks: Sonic drawing a line from something the user told it earlier to what they are saying now.

Memory already puts relevant past context in front of the model (backend/services/memory_search.py), but passively: "weave
it in if it fits". The difference between an assistant that has facts and one that has a shared history with you is the
explicit connection, made once and lightly: "this is the same shape as the retry problem you sorted out a few weeks back".

This module decides WHETHER to invite one. It never invents anything: the connection can only be to context that was really
recalled this turn, the model is told to skip it unless the link is genuine, and it may never recite what it knows, mention
memory or notes, or say it was thinking about it. It stays quiet whenever a quiet reply matters more (a safety context, a
mood the emotional layer is holding down, a closing message, another proactive item already in this turn, or a recent
callback), and it is rate-limited by a marker on Sonic's own earlier messages.
"""
from typing import Any, Iterable, List

CALLBACK_MARK = "callback_offered"
CALLBACK_MIN_GAP = 10  # messages (both sides) that must pass after a callback before another
_ROUTES = {"conversational"}


def recently_offered(recent_messages: Iterable[Any], gap: int = CALLBACK_MIN_GAP) -> bool:
    window: List[Any] = list(recent_messages)[-gap:]
    return any((getattr(m, "additional_kwargs", None) or {}).get(CALLBACK_MARK) for m in window)


def callback_allowed(
    recalled: bool,
    recent_messages: Iterable[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
    proactive_taken: bool = False,
) -> bool:
    if not recalled or risk_active or closing or energy_tier != "open" or proactive_taken:
        return False
    if source_type not in _ROUTES:
        return False
    return not recently_offered(recent_messages)


def build_callback_block() -> str:
    return (
        "\nCALLBACK (optional): the recalled context above comes from earlier conversations. If, and only if, one item "
        "genuinely connects to what they're saying now, you may draw that connection once, in a sentence or less, the way a "
        "colleague would (\"this is the same shape as the retry problem you sorted out a few weeks back\"). Use the dates "
        "only as loosely as they're given. Never list or recite what you know about them, never mention memory, records or "
        "notes, never say you were thinking about it, and skip it entirely if the link is a stretch or would pull them away "
        "from what they asked.\n"
    )
