"""COMBOS: an action that lands earns reading room, so a coding turn can chain edit, check, fix, check without running out of steps, and a turn that stops acting earns nothing.

Jack, 2026-11-01: "if action steps refunded 4 budget steps, that would allow it to correct after tests and continue to chain actions together to complete the task, while still keeping a prevention for it
getting lost or stuck, because as soon as it doesn't act the chain ends. Almost like a combo in a fighting game." Until now an action (an edit, a check, a commit proposal) gave back the one step it used
(native_loop.ACTION_TOOLS), so after a failing check Sonic had no room left to read what the fix needed.

The rules (all pure, no model call, nothing logged but counts):
- A HIT is an action that really did something: an edit that was APPLIED, a check or command that RAN and came back with a result (passed or failed), or another action tool that did not error. A hit
  earns HIT_STEPS (4) steps back.
- A WHIFF (an error, a declined edit, a refused command, no answer) earns the plain one step, as before.
- DAMAGE SCALING: a check or command that comes back the same way again earns half (2), then one, then nothing, however it was worded; an applied edit in between makes the same output news again. A check that fails
  the same way twice with no change in between cannot be farmed.
- A FAILED CHECK that is new (not the same failure as before) is the counter-hit: the loop tells the model it has room to read what the fix needs, and a turn carrying out a plan gets a few more steps
  off the plan (PlanBudget.deviation_steps), because the failure is the new task.
- The CEILING is a cap on the steps handed back in one turn (CAP, 30), counted in steps, so a long run cannot spend more than the person agreed to.
- THE COMBO DROPS the moment a step has no hit: it earns nothing, so a turn that only reads runs out of budget by itself. The longest run of consecutive hit steps is counted, never what was in them.
"""
import os
import re
from typing import Any, Dict, FrozenSet, Iterable, List, Tuple

HIT_STEPS = int(os.getenv("TOOL_AGENT_COMBO_HIT_STEPS", "4"))
CAP = int(os.getenv("TOOL_AGENT_ACTION_REFUND_CAP", "30"))
PLAN_FAIL_DEVIATIONS = int(os.getenv("CODE_PLAN_FAIL_DEVIATIONS", "3"))
PLAN_FAIL_GRANTS = 2                    # how many times a failed check may widen a plan turn's allowance

_CHECK_HEAD = re.compile(r"LOCAL CHECK (\S+) (PASSED|FAILED)\b")
_COMMAND_HEAD = re.compile(r"LOCAL COMMAND `([^`]*)` FINISHED with exit code (-?\d+)")
_NOT_A_HIT = re.compile(r"^(?:ERROR|REFUSED|NOT RUN|NO DECISION|DECLINED)\b|\b(?:DECLINED|NO DECISION)\b", re.IGNORECASE)
_RAN_TOOLS = frozenset({"run_local_check", "run_local_command"})


