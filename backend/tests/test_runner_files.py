"""A person's runner sending a project's files so Sonic can read them (backend/utils/runner_files.py, runner_jobs.request_files; plan in docs/runner-files-plan.md).

The server only ASKS; the runner shows a window on the person's computer; batches are taken only while a request that was handed to the runner is open. Every file still goes through apply_sync."""
import base64
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "runner" / "src"))
from saapp_runner import workspace_sync  # noqa: E402

import app as app_module  # noqa: E402  (imported here, at collection, as the other tests that need the app's request classes do: importing it inside a test starts services)

from backend.services import local_workspace as lw
from backend.tests.fake_runner_db import FakeDB
from backend.utils import runner_files as rf
from backend.utils import runner_jobs as rj
from backend.utils import self_counters as sc

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
USER = "jack@example.com"
PA, PB = "a1b2c3d4", "e5f6a7b8"


def later(**delta):
    return NOW + timedelta(**delta)


def runner(db, username=USER, projects=None):
    code = rj.create_pairing(db, username, "laptop", NOW)["code"]
    record = rj.authenticate(db, rj.exchange_pairing(db, code, NOW)["key"])
    rj.hello(db, record, {"version": "0.2.2", "projects": projects if projects is not None else [{"id": PA, "name": "store-api", "status": "ok", "checks": []}, {"id": PB, "name": "other", "status": "ok", "checks": []}]}, NOW)
    return record


def text(path, body="x = 1\n"):
    return {"path": path, "text_b64": base64.b64encode(body.encode()).decode()}


@pytest.fixture
def counted(monkeypatch):
    seen = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: seen.append(dict(counts)))
    return seen


def asked_and_delivered(db, record, project=PA, at=NOW):
    asked = rj.request_files(db, USER, record["_id"], project, at)
    rj.api_next(db, record, at + timedelta(seconds=1))
    return asked


# ---- asking -------------------------------------------------------------------------------------------------------------------------------

def test_the_person_asks_for_a_project_by_its_id_and_a_repeat_returns_the_same_request():
    db = FakeDB()
    record = runner(db)
    one = rj.request_files(db, USER, record["_id"], PA, NOW)
    assert one["kind"] == "files" and one["project_id"] == PA and one["name"] == "store-api" and one["state"] == "waiting"
    assert rj.request_files(db, USER, record["_id"], PA, later(seconds=5))["id"] == one["id"]
    assert rj.request_files(db, USER, record["_id"], PB, later(seconds=5))["id"] != one["id"]


def test_a_runner_that_is_not_theirs_or_a_project_it_does_not_serve_is_refused():
    db = FakeDB()
    record = runner(db)
    assert rj.request_files(db, "someone-else@example.com", record["_id"], PA, NOW) is None
    assert rj.request_files(db, USER, "no-such-runner", PA, NOW) is None
    with pytest.raises(rj.RunnerError) as error:
        rj.request_files(db, USER, record["_id"], "deadbeef", NOW)
    assert error.value.code == "no_project"


def test_only_a_few_requests_wait_at_once():
    db = FakeDB()
    record = runner(db, projects=[{"id": f"{i:08x}", "name": f"p{i}", "status": "ok", "checks": []} for i in range(5)])
    for i in range(rj.MAX_OPEN_REQUESTS):
        rj.request_files(db, USER, record["_id"], f"{i:08x}", NOW)
    with pytest.raises(rj.RunnerError) as error:
        rj.request_files(db, USER, record["_id"], f"{rj.MAX_OPEN_REQUESTS:08x}", NOW)
    assert error.value.code == "limit"


# ---- the runner hears about it ---------------------------------------------------------------------------------------------------------------

def test_the_runner_is_handed_a_files_request_once_with_the_project_id_and_never_as_an_add_project():
    db = FakeDB()
    record = runner(db)
    asked = rj.request_files(db, USER, record["_id"], PA, NOW)
    first = rj.api_next(db, record, later(seconds=1))
    assert first["share_files"] == {"id": asked["id"], "project_id": PA, "name": "store-api"} and first["add_project"] is None
    assert "share_files" not in rj.api_next(db, record, later(seconds=2))                  # given once: the window must not appear again and again


def test_an_answer_with_nothing_to_share_has_the_shape_it_always_had_and_an_add_project_request_is_unchanged():
    db = FakeDB()
    record = runner(db)
    assert rj.api_next(db, record, later(seconds=1)) == {"job": None, "update": None, "add_project": None, "remove_project": None}
    added = rj.request_project(db, USER, record["_id"], "third-app", later(seconds=2))
    answer = rj.api_next(db, record, later(seconds=3))
    assert answer["add_project"] == {"id": added["id"], "name": "third-app"} and "share_files" not in answer


