"""Running Python tests against a copy of the person's folder inside the WASI sandbox (backend/services/sandbox_tests.py)."""
import asyncio
import pathlib

import pytest

from backend.services import local_workspace as lw
from backend.services import python_sandbox
from backend.services import sandbox_tests as st

ROOT = pathlib.Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not python_sandbox.SANDBOX_WASM_PATH.exists(), reason="the sandbox interpreter is not downloaded (backend/sandbox/README.md)")

MATHY = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
BUGGY = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x)) + 1\n"
TEST_MATHY = (
    "import unittest\nfrom mathy import clamp\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_low(self):\n        self.assertEqual(clamp(-1, 0, 5), 0)\n"
    "    def test_high(self):\n        self.assertEqual(clamp(9, 0, 5), 5)\n"
)


def run(coro):
    return asyncio.run(coro)


def connect(user, files):
    lw.apply_sync(user, "demo", [{"path": p, "content": c} for p, c in files.items()], [], True)


# ---- the runs ---------------------------------------------------------------------------------------------------------------------------------

def test_passing_tests_are_reported_with_how_many_ran():
    connect("t1", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    result = run(st.run_tests("t1", ["tests/test_mathy.py"]))
    assert result["ran"] == 2 and result["failures"] == 0 and result["errors"] == 0 and result["finished"]
    text = st.format_result(result)
    assert text.startswith("SANDBOX TESTS PASSED against the folder as it is now: 2 test(s) ran, 0 failed, 0 errored.") and "only covers what these tests exercise" in text


def test_a_failing_test_comes_back_with_the_real_traceback():
    connect("t2", {"mathy.py": BUGGY, "tests/test_mathy.py": TEST_MATHY})
    result = run(st.run_tests("t2", ["tests/test_mathy.py"]))
    assert result["failures"] == 2
    text = st.format_result(result)
    assert text.startswith("SANDBOX TESTS FAILED") and "AssertionError: 6 != 5" in text and "test_high" in text


def test_proposed_edits_are_tested_as_if_applied_and_nothing_is_written_to_the_snapshot():
    connect("t3", {"mathy.py": BUGGY})
    overlay = {"mathy.py": MATHY, ".sonic/tests/test_mathy.py": TEST_MATHY}
    result = run(st.run_tests("t3", [".sonic/tests/test_mathy.py"], overlay))
    assert result["ran"] == 2 and result["failures"] == 0
    assert "AFTER your proposed edits (nothing was changed or shown)" in st.format_result(result, with_edits=True)
    snapshot = pathlib.Path(lw.get_workspace_handle("t3").root)
    assert (snapshot / "mathy.py").read_text(encoding="utf-8") == BUGGY and not (snapshot / ".sonic").exists()


def test_code_that_needs_a_package_says_the_sandbox_has_only_the_standard_library():
    connect("t4", {"uses_requests.py": "import requests\n", "tests/test_uses.py": "import unittest\nimport uses_requests\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"})
    result = run(st.run_tests("t4", ["tests/test_uses.py"]))
    text = st.format_result(result)
    assert text.startswith("SANDBOX TESTS COULD NOT RUN") and "ModuleNotFoundError" in text and "only Python's standard library" in text and "does not mean the code itself is wrong" in text


def test_a_runaway_test_is_stopped_by_the_compute_budget(monkeypatch):
    monkeypatch.setattr(st, "TEST_FUEL", 1_800_000_000)
    connect("t5", {"tests/test_spin.py": "import unittest\n\nclass T(unittest.TestCase):\n    def test_spin(self):\n        while True:\n            pass\n"})
    result = run(st.run_tests("t5", ["tests/test_spin.py"]))
    assert result["out_of_fuel"] is True
    assert st.format_result(result).startswith("SANDBOX TESTS COULD NOT RUN") and "compute budget" in st.format_result(result)


def test_a_test_cannot_write_into_the_mounted_copy_or_see_anything_outside_it():
    code = ("import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_write(self):\n        with self.assertRaises(OSError):\n            open('/work/planted.txt', 'w').write('x')\n"
            "    def test_outside(self):\n        import os\n        with self.assertRaises(OSError):\n            os.listdir('/')\n"
            "    def test_net(self):\n        import socket\n        with self.assertRaises(Exception):\n            socket.create_connection(('example.com', 80), 1)\n")
    connect("t6", {"tests/test_walls.py": code})
    result = run(st.run_tests("t6", ["tests/test_walls.py"]))
    assert result["ran"] == 3 and result["failures"] == 0 and result["errors"] == 0, result["output"]          # all three walls hold: no writing, nothing outside /work, no network
    assert not (pathlib.Path(lw.get_workspace_handle("t6").root) / "planted.txt").exists()


def test_one_persons_run_never_sees_another_persons_folder():
    connect("alice", {"secret_module.py": "VALUE = 'alice only'\n"})
    connect("bob", {"tests/test_peek.py": "import unittest\n\nclass T(unittest.TestCase):\n    def test_peek(self):\n        import secret_module\n"})
    result = run(st.run_tests("bob", ["tests/test_peek.py"]))
    assert result["errors"] == 1 and "ModuleNotFoundError" in result["output"]


# ---- the doors that close before anything runs -------------------------------------------------------------------------------------------------

def test_a_missing_folder_bad_paths_a_missing_test_file_and_too_many_files_are_refused_in_words():
    assert run(st.run_tests("nobody", ["tests/test_x.py"]))["error"].startswith("No local folder is connected")
    connect("t7", {"mathy.py": MATHY})
    for bad in (["../escape.py"], ["tests/notpython.txt"], [], "", ["/abs/test.py"], ["a/../b.py"]):
        assert "error" in run(st.run_tests("t7", bad))
    assert "No such test file" in run(st.run_tests("t7", ["tests/test_missing.py"]))["error"]
    assert "at most 5" in run(st.run_tests("t7", [f"t{n}.py" for n in range(6)]))["error"]
    assert st.format_result({"error": "x"}) == "ERROR: x"


def test_a_busy_sandbox_says_so_instead_of_queueing(monkeypatch):
    connect("t8", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    taken = [st._slots.acquire(blocking=False) for _ in range(st.MAX_CONCURRENT)]
    try:
        assert "busy" in run(st.run_tests("t8", ["tests/test_mathy.py"]))["error"]
    finally:
        for got in taken:
            if got:
                st._slots.release()
    assert run(st.run_tests("t8", ["tests/test_mathy.py"]))["ran"] == 2                                                       # and it works again once a slot is free


def test_only_source_and_small_data_files_are_copied_and_a_huge_folder_is_refused(monkeypatch, tmp_path):
    connect("t9", {"mathy.py": MATHY, "logo.png.txt": "x", "big.py": "x = 1\n" + "# pad\n" * 10, "tests/test_mathy.py": TEST_MATHY})
    monkeypatch.setattr(st, "MAX_COPY_FILES", 2)
    assert "too large to copy" in run(st.run_tests("t9", ["tests/test_mathy.py"]))["error"]
    monkeypatch.setattr(st, "MAX_COPY_FILES", 3000)
    dest = tmp_path / "copy"
    dest.mkdir()
    st._build_tree("t9", str(dest), {"../outside.py": "x", ".env": "SECRET=1", "ok/new.py": "y = 2\n"})
    names = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert "ok/new.py" in names and "mathy.py" in names and ".env" not in names and not (tmp_path / "outside.py").exists()


# ---- pure parts and wiring ------------------------------------------------------------------------------------------------------------------------

def test_the_output_is_parsed_trimmed_and_the_summary_line_is_not_shown():
    out = "x" * 9000 + "\n\n::RESULT::" + '{"ran": 3, "failures": 1, "errors": 0, "skipped": 1, "load_errors": []}\n'
    parsed = st.parse_output(out, "", False)
    assert parsed["ran"] == 3 and parsed["failures"] == 1 and parsed["skipped"] == 1 and len(parsed["output"]) < 6100 and "::RESULT::" not in parsed["output"] and parsed["finished"]
    assert st.parse_output("", "Traceback", False)["finished"] is False and "NO TESTS FOUND" in st.format_result(st.parse_output("\n::RESULT::" + '{"ran": 0}', "", False))


def test_the_module_logs_only_counts_and_mounts_the_copy_read_only():
    import ast
    source = (ROOT / "backend" / "services" / "sandbox_tests.py").read_text(encoding="utf-8")
    worker = (ROOT / "backend" / "sandbox" / "sandbox_worker.py").read_text(encoding="utf-8")
    assert 'config.preopen_dir(job["root"], "/work", False)' in worker and 'store.set_limits(memory_size=int(job["memory"]))' in worker and "from backend" not in worker and "import backend" not in worker      # it imports nothing from the app
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1])


