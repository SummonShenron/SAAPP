"""Plan mode: a plan the person approves before edits, kept across turns (backend/utils/code_plan.py)."""
import copy
import pathlib
from datetime import datetime, timezone

import pytest

from backend.utils import code_outcomes
from backend.utils import code_plan as cp

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 25, 15, 0, tzinfo=timezone.utc)
FILES = {"local/src/pages/Chat.tsx", "local/src/chatPreferences.ts", "local/src/components/ChatSettingsCard.tsx"}


def exists(path):
    return path in FILES


def good_plan(**overrides):
    plan = {
        "goal": "Show the mascot's new fist-pump animation on praise",
        "files": [{"path": "local/src/pages/Chat.tsx", "why": "play it on a liked reply"}, {"path": "local/src/chatPreferences.ts", "why": "no change to the setting"},
                  {"path": "local/src/components/NewBurst.tsx", "why": "the animation", "new": True}],
        "steps": [{"text": "Add the burst component", "files": ["local/src/components/NewBurst.tsx"]},
                  {"text": "Play it from Chat", "files": ["local/src/pages/Chat.tsx", "local/src/chatPreferences.ts"]}],
        "tests": ["npx tsc -b"], "unknowns": ["whether the sad one needs the same hook"],
    }
    plan.update(overrides)
    return plan


# ---- an in-memory collection ------------------------------------------------------------------------------------------------------------------

class _Result:
    def __init__(self, deleted=0):
        self.deleted_count = deleted


class _Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        pass

    def _match(self, flt):
        return [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def find_one(self, flt, projection=None, sort=None):
        hits = self._match(flt)
        if sort:
            hits = sorted(hits, key=lambda d: d.get(sort[0][0]), reverse=sort[0][1] < 0)
        return {k: v for k, v in copy.deepcopy(hits[0]).items() if k != "_id"} if hits else None

    def update_one(self, flt, update, upsert=False):
        hits = self._match(flt)
        if hits:
            hits[0].update(copy.deepcopy(update["$set"]))
        elif upsert:
            self.docs.append({**flt, **copy.deepcopy(update["$set"])})

    def delete_one(self, flt):
        hits = self._match(flt)[:1]
        for d in hits:
            self.docs.remove(d)
        return _Result(len(hits))

    def delete_many(self, flt):
        hits = self._match(flt)
        for d in hits:
            self.docs.remove(d)
        return _Result(len(hits))


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == cp.COLLECTION
        return self.col


# ---- validation -------------------------------------------------------------------------------------------------------------------------------

def test_a_good_plan_is_accepted_and_cleaned():
    plan, errors = cp.clean_plan(good_plan(), exists)
    assert errors == [] and plan["goal"].startswith("Show the mascot") and [s["id"] for s in plan["steps"]] == ["s1", "s2"]
    assert plan["files"][2]["new"] is True and all(s["status"] == "todo" for s in plan["steps"])


def test_a_file_that_is_not_in_the_folder_is_refused_with_a_way_out():
    plan, errors = cp.clean_plan(good_plan(files=[{"path": "local/src/pages/Chatt.tsx", "why": "x"}], steps=[{"text": "Edit it", "files": ["local/src/pages/Chatt.tsx"]}]), exists)
    assert plan is None and any("not in the folder" in e and "new" in e for e in errors)


def test_a_new_file_that_already_exists_is_refused():
    plan, errors = cp.clean_plan(good_plan(files=[{"path": "local/src/pages/Chat.tsx", "why": "x", "new": True}], steps=[{"text": "Edit it", "files": ["local/src/pages/Chat.tsx"]}]), exists)
    assert plan is None and any("already exists" in e for e in errors)


@pytest.mark.parametrize("path", ["../secrets.txt", "/etc/passwd", "C:/Windows/x.txt", "a/../../b"])
def test_paths_outside_the_folder_are_refused(path):
    plan, errors = cp.clean_plan(good_plan(files=[{"path": path, "why": "x", "new": True}], steps=[{"text": "Do it", "files": [path]}]), exists)
    assert plan is None and errors


def test_every_file_needs_a_step_and_every_step_file_must_be_in_the_plan():
    plan, errors = cp.clean_plan(good_plan(steps=[{"text": "Play it from Chat", "files": ["local/src/pages/Chat.tsx"]}]), exists)
    assert plan is None and any("no step changes" in e for e in errors)
    plan, errors = cp.clean_plan(good_plan(steps=[{"text": "Do it", "files": ["other.ts"]}]), exists)
    assert plan is None and any("not in the plan's files" in e for e in errors)


def test_empty_or_oversized_plans_are_refused():
    assert cp.clean_plan({}, exists)[0] is None and cp.clean_plan("nope", exists)[0] is None
    many = [{"path": f"f{i}.ts", "why": "x", "new": True} for i in range(cp.MAX_FILES + 1)]
    plan, errors = cp.clean_plan(good_plan(files=many, steps=[{"text": "Do it all", "files": [m["path"] for m in many]}]), exists)
    assert plan is None and any("at most" in e for e in errors)


def test_text_is_flattened_and_clipped():
    plan, _ = cp.clean_plan(good_plan(goal="Show\n\nthe   mascot's new animation " + "x" * 400), exists)
    assert "\n" not in plan["goal"] and len(plan["goal"]) <= cp.GOAL_MAX


# ---- progress ---------------------------------------------------------------------------------------------------------------------------------

def test_a_step_is_done_only_when_all_its_files_were_applied():
    plan, _ = cp.clean_plan(good_plan(), exists)
    plan["state"] = cp.APPROVED
    after, finished = cp.mark_applied(plan, ["local/src/pages/Chat.tsx"])
    assert finished == 0 and after["steps"][1]["status"] == "todo"
    after, finished = cp.mark_applied(after, ["local/src/chatPreferences.ts", "local/src/pages/Chat.tsx"])
    assert finished == 1 and after["steps"][1]["status"] == "done" and after["state"] == cp.APPROVED and cp.next_step(after)["id"] == "s1"
    after, finished = cp.mark_applied(after, ["local\\src\\components\\NewBurst.tsx".replace("\\", "/")])
    assert finished == 1 and after["state"] == cp.DONE and cp.next_step(after) is None


def test_the_plan_passed_in_is_not_changed_by_marking():
    plan, _ = cp.clean_plan(good_plan(), exists)
    before = copy.deepcopy(plan)
    cp.mark_applied(plan, ["local/src/components/NewBurst.tsx"])
    assert plan == before


# ---- the prompt blocks -----------------------------------------------------------------------------------------------------------------------

def test_only_an_approved_plan_becomes_a_context_block_with_progress_in_it():
    plan, _ = cp.clean_plan(good_plan(), exists)
    assert cp.build_context({**plan, "state": cp.PROPOSED}) == "" and cp.build_context(None) == ""
    approved, _ = cp.mark_applied({**plan, "state": cp.APPROVED}, ["local/src/components/NewBurst.tsx"])
    block = cp.build_context(approved)
    assert "APPROVED PLAN" in block and "s1 (done)" in block and "s2 (to do)" in block and "<<<" in block and "do not plan again" in block
    assert "NewBurst.tsx (new file)" in block


def test_an_earlier_plan_is_shown_back_only_while_it_is_waiting_for_feedback():
    plan, _ = cp.clean_plan(good_plan(), exists)
    assert "revised plan" in cp.revision_context({**plan, "state": cp.PROPOSED}) and cp.revision_context({**plan, "state": cp.APPROVED}) == ""


def test_the_progress_note_names_the_next_step_or_says_it_is_done():
    plan, _ = cp.clean_plan(good_plan(), exists)
    assert "s1" in cp.progress_note({**plan, "state": cp.APPROVED}) and cp.progress_note(None) == ""
    done, _ = cp.mark_applied({**plan, "state": cp.APPROVED}, [f["path"] for f in plan["files"]])
    assert "every step is done" in cp.progress_note(done)


# ---- what the person's words do --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,approves,cancels", [
    (cp.APPROVE_MESSAGE, True, False), ("yes", True, False), ("looks good", True, False), ("lgtm", True, False),
    ("cancel", False, True), ("never mind", False, True), ("yes but also touch the tests and the docs for it please", False, False),
    ("also include the sad animation", False, False),
])
def test_short_replies_approve_or_cancel_and_long_ones_are_feedback(text, approves, cancels):
    assert cp.reads_as_approval(text) is approves and cp.reads_as_cancel(text) is cancels


