"""The runner's handling of the three jobs that keep a dev server running and look at a page on this machine (serve, stop, look): the decisions are made here, whatever the server sends."""
import json
import sys

import pytest

from saapp_runner import projects
from saapp_runner.loop import Runner

NO_PNG = {"ok": True, "title": "My app", "final_url": "http://localhost:4321/", "text": "hello", "console": [], "failed": [], "blocked": [], "viewport": [390, 844], "scroll_width": 390,
          "overflow_x": False, "wide_elements": [], "screenshot_b64": "QUJD", "screenshot_bytes": 3, "device": "phone", "browser": "edge", "duration": 1.2}


class FakeClient:
    def __init__(self, jobs=()):
        self.jobs, self.reported, self.hellos, self.browsers, self.heartbeats = list(jobs), [], [], [], 0

    def hello(self, projects, status="", launch="", request_results=None, browser=""):
        self.hellos.append(projects)
        self.browsers.append(browser)
        return {"ok": True}

    def next_job(self, wait=25):
        return self.jobs.pop(0) if self.jobs else None

    def heartbeat(self, job_id, output=""):
        self.heartbeats += 1
        return {}

    def result(self, job_id, payload):
        self.reported.append((job_id, payload))
        return {"ok": True}


class StubServers:
    """Stands in for ServerManager: no process is started."""

    def __init__(self, port=None, outcome=None):
        self.port, self.outcome, self.started, self.stopped, self.touched, self.all_stopped = port, outcome, [], [], [], False

    def start(self, project_id, root, name, words, env=None, cancel=None):
        self.started.append({"project": project_id, "name": name, "words": list(words), "env": env})
        if self.outcome:
            return self.outcome
        self.port = 4321
        return {"status": "running", "name": name, "port": 4321, "exposed": False, "output_tail": "Local: http://localhost:4321/"}

    def port_of(self, project_id):
        return self.port

    def touch(self, project_id):
        self.touched.append(project_id)

    def stop(self, project_id):
        self.stopped.append(project_id)
        had, self.port = self.port is not None, None
        return had

    def stop_all(self):
        self.all_stopped = True

    def sweep(self):
        return []


class Looker:
    def __init__(self, result=None):
        self.calls, self.result = [], result or dict(NO_PNG)

    def __call__(self, url, device, browser, cancel=None, port=None):
        self.calls.append({"url": url, "device": device, "browser": browser, "port": port})
        return dict(self.result)


@pytest.fixture()
def app(tmp_path):
    root = tmp_path / "myapp"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}), encoding="utf-8")
    return root


def make(client, app, servers=None, looker=None, **kwargs):
    said = []
    runner = Runner(client, app, {"tests": [sys.executable, "-c", "print(1)"]}, say=said.append, poll_wait=0, heartbeat_every=0.2, server_manager=servers or StubServers(), looker=looker or Looker(), **kwargs)
    return runner, said


# ---- the hello -------------------------------------------------------------------------------------------------------------------------------

def test_hello_lists_the_servers_a_project_can_keep_running_and_the_browser_it_found(app):
    client = FakeClient()
    runner, _ = make(client, app, browser_finder=lambda: {"name": "edge", "path": "C:\\secret\\place\\msedge.exe"})
    assert runner.hello() is True
    (item,) = client.hellos[0]
    assert item["servers"] == [{"name": "dev", "command": "npm run dev"}]
    assert client.browsers == ["edge"], "only the browser's name is sent, never where it is installed"
    assert "secret" not in json.dumps(client.hellos)