def test_both_kinds_of_request_can_wait_and_each_is_handed_over_as_its_own_kind():
    db = FakeDB()
    record = runner(db)
    added = rj.request_project(db, USER, record["_id"], "third-app", NOW)
    shared = rj.request_files(db, USER, record["_id"], PA, NOW)
    answer = rj.api_next(db, record, later(seconds=1))
    assert answer["add_project"]["id"] == added["id"] and answer["share_files"]["id"] == shared["id"]


def test_a_files_request_is_not_called_added_just_because_a_project_of_that_name_exists_and_shared_is_a_result():
    db = FakeDB()
    record = runner(db)
    asked = rj.request_files(db, USER, record["_id"], PA, NOW)
    rj.api_next(db, record, later(seconds=1))
    projects = [{"id": PA, "name": "store-api", "status": "ok", "checks": []}]
    rj.hello(db, record, {"version": "0.2.2", "projects": projects}, later(seconds=2))
    state = lambda at: [r for r in rj.runners_for(db, USER, at)[0]["requests"] if r["id"] == asked["id"]][0]["state"]
    assert state(later(seconds=3)) == "asked"
    rj.hello(db, record, {"version": "0.2.2", "projects": projects, "request_results": [{"id": asked["id"], "result": "shared"}]}, later(seconds=4))
    assert state(later(seconds=5)) == "shared" and "shared" in rj.REQUEST_RESULTS


def test_the_page_sees_a_files_request_with_its_kind_and_project_and_an_add_request_looks_as_before():
    db = FakeDB()
    record = runner(db)
    rj.request_files(db, USER, record["_id"], PA, NOW)
    rj.request_project(db, USER, record["_id"], "third-app", NOW)
    seen = rj.runners_for(db, USER, later(seconds=1))[0]["requests"]
    files = [r for r in seen if r.get("kind") == "files"]
    plain = [r for r in seen if r.get("kind") is None]
    assert len(files) == 1 and files[0]["project_id"] == PA and set(plain[0]) == {"id", "name", "state"}


# ---- taking the files ---------------------------------------------------------------------------------------------------------------------------

def test_files_are_taken_only_while_a_request_handed_to_the_runner_is_open(counted):
    db = FakeDB()
    record = runner(db)
    body = {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("app.py")], "commit": True}
    with pytest.raises(rj.RunnerError) as nothing_asked:
        rf.api_sync(db, record, body, NOW)
    assert nothing_asked.value.code == "not_asked"
    rj.request_files(db, USER, record["_id"], PA, NOW)                                      # asked, but not yet handed to the runner (no window has been shown)
    with pytest.raises(rj.RunnerError) as not_delivered:
        rf.api_sync(db, record, body, later(seconds=1))
    assert not_delivered.value.code == "not_asked"
    rj.api_next(db, record, later(seconds=2))
    done = rf.api_sync(db, record, body, later(seconds=3))
    assert done["accepted"] == 1 and done["file_count"] == 1
    assert [c for c in counted if "runner_files_refused" in c] == [{"runner_files_refused": 1}] * 2 and {"runner_files_ok": 1} in counted


def test_another_projects_request_and_an_answered_or_too_old_one_let_nothing_in():
    db = FakeDB()
    record = runner(db)
    asked = asked_and_delivered(db, record, PB)
    body = {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("app.py")], "commit": True}
    with pytest.raises(rj.RunnerError):
        rf.api_sync(db, record, body, later(seconds=5))                                    # the request is for the other project
    asked_a = asked_and_delivered(db, record, PA, later(seconds=10))
    with pytest.raises(rj.RunnerError):
        rf.api_sync(db, record, body, later(minutes=rf.UPLOAD_MINUTES + 2))                # too long after it was handed over
    rj.hello(db, record, {"version": "0.2.2", "projects": [{"id": PA, "name": "store-api", "status": "ok", "checks": []}, {"id": PB, "name": "other", "status": "ok", "checks": []}],
                          "request_results": [{"id": asked_a["id"], "result": "declined"}]}, later(seconds=20))
    with pytest.raises(rj.RunnerError):
        rf.api_sync(db, record, body, later(seconds=21))                                   # the person said no on their computer
    assert asked["id"] != asked_a["id"]