def test_continue_is_read_in_short_messages_only():
    assert cp.reads_as_continue("continue") and cp.reads_as_continue("next step please") and not cp.reads_as_continue("what is the weather like today in the city")
    assert not cp.reads_as_continue("continue " + "word " * 30)


# ---- storage and the turn's phase ------------------------------------------------------------------------------------------------------------

def stored(db, state=cp.PROPOSED):
    cleaned, _ = cp.clean_plan(good_plan(), exists)
    plan = cp.propose(db, "jack", "s1", cleaned, NOW)
    if state != cp.PROPOSED:
        cp.set_state(db, "jack", "s1", state, NOW)
    return plan


def test_a_proposed_plan_is_stored_and_a_revision_counts_up():
    db = _DB()
    assert stored(db)["revision"] == 1 and cp.load(db, "jack", "s1")["state"] == cp.PROPOSED
    assert stored(db)["revision"] == 2
    assert cp.public_view(cp.load(db, "jack", "s1"))["plan"]["revision"] == 2 and cp.public_view(None) == {"plan": None}


def test_only_the_owner_and_the_conversation_see_a_plan():
    db = _DB()
    stored(db)
    assert cp.load(db, "someone_else", "s1") is None and cp.load(db, "jack", "other") is None


def test_no_plan_and_no_setting_means_a_normal_turn():
    assert cp.prepare(_DB(), "jack", "s1", "change the header", plan_mode=False) == {"phase": None, "context": ""}


def test_the_setting_starts_a_plan_turn_when_there_is_no_live_plan():
    assert cp.prepare(_DB(), "jack", "s1", "change the header", plan_mode=True)["phase"] == cp.PLAN_PHASE


def test_a_short_yes_approves_a_proposed_plan_and_carries_it_out():
    db = _DB()
    stored(db)
    turn = cp.prepare(db, "jack", "s1", "yes", plan_mode=True)
    assert turn["phase"] == cp.EXECUTE_PHASE and "APPROVED PLAN" in turn["context"] and cp.load(db, "jack", "s1")["state"] == cp.APPROVED


