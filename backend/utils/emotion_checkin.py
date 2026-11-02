"""A later check-in on a feeling that is still being carried, once the conversation has moved on.

The emotional layer (backend/utils/emotion_utils.py) already handles the moment itself and the reply right after the topic changes: one short sentence that it hasn't been forgotten, which is never a question.
After that it only keeps a calmer register, and says nothing, for as long as the feeling is carried. That can be a long stretch of ordinary work (a coding session, say) with the energy ceiling still in force
and nobody having asked how the person is. This is the second, spaced-out touch: when enough messages have passed and the person has shown no lift, the reply ends with ONE short question about how THEY are.

Rules, all mechanical (the model only writes the sentence):
- ONLY WHILE IT IS STILL CARRIED AND NOT LIFTING. The feeling is negative (distressed or low), has not decayed below the injection threshold, and the ceiling is "subdued": the person has shown no lift since. A
  small lift, a recovery or a decayed feeling ends it, so a person who is clearly better is never asked.
- SPACED. At least CHECKIN_AFTER_TURNS of their messages since they last showed the feeling and since the last check-in, and at most CHECKIN_MAX in a stretch of 3x that. With the decay settings a strong feeling
  fades after roughly 15 messages, so this is once, maybe twice: never a refrain.
- NEVER INSIDE A SAFETY CONTEXT (that layer has its own ladder and its own check-in), on a closing message, or for a guest.
- NO NEW STATE. A check-in is marked on the reply that made it (CHECKIN_MARK), the same way a relatable line is, and the spacing reads those marks from the conversation. It is counted only when the reply really
  ended on a question to the person (asked()), so one that the model skipped is not spent.

EMOTION_CHECKIN_ENABLED=false turns it off. Counters: emotion_checkin_offered / asked / eased / still_low (backend/utils/self_counters.py), counts only.
"""
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from backend.utils.emotion_checks import reply_body
from backend.utils.emotion_utils import EMOTION_INJECTION_THRESHOLD, effective_intensity, energy_tier

CHECKIN_MARK = "emotion_checkin"
CHECKIN_AFTER_TURNS = int(os.getenv("EMOTION_CHECKIN_AFTER_TURNS", "8"))
CHECKIN_MAX = int(os.getenv("EMOTION_CHECKIN_MAX", "2"))
CHECKIN_SPAN_FACTOR = 3  # the cap applies within this many times CHECKIN_AFTER_TURNS of their messages
CHECKIN_VALENCES = ("distressed", "low")


