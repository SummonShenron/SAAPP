"""The rhythm of a coding turn: read a little, act, check, assess, read what the result points at, act again.

A real session (2026-10-28, the trace excerpt task) read, read, read and read before it made one edit, and after an edit it read again instead of running the project's check, so the budget was gone before
anything was tested and the person pressed Continue about nine times. The loop's checkpoints count steps in total (a third and two thirds of the budget), which says nothing about whether the model has
acted. This keeps the two counts that matter, in code and with no model call:

- READS IN A ROW since the last act or check. After an edit has been applied, a streak of READS_IN_A_ROW reads (3) gets one short notice: act on what you know, or name the one thing you must read, in the same
  step as the action. Before the first edit the existing "your next action must be propose_local_edits" nudge (agent_workflow._LOCAL_EDIT_REQUIRED_ACTION) already applies, so this adds nothing there.
- READS BEFORE THE FIRST ACT, and whether an applied edit was followed by a check in the very next step. Counts only (backend/utils/code_outcomes.py), so Sonic Health can say whether the rhythm improved.

Pure: nothing here calls a model, a tool or the network, and nothing keeps text, only counts."""
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

READS_IN_A_ROW = max(int(os.getenv("LOOP_READS_IN_A_ROW", "3")), 2)

# What counts as reading: looking at the repository or the folder. Everything else a loop can do (an edit, a check, a command) is acting.
READ_TOOLS = frozenset({"read_repo_file", "search_code", "search_literal", "find_file", "trace_symbol", "list_repo_tree"})
EDIT_TOOLS = frozenset({"propose_local_edits"})
CHECK_TOOLS = frozenset({"run_local_check", "run_local_command", "run_sandbox_tests", "run_repo_tests", "run_snippet"})
ACT_TOOLS = EDIT_TOOLS | CHECK_TOOLS

STREAK_NOTICE = (
    "READING STREAK: you have read {streak} times in a row since your last edit or check. Act on what you know now: make the next edit, or run the check on what you changed. If something specific "
    "is still missing, ask for that one read in the SAME step as the action, and say what it is for. Reading more first is how the budget gets spent with nothing tested."
)


def check_hint(offered: Sequence[str], check_names: Sequence[str] = ()) -> str:
    """The line added to an APPLIED result when a way to check the change exists, or "" when none does. `offered` are this turn's tool names; `check_names` the person's named checks."""
    names = [str(n) for n in check_names if str(n).strip()]
    if "run_local_check" in offered and names:
        return f"NEXT: the change is applied. Run a check on it now with run_local_check ({', '.join(names[:4])}), then read only what its result points at."
    if "run_local_command" in offered:
        return "NEXT: the change is applied. Check it now: run the project's tests or type check for what you changed with run_local_command, then read only what its result points at."
    if "run_sandbox_tests" in offered:
        return "NEXT: the change is applied. Check it now with run_sandbox_tests on the files you changed, then read only what its result points at."
    return ""


class LoopRhythm:
    """Follows one loop's steps. `after_step(calls)` after each step that ran tools; `notice()` before the model's next decision; `summary()` at the end."""

    def __init__(self, reads_in_a_row: Optional[int] = None) -> None:
        self.reads_in_a_row = reads_in_a_row or READS_IN_A_ROW
        self.streak = 0
        self.reads_before_first_act: Optional[int] = None
        self._reads = 0
        self.applies = 0
        self.applies_checked = 0
        self.notices = 0
        self._awaiting_check = False
        self._noticed_at: set = set()

    def after_step(self, calls: Sequence[Tuple[str, Any]]) -> None:
        """`calls` is [(tool name, result text)] for one step. A step with any edit or check is an act (it ends the reading streak, even when it failed: it was an attempt); a step with only reads
        adds to the streak; a step with neither (a draft, a note) changes nothing."""
        names = [str(name or "") for name, _ in calls]
        acted = any(name in ACT_TOOLS for name in names)
        if self._awaiting_check:
            self._awaiting_check = False
            if any(name in CHECK_TOOLS for name in names):
                self.applies_checked += 1
        if acted:
            if self.reads_before_first_act is None:
                self.reads_before_first_act = self._reads
            self.streak = 0
            self._noticed_at.clear()
            if any(name in EDIT_TOOLS and " APPLIED:" in str(result) for name, result in calls):
                self.applies += 1
                self._awaiting_check = True
        elif any(name in READ_TOOLS for name in names):
            self._reads += 1
            self.streak += 1

    def notice(self) -> Optional[str]:
        """The reading-streak notice when it is due, once for each streak length that is a multiple of the limit; only after an edit has been applied (before it, the proposal nudge applies)."""
        if self.applies <= 0 or self.streak < self.reads_in_a_row or self.streak % self.reads_in_a_row != 0 or self.streak in self._noticed_at:
            return None
        self._noticed_at.add(self.streak)
        self.notices += 1
        return STREAK_NOTICE.format(streak=self.streak)

    def summary(self) -> Dict[str, Any]:
        """Counts only: reads before the first act (None when it never acted), edits applied, applied edits checked in the next step, and notices shown."""
        return {"reads_before_first_act": self.reads_before_first_act, "applies": self.applies, "applies_checked": self.applies_checked, "notices": self.notices}


def counts_from(summary: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """The counter increments for one turn's rhythm summary (keys in self_counters.RHYTHM_KEYS). Empty when there was none."""
    if not isinstance(summary, dict):
        return {}
    counts: Dict[str, int] = {"code_rhythm_turns": 1}
    first = summary.get("reads_before_first_act")
    if isinstance(first, int) and not isinstance(first, bool) and first >= 0:
        counts["code_rhythm_acted_turns"] = 1
        if first:
            counts["code_rhythm_reads_before_first_act"] = first
    for source, key in (("applies", "code_rhythm_applies"), ("applies_checked", "code_rhythm_applies_checked"), ("notices", "code_rhythm_streak_notices")):
        value = summary.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            counts[key] = value
    return counts
