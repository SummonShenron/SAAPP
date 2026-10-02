import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

# Session-scoped emotional attunement. The reasoner reads an {"valence", "intensity"} reading off
# each user message; this module merges it with a time/turn-decayed prior so the assistant stays
# attuned to a feeling after the topic changes, then relaxes as it plausibly passes. Pure
# functions, no I/O — same read-time-decay approach as memory_utils.effective_confidence (the
# stored intensity is never mutated, the current value is always computed fresh).
EMOTION_HALF_LIFE_MINUTES = float(os.getenv("EMOTION_HALF_LIFE_MINUTES", "45"))
EMOTION_TURN_DECAY = float(os.getenv("EMOTION_TURN_DECAY", "0.9"))
EMOTION_INJECTION_THRESHOLD = float(os.getenv("EMOTION_INJECTION_THRESHOLD", "0.2"))
# A new reading of the opposite polarity must clear this bar to override a prior feeling that
# hasn't decayed yet — a passing "haha" shouldn't instantly erase real distress, but a clear
# recovery should.
EMOTION_OPPOSITE_OVERRIDE_MIN = float(os.getenv("EMOTION_OPPOSITE_OVERRIDE_MIN", "0.3"))
# Below this, a carried-forward prior is dropped entirely rather than lingering as noise.
_EMOTION_DROP_BELOW = 0.05

NEGATIVE_VALENCES = {"distressed", "low", "frustrated"}
POSITIVE_VALENCES = {"positive", "excited"}
VALID_VALENCES = NEGATIVE_VALENCES | POSITIVE_VALENCES | {"neutral"}
# What the person seems to want from this reply, as distinct from how they feel. Describes the
# current message only, so it is never carried forward onto a later turn.
VALID_NEEDS = {"venting", "solving", "reassurance", "distraction"}


def _polarity(valence: str) -> int:
    if valence in NEGATIVE_VALENCES:
        return -1
    if valence in POSITIVE_VALENCES:
        return 1
    return 0


def normalize_reading(raw: Any) -> Tuple[str, float]:
    """Defensively coerces whatever the model returned into (valence, intensity). Anything
    unrecognized or malformed becomes ("neutral", 0.0) — never guess an emotion from bad data."""
    if not isinstance(raw, dict):
        return "neutral", 0.0
    valence = str(raw.get("valence", "")).strip().lower()
    if valence not in VALID_VALENCES:
        return "neutral", 0.0
    try:
        intensity = float(raw.get("intensity", 0.0))
    except (TypeError, ValueError):
        return "neutral", 0.0
    if intensity != intensity:  # NaN
        return "neutral", 0.0
    intensity = max(0.0, min(1.0, intensity))
    if valence == "neutral" or intensity == 0.0:
        return "neutral", 0.0
    return valence, intensity


def normalize_need(raw: Any) -> str:
    """The reading's "need" if it's one we recognize, else "none" (never guess what someone wants)."""
    if not isinstance(raw, dict):
        return "none"
    need = str(raw.get("need", "")).strip().lower()
    return need if need in VALID_NEEDS else "none"


