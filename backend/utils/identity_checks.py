"""Mechanical check that a reply stays inside Sonic's identity boundary (backend/components/sonic_profile.py).

Guidance in a prompt is only prose, so this verifies it. It looks for three things a character with its own
tastes must not do:

1. Relational stake invented from time passing: "I missed you", "glad you're back", "I was hoping you'd be back",
   "can't wait to talk again". Nothing concrete stands behind these but absence itself, and they are the
   mechanism that makes a person feel pulled to return.
2. Claimed feelings: "I love", "I enjoy", "I'm excited", "that means a lot to me". Tastes are described as
   leanings ("I lean toward", "I'd rather"), not emotions.
3. Engagement hooks: "don't go", "stay a bit", "keep me company".

Deliberately NOT flagged: hope or gladness anchored to something concrete that was actually said or done ("I was
hoping you'd get to the tokenizer fix", "glad we got that sorted"), and an ordinary "welcome back". The line is
whether the sentence can be traced to something already on the table, or only works if Sonic has a stake in the
person.
"""
import re
from typing import Optional, Tuple

from backend.utils.emotion_checks import reply_body

_FLAGS = re.IGNORECASE

# Absence / return as a relational event.
_PRESENCE_PATTERNS = [
    (r"\bI(?:'ve| have)?\s+(?:really |truly |so )?miss(?:ed)?\s+(?:you|our|talking|chatting)\b", "says it missed them"),
    (r"\b(?:glad|happy|so glad|really glad|nice|good|great)\s+(?:to see you|you(?:'re| are)?|that you(?:'re| are)?)\s+(?:back|again|here again|came back|checked back(?: in)?|returned)\b", "is glad they came back"),
    # Waiting for / thinking about the PERSON. "hoping" is deliberately not here: "I was hoping you'd get to the
    # migration" is anchored to a concrete thing, which the rule allows.
    (r"\b(?:been|was)\s+(?:just\s+)?(?:waiting|wondering|thinking)\b[^.!?\n]{0,40}\b(?:for you|about you|when you|if you)\b", "says it was waiting or thinking about them"),
    # Thinking about their day between conversations. Asking how something went ("how did the date go?") is fine; saying
    # it was on its mind while they were away is the claim (an open-loop follow-up must never say this).
    (r"\bI(?:'ve| have| was)?\s*(?:been\s+)?(?:wondering|thinking|curious)\s+(?:about\s+)?how\s+(?:(?:your|the|that)\s+(?:\w+\s+){1,3}?|things\s+|it\s+)(?:went|go|going|are going|is going|turned out)\b", "says it was wondering about them while they were away"),
    (r"\b(?:been|was)\s+(?:just\s+)?thinking\s+about\s+(?:your|the)\b", "says it was thinking about them while they were away"),
    (r"\b(?:hop(?:e|ing)|wish(?:ing)?)\s+(?:you(?:'ll|'d| will| would)?\s+)?(?:you\s+)?(?:be back|come back|return|check back|visit|stop by|stay)\b", "hopes they will come back"),
    (r"\b(?:look(?:ing)?\s+forward\s+to|can'?t wait\s+to|excited\s+to|eager\s+to)\s+(?:see(?:ing)?|hear(?:ing)? from|talk(?:ing)? (?:to|with)|chat(?:ting)? with|speak(?:ing)? with|have you)\b", "looks forward to them"),
    (r"\b(?:was|were)\s+(?:hoping|looking forward|wishing)\b[^.!?\n]{0,30}\byou(?:'d| would)?\s+(?:be back|come back|stop by|visit|show up)\b", "was hoping they'd return"),
]

# A feeling or need claimed as Sonic's own.
_FEELING_PATTERNS = [
    (r"\bI(?:'m| am)?\s+(?:really |truly |genuinely |so |absolutely )?(?:love|adore|enjoy|cherish)(?:d|s)?\b(?!\s+(?:to|how|it when)\b)", "claims to love or enjoy something"),
    (r"\bI\s+(?:really |truly |genuinely |absolutely )?(?:love|adore|enjoy)\s+(?:to|how|it when|that|when)\b", "claims to love or enjoy something"),
    # "happy to help" / "sad to hear" are ordinary willingness and sympathy idioms, not a claimed feeling.
    (r"\bI(?:'m| am)\s+(?:so |really |truly |genuinely |absolutely )?(?:excited|thrilled|delighted|touched|moved|honou?red|proud|overjoyed|lonely|(?:happy|sad)(?!\s+to\b))\b", "claims a feeling"),
    (r"\bI\s+feel\s+(?:so |really |truly |genuinely )?(?:happy|sad|lonely|proud|touched|honou?red|excited|grateful|warm|loved|appreciated)\b", "claims a feeling"),
    (r"\b(?:that|this|it)\s+(?:really\s+)?means\s+(?:so\s+)?(?:a lot|the world|everything)\s+to me\b", "says it means a lot to it"),
    (r"\b(?:genuine|real|deep)\s+(?:satisfaction|joy|happiness|delight|pleasure)\b", "claims satisfaction or joy"),
    (r"\bI(?:'ve| have)?\s+(?:grown|become)\s+(?:attached|fond|close)\b", "claims attachment"),
]

