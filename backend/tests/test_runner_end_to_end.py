"""The whole local runner path, for real: the actual runner package (runner/src/saapp_runner) talking over HTTP to a server built on the actual job queue (backend/utils/runner_jobs.py), running real commands.

The server here is a small stand-in for app.py's endpoints, but it calls the SAME `api_*` functions and the SAME status table, so what is proved is the protocol, the approval rule, cancelling, revoking, and that
the runner refuses what it should EVEN IF THE SERVER IS WRONG. (app.py's own endpoints are checked for wiring in test_runner_jobs_wiring.py.)
"""
import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "runner" / "src"))

from saapp_runner import config as runner_config  # noqa: E402
from saapp_runner import projects as runner_projects  # noqa: E402
from saapp_runner.client import Client, ServerError  # noqa: E402
from saapp_runner.loop import Runner  # noqa: E402

from backend.tests.fake_runner_db import FakeDB  # noqa: E402
from backend.utils import runner_jobs as rj  # noqa: E402

PY = sys.executable
JACK, SAM = "jack@example.com", "sam@example.com"


class Server:
    """The runner endpoints over real HTTP, backed by the real queue and a fake database."""

    def __init__(self):
        self.db = FakeDB()

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                path = self.path.split("?")[0]
                try:
                    status, payload = 200, outer.route(self.command, path, body, self.headers.get("Authorization") or "")
                except rj.RunnerError as problem:
                    status, payload = rj.STATUS_FOR.get(problem.code, 400), {"detail": problem.message}
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _answer

            def log_message(self, *args):
                return None

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        return self

    def route(self, method, path, body, authorization):
        if (method, path) == ("POST", "/api/runner/pair"):
            return rj.api_pair(self.db, body)
        key = rj.authenticate(self.db, authorization.removeprefix("Bearer ").strip())
        if not key:
            raise rj.RunnerError("bad_key", "That runner key is not valid (it may have been removed).")
        if (method, path) == ("POST", "/api/runner/hello"):
            return rj.api_hello(self.db, key, body)
        if (method, path) == ("GET", "/api/runner/next"):
            return rj.api_next(self.db, key)
        parts = path.strip("/").split("/")
        if method == "POST" and parts[:3] == ["api", "runner", "jobs"] and len(parts) == 5 and parts[4] == "heartbeat":
            return rj.api_heartbeat(self.db, key, parts[3])
        if method == "POST" and parts[:3] == ["api", "runner", "jobs"] and len(parts) == 5 and parts[4] == "result":
            return rj.api_result(self.db, key, parts[3], body)
        raise rj.RunnerError("not_running", "no such endpoint")

    def stop(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture()
def server():
    running = Server().start()
    yield running
    running.stop()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("SAAPP_RUNNER_HOME", str(tmp_path / "home"))
    root = tmp_path / "myapp"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_ok.py").write_text("print('ok')\n", encoding="utf-8")
    return root


def until(check, timeout=25.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


def pair_and_start(server, project, checks, username=JACK, **kwargs):
    """What a person does: asks for a pairing code in settings, pairs the runner, starts it. Returns the running Runner and its thread."""
    code = rj.create_pairing(server.db, username, "laptop")["code"]
    made = Client(server.url).pair(code)
    runner = Runner(Client(server.url, made["key"]), project, checks, say=lambda line: None, poll_wait=0, idle_wait=0.05, heartbeat_every=0.2, **kwargs)
    assert runner.hello() is True
    thread = threading.Thread(target=runner.serve, daemon=True)
    thread.start()
    return runner, thread, made["key"].split(".")[0]


def finish(runner, thread):
    runner.stop.set()
    thread.join(timeout=10)


def ask(server, username, runner_id, check, files=None, approve=True):
    job = rj.create_job(server.db, username, runner_id, check, files)
    if approve:
        assert rj.approve_job(server.db, username, job["id"])
    return job


# ---- the happy path ---------------------------------------------------------------------------------------------------------------------------

def test_pairing_then_an_approved_check_runs_on_the_machine_and_the_result_comes_back(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", "print('suite passed')"]})
    try:
        assert rj.runners_for(server.db, JACK)[0]["connected"] is True
        job = ask(server, JACK, runner_id, "tests")
        done = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)
        assert done["exit_code"] == 0 and done["output_tail"].strip() == "suite passed" and done["duration"] is not None
        assert rj.format_result(done).startswith("LOCAL CHECK tests PASSED")
    finally:
        finish(runner, thread)


def test_a_failing_check_comes_back_failed_with_its_output(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", "import sys; print('1 failed'); sys.exit(1)"]})
    try:
        job = ask(server, JACK, runner_id, "tests")
        done = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)
        assert done["exit_code"] == 1 and "1 failed" in rj.format_result(done) and rj.format_result(done).startswith("LOCAL CHECK tests FAILED (exit code 1")
    finally:
        finish(runner, thread)


def test_files_reach_the_command_as_safe_words(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", "import sys; print(sys.argv[1:])", "{files}"]})
    try:
        job = ask(server, JACK, runner_id, "tests", ["tests/test_ok.py"])
        done = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)
        assert done["output_tail"].strip() == "['./tests/test_ok.py']"
    finally:
        finish(runner, thread)


# ---- the settings chosen in the page -----------------------------------------------------------------------------------------------------------

def test_the_fake_key_choice_made_in_settings_reaches_the_command_and_a_turned_off_check_cannot_be_asked_for(server, project, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "real-secret-key")
    (project / ".env").write_text("GOOGLE_API_KEY=real-from-dotenv\n", encoding="utf-8")
    script = ("import os, pathlib\n"
              "for line in pathlib.Path('.env').read_text().splitlines():\n"
              "    key, _, value = line.partition('=')\n"
              "    os.environ.setdefault(key, value)\n"
              "print('key=' + os.environ['GOOGLE_API_KEY'])")
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", script], "types": [PY, "-c", "print('types')"]})
    try:
        outputs = []
        for fake_keys in (True, False):
            rj.update_settings(server.db, JACK, runner_id, {"project_id": runner_projects.project_id(project), "disabled": ["types"], "fake_keys": fake_keys})
            job = ask(server, JACK, runner_id, "tests")
            outputs.append(until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)["output_tail"].strip())
        assert outputs == ["key=test-not-real", "key=real-from-dotenv"]                           # the page's yes/no decided it; the shell's own key never reached either run
        with pytest.raises(rj.RunnerError) as error:
            ask(server, JACK, runner_id, "types")
        assert error.value.code == "unknown_check"
    finally:
        finish(runner, thread)


# ---- approval, cancelling, revoking -----------------------------------------------------------------------------------------------------------

def test_nothing_runs_until_the_person_approves_and_then_it_runs_once(server, project, tmp_path):
    counter = tmp_path / "runs.txt"
    command = [PY, "-c", f"open({str(counter)!r}, 'a').write('x')"]
    runner, thread, runner_id = pair_and_start(server, project, {"tests": command})
    try:
        job = ask(server, JACK, runner_id, "tests", approve=False)
        time.sleep(1.0)
        assert not counter.exists() and rj.get_job(server.db, JACK, job["id"])["status"] == rj.AWAITING
        rj.approve_job(server.db, JACK, job["id"])
        until(lambda: rj.get_job(server.db, JACK, job["id"])["status"] == rj.DONE)
        time.sleep(0.5)
        assert counter.read_text() == "x"
    finally:
        finish(runner, thread)


def test_cancelling_in_the_page_stops_a_running_check(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", "import time; print('started', flush=True); time.sleep(60)"]}, timeout=60)
    try:
        job = ask(server, JACK, runner_id, "tests")
        until(lambda: rj.get_job(server.db, JACK, job["id"])["status"] == rj.RUNNING)
        assert rj.cancel_job(server.db, JACK, job["id"])["status"] == rj.RUNNING                    # a running job is told to stop at the next heartbeat
        ended = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.CANCELLED and j)
        assert ended["exit_code"] is None
    finally:
        finish(runner, thread)


def test_removing_the_runner_in_settings_ends_it_and_cancels_its_job(server, project):
    said = []
    code = rj.create_pairing(server.db, JACK, "laptop")["code"]
    made = Client(server.url).pair(code)
    runner = Runner(Client(server.url, made["key"]), project, {"tests": [PY, "-c", "print(1)"]}, say=said.append, poll_wait=0, idle_wait=0.05)
    assert runner.hello()
    waiting = rj.create_job(server.db, JACK, made["key"].split(".")[0], "tests", None)
    thread = threading.Thread(target=runner.serve, daemon=True)
    assert rj.revoke_key(server.db, JACK, made["key"].split(".")[0])
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive() and any("Stopping:" in line and "not valid" in line for line in said)
    assert rj.get_job(server.db, JACK, waiting["id"])["status"] == rj.CANCELLED


def test_a_runner_older_than_the_server_accepts_is_turned_away_with_the_reason(server, project, monkeypatch):
    monkeypatch.setattr(rj, "MIN_RUNNER_VERSION", (9, 0, 0))
    code = rj.create_pairing(server.db, JACK, "laptop")["code"]
    made = Client(server.url).pair(code)
    said = []
    runner = Runner(Client(server.url, made["key"]), project, {"tests": [PY, "-c", "print(1)"]}, say=said.append)
    assert runner.hello() is False and any("9.0.0" in line and "Update" in line for line in said)


def test_a_wrong_or_reused_pairing_code_is_refused(server):
    code = rj.create_pairing(server.db, JACK, "laptop")["code"]
    Client(server.url).pair(code)
    with pytest.raises(ServerError) as error:
        Client(server.url).pair(code)
    assert error.value.status == 401 and "wrong, already used, or expired" in error.value.message
    with pytest.raises(ServerError):
        Client(server.url).pair("NOTAREALCODE")


# ---- the runner does not trust the server ------------------------------------------------------------------------------------------------------

def test_a_job_naming_a_check_the_person_never_defined_is_refused_even_if_the_server_hands_it_out(server, project, tmp_path):
    marker = tmp_path / "ran.txt"
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')"]})
    try:
        # A wrong or hostile server writes an approved job straight into the queue, skipping every check the real one makes.
        server.db[rj.JOBS].insert_one({"_id": "evil", "username": JACK, "runner_id": runner_id, "check": "deploy-to-production", "files": [], "status": rj.APPROVED, "approved_at": rj._now()})
        refused = until(lambda: (j := rj.get_job(server.db, JACK, "evil")) and j["status"] == rj.FAILED and j)
        assert "no check called" in refused["error"] and not marker.exists()
    finally:
        finish(runner, thread)


@pytest.mark.parametrize("files", [["../outside.py"], ["/etc/passwd"], ["--rootdir=/"], ["missing.py"]])
def test_files_that_escape_the_project_are_refused_even_if_the_server_hands_them_out(server, project, tmp_path, files):
    marker = tmp_path / "ran.txt"
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')", "{files}"]})
    try:
        server.db[rj.JOBS].insert_one({"_id": "evil", "username": JACK, "runner_id": runner_id, "check": "tests", "files": files, "status": rj.APPROVED, "approved_at": rj._now()})
        refused = until(lambda: (j := rj.get_job(server.db, JACK, "evil")) and j["status"] == rj.FAILED and j)
        assert "refused" in refused["error"].lower() and not marker.exists()
    finally:
        finish(runner, thread)


def test_one_persons_runner_never_receives_another_persons_job(server, project, tmp_path):
    marker = tmp_path / "ran.txt"
    jack_runner, jack_thread, jack_id = pair_and_start(server, project, {"tests": [PY, "-c", "print('jack')"]}, username=JACK)
    sam_runner, sam_thread, sam_id = pair_and_start(server, project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('sam ran it')"]}, username=SAM)
    try:
        job = ask(server, JACK, jack_id, "tests")
        done = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)
        assert done["output_tail"].strip() == "jack" and not marker.exists()
        assert rj.get_job(server.db, SAM, job["id"]) is None
    finally:
        finish(jack_runner, jack_thread)
        finish(sam_runner, sam_thread)


def test_the_runner_will_not_run_anything_the_person_did_not_put_in_their_own_config(server, project):
    # The checks come from the person's config in their home folder, found by the project's path; a project with no section has none, whatever the server asks.
    assert runner_config.load_checks(project) == {}


# ---- the status a real runner reports over real HTTP -------------------------------------------------------------------------------------------

def test_a_real_runner_with_nothing_to_run_tells_the_server_so_and_one_with_a_missing_program_marks_that_check(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {})
    try:
        listed = rj.runners_for(server.db, JACK)[0]
        assert listed["connected"] is True and listed["projects"][0]["status"] == "no_checks_found" and listed["projects"][0]["checks"] == []
    finally:
        finish(runner, thread)
    other = Server().start()
    try:
        runner, thread, runner_id = pair_and_start(other, project, {"tests": [PY, "-c", "print(1)"], "types": ["definitely-not-a-program-xyz", "-b"]})
        try:
            listed = rj.runners_for(other.db, JACK)[0]
            assert listed["projects"][0]["status"] == "command_missing" and {c["name"]: c["ready"] for c in listed["projects"][0]["checks"]} == {"tests": True, "types": False}
            with pytest.raises(rj.RunnerError) as error:
                ask(other, JACK, runner_id, "types")
            assert "program could not be found" in error.value.message
        finally:
            finish(runner, thread)
    finally:
        other.stop()



# ---- one runner per computer: several projects, and adding one from the page ---------------------------------------------------------------------------

class Dialog:
    """The folder dialog, answered at once with a folder the test chose (the person's click, on their own computer)."""

    def __init__(self, answer):
        self.answer = answer

    def poll(self):
        return self.answer

    def cancel(self):
        pass


def start_computer(server, tmp_path, answers, username=JACK, folder_name=""):
    """A paired computer whose runner serves whatever is in its projects.json and answers folder dialogs from `answers` ({project name: the answer the person gives})."""
    code = rj.create_pairing(server.db, username, "laptop", folder_name=folder_name)["code"]
    made = Client(server.url).pair(code)
    asked = []

    def picker(name):
        asked.append(name)
        return Dialog(answers.get(name, {"result": "declined"}))

    script = "import os; print(os.path.basename(os.getcwd()))"
    runner = Runner(Client(server.url, made["key"]), None, say=lambda line: None, poll_wait=0, idle_wait=0.05, heartbeat_every=0.2, roots_provider=runner_projects.load,
                    scanner=lambda folder: ({"where": [PY, "-c", script]}, True, ""), picker=picker, scan_every=0.3)
    assert runner.hello() is True
    thread = threading.Thread(target=runner.serve, daemon=True)
    thread.start()
    return runner, thread, made["key"].split(".")[0], asked


def project_named(server, name):
    runner = rj.runners_for(server.db, JACK)[0]
    return next((p for p in runner["projects"] if p["name"] == name), None)


def test_the_person_asks_for_a_project_by_name_chooses_its_folder_on_their_computer_and_a_check_runs_there(server, project, tmp_path):
    chosen = tmp_path / "work" / "my-app"
    chosen.mkdir(parents=True)
    runner, thread, runner_id, asked = start_computer(server, tmp_path, {"my-app": {"result": "chosen", "path": str(chosen)}})
    try:
        assert rj.runners_for(server.db, JACK)[0]["projects"] == []                                  # connected, but no folder has been allowed yet
        asked_for = rj.request_project(server.db, JACK, runner_id, "my-app")
        shown = until(lambda: project_named(server, "my-app"))
        assert asked == ["my-app"] and shown["id"] == runner_projects.project_id(chosen) and [c["name"] for c in shown["checks"]] == ["where"]
        assert until(lambda: [r for r in rj.runners_for(server.db, JACK)[0]["requests"] if r["id"] == asked_for["id"]][0]["state"] == "added")
        job = rj.create_job(server.db, JACK, runner_id, "where", None, project_id=shown["id"])
        assert rj.approve_job(server.db, JACK, job["id"])
        done = until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)
        assert done["output_tail"].strip() == "my-app" and done["project_name"] == "my-app"                # it ran in the folder that was chosen
        assert runner_projects.load() == [chosen.resolve()]
    finally:
        finish(runner, thread)


