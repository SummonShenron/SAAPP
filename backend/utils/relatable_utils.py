"""When Sonic may mention something about itself, the way a colleague sometimes does.

Humans relate by saying something about themselves when someone shares something similar. Sonic can do the honest
version of that: a habit or leaning it really has (backend/components/sonic_profile.py RELATABLE), mentioned in one
short clause, only when what the user just said genuinely overlaps with it. It can never be a story or a feeling
(backend/utils/identity_checks.py rejects "I once...", "I've been there", "I know how that feels").

This module decides WHETHER to offer one; the model decides whether it flows and may skip it. It stays quiet
whenever a quiet reply matters more:

- a safety context, or an emotional context that is still holding the energy down (the attunement rules govern);
- a closing message, which should just be closed;
- strictly grounded knowledge-base answers, which have no room for chat;
- and it is rate-limited, so it stays occasional: at most one offer every RELATABLE_MIN_GAP messages.

The rate limit reads a marker stamped on Sonic's own earlier messages (RELATABLE_MARK), so it needs no new storage.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.components.sonic_profile import RELATABLE

RELATABLE_MARK = "relatable_offered"
RELATABLE_MIN_GAP = 8  # messages (both sides) that must pass after an offer before another
_ALLOWED_ROUTES = {"conversational", "tool_output", "web"}


def _triggered(text: str, triggers: Iterable[str]) -> int:
    """How many of the triggers appear in the text as whole words or phrases."""
    lowered = (text or "").lower()
    hits = 0
    for trigger in triggers:
        if re.search(r"(?<![a-z0-9])" + re.escape(trigger.lower()) + r"(?![a-z0-9])", lowered):
            hits += 1
    return hits


def recently_offered(recent_messages: Iterable[Any], gap: int = RELATABLE_MIN_GAP) -> bool:
    """Whether Sonic offered a relatable line within the last `gap` messages."""
    window: List[Any] = list(recent_messages)[-gap:]
    return any((getattr(m, "additional_kwargs", None) or {}).get(RELATABLE_MARK) for m in window)


def select_relatable(
    user_message: str,
    recent_messages: Iterable[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """The relatable entry to offer this turn, or None. `energy_tier` is emotion_utils.energy_tier's label for this
    conversation: anything but "open" means the emotional layer is holding the energy down, so nothing is offered."""
    if risk_active or closing or energy_tier != "open" or source_type not in _ALLOWED_ROUTES:
        return None
    if recently_offered(recent_messages):
        return None
    best, best_hits = None, 0
    for entry in (RELATABLE if entries is None else entries):
        hits = _triggered(user_message, entry.get("triggers", []))
        if hits > best_hits:
            best, best_hits = entry, hits
    return best


def build_relatable_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section for an offered line ("" when none). The model may use it or skip it."""
    if not entry:
        return ""
    return (
        "\nRELATABLE (optional): if, and only if, it fits naturally, you may add ONE short clause about yourself, "
        f"tied to what they just said, along these lines: \"{entry['line']}\" Put it in your own words. Skip it if it "
        "would interrupt, repeat something you've already said, or feel forced. It is a habit of yours, not a story: "
        "never present it as something you went through.\n"
    )


# Leaning-language self-reference, for the counter of how often replies actually talk about Sonic itself.
_SELF_MENTION_RE = re.compile(
    r"\bI(?:'d| would) (?:rather|much rather|least trust)\b|\bI (?:lean|tend) (?:toward|to|on)\b|\bI(?:'m| am) (?:drawn|inclined)\b"
    r"|\bI (?:trust|lean on)\b[^.!?\n]{0,30}\b(?:least|most)\b|\bI (?:least|most) (?:trust|lean on)\b",
    re.IGNORECASE,
)


def mentions_itself(reply: str) -> bool:
    """Whether a reply states one of Sonic's own leanings (the rate worth watching, offered or not)."""
    return bool(_SELF_MENTION_RE.search(reply or ""))
