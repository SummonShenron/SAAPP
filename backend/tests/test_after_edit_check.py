"""The check that runs (or is named) right after the person applies a change (backend/utils/after_edit_check.py, runner_jobs.create_job(after_edit=True), tool_agent_node _after_edit_check)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_edit_decisions as led
from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_local_edit_decisions import _drive
from backend.tests.test_local_edits import _connect_folder, run_async
from backend.utils import after_edit_check as aec
from backend.utils import runner_jobs as rj
from backend.utils import self_counters

PYTEST = {"name": "tests", "command": ".venv/Scripts/python.exe -m pytest -q -p no:cacheprovider {files}", "ready": True}
TYPES = {"name": "types", "command": "npx tsc -b", "ready": True}
JS = {"name": "js-tests", "command": "npm test", "ready": True}
FOLDER = ["app.py", "backend/utils/thing.py", "backend/tests/test_thing.py", "backend/tests/test_other.py", "backend/utils/other.py", "local/src/a.ts"]


# ---- which check fits ------------------------------------------------------------------------------------------------------------------------

def test_a_python_change_runs_only_its_own_tests_found_anywhere_in_the_folder():
    assert aec.pick(["backend/utils/thing.py"], [PYTEST, TYPES], FOLDER) == {"check": "tests", "files": ["backend/tests/test_thing.py"]}


def test_a_changed_test_file_is_run_itself_first():
    assert aec.pick(["backend/tests/test_other.py", "backend/utils/thing.py"], [PYTEST], FOLDER) == {"check": "tests", "files": ["backend/tests/test_other.py", "backend/tests/test_thing.py"]}


def test_a_python_change_with_no_test_of_its_own_never_runs_the_whole_suite():
    assert aec.pick(["app.py"], [PYTEST], FOLDER) is None


def test_the_python_check_must_take_files_and_be_pytest():
    whole = {"name": "tests", "command": "pytest -q", "ready": True}
    assert aec.pick(["backend/utils/thing.py"], [whole], FOLDER) is None
    assert aec.pick(["backend/utils/thing.py"], [{"name": "lint", "command": "ruff check {files}", "ready": True}], FOLDER) is None


def test_a_check_whose_program_is_missing_is_not_picked():
    assert aec.pick(["backend/utils/thing.py"], [{**PYTEST, "ready": False}], FOLDER) is None
    assert aec.pick(["local/src/a.ts"], [{**TYPES, "ready": False}], FOLDER) is None


def test_typescript_gets_the_type_check():
    assert aec.pick(["local/src/a.tsx"], [PYTEST, TYPES, JS], FOLDER) == {"check": "types", "files": []}


def test_a_javascript_test_change_gets_the_js_tests_when_there_is_no_type_check():
    assert aec.pick(["local/src/a.test.mjs"], [PYTEST, JS], FOLDER) == {"check": "js-tests", "files": []}
    assert aec.pick(["local/src/a.css"], [PYTEST, TYPES, JS], FOLDER) is None


def test_python_tests_win_over_the_type_check_when_both_changed():
    assert aec.pick(["local/src/a.ts", "backend/utils/thing.py"], [TYPES, PYTEST], FOLDER)["check"] == "tests"


def test_at_most_a_few_test_files():
    many = [f"m{i}.py" for i in range(10)]
    folder = [f"tests/test_m{i}.py" for i in range(10)]
    assert len(aec.pick(many, [PYTEST], folder)["files"]) == aec.MAX_TEST_FILES


def test_nothing_changed_or_no_checks_picks_nothing():
    assert aec.pick([], [PYTEST], FOLDER) is None and aec.pick(["backend/utils/thing.py"], [], FOLDER) is None


def test_the_suggestion_names_the_exact_call_and_the_result_says_what_to_do():
    line = aec.suggestion({"check": "tests", "files": ["backend/tests/test_thing.py"]})
    assert 'run_local_check with check "tests"' in line and "backend/tests/test_thing.py" in line and "before you answer" in line
    failed = aec.result_note({"check": "types", "files": []}, "LOCAL CHECK types FAILED (exit code 2, 3 s).", False)
    assert "RAN BY ITSELF" in failed and "FAILED" in failed and "fix that now" in failed
    assert "do not run the same check again" in aec.result_note({"check": "types", "files": []}, "LOCAL CHECK types PASSED", True)


# ---- the job: approved ahead only when the person turned it on -------------------------------------------------------------------------------

NOW = datetime.now(timezone.utc) - timedelta(minutes=1)
PID = "aaaa1111"
USER = "jack@example.com"


def _computer(db):
    code = rj.create_pairing(db, USER, "laptop", NOW)["code"]
    key = rj.authenticate(db, rj.exchange_pairing(db, code, NOW)["key"])
    rj.hello(db, key, {"version": "0.2.0", "launch": "service", "status": "", "projects": [{"id": PID, "name": "local-rag", "status": "ok", "checks": [PYTEST]}]}, NOW)
    return key


def test_an_after_edit_check_waits_for_run_until_the_setting_is_on():
    db = FakeDB()
    key = _computer(db)
    off = rj.create_job(db, USER, key["_id"], "tests", ["backend/tests/test_thing.py"], NOW, PID, after_edit=True)
    assert off["status"] == rj.AWAITING
    rj.cancel_job(db, USER, off["id"], NOW)
    assert rj.update_settings(db, USER, key["_id"], {"after_edit_auto": True})["after_edit_auto"] is True
    on = rj.create_job(db, USER, key["_id"], "tests", ["backend/tests/test_thing.py"], NOW + timedelta(seconds=1), PID, after_edit=True)
    assert on["status"] == rj.APPROVED
    assert rj.claim_next(db, key, NOW + timedelta(seconds=2))["check"] == "tests"


def test_the_setting_never_approves_a_check_the_model_asked_for():
    db = FakeDB()
    key = _computer(db)
    rj.update_settings(db, USER, key["_id"], {"after_edit_auto": True})
    assert rj.create_job(db, USER, key["_id"], "tests", [], NOW, PID)["status"] == rj.AWAITING


def test_the_setting_is_shown_and_only_a_real_true_counts():
    db = FakeDB()
    key = _computer(db)
    assert rj.runners_for(db, USER, NOW)[0]["settings"]["after_edit_auto"] is False
    rj.update_settings(db, USER, key["_id"], {"after_edit_auto": True})
    rj.update_settings(db, USER, key["_id"], {"read_only_auto": True})          # another change does not undo it
    assert rj.runners_for(db, USER, NOW)[0]["settings"]["after_edit_auto"] is True
    view = rj.connected_runner(db, USER, "", NOW)
    assert rj.settings_of(view)["after_edit_auto"] is True
    rj.update_settings(db, USER, key["_id"], {"after_edit_auto": "yes"})
    assert rj.runners_for(db, USER, NOW)[0]["settings"]["after_edit_auto"] is False


# ---- through the tool agent -------------------------------------------------------------------------------------------------------------------

def _runner(monkeypatch, auto, outcome=None):
    """A connected runner with a pytest check, the folder's file list, and a run that ends with `outcome`. Returns what was asked and counted."""
    asked = {"jobs": [], "counts": []}
    view = {"id": "r1", "name": "laptop", "root_name": "local-rag", "project_id": PID, "checks": [PYTEST], "settings": {"disabled": [], "fake_keys": True, "after_edit_auto": auto}}
    monkeypatch.setattr(aw.runner_jobs, "connected_runner", lambda *a, **k: view)
    monkeypatch.setattr(aw, "snapshot_paths", lambda *a, **k: ["src/app.py", "tests/test_app.py"])
    fake = FakeDB()
    monkeypatch.setattr(aw, "get_db", lambda: fake)

    def create_job(db, username, runner_id, check, files=None, now=None, project_id=None, after_edit=False):
        asked["jobs"].append({"check": check, "files": files, "after_edit": after_edit})
        return {"id": "j1", "check": check, "status": rj.APPROVED}

    async def wait_for_result(db, username, job_id, timeout=None, on_tick=None, poll=1.5):
        asked["timeout"] = timeout
        return outcome

    monkeypatch.setattr(aw.runner_jobs, "create_job", create_job)
    monkeypatch.setattr(aw.runner_jobs, "wait_for_result", wait_for_result)
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: asked["counts"].append(dict(counts)))
    return asked


