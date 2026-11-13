"""The runner sends a project's files to SAAPP only when asked AND after the person says yes in a window on this computer (filerules.py, workspace_sync.py, share_dialog.py, sharing.py, loop.py)."""
import base64
import json
import time
from pathlib import Path

import pytest

from saapp_runner import cli, filerules, projects, share_dialog, sharing, workspace_sync
from saapp_runner.client import ServerError, Unreachable
from saapp_runner.loop import Runner

PY = __import__("sys").executable


def make(root: Path, relative: str, content=b"x = 1\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "store-api"
    make(root, "src/app.py")
    make(root, "README.md", "# hi\r\nsecond line\r\n")
    make(root, "package.json", "{}")
    make(root, ".env", "KEY=secret")
    make(root, "config/.env.production", "KEY=secret")
    make(root, "config/.env.example", "KEY=")
    make(root, "keys/server.pem", "private")
    make(root, "id_rsa", "private")
    make(root, "node_modules/lib/index.js", "x")
    make(root, ".git/config", "[core]")
    make(root, "dist/app.min.js", "x")
    make(root, "build/out.py", "x")
    make(root, "package-lock.json", "{}")
    make(root, "static/app.min.js", "x")
    make(root, "static/app.js.map", "x")
    make(root, "image.png", b"\x89PNG")
    make(root, "notes.docx", b"PK\x03\x04docx")
    make(root, "~$notes.docx", b"lock")
    return root


# ---- the rules -----------------------------------------------------------------------------------------------------------------------------

def test_secrets_build_output_lockfiles_and_binaries_are_never_picked(project):
    paths = [e.path for e in workspace_sync.scan(project).entries]
    assert paths == ["README.md", "config/.env.example", "notes.docx", "package.json", "src/app.py"]


@pytest.mark.parametrize("name,expected", [
    (".env", False), (".env.local", False), (".env.json", False), (".env.production.yaml", False), (".env.sample", True), ("prod.env", False), ("id_ed25519", False), ("credentials.json", False), ("cert.PEM", False), ("secrets.toml", False), (".npmrc", False),
    (".env.example", True), ("settings.env.sample", True), ("app.py", True), ("Dockerfile", True), (".gitignore", True), ("report.XLSX", True), ("notes.txt", True),
    ("app.min.js", False), ("yarn.lock", False), ("logo.png", False), ("~$draft.docx", False), ("LICENSE", True), ("Makefile", True), ("noextension", False), (".hidden", False),
])
def test_what_a_file_name_decides(name, expected):
    assert filerules.is_syncable_file_name(name) is expected


def test_a_big_file_is_counted_and_left_out_and_a_word_file_has_a_bigger_limit(tmp_path):
    root = tmp_path / "p"
    make(root, "ok.py")
    make(root, "huge.py", b"x" * (filerules.MAX_FILE_BYTES + 1))
    make(root, "big.docx", b"x" * (filerules.MAX_FILE_BYTES + 1))
    make(root, "huge.docx", b"x" * (filerules.MAX_DOCX_BYTES + 1))
    found = workspace_sync.scan(root)
    assert [e.path for e in found.entries] == ["big.docx", "ok.py"] and found.too_big == 2 and found.entries[0].office and not found.entries[1].office


def test_the_file_and_size_limits_stop_the_walk_and_say_why(tmp_path, monkeypatch):
    root = tmp_path / "p"
    for index in range(5):
        make(root, f"f{index}.py", "x" * 10)
    monkeypatch.setattr(filerules, "MAX_FILES", 3)
    assert len(workspace_sync.scan(root).entries) == 3 and workspace_sync.scan(root).truncated == "files"
    monkeypatch.setattr(filerules, "MAX_FILES", 8000)
    monkeypatch.setattr(filerules, "MAX_TOTAL_BYTES", 25)
    found = workspace_sync.scan(root)
    assert len(found.entries) == 2 and found.total_bytes == 20 and found.truncated == "bytes"


def test_a_link_out_of_the_project_is_never_followed(tmp_path, monkeypatch):
    root = tmp_path / "p"
    make(root, "real/a.py")
    make(tmp_path / "outside", "secret_notes.txt")
    real_is_link = workspace_sync._is_link
    monkeypatch.setattr(workspace_sync, "_is_link", lambda entry: entry.name == "linked" or real_is_link(entry))
    (root / "linked").mkdir()
    make(root, "linked/leak.py")
    assert [e.path for e in workspace_sync.scan(root).entries] == ["real/a.py"]


def test_the_summary_says_how_many_and_how_much_and_what_was_left_out(project):
    text = workspace_sync.summary(workspace_sync.scan(project))
    assert text.startswith("5 text files (") and "KB" in text
    big = workspace_sync.Scan(entries=[workspace_sync.Entry("a.py", 5, False)], total_bytes=3_400_000, too_big=2, truncated="files")
    assert "3.4 MB" in workspace_sync.summary(big) and "2 too big to send" in workspace_sync.summary(big) and "larger than the limit" in workspace_sync.summary(big)


# ---- sending -------------------------------------------------------------------------------------------------------------------------------

class Server:
    def __init__(self, fail=None):
        self.batches, self.fail = [], fail

    def workspace_sync(self, body):
        if self.fail:
            raise self.fail
        self.batches.append(body)
        return {"accepted": len(body["files"]), "rejected": [], "file_count": len(body["files"])}


def test_one_full_upload_resets_first_commits_last_and_sends_exact_bytes(project, monkeypatch):
    monkeypatch.setattr(filerules, "BATCH_MAX_FILES", 2)
    server = Server()
    found = workspace_sync.scan(project)
    outcome = workspace_sync.upload(server, "abc12345", project, found)
    assert outcome.ok and outcome.sent == 5
    assert [len(b["files"]) for b in server.batches] == [2, 2, 1]
    assert [b["reset"] for b in server.batches] == [True, False, False] and [b["commit"] for b in server.batches] == [False, False, True]
    assert len({b["upload_id"] for b in server.batches}) == 1 and {b["project_id"] for b in server.batches} == {"abc12345"}
    sent = {f["path"]: f for b in server.batches for f in b["files"]}
    assert base64.b64decode(sent["README.md"]["text_b64"]) == b"# hi\r\nsecond line\r\n"            # CRLF kept: exact bytes
    assert "content_base64" in sent["notes.docx"] and "text_b64" not in sent["notes.docx"]
    assert not any(p in sent for p in (".env", "id_rsa", "keys/server.pem", "node_modules/lib/index.js"))


def test_batches_stay_under_the_size_limit(tmp_path, monkeypatch):
    root = tmp_path / "p"
    for index in range(4):
        make(root, f"f{index}.py", "x" * 400)
    monkeypatch.setattr(filerules, "BATCH_MAX_BYTES", 900)
    server = Server()
    workspace_sync.upload(server, "id", root, workspace_sync.scan(root))
    assert [len(b["files"]) for b in server.batches] == [2, 2]


def test_a_file_that_vanished_after_the_scan_is_left_out_and_a_refusal_or_outage_is_an_outcome_not_a_crash(project):
    found = workspace_sync.scan(project)
    (project / "src" / "app.py").unlink()
    server = Server()
    assert workspace_sync.upload(server, "id", project, found).sent == 4
    refused = workspace_sync.upload(Server(fail=ServerError(403, "Nobody asked this project to send its files.")), "id", project, found)
    assert not refused.ok and "Nobody asked" in refused.message
    down = workspace_sync.upload(Server(fail=Unreachable("could not reach the server")), "id", project, found)
    assert not down.ok and "could not reach" in down.message
    empty = Server()
    assert not workspace_sync.upload(empty, "id", project, workspace_sync.Scan()).ok and empty.batches == []


# ---- the window -----------------------------------------------------------------------------------------------------------------------------

class Answers:
    def __init__(self, yes=True, fail=False):
        self.yes, self.fail, self.asked, self.closed = yes, fail, [], False

    def confirm(self, title, text):
        if self.fail:
            raise RuntimeError("no display")
        self.asked.append(text)
        return self.yes

    def close(self):
        self.closed = True


def test_the_window_names_the_project_the_folder_how_much_and_what_is_never_sent(tmp_path):
    ask = Answers(yes=True)
    assert share_dialog.run_dialog("store-api", tmp_path / "store-api", "5 text files (3 KB)", ask) == {"result": "chosen"} and ask.closed
    text = ask.asked[0]
    assert '"store-api"' in text and str(tmp_path / "store-api") in text and "5 text files (3 KB)" in text
    assert "secrets" in text and "node_modules" in text and "Stop sharing" in text and "Send these files now?" in text
    assert share_dialog.run_dialog("x", tmp_path, "1 text files", Answers(yes=False)) == {"result": "declined"}
    assert share_dialog.run_dialog("x", tmp_path, "1 text files", Answers(fail=True)) == {"result": "no_picker"}


def test_the_window_command_prints_one_line_of_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(share_dialog, "run_dialog", lambda name, root, summary: {"result": "chosen", "seen": [name, str(root), summary]})
    assert cli.main(["share-dialog", "--name", "store-api", "--root", str(tmp_path), "--summary", "5 files"]) == 0
    assert json.loads(capsys.readouterr().out) == {"result": "chosen", "seen": ["store-api", str(tmp_path), "5 files"]}


def test_the_window_in_its_own_process_does_not_block_the_caller():
    handle = share_dialog.start_dialog("x", Path("."), "1 files", [PY, "-c", "import time, json; time.sleep(0.3); print(json.dumps({'result': 'chosen'}))"])
    assert handle.poll() is None
    deadline = time.monotonic() + 15
    answer = None
    while answer is None and time.monotonic() < deadline:
        answer = handle.poll()
        time.sleep(0.05)
    assert answer == {"result": "chosen"}


# ---- asking first, sending second -----------------------------------------------------------------------------------------------------------

class Handle:
    def __init__(self, answers):
        self.answers, self.cancelled = list(answers), False

    def poll(self):
        return self.answers.pop(0) if self.answers else None

    def cancel(self):
        self.cancelled = True


def wait_for(sharer, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        done = sharer.poll()
        if done:
            return done
        time.sleep(0.02)
    raise AssertionError("never finished")


def sharer_for(answers, uploads, outcome=True, **kwargs):
    def uploader(client, project_id, root, found, say):
        uploads.append((project_id, Path(root).name, len(found.entries)))
        return workspace_sync.Outcome(outcome, 1, 0, "Sent." if outcome else "refused")
    return sharing.Sharing(object(), say=lambda text: None, starter=lambda name, root, summary: Handle(answers), uploader=uploader, **kwargs)


def test_yes_in_the_window_uploads_in_the_background_and_reports_shared(project):
    uploads = []
    sharer = sharer_for([None, {"result": "chosen"}], uploads)
    sharer.offer({"id": "r1", "project_id": "abc12345", "name": "store-api"}, project)
    assert sharer.poll() == [] and uploads == [] and sharer.busy()                      # still waiting for the person: nothing was sent
    assert wait_for(sharer) == [{"id": "r1", "result": "shared"}] and uploads == [("abc12345", "store-api", 5)] and not sharer.busy()


def test_no_in_the_window_sends_nothing_and_a_failed_upload_is_an_error(project):
    uploads = []
    sharer = sharer_for([{"result": "declined"}], uploads)
    sharer.offer({"id": "r1", "project_id": "p", "name": "store-api"}, project)
    assert wait_for(sharer) == [{"id": "r1", "result": "declined"}] and uploads == []
    failing = sharer_for([{"result": "chosen"}], uploads, outcome=False)
    failing.offer({"id": "r2", "project_id": "p", "name": "store-api"}, project)
    assert wait_for(failing) == [{"id": "r2", "result": "error"}]
    handles = iter([Handle([{"result": "no_picker"}]), Handle([{"result": "whatever"}])])
    other = sharing.Sharing(object(), say=lambda text: None, starter=lambda *a: next(handles), uploader=lambda *a, **k: uploads.append("never"))
    other.offer({"id": "r3", "project_id": "p", "name": "store-api"}, project)
    other.offer({"id": "r4", "project_id": "p", "name": "store-api"}, project)
    assert wait_for(other) == [{"id": "r3", "result": "no_picker"}, {"id": "r4", "result": "error"}]


def test_a_computer_with_no_window_a_missing_folder_or_nothing_to_send_never_uploads(project, tmp_path):
    uploads = []
    said = []
    blind = sharing.Sharing(object(), say=said.append, starter=None, uploader=lambda *a, **k: uploads.append(1))
    blind.offer({"id": "r1", "project_id": "p", "name": "store-api"}, project)
    assert blind.poll() == [{"id": "r1", "result": "no_picker"}] and uploads == [] and any("Nothing was sent" in line for line in said)
    sharer = sharer_for([{"result": "chosen"}], uploads)
    sharer.offer({"id": "r2", "project_id": "p", "name": "gone"}, tmp_path / "gone")
    sharer.offer({"id": "r3", "project_id": "p", "name": "none"}, None)
    empty = tmp_path / "empty"
    make(empty, ".env", "KEY=1")
    sharer.offer({"id": "r4", "project_id": "p", "name": "empty"}, empty)
    assert sharer.poll() == [{"id": "r2", "result": "error"}, {"id": "r3", "result": "error"}, {"id": "r4", "result": "error"}] and uploads == []


def test_a_window_that_cannot_be_started_is_no_picker_and_cancel_closes_open_windows(project):
    sharer = sharing.Sharing(object(), say=lambda text: None, starter=lambda *a: (_ for _ in ()).throw(OSError("no")), uploader=lambda *a, **k: None)
    sharer.offer({"id": "r1", "project_id": "p", "name": "x"}, project)
    assert sharer.poll() == [{"id": "r1", "result": "no_picker"}]
    handle = Handle([])
    open_one = sharing.Sharing(object(), say=lambda text: None, starter=lambda *a: handle, uploader=lambda *a, **k: None)
    open_one.offer({"id": "r2", "project_id": "p", "name": "x"}, project)
    open_one.cancel()
    assert handle.cancelled


# ---- the loop --------------------------------------------------------------------------------------------------------------------------------

class ShareClient:
    def __init__(self, polls):
        self.polls, self.hellos, self.results = list(polls), [], []
        self.update_to = self.add_project = self.remove_project = self.share_files = None

    def hello(self, listed, runner_status="", launch="", request_results=None, browser=""):
        self.hellos.append(listed)
        self.results.append(list(request_results or []))
        return {}

    def next_job(self, wait=25):
        from saapp_runner.client import ServerError
        if not self.polls:
            raise ServerError(401, "end of the script")
        self.share_files = self.polls.pop(0).get("share")
        return None


def runner_for(client, sharer, project_root):
    projects.add(project_root)
    said = []
    runner = Runner(client, None, say=said.append, poll_wait=0, idle_wait=0.0, roots_provider=projects.load, scanner=lambda folder: ({}, True, ""), sharer=sharer)
    return runner, said


def test_a_request_for_a_project_this_runner_serves_goes_to_the_sharer_with_its_folder_and_the_answer_is_reported(project, runner_home):
    offered = []

    class Recording:
        def offer(self, request, root):
            offered.append((request["id"], request["project_id"], root))

        def poll(self):
            return [{"id": "s1", "result": "shared"}] if offered else []

    root = project.resolve()
    client = ShareClient([{"share": {"id": "s1", "project_id": projects.project_id(root), "name": "store-api"}}, {}])
    runner, _ = runner_for(client, Recording(), project)
    runner.hello()
    runner.serve()
    assert offered == [("s1", projects.project_id(root), root)] and client.results[-1] == [{"id": "s1", "result": "shared"}]


def test_a_request_for_a_project_it_does_not_serve_gets_no_folder_and_a_runner_without_a_sharer_says_no_picker(project, runner_home):
    offered = []

    class Recording:
        def offer(self, request, root):
            offered.append(root)

        def poll(self):
            return []

    client = ShareClient([{"share": {"id": "s1", "project_id": "deadbeef", "name": "other"}}, {}])
    runner, _ = runner_for(client, Recording(), project)
    runner.hello()
    runner.serve()
    assert offered == [None]
    client = ShareClient([{"share": {"id": "s2", "project_id": "deadbeef", "name": "other"}}, {}])
    runner, _ = runner_for(client, None, project)
    runner.hello()
    runner.serve()
    assert client.results[-1] == [{"id": "s2", "result": "no_picker"}]


def test_the_client_reads_a_share_request_as_names_and_ids_only(monkeypatch):
    from saapp_runner.client import Client

    client = Client("http://localhost:1", "key")
    monkeypatch.setattr(client, "_call", lambda *a, **k: {"job": None, "share_files": {"id": "s1", "project_id": "p1", "name": "app", "path": "C:/secret", "extra": 1}})
    client.next_job(0)
    assert client.share_files == {"id": "s1", "project_id": "p1", "name": "app"}
    monkeypatch.setattr(client, "_call", lambda *a, **k: {"job": None})
    client.next_job(0)
    assert client.share_files is None
    seen = []
    monkeypatch.setattr(client, "_call", lambda method, path, body=None, **k: seen.append((method, path)) or {"status": "cleared"})
    client.workspace_sync({"project_id": "p"})
    client.workspace_clear()
    assert seen == [("POST", "/api/runner/workspace/sync"), ("DELETE", "/api/runner/workspace")]


def test_the_unshare_command_asks_the_server_to_clear_what_the_runner_sent(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_credentials", lambda: {"server": "http://localhost:1", "key": "k"})
    monkeypatch.setattr(cli.Client, "workspace_clear", lambda self: {"status": "cleared"})
    assert cli.main(["unshare-files"]) == 0 and "were removed" in capsys.readouterr().out
    monkeypatch.setattr(cli.Client, "workspace_clear", lambda self: {"status": "nothing_to_clear"})
    assert cli.main(["unshare-files"]) == 0 and "no files from this runner" in capsys.readouterr().out
    monkeypatch.setattr(cli, "load_credentials", lambda: None)
    assert cli.main(["unshare-files"]) == 1


def test_the_runner_is_started_with_the_sharer_wired_in():
    source = (Path(cli.__file__)).read_text(encoding="utf-8")
    assert "sharer=sharing.Sharing(client, say, share_dialog.new_starter())" in source
