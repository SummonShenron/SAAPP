import re
from typing import Any, Dict, Optional, Tuple

from backend.utils.emotion_utils import NEGATIVE_VALENCES, POSITIVE_VALENCES

# Mechanical checks that a finished reply actually honored the emotional need detected for it. The
# guidance in emotion_utils is only prose, and prose loses to a model's default behavior under
# pressure (the same lesson as every other prose-only rule in this codebase): "venting" gets detected
# correctly and advice slips in anyway, "celebration" gets enthusiasm with no curiosity. These are
# cheap pattern checks, no extra model call, run on the draft before it is accepted.
#
# They are deliberately conservative about what counts as a failure, since a failure costs a
# regeneration: a venting reply fails only on clearly advice-shaped phrasing, and a celebration reply
# fails only when it has no genuine question or invitation at all.

# Tuned against real replies: "the basic respect you should be able to expect" (descriptive), "you do
# not need to try to solve this" (a reassurance, not a suggestion) and "here's what I'm hearing" are all
# listening, not advice, and an earlier version flagged each of them.
_NOT_BE_DESCRIPTIVE = r"(?! (?:be able|be |feel|never|not|have|expect|always be))"
_SOFT_LEAD = r"(?:^|(?<=[.!?:;] )|(?<=maybe )|(?<=perhaps )|(?<=just )|(?<=please )|(?<=also )|(?<=then )|(?<=and ))"
ADVICE_PATTERNS = [
    r"\byou (?:really )?(?:should|ought to)\b" + _NOT_BE_DESCRIPTIVE,
    r"\byou (?:could|might want to|may want to|'ll want to) (?:try|consider|look into|think about|reach out|talk to|speak to|take|do|start|get)\b",
    r"\bhave you (?:tried|considered|thought about|looked into)\b",
    r"\bi(?:'d| would)? (?:suggest|recommend|encourage you to|advise)\b",
    r"\b(?:it )?(?:might|could|may|can) help (?:to|if)\b",
    # an imperative "try to ...", not "you do not need to try to ..."
    _SOFT_LEAD + r"(?:try|consider) (?:to |taking |talking |reaching |doing |journaling |going |writing |getting |setting |making )",
    r"\bone (?:thing|step) (?:you can|to try|that might)\b",
    r"\b(?:some|a few|several) (?:tips|ideas|strategies|suggestions|things (?:that )?(?:might|can|could) help)\b",
    r"\bhere(?:'s| is| are) (?:a few|some) (?:tips|ideas|things|ways|steps|suggestions|strategies)\b",
]
_ADVICE_RE = re.compile("|".join(ADVICE_PATTERNS), re.IGNORECASE | re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.MULTILINE)

# A question that would fit any good news and shows no actual interest in this one.
GENERIC_QUESTION_PATTERNS = [
    r"how (?:are|do) you (?:feeling|feel)",
    r"how (?:does|did|do) (?:it|that|this) (?:feel|make you feel)",
    r"how (?:'s|is|are) (?:that|it|everything|things) (?:going|feeling|treating)",
    r"how are you doing",
    r"what do you think",
    r"are you (?:excited|nervous|happy|thrilled|pumped)",
    r"is there anything else",
    r"anything else (?:on your mind|you want to)",
]
_GENERIC_Q_RE = re.compile("|".join(GENERIC_QUESTION_PATTERNS), re.IGNORECASE)
# Curiosity expressed as an invitation rather than a question mark ("send me a pic!").
_INVITATION_RE = re.compile(
    r"\b(?:show me|send me|share (?:a|the|their|your)|post (?:a|the)|i(?:'d| would) love to (?:see|hear)|"
    r"i want to (?:see|hear)|let me see|dying to (?:see|hear|know))\b",
    re.IGNORECASE,
)


_FOLLOW_UP_RE = re.compile(r"<<<FOLLOW_UP:.*?>>>", re.DOTALL)


def reply_body(text: str) -> str:
    """The reply without its <<<FOLLOW_UP: ...>>> tag. The UI renders that tag as a clickable
    suggested next message for the user, so a question inside it is not the assistant being curious
    (the baseline Hinge reply "That's so exciting! Tell me everything." carried its only question
    there), and advice-shaped words inside it are not advice the assistant gave."""
    return _FOLLOW_UP_RE.sub("", text).strip()


def reply_gives_advice(text: str) -> bool:
    """True when a reply reads as advice or suggestions: advice-shaped phrasing, or a list of items."""
    text = reply_body(text)
    if _ADVICE_RE.search(text):
        return True
    return len(_LIST_ITEM_RE.findall(text)) >= 2


def _sentences(text: str):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def reply_shows_curiosity(text: str) -> bool:
    """True when a reply asks at least one question that is not a generic feelings question, or
    explicitly invites the person to share or show something. A bare "tell me more" does not count:
    it is the generic version of curiosity."""
    text = reply_body(text)
    if _INVITATION_RE.search(text):
        return True
    for sentence in _sentences(text):
        if sentence.endswith("?") and not _GENERIC_Q_RE.search(sentence):
            return True
    return False


_REVISION_REASONS = {
    "venting_advice": (
        "the person mainly wants to be heard right now, and the draft gave advice or suggestions. Write it "
        "again so it stays with what they are feeling and what they actually said: no advice, tips, "
        "suggestions or lists. It is fine to end by inviting them to say more."
    ),
    "celebration_no_question": (
        "the person shared exciting news and the draft was warm but showed no real curiosity about it. "
        "Write it again keeping the enthusiasm, and ask one specific, genuinely curious question about "
        "the news itself (not a generic \"how are you feeling about it?\"), written into the reply itself and not "
        "only into a follow-up tag; if it is something they could show you, invite them to."
    ),
}


def emotional_reply_issue(state: Optional[Dict[str, Any]], reply: str) -> Optional[str]:
    """The tag of the emotional need this reply failed to honor, or None when it is fine (or when
    nothing here applies). Only a need read from THIS message is checked: a feeling carried over from
    earlier turns doesn't oblige the reply to anything in particular."""
    if not state or not reply or not state.get("read_this_turn") or state.get("lift_this_turn"):
        return None
    need = state.get("need")
    valence = str(state.get("valence", "neutral"))
    if need == "venting" and valence in NEGATIVE_VALENCES and reply_gives_advice(reply):
        return "venting_advice"
    if need == "celebration" and valence in POSITIVE_VALENCES and not reply_shows_curiosity(reply):
        return "celebration_no_question"
    return None


def build_emotional_revision_prompt(original_prompt: str, draft: str, issue: str) -> Tuple[str, str]:
    """(prompt, reason) for a single regeneration: the original prompt plus a note naming what the
    draft got wrong, with the draft itself so the good parts can be kept."""
    reason = _REVISION_REASONS[issue]
    note = (
        "\n\nREVISION NOTE (never mention this note or that you are revising): your draft reply to the "
        f"message above was rejected because {reason}\n\nREJECTED DRAFT:\n---\n{draft}\n---\n\n"
        "Now write the reply again.\n"
    )
    return original_prompt + note, reason
