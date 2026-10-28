"""Updating the runner (update.py): only to a strictly newer version NUMBER the server names, only from where the runner itself was installed, only when idle, and a failure leaves the old one running.

The decisions are checked with a recording stand-in for pip. The real thing is also run: a throwaway environment gets an older runner installed from a local archive, then `apply` upgrades it with the
real pip and the real check that the new version is what a fresh process finds."""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from saapp_runner import __version__, cli, update
from saapp_runner import config as cfg
from saapp_runner.client import Client, ServerError
from saapp_runner.loop import Runner

REPO_ZIP = "https://github.com/someone/their-saapp/archive/refs/tags/runner-v0.1.1.zip"


def direct_url(url=REPO_ZIP, subdirectory="runner"):
    return json.dumps({"url": url, "subdirectory": subdirectory, "archive_info": {}})


def newer(step=1):
    major, minor, patch = update.parse_version(__version__)
    return f"{major}.{minor}.{patch + step}"


# ---- which version is accepted ------------------------------------------------------------------------------------------------------------------

def test_only_a_plain_strictly_newer_version_is_accepted():
    assert update.accept("0.1.1", "0.1.2") == "0.1.2" and update.accept("0.1.1", " 0.2.0 ") == "0.2.0" and update.accept("0.1.9", "0.1.10") == "0.1.10"
    for refused in ("0.1.1", "0.1.0", "0.0.9", "", None, "latest", "0.1", "1", "0.1.2.3", "0.1.2-beta", "0.1.2; rm -rf /", "0.1.2.zip", "../9.9.9", "https://evil.example.com/x.zip", 7, ["0.1.2"]):
        assert update.accept("0.1.1", refused) is None, refused
    assert update.accept("junk", "0.1.2") is None


# ---- where it installs from ---------------------------------------------------------------------------------------------------------------------

def test_the_source_is_read_from_where_pip_installed_the_runner_and_only_a_github_release_archive_counts(monkeypatch):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    assert update.source_repo() == ("someone", "their-saapp")
    assert update.archive_url("someone", "their-saapp", "0.1.2") == "https://github.com/someone/their-saapp/archive/refs/tags/runner-v0.1.2.zip#subdirectory=runner"
    for bad in (direct_url("http://github.com/a/b/archive/refs/tags/runner-v0.1.1.zip"), direct_url("https://evil.example.com/a/b/archive/refs/tags/runner-v0.1.1.zip"),
                direct_url("https://github.com.evil.example.com/a/b/archive/refs/tags/runner-v0.1.1.zip"), direct_url("https://github.com/a/b/archive/refs/heads/main.zip"),
                direct_url("https://github.com/a/b/archive/refs/tags/runner-v0.1.1.zip?x=1"), direct_url("https://github.com/a/b/c/archive/refs/tags/runner-v0.1.1.zip"),
                direct_url("file:///tmp/runner.zip"), direct_url(REPO_ZIP, subdirectory="other"), direct_url(REPO_ZIP, subdirectory=""), "not json", "[]", "null", ""):
        monkeypatch.setattr(update, "_direct_url_text", lambda bad=bad: bad)
        assert update.source_repo() is None, bad
    monkeypatch.setattr(update, "_direct_url_text", lambda: None)
    assert update.source_repo() is None                                                        # a source checkout or an editable install


class Pip:
    """A stand-in for pip: remembers the commands and answers them."""

    def __init__(self, code=0, output="", installed=None):
        self.commands, self.code, self.output, self.installed = [], code, output, installed

    def run(self, command):
        self.commands.append(list(command))
        return self.code, self.output

    def verify(self):
        return self.installed if self.installed is not None else ""


