"""The connected folder survives a flaky upload and a flaky server (backend/services/local_workspace.py): a full upload is STAGED and replaces the live copy in one swap,
a stale batch is refused, the copy expires from its last USE, and each person has their own lock. (Found 2026-10-22: a failed upload used to leave a half copy that still counted as
"connected", because the first batch wiped the working one.)"""
import threading
import time

import pytest

from backend.services import local_workspace as lw

USER = "jack@example.com"
OTHER = "kim@example.com"


def file(path, content="x = 1\n"):
    return {"path": path, "content": content}


def read(user, path):
    return (lw._files_dir(user) / path).read_text(encoding="utf-8")


def upload(files, upload_id="up1", user=USER, commit=False, reset=True, name="my-repo", deleted=None):
    return lw.apply_sync(user, name, files, deleted or [], reset, upload_id, commit)


# ---- a full upload is staged and swapped in once ------------------------------------------------------------------------------------------------------------

def test_the_live_copy_is_untouched_until_the_upload_commits():
    lw.apply_sync(USER, "old", [file("old.py", "old = 1\n")], [], True)
    upload([file("new.py", "new = 1\n")])
    assert lw.workspace_status(USER)["file_count"] == 1 and read(USER, "old.py") == "old = 1\n"
    assert not (lw._files_dir(USER) / "new.py").exists()
    result = upload([file("more.py")], reset=False, commit=True)
    assert result["committed"] is True and result["file_count"] == 2
    assert read(USER, "new.py") == "new = 1\n" and not (lw._files_dir(USER) / "old.py").exists()      # the old copy is gone, not merged


def test_a_failed_or_abandoned_upload_leaves_the_working_copy_exactly_as_it_was():
    lw.apply_sync(USER, "old", [file("a.py", "a = 1\n"), file("b.py", "b = 1\n")], [], True)
    before = lw.workspace_status(USER)
    upload([file("c.py")])                                  # starts, then the network drops: no commit ever arrives
    after = lw.workspace_status(USER)
    assert (after["file_count"], after["name"]) == (before["file_count"], before["name"]) == (2, "old")
    assert read(USER, "a.py") == "a = 1\n"


def test_a_commit_leaves_no_staging_or_old_copy_behind():
    upload([file("a.py")], commit=True)
    workspace = lw._workspace_dir(USER)
    assert not (workspace / "staging").exists() and not (workspace / "files.old").exists()
    assert lw.workspace_status(USER)["connected"] is True


def test_an_empty_folder_can_be_committed_as_the_whole_upload():
    lw.apply_sync(USER, "old", [file("a.py")], [], True)
    result = upload([], commit=True)
    assert result["committed"] is True and result["file_count"] == 0
    assert lw.workspace_status(USER) == {"connected": False}          # nothing is on the server, so there is nothing to be connected to


def test_the_limits_apply_to_the_staged_copy_too(monkeypatch):
    monkeypatch.setattr(lw, "MAX_FILE_BYTES", 10)
    result = upload([file("big.py", "x" * 50), file("ok.py", "y = 1\n")], commit=True)
    assert result["accepted"] == 1 and result["rejected"][0]["reason"] == "file too large"
    assert result["file_count"] == 1


# ---- a batch from a replaced or expired upload is refused ------------------------------------------------------------------------------------------------------

def test_a_batch_for_an_upload_that_never_started_is_stale():
    with pytest.raises(lw.StaleUpload):
        upload([file("a.py")], upload_id="ghost", reset=False)


def test_a_newer_upload_replaces_an_older_one_and_the_older_one_is_then_stale():
    upload([file("a.py")], upload_id="first")
    upload([file("b.py")], upload_id="second")                          # another tab started over
    with pytest.raises(lw.StaleUpload):
        upload([file("c.py")], upload_id="first", reset=False, commit=True)
    result = upload([], upload_id="second", reset=False, commit=True)
    assert result["committed"] and result["file_count"] == 1 and (lw._files_dir(USER) / "b.py").exists()


def test_an_unfinished_upload_expires_and_its_half_copy_is_dropped(monkeypatch):
    upload([file("a.py")])
    monkeypatch.setattr(lw, "STAGING_TTL_SECONDS", -1)
    with pytest.raises(lw.StaleUpload):
        upload([file("b.py")], reset=False, commit=True)
    assert not (lw._workspace_dir(USER) / "staging").exists()


