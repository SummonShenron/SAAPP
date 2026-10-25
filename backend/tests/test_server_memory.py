"""The server memory gauge (backend/utils/server_memory.py)."""
import pathlib

from backend.utils import server_memory as sm

ROOT = pathlib.Path(__file__).resolve().parents[2]
MB = 1_000_000


def reader(files):
    return lambda path: files.get(path)


V2 = {
    "/sys/fs/cgroup/memory.max": str(512 * MB), "/sys/fs/cgroup/memory.current": str(443 * MB),
    "/sys/fs/cgroup/memory.stat": f"anon {250 * MB}\nfile {180 * MB}\ninactive_file {150 * MB}\nactive_file {30 * MB}\nkernel 8000000\n",
}


def test_cgroup_v2_splits_used_memory_into_program_and_cache_and_takes_the_reclaimable_part_out():
    box = sm.container(reader(V2))
    assert box == {"limit_mb": 512.0, "used_mb": 443.0, "anon_mb": 250.0, "file_cache_mb": 180.0, "inactive_cache_mb": 150.0, "working_set_mb": 293.0}


def test_cgroup_v1_is_read_with_its_own_names_and_no_limit_is_none():
    files = {
        "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(2 ** 63 - 4096), "/sys/fs/cgroup/memory/memory.usage_in_bytes": str(300 * MB),
        "/sys/fs/cgroup/memory/memory.stat": f"total_rss {200 * MB}\ntotal_cache {90 * MB}\ntotal_inactive_file {60 * MB}\n",
    }
    box = sm.container(reader(files))
    assert box["limit_mb"] is None and box["used_mb"] == 300.0 and box["anon_mb"] == 200.0 and box["inactive_cache_mb"] == 60.0 and box["working_set_mb"] == 240.0


def test_a_machine_with_no_container_limit_reports_nothing_instead_of_guessing():
    assert set(sm.container(reader({})).values()) == {None}
    assert set(sm.container(reader({"/sys/fs/cgroup/memory.max": "max"})).values()) == {None}


def test_the_process_size_comes_from_proc_status():
    status = "Name:\tpython\nVmHWM:\t  480000 kB\nVmRSS:\t  412000 kB\nThreads:\t12\n"
    assert sm.process(reader({"/proc/self/status": status})) == {"rss_mb": 412.0, "peak_rss_mb": 480.0}
    assert sm.process(reader({})) == {"rss_mb": None, "peak_rss_mb": None}


def test_the_temp_filesystem_is_found_by_the_longest_matching_mount():
    mounts = "overlay / overlay rw 0 0\ntmpfs /tmp tmpfs rw 0 0\ntmpfs /tmp/other tmpfs rw 0 0\n"
    assert sm._mount_type("/tmp/saapp_local_workspaces", reader({"/proc/mounts": mounts})) == "tmpfs"
    assert sm._mount_type("/var/data", reader({"/proc/mounts": mounts})) == "overlay"
    assert sm._mount_type("/x", reader({})) is None


def test_what_the_app_holds_is_counted_without_naming_anyone(tmp_path, monkeypatch):
    from backend.services import local_workspace

    base = tmp_path / "saapp_local_workspaces"
    (base / "person_one" / "files").mkdir(parents=True)
    (base / "person_one" / "files" / "a.py").write_bytes(b"x" * 2000)
    (base / "person_two").mkdir()
    monkeypatch.setattr(local_workspace, "WORKSPACE_BASE_DIR", base)
    held = sm.held_by_app({"u::1": [1, 2, 3], "u::2": [1]})
    assert held["conversations_in_memory"] == 2 and held["messages_in_memory"] == 4
    assert held["folder_people"] == 2 and held["folder_files"] == 1 and held["folder_mb"] == 0.0
    assert not any(isinstance(v, str) and "person" in v for v in held.values())      # no names anywhere in what is reported


def test_the_snapshot_never_raises_and_has_every_part(monkeypatch):
    snap = sm.snapshot({})
    assert set(snap) == {"container", "process", "held", "sandbox", "reclaimable_share_of_used"}
    monkeypatch.setattr(sm, "container", lambda read=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert sm.snapshot({})["container"] == {}


def test_the_sandbox_verdicts_compare_headroom_with_and_without_the_reclaimable_cache(monkeypatch):
    from backend.services import sandbox_tests

    original = sm.container
    monkeypatch.setattr(sm, "container", lambda read=None: original(reader(V2)))
    monkeypatch.setattr(sandbox_tests, "headroom_mb", lambda count_cache=False: 69.0 if count_cache else 219.0)      # 512 limit: minus 443 used (old), or minus the 293 working set (now)
    box = sm.snapshot({})["sandbox"]
    assert box["headroom_now_mb"] == 219.0 and box["headroom_counting_cache_mb"] == 69.0
    if box["compiled_cache_present"]:
        assert box["would_run_now"] is True and box["would_run_counting_cache"] is False


def test_the_endpoint_is_admin_only_and_the_page_reads_it():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app_source[app_source.index('@app.get("/api/admin/server-memory")'):app_source.index('@app.get("/api/admin/sonic-health")')]
    assert "Depends(require_global_admin)" in block and "server_memory.snapshot, chat_sessions" in block
