"""The runner talking to the server (client.py), its main loop (loop.py) and its commands (cli.py)."""
import argparse
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from saapp_runner import cli, config as cfg, presets
from saapp_runner.client import Client, ServerError, Unreachable, check_address
from saapp_runner.loop import Runner

PY = sys.executable


# ---- a small server to talk to ----------------------------------------------------------------------------------------------------------------

class Fake:
    """Answers each (method, path) with a (status, body) and remembers what it was sent."""

    def __init__(self):
        self.routes, self.seen = {}, []

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else None
                outer.seen.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"), "agent": self.headers.get("User-Agent"), "body": body})
                status, payload = outer.routes.get((self.command, self.path.split("?")[0]), (404, {"detail": "no route"}))
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _answer

            def log_message(self, *args):
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake():
    server = Fake().start()
    yield server
    server.stop()


# ---- the client ------------------------------------------------------------------------------------------------------------------------------

def test_the_key_is_never_sent_over_plain_http_except_to_this_machine():
    assert check_address("https://saapp.example.com/") == "https://saapp.example.com"
    assert check_address("http://127.0.0.1:8000") == "http://127.0.0.1:8000" and check_address("http://localhost:8000/") == "http://localhost:8000"
    for bad in ("http://saapp.example.com", "ftp://x.example.com", "saapp.example.com", "", "https://", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            check_address(bad)


def test_pairing_trades_a_code_for_a_key_without_any_credentials(fake):
    fake.routes[("POST", "/api/runner/pair")] = (200, {"key": "abc.def", "name": "laptop"})
    made = Client(fake.url).pair("ABCDE-FGHJK")
    assert made == {"key": "abc.def", "name": "laptop"} and fake.seen[0]["auth"] is None and fake.seen[0]["body"] == {"code": "ABCDE-FGHJK"}


def test_every_other_call_carries_the_key_and_the_version_and_names_the_checks(fake):
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    Client(fake.url, "abc.def").hello("myapp", [{"name": "tests", "command": "pytest"}])
    sent = fake.seen[0]
    assert sent["auth"] == "Bearer abc.def" and sent["agent"].startswith("saapp-runner/") and sent["body"]["root_name"] == "myapp" and sent["body"]["version"] == "0.1.0"
    assert sent["body"]["checks"] == [{"name": "tests", "command": "pytest"}]


def test_the_next_job_is_the_job_or_none(fake):
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": {"job_id": "j1", "check": "tests", "files": []}})
    assert Client(fake.url, "k.k").next_job(0)["job_id"] == "j1"
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": None})
    assert Client(fake.url, "k.k").next_job(0) is None


def test_a_server_error_keeps_the_servers_own_words_and_status(fake):
    fake.routes[("POST", "/api/runner/pair")] = (401, {"detail": "That code is wrong."})
    with pytest.raises(ServerError) as error:
        Client(fake.url).pair("x")
    assert error.value.status == 401 and error.value.message == "That code is wrong."
    fake.routes[("POST", "/api/runner/hello")] = (500, b"<html>oops</html>")
    with pytest.raises(ServerError) as error:
        Client(fake.url, "k.k").hello("x", [])
    assert error.value.status == 500


def test_an_answer_that_is_not_json_is_an_error_not_a_crash(fake):
    fake.routes[("POST", "/api/runner/hello")] = (200, b"not json at all")
    with pytest.raises(ServerError) as error:
        Client(fake.url, "k.k").hello("x", [])
    assert error.value.status == 502


def test_a_server_that_cannot_be_reached_is_reported_as_unreachable():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(Unreachable):
        Client(f"http://127.0.0.1:{port}", "k.k").hello("x", [])


# ---- the loop --------------------------------------------------------------------------------------------------------------------------------

class FakeClient:
    """Stands in for the server: hands out the jobs it was given, records what was reported, and can be told to cancel or to fail."""

    def __init__(self, jobs=(), cancel_after=None, next_errors=()):
        self.jobs, self.reported, self.cancel_after, self.heartbeats, self.next_errors, self.hellos = list(jobs), [], cancel_after, 0, list(next_errors), []

    def hello(self, root_name, checks):
        self.hellos.append((root_name, checks))
        return {"ok": True}

    def next_job(self, wait=25):
        if self.next_errors:
            raise self.next_errors.pop(0)
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job_id):
        self.heartbeats += 1
        return {"cancel": self.cancel_after is not None and self.heartbeats >= self.cancel_after}

    def result(self, job_id, payload):
        self.reported.append((job_id, payload))
        return {"ok": True}


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "myapp"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_ok.py").write_text("print('file ran')\n", encoding="utf-8")
    return root


