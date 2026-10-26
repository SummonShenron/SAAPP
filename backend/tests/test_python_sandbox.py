import asyncio
import functools

import pytest

from backend.services import python_sandbox as sandbox


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# Import allowlist pre-filter — a UX/clarity layer, not the security boundary
# (see python_sandbox.py's module docstring), but still worth getting right.
# ---------------------------------------------------------------------------

def test_allowed_import_passes():
    assert sandbox._find_disallowed_import("import math\nprint(math.sqrt(4))") is None


def test_disallowed_import_rejected():
    reason = sandbox._find_disallowed_import("import os")
    assert reason is not None
    assert "os" in reason


def test_disallowed_from_import_rejected():
    reason = sandbox._find_disallowed_import("from subprocess import run")
    assert reason is not None
    assert "subprocess" in reason


def test_dynamic_import_via_dunder_import_rejected():
    reason = sandbox._find_disallowed_import("__import__('os').system('echo hi')")
    assert reason is not None
    assert "__import__" in reason


def test_dynamic_import_via_importlib_rejected():
    reason = sandbox._find_disallowed_import("import importlib\nimportlib.import_module('os')")
    # The static "import importlib" line is itself already disallowed (importlib isn't on
    # the safe list), so this is caught before the dynamic-call check even matters — still
    # correctly rejected either way.
    assert reason is not None


def test_syntax_error_rejected_without_crashing():
    reason = sandbox._find_disallowed_import("def broken(:\n    pass")
    assert reason is not None
    assert "SyntaxError" in reason


# ---------------------------------------------------------------------------
# run_python_sandboxed: never raises, whatever goes wrong
# ---------------------------------------------------------------------------

@run_async
async def test_never_raises_on_disallowed_import():
    called = {"n": 0}

    def fake_run_in_sandbox(code, fuel, wall_seconds=None):
        called["n"] += 1
        return "should never run", ""

    import backend.services.python_sandbox as sb
    orig = sb._run_in_sandbox
    sb._run_in_sandbox = fake_run_in_sandbox
    try:
        result = await sandbox.run_python_sandboxed("import os")
    finally:
        sb._run_in_sandbox = orig

    assert result["output"] == ""
    assert "os" in result["error"]
    assert called["n"] == 0  # rejected before ever touching the sandbox


@run_async
async def test_never_raises_on_empty_code():
    result = await sandbox.run_python_sandboxed("")
    assert result == {"output": "", "error": "No code provided."}

    result = await sandbox.run_python_sandboxed(None)  # type: ignore[arg-type]
    assert result["error"] == "No code provided."


@run_async
async def test_never_raises_when_sandbox_binary_missing(monkeypatch):
    monkeypatch.setattr(sandbox, "SANDBOX_WASM_PATH", sandbox.Path("/nowhere/python-3.12.0.wasm"))

    result = await sandbox.run_python_sandboxed("print(1)")

    assert result["output"] == ""
    assert "not found" in result["error"]


@run_async
async def test_never_raises_on_unexpected_exception(monkeypatch):
    def boom(code, fuel, wall_seconds=None):
        raise RuntimeError("something wasmtime-internal exploded")

    monkeypatch.setattr(sandbox, "_run_in_sandbox", boom)

    result = await sandbox.run_python_sandboxed("print(1)")

    assert result["output"] == ""
    assert "Sandbox error" in result["error"]


@run_async
async def test_timeout_returns_error_not_raise(monkeypatch):
    def slow(code, fuel, wall_seconds=None):
        import time
        time.sleep(10)
        return "too slow", ""

    monkeypatch.setattr(sandbox, "_run_in_sandbox", slow)

    result = await sandbox.run_python_sandboxed("print(1)", timeout_seconds=0.05)

    assert result["output"] == ""
    assert "Timed out" in result["error"]


# ---------------------------------------------------------------------------
# Real integration tests against the actual WASI interpreter — skipped if the
# binary hasn't been fetched (see backend/sandbox/fetch_sandbox.py). These are
# the tests that actually prove the security properties hold, not just that
# the plumbing calls the right mock.
# ---------------------------------------------------------------------------

requires_real_sandbox = pytest.mark.skipif(
    not sandbox.SANDBOX_WASM_PATH.exists(),
    reason="python-3.12.0.wasm not present — run backend/sandbox/fetch_sandbox.py",
)


