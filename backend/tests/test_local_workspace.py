"""The connected-local-folder snapshot (backend/services/local_workspace.py): upload safety, limits,
lifecycle, and its wiring into tool_agent_node in place of the GitHub tarball."""
import asyncio
import functools
import os
import time
from pathlib import Path

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_workspace as lw
from backend.services import repo_checkout as rc

USER = "jack@example.com"


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _file(path, content="x = 1\n"):
    return {"path": path, "content": content}


def _sync(files, deleted=None, reset=False, user=USER, name="my-repo"):
    return lw.apply_sync(user, name, files, deleted or [], reset)


# ---------------------------------------------------------------------------
# Path safety and what is never stored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "", None, 5, "/etc/passwd", "../outside.py", "a/../../b.py", "a//b.py", "./a.py", "a\\b.py",
    "C:/Windows/x.py", "a/./b.py", "x" * 600, "bad\x00name.py",
])
def test_unsafe_paths_are_refused(raw):
    assert lw.safe_relative_path(raw) is None


def test_ordinary_nested_path_is_accepted_unchanged():
    assert lw.safe_relative_path("src/pages/Chat.tsx") == "src/pages/Chat.tsx"


def test_unsafe_and_secret_paths_are_rejected_without_aborting_the_batch(tmp_path):
    result = _sync([
        _file("src/ok.py"),
        _file("../escape.py"),
        _file(".env", "API_KEY=abc"),
        _file("config/server.pem", "-----BEGIN"),
        _file("node_modules/pkg/index.js"),
        _file("app/.env.production", "X=1"),
    ])

    assert result["accepted"] == 1
    assert {r["path"] for r in result["rejected"]} == {
        "../escape.py", ".env", "config/server.pem", "node_modules/pkg/index.js", "app/.env.production",
    }
    stored = Path(lw.get_workspace_handle(USER).root)
    assert (stored / "src/ok.py").exists()
    assert not (stored / ".env").exists()


def test_env_template_files_are_allowed():
    result = _sync([_file(".env.example", "API_KEY=changeme")])
    assert result["accepted"] == 1


def test_non_text_content_is_rejected():
    result = _sync([{"path": "a.py", "content": 123}])
    assert result["accepted"] == 0
    assert result["rejected"][0]["reason"] == "content is not text"


def test_oversized_file_is_rejected(monkeypatch):
    monkeypatch.setattr(lw, "MAX_FILE_BYTES", 10)
    result = _sync([_file("big.py", "x" * 50), _file("small.py", "ok")])
    assert result["accepted"] == 1
    assert result["rejected"][0]["reason"] == "file too large"


def test_total_size_and_file_count_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(lw, "MAX_TOTAL_BYTES", 10)
    result = _sync([_file("a.py", "x" * 8), _file("b.py", "x" * 8)])
    assert result["accepted"] == 1
    assert result["rejected"][0]["reason"] == "snapshot size limit reached"

    monkeypatch.setattr(lw, "MAX_TOTAL_BYTES", 10_000)
    monkeypatch.setattr(lw, "MAX_FILE_COUNT", 1)
    result = _sync([_file("c.py")], user="other@example.com")
    result = _sync([_file("d.py")], user="other@example.com")
    assert result["rejected"][0]["reason"] == "too many files"


# ---------------------------------------------------------------------------
# Sync semantics: delta updates, deletes, reset
# ---------------------------------------------------------------------------

def test_second_batch_adds_to_the_snapshot_and_overwrites_changed_files():
    _sync([_file("a.py", "old"), _file("b.py", "keep")], reset=True)
    _sync([_file("a.py", "new"), _file("c.py", "added")])

    root = Path(lw.get_workspace_handle(USER).root)
    assert (root / "a.py").read_text() == "new"
    assert (root / "b.py").read_text() == "keep"
    assert (root / "c.py").read_text() == "added"
    assert lw.workspace_status(USER)["file_count"] == 3


def test_deleted_paths_are_removed_and_uncounted():
    _sync([_file("a.py"), _file("b.py")], reset=True)
    _sync([], deleted=["a.py", "../not-a-real-target.py"])

    root = Path(lw.get_workspace_handle(USER).root)
    assert not (root / "a.py").exists()
    assert lw.workspace_status(USER)["file_count"] == 1


def test_reset_replaces_the_whole_previous_snapshot():
    _sync([_file("old.py")], reset=True)
    _sync([_file("new.py")], reset=True)

    root = Path(lw.get_workspace_handle(USER).root)
    assert not (root / "old.py").exists()
    assert (root / "new.py").exists()


def test_a_path_whose_parent_is_an_existing_file_is_rejected_not_raised():
    _sync([_file("a.py")], reset=True)
    result = _sync([_file("a.py/inside.py")])
    assert result["rejected"][0]["reason"] == "could not be written"


# ---------------------------------------------------------------------------
# Ownership: per-user, never guests
# ---------------------------------------------------------------------------

def test_snapshots_are_isolated_per_user():
    _sync([_file("mine.py")], reset=True)
    assert lw.workspace_status("someone-else@example.com") == {"connected": False}
    assert lw.get_workspace_handle("someone-else@example.com") is None


