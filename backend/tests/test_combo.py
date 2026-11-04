"""Combos (Jack, 2026-11-01): an action that lands earns reading room, so a turn can edit, check, fix and check again; a turn that stops acting earns nothing, repeats earn less and a ceiling caps the turn.
Pure tests for backend/utils/combo.py, then the native loop driven by a scripted model."""
import asyncio

import pytest

from backend.utils import combo as cb

ACTIONS = {"propose_local_edits", "run_local_check", "run_local_command", "run_sandbox_tests", "propose_commit", "propose_git_commit", "propose_merge"}
APPLIED = "Proposed 1 edit(s) across 1 file(s): a.py. APPLIED: the files now contain the change."
PASSED = "LOCAL COMMAND `npm test` FINISHED with exit code 0 (3 s)."
FAILED = "LOCAL COMMAND `npm test` FINISHED with exit code 1 (3 s).\n1 failed"


def call(name, **args):
    return {"name": name, "args": args, "id": f"{name}-{len(args)}"}


def new():
    return cb.Combo(ACTIONS, cap=30)


def test_a_repeat_earns_half_then_one_then_nothing():
    assert [cb.steps_for(n) for n in (0, 1, 2, 3, 9)] == [4, 2, 1, 0, 0]


@pytest.mark.parametrize("name, result, hit", [
    ("propose_local_edits", APPLIED, True),
    ("propose_local_edits", "DECLINED: the person did not want it.", False),
    ("propose_local_edits", "NO DECISION: nobody answered.", False),
    ("propose_local_edits", "ERROR: old_string was not found.", False),
    ("run_local_command", PASSED, True),
    ("run_local_command", FAILED, True),                                           # a failing run is still a run: it is new information
    ("run_local_command", "ERROR: the runner is not connected.", False),
    ("run_local_command", "REFUSED: rm is never run here.", False),
    ("run_local_check", "LOCAL CHECK frontend PASSED (12 s).", True),
    ("run_local_check", "LOCAL CHECK frontend FAILED (12 s).", True),
    ("propose_git_commit", "Proposed the commit.", True),
    ("propose_commit", "ERROR: nothing to commit.", False),
    ("propose_merge", "", False),
])
def test_only_an_action_that_really_did_something_is_a_hit(name, result, hit):
    assert cb.is_hit(name, result) is hit


def test_an_applied_edit_and_a_check_that_ran_earn_four_and_a_whiff_earns_the_plain_one():
    combo = new()
    assert combo.after_step([call("propose_local_edits", n=1)], [APPLIED])["grant"] == 4
    assert combo.after_step([call("propose_local_edits", n=2)], ["DECLINED: no."])["grant"] == 1
    assert combo.after_step([call("run_local_command", n=3)], [PASSED])["grant"] == 4
    assert combo.after_step([call("read_repo_file", path="a.py")], ["text"]) == {"grant": 0, "hit": False, "failed_new": False}      # a read is not an action: it earns nothing


def test_a_failed_check_is_the_counter_hit_and_the_same_failure_again_earns_less_even_if_worded_differently():
    combo = new()
    first = combo.after_step([call("run_local_command", n=1)], [FAILED])
    assert first == {"grant": 4, "hit": True, "failed_new": True}
    again = combo.after_step([call("run_local_command", n=2, flag="--run")], [FAILED])
    assert again == {"grant": 2, "hit": True, "failed_new": False}
    third = combo.after_step([call("run_local_command", n=3, flag="--verbose")], [FAILED])
    assert third["grant"] == 1 and combo.failed_new == 1
    assert combo.after_step([call("run_local_command", n=4)], [FAILED])["grant"] == 0                # now it costs a step like a read


def test_an_applied_edit_makes_the_same_failure_news_again():
    combo = new()
    combo.after_step([call("run_local_command", n=1)], [FAILED])
    combo.after_step([call("propose_local_edits", n=2)], [APPLIED])
    assert combo.after_step([call("run_local_command", n=3)], [FAILED])["failed_new"] is True


def test_the_ceiling_caps_what_a_turn_can_earn_back_in_steps():
    combo = cb.Combo(ACTIONS, cap=6)
    assert combo.after_step([call("propose_local_edits", n=1)], [APPLIED])["grant"] == 4
    assert combo.after_step([call("propose_local_edits", n=2)], [APPLIED])["grant"] == 2           # only two left under the ceiling
    assert combo.after_step([call("propose_local_edits", n=3)], [APPLIED])["grant"] == 0
    assert combo.refunded == 6 and combo.ceiling_hit