def enabled() -> bool:
    """A server-side kill switch (EMOTION_CHECKIN_ENABLED=false)."""
    return os.getenv("EMOTION_CHECKIN_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _is_human(message: Any) -> bool:
    return str(getattr(message, "type", "") or "") == "human"


def _marked(message: Any) -> bool:
    return bool((getattr(message, "additional_kwargs", None) or {}).get(CHECKIN_MARK))


def turns_since_checkin(messages: Iterable[Any]) -> Optional[int]:
    """How many of the person's messages came after the last reply that checked in, or None when none ever did."""
    seen = 0
    for message in reversed(list(messages)):
        if _marked(message):
            return seen
        if _is_human(message):
            seen += 1
    return None


def checkins_within(messages: Iterable[Any], turns: int) -> int:
    """How many replies that checked in fall within the last `turns` of the person's messages."""
    count, seen = 0, 0
    for message in reversed(list(messages)):
        if _is_human(message):
            seen += 1
        elif seen >= turns:
            break  # a reply from before the last `turns` messages of theirs: out of the window, and so is everything older
        elif _marked(message):
            count += 1
    return count


def due(
    state: Optional[Dict[str, Any]], now: datetime, messages: Iterable[Any], *, risk_active: bool, closing: bool, guest: bool,
) -> bool:
    """Whether this reply should end with a check-in. `state` is the conversation's emotional state after this turn's reading, `messages` the conversation so far."""
    if not enabled() or guest or risk_active or closing or not state:
        return False
    if str(state.get("valence", "neutral")) not in CHECKIN_VALENCES:
        return False
    if effective_intensity(state, now) < EMOTION_INJECTION_THRESHOLD or energy_tier(state, now) != "subdued":
        return False  # decayed away, or they have shown a lift: they are not asked
    if state.get("read_this_turn") or state.get("lift_this_turn"):
        return False  # this very message is about it (or easing): the ordinary blocks handle that
    try:
        if int(state.get("turns_since", 0) or 0) < CHECKIN_AFTER_TURNS:
            return False
    except (TypeError, ValueError):
        return False
    history: List[Any] = list(messages)
    since = turns_since_checkin(history)
    if since is not None and since < CHECKIN_AFTER_TURNS:
        return False
    return checkins_within(history, CHECKIN_SPAN_FACTOR * CHECKIN_AFTER_TURNS) < CHECKIN_MAX


def build_block() -> str:
    """The prompt section for a due check-in. It says outright that it overrides the carried register's "don't bring it up", since both are in the prompt."""
    return (
        "\n\nCHECK-IN (this reply only; it overrides the line above about not bringing the painful topic up): a good number of messages have passed since they last showed that feeling, and nothing they "
        "have said since has shown it lifting, so one gentle check-in is due. First answer what they actually asked, fully, the way you normally would. Then end the reply with ONE short, warm "
        "question about how THEY are doing now, in your own words and specific to what they went through earlier (never a generic \"how are you feeling?\" or \"is there anything else?\"). Rules:\n"
        "- It comes at the very end of the reply and is the only question in the closing lines; a few words after it that make it easy to skip are fine.\n"
        "- A few words of what happened is enough; do not recap it.\n"
        "- Make it easy to wave off, so it never feels like a demand for an answer.\n"
        "- No advice, no hotline or resources, no stock comfort lines (\"I'm here for you\", \"I've got you\").\n"
        "- If the reply is a result from tools or code, the work comes first and the check-in comes after it.\n"
    )


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# What a question about how the person is sounds like ("how are you holding up", "how has the rest of your day been"). Wording about them AND about how they are: a question that only says "you" ("can you share your
# config?", "does that make sense to you?") is about the work, so it is never counted as a check-in.
_ABOUT_THEM = re.compile(
    r"\b(?:how are you|how(?:'s| is| has| have| was| did) (?:your|you|the rest|it been|everything|that sit)|holding up|doing (?:now|okay|ok|alright|better|right now)|"
    r"are you (?:ok|okay|alright|doing)|you (?:ok|okay|alright)\b|feeling (?:now|any|a bit|better)|weighing on you|sitting with you|(?:rest of )?your (?:day|week|morning|afternoon|evening))\b",
    re.IGNORECASE,
)
_ABOUT_THE_WORK = re.compile(r"\b(?:want me to|should i|shall i|would you like me|do you want me|need me to|like me to)\b", re.IGNORECASE)


MAX_SKIP_WORDS = 25  # a closing line after the question ("No need to go into it if you'd rather just stick to the code.") may be this long: sampled replies from the real model ran 9 to 22 words


def _to_them(sentence: str) -> bool:
    return sentence.endswith("?") and bool(_ABOUT_THEM.search(sentence)) and not _ABOUT_THE_WORK.search(sentence)


def asked(reply: str) -> bool:
    """Whether a reply ended on a question to the person about themselves (not "want me to run the tests?"), optionally followed by one short line that makes it easy to skip. Only then is a
    check-in counted and marked as made."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(reply_body(reply or "")) if s.strip()]
    if not sentences:
        return False
    if _to_them(sentences[-1]):
        return True
    return len(sentences) >= 2 and _to_them(sentences[-2]) and not sentences[-1].endswith("?") and len(sentences[-1].split()) <= MAX_SKIP_WORDS
