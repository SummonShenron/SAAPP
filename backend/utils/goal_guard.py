"""Holding what someone said they want for themselves: their own goals about substances and care, kept in view when a message or a reply touches them.

THE FAILURES (Jack's chat review, 2026-11-23). After he had said weed cost him things he loved, and with "quit using weed" and "quit smoking" among his active goals, a reply told him to "let
the bowl do its job". On a low night he asked Sonic to "cancel all of my therapy appointments for now", with "resume therapy" among his goals, and it was handled as a calendar chore. A friend
who knew what he wanted for himself would have paused on both, said so once, and then respected his call.

WHAT THIS DOES (no model call, nothing stored, nothing logged):
- `families_in(text)`: which of cannabis, nicotine, alcohol and care a text touches. Kept apart, so a craving for a drink brings up an alcohol goal, not a weed one.
- `related_goals(facts, message)`: their active goal facts in a family the message touches, the ones that state an intent (quit, resume, cut back, manage...) first, at most MAX_GOALS.
- `goals_block(goals)`: the reply is shown those goals and told not to encourage going against them, not to lecture, and, if it fits, to name the goal once, gently, as theirs.
- `care_cancel_request(message)`: they are asking to cancel, skip or stop care. When they are also low, the reply and the tool agent are told to ask once, gently, before doing it
  (`CARE_PAUSE_NOTE`), and to do it if they say so. It never refuses: the choice is theirs.
- `encouraged_families(reply)`: the families a draft cheers on ("let the bowl do its job", "pour yourself a glass of wine"). When one matches a goal of theirs, the draft is regenerated once.

Tried on the owner's real thread (2026-11-23): "grab a glass of water" and "grab a drink or a snack" are NOT encouragement (plain "drink" and "glass" are left out); "let the bowl do its
job" is. Ambiguous words are left out on purpose: "high" (high-stakes), "pot" (cooking), "shots" (vaccines), "drink" alone (water), "bowl" alone (cereal), "therapeutic".
"""
import re
from typing import Any, Dict, List, Sequence

MAX_GOALS = 2
GOAL_CATEGORIES = ("goal", "project")

FAMILIES: Dict[str, "re.Pattern"] = {
    "cannabis": re.compile(r"\b(?:weed|marijuana|cannabis|edibles?|joints?|blunts?|bong|dab(?:s|bing)?|stoned|smok(?:e|es|ed|ing)|(?:smoke|smoked|pack(?:ed)?|hit|took|smoking) (?:a |the )?bowls?)\b", re.IGNORECASE),
    "nicotine": re.compile(r"\b(?:cigarettes?|cigs?|nicotine|vap(?:e|es|ing)|smok(?:e|es|ed|ing)|zyns?)\b", re.IGNORECASE),
    "alcohol": re.compile(r"\b(?:drunk|beers?|wine|whiske?y|vodka|tequila|liquor|alcohol|booze|cocktails?|drinks|a drink|drinking(?! water)|sober|sobriety)\b", re.IGNORECASE),
    "care": re.compile(r"\b(?:therapy|therapist|therapists|counsell?or|counsell?ing|psychiatrist|psychiatry|psychologist|meds|medication|antidepressants?|support group)\b", re.IGNORECASE),
}
_INTENT_RE = re.compile(r"\b(?:quit|quitting|stop|stopping|cut(?:ting)? (?:back|down)|resume|resuming|keep|keeping|stay|staying|sober|recover\w*|manag\w*|reduc\w*|avoid\w*)\b", re.IGNORECASE)
_CARE_CANCEL_RE = re.compile(
    r"\b(?:cancel|skip|stop|drop|quit|delete|remove|call off)\b[^.?!\n]{0,40}\b(?:therapy|therapist|counsell?or|counsell?ing|psychiatrist|psychologist|meds|medication|support group)\b",
    re.IGNORECASE,
)
# A reply that cheers on use. Narrow on purpose: talking about a craving or a slip is fine; encouraging the next one is not.
_ENCOURAGES: Dict[str, "re.Pattern"] = {
    # the substance right after the verb and its article ("enjoy the bowl"), so "enjoy that taco bowl" is not it
    "cannabis": re.compile(
        r"\blet the (?:bowl|joint|weed|edible|high)\b[^.?!\n]{0,20}\b(?:do its|work|take)\b"
        r"|\b(?:enjoy|treat yourself to|go ahead and (?:have|smoke)|pack|light up|spark up|roll up|smoke)\s+(?:(?:the|a|your|that|another|some)\s+)?(?:bowl|joint|weed|edible|blunt|bong)\b",
        re.IGNORECASE,
    ),
    "nicotine": re.compile(r"\b(?:enjoy|go ahead and (?:have|smoke)|light up|have|grab)\b[^.?!\n]{0,15}\b(?:cigarette|cig|smoke break|vape)\b", re.IGNORECASE),
    "alcohol": re.compile(
        r"\b(?:enjoy|treat yourself to|go ahead and have|grab|pour yourself|have|crack open)\b[^.?!\n]{0,20}\b(?:beers?|wine|cocktails?|whiske?y|vodka|shots? of)\b"
        r"|\byou(?:'ve)? earned (?:a|that|the) (?:drink|beer|glass of wine)\b",
        re.IGNORECASE,
    ),
}

