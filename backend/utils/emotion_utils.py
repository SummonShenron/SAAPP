import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

# Session-scoped emotional attunement. The reasoner reads an {"valence", "intensity"} reading off
# each user message; this module merges it with a time/turn-decayed prior so the assistant stays
# attuned to a feeling after the topic changes, then relaxes as it plausibly passes. Pure
# functions, no I/O — same read-time-decay approach as memory_utils.effective_confidence (the
# stored intensity is never mutated, the current value is always computed fresh).
#
# Defaults are deliberately slow: a real upset (a breakup, a bad diagnosis) is not over in ten
# messages or half an hour, and measured against the real reasoner the previous 45 min / 0.9 per
# turn dropped acute distress below the injection threshold after about ten messages.
EMOTION_HALF_LIFE_MINUTES = float(os.getenv("EMOTION_HALF_LIFE_MINUTES", "90"))
EMOTION_TURN_DECAY = float(os.getenv("EMOTION_TURN_DECAY", "0.93"))
EMOTION_INJECTION_THRESHOLD = float(os.getenv("EMOTION_INJECTION_THRESHOLD", "0.2"))
# A new reading of the opposite polarity must clear this bar to override a prior feeling that
# hasn't decayed yet — a passing "haha" shouldn't instantly erase real distress, but a clear
# recovery should.
EMOTION_OPPOSITE_OVERRIDE_MIN = float(os.getenv("EMOTION_OPPOSITE_OVERRIDE_MIN", "0.3"))
# Below this, a carried-forward prior is dropped entirely rather than lingering as noise.
_EMOTION_DROP_BELOW = 0.05
# The reply right after the topic changes gets one short, specific acknowledgement that the feeling
# hasn't been forgotten; every reply after that only keeps the calmer register (acknowledging it
# again and again reads as nagging).
EMOTION_TOUCH_MAX_TURNS = int(os.getenv("EMOTION_TOUCH_MAX_TURNS", "1"))
_GIST_MAX_CHARS = 120

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


def normalize_gist(raw: Any) -> str:
    """A short note of WHAT the person is going through, as read off their own words ("a breakup;
    Tina ended things tonight"). The prompt only ever sees the last few messages, so without this
    the carried feeling was just a mood label with no content to stay attuned *to*. Bounded and
    flattened to a single line before it goes anywhere near a prompt."""
    if not isinstance(raw, dict):
        return ""
    gist = raw.get("gist")
    if not isinstance(gist, str):
        return ""
    gist = re.sub(r"\s+", " ", gist).strip().strip("\"'")
    return gist[:_GIST_MAX_CHARS].rstrip()


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
    gist = normalize_gist(reading_raw) if read_this_turn else ""

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
        "gist": gist,
    }
    if not prior or prior_eff < _EMOTION_DROP_BELOW:
        return replace

    prior_polarity = _polarity(str(prior.get("valence", "neutral")))
    # Same kind of feeling continuing but this message gave no fresh description: keep what we knew
    # it was about rather than forgetting the cause the moment the model stops restating it.
    if not gist and _polarity(valence) == prior_polarity:
        replace["gist"] = str(prior.get("gist") or "")
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
# Carried onto a later, unrelated question. Measured against the real model, an abstract "stay
# gentle and attuned" had NO visible effect on the reply (same "let's dive in", same bold bullet
# lists as with no emotional context at all), so these name the concrete behaviors instead, and say
# outright that they outrank the persona's match-their-energy rule. {about} is the reasoner's gist.
_LOW_CARRIED_OUTRANKS = (
    "This takes priority over the guidance in your voice description about matching their energy "
    "or being playful, expressive, or upbeat.\n"
)
_LOW_CARRIED_TOUCH = (
    "earlier in this conversation the user seemed {valence}; they opened up about something painful{about}. They've now "
    "moved on to a different question, but that doesn't mean the feeling is gone; it may still be "
    "weighing on them. " + _LOW_CARRIED_OUTRANKS +
    "For this reply, stay gentle and steady:\n"
    "- Begin with ONE short sentence, light and specific to what happened (a handful of words, "
    "not a recap of the story), that shows it hasn't slipped your mind and that you're glad to "
    "help with something else. It must not be a question. Never use stock comfort lines such as "
    "\"I'm here for you\", \"I'm right here with you\", \"I've got you\" or \"take your mind "
    "off\". Skip this sentence only if your previous reply in the history already did something "
    "similar.\n"
    "- Then answer the actual question fully and well, in a calm, steady register: no \"let's dive "
    "in\", no exclamation marks, no emoji, no cheerleading.\n"
    "- Prefer plain, warm prose over bullet lists and bold headings unless the content truly needs "
    "a list.\n"
    "- Don't bring the painful topic up again beyond that single sentence; they chose to move on, "
    "so follow them.\n"
)
_LOW_CARRIED_REGISTER = (
    "earlier in this conversation the user seemed {valence} after opening up about something painful{about}, and it may "
    "still be weighing on them even though they've moved on to other things. " + _LOW_CARRIED_OUTRANKS +
    "Stay gentle and steady without making anything of it: a calm, warm register, no \"let's dive "
    "in\", no exclamation marks, no emoji, no cheerleading, plain prose over bullet lists unless "
    "the content truly needs one. Don't bring the painful topic up; just let the steadiness show. "
    "If they return to it or their mood changes, follow them.\n"
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
        gist = str(state.get("gist") or "").strip()
        about = f" ({gist})" if gist else ""
        turns = int(state.get("turns_since", 0) or 0)
        block = _LOW_CARRIED_TOUCH if turns <= EMOTION_TOUCH_MAX_TURNS else _LOW_CARRIED_REGISTER
        return _CONTEXT_HEADER + block.format(valence=valence, about=about)
    return _CONTEXT_HEADER + _POSITIVE.format(valence=valence)