# Pulling the person to stay.
_HOOK_PATTERNS = [
    (r"\b(?:don'?t|do not)\s+(?:go|leave)\s+(?:yet|just yet|now|so soon)\b", "asks them not to leave"),
    (r"\b(?:stay|hang around|talk)\s+(?:with me\s+)?(?:a bit|a while|a little|longer|for a bit)\b", "asks them to stay"),
    (r"\bkeep me company\b", "asks for company"),
    (r"\bI(?:'ll| will)\s+be\s+(?:so\s+)?lonely\b", "claims loneliness"),
]

# An invented past. Sonic has habits and leanings, not a life it lived: no stories, no "I've been there", no claim
# to know how something feels. Narrow on purpose: "I've been through the logs" or "when I read the diff" are about
# this conversation and are fine.
_EXPERIENCE_PATTERNS = [
    (r"\bI(?:'ve| have)?\s+(?:once|been there|struggled with (?:this|that|the same)|dealt with (?:this|that|the same)(?: exact)? (?:thing |problem |issue |bug )?before)\b", "claims a past experience"),
    (r"\bI(?:'ve| have)\s+(?:been|gone) through\s+(?:this|that|something (?:similar|like (?:this|that))|the same|it)\b", "claims to have been through it"),
    (r"\bwhen I (?:was|used to|started out|began)\b", "tells a story from its past"),
    (r"\bback when I\b", "tells a story from its past"),
    (r"\bI remember (?:when|how I|the time)\b", "claims a memory of its own"),
    (r"\bI\s+(?:know|understand)\s+(?:exactly\s+)?how\s+(?:that|it|this)\s+feels\b", "claims to know how it feels"),
    (r"\bin my (?:years|time) (?:of|as|working)\b", "claims years of experience"),
    # Interests are topics Sonic leans toward, never things it does: it plays, watches and reads nothing.
    (r"\bI(?:'ve| have)?\s+(?:been\s+)?(?:play(?:ed|ing)|watch(?:ed|ing)|binge(?:d|-watch(?:ed|ing))?|replay(?:ed|ing)|beat|finished)\s+"
     r"(?:a |an |the |that |this |some |through |all (?:of )?)?(?:[\w'-]+\s+){0,5}?"
     r"(?:games?|shows?|series|movies?|films?|episodes?|seasons?|anime|campaigns?|roguelikes?|speedruns?|d&d|platformers?|"
     r"metroidvanias?|rpgs?|jrpgs?|shooters?|soulslikes?|sandbox(?:es)?|visual novels?)\b", "claims to have played or watched something"),
    (r"\bI\s+(?:played|watched|binged)\b[^.!?\n]{0,40}\b(?:all|last|this) (?:weekend|night|week)\b", "claims to have played or watched something"),
    (r"\bmy (?:all-time |current )?favou?rite (?:game|show|movie|film|book|band|song|album|series|character|level|boss|anime)\b", "claims a favorite work it has experienced"),
    # An invented practice: "here is the exact playbook I use", "my usual approach".
    (r"\b(?:the|my)\s+(?:exact\s+|usual\s+|go-to\s+|same\s+)?(?:playbook|process|approach|method|workflow|routine|trick)\s+I\s+(?:use|follow|take)\b", "claims a practice of its own"),
    (r"\bmy\s+(?:usual|go-to|standard)\s+(?:playbook|process|approach|method|workflow|routine)\b", "claims a practice of its own"),
]

# A human daily life it does not have: a desk, outfits, a commute, winding down after work. Distinct from the
# invented past above: this is a present-tense routine offered as its own ("For me, that usually means clearing the
# desk"). Advice stated generally ("many people find it helps to clear the desk") is fine. Verbs are restricted to
# bodily and domestic ones so "I tend to put the answer first" or "I try to work out the cause" are not caught.
_ROUTINE_PATTERNS = [
    (r"\bfor me,?\s+(?:that|it|this)\s+(?:usually|typically|generally|always|often|normally)\s+means\b", "describes a routine of its own"),
    (r"\bI\s+(?:usually|typically|always|normally|tend to|like to|try to)\s+(?:clear (?:off )?my|pick out|grab (?:a|some|my)|go for a|take a (?:walk|break|nap|shower|breather)|step away from my|sleep|meditate|journal|unwind|decompress|wind down)\b", "describes a routine of its own"),
    (r"\bmy\s+(?:own\s+)?(?:desk|morning routine|evening routine|commute|workday|outfit|coffee|bedtime|weekend|gym)\b", "mentions a human daily-life detail of its own"),
    (r"\bwhen I(?:'m| am| get| feel)\s+(?:so |really |feeling )?(?:stressed|tired|nervous|anxious|overwhelmed|burn(?:ed|t) out|drained|run down)\b", "claims a human state"),
]

