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


# ---- attunement: a reply to someone low that is hedged, scripted, thin or interviews them (2026-11-10, Jack's blind A/B: docs/model-eval-plan.md Update 16) ----------------------------------------------------------
# Flash won 13 of 14 decisive picks. Haiku hedged (1.3 softeners a reply, 30% with two or more, against Flash's 0.3 and none), leaned on stock reflective-listening phrases ("it sounds like" 15% of replies against 4%, "lots of
# people" 11% against 4%, "my read of it" 7% against 0), and said "that must be hard" without giving anything with it. Every one of these is a pattern a check can see, so they are checks, on any base model, for a person who
# is low (a negative feeling read from THIS message). What they do NOT forbid: a concrete question that moves things forward, a plain check on how they are doing now and then, and "that must be hard" when it comes with substance.
PROBE_RE = re.compile(
    r"\bwhat (?:was|is) the (?:last|first) (?:thing|question)\b"
    r"|\bwhat did (?:she|he|they|your \w+|you) (?:say|tell|send|do|write)\b|\bwhat (?:she|he|they) (?:said|sent|told|wrote)\b"
    r"|\b(?:did|has|had|have) (?:she|he|they) (?:say|said|mention\w*|tell|told|explain\w*|give|gave)\b"
    r"|\bwhat do you (?:wish|want) (?:she|he|they)\b|\bwishing (?:she|he|they)\b"
    r"|\bwhy do you (?:think|suppose)\b|\bwhen (?:they|those thoughts|the thoughts) come back\b"
    r"|\bbefore (?:she|he|they) (?:went|stopped|got|left|started)\b"
    r"|\bwhich part\b[^?]{0,50}\b(?:hurt|hurts|stings|snags|bothers|sticks)\b",
    re.IGNORECASE,
)
STATE_ASK_RE = re.compile(
    r"\bhow (?:are|do|did|is|has|have|was) (?:you|that|this|it|things|your (?:day|morning|night|afternoon|evening|head|heart|chest|body))[^?]{0,45}\b(?:feel\w*|doing|going|holding up|sitting|landing|been|today|tonight|morning|afternoon)\b"
    r"|\bhow are you\b|\bhow'?s (?:the |your )?(?:day|morning|night|afternoon|evening|it going|things|everything)\b|\bwhat does (?:that|this|it) feel like\b|\bhow does (?:that|this|it) (?:feel|sit|land)\b|\bon your mind\b|\bhow heavy\b|\bwhat do you need\b|\bwhat would help\b"
    r"|\bare you (?:staying|keeping|still) (?:warm|comfortable|laying|lying)\b|\bhave you (?:eaten|slept|had anything|had something|gotten (?:some )?(?:sleep|rest|food|air)|been able to get)\b"
    r"|\b(?:set|put) (?:the|your) phone (?:down|away|in another room)\b|\bfresh air\b|\bsomething to (?:eat|drink)\b|\banything (?:that )?feels? grounding\b",
    re.IGNORECASE,
)
# Stock reflective-listening phrasing and "lots of people" generalising. "That must be hard" is NOT here: it is allowed with substance (THIN_COMFORT below).
TEMPLATE_PHRASE_RE = re.compile(
    r"\b(?:it|that|this) sounds like\b|\bmy (?:read|guess)(?: of it| on it| is)?\b|\b(?:lots|a lot|plenty|so many) of (?:people|us)\b|\bmost people\b|\bpeople (?:often|usually|commonly|tend)\b",
    re.IGNORECASE,
)
HEDGE_RE = re.compile(
    r"\b(?:might|maybe|perhaps|possibly|probably|could be|i wonder|seems?|seemed|my guess|my read|sort of|kind of|a bit like|in some ways|tends? to|may be|may feel)\b", re.IGNORECASE)
# "How such things go" talk (2026-11-10, Jack on a venting reply: "I don't like the 'often times can be x', the user is clearly venting and is just trying to be heard"). Haiku's hurt replies carried 1.45-1.6 of these each
# (70-75% had one), Flash 1.2 (65%); with two or more it was 11 of 30 Haiku replies against 5 of 20 Flash, so one is let through and a second is regenerated.
GENERAL_RE = re.compile(
    r"\b(?:often|sometimes)\b|\bcan (?:often |also )?(?:be|feel|start|make|read|sting)\b|\btend(?:s)? to\b|\bit'?s easy to\b|\bit'?s (?:natural|normal|common|understandable) (?:to|that|for)\b", re.IGNORECASE)