def steps_for(repeats: int) -> int:
    """What a hit earns when the same result has already come back `repeats` times: full, half, one, and then nothing (the run costs a step like any read, so a turn cannot sit re-running a check
    that keeps failing the same way until the ceiling)."""
    if repeats <= 0:
        return HIT_STEPS
    if repeats == 1:
        return max(HIT_STEPS // 2, 1)
    return 1 if repeats == 2 else 0


def _failed(name: str, text: str) -> bool:
    """Whether a check or command that ran ended badly (a failed check, a non-zero exit code)."""
    head = _CHECK_HEAD.search(text)
    if head:
        return head.group(2) == "FAILED"
    command = _COMMAND_HEAD.search(text)
    return bool(command) and int(command.group(2)) != 0


def is_hit(name: str, result: Any) -> bool:
    """Whether an action's result says it really did something."""
    text = str(result or "")
    if name == "propose_local_edits":
        return " APPLIED:" in text
    if name in _RAN_TOOLS:
        return bool(_CHECK_HEAD.search(text) or _COMMAND_HEAD.search(text))
    return bool(text.strip()) and not _NOT_A_HIT.search(text[:300])


class Combo:
    """One turn's combo. `action_tools` is the loop's set of tools that are actions (native_loop.ACTION_TOOLS)."""

    def __init__(self, action_tools: Iterable[str], cap: int = CAP):
        self.action_tools: FrozenSet[str] = frozenset(action_tools)
        self.cap = cap
        self.refunded = 0
        self.hits = 0
        self.failed_new = 0
        self.streak = 0
        self.longest = 0
        self.drops = 0
        self.ceiling_hit = False
        self._seen: Dict[Tuple[str, str], int] = {}
        self.plan_grants = 0
        self.reviews = 0               # second readings that ran this turn
        self.blocked_reviews = 0       # of those, the ones that found something blocking

    def after_step(self, calls: List[Dict[str, Any]], results: List[Any]) -> Dict[str, Any]:
        """What one step's calls earn: {"grant": steps to hand back (within the ceiling), "hit": a call landed, "failed_new": a check failed in a way it has not before this turn}."""
        wanted = 0
        hit_here = False
        failed_new = False
        changed = False
        for call, result in zip(calls, results):
            name = str(call.get("name") or "")
            if name not in self.action_tools:
                continue
            text = str(result or "")
            if not is_hit(name, text):
                wanted += 1                                             # a whiff: the plain refund of the step it used
                continue
            repeats = 0
            if name in _RAN_TOOLS:
                # Judged by what came back, not by how it was asked (a trivially different command that fails the same way is the same failure), until an applied edit changes something.
                key = (name, text[:600])
                repeats = self._seen.get(key, 0)
                self._seen[key] = repeats + 1
            elif name == "propose_local_edits":
                changed = True
            wanted += steps_for(repeats)
            hit_here = True
            self.hits += 1
            if name in _RAN_TOOLS and repeats == 0 and _failed(name, text):
                failed_new = True
                self.failed_new += 1
        if changed:
            self._seen.clear()                                          # something changed: the same output afterwards is news again
        if hit_here:
            self.streak += 1
            self.longest = max(self.longest, self.streak)
        else:
            if self.streak:
                self.drops += 1
            self.streak = 0
        grant = max(min(wanted, self.cap - self.refunded), 0)
        if wanted > grant:
            self.ceiling_hit = True
        self.refunded += grant
        return {"grant": grant, "hit": hit_here, "failed_new": failed_new}

    def after_review(self, blocked: bool) -> int:
        """A second reading of an applied change (backend/utils/diff_review.py) just finished. One that found a BLOCKING problem is a counter-hit, like a new failed check: the first earns the full
        refund, the second half (the same scaling as a repeated result), within the turn's ceiling, so the turn has room to read and fix what was found. One that found nothing blocking earns nothing.
        Returns the steps to hand back."""
        self.reviews += 1
        if not blocked:
            return 0
        grant = max(min(steps_for(self.blocked_reviews), self.cap - self.refunded), 0)
        self.blocked_reviews += 1
        if grant == 0:
            self.ceiling_hit = True
        self.refunded += grant
        return grant

    def may_widen_plan(self) -> bool:
        """Whether a failed check may widen a plan turn's allowance now (twice a turn at most). Calling it uses one."""
        if self.plan_grants >= PLAN_FAIL_GRANTS:
            return False
        self.plan_grants += 1
        return True

    def counts(self) -> Dict[str, int]:
        """The turn's counts for the day's tally (backend/utils/self_counters.COMBO_KEYS): only whole numbers, nothing about what was done."""
        if not (self.hits or self.refunded or self.reviews):
            return {}
        counts = {"combo_turns": 1, "combo_hits": self.hits, "combo_steps_granted": self.refunded, "combo_failed_checks": self.failed_new, "combo_drops": self.drops,
                  "review_runs": self.reviews, "review_blocked": self.blocked_reviews}
        if self.ceiling_hit:
            counts["combo_ceiling_hit"] = 1
        if self.longest >= 3:
            counts["combo_long"] = 1
        return {key: value for key, value in counts.items() if value}


FAILED_CHECK_NOTE = (
    "A check just failed, and that is the new task. You have room to read what the fix needs: read the failing test and the code it runs against (search first, then read around the match), make the "
    "edit, then run the check again. If it fails the same way twice, stop and say what you found."
)
