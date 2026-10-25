"""How much Sonic may reach out on its own: the person's own dial (profile "reach"), from none to more.

Four levels, each a fixed set of numbers; the default is exactly what Sonic did before the dial existed, so nobody notices a change until they move it.
  none     nothing unprompted: no open question, no leanings, no shelf items, no openers
  low      the original, quieter settings
  default  today's behaviour
  more     a little more often, never faster than the safety rules allow

The dial only changes how OFTEN. Every other gate (a heavy mood, a closing message, the avoid list, one proactive item per turn, the welcome goal's stretch,
"never twice in a row", at most 3 unanswered openers) stays on top of every level. The person's separate "start conversations" opt-in still decides whether an
opener may happen at all; "none" turns openers off too.
"""
from dataclasses import dataclass
from typing import Any, Optional

NONE, LOW, DEFAULT, MORE = "none", "low", "default", "more"
LEVELS = (NONE, LOW, DEFAULT, MORE)


@dataclass(frozen=True)
class Reach:
    level: str
    offers: bool            # whether Sonic may bring something up by itself within a conversation at all
    gap_hours: float        # the wait between two unprompted offers
    findings_per_day: int   # shelf items matched to what the person just said
    extenders: int          # unprompted offers in one conversation
    opener_hours: float     # the wait between two conversation openers (only when "start conversations" is on)


def level_of(profile: Optional[dict]) -> str:
    """The person's level. A profile saved before the dial existed only has the old yes/no switch: no stays none, anything else is the default."""
    profile = profile or {}
    explicit = profile.get("reach")
    if explicit in LEVELS:
        return explicit
    return NONE if profile.get("offers") is False else DEFAULT


def settings(level: str) -> Reach:
    """The numbers for a level. The default reads the live constants, so tuning them elsewhere still moves it."""
    from backend.utils import curiosity, initiation, reading_shelf

    if level == NONE:
        return Reach(NONE, False, curiosity.MIN_HOURS_BETWEEN, 0, 0, initiation.MIN_HOURS_BETWEEN)
    if level == LOW:
        return Reach(LOW, True, 4.0, 0, 1, 36.0)
    if level == MORE:
        return Reach(MORE, True, 1.0, 5, 2, 8.0)
    return Reach(DEFAULT, True, curiosity.MIN_HOURS_BETWEEN, reading_shelf.FINDINGS_PER_DAY, 1, initiation.MIN_HOURS_BETWEEN)


def reach_of(profile: Optional[dict]) -> Reach:
    return settings(level_of(profile))


def opener_allowed(profile: Optional[Any]) -> bool:
    return level_of(profile) != NONE
