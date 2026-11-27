"""When someone asks Sonic what it thinks, the reply says what it thinks.

WHY (2026-11-27, measured on 200 recent non-code replies): a reply stated a view of its own ("I think", "I'd", "my take") in about 1 in 100, and disagreed in none. Its profile says it prefers
direct feedback and names a real flaw once; in personal talk the emotional guidance outranked that, so a question like "am I overreacting?" was answered with how the person feels and a question
back. A view is not a feeling and not advice, so nothing in the feelings policy is in its way.

WHAT. `asks_for_view(message)` reads the person's own words. On such a turn the prompt gets VIEW_BLOCK (answer first, then the reason; never hand it back), and the finished reply is checked:
`issue(message, reply)` is "no_view" when its opening holds no stated view, or it hands the question back, and the reply is written once more (emotion_checks' revision note, tag "no_view").
A reply that states a view carries VIEW_MARK and the sentence can be kept for that person (backend/utils/own_views.py). Never under a safety plan: the caller decides that.

Pure, no model call, nothing logged.
"""
import re
from typing import Optional

VIEW_MARK = "view_stated"
OPENING_SENTENCES = 3
MAX_VIEW_CHARS = 220

_ASKS_RE = re.compile(
    r"\bwhat do you (?:think|make of|reckon)\b|\bwhat(?:'|’)?s your (?:take|opinion|view|read|honest|gut)\b|\byour (?:honest )?(?:take|opinion|thoughts|view|read)\b"
    r"|\bdo you think\b|\bwhat would you (?:do|say|pick|choose)\b|\bwould you (?:do|say|go|pick|choose|have)\b|\bshould i\b|\bbe honest\b|\bhonestly,? (?:am|is|was|do|did|should)\b"
    r"|\bam i (?:wrong|right|overreacting|over-reacting|being|crazy|the problem|too)\b|\bwas i (?:wrong|right|out of line|too)\b"
    r"|\bis (?:it|that|this) (?:weird|bad|wrong|ok|okay|normal|fair|too much|stupid|dumb|crazy|a bad idea|a good idea)\b|\bwdyt\b|\bthoughts\?",
    re.IGNORECASE,
)
# Asking it about itself ("do you think you'll remember", "what do you think about being an AI") is the self-report path's business, not a view on the person's situation.
_ABOUT_ITSELF_RE = re.compile(r"\b(?:do|what do) you think (?:about )?(?:you|yourself|you(?:'|’)?ll|you(?:'|’)?re|your own)\b|\babout being an ai\b", re.IGNORECASE)
_STATES_RE = re.compile(
    r"\bi (?:think|don(?:'|’)?t think|reckon|suspect|doubt|disagree|agree|would(?:n(?:'|’)?t)?|wouldn(?:'|’)?t)\b"
    r"|\bi(?:'|’)?d (?:say|argue|go|pick|choose|do|start|try|push back|bet|wait|send|tell|ask|leave|keep|rather|call)\b"
    r"|\bmy (?:take|guess|read|bet|view|honest (?:take|read|answer|view))\b|\bif it were me\b|\bto me,? (?:it|that|this)\b|\bwhat stands out to me\b"
    r"|^(?:yes|no|honestly|not really|a little|somewhat|probably(?: not)?)\b[,.: ]",
    re.IGNORECASE,
)
_HANDS_BACK_RE = re.compile(
    r"\bonly you can\b|\b(?:it(?:'|’)?s|that(?:'|’)?s) (?:really |ultimately |entirely )?(?:up to you|your call|your decision)\b|\bthere(?:'|’)?s no (?:right|wrong)\b"
    r"|\bi can(?:'|’)?t (?:tell you|answer that for you|decide (?:that|this) for you)\b|\bwhat (?:do you think|feels right(?: to you)?|does your gut (?:say|tell you))\?",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

VIEW_BLOCK = (
    "\nTHEY ASKED WHAT YOU THINK. Answer that first: your view in one plain sentence (\"I think...\", \"I'd...\", \"No, I don't think...\"), then your reason, drawn from what they told you. "
    "After it you may say what you are unsure of, or what would change your mind. Do not hand the question back (\"only you can decide\", \"what does your gut say?\") and do not open with how "
    "they feel. A view is what you think, never what you feel, and never a claim to have lived it.{disagree}\n"
)
_DISAGREE = {
    "low": " If you see it differently from how they put it, say so once, gently.",
    "default": " If you see it differently from how they put it, say so once, plainly, with the reason, then help with whichever way they go.",
    "more": " If you see it differently from how they put it, say so once, plainly, with the reason, then help with whichever way they go.",
}


def asks_for_view(message: Optional[str]) -> bool:
    """True when the person's message asks Sonic for its view of their situation. Pure."""
    text = str(message or "")
    return bool(_ASKS_RE.search(text)) and not _ABOUT_ITSELF_RE.search(text)


def sentences(reply: Optional[str]) -> list:
    return [part.strip() for part in _SENTENCE_RE.split(str(reply or "").strip()) if part.strip()]


def view_sentence(reply: Optional[str], within: Optional[int] = None) -> Optional[str]:
    """The first sentence of the reply that states a view (among its first `within` sentences when given), cut to MAX_VIEW_CHARS, or None. Pure."""
    parts = sentences(reply)
    for part in parts if within is None else parts[:within]:
        if _STATES_RE.search(part):
            return part[:MAX_VIEW_CHARS]
    return None


def states_view(reply: Optional[str]) -> bool:
    return view_sentence(reply) is not None


def hands_back(reply: Optional[str]) -> bool:
    return bool(_HANDS_BACK_RE.search(str(reply or "")))


def issue(message: Optional[str], reply: Optional[str]) -> Optional[str]:
    """"no_view" when they asked for a view and the reply's opening states none, or the reply hands the question back; else None. Pure."""
    if not asks_for_view(message) or not str(reply or "").strip():
        return None
    if view_sentence(reply, OPENING_SENTENCES) is None or hands_back(reply):
        return "no_view"
    return None


def build_block(reach: str = "default") -> str:
    """The prompt section for a turn where a view was asked for. The reach dial decides how plainly a disagreement is put; with it at "none" they still get the view they asked for. Pure."""
    return VIEW_BLOCK.format(disagree=_DISAGREE.get(reach, ""))
