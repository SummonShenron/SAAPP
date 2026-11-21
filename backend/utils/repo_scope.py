"""Is this conversation about the connected repo, or has the person said it is about a project OUTSIDE it? Read from their own words, in code, no model call (2026-11-20).

WHY. Sonic is wired to one connected repo or folder. Every rule that decides whether a turn gets the repo tools says "anything code-like, and any short follow-up to a coding turn, is repo work", and nothing could
say the opposite: a person who said "this isn't about the repo, it's my other project" had every later message sent to list_repo_tree, search_literal and read_repo_file anyway, and the turns were spent (and paid for)
looking for files that were never there. Jack's report: he could not talk about a coding project outside the connected repo.

WHAT. `outside_repo(messages)` reads the person's messages in order. A message that says the talk is outside the repo ("not about this repo", "my other project", "outside the connected folder") switches the repo off; a
message that brings it back ("back to the repo", "check the repo") switches it on again. The LAST such message wins, so it lasts the whole conversation until they say otherwise, with no new state to store (the messages
are the state, and they are saved with the conversation). A message that says nothing either way changes nothing.

The detector is narrow on purpose. A false "outside" costs the repo tools until the person says they are back (one sentence), a miss costs what it cost before. The wording needs a negation, "outside", or "my other
project" style phrase; plain "a separate repo" does not count (it is how a person talks about splitting THIS repo). Pure: no model, no network, never raises."""
import re
from typing import Any, Iterable, List

_NOUN = r"(?:repo(?:sitory)?|code ?base|folder|project|app|code)"
_THE = r"(?:this|the|my|our|that|your|sonic'?s)"
_CONNECTED = r"(?:(?:connected|local|current|linked|open)\s+)?"
_THIS_NOUN = rf"{_THE}\s+{_CONNECTED}{_NOUN}"

# "this isn't about the repo", "that's not in the connected folder", "nothing to do with this repo", "not related to the codebase".
_NEGATED = re.compile(
    rf"\b(?:not|isn'?t|is not|aren'?t|are not|wasn'?t|nothing)\s+(?:really\s+|actually\s+|even\s+)?(?:about|in|from|part of|related to|to do with|for|inside|on)\s+{_THIS_NOUN}\b"
    rf"|\bnothing to do with\s+{_THIS_NOUN}\b"
    rf"|\b(?:doesn'?t|does not|don'?t|do not)\s+(?:live|exist|belong)\s+(?:in|inside|on)\s+{_THIS_NOUN}\b"
    rf"|\bunrelated to\s+{_THIS_NOUN}\b",
    re.IGNORECASE,
)
# "outside the repo", "outside of the connected folder", "other than this repo", "apart from the codebase".
_OUTSIDE = re.compile(rf"\b(?:outside|beyond|apart from|other than|aside from|away from)\s+(?:of\s+)?{_THIS_NOUN}\b", re.IGNORECASE)
# "my other project", "another project", "a different repo", "a project that isn't connected": a project that is plainly somewhere else. "a separate repo" alone is NOT here (see the module note).
_ELSEWHERE = re.compile(
    rf"\b(?:my|our)\s+other\s+{_NOUN}\b"
    rf"|\b(?:about|on|for|with)\s+(?:another|a\s+different|a\s+totally\s+different|an?\s+unrelated)\s+(?:project|repo(?:sitory)?|code ?base|app)\b"
    rf"|\b(?:another|a\s+different|a\s+totally\s+different|an?\s+unrelated)\s+(?:project|repo(?:sitory)?|code ?base|app)\s+(?:of\s+mine|i(?:'m| am| have|'ve)|that\s+(?:i|we)|we(?:'re| are| have))\b"
    r"|\b(?:not|isn'?t|aren'?t)\s+(?:connected|linked|in\s+sonic)\b"
    r"|\b(?:not|isn'?t)\s+(?:a\s+)?(?:project|repo(?:sitory)?)\s+(?:you(?:'ve| have)|sonic\s+has)\s+(?:access|connected|linked)",
    re.IGNORECASE,
)
# The person brings the repo back: "back to the repo", "this repo again", or names a GitHub repo to read.
_RETURN = re.compile(
    rf"\bback\s+(?:to|on|in|at|into)\s+(?:{_THIS_NOUN}|sonic|saapp)\b"
    rf"|\b{_THIS_NOUN}\s+(?:again|now)\b"
    r"|\bgithub\.com/[\w.-]+/[\w.-]+",
    re.IGNORECASE,
)
# An order to use the repo: "check the repo", "look in the codebase", "search the connected folder". Only the repo's own names (not "project" or "code", which a person says of anything).
_ORDER = re.compile(
    rf"\b(?:look|check|search|read|open|dig|go|grep|scan)\w*\s+(?:in|at|through|into|inside|over)?\s*{_THE}\s+{_CONNECTED}(?:repo(?:sitory)?|code ?base|folder)\b",
    re.IGNORECASE,
)


def _text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def _is_person(message: Any) -> bool:
    if isinstance(message, str):
        return True
    return getattr(message, "type", "") == "human"


def says_back(text: str) -> bool:
    """True when this one message brings the repo back or names a GitHub repo to read, and does not itself say the talk is elsewhere. "Check the repo" brings it back; "look at my project, it's not in this repo" does not."""
    try:
        sample = str(text or "")
        if _RETURN.search(sample):
            return True
        return bool(_ORDER.search(sample)) and not _says_elsewhere(sample)
    except Exception:
        return False


def _says_elsewhere(sample: str) -> bool:
    return bool(_NEGATED.search(sample) or _OUTSIDE.search(sample) or _ELSEWHERE.search(sample))


def says_outside(text: str) -> bool:
    """True when this one message says the talk is about a project outside the connected repo, and does not also bring the repo back."""
    try:
        sample = str(text or "")
        if not sample.strip() or _RETURN.search(sample):
            return False
        return _says_elsewhere(sample)
    except Exception:
        return False


def outside_repo(messages: Iterable[Any]) -> bool:
    """Whether the conversation is, by the person's own last word on it, about a project outside the connected repo. The last of their messages that says anything either way decides; none at all is False (the repo, as before)."""
    try:
        outside = False
        for message in messages or []:
            if not _is_person(message):
                continue
            text = _text(message)
            if says_back(text):
                outside = False
            elif says_outside(text):
                outside = True
        return outside
    except Exception:
        return False


def repo_tool_prefixes(names: Iterable[str]) -> tuple:
    """The menu-line starts ("- read_repo_file —") for the named tools, for filtering a tool menu."""
    return tuple(f"- {name} —" for name in names)


def without_repo_lines(menu_lines: List[str], names: Iterable[str]) -> List[str]:
    """The menu with the repo and folder tools taken out."""
    prefixes = repo_tool_prefixes(names)
    return [line for line in menu_lines if not line.startswith(prefixes)]


OUTSIDE_NOTE = (
    "NOT THIS REPO: the person has said this conversation is about a project OUTSIDE the connected repo or folder. There are no repo or folder tools this turn, and none are needed: do not look for files, do not "
    "say you are checking the repo, and do not ask them to connect it. Talk about their project from what they tell you and what they paste, say plainly when you would need to see a file you cannot, and ask them "
    "to paste it. If they bring the repo back (\"back to the repo\", \"check the repo\"), the tools return."
)