def test_the_combo_drops_the_moment_a_step_has_no_landed_action_and_the_longest_run_is_kept():
    combo = new()
    combo.after_step([call("propose_local_edits", n=1)], [APPLIED])
    combo.after_step([call("run_local_command", n=2)], [FAILED])
    combo.after_step([call("propose_local_edits", n=3)], [APPLIED])
    combo.after_step([call("read_repo_file", path="a.py")], ["text"])                               # only reading: the chain ends
    combo.after_step([call("run_local_command", n=4)], [PASSED])
    assert combo.longest == 3 and combo.drops == 1 and combo.streak == 1
    counts = combo.counts()
    assert counts["combo_turns"] == 1 and counts["combo_hits"] == 4 and counts["combo_long"] == 1 and counts["combo_drops"] == 1


def test_counts_are_whole_numbers_only_and_empty_when_nothing_was_done():
    assert new().counts() == {}
    from backend.utils.self_counters import KEYS

    combo = new()
    combo.after_step([call("propose_local_edits", n=1)], [APPLIED])
    assert set(combo.counts()) <= KEYS and all(isinstance(v, int) for v in combo.counts().values())


def test_a_failed_check_may_widen_a_plan_turn_twice_at_most():
    combo = new()
    assert combo.may_widen_plan() and combo.may_widen_plan() and not combo.may_widen_plan()


# ---- the loop, with a scripted model ---------------------------------------------------------------------------------------------------------------

def _drive(script, results, max_steps, plan_budget=None):
    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    seen = []

    class LLM:
        async def ainvoke_tools(self, messages, offered):
            seen.append([str(getattr(m, "content", "")) for m in messages])
            if not script:
                return AIMessage(content="out of script")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return results[decision["tool_action"]]

    tools = [{"name": n, "description": n} for n in ("read_repo_file", "search_literal", "propose_local_edits", "run_local_command")]
    turn = llm_turns.begin("someone")
    try:
        out = asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="fix it", max_steps=max_steps, required_action=None, plan_budget=plan_budget))
    finally:
        llm_turns._current.set(None)
    return out, seen


def _tool(name, **args):
    return {"name": name, "args": args, "id": f"c-{name}-{sorted(args.items())}", "type": "tool_call"}


def _results(*checks):
    queue = list(checks)

    class Results(dict):
        def __getitem__(self, key):
            if key == "run_local_command":
                return queue.pop(0)
            return super().__getitem__(key)

    return Results({"propose_local_edits": APPLIED, "read_repo_file": "text", "search_literal": "a.py:3"})


def test_after_a_failing_check_there_is_room_to_read_the_fix_and_chain_on():
    """Two steps of budget. The old refund (one step per action) left two reads in total; a landed edit and a failed check now earn enough to read four files, fix it and prove it."""
    script = [_tool("propose_local_edits", n=1), _tool("run_local_command", n=2)] + [_tool("read_repo_file", path=f"f{i}.py", start_line=10, line_count=60) for i in range(4)] + \
             [_tool("propose_local_edits", n=3), _tool("run_local_command", n=4), "Fixed: the check passes now."]
    out, seen = _drive(script, _results(FAILED, PASSED), max_steps=2)
    assert out["final_answer"] == "Fixed: the check passes now." and out["show_work"] is False
    assert any(cb.FAILED_CHECK_NOTE in " ".join(messages) for messages in seen)                   # the model was told the failure is the new task


def test_a_turn_that_only_reads_still_runs_out_of_steps():
    script = [_tool("read_repo_file", path=f"f{i}.py", start_line=10, line_count=60) for i in range(8)]
    out, seen = _drive(script, _results(), max_steps=3)
    assert out["show_work"] is True and len(out["attempts"]) == 3


def test_the_same_failure_over_and_over_cannot_be_farmed_for_steps():
    """Every run fails the same way: 4, then 2, then 1, then nothing, so the turn is forced to answer long before the ceiling."""
    script = [_tool("run_local_command", n=i) for i in range(30)]
    out, seen = _drive(script, _results(*[FAILED] * 30), max_steps=2)
    assert out["show_work"] is True and len(out["attempts"]) < 30


