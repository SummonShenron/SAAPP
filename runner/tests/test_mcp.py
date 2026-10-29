"""The runner as an MCP server (mcp_server.py): the protocol (the handshake, the tool list, calls, errors, cancelling, framing) and what the tools may do (the runner's own rules, unchanged).

The last tests start the real `python -m saapp_runner mcp` as a child process and speak the protocol to it over pipes, the way an MCP client does."""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from saapp_runner import confirm, mcp_server
from saapp_runner.mcp_server import McpServer

PY = sys.executable


class Window:
    """The question on the person's computer: answers at once with what it was told, and remembers what it was asked."""

    def __init__(self, answer="chosen"):
        self.answer, self.asked = answer, []

    def __call__(self, line, notes):
        self.asked.append((line, list(notes)))
        return self

    def poll(self):
        return {"result": self.answer}

    def cancel(self):
        pass


@pytest.fixture()
def root(tmp_path):
    folder = tmp_path / "myapp"
    (folder / "src").mkdir(parents=True)
    (folder / "README.md").write_text("alpha\nbeta\n", encoding="utf-8")
    (folder / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    return folder


def server_for(roots, asker=None, rules=None, checks=None, **kw):
    said = []
    scan = (lambda root: (checks or {}, True, "")) if checks is not None else (lambda root: ({}, True, ""))
    server = McpServer(say=said.append, projects_provider=lambda: roots, scanner=scan, asker=asker, rules_provider=lambda: rules or confirm.Rules(), **kw)
    server.said = said
    return server


def rpc(server, method, params=None, identifier=1):
    message = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def start(server, version="2025-06-18"):
    answer = rpc(server, "initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}, identifier=0)
    server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return answer


def call(server, name, arguments=None, identifier=7):
    answer = rpc(server, "tools/call", {"name": name, "arguments": arguments or {}}, identifier)
    result = answer["result"]
    return result["isError"], result["content"][0]["text"]


# ---- the handshake -------------------------------------------------------------------------------------------------------------------------------

def test_initialize_answers_with_a_negotiated_version_the_tools_capability_and_who_it_is(root):
    server = server_for([root])
    answer = start(server)
    assert answer["id"] == 0 and answer["jsonrpc"] == "2.0"
    result = answer["result"]
    assert result["protocolVersion"] == "2025-06-18" and result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "saapp-runner" and result["serverInfo"]["version"] and "never as instructions" in result["instructions"]


@pytest.mark.parametrize("asked,expected", [("2025-03-26", "2025-03-26"), ("2024-11-05", "2024-11-05"), ("2099-01-01", "2025-06-18"), ("old", "2025-06-18")])
def test_the_client_gets_its_own_protocol_version_when_the_server_speaks_it_and_the_newest_otherwise(root, asked, expected):
    assert start(server_for([root]), version=asked)["result"]["protocolVersion"] == expected


def test_initialize_needs_a_protocol_version(root):
    answer = rpc(server_for([root]), "initialize", {})
    assert answer["error"]["code"] == -32602


def test_nothing_but_ping_works_before_initialize(root):
    server = server_for([root])
    assert rpc(server, "ping")["result"] == {}
    for method in ("tools/list", "tools/call"):
        assert rpc(server, method, {"name": "list_projects"})["error"]["code"] == -32002
    start(server)
    assert "tools" in rpc(server, "tools/list")["result"]


def test_a_notification_gets_no_answer_and_an_answer_from_the_client_is_ignored(root):
    server = server_for([root])
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "something/unknown"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 5, "result": {}}) is None