# ---- the older, direct path still works ---------------------------------------------------------------------------------------------------------------------------

def test_a_small_delta_without_an_upload_id_is_applied_to_the_live_copy():
    lw.apply_sync(USER, "my-repo", [file("a.py", "a = 1\n"), file("b.py")], [], True)
    result = lw.apply_sync(USER, "my-repo", [file("a.py", "a = 2\n")], ["b.py"], False)
    assert result["file_count"] == 1 and read(USER, "a.py") == "a = 2\n"


def test_a_reset_without_an_upload_id_still_wipes_first():
    lw.apply_sync(USER, "x", [file("a.py")], [], True)
    lw.apply_sync(USER, "x", [file("b.py")], [], True)
    assert not (lw._files_dir(USER) / "a.py").exists() and (lw._files_dir(USER) / "b.py").exists()


def test_guests_cannot_upload_staged_either():
    with pytest.raises(PermissionError):
        lw.apply_sync("guest", "x", [], [], True, "up1", True)


# ---- the copy expires from its last USE, not its last upload ---------------------------------------------------------------------------------------------------------

def test_a_copy_in_use_does_not_expire_twelve_hours_after_its_upload(monkeypatch):
    upload([file("a.py")], commit=True)
    start = time.time()
    monkeypatch.setattr(lw.time, "time", lambda: start + lw.WORKSPACE_TTL_SECONDS - 60)
    assert lw.get_workspace_handle(USER) is not None                      # a turn uses it, which counts
    monkeypatch.setattr(lw.time, "time", lambda: start + lw.WORKSPACE_TTL_SECONDS + 3600)
    assert lw.workspace_status(USER)["connected"] is True                 # an hour past the upload's 12, but only an hour after the last use


def test_an_unused_copy_still_expires(monkeypatch):
    upload([file("a.py")], commit=True)
    later = time.time() + lw.WORKSPACE_TTL_SECONDS + 60                  # read before the clock is replaced (it is the one time module)
    monkeypatch.setattr(lw.time, "time", lambda: later)
    assert lw.workspace_status(USER) == {"connected": False}
    assert not lw._workspace_dir(USER).exists()


def test_use_is_written_at_most_every_few_minutes(monkeypatch):
    upload([file("a.py")], commit=True)
    writes = []
    real = lw._write_manifest
    monkeypatch.setattr(lw, "_write_manifest", lambda user, manifest: (writes.append(1), real(user, manifest))[1])
    for _ in range(5):
        lw.get_workspace_handle(USER)
    assert writes == []                                                    # just uploaded, so used "recently": no write per turn


# ---- one lock per person ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_each_person_has_their_own_lock_and_the_same_person_always_the_same_one():
    assert lw._user_lock(USER) is lw._user_lock(USER.upper())
    assert lw._user_lock(USER) is not lw._user_lock(OTHER)


def test_one_persons_upload_does_not_wait_for_anothers():
    entered = threading.Event()
    release = threading.Event()

    def hold():
        with lw._user_lock(USER):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    entered.wait(5)
    try:
        started = time.time()
        lw.apply_sync(OTHER, "x", [file("a.py")], [], True)
        assert time.time() - started < 2
    finally:
        release.set()
        thread.join()


# ---- the wiring --------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_endpoint_passes_the_upload_id_and_answers_a_stale_batch_with_409_and_the_chat_request_carries_the_flag():
    source = open("app.py", encoding="utf-8").read()
    assert "upload_id: Optional[str] = None" in source and "commit: bool = False" in source
    assert "payload.upload_id," in source and "payload.commit," in source
    assert "except LocalWorkspaceStaleUpload:" in source and "status_code=409" in source[source.index("except LocalWorkspaceStaleUpload:"):][:500]
    assert "folder_expected: bool = False" in source


def test_a_code_turn_that_found_no_folder_on_the_server_says_so_but_only_when_the_browser_expected_one():
    source = open("app.py", encoding="utf-8").read()
    assert "if request.folder_expected and not folder_connected and code_summary:" in source
    assert "was not on the server for this answer, so it used GitHub instead" in source
