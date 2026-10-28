"""One runner per computer, serving several projects (projects.py), the dialog that asks the person to choose a folder (picker.py), and how the loop follows SAAPP's requests to add or forget a project.

The point of all of it: SAAPP can only ASK for a project by name; the folder is chosen on this computer, by the person, or typed by them. No test here lets a path come from the server."""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from saapp_runner import __version__, cli, picker, projects
from saapp_runner import config as cfg
from saapp_runner.client import Client, ServerError
from saapp_runner.loop import Runner

PY = sys.executable


@pytest.fixture()
def folders(tmp_path):
    made = []
    for name in ("alpha", "beta"):
        (tmp_path / name / "tests").mkdir(parents=True)
        made.append(tmp_path / name)
    return made


# ---- the list of projects ----------------------------------------------------------------------------------------------------------------------

def test_a_folder_is_added_once_listed_in_order_and_described(folders, runner_home):
    alpha, beta = folders
    assert projects.load() == [] and projects.describe() == []
    assert projects.add(alpha) == (True, "Added the project alpha.") and projects.add(beta)[0]
    assert projects.add(alpha) == (True, "alpha was already one of this computer's projects.")                  # twice is fine and adds nothing
    assert projects.load() == [alpha.resolve(), beta.resolve()]
    assert [(item["root"], item["exists"]) for item in projects.describe()] == [(str(alpha.resolve()), True), (str(beta.resolve()), True)]


def test_a_project_id_is_short_stable_and_different_for_different_folders(folders):
    alpha, beta = folders
    assert projects.project_id(alpha) == projects.project_id(alpha / ".." / "alpha") and len(projects.project_id(alpha)) == 8
    assert projects.project_id(alpha) != projects.project_id(beta)
    assert all(ch in "0123456789abcdef" for ch in projects.project_id(alpha))


def test_a_folder_that_cannot_be_a_project_is_refused_in_words(tmp_path, runner_home):
    assert projects.add(tmp_path / "missing") == (False, "That is not a folder.")
    (tmp_path / "a-file.txt").write_text("x", encoding="utf-8")
    assert projects.add(tmp_path / "a-file.txt")[0] is False
    assert projects.add(Path.home()) == (False, "That is your whole home folder. Choose the project's own folder inside it.")
    top = Path(Path.home().anchor)
    assert projects.add(top) == (False, "That is the top of a drive. Choose the project's own folder.")
    assert projects.load() == []