def test_a_failed_check_on_a_plan_turn_widens_the_allowance_for_the_reads_the_fix_needs():
    from backend.utils import code_plan as cp

    plan = {"state": cp.APPROVED, "goal": "g", "steps": [{"id": "s1", "text": "Edit a.py", "files": ["a.py"], "status": "todo"}], "files": [{"path": "a.py", "why": "x"}], "tests": [], "unknowns": []}
    budget = cp.PlanBudget(plan, deviation_steps=1)
    script = [_tool("run_local_command", n=1)] + [_tool("search_literal", text=f"w{i}") for i in range(3)] + ["Found it."]
    out, seen = _drive(script, _results(FAILED), max_steps=30, plan_budget=budget)
    assert out["final_answer"] == "Found it." and budget.deviation_steps == 1 + cb.PLAN_FAIL_DEVIATIONS       # three searches are off the plan: with an allowance of 1 they would have stopped it


def test_the_loop_uses_the_combo_and_records_its_counts():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    source = (root / "backend/services/native_loop.py").read_text(encoding="utf-8")
    assert "combo = combo_rules.Combo(ACTION_TOOLS, ACTION_REFUND_CAP)" in source and "combo.after_step(calls, results)" in source
    assert "**combo.counts(forced and stop_reason in (\"\", \"step_limit\"))" in source


# ---- running out of steps while the chain is alive (2026-11-05) ----------------------------------------------------------------------------------

def test_a_turn_that_landed_an_action_and_ran_out_of_steps_is_counted_and_so_is_one_whose_chain_was_still_going():
    from backend.utils.self_counters import KEYS

    alive = new()
    alive.after_step([call("propose_local_edits", n=1)], [APPLIED])                    # the last step was a hit
    counts = alive.counts(ended_by_limit=True)
    assert counts["combo_limit_turns"] == 1 and counts["combo_limit_alive"] == 1 and set(counts) <= KEYS
    dropped = new()
    dropped.after_step([call("propose_local_edits", n=1)], [APPLIED])
    dropped.after_step([call("read_repo_file", path="a.py")], ["text"])                # then a read: the chain dropped
    counts = dropped.counts(ended_by_limit=True)
    assert counts["combo_limit_turns"] == 1 and "combo_limit_alive" not in counts
    assert "combo_limit_turns" not in alive.counts() and "combo_limit_turns" not in alive.counts(ended_by_limit=False)       # a turn that finished on its own is not one
    assert new().counts(ended_by_limit=True) == {}                                     # nothing landed: not a combo turn (the loop's own cap_hit_turns counts it)


def test_the_note_gives_the_true_terms_and_stops_when_the_turn_has_earned_all_it_may():
    note = cb.action_room_note(0, 30, 4)
    assert "hands back up to 4 tool steps" in note and "Do not make a change that was not asked for" in note
    assert "up to 2 tool steps" in cb.action_room_note(28, 30, 4)                        # never promises more than the ceiling leaves
    assert cb.action_room_note(30, 30, 4) == "" and cb.action_room_note(31, 30, 4) == ""


def test_the_low_steps_warning_on_a_turn_that_can_act_carries_the_note_and_one_that_cannot_act_does_not():
    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    def run(tool_names):
        seen, script = [], [_tool("read_repo_file", path=f"f{i}.py", start_line=10, line_count=60) for i in range(5)]

        class LLM:
            async def ainvoke_tools(self, messages, offered):
                seen.append(" ".join(str(getattr(m, "content", "")) for m in messages))
                item = script.pop(0) if script else "done"
                return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

        async def act(decision):
            return "text"

        llm_turns.begin("someone")
        try:
            asyncio.run(nl.run(llm=LLM(), tools=[{"name": n, "description": n} for n in tool_names], act=act, question="look", max_steps=4, required_action=None))
        finally:
            llm_turns._current.set(None)
        return " ".join(seen)

    can_act = run(["read_repo_file", "propose_local_edits"])
    assert "STEPS: you have 2 tool steps left" in can_act and "hands back up to 4 tool steps" in can_act
    read_only = run(["read_repo_file"])
    assert "STEPS: you have 2 tool steps left" in read_only and "hands back" not in read_only