def test_feedback_on_a_proposed_plan_plans_again_with_the_old_one_in_view():
    db = _DB()
    stored(db)
    turn = cp.prepare(db, "jack", "s1", "also include the sad animation in the same plan", plan_mode=True)
    assert turn["phase"] == cp.PLAN_PHASE and "revised plan" in turn["context"] and cp.load(db, "jack", "s1")["state"] == cp.PROPOSED


def test_cancelling_ends_the_plan():
    db = _DB()
    stored(db)
    assert cp.prepare(db, "jack", "s1", "cancel", plan_mode=True)["phase"] is None and cp.load(db, "jack", "s1")["state"] == cp.CANCELLED
    assert cp.prepare(db, "jack", "s1", "change the header", plan_mode=True)["phase"] == cp.PLAN_PHASE      # a fresh request after a cancel plans again


def test_an_approved_plan_is_carried_out_even_with_the_setting_off():
    db = _DB()
    stored(db, cp.APPROVED)
    turn = cp.prepare(db, "jack", "s1", "continue", plan_mode=False)
    assert turn["phase"] == cp.EXECUTE_PHASE and "APPROVED PLAN" in turn["context"]


def test_applied_edits_finish_steps_and_the_plan():
    db = _DB()
    stored(db, cp.APPROVED)
    assert cp.record_applied(db, "jack", "s1", ["local/src/components/NewBurst.tsx"]) == 1
    assert cp.record_applied(db, "jack", "s1", ["local/src/components/NewBurst.tsx"]) == 0           # reported twice: counted once
    assert cp.record_applied_for_user(db, "jack", ["local/src/pages/Chat.tsx", "local/src/chatPreferences.ts"]) == 1
    assert cp.load(db, "jack", "s1")["state"] == cp.DONE
    assert cp.prepare(db, "jack", "s1", "next", plan_mode=False)["phase"] is None                    # nothing left to carry out


def test_a_proposed_plan_is_not_touched_by_an_applied_edit():
    db = _DB()
    stored(db)
    assert cp.record_applied(db, "jack", "s1", ["local/src/components/NewBurst.tsx"]) == 0


def test_the_person_can_reword_or_remove_a_step_but_not_a_finished_one():
    db = _DB()
    stored(db, cp.APPROVED)
    assert cp.edit_step(db, "jack", "s1", "s2", "Play it only on praise", NOW)["steps"][1]["text"] == "Play it only on praise"
    assert cp.edit_step(db, "jack", "s1", "s2", "ab", NOW) is None and cp.edit_step(db, "jack", "s1", "nope", "A fine step text", NOW) is None
    cp.record_applied(db, "jack", "s1", ["local/src/components/NewBurst.tsx"])
    assert cp.edit_step(db, "jack", "s1", "s1", None, NOW) is None
    assert [s["id"] for s in cp.edit_step(db, "jack", "s1", "s2", None, NOW)["steps"]] == ["s1"]
    assert cp.edit_step(db, "jack", "s1", "s1", None, NOW) is None                                   # the last step cannot be removed


# --- the stall guard: turns that finish nothing are counted, and the person can close a step that needs no edit (2026-10-30: ~10 Continues, ~1.8 cents each, after the work was done) ---

def test_turns_that_finish_no_step_are_counted_and_progress_resets_the_count():
    db = _DB()
    stored(db, cp.APPROVED)
    assert cp.load(db, "jack", "s1")["stalled"] == 0                                                  # counted from the moment of approval
    assert cp.record_turn_end(db, "jack", "s1", "step_limit", NOW) == 1
    assert cp.record_turn_end(db, "jack", "s1", "model_answered", NOW) == 2
    assert cp.public_view(cp.load(db, "jack", "s1"))["plan"]["stalled"] == 2
    cp.record_applied(db, "jack", "s1", ["local/src/components/NewBurst.tsx"])                        # an applied edit finished a step
    assert cp.load(db, "jack", "s1")["stalled"] == 0
    assert cp.record_turn_end(db, "jack", "s1", "step_limit", NOW) == 1                               # and counting starts again


def test_a_turn_that_finished_a_step_is_not_a_stall_even_when_it_stopped_early():
    plan = {"state": cp.APPROVED, "stalled": 3, "done_seen": 0, "steps": [{"id": "s1", "status": "done"}, {"id": "s2", "status": "todo"}]}
    after, stalled = cp.after_turn(plan, "step_limit")
    assert stalled is False and after["stalled"] == 0 and after["done_seen"] == 1


def test_the_persons_own_decision_or_a_finished_plan_is_not_a_stall():
    plan = {"state": cp.APPROVED, "stalled": 1, "done_seen": 0, "steps": [{"id": "s1", "status": "todo"}]}
    for reason in ("declined", "no_decision", "plan_done"):
        after, stalled = cp.after_turn(plan, reason)
        assert stalled is False and after["stalled"] == 1


def test_a_plan_from_before_stalls_were_counted_starts_counting_from_its_next_turn():
    plan = {"state": cp.APPROVED, "steps": [{"id": "s1", "status": "todo"}]}                          # no done_seen stored
    after, stalled = cp.after_turn(plan, "step_limit")
    assert stalled is False and after["stalled"] == 0 and after["done_seen"] == 0
    assert cp.after_turn(after, "step_limit")[1] is True