@pytest.mark.parametrize("guest", ["guest-recruiter@example.com", "guest_bty", "guest"])
def test_guests_cannot_sync_and_never_get_a_handle(guest):
    with pytest.raises(PermissionError):
        lw.apply_sync(guest, "repo", [_file("a.py")], [], True)
    assert lw.get_workspace_handle(guest) is None


def test_no_username_means_no_workspace():
    assert lw.get_workspace_handle(None) is None
    assert lw.workspace_status("") == {"connected": False}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def test_status_reports_name_size_and_count():
    _sync([_file("a.py", "12345"), _file("b.py", "123")], reset=True, name="local-rag")
    status = lw.workspace_status(USER)
    assert status["connected"] is True
    assert status["name"] == "local-rag"
    assert status["file_count"] == 2
    assert status["total_bytes"] == 8


def test_expired_snapshot_is_deleted_and_reads_as_disconnected(monkeypatch):
    _sync([_file("a.py")], reset=True)
    workspace_dir = Path(lw._workspace_dir(USER))
    assert workspace_dir.exists()

    later = time.time() + lw.WORKSPACE_TTL_SECONDS + 60
    monkeypatch.setattr(lw.time, "time", lambda: later)

    assert lw.workspace_status(USER) == {"connected": False}
    assert not workspace_dir.exists()


def test_clear_workspace_disconnects():
    _sync([_file("a.py")], reset=True)
    lw.clear_workspace(USER)
    assert lw.get_workspace_handle(USER) is None


def test_handle_is_not_owned_so_per_turn_cleanup_never_deletes_the_snapshot():
    _sync([_file("a.py")], reset=True)
    handle = lw.get_workspace_handle(USER)

    rc.cleanup_checkout(handle)

    assert (Path(handle.root) / "a.py").exists()


def test_downloaded_checkouts_are_still_owned_and_cleaned_up(tmp_path):
    tempdir = tmp_path / "dl"
    tempdir.mkdir()
    rc.cleanup_checkout(rc.CheckoutHandle(root=str(tempdir), tempdir=str(tempdir)))
    assert not tempdir.exists()


# ---------------------------------------------------------------------------
# tool_agent_node: reads come from the snapshot, not the tarball
# ---------------------------------------------------------------------------

@run_async
async def test_tool_agent_node_reads_the_connected_folder_instead_of_downloading_a_tarball(monkeypatch):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    _sync([_file("src/Chat.tsx", "const overflowItems = [\n];\n")], reset=True, user="jack", name="local-rag")

    def _no_tarball(*a, **k):
        raise AssertionError("the tarball must not be downloaded when a local folder is connected")

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", _no_tarball)
    _setup_github_repo(monkeypatch)
    captured = {}

    async def fake_run_react_loop(**kwargs):
        captured.update(kwargs)
        captured["found"] = await kwargs["declaration_lookup"](["overflowItems"])
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("move the controls", username="jack"))

    assert captured["found"]["overflowItems"][0]["path"] == "src/Chat.tsx"
    assert "LOCAL WORKSPACE" in captured["schema"]
    assert "'local-rag'" in captured["schema"]
    # The snapshot outlives the turn.
    assert (Path(lw.get_workspace_handle("jack").root) / "src/Chat.tsx").exists()


@run_async
async def test_tool_agent_node_without_a_connected_folder_has_no_workspace_note(monkeypatch):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    captured = {}

    async def fake_run_react_loop(**kwargs):
        captured.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("move the controls", username="jack"))

    assert "LOCAL WORKSPACE" not in captured["schema"]


# ---------------------------------------------------------------------------
# Regression: observed in production use on a Windows backend — a CRLF file was written through
# text mode, so every "\r\n" landed on disk as "\r\r\n" and read back with a blank line after each
# line (a 1830-line app.py looked like 3661 lines, so Sonic's line ranges pointed at the wrong code).
# ---------------------------------------------------------------------------

def test_files_are_stored_as_the_exact_bytes_uploaded_including_crlf():
    content = "line one\r\nline two\r\n"
    _sync([_file("src/crlf.py", content)], reset=True)

    stored = Path(lw.get_workspace_handle(USER).root) / "src/crlf.py"

    assert stored.read_bytes() == content.encode("utf-8")
    assert stored.read_text(encoding="utf-8").splitlines() == ["line one", "line two"]


def test_a_snapshot_written_by_an_older_storage_format_reads_as_disconnected():
    _sync([_file("a.py")], reset=True)
    manifest_path = lw._manifest_path(USER)
    manifest = lw._read_manifest(USER)
    manifest.pop("version")
    lw._write_manifest(USER, manifest)

    assert lw.workspace_status(USER) == {"connected": False}
    assert lw.get_workspace_handle(USER) is None


def test_a_delta_sync_onto_an_older_format_snapshot_wipes_the_stale_files_first():
    _sync([_file("old_corrupt.py", "bad\r\r\n")], reset=True)
    manifest = lw._read_manifest(USER)
    manifest.pop("version")
    lw._write_manifest(USER, manifest)

    _sync([_file("fresh.py", "ok\n")])  # not a reset — as a browser delta sync would be

    root = Path(lw.get_workspace_handle(USER).root)
    assert not (root / "old_corrupt.py").exists()
    assert (root / "fresh.py").exists()
    assert lw.workspace_status(USER)["file_count"] == 1