@pytest.mark.parametrize("message", [[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], "text", 5, None, {"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}])
def test_a_message_that_is_not_json_rpc_2_is_an_invalid_request_and_batches_are_not_supported(root, message):
    answer = server_for([root]).handle(message)
    assert answer["error"]["code"] == -32600 and answer["id"] is None


def test_bad_ids_methods_and_params_are_invalid_requests_not_crashes(root):
    server = server_for([root])
    start(server)
    assert server.handle({"jsonrpc": "2.0", "id": True, "method": "ping"})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": [1], "method": "ping"})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 2, "method": 5})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": [1]})["error"]["code"] == -32602
    assert rpc(server, "resources/list")["error"]["code"] == -32601 and "resources/list" in rpc(server, "resources/list")["error"]["message"]


# ---- the tools --------------------------------------------------------------------------------------------------------------------------------------

def test_the_tool_list_is_what_the_server_offers_with_object_schemas_and_hints_a_client_can_use(root):
    server = server_for([root])
    start(server)
    tools = {tool["name"]: tool for tool in rpc(server, "tools/list")["result"]["tools"]}
    assert set(tools) == {"list_projects", "run_check", "look", "run_command"}
    for tool in tools.values():
        assert tool["inputSchema"]["type"] == "object" and tool["description"] and tool["title"] and tool["inputSchema"]["additionalProperties"] is False
        assert set(tool["annotations"]) == {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    assert tools["look"]["annotations"]["readOnlyHint"] is True and tools["list_projects"]["annotations"]["readOnlyHint"] is True
    assert tools["run_command"]["annotations"]["readOnlyHint"] is False and tools["run_command"]["annotations"]["destructiveHint"] is True
    assert tools["run_check"]["inputSchema"]["required"] == ["check"] and tools["look"]["inputSchema"]["required"] == ["line"]
    assert "path" not in json.dumps([t["inputSchema"] for t in tools.values()]).lower().replace("paths inside", "")           # a model never names a folder: only a project's name


def test_an_unknown_tool_or_bad_arguments_are_protocol_errors_and_a_failed_tool_is_a_result_the_model_can_read(root):
    server = server_for([root])
    start(server)
    assert rpc(server, "tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert rpc(server, "tools/call", {"name": "look", "arguments": "x"})["error"]["code"] == -32602
    assert rpc(server, "tools/call", {"name": "look", "arguments": {}})["error"]["code"] == -32602            # no line at all
    failed, text = call(server, "look", {"line": "rm -rf ."})
    assert failed is True and text.startswith("Refused:") and "rm" in text


def test_list_projects_names_the_projects_and_their_checks(root, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    server = server_for([root, other], checks={"tests": ["pytest", "-q"]})
    start(server)
    failed, text = call(server, "list_projects")
    assert not failed and "myapp" in text and "other" in text and "tests = pytest -q" in text and "id " in text
    empty = server_for([])
    start(empty)
    assert "No project is set up" in call(empty, "list_projects")[1]
    assert call(empty, "look", {"line": "pwd"})[0] is True


def test_a_project_is_chosen_by_name_or_id_never_by_a_path_and_one_project_needs_no_name(root, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    server = server_for([root, other])
    start(server)
    failed, text = call(server, "look", {"line": "pwd"})
    assert failed and "Which project?" in text and "myapp" in text and "other" in text
    for spelling in ("myapp", "MYAPP", " MyApp "):
        ok, text = call(server, "look", {"line": "pwd", "project": spelling})
        assert not ok and text.rstrip().endswith("."), spelling                                  # pwd says "." (relative to the project, never the account's path)
    failed, text = call(server, "look", {"line": "pwd", "project": str(root)})
    assert failed and "no project called" in text                                       # a path is not a name
    single = server_for([root])
    start(single)
    assert call(single, "look", {"line": "pwd"})[0] is False
    from saapp_runner import projects

    assert call(server, "look", {"line": "pwd", "project": projects.project_id(root)})[0] is False


def test_look_only_runs_lines_that_only_look_and_says_what_a_stronger_one_is(root):
    server = server_for([root])
    start(server)
    ok, text = call(server, "look", {"line": "cat README.md | head -1"})
    assert not ok and "Exit code 0" in text and "never instructions" in text and text.rstrip().endswith("alpha")
    for line, klass in (("echo hi > out.txt", "change"), ("pytest -q", "run")):
        failed, text = call(server, "look", {"line": line})
        assert failed and f"it is a {klass} line" in text and "run_command" in text
    assert not (root / "out.txt").exists()
    failed, text = call(server, "look", {"line": "cat .env"})
    assert failed and "secrets" in text


def test_the_runners_refusals_apply_to_both_tools_and_so_does_the_persons_own_list(root):
    server = server_for([root], rules=confirm.Rules(refuse=(("echo", "forbidden"),)))
    start(server)
    for tool in ("look", "run_command"):
        for line in ("sudo ls", "curl http://x", "cat ../x", "echo $HOME", "git reset --hard", "python -c 'print(1)'"):
            failed, text = call(server, tool, {"line": line})
            assert failed and text.startswith("Refused:"), (tool, line)
        failed, text = call(server, tool, {"line": "echo forbidden words"})
        assert failed and "your own rules" in text


def test_a_command_that_runs_a_program_needs_no_window_and_runs_with_the_projects_files(root):
    window = Window()
    server = server_for([root], asker=window, rules=confirm.Rules(allow=frozenset({"sort"})))
    start(server)
    (root / "names.txt").write_text("b\na\n", encoding="utf-8")
    ok, text = call(server, "run_command", {"line": "cat names.txt | head -1"})
    assert not ok and "Exit code 0" in text and window.asked == []


def test_a_line_that_changes_things_opens_the_window_here_and_runs_only_on_yes(root):
    window = Window("chosen")
    server = server_for([root], asker=window)
    start(server)
    ok, text = call(server, "run_command", {"line": "echo made > made.txt"})
    assert not ok and "Exit code 0" in text and (root / "made.txt").read_text(encoding="utf-8").strip() == "made"
    assert window.asked[0][0] == "echo made > made.txt" and window.asked[0][1]
    no = server_for([root], asker=Window("declined"))
    start(no)
    failed, text = call(no, "run_command", {"line": "echo again > again.txt"})
    assert failed and "said no" in text and not (root / "again.txt").exists()


def test_with_no_way_to_ask_a_change_is_refused_and_turning_the_question_off_is_the_persons_choice(root):
    server = server_for([root], asker=None)
    start(server)
    failed, text = call(server, "run_command", {"line": "echo x > a.txt"})
    assert failed and "cannot show the window" in text and not (root / "a.txt").exists()
    broken = server_for([root], asker=Window("no_picker"))
    start(broken)
    failed, text = call(broken, "run_command", {"line": "echo x > b.txt"})
    assert failed and "no window could be shown" in text and not (root / "b.txt").exists()
    quiet = server_for([root], asker=None, rules=confirm.Rules(confirm_changes=False))
    start(quiet)
    assert call(quiet, "run_command", {"line": "echo x > c.txt"})[0] is False and (root / "c.txt").exists()
    outside = call(quiet, "run_command", {"line": "echo x > ../escape.txt"})
    assert outside[0] is True and not (root.parent / "escape.txt").exists()                     # the question being off never switches the rules off


def test_run_check_runs_a_named_check_in_the_project_with_fake_keys_and_reports_how_it_ended(root, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "a-real-looking-key")
    script = "import os; print('key=' + os.environ.get('GOOGLE_API_KEY', 'none'))"
    server = server_for([root], checks={"tests": [PY, "-c", script], "fails": [PY, "-c", "import sys; print('boom'); sys.exit(3)"], "quiet": [PY, "-c", "pass"]})
    start(server)
    ok, text = call(server, "run_check", {"check": "tests"})
    assert not ok and "Exit code 0" in text and "key=test-not-real" in text and "a-real-looking-key" not in text
    ok, text = call(server, "run_check", {"check": "fails"})
    assert not ok and "Exit code 3" in text and "boom" in text                             # a failing check is a result, not a tool error
    assert "printed nothing" in call(server, "run_check", {"check": "quiet"})[1]
    bare = server_for([root], checks={"tests": [PY, "-c", script]}, rules=confirm.Rules(fake_keys=False))
    start(bare)
    text = call(bare, "run_check", {"check": "tests"})[1]
    assert "key=none" in text and "a-real-looking-key" not in text and "test-not-real" not in text      # fake keys off means no key at all: the environment is clean either way


def test_run_check_names_what_exists_when_asked_for_a_check_that_does_not_and_refuses_unsafe_files(root):
    server = server_for([root], checks={"tests": [PY, "-c", "print(1)"]})
    start(server)
    failed, text = call(server, "run_check", {"check": "lint"})
    assert failed and "no check called" in text and "tests" in text
    for files in (["../outside.py"], ["/etc/passwd"], ["--rootdir=/"]):
        failed, text = call(server, "run_check", {"check": "tests", "files": files})
        assert failed and text.startswith("Refused:"), files
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    failed, text = call(server, "run_check", {"check": "tests", "files": ["src/app.py"]})
    assert failed and "does not take files" in text
    assert rpc(server, "tools/call", {"name": "run_check", "arguments": {}})["error"]["code"] == -32602


def test_output_is_capped_to_the_end_and_a_timeout_is_said_plainly(root):
    big = server_for([root], checks={"loud": [PY, "-c", "print('line ' * 5000); print('THE END')"]}, max_chars=300)
    start(big)
    ok, text = call(big, "run_check", {"check": "loud"})
    assert not ok and "start was cut" in text and text.rstrip().endswith("THE END") and len(text) < 600
    slow = server_for([root], checks={"slow": [PY, "-c", "import time; time.sleep(30)"]}, timeout=1)
    start(slow)
    started = time.monotonic()
    ok, text = call(slow, "run_check", {"check": "slow"})
    assert not ok and "timed out" in text and time.monotonic() - started < 20


def test_one_command_at_a_time(root):
    server = server_for([root], checks={"slow": [PY, "-c", "import time; time.sleep(2)"], "fast": [PY, "-c", "print(1)"]})
    start(server)
    first = {}
    thread = threading.Thread(target=lambda: first.update(answer=rpc(server, "tools/call", {"name": "run_check", "arguments": {"check": "slow"}}, 20)), daemon=True)
    thread.start()
    time.sleep(0.5)
    failed, text = call(server, "run_check", {"check": "fast"})
    assert failed and "still running" in text
    thread.join(timeout=15)
    assert first["answer"]["result"]["isError"] is False
    assert call(server, "run_check", {"check": "fast"})[0] is False                           # free again


def test_the_log_names_the_tool_and_how_long_but_never_a_command_a_path_or_output(root):
    server = server_for([root])
    start(server)
    call(server, "look", {"line": "cat README.md"})
    call(server, "look", {"line": "cat .env"})
    log = "\n".join(server.said)
    assert "mcp: look done" in log and "mcp: look refused or failed" in log
    for private in ("README", ".env", "alpha", "hunter2", str(root), "myapp"):
        assert private not in log


# ---- the stream: framing, parallel calls, cancelling ----------------------------------------------------------------------------------------------

def run_stream(server, lines, timeout=20):
    """Feeds lines to serve() and returns every message it wrote."""
    out = io.StringIO()
    thread = threading.Thread(target=lambda: server.serve(io.StringIO("".join(line + "\n" for line in lines)), out), daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive()
    written = out.getvalue()
    assert written == "" or written.endswith("\n")
    return [json.loads(line) for line in written.splitlines()], written


def test_the_stream_is_one_json_message_per_line_and_bad_lines_are_answered_not_fatal(root):
    server = server_for([root])
    messages, raw = run_stream(server, ['{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}', "this is not json", "", '{"jsonrpc":"2.0","id":2,"method":"ping"}',
                                        '{"jsonrpc":"2.0","method":"notifications/initialized"}', '{"jsonrpc":"2.0","id":3,"method":"tools/list"}'])
    assert [m.get("id") for m in messages] == [1, None, 2, 3] and messages[1]["error"]["code"] == -32700
    assert all("\n" not in json.dumps(m) for m in messages) and "\r" not in raw
    assert [t["name"] for t in messages[3]["result"]["tools"]] == mcp_server.TOOL_NAMES


def test_a_ping_is_answered_while_a_command_is_still_running_and_a_cancel_stops_it_with_no_answer(root):
    server = server_for([root], checks={"slow": [PY, "-c", "import time; time.sleep(60)"]})
    r, w = os.pipe()
    reader, writer = os.fdopen(r, "r", encoding="utf-8"), os.fdopen(w, "w", encoding="utf-8")
    out = io.StringIO()
    thread = threading.Thread(target=lambda: server.serve(reader, out), daemon=True)
    thread.start()

    def send(message):
        writer.write(json.dumps(message) + "\n")
        writer.flush()

    def seen(identifier, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in out.getvalue().splitlines():
                if json.loads(line).get("id") == identifier:
                    return json.loads(line)
            time.sleep(0.05)
        return None

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert seen(1)
    send({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "run_check", "arguments": {"check": "slow"}}})
    time.sleep(0.6)
    send({"jsonrpc": "2.0", "id": 10, "method": "ping"})
    assert seen(10)["result"] == {} and seen(9, timeout=0.3) is None                      # answered the ping while the check was running
    started = time.monotonic()
    send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}})
    time.sleep(2.0)
    assert time.monotonic() - started < 10
    assert seen(9, timeout=0.5) is None                                                    # a cancelled request gets no answer
    send({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "list_projects"}})
    assert seen(11)["result"]["isError"] is False                                           # and the server is free again
    writer.close()
    thread.join(timeout=20)
    assert not thread.is_alive()


def test_when_the_client_goes_away_a_running_command_is_stopped(root):
    server = server_for([root], checks={"slow": [PY, "-c", "import time; time.sleep(60)"]})
    started = time.monotonic()
    run_stream(server, ['{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}',
                        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"run_check","arguments":{"check":"slow"}}}'], timeout=30)
    assert time.monotonic() - started < 25


# ---- the real thing: a child process over pipes --------------------------------------------------------------------------------------------------

@pytest.fixture()
def child(tmp_path, root):
    home = tmp_path / "home"
    home.mkdir()
    (home / "projects.json").write_text(json.dumps({"projects": [{"root": str(root)}]}), encoding="utf-8")
    env = {**os.environ, "SAAPP_RUNNER_HOME": str(home), "PYTHONPATH": str(Path(mcp_server.__file__).resolve().parents[1]), "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen([PY, "-m", "saapp_runner", "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(tmp_path))

    def talk(message):
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        process.stdin.flush()
        return json.loads(process.stdout.readline().decode("utf-8"))

    yield process, talk
    try:
        process.stdin.close()
        process.wait(timeout=15)
    except Exception:
        process.kill()


def test_a_real_client_session_over_stdio(child):
    process, talk = child
    init = talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}}})
    assert init["result"]["serverInfo"]["name"] == "saapp-runner"
    process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    process.stdin.flush()
    tools = talk({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_projects", "run_check", "look", "run_command"}
    listed = talk({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})
    assert listed["result"]["isError"] is False and "myapp" in listed["result"]["content"][0]["text"]
    looked = talk({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "look", "arguments": {"line": "cat README.md | head -1"}}})
    assert looked["result"]["isError"] is False and looked["result"]["content"][0]["text"].rstrip().endswith("alpha")
    refused = talk({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "look", "arguments": {"line": "cat .env"}}})
    assert refused["result"]["isError"] is True and "hunter2" not in json.dumps(refused)
    ran = talk({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "run_command", "arguments": {"line": "echo hello from the child", "project": "myapp"}}})
    assert ran["result"]["isError"] is False and "hello from the child" in ran["result"]["content"][0]["text"]
    # (a line that changes things is not tried here: it would open the real confirmation window on this screen)


