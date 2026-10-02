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

NEGATIVE_VALENCES = {"distressed", "low"}
POSITIVE_VALENCES = {"positive", "excited"}
VALID_VALENCES = NEGATIVE_VALENCES | POSITIVE_VALENCES | {"neutral"}


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

    def _carry_forward() -> Optional[Dict[str, Any]]:
        if not prior or prior_eff < _EMOTION_DROP_BELOW:
            return None
        return {**prior, "turns_since": int(prior.get("turns_since", 0)) + 1}

    if valence == "neutral":
        return _carry_forward()

    replace = {
        "valence": valence,
        "intensity": intensity,
        "updated_at": now.isoformat(),
        "turns_since": 0,
    }
    if not prior or prior_eff < _EMOTION_DROP_BELOW:
        return replace

    prior_polarity = _polarity(str(prior.get("valence", "neutral")))
    if _polarity(valence) != prior_polarity and prior_polarity != 0:
        return replace if intensity >= EMOTION_OPPOSITE_OVERRIDE_MIN else _carry_forward()
    return replace if intensity >= prior_eff else _carry_forward()


def build_emotional_context(state: Optional[Dict[str, Any]], now: datetime) -> str:
    """Soft-prior block for the voice prompt, or "" when the carried feeling has decayed below the
    injection threshold. Never tells the model to mention that it's tracking anything."""
    if effective_intensity(state, now) < EMOTION_INJECTION_THRESHOLD:
        return ""
    valence = str((state or {}).get("valence", "neutral"))
    if valence in NEGATIVE_VALENCES:
        return (
            "\n\nEMOTIONAL CONTEXT (a soft prior, never mention you're tracking it): earlier in "
            f"this conversation the user seemed {valence}, and that may still be weighing on them "
            "even if the topic has since changed. Stay gentle and attuned rather than abruptly "
            "upbeat or breezy, and let your tone follow theirs as it shifts.\n"
        )
    return (
        "\n\nEMOTIONAL CONTEXT (a soft prior, never mention you're tracking it): earlier in this "
        f"conversation the user seemed {valence}. Let that warmth carry naturally if it fits, "
        "and follow their tone as it shifts.\n"
    )