def test_a_project_is_forgotten_by_folder_or_by_id_and_nothing_in_it_is_touched(folders, runner_home):
    alpha, beta = folders
    (alpha / "keep.txt").write_text("mine", encoding="utf-8")
    projects.add(alpha), projects.add(beta)
    assert projects.remove(alpha) is True and projects.remove(alpha) is False and projects.load() == [beta.resolve()]
    assert (alpha / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert projects.remove_id("ffffffff") is False and projects.remove_id(projects.project_id(beta)) is True and projects.load() == []


def test_a_folder_that_has_gone_is_still_listed_so_a_move_is_noticed_and_a_damaged_list_reads_as_empty(folders, runner_home):
    alpha, _ = folders
    projects.add(alpha)
    alpha.rename(alpha.with_name("moved"))
    assert projects.describe()[0]["exists"] is False
    projects.path().write_text("not json", encoding="utf-8")
    assert projects.load() == []
    projects.path().write_text(json.dumps({"projects": [{"root": 5}, "junk", {"root": str(alpha)}]}), encoding="utf-8")
    assert projects.load() == [alpha]


def test_the_list_is_kept_private_on_systems_with_permissions(folders, runner_home):
    import os

    projects.add(folders[0])
    if os.name != "nt":
        assert oct(projects.path().stat().st_mode & 0o777) == "0o600"


# ---- the dialog --------------------------------------------------------------------------------------------------------------------------------

class Answers:
    """A stand-in for the window toolkit: answers the questions it is asked, and remembers them."""

    def __init__(self, confirm=(True,), folder="", fail=False):
        self.confirms, self.folder_answer, self.asked, self.closed, self.fail = list(confirm), folder, [], False, fail

    def confirm(self, title, text):
        if self.fail:
            raise RuntimeError("no display")
        self.asked.append(text)
        return self.confirms.pop(0) if self.confirms else False

    def folder(self, title):
        self.asked.append(title)
        return self.folder_answer

    def close(self):
        self.closed = True


def test_the_dialog_asks_first_then_for_the_folder_and_reports_the_one_chosen(tmp_path):
    ask = Answers(folder=str(tmp_path / "my-app"))
    assert picker.run_dialog("my-app", ask) == {"result": "chosen", "path": str(tmp_path / "my-app")}
    assert "my-app" in ask.asked[0] and "after you press Run in SAAPP" in ask.asked[0] and ask.closed is True


def test_saying_no_at_any_step_is_declined_and_a_different_folder_name_asks_once_more(tmp_path):
    assert picker.run_dialog("my-app", Answers(confirm=(False,)))["result"] == "declined"
    assert picker.run_dialog("my-app", Answers(folder=""))["result"] == "declined"                           # the folder window was cancelled
    different = Answers(confirm=(True, False), folder=str(tmp_path / "other-name"))
    assert picker.run_dialog("my-app", different) == {"result": "declined"} and "other-name" in different.asked[-1] and "my-app" in different.asked[-1]
    agreed = Answers(confirm=(True, True), folder=str(tmp_path / "other-name"))
    assert picker.run_dialog("my-app", agreed)["result"] == "chosen"
    same_ignoring_case = Answers(folder=str(tmp_path / "MY-APP"))
    assert picker.run_dialog("my-app", same_ignoring_case)["result"] == "chosen" and len(same_ignoring_case.asked) == 2             # no extra question when only the case differs


def test_a_window_toolkit_that_fails_is_no_picker_not_a_crash():
    assert picker.run_dialog("my-app", Answers(fail=True)) == {"result": "no_picker"}


def test_the_dialog_command_prints_one_line_of_json(monkeypatch, capsys):
    monkeypatch.setattr(picker, "run_dialog", lambda name: {"result": "chosen", "path": "/x/" + name})
    assert cli.main(["pick-folder", "--name", "my-app"]) == 0
    assert json.loads(capsys.readouterr().out) == {"result": "chosen", "path": "/x/my-app"}


def _child(code):
    return [PY, "-c", code]


def _wait(handle, seconds=15.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        answer = handle.poll()
        if answer is not None:
            return answer
        time.sleep(0.05)
    raise AssertionError("the dialog never answered")


def test_a_dialog_in_its_own_process_gives_its_answer_without_blocking_the_caller():
    handle = picker.start_dialog("x", _child("import time, json; time.sleep(0.4); print(json.dumps({'result': 'chosen', 'path': '/p/x'}))"))
    assert handle.poll() is None                                                                 # still open: the caller was not held up
    assert _wait(handle) == {"result": "chosen", "path": "/p/x"} and handle.poll() == {"result": "chosen", "path": "/p/x"}


def test_an_answer_that_is_not_one_of_the_known_ones_or_no_answer_is_an_error():
    for code in ("print('junk')", "import json; print(json.dumps({'result': 'launch-missiles'}))", "pass", "import sys; sys.exit(3)"):
        assert _wait(picker.start_dialog("x", _child(code))) == {"result": "error"}


def test_a_dialog_nobody_answers_is_closed_after_the_window_the_server_gives_it():
    handle = picker.start_dialog("x", _child("import time; time.sleep(60)"))
    assert handle.poll() is None
    assert handle.poll(now=time.monotonic() + picker.TIMEOUT_SECONDS + 5) == {"result": "declined"}
    handle.process.wait(timeout=10)
    assert handle.process.poll() is not None                                                      # the process was ended too


# ---- the client keeps only what it understands --------------------------------------------------------------------------------------------------

def test_the_client_keeps_only_a_name_and_an_id_from_an_add_request_and_ignores_the_rest(fake):
    client = Client(fake.url, "abc.def")
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": None, "add_project": {"id": "r1", "name": "my-app", "path": "C:/Windows", "cmd": "rm -rf /"}, "remove_project": "a1b2c3d4"})
    assert client.next_job(0) is None
    assert client.add_project == {"id": "r1", "name": "my-app"} and client.remove_project == "a1b2c3d4"          # a path the server sent is never even kept
    for odd in ({"job": None, "add_project": "my-app"}, {"job": None, "add_project": {"id": "r1"}}, {"job": None, "add_project": ["x"], "remove_project": {"a": 1}}, {"job": None}):
        fake.routes[("GET", "/api/runner/next")] = (200, odd)
        client.next_job(0)
        assert client.add_project is None and client.remove_project is None, odd


# ---- the loop ----------------------------------------------------------------------------------------------------------------------------------

class Client3:
    """A stand-in for the server: one scripted poll after another, each giving a job and/or instructions, and a record of what the runner said."""

    def __init__(self, polls=()):
        self.polls = list(polls)
        self.update_to = self.add_project = self.remove_project = None
        self.hellos, self.results, self.reported = [], [], []

    def hello(self, projects_, status="", launch="", request_results=None):
        self.hellos.append(projects_)
        self.results.append(list(request_results or []))
        return {"ok": True}

    def next_job(self, wait=25):
        if not self.polls:
            raise ServerError(401, "end of the script")
        poll = self.polls.pop(0)
        self.update_to, self.add_project, self.remove_project = poll.get("update"), poll.get("add"), poll.get("remove")
        return poll.get("job")

    def heartbeat(self, job_id):
        return {}

    def result(self, job_id, payload):
        self.reported.append((job_id, payload))
        return {}


class FakeDialog:
    def __init__(self, answers):
        self.answers, self.polls = list(answers), 0

    def poll(self):
        self.polls += 1
        return self.answers.pop(0) if self.answers else None

    def cancel(self):
        pass


def runner_for(client, **kwargs):
    said = []
    runner = Runner(client, None, say=said.append, poll_wait=0, idle_wait=0.0, roots_provider=projects.load, scanner=lambda folder: ({"tests": [PY, "-c", "print('ran')"]}, True, ""), **kwargs)
    return runner, said


def test_a_project_asked_for_is_added_only_after_the_person_chooses_a_folder_and_is_reported(folders, runner_home):
    alpha, _ = folders
    asked = []
    dialog = FakeDialog([None, None, {"result": "chosen", "path": str(alpha)}])
    client = Client3([{"add": {"id": "r1", "name": "alpha"}}, {}, {}, {}])
    runner, said = runner_for(client, picker=lambda name: (asked.append(name), dialog)[1])
    runner.hello()
    runner.serve()
    assert asked == ["alpha"] and dialog.polls >= 3                                         # the loop kept polling while the window was open
    assert projects.load() == [alpha.resolve()]
    assert client.results[-1] == [{"id": "r1", "result": "added"}] and [p["name"] for p in client.hellos[-1]] == ["alpha"]
    assert any("A window on this computer asks you to choose its folder" in line for line in said)


def test_saying_no_adds_nothing_and_tells_the_server(folders, runner_home):
    client = Client3([{"add": {"id": "r1", "name": "alpha"}}, {}, {}])
    runner, _ = runner_for(client, picker=lambda name: FakeDialog([{"result": "declined"}]))
    runner.hello()
    runner.serve()
    assert projects.load() == [] and client.results[-1] == [{"id": "r1", "result": "declined"}] and client.hellos[-1] == []


def test_a_computer_that_cannot_show_a_dialog_says_so_and_how_to_add_the_folder_by_hand(runner_home):
    client = Client3([{"add": {"id": "r1", "name": "alpha"}}])
    runner, said = runner_for(client, picker=None)
    runner.hello()
    runner.serve()
    assert client.results[-1] == [{"id": "r1", "result": "no_picker"}] and any("saapp-runner add-project --root" in line for line in said)
    assert projects.load() == []


def test_a_folder_that_cannot_be_a_project_is_an_error_not_an_addition(runner_home):
    client = Client3([{"add": {"id": "r1", "name": "home"}}, {}, {}])
    runner, said = runner_for(client, picker=lambda name: FakeDialog([{"result": "chosen", "path": str(Path.home())}]))
    runner.hello()
    runner.serve()
    assert projects.load() == [] and client.results[-1] == [{"id": "r1", "result": "error"}] and any("whole home folder" in line for line in said)
    for answer, expected in (({"result": "no_picker"}, "no_picker"), ({"result": "error"}, "error"), ({"result": "chosen"}, "error")):
        client = Client3([{"add": {"id": "r2", "name": "x"}}, {}, {}])
        runner, _ = runner_for(client, picker=lambda name, answer=answer: FakeDialog([answer]))
        runner.hello()
        runner.serve()
        assert client.results[-1] == [{"id": "r2", "result": expected}], answer


def test_one_dialog_at_a_time_and_a_repeated_request_does_not_open_a_second_window(folders, runner_home):
    alpha, beta = folders
    opened = []
    dialogs = {"alpha": FakeDialog([None, None, {"result": "chosen", "path": str(alpha)}]), "beta": FakeDialog([{"result": "chosen", "path": str(beta)}])}
    client = Client3([{"add": {"id": "r1", "name": "alpha"}}, {"add": {"id": "r1", "name": "alpha"}}, {"add": {"id": "r2", "name": "beta"}}, {}, {}, {}, {}, {}])
    runner, _ = runner_for(client, picker=lambda name: (opened.append(name), dialogs[name])[1])
    runner.hello()
    runner.serve()
    assert opened == ["alpha", "beta"] and projects.load() == [alpha.resolve(), beta.resolve()]          # alpha once, then beta after it was answered
    assert sorted(r["id"] for results in client.results for r in results) == ["r1", "r2"]


def test_removing_a_project_in_saapp_makes_the_runner_forget_it_and_say_so(folders, runner_home):
    alpha, beta = folders
    projects.add(alpha), projects.add(beta)
    client = Client3([{"remove": projects.project_id(alpha)}, {}])
    runner, said = runner_for(client)
    runner.hello()
    assert [p["name"] for p in client.hellos[-1]] == ["alpha", "beta"]
    runner.serve()
    assert projects.load() == [beta.resolve()] and [p["name"] for p in client.hellos[-1]] == ["beta"] and any("removed here" in line for line in said)
    assert (alpha / "tests").is_dir()                                                                # the folder itself is untouched


def test_a_job_runs_in_the_folder_of_the_project_it_names_and_nowhere_else(folders, runner_home):
    alpha, beta = folders
    projects.add(alpha), projects.add(beta)
    script = "import os; print(os.path.basename(os.getcwd()))"
    client = Client3([{"job": {"job_id": "j1", "check": "tests", "files": [], "project_id": projects.project_id(beta)}}, {"job": {"job_id": "j2", "check": "tests", "files": [], "project_id": projects.project_id(alpha)}}])
    said = []
    runner = Runner(client, None, say=said.append, poll_wait=0, idle_wait=0.0, heartbeat_every=0.2, roots_provider=projects.load, scanner=lambda folder: ({"tests": [PY, "-c", script]}, True, ""))
    runner.serve()
    assert [(job_id, report["output_tail"].strip()) for job_id, report in client.reported] == [("j1", "beta"), ("j2", "alpha")]
    assert any("Running tests in beta" in line for line in said)


def test_a_job_for_a_project_this_computer_does_not_serve_is_refused_and_nothing_runs(folders, runner_home):
    alpha, _ = folders
    projects.add(alpha)
    client = Client3([{"job": {"job_id": "j1", "check": "tests", "files": [], "project_id": "ffffffff"}}, {"job": {"job_id": "j2", "check": "nonexistent", "files": [], "project_id": projects.project_id(alpha)}}])
    runner, said = runner_for(client)
    runner.serve()
    assert [(job_id, report["exit_code"]) for job_id, report in client.reported] == [("j1", None), ("j2", None)]
    assert "does not serve that project" in client.reported[0][1]["error"] and "has no check called nonexistent" in client.reported[1][1]["error"]
    assert not any("ran" in report.get("output_tail", "") for _, report in client.reported)


def test_a_job_with_no_project_goes_to_the_only_one_and_is_refused_when_there_are_several(folders, runner_home):
    alpha, beta = folders
    projects.add(alpha)
    client = Client3([{"job": {"job_id": "j1", "check": "tests", "files": []}}])
    runner, _ = runner_for(client)
    runner.serve()
    assert client.reported[0][1]["output_tail"].strip() == "ran"                                      # an older server that names no project: the only one is meant
    projects.add(beta)
    client = Client3([{"job": {"job_id": "j2", "check": "tests", "files": []}}])
    runner, _ = runner_for(client)
    runner.serve()
    assert client.reported[0][1]["exit_code"] is None and "does not serve that project" in client.reported[0][1]["error"]


def test_an_update_waits_for_the_dialog_to_be_answered(folders, runner_home):
    alpha, _ = folders
    calls = []
    dialog = FakeDialog([None, None, {"result": "chosen", "path": str(alpha)}])
    client = Client3([{"add": {"id": "r1", "name": "alpha"}, "update": "9.9.9"}, {"update": "9.9.9"}, {"update": "9.9.9"}, {"update": "9.9.9"}, {}])
    runner, _ = runner_for(client, picker=lambda name: dialog, updater=lambda version: (calls.append(version), (False, ""))[1])
    runner.hello()
    runner.serve()
    assert calls[:1] == ["9.9.9"] and dialog.polls >= 3 and projects.load() == [alpha.resolve()]       # the first update attempt came only after the window was answered


# ---- the commands ------------------------------------------------------------------------------------------------------------------------------

def test_add_remove_and_list_projects_by_hand(folders, runner_home, capsys):
    alpha, _ = folders
    assert cli.main(["projects"]) == 0 and "No projects yet" in capsys.readouterr().out
    assert cli.main(["add-project", "--root", str(alpha)]) == 0 and "Added the project alpha" in capsys.readouterr().out
    assert cli.main(["projects"]) == 0
    assert projects.project_id(alpha) in capsys.readouterr().out
    assert cli.main(["add-project", "--root", str(alpha.parent / "nowhere")]) == 1 and "That is not a folder." in capsys.readouterr().out
    assert cli.main(["remove-project", "--root", str(alpha)]) == 0 and "Nothing in the folder was touched" in capsys.readouterr().out
    assert cli.main(["remove-project", "--root", str(alpha)]) == 1 and "not one of this computer's projects" in capsys.readouterr().out


def test_add_project_without_a_folder_asks_for_one_and_gives_up_politely(folders, runner_home, monkeypatch, capsys):
    alpha, _ = folders
    monkeypatch.setattr("builtins.input", lambda prompt="": f' "{alpha}" ')
    assert cli.main(["add-project"]) == 0 and projects.load() == [alpha.resolve()]
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    assert cli.main(["add-project"]) == 1 and "Nothing was added" in capsys.readouterr().out


def test_start_with_a_typed_folder_allows_it_and_runs_for_every_project(fake, folders, runner_home):
    alpha, beta = folders
    cfg.save_credentials(fake.url, "abc.def")
    projects.add(beta)
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (401, {"detail": "stop"})
    assert cli.cmd_start(argparse.Namespace(root=str(alpha), timeout=5), say=lambda line: None, stop=threading.Event()) == 0
    hello = next(call for call in fake.seen if call["path"].endswith("/hello"))
    assert sorted(p["name"] for p in hello["body"]["projects"]) == ["alpha", "beta"] and hello["body"]["version"] == __version__
