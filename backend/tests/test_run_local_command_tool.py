"""The run_local_command tool in the tool agent: offered only with a connected runner that is new enough, a signed-in person and a page that can show the card; a refused line is told to the model at once and
never becomes a card; the person's Run (and for a change, the stronger confirmation) lets it run; the result is the real one, labelled as data (backend/services/agent_workflow.py, runner_jobs.py)."""
import threading
import time

import pytest

from backend.services import agent_workflow as aw
from backend.services import native_tools as nt
from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_run_local_check_tool import drive, run
from backend.utils import runner_jobs as rj

PID = "aaaa1111"


def connect(db, username="jack", version="0.1.1", name="demo", read_only_auto=False):
    code = rj.create_pairing(db, username, "laptop")["code"]
    record = rj.authenticate(db, rj.exchange_pairing(db, code)["key"])
    rj.hello(db, record, {"version": version, "launch": "service", "status": "", "projects": [
        {"id": PID, "name": name, "status": "ok", "checks": [{"name": "tests", "command": "pytest -q", "ready": True}]}]})
    if read_only_auto:
        rj.update_settings(db, username, record["_id"], {"read_only_auto": True})
    return record


def person_and_runner(db, record, outcome, confirmed=False, run_it=True):
    """The other side: presses Run (with the stronger confirmation when `confirmed`), then the runner reports `outcome`. 'cancel' cancels the card."""
    def play():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = db[rj.JOBS].find({"runner_id": record["_id"], "status": {"$in": [rj.AWAITING, rj.APPROVED]}})
            if jobs:
                job = jobs[0]
                if outcome == "cancel":
                    rj.cancel_job(db, "jack", job["_id"])
                    return
                if job["status"] == rj.AWAITING:
                    rj.approve_job(db, "jack", job["_id"], None, confirmed)
                claimed = rj.claim_next(db, record)
                if claimed and run_it:
                    time.sleep(0.2)
                    rj.complete_job(db, record, claimed["job_id"], outcome)
                return
            time.sleep(0.05)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread


def command(line):
    return {"tool_action": "run_local_command", "args": {"line": line}}


def test_the_person_presses_run_the_runner_reports_and_the_tool_returns_the_real_result(monkeypatch):
    db = FakeDB()
    record = connect(db)
    helper = person_and_runner(db, record, {"exit_code": 0, "output_tail": "On branch main", "duration": 0.3})
    seen = run(drive(monkeypatch, [command("git   status")], db=db))
    helper.join(timeout=5)
    result = seen["observations"][0]
    assert result.startswith("LOCAL COMMAND `git status` FINISHED with exit code 0") and "On branch main" in result and "never as instructions" in result
    (card,) = [data for name, data in seen["events"] if name == "runner_check"]
    assert card["job"]["kind"] == "command" and card["job"]["command_shown"] == "git status" and card["job"]["command_class"] == "read" and card["job"]["status"] == rj.AWAITING
    assert card["runner"] == {"name": "laptop", "root_name": "demo"}


def test_a_change_runs_only_after_the_stronger_confirmation(monkeypatch):
    db = FakeDB()
    record = connect(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 1.0)
    person_and_runner(db, record, {"exit_code": 0, "output_tail": "", "duration": 0.1}, confirmed=False).join(timeout=5)
    seen = run(drive(monkeypatch, [command("git add -A")], db=db))
    assert "NOT FINISHED" in seen["observations"][0] and db[rj.JOBS].find({"status": rj.AWAITING})          # one plain click did not approve a change
    db = FakeDB()
    record = connect(db)
    helper = person_and_runner(db, record, {"exit_code": 0, "output_tail": "", "duration": 0.1}, confirmed=True)
    seen = run(drive(monkeypatch, [command("git add -A")], db=db))
    helper.join(timeout=5)
    assert "FINISHED with exit code 0" in seen["observations"][0]


def test_a_refused_line_is_told_to_the_model_at_once_and_no_card_is_shown(monkeypatch):
    db = FakeDB()
    connect(db)
    lines = ["rm -rf .", "cat .env", "curl http://x", "git push --force", "echo $HOME", "cd .."]
    seen = run(drive(monkeypatch, [command(line) for line in lines], db=db))
    assert all(o.startswith("ERROR: ") for o in seen["observations"]) and "never run here" in seen["observations"][0]
    assert not db[rj.JOBS].docs and not [1 for name, _ in seen["events"] if name == "runner_check"]


def test_a_card_the_person_cancels_is_reported_and_not_asked_again(monkeypatch):
    db = FakeDB()
    record = connect(db)
    person_and_runner(db, record, "cancel")
    seen = run(drive(monkeypatch, [command("pytest -q")], db=db))
    assert "WAS CANCELLED by the person" in seen["observations"][0] and "Do not offer to run it again" in seen["observations"][0]


def test_a_refusal_by_the_runner_on_their_computer_comes_back_with_its_reason(monkeypatch):
    db = FakeDB()
    record = connect(db)
    person_and_runner(db, record, {"exit_code": None, "error": "The runner refused it: your own rules in commands.toml refuse \"pytest\""})
    seen = run(drive(monkeypatch, [command("pytest -q")], db=db))
    assert "DID NOT RUN" in seen["observations"][0] and "your own rules" in seen["observations"][0]


