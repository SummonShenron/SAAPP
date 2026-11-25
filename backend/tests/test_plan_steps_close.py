"""Plan steps that used to stall (2026-11-24): a step whose files were applied across several proposals now ticks, a step that only runs something is refused when the plan is written, and
Sonic can close a step that needs no edit, after reading its files, with a reason the person sees (backend/utils/code_plan.py). No model, no database."""
import copy
import pathlib

import pytest

from backend.services import native_tools
from backend.tests.test_code_plan import NOW, _DB, exists, good_plan
from backend.utils import code_plan as cp, self_counters

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHAT, PREFS, BURST = "local/src/pages/Chat.tsx", "local/src/chatPreferences.ts", "local/src/components/NewBurst.tsx"


def _approved():
    plan, _ = cp.clean_plan(good_plan(), exists)
    return {**plan, "state": cp.APPROVED}


# ---- a step whose files arrive in more than one Apply ----

def test_a_step_ticks_once_all_its_files_have_been_applied_across_several_proposals():
    plan = _approved()
    after, finished = cp.mark_applied(plan, [CHAT])
    assert finished == 0 and after["steps"][1]["status"] == "todo" and after["steps"][1]["applied"] == [CHAT]
    after, finished = cp.mark_applied(after, [PREFS])                    # the other file, in a later proposal
    assert finished == 1 and after["steps"][1]["status"] == "done"


def test_a_file_outside_the_step_does_not_count_toward_it_and_the_plan_passed_in_is_unchanged():
    plan = _approved()
    before = copy.deepcopy(plan)
    after, _ = cp.mark_applied(plan, [BURST, "app.py"])
    assert "applied" not in after["steps"][1] and after["steps"][0]["status"] == "done"
    assert plan == before


def test_the_prompt_says_which_files_of_an_unfinished_step_are_already_in():
    after, _ = cp.mark_applied(_approved(), [CHAT])
    assert f"s2 (to do, applied so far: {CHAT})" in cp.build_context(after)
    assert "s1 (to do)" in cp.build_context(after)


# ---- a step that only runs something ----

@pytest.mark.parametrize("text, refused", [
    ("Run the tests in test_runner_look_jobs.py", True),
    ("Re-run the frontend suite", True),
    ("Execute the migration script", True),
    ("Run the migration and update the schema file", False),               # it changes something
    ("Rename runner_jobs to job_queue", False),
])
def test_a_step_that_only_runs_something_is_refused_when_the_plan_is_written(text, refused):
    raw = good_plan(steps=[{"text": text, "files": [CHAT, PREFS]}, {"text": "Add the burst component", "files": [BURST]}])
    plan, errors = cp.clean_plan(raw, exists)
    assert (plan is None) is refused
    if refused:
        assert any("may need no edit" in e for e in errors)


# ---- Sonic closes a step that needs no edit ----

def test_a_step_is_closed_with_the_reason_kept_once_its_files_were_read():
    plan, why = cp.close_step(_approved(), "s2", "Chat.tsx already plays it on a liked reply and the setting is unchanged", [CHAT, PREFS])
    assert why == "" and plan["steps"][1]["status"] == "done" and plan["steps"][1]["closed"].startswith("Chat.tsx already plays")
    assert plan["state"] == cp.APPROVED and plan["stalled"] == 0 and plan["done_seen"] == 1


def test_closing_the_last_step_finishes_the_plan():
    plan, _ = cp.mark_applied(_approved(), [BURST])
    plan, why = cp.close_step(plan, "s2", "both files already hold the change from earlier", [CHAT, PREFS])
    assert why == "" and plan["state"] == cp.DONE


def test_a_file_already_edited_for_the_step_counts_as_looked_at():
    plan, _ = cp.mark_applied(_approved(), [CHAT])
    closed, why = cp.close_step(plan, "s2", "the preference file needs nothing after the Chat change", [PREFS])
    assert why == "" and closed["steps"][1]["status"] == "done"


