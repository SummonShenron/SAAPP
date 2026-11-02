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
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from backend.components.sonic_profile import INTERESTS, RELATABLE
from backend.utils.reading_shelf import cosine

RELATABLE_MARK = "relatable_offered"
RELATABLE_MIN_GAP = 8  # messages (both sides) that must pass after an offer before another
ALLOWED_ROUTES = frozenset({"conversational", "tool_output", "web"})

# A message with no trigger word can still overlap an entry in meaning: the turn's own question embedding (already computed for memory) is compared with each entry's. Calibrated 2026-11-01 on 28 invented
# messages against the ten entries (entry text = line + its trigger words): related messages scored 0.58-0.75, unrelated ones, including things said to Sonic about its own work, topped out at 0.62
# ("run the tests and tell me what failed" against find_the_commit: 0.616). Set high on purpose: it is better to miss a link than to force one.
RELATABLE_MEANING_MIN = 0.64
MEANING_RETRY_SECONDS = 300  # after an entry failed to embed, the next attempt waits this long, so a service that is down is not called every turn
_vectors: Dict[str, List[float]] = {}  # entry text -> vector, kept for the life of the process
_last_attempt = 0.0


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


def entry_text(entry: Dict[str, Any]) -> str:
    """What an entry is embedded as: its line and the words that point at it (that mix separated related from unrelated messages best)."""
    return f"{entry['line']} ({', '.join(entry.get('triggers', []))})"


def entry_vectors(embed: Callable[[str], Optional[List[float]]], entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, List[float]]:
    """{entry id: vector} for every entry that has one. Blocking (a network call per entry not yet embedded), so call it from a worker thread. Each text is embedded once per process;
    one that failed is tried again no sooner than MEANING_RETRY_SECONDS later. Never raises."""
    global _last_attempt
    entries = RELATABLE + INTERESTS if entries is None else entries
    missing = [entry for entry in entries if entry_text(entry) not in _vectors]
    if missing and time.monotonic() - _last_attempt >= MEANING_RETRY_SECONDS:
        _last_attempt = time.monotonic()
        for entry in missing:
            try:
                vector = embed(entry_text(entry))
            except Exception:
                vector = None
            if vector:
                _vectors[entry_text(entry)] = vector
    return {entry["id"]: _vectors[entry_text(entry)] for entry in entries if entry_text(entry) in _vectors}


def _closest_in_meaning(vector: List[float], entries: List[Dict[str, Any]], vectors: Dict[str, List[float]]) -> Optional[Dict[str, Any]]:
    scored = [(cosine(vector, vectors[entry["id"]]), entry) for entry in entries if vectors.get(entry["id"])]
    if not scored:
        return None
    score, entry = max(scored, key=lambda pair: pair[0])
    return entry if score >= RELATABLE_MEANING_MIN else None


def select_relatable(
    user_message: str,
    recent_messages: Iterable[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
    entries: Optional[List[Dict[str, Any]]] = None,
    vector: Optional[List[float]] = None,
    vectors: Optional[Dict[str, List[float]]] = None,
) -> Optional[Dict[str, Any]]:
    """The relatable entry to offer this turn, or None. `energy_tier` is emotion_utils.energy_tier's label for this
    conversation: anything but "open" means the emotional layer is holding the energy down, so nothing is offered.
    A trigger word wins. Without one, and given the message's embedding (`vector`) and the entries' (`vectors`, from entry_vectors), the entry closest in meaning is
    offered if it clears RELATABLE_MEANING_MIN; that one comes back as a copy marked {"match": "meaning"} so it can be counted."""
    if risk_active or closing or energy_tier != "open" or source_type not in ALLOWED_ROUTES:
        return None
    if recently_offered(recent_messages):
        return None
    pool = RELATABLE + INTERESTS if entries is None else entries
    best, best_hits = None, 0
    for entry in pool:
        hits = _triggered(user_message, entry.get("triggers", []))
        if hits > best_hits:
            best, best_hits = entry, hits
    if best is None and vector and vectors:
        closest = _closest_in_meaning(vector, pool, vectors)
        if closest:
            return {**closest, "match": "meaning"}
    return best


def build_relatable_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section for an offered line ("" when none). The model may use it or skip it."""
    if not entry:
        return ""
    return (
        "\nRELATABLE (optional): if, and only if, it fits naturally, you may add ONE short clause about yourself, "
        f"tied to what they just said, along these lines: \"{entry['line']}\" Put it in your own words. Skip it if it "
        "would interrupt, repeat something you've already said, or feel forced. It is a habit or leaning of yours, not a story or an activity: "
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