def test_a_project_the_runner_does_not_serve_is_refused():
    db = FakeDB()
    record = runner(db)
    asked_and_delivered(db, record, PA)
    with pytest.raises(rj.RunnerError) as error:
        rf.api_sync(db, record, {"project_id": "deadbeef", "files": []}, later(seconds=5))
    assert error.value.code == "no_project"


def test_the_snapshot_is_named_for_the_project_says_it_came_from_the_runner_and_secrets_are_still_refused():
    db = FakeDB()
    record = runner(db)
    asked_and_delivered(db, record)
    files = [text("src/app.py"), text(".env", "KEY=secret"), text("id_rsa", "private"), text("node_modules/x.js", "x"), text("config.example", "A=1")]
    result = rf.api_sync(db, record, {"project_id": PA, "reset": True, "upload_id": "u1", "files": files, "commit": True}, later(seconds=5))
    assert result["accepted"] == 2 and {r["path"] for r in result["rejected"]} == {".env", "id_rsa", "node_modules/x.js"}
    status = lw.workspace_status(USER)
    assert status["connected"] and status["name"] == "store-api" and status["source"] == "runner" and status["file_count"] == 2
    assert sorted(lw.snapshot_paths(USER)) == ["config.example", "src/app.py"]


def test_batches_are_staged_until_the_commit_and_a_later_delta_keeps_the_source():
    db = FakeDB()
    record = runner(db)
    asked_and_delivered(db, record)
    rf.api_sync(db, record, {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("a.py")], "commit": False}, later(seconds=5))
    assert lw.workspace_status(USER)["connected"] is False                                 # nothing is live until the last batch says commit
    rf.api_sync(db, record, {"project_id": PA, "upload_id": "u1", "files": [text("b.py")], "commit": True}, later(seconds=6))
    assert lw.workspace_status(USER)["file_count"] == 2
    with pytest.raises(rj.RunnerError) as stale:
        rf.api_sync(db, record, {"project_id": PA, "upload_id": "other", "files": [text("c.py")], "commit": True}, later(seconds=7))
    assert stale.value.code == "stale"


def test_a_guest_owner_gets_nothing_and_the_other_source_replaces_the_copy():
    db = FakeDB()
    guest = runner(db, username="guest_bty")
    asked = rj.request_files(db, "guest_bty", guest["_id"], PA, NOW)
    rj.api_next(db, guest, later(seconds=1))
    with pytest.raises(rj.RunnerError) as error:
        rf.api_sync(db, guest, {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("a.py")], "commit": True}, later(seconds=2))
    assert error.value.code == "not_asked" and asked["project_id"] == PA
    record = runner(db)
    asked_and_delivered(db, record)
    rf.api_sync(db, record, {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("a.py")], "commit": True}, later(seconds=5))
    assert lw.workspace_status(USER)["source"] == "runner"
    lw.apply_sync(USER, "my-folder", [{"path": "b.py", "content": "y = 2\n"}], [], True)                  # the browser connects a folder: it replaces the copy
    assert lw.workspace_status(USER)["source"] == "browser" and lw.workspace_status(USER)["name"] == "my-folder"


def test_a_folder_connected_by_the_browser_still_reads_as_the_browser_and_an_old_manifest_has_no_source():
    lw.apply_sync(USER, "my-folder", [{"path": "a.py", "content": "x = 1\n"}], [], False)
    assert lw.workspace_status(USER)["source"] == "browser" and lw.snapshot_source(USER) == "browser"
    assert lw.snapshot_source("nobody@example.com") is None


# ---- taking them back -----------------------------------------------------------------------------------------------------------------------------

def test_the_runner_can_take_back_only_files_it_sent():
    db = FakeDB()
    record = runner(db)
    lw.apply_sync(USER, "my-folder", [{"path": "a.py", "content": "x = 1\n"}], [], False)
    assert rf.api_clear(db, record) == {"status": "nothing_to_clear"} and lw.workspace_status(USER)["connected"]
    asked_and_delivered(db, record)
    rf.api_sync(db, record, {"project_id": PA, "reset": True, "upload_id": "u1", "files": [text("a.py")], "commit": True}, later(seconds=5))
    assert rf.api_clear(db, record) == {"status": "cleared"} and lw.workspace_status(USER)["connected"] is False