# ---- its own process, memory, and the compiled cache ---------------------------------------------------------------------------------------------

import subprocess


def test_a_sandbox_process_that_dies_or_overruns_is_reported_in_words_and_the_server_carries_on(monkeypatch):
    connect("p1", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    monkeypatch.setattr(st.subprocess if hasattr(st, "subprocess") else subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, -9, stdout="", stderr=""))
    killed = run(st.run_tests("p1", ["tests/test_mathy.py"]))
    assert "stopped unexpectedly" in killed["error"] and "most likely it ran out of memory" in killed["error"] and "server itself is unaffected" in killed["error"]

    def too_slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", too_slow)
    assert "did not finish within" in run(st.run_tests("p1", ["tests/test_mathy.py"]))["error"]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="not json", stderr=""))
    assert "stopped unexpectedly" in run(st.run_tests("p1", ["tests/test_mathy.py"]))["error"]


def test_a_run_that_the_server_has_no_memory_for_is_not_started(monkeypatch):
    connect("p2", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    started = []
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: started.append(1) or real_run(*a, **k))
    monkeypatch.setattr(st, "headroom_mb", lambda: 120.0)
    refused = run(st.run_tests("p2", ["tests/test_mathy.py"]))
    assert "short of memory" in refused["error"] and "restarted the server" in refused["error"] and started == []
    monkeypatch.setattr(st, "headroom_mb", lambda: None)                           # a machine that cannot say: assume plenty
    assert run(st.run_tests("p2", ["tests/test_mathy.py"]))["ran"] == 2 and started == [1]


