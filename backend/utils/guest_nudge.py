"""The mid-chat nudge for the shared portfolio guest (docs/guest-mode-plan.md, step 4).

When a guest asks for something only an account can do (work in their own project, run their own tests, remember them, tidy a folder), Sonic answers as it always did (the tools in front of it
already say what a guest cannot have), and the page shows one card under that reply: what an account adds, and a sign-up button. This module only decides, from the words of the message, which card.

No model call: a few plain patterns, chosen for precision over reach. A missed nudge costs nothing (the "With an account" tab and the allowance line are still there); a wrong one would be an
irritating pop-up on a question that had nothing to do with it. So each pattern needs the person to say "my" project, tests, folder, or to say "remember". The keys are the ones the page knows its
account cards by (local/src/welcomeCards.ts, `key`), and the counter group `guest_nudge` (backend/utils/self_counters.py) counts each, with no text.
"""
import re
from typing import Optional

from backend.utils.guest_limits import is_portfolio_guest

FEATURES = ("checks", "fix", "tidy", "memory")      # the order they are tried in: the most specific first

_PATTERNS = {
    # "run my tests", "run our test suite on my machine", "run the linter on my computer"
    "checks": re.compile(
        r"\brun\s+(?:my|our)\s+(?:project'?s?\s+)?(?:unit\s+|integration\s+)?(?:tests?|test\s+suite|checks?|linter|lint|build|type\s?check)\b"
        r"|\b(?:run|execute)\b.{0,40}\bon\s+my\s+(?:machine|computer|laptop|pc)\b"
    ),
    # "connect my folder", "fix the bug in my code", "edit my project", "look through my repo and change ..."
    "fix": re.compile(
        r"\b(?:connect|open|link|use|read|look\s+(?:at|through))\s+my\s+(?:\w+\s+)?(?:project|folder|repo|repository|codebase|code)\b"
        r"|\b(?:fix|edit|change|refactor|update|patch|debug|add\s+\w+\s+to)\b.{0,50}\b(?:in|of|to|for)\s+my\s+(?:\w+\s+)?(?:project|codebase|repo|repository|code|app|files?)\b"
    ),
    # "tidy up my downloads", "organize my messy desktop folder", "sort my files"
    "tidy": re.compile(
        r"\b(?:tidy|clean\s+up|organi[sz]e|sort)\b.{0,30}\bmy\s+(?:\w+\s+)?(?:downloads|desktop|documents|folder|files)\b"
    ),
    # "remember that I like ...", "don't forget my ...", "keep in mind I ...", "from now on, ..."
    "memory": re.compile(
        r"\bremember\s+(?:that|this|me|my|i|i'm|i’m)\b|\bdon['’]?t\s+forget\b|\bkeep\s+in\s+mind\b|\bsave\s+(?:this|that)\s+(?:for|to)\b|\bnext\s+time\s+(?:i|we)\b|\bfrom\s+now\s+on\b"
    ),
}


def feature_for(username: Optional[str], message: Optional[str]) -> Optional[str]:
    """The account feature the shared guest's message is asking for (one of FEATURES), or None: for anyone else, and for a message that asks for none of them."""
    if not message or not is_portfolio_guest(username):
        return None
    text = " ".join(str(message).lower().split())
    for feature in FEATURES:
        if _PATTERNS[feature].search(text):
            return feature
    return None
