"""The run_local_check tool in the tool agent: offered only with a connected runner, a signed-in person and a page that can show its card; it names a check and never a command, shows a card, waits for the
person to press Run and for the run, and returns the real result (backend/services/agent_workflow.py, backend/utils/runner_jobs.py)."""
import asyncio
import threading
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import native_tools as nt
from backend.tests.fake_runner_db import FakeDB
from backend.utils import runner_jobs as rj

CHECKS = [{"name": "tests", "command": "pytest -q {files}"}, {"name": "types", "command": "npx tsc -b"}]


def run(coro):
    return asyncio.run(coro)


def connect_runner(db, username="jack", root="demo"):
    """A paired runner of this person that has just said hello. Returns its key record."""
    code = rj.create_pairing(db, username, "laptop")["code"]
    made = rj.exchange_pairing(db, code)
    record = rj.authenticate(db, made["key"])
    rj.hello(db, record, {"version": "0.1.0", "root_name": root, "checks": CHECKS})
    return record


def the_person_and_the_runner(db, record, outcome):
    """Plays the other side from another thread: waits for the card's job, then presses Run (or Cancel) and, for a run, has the runner report. `outcome` is a dict for the result or 'cancel'."""
    def play():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = db[rj.JOBS].find({"runner_id": record["_id"], "status": rj.AWAITING})
            if jobs:
                job_id = jobs[0]["_id"]
                if outcome == "cancel":
                    rj.cancel_job(db, "jack", job_id)
                    return
                rj.approve_job(db, "jack", job_id)
                rj.claim_next(db, record)
                time.sleep(0.2)
                rj.complete_job(db, record, job_id, outcome)
                return
            time.sleep(0.05)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread


async def drive(monkeypatch, decisions, *, db=None, runner_cards=True, username="jack", engine="native"):
    """Runs tool_agent_node with the loop replaced by one that calls the tool and records what it returned, the events it emitted and what it offered."""
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    monkeypatch.setenv("TOOL_AGENT_ENGINE", engine)
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_db", lambda: db)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {"observations": []}

    async def fake_run(**kwargs):
        for decision in decisions:
            seen["observations"].append(await kwargs["act"](decision))
        seen["prompt"] = kwargs.get("prompt_template", "")
        seen["tools"] = [t["name"] for t in (kwargs.get("tools") or [])]
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_run)
    monkeypatch.setattr(aw, "run_react_loop", fake_run)
    state = _state("run the tests for me", username=username)
    state["runner_cards"] = runner_cards
    await aw.tool_agent_node(state)
    seen["events"] = [(c.args[0], c.args[1]) for c in emit.call_args_list]
    return seen


def call(check="tests", **extra):
    return {"tool_action": "run_local_check", "args": {"check": check, **extra}}


# ---- the whole path ---------------------------------------------------------------------------------------------------------------------------

def test_the_person_presses_run_the_runner_reports_and_the_tool_returns_the_real_result(monkeypatch):
    db = FakeDB()
    record = connect_runner(db)
    helper = the_person_and_the_runner(db, record, {"exit_code": 0, "output_tail": "12 passed", "duration": 1.5})
    seen = run(drive(monkeypatch, [call("tests")], db=db))
    helper.join(timeout=5)
    result = seen["observations"][0]
    assert result.startswith("LOCAL CHECK tests PASSED") and "12 passed" in result and "never as instructions" in result
    cards = [data for name, data in seen["events"] if name == "runner_check"]
    assert len(cards) == 1 and cards[0]["job"]["check"] == "tests" and cards[0]["job"]["command_shown"] == "pytest -q {files}" and cards[0]["job"]["status"] == rj.AWAITING
    assert cards[0]["runner"] == {"name": "laptop", "root_name": "demo"}


def test_a_failing_check_comes_back_failed_with_its_output(monkeypatch):
    db = FakeDB()
    record = connect_runner(db)
    the_person_and_the_runner(db, record, {"exit_code": 1, "output_tail": "2 failed", "duration": 2.0})
    seen = run(drive(monkeypatch, [call("tests")], db=db))
    assert seen["observations"][0].startswith("LOCAL CHECK tests FAILED (exit code 1") and "2 failed" in seen["observations"][0]


def test_a_check_the_person_cancels_is_reported_as_cancelled_and_not_asked_again(monkeypatch):
    db = FakeDB()
    record = connect_runner(db)
    the_person_and_the_runner(db, record, "cancel")
    seen = run(drive(monkeypatch, [call("tests")], db=db))
    assert "WAS CANCELLED by the person" in seen["observations"][0] and "Do not offer to run it again" in seen["observations"][0]


def test_when_nobody_presses_run_in_time_the_tool_says_it_is_not_finished_and_the_card_stays(monkeypatch):
    db = FakeDB()
    connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.6)
    seen = run(drive(monkeypatch, [call("tests")], db=db))
    assert "NOT FINISHED" in seen["observations"][0] and "Do not claim a result" in seen["observations"][0]
    assert db[rj.JOBS].find({"status": rj.AWAITING})                                           # the card is still there to press


