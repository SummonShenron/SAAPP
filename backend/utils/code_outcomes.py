"""How Sonic's coding work ends, as aggregate counts: the signal a "get better at coding" goal would need (docs/goals-plan.md). Nothing here is a lever and nothing here stores
a word of anyone's code or messages: it adds to the daily counters (backend/utils/self_counters.py: no user, no text) and puts one flag on Sonic's own reply.

A CODING TURN is a tool-agent turn whose loop called at least one tool that reads or changes the repo or the connected folder (REPO_TOOLS). For each one:

- `code_turns`      it happened;
- `code_cap_hit`    the loop ran out of steps (or repeated itself) and was made to answer: it may have wanted more road;
- `code_edited`     it proposed an edit that did not come back as an error;
- `code_verified`   of those, it also ran the repo's tests or a snippet in the same turn (the habit a "verify before saying done" lever would raise).

The reply carries CODE_MARK, so the person's NEXT message can be read as what happened to the work:

- `code_followed`   a next message arrived (a sign-off counts: it is a turn that did not go back to the work);
- `code_redo`       the next message says it did not work: still failing, still broken, wrong, the same error.

`code_redo / code_followed` is crude on purpose, like "picked up" for the welcome goal: a new, unrelated bug reads as a redo, and a person who quietly fixes it themselves reads as moving on. It
is a trend to watch against itself week to week, not a score. All of it is an aggregate over everyone; no per-person copy exists.
"""
import re
from typing import Any, Dict, Optional

from backend.utils.self_counters import tally

CODE_MARK = "code_turn"

# The tools that read or change the repo or the connected folder (the same set the trace announces as "Reading from": backend/services/agent_workflow.py _REPO_SOURCE_TOOLS).
REPO_TOOLS = frozenset({
    "list_repo_tree", "read_repo_file", "search_code", "find_file", "trace_symbol", "search_literal",
    "diff_branches", "list_commits", "list_pull_requests", "propose_local_edits", "run_repo_tests", "run_snippet",
})
EDIT_TOOLS = frozenset({"propose_local_edits"})
VERIFY_TOOLS = frozenset({"run_repo_tests", "run_snippet"})

_REDO_RE = re.compile(
    r"\b(?:still|again)\s+(?:fail(?:s|ing|ed)?|broken|broke|not working|doesn'?t work|isn'?t working|erroring|errors?|throw(?:s|ing)|crash(?:es|ing)?|wrong|the same)\b"
    r"|\b(?:doesn'?t|does not|didn'?t|did not|isn'?t|is not|won'?t|still won'?t|not)\s+(?:work(?:ing)?|fix(?:ed)?|compile|run|pass|help|build|load)\b"
    r"|\bthat(?:'s| is| was)\s+(?:wrong|not right|incorrect|not it|broken)\b|\b(?:same|new)\s+(?:error|exception|traceback|failure)\b"
    r"|\b(?:tests?|build|it)\s+(?:is |are )?(?:still\s+)?(?:fail(?:s|ing|ed)?|broken|red)\b|\bbroke(?:n)?\s+(?:it|everything|the)\b",
    re.IGNORECASE,
)


def _called(tools: Dict[str, Dict[str, int]], names: frozenset, *, ok_only: bool = False) -> bool:
    for name in names:
        entry = tools.get(name)
        if entry and (entry.get("calls", 0) - (entry.get("errors", 0) if ok_only else 0)) > 0:
            return True
    return False


def summarize_turn(turn: Any) -> Optional[Dict[str, bool]]:
    """What a finished turn did, as flags, or None when it was not a coding turn. `turn` is the llm_turns.Turn for this chat turn."""
    tools = getattr(turn, "tools", None) or {}
    if turn is None or not getattr(turn, "loop_ran", False) or not _called(tools, REPO_TOOLS):
        return None
    edited = _called(tools, EDIT_TOOLS, ok_only=True)
    return {"cap_hit": bool(getattr(turn, "cap_hit", False)), "edited": edited, "verified": edited and _called(tools, VERIFY_TOOLS, ok_only=True)}


def tally_turn(counts: Dict[str, int], summary: Optional[Dict[str, bool]]) -> None:
    if not summary:
        return
    tally(counts, "code_turns")
    for flag, key in (("cap_hit", "code_cap_hit"), ("edited", "code_edited"), ("verified", "code_verified")):
        if summary.get(flag):
            tally(counts, key)


def followup(previous_marks: Optional[Dict[str, Any]], message: str) -> Optional[str]:
    """"redo" when the message after a coding reply says it did not work, "followed" for any other next message, None when the previous reply was not a coding turn."""
    if not (previous_marks or {}).get(CODE_MARK):
        return None
    return "redo" if _REDO_RE.search(message or "") else "followed"


def tally_followup(counts: Dict[str, int], outcome: Optional[str]) -> None:
    if outcome:
        tally(counts, "code_followed")
        if outcome == "redo":
            tally(counts, "code_redo")