FAILED_RUN = {"id": "j1", "check": "tests", "status": rj.DONE, "exit_code": 1, "duration": 2, "output_tail": "E   assert 'hello' == 'hi'"}
PASSED_RUN = {"id": "j1", "check": "tests", "status": rj.DONE, "exit_code": 0, "duration": 2, "output_tail": "1 passed"}


@run_async
async def test_with_the_setting_on_an_applied_change_runs_its_tests_and_the_failure_goes_back_in_the_same_turn(monkeypatch):
    _connect_folder()
    asked = _runner(monkeypatch, True, FAILED_RUN)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert asked["jobs"] == [{"check": "tests", "files": ["tests/test_app.py"], "after_edit": True}] and asked["timeout"] == aec.WAIT_SECONDS
    assert "APPLIED" in seen["observation"] and "RAN BY ITSELF" in seen["observation"] and "FAILED" in seen["observation"] and "assert 'hello' == 'hi'" in seen["observation"]
    assert {"code_after_edit_check_run": 1, "code_after_edit_check_failed": 1} in asked["counts"]


@run_async
async def test_a_pass_is_counted_as_a_run_not_a_failure(monkeypatch):
    _connect_folder()
    asked = _runner(monkeypatch, True, PASSED_RUN)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert "PASSED" in seen["observation"] and {"code_after_edit_check_run": 1} in asked["counts"]
    assert not any("code_after_edit_check_failed" in c for c in asked["counts"])