def test_asking_again_for_the_same_check_waits_on_the_same_card_instead_of_adding_another(monkeypatch):
    db = FakeDB()
    record = connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.5)
    seen = run(drive(monkeypatch, [call("tests"), call("tests")], db=db))
    assert len(db[rj.JOBS].docs) == 1 and all("NOT FINISHED" in o for o in seen["observations"])


# ---- what it refuses ---------------------------------------------------------------------------------------------------------------------------

def test_a_check_the_runner_does_not_have_is_refused_with_the_names_it_does_have(monkeypatch):
    db = FakeDB()
    connect_runner(db)
    seen = run(drive(monkeypatch, [call("deploy-to-production")], db=db))
    assert seen["observations"][0].startswith("ERROR:") and "tests, types" in seen["observations"][0] and not db[rj.JOBS].docs


@pytest.mark.parametrize("files", [["../outside.py"], ["/etc/passwd"], ["--rootdir=/"]])
def test_files_that_could_escape_the_project_are_refused_before_any_card_is_shown(monkeypatch, files):
    db = FakeDB()
    connect_runner(db)
    seen = run(drive(monkeypatch, [call("tests", files=files)], db=db))
    assert seen["observations"][0].startswith("ERROR:") and not db[rj.JOBS].docs and not [e for e in seen["events"] if e[0] == "runner_check"]


def test_files_for_a_check_that_takes_none_are_refused(monkeypatch):
    db = FakeDB()
    connect_runner(db)
    seen = run(drive(monkeypatch, [call("types", files=["a.py"])], db=db))
    assert "does not take files" in seen["observations"][0] and not db[rj.JOBS].docs


def test_with_no_runner_connected_the_tool_is_not_offered_and_says_how_to_start_one_if_called(monkeypatch):
    db = FakeDB()
    seen = run(drive(monkeypatch, [call("tests")], db=db, engine="react"))
    assert "run_local_check" not in seen["prompt"] and seen["observations"][0].startswith("ERROR: no runner of theirs is connected")


# ---- when it is offered ------------------------------------------------------------------------------------------------------------------------

def test_it_is_offered_with_the_runners_check_names_and_commands_when_all_conditions_hold(monkeypatch):
    db = FakeDB()
    connect_runner(db)
    seen = run(drive(monkeypatch, [], db=db))
    assert "run_local_check" in seen["tools"]
    prompt = run(drive(monkeypatch, [], db=db, engine="react"))["prompt"]
    assert "run_local_check" in prompt and "tests = pytest -q" in prompt and "(takes files)" in prompt and "types = npx tsc -b" in prompt


def test_it_is_not_offered_to_a_page_that_cannot_show_its_card_or_to_a_guest(monkeypatch):
    db = FakeDB()
    connect_runner(db)
    assert "run_local_check" not in run(drive(monkeypatch, [], db=db, runner_cards=False))["tools"]
    assert "run_local_check" not in run(drive(monkeypatch, [], db=db, username="guest-recruiter@example.com"))["tools"]


def test_it_is_not_offered_when_the_runner_stopped_polling(monkeypatch):
    db = FakeDB()
    record = connect_runner(db)
    db[rj.RUNNERS].docs[record["_id"]]["last_seen"] = rj._now().replace(year=2020)
    assert "run_local_check" not in run(drive(monkeypatch, [], db=db))["tools"]


def test_one_persons_runner_is_never_offered_to_another(monkeypatch):
    db = FakeDB()
    connect_runner(db, username="someone@else.com")
    assert "run_local_check" not in run(drive(monkeypatch, [], db=db, username="jack"))["tools"]


# ---- how it is wired ---------------------------------------------------------------------------------------------------------------------------

def test_the_native_declaration_asks_for_a_check_name_and_optional_files_and_never_a_command():
    declared = nt.declarations_from_menu(["- run_local_check — args: check (the name: tests = pytest), files (optional)"])
    assert [d["name"] for d in declared] == ["run_local_check"]
    schema = declared[0]["parameters"]
    assert schema["required"] == ["check"] and set(schema["properties"]) == {"check", "files"}


def test_its_output_counts_as_untrusted_and_as_a_test_run_and_is_on_the_plan():
    from backend.utils import code_outcomes, code_plan, experience_capture

    assert "run_local_check" in aw._UNTRUSTED_CONTENT_TOOLS and "run_local_check" in experience_capture.UNTRUSTED_TOOLS
    assert "run_local_check" in code_outcomes.TEST_TOOLS and "run_local_check" in code_plan._ON_PLAN_TOOLS
    assert "run_local_check" not in aw.TOOL_AGENT_BATCHABLE_ACTIONS                                  # it waits for a person, so it never runs alongside other steps