@requires_real_sandbox
@run_async
async def test_real_sandbox_executes_basic_code():
    result = await sandbox.run_python_sandboxed("print(2 + 2)")
    assert result["output"].strip() == "4"
    assert result["error"] == ""


@requires_real_sandbox
@run_async
async def test_real_sandbox_allows_whitelisted_stdlib():
    result = await sandbox.run_python_sandboxed("import math\nprint(math.sqrt(16))")
    assert result["output"].strip() == "4.0"


@requires_real_sandbox
@run_async
async def test_real_sandbox_blocks_filesystem_access():
    result = await sandbox.run_python_sandboxed(
        "open('C:/Users/jackh/local-rag/requirements.txt').read()"
    )
    assert result["output"] == ""
    assert "FileNotFoundError" in result["error"]


@requires_real_sandbox
@run_async
async def test_real_sandbox_has_no_process_spawn_capability():
    # Goes around the import allowlist on purpose (calling _run_in_sandbox directly instead
    # of run_python_sandboxed) — the allowlist would reject "import os" first, which tests
    # the pre-filter, not the thing this test actually cares about: that even if some code
    # got past the pre-filter, the WASI capability restriction still holds underneath it.
    stdout, stderr = await asyncio.to_thread(
        sandbox._run_in_sandbox, "import os\nos.system('echo pwned')", sandbox.DEFAULT_FUEL
    )
    assert stdout == ""
    assert "Error" in stderr


@requires_real_sandbox
@run_async
async def test_real_sandbox_traps_infinite_loop_quickly():
    import time
    start = time.time()
    result = await sandbox.run_python_sandboxed(
        "while True:\n    pass", fuel=50_000_000, timeout_seconds=5.0
    )
    elapsed = time.time() - start
    assert elapsed < 4.5  # fuel exhaustion, not the 5s wall-clock backstop, is what stopped it (this now includes starting the sandbox's own process, about a second)
    assert result["output"] == ""


# ---------------------------------------------------------------------------
# The interpreter never runs inside the web server (it did once, and took it down)
# ---------------------------------------------------------------------------

def test_the_server_process_never_loads_the_interpreter():
    import pathlib

    source = pathlib.Path(sandbox.__file__).read_text(encoding="utf-8")
    assert "from wasmtime" not in source and "import wasmtime" not in source and "Module.from_file" not in source
    assert "run_worker(" in source


@run_async
async def test_a_server_short_of_memory_refuses_in_words_and_does_not_start_anything(monkeypatch):
    from backend.services import sandbox_tests as st

    monkeypatch.setattr(st, "headroom_mb", lambda count_cache=False: 10.0)
    started = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: started.append(a))
    result = await sandbox.run_python_sandboxed("print(1)")
    assert result["output"] == "" and "short of memory" in result["error"] and "code was not run" in result["error"] and started == []


@run_async
async def test_a_worker_that_dies_or_overruns_comes_back_as_an_error_not_an_exception(monkeypatch):
    import subprocess

    from backend.services import sandbox_tests as st

    monkeypatch.setattr(st, "headroom_mb", lambda count_cache=False: None)

    def overrun(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=1)

    monkeypatch.setattr("subprocess.run", overrun)
    assert "Timed out after" in (await sandbox.run_python_sandboxed("print(1)", timeout_seconds=1.0))["error"]
    monkeypatch.setattr("subprocess.run", lambda *a, **k: type("Done", (), {"returncode": 1, "stdout": "", "stderr": ""})())
    assert "stopped unexpectedly" in (await sandbox.run_python_sandboxed("print(1)"))["error"]


@requires_real_sandbox
@run_async
async def test_real_run_gives_the_guest_no_filesystem_and_reports_an_endless_loop_in_words():
    mounted = await sandbox.run_python_sandboxed("print(open('/work/x').read())")
    assert mounted["output"] == "" and "FileNotFoundError" in mounted["error"]          # nothing is mounted for run_python, not even an empty folder to find
    looped = await sandbox.run_python_sandboxed("while True:\n    pass", fuel=50_000_000, timeout_seconds=8.0)
    assert looped["output"] == "" and "compute budget" in looped["error"]