def make_runner(client, project, checks, **kwargs):
    said = []
    runner = Runner(client, project, checks, say=said.append, poll_wait=0, heartbeat_every=0.2, **kwargs)
    return runner, said


def test_hello_introduces_the_folder_and_the_checks_with_their_command_text(project):
    client = FakeClient()
    runner, _ = make_runner(client, project, {"tests": [PY, "-c", "print(1)"], "types": ["npx", "tsc", "-b"]})
    assert runner.hello() is True
    root_name, checks = client.hellos[0]
    assert root_name == "myapp" and [c["name"] for c in checks] == ["tests", "types"] and checks[1]["command"] == "npx tsc -b"


def test_an_approved_job_runs_the_command_the_person_defined_and_reports_what_happened(project):
    client = FakeClient([{"job_id": "j1", "check": "tests", "files": []}])
    runner, said = make_runner(client, project, {"tests": [PY, "-c", "print('all good')"]})
    runner.handle({"job_id": "j1", "check": "tests", "files": []})
    job_id, report = client.reported[0]
    assert job_id == "j1" and report["exit_code"] == 0 and report["output_tail"].strip() == "all good" and report["timed_out"] is False
    assert any("Running tests" in line for line in said)


def test_files_are_checked_again_here_and_passed_to_the_command_as_separate_safe_words(project):
    script = "import sys; print(sys.argv[1:])"
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", script, "{files}"]})
    runner.handle({"job_id": "j1", "check": "tests", "files": ["tests/test_ok.py"]})
    assert runner.client.reported[0][1]["output_tail"].strip() == "['./tests/test_ok.py']"


def test_a_check_the_person_never_defined_is_refused_and_nothing_runs(project, tmp_path):
    marker = tmp_path / "ran.txt"
    runner, said = make_runner(FakeClient(), project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')"]})
    runner.handle({"job_id": "j1", "check": "deploy-to-production", "files": []})
    job_id, report = runner.client.reported[0]
    assert report["exit_code"] is None and "no check called" in report["error"] and not marker.exists() and any("Refused" in line for line in said)


@pytest.mark.parametrize("files", [["../outside.py"], ["/etc/passwd"], ["missing.py"], ["--rootdir=/"], ["tests"]])
def test_files_that_escape_or_do_not_exist_are_refused_without_running_anything(project, tmp_path, files):
    marker = tmp_path / "ran.txt"
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')", "{files}"]})
    runner.handle({"job_id": "j1", "check": "tests", "files": files})
    report = runner.client.reported[0][1]
    assert report["exit_code"] is None and "refused" in report["error"].lower() and not marker.exists()


def test_files_are_not_passed_to_a_program_that_runs_through_cmd_exe(project, tmp_path, monkeypatch):
    from saapp_runner import execute

    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(execute, "safe_for_files", lambda words, windows=None: False)
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", f"open({str(marker)!r}, 'w').write('x')", "{files}"]})
    runner.handle({"job_id": "j1", "check": "tests", "files": ["tests/test_ok.py"]})
    report = runner.client.reported[0][1]
    assert report["exit_code"] is None and "cmd.exe" in report["error"] and not marker.exists()


def test_files_given_to_a_check_that_takes_none_are_refused(project):
    runner, _ = make_runner(FakeClient(), project, {"types": [PY, "-c", "print(1)"]})
    runner.handle({"job_id": "j1", "check": "types", "files": ["tests/test_ok.py"]})
    assert "does not take files" in runner.client.reported[0][1]["error"]


def test_a_failing_command_is_reported_with_its_exit_code(project):
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", "import sys; print('bad'); sys.exit(4)"]})
    runner.handle({"job_id": "j1", "check": "tests", "files": []})
    assert runner.client.reported[0][1]["exit_code"] == 4


def test_a_job_cancelled_in_saapp_is_stopped_at_the_next_heartbeat(project):
    client = FakeClient(cancel_after=1)
    runner, said = make_runner(client, project, {"tests": [PY, "-c", "import time; time.sleep(60)"]}, timeout=60)
    runner.handle({"job_id": "j1", "check": "tests", "files": []})
    report = client.reported[0][1]
    assert report["exit_code"] is None and client.heartbeats >= 1 and any("cancelled" in line for line in said) and report["duration"] < 30


def test_a_job_without_an_id_is_ignored(project):
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", "print(1)"]})
    runner.handle({"check": "tests"})
    assert runner.client.reported == []


def test_serving_runs_each_job_then_stops_when_the_key_is_revoked(project):
    client = FakeClient([{"job_id": "j1", "check": "tests", "files": []}, {"job_id": "j2", "check": "tests", "files": []}], next_errors=[])
    runner, said = make_runner(client, project, {"tests": [PY, "-c", "print('hi')"]})
    original = client.next_job

    def next_job(wait=25):
        if not client.jobs:
            raise ServerError(401, "That runner was removed.")
        return original(wait)

    client.next_job = next_job
    runner.serve()
    assert [r[0] for r in client.reported] == ["j1", "j2"] and any("Stopping: That runner was removed." in line for line in said)


def test_serving_waits_out_a_server_that_cannot_be_reached_and_keeps_going(project):
    client = FakeClient([{"job_id": "j1", "check": "tests", "files": []}], next_errors=[Unreachable("could not reach it")])
    runner, said = make_runner(client, project, {"tests": [PY, "-c", "print('hi')"]})
    runner.stop = threading.Event()
    stopper = threading.Timer(6.0, runner.stop.set)
    stopper.start()
    runner.stop.wait = lambda timeout=None: False                                              # do not really wait out the retry delay
    original = client.next_job

    def next_job(wait=25):
        if client.reported:
            runner.stop.set()
            return None
        return original(wait)

    client.next_job = next_job
    runner.serve()
    stopper.cancel()
    assert client.reported and any("trying again" in line for line in said)


def test_hello_says_why_when_the_server_turns_the_runner_away(project):
    class Refusing(FakeClient):
        def hello(self, root_name, checks):
            raise ServerError(426, "Update the runner.")

    runner, said = make_runner(Refusing(), project, {"tests": [PY, "-c", "print(1)"]})
    assert runner.hello() is False and any("Update the runner." in line for line in said)


# ---- the commands ----------------------------------------------------------------------------------------------------------------------------

def test_init_suggests_checks_from_the_projects_files_and_never_runs_anything(tmp_path):
    root = tmp_path / "proj"
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}), encoding="utf-8")
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    found = cli.detect_checks(root)
    assert found["tests"][1:] == ["-m", "pytest", "-q", "-p", "no:cacheprovider", "{files}"] and found["tests"][0].endswith("python.exe")
    assert found["js-tests"] == ["npm", "test"] and found["types"] == ["npx", "tsc", "-b"]
    assert cli.detect_checks(tmp_path / "empty") == {}


