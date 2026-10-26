"""How Sonic's coding work ends, as aggregate counts: the signal a "get better at coding" goal would need (docs/goals-plan.md). Nothing here is a lever and nothing here stores
a word of anyone's code or messages: it adds to the daily counters (backend/utils/self_counters.py: no user, no text) and puts one flag on Sonic's own reply.

A CODING TURN is a tool-agent turn whose loop called at least one tool that reads or changes the repo or the connected folder (REPO_TOOLS). For each one:

- `code_turns`      it happened;
- `code_cap_hit`    the loop ran out of steps (or repeated itself) and was made to answer: it may have wanted more road;
- `code_edited`     it proposed an edit that did not come back as an error;
- `code_verified`   of those, it also ran the repo's tests, a snippet or the sandbox tests in the same turn (the habit a "verify before saying done" lever would raise);
- `code_edit_calls` / `code_edit_errors`   how many times it tried to propose an edit, and how many of those attempts failed outright (text not found, ambiguous, refused): an edit that never got as far
                    as a card is work that was wrong before anyone saw it;
- `code_test_runs` how many times it ran tests or a snippet to check its work.

WHAT HAPPENS TO A PROPOSED EDIT is reported by the browser (POST /api/local-edits/{id}/decision) and counted once per proposal, by size (`small`: one file and at most SMALL_EDIT_LINES changed lines; everything
else `large`, so the numbers can show success falling as the work grows):

- `code_edit_applied`, `code_edit_declined`, `code_edit_undone` (and the `_small` / `_large` splits). Applied is not "right" (people apply and fix), but a decline or an undo is a plain no.

A proposal that TOUCHES A FILE the person applied from Sonic within the last REWORK_WINDOW_MINUTES is counted as rework (`code_proposal_rework`, out of `code_proposals`): the first attempt probably needed fixing. It is a
proxy, not a verdict (iterating on a feature looks the same), so it is a rate to watch against itself.

The reply carries CODE_MARK, so the person's NEXT message can be read as what happened to the work:

- `code_followed`   a next message arrived (a sign-off counts: it is a turn that did not go back to the work);
- `code_redo`       the next message says it did not work, in words a phrase list knows: still failing, still broken, wrong, the same error;
- `code_frustrated` the next message READ as frustrated: the reasoner's reading of this very message (backend/utils/emotion_utils.py: valence "frustrated", read on this turn, at least FRUSTRATION_MIN), which costs
                    nothing extra and hears pushback the phrase list never will ("ugh no.....!", "come on"). Frustration may be about something else, so it is a signal, not a verdict;
- `code_pushback`   either of the two, counted once: this is what the goal's headline subtracts.

`code_redo / code_followed` is crude on purpose, like "picked up" for the welcome goal: a new, unrelated bug reads as a redo, and a person who quietly fixes it themselves reads as moving on. It
is a trend to watch against itself week to week, not a score. All of it is an aggregate over everyone; no per-person copy exists.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.utils.self_counters import tally

CODE_MARK = "code_turn"

# The tools that read or change the repo or the connected folder (the same set the trace announces as "Reading from": backend/services/agent_workflow.py _REPO_SOURCE_TOOLS).
REPO_TOOLS = frozenset({
    "list_repo_tree", "read_repo_file", "search_code", "find_file", "trace_symbol", "search_literal",
    "diff_branches", "list_commits", "list_pull_requests", "propose_local_edits", "run_repo_tests", "run_snippet",
})
EDIT_TOOLS = frozenset({"propose_local_edits"})
TEST_TOOLS = frozenset({"run_repo_tests", "run_snippet", "run_sandbox_tests"})
VERIFY_TOOLS = TEST_TOOLS
SMALL_EDIT_LINES = 20
FRUSTRATION_MIN = float(os.getenv("CODE_FRUSTRATION_MIN", "0.4"))
REWORK_WINDOW_MINUTES = 45
OUTCOMES = ("applied", "declined", "undone")

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


def _count(tools: Dict[str, Dict[str, int]], names: frozenset, key: str) -> int:
    return sum(int((tools.get(name) or {}).get(key, 0)) for name in names)


def summarize_turn(turn: Any) -> Optional[Dict[str, Any]]:
    """What a finished turn did (flags and small counts), or None when it was not a coding turn. `turn` is the llm_turns.Turn for this chat turn."""
    tools = getattr(turn, "tools", None) or {}
    if turn is None or not getattr(turn, "loop_ran", False) or not _called(tools, REPO_TOOLS):
        return None
    edited = _called(tools, EDIT_TOOLS, ok_only=True)
    return {
        "cap_hit": bool(getattr(turn, "cap_hit", False)), "edited": edited, "verified": edited and _called(tools, VERIFY_TOOLS, ok_only=True),
        "edit_calls": _count(tools, EDIT_TOOLS, "calls"), "edit_errors": _count(tools, EDIT_TOOLS, "errors"), "test_runs": _count(tools, TEST_TOOLS, "calls"),
    }


def _add(counts: Dict[str, int], key: str, amount: int) -> None:
    from backend.utils.self_counters import KEYS

    if amount > 0 and key in KEYS:
        counts[key] = counts.get(key, 0) + int(amount)


def size_bucket(changes: Dict[str, Any]) -> str:
    """"small" for an edit to one file of at most SMALL_EDIT_LINES changed lines, otherwise "large". `changes` is {path: (lines added, lines removed)}. Pure."""
    lines = sum(int(a) + int(d) for a, d in (changes or {}).values())
    return "small" if len(changes or {}) <= 1 and lines <= SMALL_EDIT_LINES else "large"


def outcome_counts(outcome: str, changes: Dict[str, Any]) -> Dict[str, int]:
    """The counters for one proposal's outcome: {"code_edit_applied": 1, "code_edit_applied_small": 1}. Empty for an outcome that is not one of OUTCOMES. Pure."""
    if outcome not in OUTCOMES:
        return {}
    return {f"code_edit_{outcome}": 1, f"code_edit_{outcome}_{size_bucket(changes)}": 1}