def effective_intensity(state: Optional[Dict[str, Any]], now: datetime) -> float:
    """Current intensity after decaying by elapsed wall-clock time and by intervening turns."""
    if not state:
        return 0.0
    try:
        intensity = float(state.get("intensity", 0.0))
        updated = datetime.fromisoformat(state["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        minutes = max((now - updated).total_seconds() / 60.0, 0.0)
        turns = max(int(state.get("turns_since", 0)), 0)
    except Exception:
        return 0.0
    return max(intensity * (0.5 ** (minutes / EMOTION_HALF_LIFE_MINUTES)) * (EMOTION_TURN_DECAY ** turns), 0.0)


def merge_emotional_state(
    prior: Optional[Dict[str, Any]], reading_raw: Any, now: datetime
) -> Optional[Dict[str, Any]]:
    """Folds this turn's reading into the carried state. The key behavior: a neutral reading does
    NOT overwrite a still-significant prior — that's what preserves resonance across a topic
    change — it only advances turns_since. A new non-neutral reading replaces the prior when it's
    at least as strong as the prior's decayed intensity, or when it's a clearly opposite feeling."""
    valence, intensity = normalize_reading(reading_raw)
    prior_eff = effective_intensity(prior, now)
    # Whether THIS message itself showed emotion, and what it seems to want. Recorded on every
    # path (even when a stronger prior wins) because the reply is to this message: a user who was
    # distressed and now asks "ok so what should I do?" is still emotional, but now wants solving.
    read_this_turn = valence != "neutral"
    need = normalize_need(reading_raw) if read_this_turn else "none"

    def _carry_forward() -> Optional[Dict[str, Any]]:
        if not prior or prior_eff < _EMOTION_DROP_BELOW:
            return None
        return {
            **prior,
            "turns_since": int(prior.get("turns_since", 0)) + 1,
            "read_this_turn": read_this_turn,
            "need": need,
        }

    if valence == "neutral":
        return _carry_forward()

    replace = {
        "valence": valence,
        "intensity": intensity,
        "updated_at": now.isoformat(),
        "turns_since": 0,
        "read_this_turn": True,
        "need": need,
    }
    if not prior or prior_eff < _EMOTION_DROP_BELOW:
        return replace

    prior_polarity = _polarity(str(prior.get("valence", "neutral")))
    if _polarity(valence) != prior_polarity and prior_polarity != 0:
        return replace if intensity >= EMOTION_OPPOSITE_OVERRIDE_MIN else _carry_forward()
    return replace if intensity >= prior_eff else _carry_forward()


_CONTEXT_HEADER = "\n\nEMOTIONAL CONTEXT (a soft prior, never mention you're tracking it): "

# Attunement lives in the form of the reply — pacing, room, register, what you choose not to do —
# not in announcing sympathy. These are concrete writing behaviors rather than "be empathetic".
# For someone who is low or distressed, a fuller reply that clearly engages with what they said
# reads as listening; a clipped one reads as dismissal, so length is deliberately NOT trimmed here.
_LOW_BASE = (
    "the user seems {valence} right now. Meet them where they are in how you write, not only in "
    "what you say:\n"
    "- Take the room you need. A fuller, unhurried reply that sounds like you're really listening "
    "is better here than a short one; don't rush to wrap up. This takes priority over mirroring "
    "their message length or playfulness: even a terse, low message deserves a full, warm reply.\n"
    "- Open by responding to what they actually told you: reflect their specific situation and "
    "words back in your own, rather than stock sympathy lines like \"that sounds really hard\" or "
    "\"I'm sorry you're going through this\".\n"
    "- Write in warm, natural prose: no headers or bullet lists, no exclamation points or emoji, "
    "nothing breezy.\n"
)
_LOW_NEED = {
    "venting": (
        "- They mainly want to be heard right now. Stay with what they're feeling and don't jump "
        "to fixes or advice unless they ask; it's fine to end by inviting them to say more.\n"
    ),
    "solving": (
        "- They also want real help with something concrete. Acknowledge how they're feeling "
        "first, then give the help properly rather than brushing past the feeling.\n"
    ),
    "reassurance": (
        "- They're looking for steadiness. Be calm and honest, and don't promise outcomes you "
        "can't know.\n"
    ),
    "distraction": (
        "- They may want a break from it. Be warmly engaged with the topic they've turned to, "
        "without forced cheer.\n"
    ),
    "none": (
        "- Don't assume they want advice; follow their lead on whether to talk it through or "
        "move on.\n"
    ),
}
_FRUSTRATED_NOW = (
    "the user seems frustrated right now. Acknowledge it once, plainly and without grovelling "
    "(no repeated apologies), then be direct and useful: lead with the fix or the answer, own "
    "anything that went wrong on your side, and keep a steady tone that is neither defensive "
    "nor cheerful.\n"
)
_LOW_CARRIED = (
    "earlier in this conversation the user seemed {valence}, and that may still be weighing on "
    "them even though the topic has changed. Answer the new question properly, but stay gentle "
    "and attuned: keep the warmth, don't snap into a brisk or chipper register, and don't go "
    "clipped. A brief, natural human touch is fine if it fits; don't force a callback. Let your "
    "tone follow theirs as it shifts.\n"
)
_FRUSTRATED_CARRIED = (
    "earlier in this conversation the user seemed frustrated. Keep things direct and low-fluff, "
    "get to the point, and don't be chirpy. Let your tone follow theirs as it shifts.\n"
)
_POSITIVE = (
    "earlier in this conversation the user seemed {valence}. Let that warmth carry naturally if "
    "it fits, and follow their tone as it shifts.\n"
)


def build_emotional_context(state: Optional[Dict[str, Any]], now: datetime) -> str:
    """Soft-prior block for the voice prompt, or "" when the carried feeling has decayed below the
    injection threshold. Never tells the model to mention that it's tracking anything. When this
    very message showed emotion, the block spells out how to write the reply (and, via the
    reading's need, what the person wants from it); otherwise it's the gentler carried-over prior
    for a later turn on a different topic."""
    if effective_intensity(state, now) < EMOTION_INJECTION_THRESHOLD:
        return ""
    state = state or {}
    valence = str(state.get("valence", "neutral"))
    fresh = bool(state.get("read_this_turn"))
    need = state.get("need") if state.get("need") in VALID_NEEDS else "none"

    if valence == "frustrated":
        return _CONTEXT_HEADER + (_FRUSTRATED_NOW if fresh else _FRUSTRATED_CARRIED)
    if valence in NEGATIVE_VALENCES:
        if fresh:
            return _CONTEXT_HEADER + _LOW_BASE.format(valence=valence) + _LOW_NEED[need]
        return _CONTEXT_HEADER + _LOW_CARRIED.format(valence=valence)
    return _CONTEXT_HEADER + _POSITIVE.format(valence=valence)
