import re
from typing import Optional, Tuple

from backend.utils.emotion_checks import reply_body

# Sonic ends conversations well: when a user says thanks or goodnight it says goodnight back, instead of
# bolting on "anything else?" or a question to keep them talking. Nothing in the code used to ask for
# that; it came from the model's own habits plus the "trusted colleague" persona, which is exactly why it
# was fragile: one innocent edit to the persona or to the follow-up rule could end it with no error and
# no failing test. This module makes it a deliberate behavior in three parts:
#   1. the persona carries an explicit rule (constraints.py, SONIC_ASSISTANT_PERSONA, "KNOW WHEN TO STOP");
#   2. a clearly closing message (below) gets a short directive on top of that rule;
#   3. the finished reply to a closing message is checked for engagement-fishing, with one rewrite if so.
# Only unambiguous closings are detected. A message that also asks or says anything else is not a closing,
# and anything subtler (a user drifting off mid-thought) is left to the persona rule's own judgment.

# Phrases that, on their own, say "I'm done here". Deliberately narrow: a bare "ok", "yes" or "sure" can
# just as easily mean "continue", so they are not here.
_CLOSING_PHRASES = [
    r"thanks?", r"thank you", r"thx", r"ty", r"much appreciated", r"appreciate (?:it|that|you)", r"cheers",
    r"good ?night", r"night night", r"nighty night", r"gn", r"sweet dreams",
    r"bye(?: bye)?", r"goodbye", r"see (?:you|ya)(?: later| tomorrow| soon)?", r"later", r"ttyl", r"talk (?:to you )?(?:later|soon|tomorrow)",
    r"have a (?:good|great|nice) (?:one|night|day|evening|weekend)",
    r"that'?s (?:all|it)(?: i needed| for now| for today)?", r"that is (?:all|it)", r"all (?:set|good|done)", r"i'?m (?:good|all set|done|set)",
    r"got it", r"makes sense", r"sounds good", r"will do", r"perfect", r"awesome", r"great", r"cool", r"nice", r"wonderful", r"excellent",
    r"that (?:worked|helped|did it|fixed it|works|helps)", r"that'?s (?:perfect|great|helpful|exactly what i needed)",
    r"you'?re the best", r"you rock",
    # "I'm leaving" statements: the user announcing the end of their session
    r"(?:i'?m|im|i am) (?:done|finished|off)(?: with work)?(?: for (?:the day|today|tonight|the night|the week|now))?",
    r"done (?:with work )?for (?:the day|today|tonight|the night|the week)",
    r"calling it (?:a day|a night)", r"(?:logging|signing) off", r"heading (?:out|off|home|to bed)",
    r"(?:i'?m |im )?(?:going|gonna|off) (?:to )?(?:bed|sleep|home)", r"wrapping up (?:for )?(?:the day|today|tonight)",
]
_CLOSING_RE = re.compile(r"\b(?:" + "|".join(_CLOSING_PHRASES) + r")\b", re.IGNORECASE)
# Words that may sit around a closing phrase without turning it into something else.
_FILLER = {
    "ok", "okay", "alright", "aight", "yeah", "yep", "yup", "so", "well", "just", "really", "very", "much", "a", "lot", "again",
    "lol", "haha", "hah", "heh", "and", "for", "all", "the", "help", "your", "you", "it", "this", "that", "now", "then", "too",
    "saapp", "sonic", "assistant", "buddy", "friend", "man", "mate", "bro", "dude",
    "i", "will", "see", "talk", "later", "tomorrow", "soon", "today", "tonight", "good", "great", "one", "of", "to", "from", "me",
    # the time and the setting around "I'm done for the day" ("oh its okay im done with work for the day lol its 6:30 pm")
    "oh", "its", "it's", "work", "with", "day", "evening", "night", "pm", "am", "bed", "home", "off", "out", "weekend", "o'clock",
}
_MAX_WORDS = 16


def is_closing_message(text: Optional[str]) -> bool:
    """True when the whole message is a sign-off or an acknowledgement ("thanks!", "perfect, that fixed
    it, thanks", "goodnight saapp"), with nothing left over: no question, no new request, no new
    information. Conservative on purpose, since a wrong yes tells the model to cut a real conversation short."""
    if not text or "?" in text:
        return False
    stripped = text.strip()
    if len(stripped.split()) > _MAX_WORDS:
        return False
    if not _CLOSING_RE.search(stripped):
        return False
    leftover = _CLOSING_RE.sub(" ", stripped)
    words = re.findall(r"[a-z']+", leftover.lower())
    return all(w.strip("'") in _FILLER or not w.strip("'") for w in words)


CLOSING_DIRECTIVE = (
    "\n\nCONVERSATION ENDING (this turn): the user is wrapping up. Close the conversation the way a good "
    "colleague would, in a line or two: warm, specific to what you were just doing or talking about if that "
    "comes naturally, and finished. No question, no <<<FOLLOW_UP>>> tag, no \"anything else?\", no "
    "\"let me know if you need anything\", no new topic, no list of things you could do next. If they said "
    "goodnight, say goodnight. If a thank-you, a short you're-welcome-shaped reply is enough. Let them go.\n"
)

# Phrases that turn a sign-off into a hook, or into customer-service boilerplate.
_FISHING_PATTERNS = [
    r"\banything else\b",
    r"\blet me know (?:if|whether|how|what)\b",
    r"\bfeel free to\b",
    r"\bdon'?t hesitate\b",
    r"\b(?:i'?m|i am) (?:always )?(?:here|around|available) (?:if|whenever|anytime|any time|should)\b",
    r"\bneed (?:anything|any(?:thing)? else|more help|further help)\b",
    r"\bif you (?:have|get|think of) any (?:other |more |further )?(?:questions|issues|problems)\b",
    r"\bhappy to help (?:with|you with) (?:anything|more)\b",
    r"\bwhat(?:'s| is) next\b",
]
_FISHING_RE = re.compile("|".join(_FISHING_PATTERNS), re.IGNORECASE)
_FOLLOW_UP_TAG_RE = re.compile(r"<<<FOLLOW_UP:", re.IGNORECASE)


def closing_reply_issue(reply: str) -> Optional[str]:
    """The reason a reply to a closing message is still fishing for engagement, or None when it is a
    clean close: a follow-up tag, a question, or boilerplate that invites the user to keep going."""
    if not reply:
        return None
    if _FOLLOW_UP_TAG_RE.search(reply):
        return "a follow-up suggestion"
    body = reply_body(reply)
    if "?" in body:
        return "a question"
    match = _FISHING_RE.search(body)
    if match:
        return f"an invitation to continue (\"{match.group(0)}\")"
    return None


def build_closing_revision_prompt(original_prompt: str, draft: str, issue: str) -> Tuple[str, str]:
    """(prompt, reason) for a single rewrite of a closing reply that went fishing."""
    reason = (
        f"the user was wrapping up and the draft kept the conversation going with {issue}. Write it again as "
        "a clean close: a line or two, warm, with no question, no follow-up tag and no invitation to ask for "
        "more. If they said goodnight, say goodnight."
    )
    note = (
        "\n\nREVISION NOTE (never mention this note or that you are revising): your draft reply to the "
        f"message above was rejected because {reason}\n\nREJECTED DRAFT:\n---\n{draft}\n---\n\n"
        "Now write the reply again.\n"
    )
    return original_prompt + note, reason