@pytest.mark.parametrize("plan_state, step, reason, read, fragment", [
    (cp.PROPOSED, "s2", "nothing to change here at all", [CHAT, PREFS], "no approved plan"),
    (cp.APPROVED, "s9", "nothing to change here at all", [CHAT, PREFS], "no step has that id"),
    (cp.APPROVED, "s2", "no change", [CHAT, PREFS], "say in one line why"),
    (cp.APPROVED, "s2", "nothing to change here at all", [CHAT], f"read {PREFS} first"),
    (cp.APPROVED, "s2", "nothing to change here at all", [], "read "),
])
def test_a_step_is_not_closed_without_an_approved_plan_a_real_step_a_reason_and_its_files_read(plan_state, step, reason, read, fragment):
    plan = {**_approved(), "state": plan_state}
    closed, why = cp.close_step(plan, step, reason, read)
    assert closed is None and fragment in why


def test_a_done_step_cannot_be_closed_again_and_the_plan_passed_in_is_unchanged():
    plan, _ = cp.mark_applied(_approved(), [BURST])
    before = copy.deepcopy(plan)
    closed, why = cp.close_step(plan, "s1", "it is already in place here", [BURST])
    assert closed is None and "already done" in why
    cp.close_step(plan, "s2", "both files already hold the change", [CHAT, PREFS])
    assert plan == before


def test_closing_is_stored_and_a_refusal_stores_nothing():
    db = _DB()
    cleaned, _ = cp.clean_plan(good_plan(), exists)
    cp.propose(db, "jack", "c1", cleaned, NOW)
    cp.set_state(db, "jack", "c1", cp.APPROVED, NOW)
    assert cp.record_close(db, "jack", "c1", "s2", "short", [CHAT, PREFS], NOW)[0] is None
    assert cp.load(db, "jack", "c1")["steps"][1]["status"] == "todo"
    updated, _ = cp.record_close(db, "jack", "c1", "s2", "both files already hold the change", [CHAT, PREFS], NOW)
    assert updated is not None and cp.load(db, "jack", "c1")["steps"][1]["closed"] == "both files already hold the change"


def test_the_card_gets_the_reason_and_the_execute_rules_point_at_the_tool():
    plan, _ = cp.close_step(_approved(), "s2", "both files already hold the change", [CHAT, PREFS])
    assert cp.public_view(plan)["plan"]["steps"][1]["closed"] == "both files already hold the change"
    assert "call close_plan_step" in cp.EXECUTE_RULES and "the person can mark the step done" not in cp.EXECUTE_RULES


# ---- the wiring ----

def test_the_tool_is_declared_offered_only_while_a_plan_is_carried_out_and_counted():
    assert set(native_tools.REGISTRY["close_plan_step"]["required"]) == {"step", "reason"}
    assert "close_plan_step" in native_tools.NATIVE_DESCRIPTIONS
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    offered = source.index('"- close_plan_step — args:')
    assert source.rfind("if plan_phase == code_plan.EXECUTE_PHASE:", 0, offered) > source.rfind("menu_lines", 0, offered) - 400
    assert 'if tool_action == "close_plan_step":\n                return await _close_plan_step(args)' in source
    assert "if plan_phase != code_plan.EXECUTE_PHASE:\n                return \"ERROR: there is no approved plan being carried out in this turn.\"" in source
    assert "args.get(\"step\"), args.get(\"reason\"), list(_turn_file_cache)," in source
    assert 'await safe_emit_event("code_plan", code_plan.public_view(updated))' in source
    assert "code_plan_step_closed" in self_counters.KEYS and '{"code_plan_step_closed": 1}' in source
    assert "close_plan_step" in cp._ON_PLAN_TOOLS                    # on the plan: it does not use up a step


def test_the_page_shows_the_reason_on_the_step():
    card = (ROOT / "local" / "src" / "components" / "PlanCard.tsx").read_text(encoding="utf-8")
    assert "{step.closed && <span className=\"plan-step-closed\">No edit needed: {step.closed}</span>}" in card
    api = (ROOT / "local" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "closed?: string;" in api