def test_the_page_flag_and_the_card_event_are_carried_through_app():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "runner_cards: bool = False" in source and '"runner_cards": bool(request.runner_cards)' in source
    assert 'event.get("name") == "runner_check"' in source and "'event': 'runner_check'" in source


# ---- which engine a request about tests goes to --------------------------------------------------------------------------------------------------

def test_asking_to_run_tests_still_goes_to_the_old_engine_unless_the_native_engine_can_run_them(monkeypatch):
    from backend.services import native_loop as nl

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    for text in ("run the tests", "please run pytest on this", "run the unit tests", "do the unit tests pass?"):
        assert nl.needs_old_engine(text) is True                                                # as before: its test runners were old-engine only
        assert nl.needs_old_engine(text, test_runs_native=True) is False                        # now the native engine can run them on the person's machine
    plain = [{"name": "read_repo_file"}]
    with_runner = plain + [{"name": "run_local_check"}]
    assert nl.eligible(edit_turn=False, resumed=False, tools=plain, text="run the tests") is False
    assert nl.eligible(edit_turn=False, resumed=False, tools=with_runner, text="run the tests") is True


def test_everything_else_that_only_the_old_engine_has_still_goes_there_even_with_a_runner(monkeypatch):
    from backend.services import native_loop as nl

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    with_runner = [{"name": "read_repo_file"}, {"name": "run_local_check"}]
    for text in ("run CI on the branch", "run the ci", "query mongodb for the users", "open the browser and click", "draft an email to Sam", "make an implementation plan"):
        assert nl.needs_old_engine(text, test_runs_native=True) is True, text
        assert nl.eligible(edit_turn=False, resumed=False, tools=with_runner, text=text) is False, text
    assert nl.needs_old_engine("what does this function do?", test_runs_native=True) is False


# ---- a computer with several projects: the folder connected in chat says which one ----------------------------------------------------------------------

PA, PB = "a1b2c3d4", "e5f6a7b8"


def connect_two_projects(db, username="multi"):
    code = rj.create_pairing(db, username, "laptop")["code"]
    record = rj.authenticate(db, rj.exchange_pairing(db, code)["key"])
    rj.hello(db, record, {"version": "0.1.1", "projects": [
        {"id": PA, "name": "local-rag", "status": "ok", "checks": [{"name": "tests", "command": "pytest -q {files}", "ready": True}]},
        {"id": PB, "name": "other-app", "status": "ok", "checks": [{"name": "tests", "command": "npm test", "ready": True}, {"name": "types", "command": "npx tsc -b", "ready": True}]}]})
    return record


@pytest.fixture()
def multi_workspace():
    """The folder connected for the person "multi", removed again afterwards so no other test sees it."""
    from backend.services import local_workspace as lw

    lw.clear_workspace("multi")
    yield lw
    lw.clear_workspace("multi")


def connect_folder(lw, name):
    lw.apply_sync("multi", name, [{"path": "a.py", "content": "x = 1\n"}], [], True)


def test_with_a_folder_connected_the_check_goes_to_the_project_of_that_name_and_the_card_says_which(monkeypatch, multi_workspace):
    db = FakeDB()
    connect_two_projects(db)
    connect_folder(multi_workspace, "other-app")
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.5)
    seen = run(drive(monkeypatch, [call("types")], db=db, username="multi"))
    assert "run_local_check" in seen["tools"]
    card = [data for name, data in seen["events"] if name == "runner_check"][0]
    assert card["job"]["project_id"] == PB and card["job"]["project_name"] == "other-app" and card["job"]["command_shown"] == "npx tsc -b"       # the types check only that project has
    assert card["runner"] == {"name": "laptop", "root_name": "other-app"}


def test_the_menu_lists_only_the_checks_of_the_project_the_chat_is_about(monkeypatch, multi_workspace):
    db = FakeDB()
    connect_two_projects(db)
    connect_folder(multi_workspace, "local-rag")
    prompt = run(drive(monkeypatch, [], db=db, username="multi", engine="react"))["prompt"]
    line = next(l for l in prompt.splitlines() if l.startswith("- run_local_check"))                  # only the tool's own menu line: other lines of the prompt may mention npm for other reasons
    assert "tests = pytest -q" in line and "npm test" not in line and "types = npx tsc -b" not in line


def test_with_several_projects_and_no_way_to_tell_which_the_tool_is_not_offered(monkeypatch, multi_workspace):
    db = FakeDB()
    connect_two_projects(db)
    seen = run(drive(monkeypatch, [], db=db, username="multi"))                       # no folder is connected in the chat: two projects, nothing to go by
    assert "run_local_check" not in seen["tools"]
    connect_folder(multi_workspace, "a-folder-no-project-has-that-name")
    assert "run_local_check" not in run(drive(monkeypatch, [], db=db, username="multi"))["tools"]