def test_the_address_comes_from_the_runners_own_source_never_from_what_the_server_sent(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    pip = Pip(installed=newer())
    done, message = update.apply(newer(), run=pip.run, verify=pip.verify)
    assert done and message == f"Updated {__version__} -> {newer()}."
    command = pip.commands[0]
    assert command[:5] == [sys.executable, "-m", "pip", "install", "--upgrade"] and command[-1] == f"https://github.com/someone/their-saapp/archive/refs/tags/runner-v{newer()}.zip#subdirectory=runner"
    # what the server sent is a number and that is all there is: text shaped like an address is not a version, so nothing is installed
    pip = Pip()
    assert update.apply("https://evil.example.com/x.zip", run=pip.run, verify=pip.verify)[0] is False and pip.commands == []


def test_a_runner_that_was_not_installed_from_a_release_archive_does_not_update_itself_and_says_how_to(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: None)
    pip = Pip()
    done, message = update.apply(newer(), run=pip.run, verify=pip.verify)
    assert done is False and "Run the setup file again" in message and pip.commands == []


def test_an_offer_that_is_not_newer_installs_nothing(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    pip = Pip()
    for offered in (__version__, "0.0.1", "garbage"):
        assert update.apply(offered, run=pip.run, verify=pip.verify) == (False, "Not updating: the offered version is not newer than this one.")
    assert pip.commands == []


def test_a_failed_install_is_said_remembered_and_leaves_the_old_runner_running(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    pip = Pip(code=1, output="Collecting\nERROR: could not find a version\n")
    done, message = update.apply(newer(), run=pip.run, verify=pip.verify)
    assert done is False and "did not install" in message and "could not find a version" in message and "keeps running" in message
    assert update.failed_recently(newer()) is True and update.failed_recently("9.9.9") is False


def test_an_install_that_reports_the_wrong_version_is_put_back(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    pip = Pip(installed="0.0.1")
    done, message = update.apply(newer(), run=pip.run, verify=pip.verify)
    assert done is False and "was put back" in message
    assert len(pip.commands) == 2 and pip.commands[1][-1].endswith(f"runner-v{__version__}.zip#subdirectory=runner")             # the old version's archive from the same place
    assert update.failed_recently() is True


def test_a_failure_is_not_retried_for_a_few_hours_and_a_replaced_runner_is_not_blamed(monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    update.apply(newer(), run=Pip(code=1).run, verify=Pip().verify)
    pip = Pip(installed=newer())
    assert update.attempt(newer(), run=pip.run, verify=pip.verify) == (False, "") and pip.commands == []                      # quietly: it already failed
    soon = time.time() + 5 * 3600
    later = time.time() + 7 * 3600
    assert update.failed_recently(newer(), soon) is True and update.failed_recently(newer(), later) is False
    monkeypatch.setattr(update, "__version__", "9.9.9")
    assert update.failed_recently(newer()) is False                                              # the runner that failed is no longer the one running
    monkeypatch.setattr(update, "__version__", __version__)
    cfg.home().joinpath("update.json").write_text(json.dumps({"version": newer(), "from": __version__, "ok": True, "at": time.time()}), encoding="utf-8")
    assert update.failed_recently() is False


# ---- the loop -----------------------------------------------------------------------------------------------------------------------------------

class Client2:
    """A stand-in for the server's poll: hands out an update offer (and jobs) in the order given."""

    def __init__(self, polls):
        self.polls, self.update_to, self.hellos, self.statuses = list(polls), None, [], []

    def hello(self, projects, status="", launch="", request_results=None):
        self.hellos.append(projects)
        self.statuses.append(status)
        return {"ok": True}

    def next_job(self, wait=25):
        if not self.polls:
            raise ServerError(401, "stop")
        job, self.update_to = self.polls.pop(0)
        return job


def make(project, client, updater, launch="hand"):
    said = []
    runner = Runner(client, project, {"tests": [sys.executable, "-c", "print(1)"]}, say=said.append, poll_wait=0, idle_wait=0.01, heartbeat_every=0.2, launch=launch, updater=updater)
    return runner, said


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    return root


def test_an_update_offered_while_idle_is_installed_and_a_runner_started_at_login_exits_so_the_system_starts_the_new_one(project):
    calls = []
    runner, said = make(project, Client2([(None, newer())]), lambda version: (calls.append(version), (True, f"Updated {__version__} -> {version}."))[1], launch="service")
    runner.serve()
    assert calls == [newer()] and runner.exit_code == update.EXIT_RESTART == 75 and runner.stop.is_set()
    assert any("Restarting to use the new version" in line for line in said)


def test_a_runner_started_by_hand_stops_cleanly_and_says_to_start_it_again(project):
    runner, said = make(project, Client2([(None, newer())]), lambda version: (True, "Updated."), launch="hand")
    runner.serve()
    assert runner.exit_code == 0 and any("Start the runner again to use the new version." in line for line in said)


def test_a_failed_update_keeps_the_runner_running_says_why_and_tells_the_server(project, runner_home):
    client = Client2([(None, newer()), (None, None)])
    runner, said = make(project, client, lambda version: (False, "The update did not install (pip exit code 1)."))
    runner.hello()
    runner.serve()
    assert not runner.stop.is_set() and runner.exit_code == 0 and any("did not install" in line for line in said)
    assert len(client.hellos) == 2                                                          # it said hello again so the page can say the update failed


def test_a_quiet_refusal_says_nothing_and_does_not_stop(project):
    runner, said = make(project, Client2([(None, newer()), (None, None)]), lambda version: (False, ""))
    runner.serve()
    assert not runner.stop.is_set() and not any("update" in line.lower() for line in said)


def test_nothing_happens_when_the_runner_has_no_updater_and_a_waiting_job_is_run_before_any_update(project):
    runner, _ = make(project, Client2([(None, newer()), (None, None)]), None)
    runner.serve()
    assert not runner.stop.is_set()
    seen = []
    ran = []
    client = Client2([({"job_id": "j1", "check": "tests", "files": []}, newer()), (None, None)])
    client.heartbeat = lambda job_id: {}
    client.result = lambda job_id, payload: ran.append(job_id) or {}
    runner, _ = make(project, client, lambda version: (seen.append(version), (False, ""))[1])
    runner.serve()
    assert ran == ["j1"] and seen == []                                                     # the job came first; an offer that arrived with it is not acted on in the same poll


def test_the_status_says_update_failed_after_a_failure_and_clears_after_the_runner_is_replaced(project, monkeypatch, runner_home):
    monkeypatch.setattr(update, "_direct_url_text", lambda: direct_url())
    update.apply(newer(), run=Pip(code=1).run, verify=Pip().verify)
    client = Client2([])
    runner, _ = make(project, client, None)
    runner.hello()
    assert client.statuses == ["update_failed"]                                                  # the runner's own status, not a project's
    monkeypatch.setattr(update, "__version__", "9.9.9")
    runner.hello()
    assert client.statuses[-1] == ""


def test_the_client_keeps_only_a_version_string_from_the_poll(fake):
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": None, "update": "0.1.2"})
    client = Client(fake.url, "abc.def")
    assert client.next_job(0) is None and client.update_to == "0.1.2"
    for odd in ({"job": None, "update": None}, {"job": None}, {"job": None, "update": {"url": "https://evil.example.com/x.zip"}}, {"job": None, "update": ["0.1.2"]}):
        fake.routes[("GET", "/api/runner/next")] = (200, odd)
        client.next_job(0)
        assert client.update_to is None, odd


def test_a_background_runner_exits_with_the_restart_code_after_an_update_and_a_hand_started_one_with_zero(fake, project, runner_home, monkeypatch):
    cfg.save_credentials(fake.url, "abc.def")
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": None, "update": newer()})
    monkeypatch.setattr(update, "attempt", lambda offered, **kw: (True, f"Updated {__version__} -> {offered}."))
    assert cli.cmd_start(argparse.Namespace(root=str(project), timeout=5, service=True), stop=threading.Event()) == update.EXIT_RESTART
    assert cli.cmd_start(argparse.Namespace(root=None, timeout=5), say=lambda line: None, stop=threading.Event()) == 0


# ---- the real pip ------------------------------------------------------------------------------------------------------------------------------

def _archive(work, version):
    """A runner archive shaped like GitHub's, built from this checkout with the version changed."""
    repo = Path(__file__).resolve().parents[2]
    out = work / f"runner-{version}.zip"
    prefix = f"SAAPP-runner-v{version}/"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(prefix + "README.md", "readme\n")
        for path in sorted((repo / "runner").rglob("*")):
            if not path.is_file() or any(bad in str(path) for bad in ("__pycache__", ".pytest_cache", "egg-info")):
                continue
            rel = path.relative_to(repo / "runner").as_posix()
            data = path.read_bytes()
            if rel in ("pyproject.toml", "src/saapp_runner/__init__.py"):
                data = re.sub(rb'(version = |__version__ = )"[^"]+"', rb'\1"' + version.encode() + rb'"', data)
            z.writestr(prefix + "runner/" + rel, data)
    return out.as_uri() + "#subdirectory=runner"


def test_the_real_pip_upgrades_an_installed_runner_and_a_failing_url_changes_nothing(tmp_path):
    old, new = _archive(tmp_path, "0.1.1"), _archive(tmp_path, "0.1.2")
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    assert subprocess.run([str(python), "-m", "pip", "install", old], capture_output=True, text=True).returncode == 0
    home = tmp_path / "home"

    def inside(code):
        env = {"SAAPP_RUNNER_HOME": str(home), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": __import__("os").environ.get("PATH", "")}
        done = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, env=env, timeout=600)
        return done.stdout.strip()

    assert inside("import saapp_runner; print(saapp_runner.__version__)") == "0.1.1"
    # a url that cannot be fetched: pip fails, the old version stays, and the failure is remembered
    assert inside(f"from saapp_runner import update; print(update.apply('0.1.2', url_for=lambda v: 'file:///definitely/not/here.zip#subdirectory=runner'))").startswith('(False, "The update to 0.1.2 did not install')
    assert inside("import saapp_runner; print(saapp_runner.__version__)") == "0.1.1"
    assert json.loads((home / "update.json").read_text(encoding="utf-8"))["ok"] is False
    # the real archive: installed, and a fresh process reports the new version
    assert inside(f"from saapp_runner import update; print(update.apply('0.1.2', url_for=lambda v: {new!r}))") == "(True, 'Updated 0.1.1 -> 0.1.2.')"
    assert inside("import saapp_runner; print(saapp_runner.__version__)") == "0.1.2"
    assert json.loads((home / "update.json").read_text(encoding="utf-8"))["ok"] is True
