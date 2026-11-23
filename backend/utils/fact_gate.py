"""Keeps passing work status out of the stored facts (the write side of the 2026-11-23 memory review).

THE FAILURE. Compaction promoted lines like "The user is actively developing an AI project" or "Jack is working on an error lambda" into facts. With no name, date or outcome,
such a line is close in meaning to nearly anything said about work, so it kept taking one of the 5 fact slots a reply gets, and it read as current for ever. 53 of them were
archived by hand (run vague-2026-11-23). This stops new ones.

THE RULE, measured on that hand-sorted set (53 archived, 25 kept) and the other 861 stored facts: a fact is dropped when it is a PROJECT fact about work or their own projects,
opens with a status phrase ("is currently refining", "is working on", "is preparing to"), and names nothing specific (no digit, capitalised or camelCase name, code name or quoted
term). That caught 32 of the 53, 1 of the 25 kept and 1 of the other 861. A wider rule caught 39 of the 53 but would also have dropped 49 of the other 861, mostly lasting
descriptions ("is developing a personal project called errAgent"): a gate that throws facts away has to be the narrow one. Goals and career facts are never dropped: the same wording there ("is working on managing alcohol cravings", "is currently employed as a
contractor") was what was worth keeping. The note the fact came from is kept either way, so the detail is still in memory with its date.

Only facts a model inferred go through this (compaction); something the person asked Sonic to remember is saved as they said it. Pure, no model call.
"""
import re

GATED_CATEGORIES = frozenset({"project"})
GATED_DOMAINS = frozenset({"work", "own"})

# A status phrase: either a "right now" word in front of any doing verb ("is currently refining", "is actively developing"), or a verb that is itself only ever
# about the present ("is working on", "is preparing to"). A plain "is developing X" without a "right now" word was left alone: on the real store it was mostly a
# lasting description of a project ("is developing a personal project called errAgent"), not a passing task.
_WHO = r"^(?:the user|jack|he|she|they)\s+(?:is\s+|are\s+)?"
STATUS_RE = re.compile(
    _WHO + r"(?:(?:actively|currently|now|still)\s+(?:is\s+)?\w+"
    r"|(?:working on|preparing (?:for|to)|focused on|engaged in|in the process of|undergoing|following the progress|participating in)\b)",
    re.IGNORECASE,
)
_NOT_NAMES = frozenset({"jack", "the", "user", "he", "his", "she", "her", "they", "their", "ai", "i"})
_CAPITAL_RE = re.compile(r"\b[A-Z][A-Za-z]{2,}")
_DIGIT_RE = re.compile(r"\d")
# Names that are not capitalised words: camelCase (errAgent, xMatters), file paths and code names (local_workspace.py, feat/notes), and anything quoted.
_CODE_NAME_RE = re.compile(r"\b[a-z]+[A-Z]\w*|\w[\w-]*[/_.]\w|['\"‘“][^'\"’”]{2,}['\"’”]")


def names_something(text: str) -> bool:
    """True when the fact holds a digit, a capitalised name (not the person or a pronoun: _NOT_NAMES), a camelCase or code name, or something quoted. Pure."""
    rest = text or ""
    if _DIGIT_RE.search(rest) or _CODE_NAME_RE.search(rest):
        return True
    return any(word.lower() not in _NOT_NAMES for word in _CAPITAL_RE.findall(rest))


def is_passing_status(text: str, category: str, domain: str) -> bool:
    """True for a fact that should not be stored: a project fact about work or their own projects that only says what they are doing now, naming nothing. Pure."""
    text = " ".join(str(text or "").split())
    if (category or "") not in GATED_CATEGORIES or (domain or "") not in GATED_DOMAINS:
        return False
    return bool(STATUS_RE.search(text)) and not names_something(text)
