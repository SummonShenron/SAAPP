"""Three checks on a final answer in the native loop, ported from the old ReAct loop (docs/react-sunset-plan.md, stage C). Each is free (no model call), mechanical and pure,
and each sends an answer back at most once: a correction is forced, and a second try goes through even if the check was a false positive.

1. A capability that was offered this turn is denied ("I can't edit your files" while propose_local_edits is among the tools).
2. A diff in the answer claims lines were already in a file that no real read of that file this turn ever showed.
3. Code in the answer when nothing real in the repo was looked at this turn (and the code is not quoted from earlier in the conversation).

The other checks the old loop had (a judge model for claims and idiom, a redeclaration lookup, the truncation and start-line notes, batching) were not ported: see the plan.
"""
import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from backend.utils.agent_utils import (
    _CAPABILITY_DENIAL_RE,
    _final_answer_has_code_block,
    _final_diff_disagrees_with_fetched_content,
    _has_successful_code_precedent_research,
    answer_code_quotes_conversation,
)

MAX_SEND_BACKS = 1
_MENU_TOOL_RE = re.compile(r"^-\s+([a-z_]+)\b")


def denied_capability(answer: str, offered: Iterable[str], watchlist: Optional[Sequence[Tuple[Any, str]]]) -> Optional[str]:
    """The name of an offered tool whose capability the answer denies, or None. `watchlist` is a list of (regex for how the capability is described, the tool's menu line start)."""
    if not watchlist or not _CAPABILITY_DENIAL_RE.search(answer or ""):
        return None
    names = set(offered)
    for capability_re, menu_start in watchlist:
        match = _MENU_TOOL_RE.match(menu_start or "")
        if match and match.group(1) in names and capability_re.search(answer):
            return match.group(1)
    return None


def denial_notice(tool: str) -> str:
    return (f"Your last answer denied having a capability, but '{tool}' is among your tools this turn: you do have it right now. Do not deny having it; "
            "if you have not used it yet, use it before answering.")


def ungrounded_diff_path(answer: str, attempts: List[dict]) -> Optional[str]:
    """The path of the first diff in the answer whose claimed existing lines no real read of that file ever returned this turn, or None."""
    return _final_diff_disagrees_with_fetched_content(answer or "", attempts)


def diff_notice(path: str) -> str:
    return (f"Your proposed diff edits {path}, but none of the lines it claims already exist there ever appeared in a real read_repo_file result for that exact path this turn. "
            f"Read {path} for real, quote the actual current lines you are changing, and rebuild the diff from what is really there before answering again: do not guess at the file's structure.")


def code_without_reading(answer: str, attempts: List[dict], conversation_text: str = "") -> bool:
    """Whether the answer contains code although nothing real in the repo was read or found this turn, and the code is not just quoted from earlier in the conversation."""
    return (
        _final_answer_has_code_block(answer or "")
        and not _has_successful_code_precedent_research(attempts)
        and not answer_code_quotes_conversation(answer or "", conversation_text)
    )


NO_READ_NOTICE = (
    "Your answer includes code, but you have not looked at any real code in this repo this turn (no successful read_repo_file, search_code, search_literal or find_file result yet). "
    "Find and read something comparable in the real repo before proposing an implementation: do not write code from general knowledge of how a project 'like this' is usually structured."
)