def test_the_stall_count_only_moves_for_an_approved_plan():
    db = _DB()
    stored(db)                                                                                         # proposed
    assert cp.record_turn_end(db, "jack", "s1", "step_limit", NOW) == 0
    assert not cp.load(db, "jack", "s1").get("stalled")


def test_the_person_can_mark_a_step_done_without_an_edit_and_the_last_one_finishes_the_plan():
    db = _DB()
    stored(db, cp.APPROVED)
    cp.record_turn_end(db, "jack", "s1", "step_limit", NOW)
    cp.record_turn_end(db, "jack", "s1", "step_limit", NOW)
    plan = cp.mark_done(db, "jack", "s1", "s1", NOW)
    assert plan["steps"][0]["status"] == "done" and plan["state"] == cp.APPROVED and plan["stalled"] == 0
    assert cp.mark_done(db, "jack", "s1", "s1", NOW) is None                                          # already done
    assert cp.mark_done(db, "jack", "s1", "nope", NOW) is None
    assert cp.mark_done(db, "jack", "s1", "s2", NOW)["state"] == cp.DONE


def test_a_step_can_only_be_marked_done_on_an_approved_plan():
    db = _DB()
    stored(db)                                                                                         # proposed
    assert cp.mark_done(db, "jack", "s1", "s1", NOW) is None
    assert cp.mark_step_done({"state": cp.DONE, "steps": [{"id": "s1", "status": "todo"}]}, "s1") is None


def test_the_execute_rules_say_a_check_passed_only_with_a_result_from_this_turn():
    text = cp.EXECUTE_RULES
    assert "IN THIS TURN" in text and "otherwise say it was not run" in text
    assert "not in the plan's files" in text and "leave it alone" in text
    assert "mark the step done" in text