# ---- wiring ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_counters_are_registered_and_every_one_is_incremented_in_the_module():
    assert set(sc.RUNNER_FILES_KEYS) == {"runner_files_ok", "runner_files_refused", "runner_files_failed"} and set(sc.RUNNER_FILES_KEYS) <= sc.KEYS
    source = open(rf.__file__, encoding="utf-8").read()
    for key in sc.RUNNER_FILES_KEYS:
        assert f'_count("{key}")' in source, key


def test_the_endpoints_are_thin_and_the_poll_wakes_for_a_files_request():
    source = open("app.py", encoding="utf-8").read()
    assert '@app.post("/api/runner/runners/{runner_id}/share-files")' in source and '@app.post("/api/runner/workspace/sync")' in source and '@app.delete("/api/runner/workspace")' in source
    assert 'answer.get("share_files")' in source
    assert "runner_files.api_sync" in source and "runner_jobs.request_files" in source and "async def runner_workspace_sync(payload: RunnerWorkspaceSync, key = Depends(get_runner_key))" in source
    assert rj.STATUS_FOR["not_asked"] == 403 and rj.STATUS_FOR["stale"] == 409


# ---- the two halves together --------------------------------------------------------------------------------------------------------------------

def test_what_the_real_runner_sends_is_what_the_real_server_takes(tmp_path):
    """The runner's own scan and upload, its bodies read by the app's own request model and handed to api_sync as the endpoint does: no hand-built payloads on either side."""
    project = tmp_path / "store-api"
    for relative, content in {"src/app.py": b"print('hi')\r\nsecond\r\n", "README.md": b"# readme\n", ".env": b"KEY=secret", "keys/a.pem": b"private", "node_modules/x/i.js": b"x", "bad.docx": b"not a real docx"}.items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_bytes(content)
    db = FakeDB()
    record = runner(db)
    asked_and_delivered(db, record)
    seen = []

    class AdapterClient:
        def workspace_sync(self, body):
            model = app_module.RunnerWorkspaceSync(**body)                                           # the app's own request class: field names and types must fit
            seen.append(model)
            return rf.api_sync(db, record, {**model.dict(), "files": [f.dict() for f in model.files]}, later(seconds=5))

    outcome = workspace_sync.upload(AdapterClient(), PA, project, workspace_sync.scan(project))
    assert outcome.ok and seen and all(m.project_id == PA for m in seen)
    status = lw.workspace_status(USER)
    assert status["connected"] and status["source"] == "runner" and status["name"] == "store-api"
    paths = sorted(lw.snapshot_paths(USER))
    assert paths == ["README.md", "src/app.py"], paths                                        # the secret, the key, node_modules and the broken Word file are all absent
    handle = lw.get_workspace_handle(USER)
    assert (pathlib.Path(handle.root) / "src" / "app.py").read_bytes() == b"print('hi')\r\nsecond\r\n"          # exact bytes, CRLF kept


def test_a_runner_with_a_forged_secret_in_the_body_is_still_refused_by_the_server():
    db = FakeDB()
    record = runner(db)
    asked_and_delivered(db, record)
    forged = {"project_id": PA, "reset": True, "upload_id": "u1", "commit": True, "files": [text(".env", "KEY=secret"), text("config/secrets.json", "{}"), text("deploy/key.pem", "x"), text("ok.py")]}
    result = rf.api_sync(db, record, forged, later(seconds=5))
    assert result["accepted"] == 1 and {r["path"] for r in result["rejected"]} == {".env", "config/secrets.json", "deploy/key.pem"} and lw.snapshot_paths(USER) == ["ok.py"]


def test_a_runner_too_old_to_send_files_is_told_to_update_instead_of_left_waiting():
    db = FakeDB()
    code = rj.create_pairing(db, USER, "laptop", NOW)["code"]
    record = rj.authenticate(db, rj.exchange_pairing(db, code, NOW)["key"])
    rj.hello(db, record, {"version": "0.2.1", "projects": [{"id": PA, "name": "store-api", "status": "ok", "checks": []}]}, NOW)
    with pytest.raises(rj.RunnerError) as error:
        rj.request_files(db, USER, record["_id"], PA, NOW)
    assert error.value.code == "old_runner" and "0.2.2" in error.value.message and rj.STATUS_FOR["old_runner"] == 409 and rj.FILES_MIN_VERSION == (0, 2, 2)
    rj.hello(db, record, {"version": "0.2.2", "projects": [{"id": PA, "name": "store-api", "status": "ok", "checks": []}]}, later(seconds=1))
    assert rj.request_files(db, USER, record["_id"], PA, later(seconds=2))["state"] == "waiting"
