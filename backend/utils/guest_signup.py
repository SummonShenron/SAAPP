"""Counts the sign-up buttons a guest presses (docs/guest-mode-plan.md, step 4).

The guest pages have five places that say "Create a free account": the reply nudge under an answer, the account-lane card, the allowance line above the message box, the refusal at the limit, and the demo project's
diff card. Without counting them there is no way to tell whether a post, a card or a nudge produces sign-ups. This records WHICH button was pressed and HOW MANY free turns the visitor had used that day when they
pressed it, and nothing else: no message, no name, no address (backend/utils/self_counters.py `guest_signup` and `guest_signup_after`; counts only).

Only the shared portfolio guest is counted, only a known button name is counted, and a failure never reaches the page: this must not be able to get in the way of someone signing up.
"""
import logging
from typing import Any, Optional

from backend.utils import guest_limits
from backend.utils.self_counters import record_turn_counts, tally

logger = logging.getLogger("SASS Logger")

# Which button. The prefix says where it is: `card_` the account-lane card shown before any chat, `nudge_` the card under a reply (both end in the account feature, the same words as
# backend/utils/guest_nudge.py FEATURES), `banner` the allowance line, `limit` the refusal at the limit, `demo_diff` the demo project's diff card.
FEATURES = ("checks", "fix", "tidy", "memory")
SOURCES = ("banner", "limit", "demo_diff") + tuple(f"card_{f}" for f in FEATURES) + tuple(f"nudge_{f}" for f in FEATURES)

# How many of the visitor's free turns had been used that day (the same count the allowance line shows).
BUCKETS = ("none", "one_or_two", "three_to_five", "six_plus")


def bucket_of(turns: Any) -> str:
    """The bucket for a number of turns used. Anything that is not a count reads as none. Pure."""
    try:
        used = int(turns)
    except (TypeError, ValueError):
        used = 0
    if used <= 0:
        return "none"
    if used <= 2:
        return "one_or_two"
    if used <= 5:
        return "three_to_five"
    return "six_plus"


def clean_source(raw: Any) -> Optional[str]:
    """The button name when it is one of the known ones, else None. Pure."""
    return raw if isinstance(raw, str) and raw in SOURCES else None


def count_click(db: Any, username: Optional[str], raw_source: Any, visitor_id: Optional[str], address: str) -> Optional[str]:
    """Counts one press and returns the button counted, or None when nothing was (not the shared guest, or not a known button). Blocking (reads the allowance, writes a counter). Never raises."""
    try:
        if not guest_limits.is_portfolio_guest(username):
            return None
        source = clean_source(raw_source)
        if source is None:
            return None
        used = guest_limits.allowance(db, visitor_id, address).get("used", 0)
        counts: dict = {}
        tally(counts, "guest_signup", source)
        tally(counts, "guest_signup_after", bucket_of(used))
        record_turn_counts(counts)
        return source
    except Exception:
        logger.warning("[guest_signup] could not count a sign-up press.")
        return None