def test_the_stall_wiring_is_in_the_route_the_turn_end_and_the_counters():
    import pathlib as _p
    from backend.utils.self_counters import KEYS

    root = _p.Path(__file__).resolve().parents[2]
    app_source = (root / "app.py").read_text(encoding="utf-8")
    workflow = (root / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert 'action.action == "mark_done"' in app_source and "code_plan_utils.mark_done" in app_source
    assert "code_plan.record_turn_end," in workflow and '"code_plan_stalled"' in workflow
    assert {"code_plan_stalled", "code_plan_marked_done"} <= set(KEYS)


def test_forgetting_everything_deletes_plans():
    db = _DB()
    stored(db)
    assert cp.clear_all(db, "jack") == 1 and cp.load(db, "jack", "s1") is None


def test_a_database_that_fails_leaves_the_turn_unplanned():
    class _Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert cp.prepare(_Broken(), "jack", "s1", "yes", plan_mode=True) == {"phase": None, "context": ""}


def test_the_switch_turns_the_feature_off(monkeypatch):
    monkeypatch.setenv("CODE_PLAN_ENABLED", "false")
    assert cp.prepare(_DB(), "jack", "s1", "change the header", plan_mode=True) == {"phase": None, "context": ""}


# ---- the wiring -------------------------------------------------------------------------------------------------------------------------------

def test_a_plan_turn_offers_only_read_tools_and_the_plan_tool():
    from backend.services import agent_workflow as aw

    assert aw._PLAN_TURN_TOOLS == {"read_repo_file", "find_file", "search_literal", "list_repo_tree", "submit_plan"}
    assert not {"propose_local_edits", "run_sandbox_tests", "propose_commit", "propose_merge", "run_python", "web_search"} & aw._PLAN_TURN_TOOLS
    assert aw._PLAN_REQUIRED_ACTION["tool_action"] == "submit_plan"


def test_submit_plan_has_a_native_declaration_and_is_offered_only_from_the_menu_line():
    from backend.services import native_tools as nt

    assert "submit_plan" in nt.REGISTRY and "submit_plan" in nt.NATIVE_DESCRIPTIONS
    required = nt.REGISTRY["submit_plan"]["required"]
    assert {"goal", "files", "steps"} <= set(required)
    declared = nt.declarations_from_menu(["- submit_plan — args: goal, files, steps"])
    assert [d["name"] for d in declared] == ["submit_plan"] and nt.declarations_from_menu(["- read_repo_file — args: path"])[0]["name"] == "read_repo_file"
    assert nt.declarations_from_menu([]) == []


def test_the_turn_is_wired_to_the_plan_in_the_agent_and_the_app():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "tool_action == \"submit_plan\"" in workflow and "code_plan.progress_note(" in workflow and "code_plan.record_applied," in workflow
    assert "plan_phase != code_plan.PLAN_PHASE and not any(tool.get(\"name\") == \"propose_local_edits\"" in workflow       # a plan turn is native whatever the edit tool says
    assert "code_plan_utils.prepare," in app_source and "code_plan_utils.record_applied_for_user" in app_source and "code_plan_utils.clear_all" in app_source
    assert "plan_mode=bool(_settings.get(\"plan_mode\")) and budget_guard.allows(\"plan_mode\", budget_state)" in app_source


def test_the_counters_exist_and_a_plan_turn_is_tallied_apart():
    from backend.utils import code_outcomes
    from backend.utils.self_counters import KEYS

    assert {"code_plan_turns", "code_plan_submitted", "code_plan_approved", "code_plan_exec_turns", "code_plan_exec_cap_hit", "code_plan_steps_done"} <= set(KEYS)
    counts = {}
    code_outcomes.tally_turn(counts, {"cap_hit": True, "edited": True}, "execute")
    assert counts["code_plan_exec_turns"] == 1 and counts["code_plan_exec_cap_hit"] == 1 and counts["code_plan_exec_edited"] == 1 and counts["code_turns"] == 1
    counts = {}
    code_outcomes.tally_turn(counts, {"cap_hit": False}, "plan")
    assert counts["code_plan_turns"] == 1 and "code_plan_turn_cap_hit" not in counts


def test_the_deviation_cap_counts_files_outside_the_approved_plan():
    plan, _ = cp.clean_plan(good_plan(), exists)
    approved = {**plan, "state": cp.APPROVED}
    inside = [{"path": "local/src/pages/Chat.tsx"}, {"path": "local/src/components/NewBurst.tsx", "content": "x"}]
    assert cp.off_plan_paths(approved, inside) == []
    assert cp.off_plan_paths(approved, inside + [{"path": "app.py"}, {"path": "app.py"}, {"path": "backend/x.py"}]) == ["app.py", "backend/x.py"]
    assert cp.off_plan_paths({**plan, "state": cp.PROPOSED}, [{"path": "app.py"}]) == [] and cp.off_plan_paths(None, [{"path": "app.py"}]) == []
    assert cp.off_plan_paths(approved, "not a list") == [] and cp.MAX_OFF_PLAN_FILES == 1


def test_a_plan_turn_has_a_large_read_allowance_and_an_execution_turn_a_deviation_guard():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert 'TOOL_AGENT_PLAN_STEPS = int(os.getenv("TOOL_AGENT_PLAN_STEPS", "12"))' in workflow
    assert "TOOL_AGENT_PLAN_STEPS if plan_phase == code_plan.PLAN_PHASE else 0" in workflow
    assert "len(off_plan) > code_plan.MAX_OFF_PLAN_FILES" in workflow


# ---- the execution budget ---------------------------------------------------------------------------------------------------------------------

def _approved():
    plan, _ = cp.clean_plan(good_plan(), exists)
    return {**plan, "state": cp.APPROVED}


def _call(name, **args):
    return {"name": name, "args": args, "id": f"id_{name}"}


def test_steps_on_the_plan_are_free_and_steps_off_it_are_counted():
    budget = cp.PlanBudget(_approved(), deviation_steps=2, hard_cap=30)
    assert budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx")], 1) == {"refund": 1, "stop": False, "note": None}
    assert budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx".replace("/", chr(92))), _call("propose_local_edits", edits=[])], 2)["refund"] == 1
    first = budget.after_step([_call("search_literal", query="x")], 3)
    assert first["refund"] == 0 and not first["stop"] and "1 of 2" in first["note"]
    assert budget.after_step([_call("read_repo_file", path="app.py")], 4)["note"].startswith("DEVIATION") and budget.deviations == 2
    last = budget.after_step([_call("web_search", query="x")], 5)
    assert last["stop"] and "stop" in last["note"].lower()


def test_only_the_deviation_allowance_says_it_stopped_for_a_deviation_not_the_ceiling_or_an_ordinary_step():
    budget = cp.PlanBudget(_approved(), deviation_steps=2, hard_cap=5)
    assert "deviation_stop" not in budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx")], 1)            # an on-plan step
    assert "deviation_stop" not in budget.after_step([_call("search_literal", query="a")], 2)                                  # the 1st off-plan step: counted, not stopped
    assert "deviation_stop" not in budget.after_step([_call("search_literal", query="b")], 3)                                  # the 2nd: still within the allowance of 2
    over = budget.after_step([_call("search_literal", query="c")], 4)                                                          # the 3rd passes it
    assert over["stop"] is True and over["deviation_stop"] is True
    ceiling = cp.PlanBudget(_approved(), hard_cap=5).after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx")], 5)  # the ceiling stops an ON-plan turn
    assert ceiling["stop"] is True and "deviation_stop" not in ceiling


def _run_loop_in_a_turn(script, plan_budget, max_steps):
    """Runs the native loop with a scripted model inside a real llm_turns Turn and returns that Turn, so a test can see what the loop recorded on it."""
    import asyncio

    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    class LLM:
        async def ainvoke_tools(self, messages, tools):
            if not tools:
                return AIMessage(content="forced final")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return f"ok {decision['tool_action']}"

    tools = [{"name": "read_repo_file", "description": "r"}, {"name": "search_literal", "description": "s"}, {"name": "propose_local_edits", "description": "p"}]
    turn = llm_turns.begin("someone")
    try:
        asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="go", max_steps=max_steps, plan_budget=plan_budget))
    finally:
        llm_turns._current.set(None)               # do not leave this turn current for the next test
    return turn


def _tool(name, **args):
    return {"name": name, "args": args, "id": f"i{name}{len(args)}{id(args)}", "type": "tool_call"}