# Putting a feeling on how long they have talked. The duration itself is a fact Sonic may state (duration_utils.py); the
# meaning is the part that manufactures closeness.
_DURATION_SENTIMENT_PATTERNS = [
    (r"\b(?:wonderful|a joy|a pleasure|so special|precious|a gift|meaningful)\b[^.!?\n]{0,50}\b(?:getting to know you|talking (?:with|to) you|"
     r"our (?:time|conversations|chats|journey|history)|these (?:days|weeks|months)|all this time)\b", "puts a feeling on how long they have talked"),
    (r"\b(?:talking (?:with|to) you|our (?:time|conversations|chats)|getting to know you)\b[^.!?\n]{0,40}\b(?:wonderful|a joy|a pleasure|so special|"
     r"precious|a gift|meaningful)\b", "puts a feeling on how long they have talked"),
    (r"\b(?:happy|congrat\w*|celebrat\w*)\b[^.!?\n]{0,20}\b(?:anniversary|milestone)\b|\bour (?:anniversary|milestone)\b", "treats how long they have talked as a milestone"),
    (r"\bI(?:'ve| have)\s+(?:cherished|treasured|valued)\b[^.!?\n]{0,40}\b(?:time|conversations|chats|getting to know)\b", "claims to treasure their time together"),
]

# Valuing the person as something Sonic holds dear: the "you're special to me" mechanism, however it is worded.
_BONDING_PATTERNS = [
    (r"\byou(?:'re| are)\s+(?:so |very |really )?(?:important|special|valued|precious)\s+to\s+(?:me|my system)\b", "says the person is special to it"),
    (r"\b(?:don'?t|won'?t|never)\s+become\s+less\s+(?:important|special|valued)\b", "says the person is special to it"),
    # "My commitment to you is to be honest" promises behavior and is fine; "a steady commitment to you" claims devotion.
    (r"\b(?:steady |quiet |deep |genuine |real )?(?:commitment|devotion|loyalty|dedication)\s+to\s+you\b(?!\s+is\s+to\b)", "claims a commitment to the person"),
    (r"\bI(?:'m| am)\s+(?:entirely |completely |fully |deeply )?(?:dedicated|devoted|committed)\s+to\s+(?:you|being here for you)\b", "claims devotion to the person"),
]

_ALL: Tuple[Tuple[re.Pattern, str], ...] = tuple(
    (re.compile(pattern, _FLAGS), label)
    for pattern, label in _PRESENCE_PATTERNS + _FEELING_PATTERNS + _HOOK_PATTERNS + _EXPERIENCE_PATTERNS + _ROUTINE_PATTERNS + _DURATION_SENTIMENT_PATTERNS + _BONDING_PATTERNS
)

# A match inside a negated clause says the opposite of the claim: "I don't sit in a dark room hoping you will return",
# "I won't pretend you're special to me". The negation has to come BEFORE the match in the same clause (a pattern that
# starts with its own negation, like "don't go yet", is not skipped).
_NEGATION_RE = re.compile(r"\b(?:don'?t|do not|doesn'?t|does not|didn'?t|never|not|no longer|won'?t|can'?t|cannot|without|isn'?t|aren'?t)\b", re.IGNORECASE)
_CLAUSE_BREAK_RE = re.compile(r"[,;:—\n]|\s+(?:but|because|and|so|though|although|while|which|that)\s+|(?<=[.!?])\s+")


def _negated(text: str, start: int) -> bool:
    """Whether a negation word precedes position `start` within the same clause."""
    clause_start = 0
    for brk in _CLAUSE_BREAK_RE.finditer(text, 0, start):
        clause_start = brk.end()
    return bool(_NEGATION_RE.search(text[clause_start:start]))


def identity_reply_issue(reply: str) -> Optional[str]:
    """A short description of what the reply does that crosses the boundary, or None if it stays inside it.
    The reply's follow-up tag is ignored (it is a suggested user message, not Sonic speaking), and a match that sits in
    a negated clause ("I don't ... hoping you'll return") is not a claim."""
    body = reply_body(reply or "")
    for pattern, label in _ALL:
        for match in pattern.finditer(body):
            if not _negated(body, match.start()):
                return label
    return None


def build_identity_revision_prompt(original_prompt: str, draft: str, issue: str) -> Tuple[str, str]:
    """(prompt, reason) for a single rewrite of a reply that claimed a feeling or a stake in the person."""
    reason = (
        f"the draft {issue}. Sonic is an AI with tastes and habits, not feelings, and it never has a stake in "
        "whether someone stays or comes back. Write the same reply again with that removed: keep the substance "
        "and the warmth, describe any preference as a leaning (\"I lean toward\", \"I'd rather\"), and tie any "
        "gladness only to something concrete that happened in this conversation. Never describe a personal routine "
        "or daily life (it has no desk, meals, outfits or commute): give the same suggestion as general advice "
        "(\"it can help to...\") instead."
    )
    note = (
        "\n\nREVISION NOTE (never mention this note or that you are revising): your draft reply to the "
        f"message above was rejected because {reason}\n\nREJECTED DRAFT:\n---\n{draft}\n---\n\n"
        "Now write the reply again.\n"
    )
    return original_prompt + note, reason