MAX_GENERAL = 1
# The sharper form Jack named (2026-11-11, round 3): "when X happens, it's usually because of Y" ("yeah, no shit"). One is enough to regenerate. Measured: 1 of 42 Haiku replies and 2 of 32 Flash replies in the three blind rounds.
CAUSE_GENERAL_RE = re.compile(
    r"\b(?:usually|often|typically|generally|tends? to be)\b[^.?!]{0,40}\b(?:because|since|due to|comes from|from)\b"
    r"|\b(?:when|after|if)\b[^.?!]{0,100}\b(?:it'?s|that'?s|this is|it is)\b[^.?!]{0,12}\b(?:usually|often|typically|generally)\b"
    r"|\b(?:is|are) (?:usually|often|typically) (?:a sign|because|the result|what happens)\b", re.IGNORECASE)
MAX_HEDGES = 1                 # measured: Flash 0.3 a reply and never two; Haiku 1.3 and 30% with two or more
COMFORT_RE = re.compile(r"\bmust be (?:hard|heavy|tough|difficult|a lot)\b|\bhard to sit with\b", re.IGNORECASE)
EXPLAIN_RE = re.compile(
    r"\b(?:feels? like|it'?s like|is like|the hard part|what makes (?:it|that)|the part that|that'?s the part|the thing about|because|which is why|it'?s the kind of|the way (?:it|that)|that kind of)\b", re.IGNORECASE)
THIN_COMFORT_WORDS = 40        # words in the rest of the reply that must come with "that must be hard", and one of the explaining phrases among them


def _question_sentences(text: str):
    return [s for s in _sentences(text) if s.endswith("?")]


def reply_attunement_issue(reply: str) -> Optional[str]:
    """The tag of what is off about a reply to someone who is low, or None: "probing_question" (it ends by making them retell what happened, was said or why), "two_questions", "template_phrase" (a stock
    reflective-listening phrase), "thin_comfort" ("that must be hard" with nothing given with it), "hedged" (more than MAX_HEDGES softeners) or "generalizing" (more than MAX_GENERAL "how such things go" phrases). Pure."""
    text = reply_body(reply)
    if not text:
        return None
    questions = _question_sentences(text)
    if questions and PROBE_RE.search(questions[-1]):
        return "probing_question"
    if len(questions) >= 2:
        return "two_questions"
    if TEMPLATE_PHRASE_RE.search(text):
        return "template_phrase"
    sentences = _sentences(text)
    comfort = [s for s in sentences if COMFORT_RE.search(s)]
    if comfort:
        rest = " ".join(s for s in sentences if s not in comfort)
        if len(rest.split()) < THIN_COMFORT_WORDS or not EXPLAIN_RE.search(rest):
            return "thin_comfort"
    if len(HEDGE_RE.findall(text)) > MAX_HEDGES:
        return "hedged"
    if len(GENERAL_RE.findall(text)) > MAX_GENERAL or CAUSE_GENERAL_RE.search(text):
        return "generalizing"
    return None


_REVISION_REASONS = {
    "venting_advice": (
        "the person mainly wants to be heard right now, and the draft gave advice or suggestions. Write it "
        "again so it stays with what they are feeling and what they actually said: no advice, tips, "
        "suggestions or lists. It is fine to end by inviting them to say more."
    ),
    "probing_question": (
        "the draft ends by asking them to retell what happened, what was said or why, which makes them do the work. Write it again without that question: say what it is like for them, plainly and "
        "specific to what they said, and if you ask anything, ask ONE concrete question that is easy to answer and moves forward (a choice between two next steps, or a specific detail about what they care about)."
    ),
    "two_questions": (
        "the draft asked more than one question. Write it again with at most one: concrete, easy to answer, and moving things forward."
    ),
    "template_phrase": (
        "the draft leaned on stock phrasing (\"it sounds like\", \"my read\", \"lots of people\", \"most people\"). Write it again without those: say plainly, in your own words, what it is like for them, "
        "using what they told you."
    ),
    "thin_comfort": (
        "the draft said that it must be hard, or hard to sit with, without giving them anything with it. Write it again: keep the warmth, but add what it is actually like or what tends to help, in your own "
        "words and specific to what they said."
    ),
    "generalizing": (
        "the draft talked about how such things go in general (\"often\", \"can be\", \"tends to\", \"it's easy to\"). Write it again speaking only about THIS person and what they told you: say what it is like for them, "
        "straight to them, with their own details. They are being heard, not taught what is typical."
    ),
    "hedged": (
        "the draft hedged too much (maybe, might, seems, probably, sort of). Write it again saying what you think it is like for them plainly, as something you are telling them, with at most one softener in the whole reply."
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
    if valence in NEGATIVE_VALENCES:
        return reply_attunement_issue(reply)       # a low person, whatever the need: hedged, scripted, thin or interviewing replies are regenerated once
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