def test_saying_no_in_the_dialog_adds_nothing_and_the_page_can_tell(server, project, tmp_path):
    runner, thread, runner_id, asked = start_computer(server, tmp_path, {"my-app": {"result": "declined"}})
    try:
        asked_for = rj.request_project(server.db, JACK, runner_id, "my-app")
        assert until(lambda: [r for r in rj.runners_for(server.db, JACK)[0]["requests"] if r["id"] == asked_for["id"]][0]["state"] == "declined")
        assert rj.runners_for(server.db, JACK)[0]["projects"] == [] and runner_projects.load() == []
    finally:
        finish(runner, thread)


def test_the_folder_connected_in_chat_is_asked_for_as_soon_as_the_computer_is_set_up(server, project, tmp_path):
    chosen = tmp_path / "work" / "my-app"
    chosen.mkdir(parents=True)
    runner, thread, runner_id, asked = start_computer(server, tmp_path, {"my-app": {"result": "chosen", "path": str(chosen)}}, folder_name="my-app")
    try:
        assert until(lambda: project_named(server, "my-app")) and asked == ["my-app"]               # the setup file asked the person nothing: the window appeared by itself
    finally:
        finish(runner, thread)


def test_two_projects_on_one_computer_each_run_their_own_checks_in_their_own_folder_with_their_own_settings(server, project, tmp_path):
    one, two = tmp_path / "work" / "one", tmp_path / "work" / "two"
    one.mkdir(parents=True), two.mkdir(parents=True)
    runner_projects.add(one), runner_projects.add(two)
    runner, thread, runner_id, _ = start_computer(server, tmp_path, {})
    try:
        until(lambda: project_named(server, "one") and project_named(server, "two"))
        rj.update_settings(server.db, JACK, runner_id, {"project_id": runner_projects.project_id(two), "disabled": ["where"], "fake_keys": False})
        for name, folder in (("one", one),):
            job = rj.create_job(server.db, JACK, runner_id, "where", None, project_id=runner_projects.project_id(folder))
            assert rj.approve_job(server.db, JACK, job["id"])
            assert until(lambda: (j := rj.get_job(server.db, JACK, job["id"])) and j["status"] == rj.DONE and j)["output_tail"].strip() == name
        with pytest.raises(rj.RunnerError) as error:
            rj.create_job(server.db, JACK, runner_id, "where", None, project_id=runner_projects.project_id(two))
        assert "turned off" in error.value.message                                               # project two's setting, not project one's
        with pytest.raises(rj.RunnerError) as error:
            rj.create_job(server.db, JACK, runner_id, "where", None)
        assert error.value.code == "no_project"                                                  # two projects: it has to say which
    finally:
        finish(runner, thread)