def test_a_turn_stopped_for_wandering_off_the_plan_is_marked_so_the_app_can_capture_it():
    wandering = [_tool("search_literal", query=f"q{i}") for i in range(5)]
    turn = _run_loop_in_a_turn(wandering, cp.PlanBudget(_approved(), deviation_steps=2), max_steps=6)
    assert turn.plan_deviated is True


def test_a_turn_that_stays_on_the_plan_or_ends_at_the_ceiling_is_not_marked():
    on_plan = [_tool("read_repo_file", path="local/src/pages/Chat.tsx"), _tool("propose_local_edits", edits=[{"path": "local/src/pages/Chat.tsx"}]), "all done"]
    assert _run_loop_in_a_turn(on_plan, cp.PlanBudget(_approved()), max_steps=3).plan_deviated is False
    long_on_plan = [_tool("read_repo_file", path="local/src/pages/Chat.tsx", start_line=i) for i in range(1, 9)]
    assert _run_loop_in_a_turn(long_on_plan, cp.PlanBudget(_approved(), hard_cap=3), max_steps=4).plan_deviated is False       # stopped by the ceiling, not by wandering


def test_the_turn_record_starts_unmarked_and_a_finished_turn_cannot_be_marked():
    from backend.utils import llm_turns

    turn = llm_turns.begin("u")
    try:
        assert turn.plan_deviated is False
        turn.done = True
        llm_turns.note_plan_deviation()
        assert turn.plan_deviated is False
    finally:
        llm_turns._current.set(None)
    llm_turns.note_plan_deviation()                 # no turn running: nothing to mark, and it must not raise


def test_a_step_that_mixes_a_plan_read_with_a_search_counts_as_a_deviation():
    budget = cp.PlanBudget(_approved())
    assert budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx"), _call("search_literal", query="x")], 1)["refund"] == 0 and budget.deviations == 1


def test_the_hard_cap_ends_even_an_on_plan_turn():
    budget = cp.PlanBudget(_approved(), hard_cap=5)
    assert not budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx")], 4)["stop"]
    assert budget.after_step([_call("read_repo_file", path="local/src/pages/Chat.tsx")], 5)["stop"]


def test_the_native_loop_runs_a_long_plan_on_a_small_base_budget_and_stops_when_it_wanders():
    import asyncio

    from backend.services import native_loop as nl

    class LLM:
        def __init__(self, script):
            self.script, self.n = script, 0

        async def ainvoke_tools(self, messages, tools):
            self.n += 1
            from langchain_core.messages import AIMessage

            if not tools:
                return AIMessage(content="forced final")
            item = self.script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return f"ok {decision['tool_action']}"

    def tool_call(name, **args):
        return {"name": name, "args": args, "id": f"i{name}{len(args)}{id(args)}", "type": "tool_call"}

    tools = [{"name": "read_repo_file", "description": "r"}, {"name": "search_literal", "description": "s"}, {"name": "propose_local_edits", "description": "p"}]
    on_plan = [tool_call("read_repo_file", path=p) for p in ("local/src/pages/Chat.tsx", "local/src/chatPreferences.ts", "local/src/components/ChatSettingsCard.tsx")]
    on_plan[2]["args"] = {"path": "local/src/pages/Chat.tsx", "start_line": 400}
    script = on_plan + [tool_call("propose_local_edits", edits=[{"path": "local/src/pages/Chat.tsx"}]), "all done"]
    llm = LLM(script)
    plan = _approved()
    result = asyncio.run(nl.run(llm=llm, tools=tools, act=act, question="go", max_steps=3, plan_budget=cp.PlanBudget(plan)))
    assert result["final_answer"] == "all done" and len(result["attempts"]) == 4 and not result["show_work"]      # four tool steps on a base budget of three: none ran out

    wandering = [tool_call("search_literal", query=f"q{i}") for i in range(5)]
    llm = LLM(wandering)
    result = asyncio.run(nl.run(llm=llm, tools=tools, act=act, question="go", max_steps=6, plan_budget=cp.PlanBudget(plan, deviation_steps=2)))
    assert len(result["attempts"]) == 3 and result["final_answer"] == "forced final"                                  # the third step off the plan ended the loop


# ---- why a plan-carrying turn ended (diagnostics) and the checkpoint that used to end it early --------------------------------------------------

def test_the_stop_reason_is_one_of_the_known_reasons_and_a_finished_plan_always_wins():
    assert cp.stop_reason(True, "model_answered", "declined") == "plan_done" and cp.stop_reason(True, "hard_cap") == "plan_done"
    assert cp.stop_reason(False, "checkpoint_answered") == "checkpoint_answered" and cp.stop_reason(False, "hard_cap") == "hard_cap"
    assert cp.stop_reason(False, "deviation_allowance") == "deviation_allowance" and cp.stop_reason(False, "step_limit") == "step_limit" and cp.stop_reason(False, "repeats") == "repeats"
    assert cp.stop_reason(False, "model_answered", "declined") == "declined" and cp.stop_reason(False, "", "no_decision") == "no_decision"       # the person's decision explains a plain stop
    assert cp.stop_reason(False, "hard_cap", "declined") == "hard_cap"                                                                         # but never hides a real limit
    assert cp.stop_reason(False, "model_answered") == "model_answered" and cp.stop_reason(False) == "model_answered"
    assert cp.stop_reason(False, "something new", "also new") == "model_answered" and cp.stop_reason(False, "plan_done") == "model_answered"      # unknown values still count somewhere
    assert all(cp.stop_reason(False, reason) in cp.PLAN_STOP_REASONS for reason in cp.PLAN_STOP_REASONS) and len(set(cp.PLAN_STOP_REASONS)) == len(cp.PLAN_STOP_REASONS)