def test_a_project_with_no_server_script_and_a_computer_with_no_browser_say_nothing_extra(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    client = FakeClient()
    runner, _ = make(client, root)
    runner.hello()
    assert "servers" not in client.hellos[0][0] and client.browsers == [""]


# ---- serve and stop --------------------------------------------------------------------------------------------------------------------------

def test_a_serve_job_starts_the_projects_own_server_by_name_and_reports_the_port(app):
    client, servers = FakeClient(), StubServers()
    runner, said = make(client, app, servers)
    runner.handle({"job_id": "j1", "kind": "serve", "server": "dev"})
    (job_id, report), = client.reported
    assert job_id == "j1" and report["exit_code"] == 0 and report["server"] == {"status": "running", "name": "dev", "port": 4321, "exposed": False}
    assert servers.started[0]["words"] == ["npm", "run", "dev"] and servers.started[0]["env"] is None
    assert any("running on port 4321" in line for line in said)


def test_the_server_cannot_make_the_runner_start_anything_the_project_does_not_offer(app):
    client, servers = FakeClient(), StubServers()
    runner, _ = make(client, app, servers)
    for name in ("build", "rm -rf /", "", "start", "dev; calc"):
        runner.handle({"job_id": "j", "kind": "serve", "server": name, "line": "calc.exe", "command": ["calc"]})
    assert servers.started == [] and all(report["exit_code"] is None and "has no server called" in report["error"] for _, report in client.reported)


def test_fake_keys_are_passed_to_the_server_only_as_a_yes(app):
    for settings, expect in (({"fake_keys": True}, True), ({"fake_keys": "yes"}, False), ({}, False), (None, False)):
        servers = StubServers()
        runner, _ = make(FakeClient(), app, servers)
        runner.handle({"job_id": "j", "kind": "serve", "server": "dev", "settings": settings})
        assert (servers.started[0]["env"] is not None) is expect
        if expect:
            assert set(servers.started[0]["env"].values()) == {"test-not-real"}


@pytest.mark.parametrize("outcome, word", [
    ({"status": "exited", "exit_code": 1, "output_tail": "Error: EADDRINUSE"}, "stopped by itself"),
    ({"status": "no_port", "output_tail": "starting..."}, "never said which port"),
    ({"status": "cancelled"}, "Stopped before it was ready"),
])
def test_a_server_that_did_not_come_up_is_reported_with_why_and_its_last_output(app, outcome, word):
    client = FakeClient()
    runner, _ = make(client, app, StubServers(outcome=outcome))
    runner.handle({"job_id": "j1", "kind": "serve", "server": "dev"})
    (_, report), = client.reported
    assert word in report["error"] and report["server"]["status"] == outcome["status"] and report["output_tail"] == outcome.get("output_tail", "")


def test_a_stop_job_stops_the_projects_server(app):
    client, servers = FakeClient(), StubServers(port=4321)
    runner, _ = make(client, app, servers)
    runner.handle({"job_id": "j1", "kind": "stop"})
    runner.handle({"job_id": "j2", "kind": "stop"})
    assert [r["server"]["status"] for _, r in client.reported] == ["stopped", "none"] and len(servers.stopped) == 2


def test_the_runner_stops_every_server_it_started_however_it_ends(app):
    servers = StubServers()
    runner, _ = make(FakeClient(), app, servers)
    runner.stop.set()
    runner.serve()
    assert servers.all_stopped is True


# ---- looking ---------------------------------------------------------------------------------------------------------------------------------

def test_a_look_at_a_page_the_person_runs_opens_it_at_the_chosen_size_and_reports_everything_but_a_big_command(app):
    client, looker = FakeClient(), Looker()
    runner, _ = make(client, app, StubServers(), looker, browser_finder=lambda: {"name": "edge", "path": "x"})
    runner.handle({"job_id": "j1", "kind": "look", "url": "http://localhost:5173/pricing", "device": "phone"})
    (_, report), = client.reported
    assert looker.calls == [{"url": "http://localhost:5173/pricing", "device": "phone", "browser": {"name": "edge", "path": "x"}, "port": None}]
    assert report["exit_code"] == 0 and report["look"]["title"] == "My app" and report["look"]["screenshot_b64"] == "QUJD"


@pytest.mark.parametrize("url", ["http://example.com/", "https://localhost:5173/", "file:///etc/passwd", "http://192.168.1.4:3000/", "http://localhost:3000@evil.com/", "javascript:alert(1)", "", None])
def test_an_address_that_is_not_on_this_machine_is_refused_even_though_the_server_sent_it(app, url):
    client, looker = FakeClient(), Looker()
    runner, said = make(client, app, StubServers(), looker)
    runner.handle({"job_id": "j1", "kind": "look", "url": url, "device": "phone"})
    (_, report), = client.reported
    assert looker.calls == [] and report["exit_code"] is None and report["error"].startswith("The runner refused it:")
    assert not any("example.com" in line for line in said), "the log never quotes the address"


def test_an_unknown_device_is_refused(app):
    client, looker = FakeClient(), Looker()
    runner, _ = make(client, app, StubServers(), looker)
    runner.handle({"job_id": "j1", "kind": "look", "url": "http://localhost:5173/", "device": "fridge"})
    assert looker.calls == [] and "device" in client.reported[0][1]["error"]


def test_a_look_in_a_preview_session_must_be_on_the_port_of_the_server_this_runner_keeps(app):
    client, looker, servers = FakeClient(), Looker(), StubServers(port=4321)
    runner, _ = make(client, app, servers, looker)
    runner.handle({"job_id": "j1", "kind": "look", "session": True, "url": "http://localhost:4321/a", "device": "desktop", "port": 9999})
    runner.handle({"job_id": "j2", "kind": "look", "session": True, "url": "http://localhost:3000/", "device": "desktop", "port": 3000})
    assert [c["url"] for c in looker.calls] == ["http://localhost:4321/a"] and looker.calls[0]["port"] == 4321, "the port the server claims is ignored; the runner's own is used"
    assert client.reported[0][1]["exit_code"] == 0 and "port 4321" in client.reported[1][1]["error"]
    assert servers.touched, "a look counts as use, so the idle clock restarts"


def test_a_session_look_with_no_server_running_here_is_refused_with_the_reason(app):
    client, looker = FakeClient(), Looker()
    runner, _ = make(client, app, StubServers(port=None), looker)
    runner.handle({"job_id": "j1", "kind": "look", "session": True, "url": "http://localhost:4321/", "device": "phone"})
    assert looker.calls == [] and "no server is running" in client.reported[0][1]["error"]


def test_a_session_flag_that_is_not_a_real_yes_is_just_a_look_at_any_page_on_this_machine(app):
    looker = Looker()
    runner, _ = make(FakeClient(), app, StubServers(port=4321), looker)
    runner.handle({"job_id": "j1", "kind": "look", "session": "true", "url": "http://localhost:3000/", "device": "phone"})
    assert looker.calls[0]["port"] is None


def test_a_look_that_did_not_work_reports_why_and_no_screenshot(app):
    client = FakeClient()
    runner, _ = make(client, app, StubServers(), Looker({"ok": False, "error": "The page could not be opened (net::ERR_CONNECTION_REFUSED). Is the server running on that port?", "duration": 0.5}))
    runner.handle({"job_id": "j1", "kind": "look", "url": "http://localhost:5999/", "device": "phone"})
    (_, report), = client.reported
    assert report["exit_code"] is None and "Is the server running" in report["error"] and "look" not in report


def test_a_look_for_a_project_this_runner_does_not_serve_is_refused(app):
    client, looker = FakeClient(), Looker()
    runner, _ = make(client, app, StubServers(), looker)
    runner.handle({"job_id": "j1", "kind": "look", "project_id": "abcdef01", "url": "http://localhost:5173/", "device": "phone"})
    assert looker.calls == [] and "does not serve that project" in client.reported[0][1]["error"]


def test_the_project_id_is_the_one_the_runner_made(app):
    runner, _ = make(FakeClient(), app)
    assert list(runner.projects) == [projects.project_id(app)]
