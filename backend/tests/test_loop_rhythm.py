"""The rhythm of a coding turn (backend/utils/loop_rhythm.py, native_loop.py): after an applied edit the model is told, in code, to run the check; after a streak of reads it is told to act; and the counts that say
whether this helped are kept. From the 2026-10-28 session: read, read, read, read, then an edit, then more reading, and nothing tested before the budget ran out."""
import asyncio

import pytest

from backend.services import native_loop
from backend.utils import code_outcomes, llm_turns, loop_rhythm, self_counters
from backend.utils.loop_rhythm import LoopRhythm, check_hint, counts_from

@pytest.fixture(autouse=True)
def _no_checkpoints(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")          # a checkpoint is an extra model call that would consume a scripted reply


APPLIED = "Proposed edits to src/app.py. APPLIED: the person applied the edits, so those files now contain them."


def step(rhythm, *calls):
    rhythm.after_step(list(calls))


# ---- the pure part -----------------------------------------------------------------------------------------------------------------------------

def test_reads_before_the_first_act_are_counted_and_an_edit_or_check_is_the_first_act():
    r = LoopRhythm()
    step(r, ("read_repo_file", "text"))
    step(r, ("search_literal", "hits"), ("read_repo_file", "text"))
    step(r, ("propose_local_edits", APPLIED))
    assert r.summary() == {"reads_before_first_act": 2, "applies": 1, "applies_checked": 0, "notices": 0}
    c = LoopRhythm()
    step(c, ("read_repo_file", "x"))
    step(c, ("run_local_check", "PASSED"))
    assert c.summary()["reads_before_first_act"] == 1                                       # a check is an act too
    never = LoopRhythm()
    step(never, ("read_repo_file", "x"))
    assert never.summary()["reads_before_first_act"] is None


def test_a_streak_of_reads_after_an_applied_edit_gets_one_notice_and_acting_starts_the_count_again():
    r = LoopRhythm()
    step(r, ("propose_local_edits", APPLIED))
    for _ in range(2):
        step(r, ("read_repo_file", "x"))
        assert r.notice() is None
    step(r, ("search_code", "x"))
    notice = r.notice()
    assert notice and "READING STREAK: you have read 3 times in a row" in notice and "SAME step" in notice
    assert r.notice() is None                                                                # once for this length
    for _ in range(3):
        step(r, ("read_repo_file", "x"))
    assert "read 6 times" in r.notice()                                                      # and again at the next multiple
    step(r, ("run_local_command", "ok"))
    assert r.streak == 0 and r.notice() is None
    step(r, ("read_repo_file", "x"), ("find_file", "y"))
    assert r.streak == 1                                                                     # a step with several reads is one step


def test_no_streak_notice_before_the_first_applied_edit_because_the_proposal_nudge_covers_that():
    r = LoopRhythm()
    for _ in range(6):
        step(r, ("read_repo_file", "x"))
    assert r.notice() is None
    step(r, ("propose_local_edits", "ERROR: old_string not found"))                         # an attempt, not an applied edit
    for _ in range(3):
        step(r, ("read_repo_file", "x"))
    assert r.applies == 0 and r.notice() is None and r.streak == 3


def test_a_failed_attempt_still_ends_the_streak_and_a_declined_edit_is_not_an_apply():
    r = LoopRhythm()
    step(r, ("read_repo_file", "x"))
    step(r, ("propose_local_edits", "DECLINED: not that"))
    assert r.streak == 0 and r.applies == 0 and r.reads_before_first_act == 1


def test_an_applied_edit_counts_as_checked_only_when_the_very_next_step_runs_a_check():
    r = LoopRhythm()
    step(r, ("propose_local_edits", APPLIED))
    step(r, ("run_local_check", "PASSED"))
    step(r, ("propose_local_edits", APPLIED))
    step(r, ("read_repo_file", "x"))
    step(r, ("run_local_check", "PASSED"))                                                   # too late: a read came first
    step(r, ("propose_local_edits", APPLIED), ("run_local_command", "ok"))                  # the same step does not count either
    assert r.applies == 3 and r.applies_checked == 1


def test_steps_with_neither_a_read_nor_an_act_change_nothing():
    r = LoopRhythm()
    step(r, ("propose_local_edits", APPLIED))
    step(r, ("read_repo_file", "x"))
    step(r, ("save_draft", "ok"), ("continue_working", ""))
    assert r.streak == 1 and r.notices == 0


def test_the_limit_can_be_set_and_is_never_below_two(monkeypatch):
    r = LoopRhythm(reads_in_a_row=2)
    step(r, ("propose_local_edits", APPLIED))
    step(r, ("read_repo_file", "x"))
    step(r, ("read_repo_file", "x"))
    assert r.notice() is not None
    assert loop_rhythm.READS_IN_A_ROW >= 2


def test_the_hint_names_the_check_that_exists_and_is_empty_when_none_does():
    assert "run_local_check (tests, types)" in check_hint(["read_repo_file", "run_local_check"], ["tests", "types"])
    assert "run_local_command" in check_hint(["propose_local_edits", "run_local_command"], [])
    assert "run_sandbox_tests" in check_hint(["propose_local_edits", "run_sandbox_tests"], [])
    assert check_hint(["propose_local_edits", "read_repo_file"], []) == "" and check_hint([], ["tests"]) == ""
    assert check_hint(["run_local_check", "run_local_command"], ["tests"]).startswith("NEXT: the change is applied. Check it with run_local_check")
    for offered in (["run_local_check"], ["run_local_command"], ["run_sandbox_tests"]):
        assert "if the tests for it do not exist yet, write them first" in check_hint(offered, ["tests"])            # a check that cannot exercise the change yet is not the next step


def test_the_counter_increments_use_only_known_keys_and_only_nonzero_amounts():
    assert counts_from(None) == {} and counts_from("x") == {}
    assert counts_from({"reads_before_first_act": None, "applies": 0, "applies_checked": 0, "notices": 0}) == {"code_rhythm_turns": 1}
    got = counts_from({"reads_before_first_act": 4, "applies": 2, "applies_checked": 1, "notices": 1})
    assert got == {"code_rhythm_turns": 1, "code_rhythm_acted_turns": 1, "code_rhythm_reads_before_first_act": 4, "code_rhythm_applies": 2, "code_rhythm_applies_checked": 1, "code_rhythm_streak_notices": 1}
    assert set(got) <= set(self_counters.KEYS) and set(self_counters.RHYTHM_KEYS) <= self_counters.KEYS
    assert counts_from({"reads_before_first_act": 0})["code_rhythm_acted_turns"] == 1 and "code_rhythm_reads_before_first_act" not in counts_from({"reads_before_first_act": 0})


# ---- through the native loop ---------------------------------------------------------------------------------------------------------------------

class Reply:
    def __init__(self, calls=(), content=""):
        self.tool_calls, self.content = list(calls), content


def call(name, n, **args):
    return {"name": name, "args": args or {"path": f"f{n}.py"}, "id": f"c{n}"}


class ScriptedLLM:
    """Replies from a script; records the text of the notices (human messages) in the conversation at each decision."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    async def ainvoke_tools(self, messages, tools):
        self.seen.append([str(getattr(m, "content", "")) for m in messages if m.__class__.__name__ == "HumanMessage"])
        return self.replies.pop(0)


async def _run(replies, tools, results, **kwargs):
    llm = ScriptedLLM(replies)

    async def act(decision):
        return results.get(decision["tool_action"], "text of the file")

    out = await native_loop.run(llm=llm, tools=[{"name": t} for t in tools], act=act, question="make the greeting friendlier", max_steps=12, **kwargs)
    return llm, out


def test_after_an_applied_edit_the_loop_tells_the_model_to_run_the_check_and_after_three_reads_to_act():
    tools = ["read_repo_file", "search_literal", "propose_local_edits", "run_local_check"]
    replies = [Reply([call("read_repo_file", 1)]), Reply([call("propose_local_edits", 2, summary="s", edits=[])]),
               Reply([call("read_repo_file", 3)]), Reply([call("read_repo_file", 4)]), Reply([call("read_repo_file", 5)]), Reply([], "done")]
    llm, out = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint="NEXT: run the check now."))
    assert out["final_answer"] == "done"
    before_apply = llm.seen[1]                                                                 # the decision right after the first read
    assert not any("NEXT:" in text or "READING STREAK" in text for text in before_apply)
    right_after_apply = llm.seen[2]
    assert any(text == "NEXT: run the check now." for text in right_after_apply) and not any("READING STREAK" in t for t in right_after_apply)
    after_three_reads = llm.seen[5]
    assert any("READING STREAK: you have read 3 times in a row" in text for text in after_three_reads)
    assert sum("READING STREAK" in t for t in llm.seen[4]) == 0                               # not before the third read


def test_a_turn_that_cannot_change_the_folder_is_never_nudged_this_way():
    tools = ["read_repo_file"]
    replies = [Reply([call("read_repo_file", n)]) for n in range(1, 6)] + [Reply([], "answer")]
    llm, out = asyncio.run(_run(replies, tools, {}, after_apply_hint="NEXT: run the check now."))
    assert out["final_answer"] == "answer"
    assert not any("READING STREAK" in t or "NEXT:" in t for seen in llm.seen for t in seen)


def test_no_hint_is_given_when_there_is_none_or_the_edit_was_declined():
    tools = ["read_repo_file", "propose_local_edits"]
    replies = [Reply([call("propose_local_edits", 1, summary="s", edits=[])]), Reply([], "ok")]
    llm, _ = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint=""))
    assert not any("NEXT:" in t for seen in llm.seen for t in seen)
    replies = [Reply([call("propose_local_edits", 1, summary="s", edits=[])]), Reply([], "ok")]
    llm, _ = asyncio.run(_run(replies, tools, {"propose_local_edits": "DECLINED: no"}, after_apply_hint="NEXT: run the check now."))
    assert not any("NEXT:" in t for seen in llm.seen for t in seen)


def test_the_hint_is_shortened_like_any_old_result_and_is_not_repeated_after_each_step():
    tools = ["read_repo_file", "propose_local_edits"]
    replies = [Reply([call("propose_local_edits", 1, summary="s", edits=[])]), Reply([call("read_repo_file", 2)]), Reply([call("read_repo_file", 3)]), Reply([], "ok")]
    llm, _ = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint="NEXT: run the check now."))
    assert sum("NEXT: run the check now." in t for t in llm.seen[-1]) == 1


# ---- the counts reach the counters -------------------------------------------------------------------------------------------------------------

def test_the_rhythm_is_kept_on_the_turn_and_becomes_counters_for_a_coding_turn():
    turn = llm_turns.begin("jack")
    llm_turns.note_tool_loop(6, False, tool_steps=12)
    for name in ("read_repo_file", "read_repo_file", "propose_local_edits"):
        llm_turns.note_tool_call(name, True)
    llm_turns.note_rhythm({"reads_before_first_act": 2, "applies": 1, "applies_checked": 1, "notices": 0, "text": "must not be kept"})
    assert turn.rhythm == {"reads_before_first_act": 2, "applies": 1, "applies_checked": 1, "notices": 0}
    summary = code_outcomes.summarize_turn(turn)
    assert summary["rhythm"] == turn.rhythm
    counts = {}
    code_outcomes.tally_turn(counts, summary)
    assert counts["code_rhythm_turns"] == 1 and counts["code_rhythm_reads_before_first_act"] == 2 and counts["code_rhythm_applies_checked"] == 1 and counts["code_rhythm_acted_turns"] == 1
    llm_turns.note_rhythm("nonsense")                                                          # never raises, never replaces it
    assert turn.rhythm["applies"] == 1


def test_the_tool_agent_hands_the_loop_the_hint_built_from_this_turns_tools_and_checks():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "after_apply_hint=loop_rhythm.check_hint([t.get(\"name\") for t in native_tools]" in source and "runner_jobs.runnable_checks(runner_for_checks)" in source


def test_a_checkpoint_after_an_unchecked_edit_does_not_just_say_prefer_answering(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "on")
    tools = ["read_repo_file", "propose_local_edits", "run_local_check"]
    replies = [Reply([call("propose_local_edits", 1, summary="s", edits=[])]), Reply([call("read_repo_file", 2)]), Reply([call("read_repo_file", 3)]), Reply([call("read_repo_file", 4)]),
               Reply([], "answer at the checkpoint"), Reply([], "done")]
    llm, out = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint="NEXT: run the check now."))
    checkpoint_prompts = [t for seen in llm.seen for t in seen if t.startswith("CHECKPOINT:")]
    assert checkpoint_prompts and "applied an edit that has not been checked" in checkpoint_prompts[0]
    # with no way to check, the checkpoint is the usual one
    replies = [Reply([call("propose_local_edits", 1, summary="s", edits=[])]), Reply([call("read_repo_file", 2)]), Reply([call("read_repo_file", 3)]), Reply([call("read_repo_file", 4)]),
               Reply([], "answer"), Reply([], "done")]
    llm, _ = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint=""))
    assert not any("has not been checked" in t for seen in llm.seen for t in seen)


def test_an_approved_plan_with_steps_to_go_gets_no_run_the_check_line_but_its_last_step_does():
    tools = ["read_repo_file", "propose_local_edits", "run_local_check"]
    steps = {"left": 1}

    async def plan_feed():
        if steps["left"] > 0:
            steps["left"] -= 1
            return "FILES FOR STEP s2: ..."
        return ""

    replies = [Reply([call("propose_local_edits", 1, summary="step one", edits=[])]), Reply([call("propose_local_edits", 2, summary="step two", edits=[])]), Reply([], "done")]
    llm, _ = asyncio.run(_run(replies, tools, {"propose_local_edits": APPLIED}, after_apply_hint="NEXT: run the check now.", plan_feed=plan_feed))
    after_first = llm.seen[1]                                   # the decision after step 1 applied, with step 2 still to come
    after_last = llm.seen[2]                                    # the decision after the last step applied
    assert any(text.startswith("FILES FOR STEP s2") for text in after_first) and not any("NEXT: run the check now." in text for text in after_first)
    assert any("NEXT: run the check now." in text for text in after_last)