def test_init_with_yes_writes_the_checks_and_will_not_overwrite_them(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    assert cli.main(["init", "--root", str(root), "--yes"]) == 0
    assert "tests" in cfg.load_checks(root)
    assert cli.main(["init", "--root", str(root), "--yes"]) == 1 and "already has a section" in capsys.readouterr().out


def test_init_asks_before_adding_each_check(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    answers = iter(["n", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["init", "--root", str(root)]) == 0
    assert list(cfg.load_checks(root)) == ["types"]


def test_pair_saves_the_key_for_that_project_and_says_where(fake, capsys, runner_home, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    fake.routes[("POST", "/api/runner/pair")] = (200, {"key": "abc.def", "name": "laptop"})
    assert cli.main(["pair", "--server", fake.url, "--root", str(root), "ABCDEFGHJK"]) == 0
    assert cfg.load_credentials(root) == {"server": fake.url, "key": "abc.def"} and "Paired" in capsys.readouterr().out
    other = tmp_path / "other"
    other.mkdir()
    assert cfg.load_credentials(other) is None                                                          # another project is still unpaired


def test_pair_says_what_went_wrong_and_saves_nothing(fake, capsys, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    fake.routes[("POST", "/api/runner/pair")] = (401, {"detail": "That code is wrong, already used, or expired."})
    assert cli.main(["pair", "--server", fake.url, "--root", str(root), "WRONGWRONG"]) == 1
    assert "wrong, already used, or expired" in capsys.readouterr().out and cfg.load_credentials(root) is None
    assert cli.main(["pair", "--server", "http://remote.example.com", "--root", str(root), "ABCDEFGHJK"]) == 1      # plain http to another machine
    assert cli.main(["pair", "--server", fake.url, "--root", str(tmp_path / "not-a-folder"), "ABCDEFGHJK"]) == 1
    assert "is not a folder" in capsys.readouterr().out


def test_start_will_not_begin_unpaired_or_when_the_project_has_nothing_to_run(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    assert cli.main(["start", "--root", str(root)]) == 1 and "not paired" in capsys.readouterr().out
    cfg.save_credentials("https://saapp.example.com", "abc.def", root)
    assert cli.main(["start", "--root", str(root)]) == 1 and "Found no tests or type check" in capsys.readouterr().out


# ---- checks found by the runner itself, and the settings chosen in the page ------------------------------------------------------------------------

def test_a_venv_called_venv_is_used_and_no_venv_means_a_plain_pytest(tmp_path):
    root = tmp_path / "proj"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "venv" / "bin" / "python").write_text("", encoding="utf-8")
    (root / "pytest.ini").write_text("", encoding="utf-8")
    assert presets.detect(root)["tests"][:3] == [str(root / "venv" / "bin" / "python"), "-m", "pytest"]
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "pytest.ini").write_text("", encoding="utf-8")
    assert presets.detect(bare)["tests"] == ["pytest", "-q", "-p", "no:cacheprovider", "{files}"]


def test_a_package_json_without_a_test_script_or_with_junk_adds_no_check(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    for text in ('{"scripts": {"build": "vite build"}}', "not json", "[1, 2]", '{"scripts": "oops"}'):
        (root / "package.json").write_text(text, encoding="utf-8")
        assert "js-tests" not in presets.detect(root)


def test_a_check_written_in_the_config_replaces_the_preset_of_the_same_name(tmp_path, runner_home):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert presets.effective_checks(root)["tests"][-1] == "{files}"
    cfg.append_project(root, {"tests": ["mytest", "--fast"], "lint": ["mylint"]})
    checks = presets.effective_checks(root)
    assert checks["tests"] == ["mytest", "--fast"] and checks["lint"] == ["mylint"] and checks["types"] == ["npx", "tsc", "-b"]


def test_start_needs_no_init_because_the_runner_finds_the_checks_itself(fake, tmp_path, runner_home):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    cfg.save_credentials(fake.url, "abc.def", root)
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (401, {"detail": "stop"})                 # the first poll ends the run
    said = []
    stop = threading.Event()
    assert cli.cmd_start(argparse.Namespace(root=str(root), timeout=5), say=said.append, stop=stop) == 0
    hello = next(call for call in fake.seen if call["path"].endswith("/hello"))
    assert [c["name"] for c in hello["body"]["checks"]] == ["tests"] and any("can run: tests" in line for line in said)


def test_fake_ai_keys_are_set_only_when_the_job_says_yes_and_hide_a_real_one(project, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "real-secret-key")
    script = "import os; print([os.environ.get(k) for k in ('GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY')])"
    runner, said = make_runner(FakeClient(), project, {"tests": [PY, "-c", script]})
    runner.handle({"job_id": "j1", "check": "tests", "files": [], "settings": {"fake_keys": True}})
    runner.handle({"job_id": "j2", "check": "tests", "files": [], "settings": {"fake_keys": False}})
    runner.handle({"job_id": "j3", "check": "tests", "files": []})
    runner.handle({"job_id": "j4", "check": "tests", "files": [], "settings": {"fake_keys": "yes"}})              # only a real True counts
    outputs = {job: report["output_tail"].strip() for job, report in runner.client.reported}
    assert outputs["j1"] == "['test-not-real', 'test-not-real', 'test-not-real', 'test-not-real']"
    assert outputs["j2"] == outputs["j3"] == outputs["j4"] == "[None, None, None, None]"                          # the shell's real key never reaches a check
    assert any("with fake AI keys" in line for line in said) and "real-secret-key" not in " ".join(outputs.values()) + " ".join(said)


def test_a_fake_key_beats_a_dotenv_file_in_the_project(project, tmp_path):
    (project / ".env").write_text("GOOGLE_API_KEY=real-from-dotenv\n", encoding="utf-8")
    script = ("import os, pathlib\n"
              "for line in pathlib.Path('.env').read_text().splitlines():\n"
              "    key, _, value = line.partition('=')\n"
              "    os.environ.setdefault(key, value)               # what python-dotenv does by default: it never replaces a variable that is already set\n"
              "print(os.environ['GOOGLE_API_KEY'])")
    runner, _ = make_runner(FakeClient(), project, {"tests": [PY, "-c", script]})
    runner.handle({"job_id": "j1", "check": "tests", "files": [], "settings": {"fake_keys": True}})
    runner.handle({"job_id": "j2", "check": "tests", "files": [], "settings": {"fake_keys": False}})
    outputs = {job: report["output_tail"].strip() for job, report in runner.client.reported}
    assert outputs == {"j1": "test-not-real", "j2": "real-from-dotenv"}
