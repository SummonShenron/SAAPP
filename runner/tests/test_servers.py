"""A project's own server, kept running between jobs (saapp_runner/servers.py), with real stand-in server processes."""
import json
import socket
import sys
import threading
import time

import pytest

from saapp_runner.servers import ServerManager, detect, find_port, says_exposed

# A stand-in dev server: picks a free port, prints the line a real one prints, and serves until it is killed.
SERVER = (
    "import http.server, socketserver, sys\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
    "    def log_message(self, *a): pass\n"
    "s = socketserver.TCPServer(('127.0.0.1', 0), H)\n"
    "print('  VITE v5  ready'); print('  Local: http://localhost:%d/' % s.server_address[1], flush=True)\n"
    "s.serve_forever()\n"
)


def words():
    return [sys.executable, "-u", "-c", SERVER]


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_closed(port, seconds=5):
    deadline = time.monotonic() + seconds
    while listening(port) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not listening(port)


@pytest.fixture()
def manager():
    manager = ServerManager(idle_seconds=60, port_wait=20)
    yield manager
    manager.stop_all()


def test_a_project_offers_npm_run_dev_or_start_by_name_from_its_own_package_file(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "start": "node x", "test": "t"}}), encoding="utf-8")
    assert detect(tmp_path) == {"dev": ["npm", "run", "dev"]}
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node x"}}), encoding="utf-8")
    assert detect(tmp_path) == {"start": ["npm", "start"]}
    for body in ('{"scripts": {"test": "t"}}', "{}", "not json", '{"scripts": "oops"}', "[]"):
        (tmp_path / "package.json").write_text(body, encoding="utf-8")
        assert detect(tmp_path) == {}
    assert detect(tmp_path / "missing") == {}


@pytest.mark.parametrize("output, port", [
    ("  VITE v5.4.0  ready in 300 ms\n\n  \x1b[32m➜\x1b[39m  Local:   http://localhost:5173/\n", 5173),
    ("Running on http://127.0.0.1:5000\nPress CTRL+C to quit", 5000),
    ("ready - started server on 0.0.0.0:3000, url: http://localhost:3000", 3000),
    ("Server listening on port 8080", 8080),
    ("Local: http://[::1]:4321/", 4321),
    ("nothing useful here", None), ("", None), (None, None), ("listening on port 99999", None),
])
def test_the_port_is_read_from_what_the_server_prints(output, port):
    assert find_port(output) == port


def test_the_output_says_whether_other_computers_can_reach_the_server():
    assert says_exposed("  Local:   http://localhost:5173/\n  Network: http://192.168.1.20:5173/") is True
    assert says_exposed("Listening on 0.0.0.0:3000") is True
    assert says_exposed("  Local:   http://localhost:5173/\n  Network: use --host to expose") is False
    assert says_exposed("Running on http://127.0.0.1:5000") is False


def test_it_starts_waits_for_the_port_and_the_server_really_answers(manager, tmp_path):
    result = manager.start("p1", tmp_path, "dev", words())
    assert result["status"] == "running" and result["name"] == "dev" and result["exposed"] is False and "Local: http://localhost:" in result["output_tail"]
    port = result["port"]
    assert listening(port) and manager.port_of("p1") == port and manager.status("p1")["port"] == port
    assert manager.start("p1", tmp_path, "dev", words())["port"] == port, "the same server is not started twice"


def test_stopping_really_ends_the_process_and_frees_the_port(manager, tmp_path):
    port = manager.start("p1", tmp_path, "dev", words())["port"]
    assert manager.stop("p1") is True and manager.stop("p1") is False
    assert wait_closed(port) and manager.status("p1") is None and manager.port_of("p1") is None


def test_a_server_that_exits_by_itself_reports_its_exit_code_and_what_it_printed(manager, tmp_path):
    result = manager.start("p1", tmp_path, "dev", [sys.executable, "-c", "import sys; print('Error: port in use'); sys.exit(3)"])
    assert result["status"] == "exited" and result["exit_code"] == 3 and "port in use" in result["output_tail"] and manager.status("p1") is None


def test_a_server_that_never_says_its_port_is_stopped_and_reported(tmp_path):
    manager = ServerManager(port_wait=1.5)
    result = manager.start("p1", tmp_path, "dev", [sys.executable, "-u", "-c", "import time; print('starting...'); time.sleep(60)"])
    assert result["status"] == "no_port" and "starting..." in result["output_tail"] and manager.status("p1") is None


def test_a_missing_program_is_a_plain_answer_not_an_exception(manager, tmp_path):
    result = manager.start("p1", tmp_path, "dev", ["definitely-not-a-program-xyz", "run"])
    assert result["status"] == "exited" and "not found" in result["output_tail"]


def test_cancelling_while_it_starts_stops_it(tmp_path):
    manager = ServerManager(port_wait=20)
    cancel = threading.Event()
    threading.Timer(0.8, cancel.set).start()
    result = manager.start("p1", tmp_path, "dev", [sys.executable, "-u", "-c", "import time; print('starting...'); time.sleep(60)"], cancel=cancel)
    assert result == {"status": "cancelled"} and manager.status("p1") is None


def test_a_server_nobody_uses_is_stopped_after_the_idle_limit_and_using_it_keeps_it(manager, tmp_path):
    manager.idle_seconds = 100
    port = manager.start("p1", tmp_path, "dev", words())["port"]
    assert manager.sweep(time.monotonic() + 50) == [] and listening(port)
    manager.touch("p1")
    assert manager.sweep(time.monotonic() + 99) == [] and listening(port)
    assert manager.sweep(time.monotonic() + 101) == ["p1"]
    assert wait_closed(port)


def test_one_server_per_project_a_different_one_replaces_it_and_projects_do_not_touch_each_other(manager, tmp_path):
    first = manager.start("p1", tmp_path, "dev", words())["port"]
    other = manager.start("p2", tmp_path, "dev", words())["port"]
    assert first != other and listening(first) and listening(other)
    replaced = manager.start("p1", tmp_path, "start", words())
    assert replaced["name"] == "start"
    assert wait_closed(first) and listening(other)
    assert manager.stop_all() == 2


def test_the_server_is_told_not_to_open_a_browser_and_does_not_see_ci(manager, tmp_path):
    script = "import os; print('BROWSER=' + os.environ.get('BROWSER', '') + ' CI=' + os.environ.get('CI', 'unset'), flush=True); import time; time.sleep(30)"
    manager.port_wait = 2
    result = manager.start("p1", tmp_path, "dev", [sys.executable, "-u", "-c", script])
    assert result["status"] == "no_port" and "BROWSER=none CI=unset" in result["output_tail"]


def test_fake_keys_reach_the_server_only_when_asked(manager, tmp_path):
    script = "import os; print('KEY=' + os.environ.get('GOOGLE_API_KEY', 'none'), flush=True); import time; time.sleep(30)"
    manager.port_wait = 2
    assert "KEY=test-not-real" in manager.start("p1", tmp_path, "dev", [sys.executable, "-u", "-c", script], {"GOOGLE_API_KEY": "test-not-real"})["output_tail"]
    assert "KEY=none" in manager.start("p2", tmp_path, "dev", [sys.executable, "-u", "-c", script])["output_tail"]
