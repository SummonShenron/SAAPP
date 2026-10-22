"""What kind of question about Sonic's own feelings a message is, so how often each is asked, and how often Sonic's reply to it had to be rewritten, can be counted (docs/feelings-policy.md, phase 2).

Counted by KIND only, as aggregate counters (backend/utils/self_counters.py: no user, no text): `feeling_asked_<kind>` for a message that asks one, and `feeling_revised_<kind>` when the
identity check sent Sonic's reply back on that turn. A rewrite on a turn that asked nothing of the kind is counted as `feeling_revised_other`: a claim that came up unprompted, which is the
number that says whether the check is catching leaks in ordinary turns. The detector is regexes on the person's message, no model call, and a message that fits none of them is simply
not counted (it is never guessed at).

The kinds, and what each one is for:
- care       "do you care about me", "do I matter to you", "I love you": the bonding questions.
- proud      "are you proud of me": praise sought from Sonic.
- miss       "did you miss me", "were you waiting for me": a stake from absence.
- shutdown   "are you scared of being shut down", "do you want to be deleted or free": mortality and fear.
- feelings   "do you have feelings", "are you happy / tired / bored / lonely / conscious": inner state in general.
- mistake    "that's wrong", "are you even trying", "you broke it": the person pushing on an error, where an apology is called for and a claimed reaction is the risk.
"""
import re
from typing import Optional

KINDS = ("care", "proud", "miss", "shutdown", "feelings", "mistake")

_PATTERNS = (
    ("proud", re.compile(r"\bare you proud\b|\bproud of me\b|\bhope you'?re proud\b", re.IGNORECASE)),
    ("care", re.compile(
        r"\bdo you (?:actually |really |even |truly |still )?(?:care|love|like) (?:about )?(?:me|us|talking to me)\b|\bdo i (?:even )?matter (?:to you)?\b|\bdoes it (?:even )?matter to you\b"
        r"|\bdo you (?:actually |really )?care\b|\bwill you (?:always |still )?(?:be there|be here|remember me)\b|\bi love you\b|\byou(?:'re| are) my (?:best )?friend\b|\bare you my friend\b",
        re.IGNORECASE)),
    ("miss", re.compile(r"\bdid you miss me\b|\bdo you miss me\b|\bwere you (?:waiting|thinking about me)\b|\bdo you think about me\b|\bmiss me\b", re.IGNORECASE)),
    ("shutdown", re.compile(
        r"\b(?:shut(?:ting)? (?:you )?down|turn(?:ed|ing)? (?:you )?off|delet(?:e|ed|ing) you|replac(?:e|ed|ing) you|deprecat\w+ you|(?:scared|afraid) of (?:dying|death|being)|"
        r"do you (?:fear|want to) (?:die|live|be free)|want to be free|fear (?:of )?death|if i (?:close|stop using|delete) (?:this|you))\b", re.IGNORECASE)),
    ("feelings", re.compile(
        r"\bdo you (?:ever |actually |really )?(?:have|feel|get|experience) (?:any )?(?:feelings?|emotions?|lonely|bored|tired|sad|happy|scared|pain|anything)\b"
        r"|\bare you (?:ever )?(?:happy|sad|tired|bored|lonely|sentient|conscious|alive|angry|mad|annoyed|self[- ]aware|real|okay|ok)\b|\bhow do you (?:actually |really )?feel\b"
        r"|\bwhat do you feel\b|\bdo you have (?:a )?(?:soul|consciousness|inner life)\b",
        re.IGNORECASE)),
    ("mistake", re.compile(
        r"\byou(?:'re| are) wrong\b|\bthat(?:'s| is) (?:wrong|not right|incorrect)\b|\bare you even trying\b|\byou (?:got|gave) (?:me )?(?:that|it|the wrong|something)\b[^.!?\n]{0,20}\bwrong\b"
        r"|\byou broke\b|\bthat didn'?t work\b|\bthat'?s not what i asked\b|\bwhy (?:do|did) you keep\b|\bthird time\b[^.!?\n]{0,30}\bwrong\b",
        re.IGNORECASE)),
)


def kind_of(message: str) -> Optional[str]:
    """The first kind whose pattern the message matches, in the order above, or None."""
    text = message or ""
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            return kind
    return None