def test_removing_a_project_in_the_page_makes_the_runner_forget_it_and_it_never_returns(server, project, tmp_path):
    one, two = tmp_path / "work" / "one", tmp_path / "work" / "two"
    one.mkdir(parents=True), two.mkdir(parents=True)
    runner_projects.add(one), runner_projects.add(two)
    runner, thread, runner_id, _ = start_computer(server, tmp_path, {})
    try:
        until(lambda: project_named(server, "one") and project_named(server, "two"))
        assert rj.request_remove_project(server.db, JACK, runner_id, runner_projects.project_id(one)) is True
        assert project_named(server, "one") is None                                               # gone from the page at once
        until(lambda: runner_projects.load() == [two.resolve()])                                   # and forgotten on the computer
        time.sleep(0.8)
        assert project_named(server, "one") is None and project_named(server, "two") is not None and (one / "..").resolve().joinpath("one").is_dir()
    finally:
        finish(runner, thread)


# ---- command lines, for real -----------------------------------------------------------------------------------------------------------------

class _Window:
    """The question on the person's computer: answers at once."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def __call__(self, line, notes):
        self.asked.append(line)
        return self

    def poll(self):
        return {"result": self.answer}

    def cancel(self):
        pass


def command_job(server, username, runner_id, line, project, confirmed=False):
    runner = server.db[rj.RUNNERS].docs[runner_id]
    job = rj.create_command_job(server.db, username, runner_id, line, runner["projects"][0]["id"])
    if job["status"] == rj.AWAITING:
        assert rj.approve_job(server.db, username, job["id"], None, confirmed)
    return job


def test_a_command_the_person_approved_runs_on_the_machine_through_the_real_shell_and_the_output_comes_back(server, project):
    (project / "README.md").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    runner, thread, runner_id = pair_and_start(server, project, {"tests": [PY, "-c", "print(1)"]})
    try:
        job = command_job(server, JACK, runner_id, "cat README.md | grep a | head -2", project)
        done = until(lambda: (lambda j: j if j and j["status"] in (rj.DONE, rj.FAILED) else None)(rj.get_job(server.db, JACK, job["id"])))
        assert done["status"] == rj.DONE and done["exit_code"] == 0 and done["output_tail"].replace("\r\n", "\n") == "alpha\nbeta\n"      # the first two lines with an "a"
        assert rj.format_result(done).startswith("LOCAL COMMAND `cat README.md | grep a | head -2` FINISHED with exit code 0")
    finally:
        finish(runner, thread)


def test_a_change_waits_for_the_window_on_the_machine_and_a_no_there_runs_nothing(server, project):
    window = _Window("declined")
    runner, thread, runner_id = pair_and_start(server, project, {}, asker=window)
    try:
        job = command_job(server, JACK, runner_id, "echo hello > made.txt", project, confirmed=True)
        done = until(lambda: (lambda j: j if j and j["status"] in (rj.DONE, rj.FAILED) else None)(rj.get_job(server.db, JACK, job["id"])))
        assert done["status"] == rj.FAILED and "You said no" in done["error"] and window.asked == ["echo hello > made.txt"] and not (project / "made.txt").exists()
    finally:
        finish(runner, thread)
    window = _Window("chosen")
    runner, thread, runner_id = pair_and_start(server, project, {}, username=SAM, asker=window)
    try:
        job = command_job(server, SAM, runner_id, "echo hello > made.txt", project, confirmed=True)
        done = until(lambda: (lambda j: j if j and j["status"] in (rj.DONE, rj.FAILED) else None)(rj.get_job(server.db, SAM, job["id"])))
        assert done["status"] == rj.DONE and done["exit_code"] == 0 and (project / "made.txt").read_text(encoding="utf-8").strip() == "hello"
    finally:
        finish(runner, thread)


def test_a_server_that_lies_about_a_commands_class_or_sends_a_refused_line_gets_a_refusal_from_the_runner(server, project):
    runner, thread, runner_id = pair_and_start(server, project, {})
    try:
        for line, klass in (("echo x > sneaky.txt", "read"), ("rm -rf .", "read"), ("cat .env", "read")):
            doc = server.db[rj.JOBS]
            job_id = f"forged{abs(hash(line))}"
            doc.insert_one({"_id": job_id, "username": JACK, "runner_id": runner_id, "project_id": runner._introduction()[1][0]["id"], "kind": "command", "check": "command", "command_shown": line,
                            "command_class": klass, "files": [], "status": rj.APPROVED, "approved_at": rj._now(), "created_at": rj._now()})
            done = until(lambda: (lambda j: j if j and j["status"] in (rj.DONE, rj.FAILED) else None)(rj.get_job(server.db, JACK, job_id)))
            assert done["status"] == rj.FAILED and "refused" in done["error"].lower(), line
        assert not (project / "sneaky.txt").exists()
    finally:
        finish(runner, thread)