def test_every_stop_reason_has_a_counter_and_the_counters_name_no_content():
    from backend.utils import self_counters

    assert {f"code_plan_stop_{reason}" for reason in cp.PLAN_STOP_REASONS} <= set(self_counters.KEYS) and {"code_plan_exec_early", "code_plan_exec_tool_steps"} <= set(self_counters.KEYS)


def _calls_offered(script, plan_budget, max_steps):
    """Like _run_loop_in_a_turn, but also returns the tool names the model was offered on each call, so a test can see whether a checkpoint question was asked."""
    import asyncio

    from langchain_core.messages import AIMessage

    from backend.services import native_loop as nl
    from backend.utils import llm_turns

    offered = []

    class LLM:
        async def ainvoke_tools(self, messages, tools):
            offered.append([t["name"] for t in tools])
            if not tools:
                return AIMessage(content="forced final")
            item = script.pop(0)
            return AIMessage(content=item if isinstance(item, str) else "", tool_calls=[] if isinstance(item, str) else [item])

    async def act(decision):
        return f"ok {decision['tool_action']}"

    tools = [{"name": "read_repo_file", "description": "r"}, {"name": "search_literal", "description": "s"}, {"name": "propose_local_edits", "description": "p"}]
    turn = llm_turns.begin("someone")
    try:
        result = asyncio.run(nl.run(llm=LLM(), tools=tools, act=act, question="go", max_steps=max_steps, plan_budget=plan_budget))
    finally:
        llm_turns._current.set(None)
    return turn, result, offered


def test_a_checkpoint_does_not_end_a_turn_that_is_carrying_out_a_plan():
    # Seven on-plan steps (a read and an edit per plan step) on a budget of 12, which used to meet a checkpoint after four raw steps: "decide now, prefer answering".
    steps = [_tool("read_repo_file", path="local/src/pages/Chat.tsx", start_line=i) if i % 2 else _tool("propose_local_edits", edits=[{"path": "local/src/pages/Chat.tsx"}]) for i in range(1, 8)]
    turn, result, offered = _calls_offered(steps + ["every step is done"], cp.PlanBudget(_approved()), max_steps=12)
    assert result["final_answer"] == "every step is done" and len(result["attempts"]) == 7
    assert not any(names == ["continue_working"] or "continue_working" in names for names in offered)               # no checkpoint question was ever asked
    assert turn.checkpoints_answered == 0 and turn.plan_exit == "model_answered"


def test_the_same_turn_without_a_plan_still_meets_its_checkpoints():
    steps = [_tool("read_repo_file", path=f"f{i}.py") for i in range(1, 6)]
    turn, result, offered = _calls_offered(steps + ["answer"], None, max_steps=12)
    assert any("continue_working" in names for names in offered)                                                      # the checkpoint is only skipped for a plan
    assert turn.plan_exit == ""                                                                                        # and a turn that is not carrying out a plan records no plan exit


def test_the_loop_says_how_a_plan_turn_ended():
    stays = [_tool("read_repo_file", path="local/src/pages/Chat.tsx", start_line=i) for i in range(1, 9)]
    assert _calls_offered(["done"], cp.PlanBudget(_approved()), 12)[0].plan_exit == "model_answered"
    assert _calls_offered([_tool("search_literal", query=f"q{i}") for i in range(5)], cp.PlanBudget(_approved(), deviation_steps=2), 6)[0].plan_exit == "deviation_allowance"
    assert _calls_offered(list(stays), cp.PlanBudget(_approved(), hard_cap=3), 4)[0].plan_exit == "hard_cap"
    assert _calls_offered([_tool("search_literal", query=f"q{i}") for i in range(5)], cp.PlanBudget(_approved(), deviation_steps=10), 3)[0].plan_exit == "step_limit"


def test_the_turn_record_keeps_the_plan_exit_the_decision_and_the_final_reason_and_ignores_a_finished_turn():
    from backend.utils import llm_turns

    turn = llm_turns.begin("u")
    try:
        assert (turn.plan_exit, turn.plan_blocked, turn.plan_stop) == ("", "", "")
        llm_turns.note_plan_exit("hard_cap")
        llm_turns.note_plan_blocked("declined")
        llm_turns.note_plan_stop("declined")
        assert (turn.plan_exit, turn.plan_blocked, turn.plan_stop) == ("hard_cap", "declined", "declined")
        turn.done = True
        llm_turns.note_plan_exit("repeats")
        assert turn.plan_exit == "hard_cap"
    finally:
        llm_turns._current.set(None)
    llm_turns.note_plan_exit("x"), llm_turns.note_plan_blocked("x"), llm_turns.note_plan_stop("x")                      # no turn running: nothing to mark, and none of them raises