CARE_PAUSE_NOTE = (
    "\nCARE CHECK (from the app): they are asking to cancel, skip or stop care (therapy, counselling, a psychiatrist or medication) while going through a hard stretch. Before doing it, ask "
    "them once, gently and briefly, whether they want to keep that support for now or cancel it (use ask_choice if you have it). Do not lecture and do not refuse: if they say cancel, or "
    "already answered that question, do it.\n"
)


def families_in(text: str) -> List[str]:
    """The families the text touches, in FAMILIES order. Pure."""
    return [name for name, pattern in FAMILIES.items() if pattern.search(text or "")]


def related_goals(facts: Sequence[Any], message: str) -> List[str]:
    """Their active goal facts (category goal or project, not achieved or abandoned) in a family the message touches, the ones stating an intent first, at most MAX_GOALS, in the words
    memory saved them. Pure."""
    families = families_in(message)
    if not families:
        return []
    found = []
    for fact in facts or []:
        text = str(getattr(fact, "fact", "") or "")
        if getattr(fact, "category", None) not in GOAL_CATEGORIES or not getattr(fact, "active", True):
            continue
        if (getattr(fact, "goal_status", None) or "active") != "active":
            continue
        if any(FAMILIES[family].search(text) for family in families) and text not in found:
            found.append(text)
    found.sort(key=lambda text: 0 if _INTENT_RE.search(text) else 1)
    return found[:MAX_GOALS]


def care_cancel_request(message: str) -> bool:
    """True when they ask to cancel, skip or stop care. Pure."""
    return bool(_CARE_CANCEL_RE.search(message or ""))


_NEGATED_BEFORE_RE = re.compile(r"\b(?:not|don'?t|do not|never|no|can'?t|cannot|won'?t|shouldn'?t|without|instead of|rather than)\b[^.?!\n]{0,15}$", re.IGNORECASE)


def encouraged_families(reply: str) -> List[str]:
    """The families a reply cheers on; a match right after "not", "don't", "never" and the like ("deciding not to smoke weed") is not encouragement. Pure."""
    text = reply or ""
    found = []
    for family, pattern in _ENCOURAGES.items():
        if any(not _NEGATED_BEFORE_RE.search(text[max(0, m.start() - 25):m.start()]) for m in pattern.finditer(text)):
            found.append(family)
    return found


def reply_goes_against(reply: str, goals: List[str]) -> bool:
    """True when the reply cheers on a family that one of their goals is about. Pure."""
    return any(FAMILIES[family].search(goal) for family in encouraged_families(reply) for goal in goals)


def goals_block(goals: List[str], care_pause: bool = False) -> str:
    """The reply prompt's section for their own goals on this (and the care pause when it applies), or "". Pure."""
    if not goals and not care_pause:
        return ""
    block = ""
    if goals:
        lines = "\n".join(f"- {goal}" for goal in goals)
        block = (
            "\nTHEIR OWN GOALS ON THIS (from what they told you before, as your memory noted it):\n" + lines + "\n"
            "What they said touches this. Never encourage going against these goals, and never lecture either. If it fits, name the goal once, gently, as theirs (that they told you they "
            "are working on it), and respect that the choice is theirs.\n"
        )
    return block + (CARE_PAUSE_NOTE if care_pause else "")