@run_async
async def test_a_run_that_did_not_finish_is_not_counted_as_failed(monkeypatch):
    _connect_folder()
    asked = _runner(monkeypatch, True, {"id": "j1", "check": "tests", "status": rj.RUNNING})
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert "NOT FINISHED" in seen["observation"] and {"code_after_edit_check_run": 1} in asked["counts"]


@run_async
async def test_with_the_setting_off_nothing_runs_and_the_result_names_the_check(monkeypatch):
    _connect_folder()
    asked = _runner(monkeypatch, False, FAILED_RUN)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert asked["jobs"] == [] and 'run_local_check with check "tests"' in seen["observation"] and "tests/test_app.py" in seen["observation"]
    assert {"code_after_edit_check_suggested": 1} in asked["counts"]


@run_async
async def test_a_declined_change_runs_nothing(monkeypatch):
    _connect_folder()
    asked = _runner(monkeypatch, True, FAILED_RUN)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "declined"), state_extra={"runner_cards": True})
    assert "DECLINED" in seen["observation"] and asked["jobs"] == [] and "run_local_check" not in seen["observation"].split("DECLINED", 1)[1]


@run_async
async def test_a_busy_runner_falls_back_to_naming_the_check(monkeypatch):
    _connect_folder()
    _runner(monkeypatch, True, FAILED_RUN)

    def busy(*a, **k):
        raise rj.RunnerError("busy", "A check is already waiting or running on that runner.")

    monkeypatch.setattr(aw.runner_jobs, "create_job", busy)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert 'run_local_check with check "tests"' in seen["observation"] and "RAN BY ITSELF" not in seen["observation"]


@run_async
async def test_no_runner_adds_nothing_and_logs_no_failure(monkeypatch, caplog):
    _connect_folder()
    with caplog.at_level("WARNING", logger="SASS Logger"):
        seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"))
    assert "APPLIED" in seen["observation"] and "run_local_check" not in seen["observation"] and "RAN BY ITSELF" not in seen["observation"]
    assert not any("could not pick a check" in record.getMessage() for record in caplog.records)


def test_a_stored_value_that_is_not_a_real_true_does_not_turn_it_on():
    assert rj.settings_of({"settings": {"after_edit_auto": "yes"}})["after_edit_auto"] is False
    assert rj.settings_of({"settings": {"after_edit_auto": True}})["after_edit_auto"] is True


def test_the_counters_are_known_keys():
    for key in ("code_after_edit_check_run", "code_after_edit_check_failed", "code_after_edit_check_suggested"):
        assert key in self_counters._PLAIN_KEYS


@run_async
async def test_a_finished_automatic_run_counts_as_the_turn_checking_its_work_and_an_unfinished_one_does_not(monkeypatch):
    from backend.utils import llm_turns

    _connect_folder()
    _runner(monkeypatch, True, FAILED_RUN)
    turn = llm_turns.begin()
    await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert turn.tools.get("run_local_check", {}).get("calls") == 1


@run_async
async def test_an_unfinished_automatic_run_does_not_count_as_checking(monkeypatch):
    from backend.utils import llm_turns

    _connect_folder()
    asked = _runner(monkeypatch, True, {"id": "j1", "check": "tests", "status": rj.RUNNING})
    turn = llm_turns.begin()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"runner_cards": True})
    assert asked["jobs"] and "NOT FINISHED" in seen["observation"]          # it did run the check, so the assertion below is not vacuous
    assert "run_local_check" not in turn.tools