def tally_turn(counts: Dict[str, int], summary: Optional[Dict[str, Any]], plan_phase: Optional[str] = None) -> None:
    if not summary:
        return
    tally(counts, "code_turns")
    if plan_phase == "plan":            # plan mode (backend/utils/code_plan.py): the turn that reads and writes the plan, and the turns that carry an approved one out
        tally(counts, "code_plan_turns")
        if summary.get("cap_hit"):
            tally(counts, "code_plan_turn_cap_hit")
    elif plan_phase == "execute":
        tally(counts, "code_plan_exec_turns")
        if summary.get("cap_hit"):
            tally(counts, "code_plan_exec_cap_hit")
        if summary.get("edited"):
            tally(counts, "code_plan_exec_edited")
    _add(counts, "code_edit_calls", summary.get("edit_calls", 0))
    _add(counts, "code_edit_errors", summary.get("edit_errors", 0))
    _add(counts, "code_test_runs", summary.get("test_runs", 0))
    for flag, key in (("cap_hit", "code_cap_hit"), ("edited", "code_edited"), ("verified", "code_verified")):
        if summary.get(flag):
            tally(counts, key)


def followup(previous_marks: Optional[Dict[str, Any]], message: str) -> Optional[str]:
    """"redo" when the message after a coding reply says it did not work, "followed" for any other next message, None when the previous reply was not a coding turn."""
    if not (previous_marks or {}).get(CODE_MARK):
        return None
    return "redo" if _REDO_RE.search(message or "") else "followed"


def is_frustrated(emotional_state: Any) -> bool:
    """True when the reasoner read THIS message as frustrated, at least FRUSTRATION_MIN strong. A feeling carried over from earlier in the conversation does not count (it is not about this reply). Pure."""
    state = emotional_state if isinstance(emotional_state, dict) else {}
    try:
        return bool(state.get("read_this_turn")) and str(state.get("valence")) == "frustrated" and float(state.get("intensity", 0.0)) >= FRUSTRATION_MIN
    except (TypeError, ValueError):
        return False


def pushback_counts(followup: Optional[str], emotional_state: Any) -> Dict[str, int]:
    """The counters for the message after a coding reply: code_frustrated when it read as frustrated, and code_pushback (once) when it did or when the phrase list said "redo". {} when the previous reply was
    not coding work. Pure."""
    if followup is None:
        return {}
    frustrated = is_frustrated(emotional_state)
    counts: Dict[str, int] = {}
    if frustrated:
        counts["code_frustrated"] = 1
    if frustrated or followup == "redo":
        counts["code_pushback"] = 1
    return counts


def rework_paths(changes: Dict[str, Any], applied: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """The paths of a new proposal that the person applied from Sonic within the last REWORK_WINDOW_MINUTES. `applied` is {path: {"at": iso time, ...}} (backend/utils/applied_changes.py). Pure."""
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(minutes=REWORK_WINDOW_MINUTES)
    found = []
    for path in changes or {}:
        try:
            at = datetime.fromisoformat(str((applied or {}).get(path, {}).get("at")))
        except (ValueError, AttributeError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if cutoff <= at <= moment + timedelta(minutes=1):
            found.append(path)
    return found


def proposal_counts(changes: Dict[str, Any], applied: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, int]:
    """The counters for one proposal that reached the person as a card: code_proposals, and code_proposal_rework when it touches a file applied a short while ago. Pure."""
    counts = {"code_proposals": 1}
    if rework_paths(changes, applied, now):
        counts["code_proposal_rework"] = 1
    return counts


# The reply is stamped with the ids of the working leanings it used (backend/utils/leanings.py), so the next message can be counted against them.
LEANINGS_MARK = "leanings_used"
_PRAISE_RE = re.compile(
    r"\b(?:perfect|thank(?:s| you)|thx|great|awesome|nice(?: work| one)?|love (?:it|that|this)|exactly|that'?s (?:it|right|what)|looks (?:great|good)|works? (?:now|great|perfectly)|worked|fixed it|good (?:job|call|idea|point)|helpful|genius|brilliant|well done|yay|woo)\b",
    re.IGNORECASE,
)


def is_praise(message: str) -> bool:
    """Whether a short message praises or thanks and says nothing that reads as pushback. Crude on purpose, and only ever counted, never acted on. Pure."""
    text = message or ""
    return len(text.split()) <= 40 and bool(_PRAISE_RE.search(text)) and not _REDO_RE.search(text)


def tally_followup(counts: Dict[str, int], outcome: Optional[str]) -> None:
    if outcome:
        tally(counts, "code_followed")
        if outcome == "redo":
            tally(counts, "code_redo")
