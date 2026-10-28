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