def test_a_cold_start_needs_more_free_memory_than_one_with_the_compiled_cache(monkeypatch, tmp_path):
    connect("p3", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    monkeypatch.setattr(st, "_cache_path", lambda: tmp_path / "none.cwasm")
    monkeypatch.setattr(st, "headroom_mb", lambda: float(st.NEED_MB_WARM + 10))      # enough to load the cache, not enough to compile
    assert "short of memory" in run(st.run_tests("p3", ["tests/test_mathy.py"]))["error"]
    warm = tmp_path / "warm.cwasm"
    warm.write_bytes(b"x")
    monkeypatch.setattr(st, "_cache_path", lambda: warm)
    assert "short of memory" not in str(run(st.run_tests("p3", ["tests/test_mathy.py"])).get("error", ""))


def test_the_first_run_leaves_the_compiled_interpreter_behind_a_broken_cache_is_replaced_and_the_second_run_uses_it(monkeypatch, tmp_path):
    connect("p4", {"mathy.py": MATHY, "tests/test_mathy.py": TEST_MATHY})
    cache = tmp_path / "python.cwasm"
    monkeypatch.setattr(st, "_cache_path", lambda: cache)
    monkeypatch.setattr(st, "headroom_mb", lambda: None)
    assert run(st.run_tests("p4", ["tests/test_mathy.py"]))["ran"] == 2 and cache.exists() and cache.stat().st_size > 1_000_000
    first = cache.stat().st_mtime_ns
    assert run(st.run_tests("p4", ["tests/test_mathy.py"]))["ran"] == 2 and cache.stat().st_mtime_ns == first, "the second run loads the cache and does not rewrite it"
    cache.write_bytes(b"not a compiled module")
    assert run(st.run_tests("p4", ["tests/test_mathy.py"]))["ran"] == 2 and cache.stat().st_size > 1_000_000, "a cache that will not load is thrown away and made again"


def test_the_server_can_say_how_much_memory_it_has_left(monkeypatch):
    files = {"/sys/fs/cgroup/memory.max": "536870912", "/sys/fs/cgroup/memory.current": "400000000"}
    monkeypatch.setattr(st, "_read_text", lambda path: files.get(path))
    assert round(st.headroom_mb()) == 137                                           # a 512 MB container with 400 MB in use
    files["/sys/fs/cgroup/memory.max"] = "max"
    files["/proc/meminfo"] = "MemTotal: 8000000 kB\nMemAvailable: 2500000 kB\n"
    assert round(st.headroom_mb()) == 2500
    monkeypatch.setattr(st, "_read_text", lambda path: None)
    assert st.headroom_mb() is None


def test_the_worker_gets_none_of_the_servers_settings_and_the_tool_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://user:pass@host/db")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = st._clean_env()
    assert "MONGO_URI" not in env and "ANTHROPIC_API_KEY" not in env and env.get("PATH") == "/usr/bin"
    monkeypatch.setenv("SANDBOX_TESTS", "off")
    assert "switched off" in run(st.run_tests("anyone", ["tests/test_x.py"]))["error"]
    monkeypatch.setenv("SANDBOX_TESTS", "on")
    assert st.enabled() is True


def test_the_build_script_precompiles_and_the_cache_is_never_committed(monkeypatch):
    fetch_source = (ROOT / "backend" / "sandbox" / "fetch_sandbox.py").read_text(encoding="utf-8")
    assert fetch_source.count("precompile()") >= 3 and "--precompile" in fetch_source
    assert "backend/sandbox/*.cwasm" in (ROOT / ".gitignore").read_text(encoding="utf-8")
