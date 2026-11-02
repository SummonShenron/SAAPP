"""Why a turn brought nothing up on Sonic's own, so the numbers can say which gate is holding things back.

Sonic's own contributions to a reply (a relatable line, a memory callback, a reading-shelf finding, a question of its own, an open loop and the rest) each have gates of their own, and until
now only the opening-a-conversation path counted why it stayed quiet (initiation_refused_*). A turn with no contribution is counted here under the FIRST gate that applies, in the order a
person would read them: who it is, a safety context, a closing message, an emotional layer holding the energy down, a route that never carries these, the rate limit, and last
"nothing_matched": every gate was open and nothing overlapped. Counts only (backend/utils/self_counters.py proactive_quiet_*): no message, topic or person.
"""
from typing import Optional

from backend.utils.relatable_utils import ALLOWED_ROUTES


def quiet_reason(
    *, brought_something_up: bool, guest: bool, risk_active: bool, closing: bool, energy_tier: str, source_type: str, rate_limited: bool
) -> Optional[str]:
    """The proactive_quiet counter name for this turn, or None when the turn did bring something up (nothing to explain)."""
    if brought_something_up:
        return None
    if guest:
        return "guest"
    if risk_active:
        return "risk"
    if closing:
        return "closing"
    if energy_tier != "open":
        return "energy"
    if source_type not in ALLOWED_ROUTES:
        return "route"
    if rate_limited:
        return "rate_limit"
    return "nothing_matched"