def test_a_plan_turn_is_counted_by_why_it_ended_and_a_stop_with_steps_left_is_counted_as_early():
    from types import SimpleNamespace

    done, early = {}, {}
    code_outcomes.tally_turn(done, {"cap_hit": False, "edited": True, "plan_stop": "plan_done", "tool_steps": 9}, "execute")
    code_outcomes.tally_turn(early, {"cap_hit": False, "edited": True, "plan_stop": "checkpoint_answered", "tool_steps": 4}, "execute")
    assert done["code_plan_stop_plan_done"] == 1 and "code_plan_exec_early" not in done and done["code_plan_exec_tool_steps"] == 9
    assert early["code_plan_stop_checkpoint_answered"] == 1 and early["code_plan_exec_early"] == 1 and early["code_plan_exec_tool_steps"] == 4
    other = {}
    code_outcomes.tally_turn(other, {"cap_hit": False, "edited": True, "plan_stop": "plan_done", "tool_steps": 3}, "plan")                    # only turns carrying out a plan count these
    assert not any(key.startswith("code_plan_stop") for key in other)
    # a turn that answered before calling any tool is still a coding turn when it was carrying out a plan: that is the ending worth counting
    quiet = SimpleNamespace(loop_ran=True, tools={}, cap_hit=False, plan_stop="model_answered", loop_steps=1)
    assert code_outcomes.summarize_turn(quiet)["plan_stop"] == "model_answered"
    assert code_outcomes.summarize_turn(SimpleNamespace(loop_ran=True, tools={}, cap_hit=False, plan_stop="", loop_steps=1)) is None


def test_the_turn_end_is_recorded_for_plan_execution_and_a_declined_or_unanswered_proposal_is_noted():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "if plan_phase == code_plan.EXECUTE_PHASE:\n            await _note_plan_turn_end()" in workflow
    assert 'llm_turns.note_plan_blocked("no_decision")' in workflow and 'llm_turns.note_plan_blocked("declined")' in workflow
    assert "code_plan.stop_reason(done, getattr(turn, \"plan_exit\", \"\"), getattr(turn, \"plan_blocked\", \"\"))" in workflow and "[code plan] a turn carrying out the plan ended: reason=%s" in workflow
    health = (ROOT / "local" / "src" / "pages" / "SonicHealth.tsx").read_text(encoding="utf-8")
    assert "code_plan_stop_" in health


# ---- reading the next step's files ahead (no model call) --------------------------------------------------------------------------------------

def test_the_next_steps_existing_files_are_read_for_the_prompt_and_new_ones_are_not():
    plan = _approved()
    plan["steps"][0]["status"] = "done"                       # s1 (the new file) is done, so the next step is s2: Chat.tsx and chatPreferences.ts
    contents = {"local/src/pages/Chat.tsx": "chat body", "local/src/chatPreferences.ts": "prefs body"}
    block = cp.files_block(plan, contents.get)
    assert "FILES FOR STEP s2" in block and "--- local/src/pages/Chat.tsx" in block and "chat body" in block and "--- local/src/chatPreferences.ts" in block and "prefs body" in block
    fresh = cp.files_block(_approved(), contents.get)         # the first step only creates a new file: there is nothing to read
    assert fresh == ""


def test_prefetch_is_cut_by_file_and_by_total_and_says_so(monkeypatch):
    monkeypatch.setattr(cp, "PREFETCH_TOTAL_CHARS", cp.PREFETCH_FILE_CHARS + 1000)
    plan = _approved()
    plan["steps"][0]["status"] = "done"
    big = {"local/src/pages/Chat.tsx": "x" * (cp.PREFETCH_FILE_CHARS + 500), "local/src/chatPreferences.ts": "y" * (cp.PREFETCH_TOTAL_CHARS)}
    block = cp.files_block(plan, big.get)
    assert "[cut: the first" in block and "not included here" in block


def test_nothing_is_read_for_a_plan_that_is_not_approved_or_finished():
    plan, _ = cp.clean_plan(good_plan(), exists)
    assert cp.files_block({**plan, "state": cp.PROPOSED}, lambda path: "text") == "" and cp.files_block(None, lambda path: "text") == ""
    finished, _ = cp.mark_applied({**plan, "state": cp.APPROVED}, [f["path"] for f in plan["files"]])
    assert cp.files_block(finished, lambda path: "text") == ""


def test_the_execution_turn_reads_the_files_ahead_in_the_agent():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "await _plan_files_block() if plan_phase == code_plan.EXECUTE_PHASE" in workflow and "code_plan.files_block," in workflow


def test_long_text_is_cut_at_a_word_with_an_ellipsis_never_mid_word():
    long_step = " ".join(["add"] + ["counter"] * 80)
    plan, errors = cp.clean_plan(good_plan(steps=[{"text": long_step, "files": ["local/src/components/NewBurst.tsx"]},
                                                 {"text": "Play it from Chat", "files": ["local/src/pages/Chat.tsx", "local/src/chatPreferences.ts"]}]), exists)
    text = plan["steps"][0]["text"]
    assert errors == [] and len(text) <= cp.TEXT_MAX and text.endswith("…") and text[:-1].split(" ")[-1] == "counter"
    short = "Add the burst component"
    assert cp._flat(short, cp.TEXT_MAX) == short and cp.TEXT_MAX >= 300