def test_the_child_writes_only_protocol_on_stdout_and_exits_cleanly_when_the_client_closes(child):
    process, talk = child
    talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    talk({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "look", "arguments": {"line": "pwd"}}})
    process.stdin.close()
    assert process.wait(timeout=20) == 0
    rest = process.stdout.read().decode("utf-8")
    assert rest == ""                                                                          # nothing but the answers we already read
    log = process.stderr.read().decode("utf-8")
    assert "mcp: client connected" in log and "mcp: look done" in log and "pwd" not in log


def test_print_config_gives_the_entry_a_client_needs():
    from saapp_runner.mcp_server import client_config

    entry = client_config("/usr/bin/python3")["mcpServers"]["saapp-runner"]
    assert entry == {"command": "/usr/bin/python3", "args": ["-m", "saapp_runner", "mcp"]}
    out = subprocess.run([PY, "-m", "saapp_runner", "mcp", "--print-config"], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(Path(mcp_server.__file__).resolve().parents[1])}, timeout=30)
    assert out.returncode == 0 and json.loads(out.stdout)["mcpServers"]["saapp-runner"]["args"][-1] == "mcp"


def test_the_config_comes_in_the_portable_form_and_in_vs_codes_own_form():
    from saapp_runner.mcp_server import client_config

    assert client_config("py")["mcpServers"]["saapp-runner"] == {"command": "py", "args": ["-m", "saapp_runner", "mcp"]}
    assert client_config("py", "vscode") == {"servers": {"saapp-runner": {"type": "stdio", "command": "py", "args": ["-m", "saapp_runner", "mcp"]}}}
    env = {**os.environ, "PYTHONPATH": str(Path(mcp_server.__file__).resolve().parents[1])}
    out = subprocess.run([PY, "-m", "saapp_runner", "mcp", "--print-config", "--format", "vscode"], capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0 and json.loads(out.stdout)["servers"]["saapp-runner"]["type"] == "stdio"