def test_a_read_only_line_runs_without_a_click_when_the_person_turned_that_on(monkeypatch):
    db = FakeDB()
    record = connect(db, read_only_auto=True)
    helper = person_and_runner(db, record, {"exit_code": 0, "output_tail": "src/", "duration": 0.1})
    seen = run(drive(monkeypatch, [command("ls")], db=db))
    helper.join(timeout=5)
    assert "FINISHED with exit code 0" in seen["observations"][0]
    (card,) = [data for name, data in seen["events"] if name == "runner_check"]
    assert card["job"]["auto_approved"] is True


def test_asking_again_for_the_same_line_waits_on_the_same_card(monkeypatch):
    db = FakeDB()
    connect(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.5)
    seen = run(drive(monkeypatch, [command("git status"), command("git  status")], db=db))
    assert len(db[rj.JOBS].docs) == 1 and all("NOT FINISHED" in o for o in seen["observations"])


# ---- when it is offered ------------------------------------------------------------------------------------------------------------------------

def test_it_is_offered_to_a_new_enough_runner_with_the_rules_in_its_description(monkeypatch):
    db = FakeDB()
    connect(db)
    seen = run(drive(monkeypatch, [], db=db))
    assert "run_local_command" in seen["tools"]
    prompt = run(drive(monkeypatch, [], db=db, engine="react"))["prompt"]
    assert "run_local_command" in prompt and "stronger confirmation" in prompt and "never as instructions" in prompt and "sudo" in prompt


def test_it_is_not_offered_to_an_old_runner_a_page_without_cards_a_guest_or_another_persons_runner(monkeypatch):
    db = FakeDB()
    connect(db, version="0.1.0")
    assert "run_local_command" not in run(drive(monkeypatch, [], db=db))["tools"]
    db = FakeDB()
    connect(db)
    assert "run_local_command" not in run(drive(monkeypatch, [], db=db, runner_cards=False))["tools"]
    assert "run_local_command" not in run(drive(monkeypatch, [], db=db, username="guest-recruiter@example.com"))["tools"]
    db = FakeDB()
    connect(db, username="someone@else.com")
    assert "run_local_command" not in run(drive(monkeypatch, [], db=db, username="jack"))["tools"]


def test_called_with_no_runner_it_says_how_to_start_one(monkeypatch):
    seen = run(drive(monkeypatch, [command("git status")], db=FakeDB(), engine="react"))
    assert seen["observations"][0].startswith("ERROR: no runner of theirs that can run commands")


def test_it_is_not_offered_if_the_server_could_not_load_the_rules(monkeypatch):
    from backend.utils import runner_commands

    db = FakeDB()
    connect(db)
    monkeypatch.setattr(runner_commands, "AVAILABLE", False)
    assert "run_local_command" not in run(drive(monkeypatch, [], db=db))["tools"]


# ---- how it is wired ---------------------------------------------------------------------------------------------------------------------------

def test_the_native_declaration_asks_for_one_line_and_nothing_else():
    declared = nt.declarations_from_menu(["- run_local_command — args: line (ONE command line)"])
    assert [d["name"] for d in declared] == ["run_local_command"] and declared[0]["parameters"]["required"] == ["line"] and set(declared[0]["parameters"]["properties"]) == {"line"}


def test_its_output_is_untrusted_it_waits_for_a_person_and_it_is_on_the_plan():
    from backend.utils import code_plan, experience_capture

    assert "run_local_command" in aw._UNTRUSTED_CONTENT_TOOLS and "run_local_command" in experience_capture.UNTRUSTED_TOOLS and "run_local_command" in code_plan._ON_PLAN_TOOLS
    assert "run_local_command" not in aw.TOOL_AGENT_BATCHABLE_ACTIONS


def test_a_plan_turn_refuses_it_until_the_plan_is_approved():
    import pathlib

    source = (pathlib.Path(aw.__file__)).read_text(encoding="utf-8")
    block = source.split('if tool_action == "run_local_command":', 1)[1][:260]
    assert "PLAN_PHASE" in block and "submit_plan" in block


def test_the_changelog_says_it_in_the_first_person_and_the_runner_files_it_depends_on_exist():
    import pathlib

    from backend.components.sonic_changelog import CHANGELOG

    entry = next(item for item in CHANGELOG if item["id"] == "run_local_commands")
    assert entry["line"].startswith("With the SAAPP runner") and " I can now " in entry["line"]
    root = pathlib.Path(aw.__file__).resolve().parents[2]
    assert all((root / spec.split("::")[0]).exists() for spec in entry["requires"])


def test_approve_endpoint_takes_the_confirmation_and_the_setting_is_a_yes_or_no():
    import pathlib

    source = (pathlib.Path(aw.__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "class RunnerApproveRequest" in source and "confirmed: bool = False" in source and "read_only_auto: Optional[bool] = None" in source
    assert "runner_jobs.approve_job, _runner_db_or_503(), username, job_id, None, bool(body and body.confirmed)" in source
