"""Runs Python tests against a copy of the person's connected folder inside the WASI sandbox (backend/services/python_sandbox.py), so Sonic can check a change BEFORE it asks to apply it.

WHY. Sonic used to write a change and hope. It can now write a test (for example in `.sonic/tests/`), run it against the folder AS IT WOULD BE after its proposed edits, read the real result,
and fix the change before the person sees a diff card. That is the "act, then check" half of working, with ground truth instead of reasoning about whether the code works.

WHAT RUNS. The same genuine CPython-on-WASI interpreter the `run_python` tool uses, with the same hard limits, plus a read-only mount of a fresh copy of the folder:
- NO NETWORK, no process spawning, no clock tricks: WASI preview1 has none of them to grant.
- THE FOLDER IS A COPY, MOUNTED READ-ONLY. The guest sees `/work` and nothing else (verified: listing `/` fails, writing into `/work` is refused, the host is never touched). It is a fresh
  directory per run, deleted afterwards, built only from THIS person's snapshot, so nothing crosses between people.
- ITS OWN PROCESS. The interpreter runs in a child process (backend/sandbox/sandbox_worker.py), never inside the server: loading it costs hundreds of MB, and a crash or a kill must only be able to
  take the child. Before a run starts the server checks it has the memory to spare and refuses (in words) when it does not; SANDBOX_TESTS=off switches the tool off. The compiled interpreter is
  cached on disk (python-3.12.0.cwasm, made at build by fetch_sandbox.py or by the first run), which makes a run small and fast.
- BOUNDED. A fuel budget (an instruction count, so an infinite loop is trapped in well under a second), a memory ceiling (128 MB for the guest), a wall-clock wait, a cap on output, and a cap on how much of
  the folder is copied (only source and small data files, never binaries). At most MAX_CONCURRENT runs at once on the server; a busy server says so instead of queueing.
- STANDARD LIBRARY ONLY. The interpreter has no installed packages. Tests of code that imports FastAPI, LangChain, pymongo and the like cannot import them and fail with
  ModuleNotFoundError; the result says so plainly, and the useful tests are of pure logic (parsers, helpers, algorithms) or use stand-ins. This is not a replacement for the project's real
  CI (run_repo_tests); it is a fast check of the part that can be checked here.

PROPOSED EDITS. `overlay` is {path: final text} for the files the proposed edits would create or change (backend/services/local_edits.py validate_edit_proposal returns it), written over
the copy, so the tests run against the result of the change. Nothing is written to the person's folder or to the server's snapshot.

NO FILE TEXT IS LOGGED: only counts.
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services import local_workspace

logger = logging.getLogger("SASS Logger")

TEST_FUEL = int(os.getenv("SANDBOX_TEST_FUEL", "4000000000"))     # unittest alone starts up on about 1.3 billion
WALL_SECONDS = float(os.getenv("SANDBOX_TEST_WALL_SECONDS", "25"))
MEMORY_BYTES = int(os.getenv("SANDBOX_TEST_MEMORY_BYTES", str(128 * 1024 * 1024)))
MAX_CONCURRENT = int(os.getenv("SANDBOX_TEST_MAX_CONCURRENT", "2"))
MAX_TEST_FILES = 5
MAX_OUTPUT_CHARS = 6000
MAX_COPY_FILES = 3000
MAX_COPY_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
COPIED_SUFFIXES = (".py", ".json", ".txt", ".md", ".csv", ".yaml", ".yml", ".toml", ".cfg", ".ini")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

# Runs inside the guest. Loads each named test file as a module (so a file may import its neighbours from /work), runs every unittest case in them, and ends with one machine-readable
# line. argv[1:] are paths relative to /work.
RUNNER = '''
import importlib.util, json, sys, unittest
sys.path.insert(0, "/work")
summary = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0, "load_errors": []}
suite = unittest.TestSuite()
loader = unittest.TestLoader()
for index, path in enumerate(sys.argv[1:]):
    try:
        folder = path.rsplit("/", 1)[0] if "/" in path else ""
        if folder:
            sys.path.insert(0, "/work/" + folder)
        name = "sandbox_test_module_%d" % index
        spec = importlib.util.spec_from_file_location(name, "/work/" + path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    except BaseException as exc:
        summary["load_errors"].append(path + ": " + type(exc).__name__ + ": " + str(exc)[:300])
outcome = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
summary.update(ran=outcome.testsRun, failures=len(outcome.failures), errors=len(outcome.errors), skipped=len(outcome.skipped))
print()
print("::RESULT::" + json.dumps(summary))
'''
_RESULT_MARK = "::RESULT::"


class SandboxError(Exception):
    """Something that stops a run before or around the guest: said to the model in words."""


def _build_tree(username: str, dest: str, overlay: Dict[str, str]) -> int:
    """Copies the person's snapshot (source and small data files only) into `dest`, then writes the overlay over it. Returns the number of files. Blocking."""
    handle = local_workspace.get_workspace_handle(username)
    if handle is None:
        raise SandboxError("No local folder is connected, so there is nothing to test.")
    source = Path(handle.root)
    count = total = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or not path.name.lower().endswith(COPIED_SUFFIXES):
            continue
        relative = path.relative_to(source).as_posix()
        if local_workspace.path_rejection(relative):
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            continue
        count += 1
        total += size
        if count > MAX_COPY_FILES or total > MAX_COPY_BYTES:
            raise SandboxError(f"The folder is too large to copy into the sandbox (over {MAX_COPY_FILES} source files or {MAX_COPY_BYTES // (1024 * 1024)} MB), so nothing was tested.")
        target = Path(dest) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for relative, text in (overlay or {}).items():
        safe = local_workspace.safe_relative_path(relative)
        if safe is None or local_workspace.path_rejection(safe) or not isinstance(text, str) or len(text.encode("utf-8")) > MAX_FILE_BYTES:
            continue                       # a path or size the snapshot itself would not hold is not written
        target = Path(dest) / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        count += 1
    return count


WORKER = Path(__file__).resolve().parent.parent / "sandbox" / "sandbox_worker.py"
# Memory the server must have free before a run starts. Measured 2026-10-25: a run with the compiled interpreter cached peaks near 60 MB (0.7 s); one that has to compile it first peaks near
# 430 MB (3 s). The guest's own memory is capped at 128 MB, so a warm run is held to 200 MB of headroom and a cold one to 700 MB. A run that would not fit is refused in words instead of being started, because an out-of-memory kill takes the whole server with it
# (2026-10-25: the instance restarted about 30 seconds after a run began).
NEED_MB_WARM = int(os.getenv("SANDBOX_TEST_NEED_MB_WARM", "200"))
NEED_MB_COLD = int(os.getenv("SANDBOX_TEST_NEED_MB_COLD", "700"))
_ENV_KEEP = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TMP", "TEMP", "TMPDIR", "HOME", "LANG", "LC_ALL")


def enabled() -> bool:
    """SANDBOX_TESTS=off switches the tool off without a deploy of code (the setting is read on every run)."""
    return os.getenv("SANDBOX_TESTS", "on").strip().lower() not in ("0", "false", "no", "off")


def _read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _inactive_cache_bytes(stat_path: str, key: str) -> int:
    """The inactive file cache in a container's memory.stat, in bytes (0 when it cannot be read). The kernel gives this back before it refuses anyone memory, so it is not use."""
    for line in (_read_text(stat_path) or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == key and parts[1].isdigit():
            return int(parts[1])
    return 0


def headroom_mb(count_cache: bool = False) -> Optional[float]:
    """How much memory this server can still use, in MB: from its container limit (cgroup v2 or v1) when it has one, otherwise what the machine reports as available. None when it cannot be
    told (a developer's own machine), which is taken to mean "plenty". The container's "used" figure includes file cache (every file written or read stays cached), and the inactive part of
    it is handed back before anything is refused, so by default it is not counted as used (the working set, as `docker stats` has it). `count_cache=True` gives the old, stricter figure;
    the Sonic Health memory card shows both (backend/utils/server_memory.py)."""
    limit, used = _read_text("/sys/fs/cgroup/memory.max"), _read_text("/sys/fs/cgroup/memory.current")
    if limit and limit != "max" and used and limit.isdigit() and used.isdigit():
        back = 0 if count_cache else _inactive_cache_bytes("/sys/fs/cgroup/memory.stat", "inactive_file")
        return (int(limit) - max(int(used) - back, 0)) / 1e6
    limit, used = _read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes"), _read_text("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if limit and used and limit.isdigit() and used.isdigit() and int(limit) < 2 ** 60:
        back = 0 if count_cache else _inactive_cache_bytes("/sys/fs/cgroup/memory/memory.stat", "total_inactive_file")
        return (int(limit) - max(int(used) - back, 0)) / 1e6
    meminfo = _read_text("/proc/meminfo")
    if meminfo:
        for line in meminfo.splitlines():
            if line.startswith("MemAvailable:"):
                try:
                    return int(line.split()[1]) / 1e3
                except (IndexError, ValueError):
                    return None
    return None


def _clean_env() -> Dict[str, str]:
    """The environment the worker gets: the few variables a process needs to start, and none of the server's settings or secrets."""
    return {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}


def _cache_path() -> Path:
    from backend.services.python_sandbox import SANDBOX_WASM_PATH

    return SANDBOX_WASM_PATH.with_suffix(".cwasm")


def run_worker(
    job: Dict[str, Any],
    wall_seconds: float,
    *,
    short_of_memory: str = "The server is short of memory right now, so the tests were not started (starting them could have restarted the server). Nothing was tested; try again in a moment.",
    timed_out: Optional[str] = None,
    stopped: str = "The sandbox process stopped unexpectedly (most likely it ran out of memory), so nothing was tested. The server itself is unaffected.",
) -> Dict[str, Any]:
    """Blocking. Runs a job in the sandbox's own process (backend/sandbox/sandbox_worker.py) after checking the server has the memory for it, and returns the worker's answer
    ({"stdout", "stderr", "out_of_fuel", ...}). The test runs and the run_python tool both go through here, so neither ever loads the interpreter inside the web server (compiling it
    there peaks near 450 MB and took the server down once). `job` adds to the standard fields (the interpreter, its compiled cache, the guest's memory cap); a `root` of None grants the
    guest no filesystem at all. Raises FileNotFoundError when the interpreter is missing and SandboxError, in words, when the run cannot be started safely or the process dies."""
    import subprocess
    import sys

    from backend.services.python_sandbox import SANDBOX_WASM_PATH

    if not SANDBOX_WASM_PATH.exists():
        raise FileNotFoundError(f"Python sandbox interpreter not found at {SANDBOX_WASM_PATH}. Run backend/sandbox/fetch_sandbox.py.")
    cache = _cache_path()
    need = NEED_MB_WARM if cache.exists() else NEED_MB_COLD
    room = headroom_mb()
    if room is not None and room < need:
        logger.warning("[sandbox_tests] refused a run: %d MB free, %d MB needed.", int(room), need)
        raise SandboxError(short_of_memory)
    full = {"wasm": str(SANDBOX_WASM_PATH), "cwasm": str(cache), "memory": MEMORY_BYTES, **job}
    try:
        done = subprocess.run([sys.executable, "-I", str(WORKER)], input=json.dumps(full), capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=wall_seconds, env=_clean_env(), cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        raise SandboxError(timed_out or f"The tests did not finish within {int(wall_seconds + 3)} seconds and were stopped, so the result is unknown.") from None
    try:
        result = json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else None
    except ValueError:
        result = None
    if not isinstance(result, dict):
        logger.warning("[sandbox_tests] the sandbox process ended abnormally (exit code %d).", int(done.returncode))
        raise SandboxError(stopped)
    logger.info("[sandbox_tests] the sandbox process used about %d MB%s.", int(result.get("peak_mb") or 0), " and compiled the interpreter" if result.get("compiled") else "")
    return {"stdout": str(result.get("stdout", "")), "stderr": str(result.get("stderr", "")), "out_of_fuel": bool(result.get("out_of_fuel"))}


def _run_guest(root: str, paths: List[str]) -> Dict[str, Any]:
    """Blocking. Runs the test runner in a fresh interpreter, in its own process, with `root` mounted read-only at /work. Returns {"stdout", "stderr", "out_of_fuel"}."""
    return run_worker({"root": root, "argv": ["python", "-c", RUNNER, *paths], "fuel": TEST_FUEL}, max(WALL_SECONDS - 3, 5))


def parse_output(stdout: str, stderr: str, out_of_fuel: bool) -> Dict[str, Any]:
    """The structured result out of what the guest printed. Pure."""
    summary: Dict[str, Any] = {}
    shown = stdout
    if _RESULT_MARK in stdout:
        head, _, tail = stdout.rpartition(_RESULT_MARK)
        try:
            summary = json.loads(tail.strip().splitlines()[0])
        except (ValueError, IndexError):
            summary = {}
        shown = head
    text = shown.strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = "... (earlier output cut)\n" + text[-MAX_OUTPUT_CHARS:]
    guest_errors = (stderr or "").strip()[-1500:]
    return {
        "ran": int(summary.get("ran", 0)), "failures": int(summary.get("failures", 0)), "errors": int(summary.get("errors", 0)), "skipped": int(summary.get("skipped", 0)),
        "load_errors": [str(e)[:400] for e in (summary.get("load_errors") or [])][:MAX_TEST_FILES],
        "output": text, "guest_stderr": guest_errors, "out_of_fuel": bool(out_of_fuel), "finished": bool(summary),
    }


def unavailable_imports(source: str, root: str, test_path: str) -> List[str]:
    """The modules a test file imports at its top level that the sandbox can never provide: not in the standard library, and not a file or folder in the copy (at its root or beside the test).

    WHY (2026-10-27). Sonic planned "run the test" for a pytest file. The server was short of memory, so the run was refused with "try again in a moment", which hid the real answer: the file
    imports pytest, the sandbox has only the standard library, and it could never have run however much memory was free. A permanent failure is now said before the memory check, and said as permanent.

    Only plain imports at the top of the file are read: one inside `try`, `if` or a function may be guarded, so it is never grounds for refusing. Imports of other files in the folder are not followed
    (what THEY import is reported by the run itself). Empty when this Python cannot list its standard library, since guessing would refuse good files. Pure apart from looking at the copy."""
    import ast
    import sys

    stdlib = getattr(sys, "stdlib_module_names", None)
    if not stdlib:
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []                                                     # the guest reports a syntax error better than this can
    base = Path(root)
    beside = (base / test_path).parent
    found: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        else:
            continue
        for module in modules:
            top = module.split(".")[0]
            if top in stdlib or top in found:
                continue
            if any((where / f"{top}.py").is_file() or (where / top).is_dir() for where in (base, beside)):
                continue
            found.append(top)
    return found


def unavailable_message(path: str, modules: List[str]) -> str:
    """What the model is told when a test file can never run here. Says so plainly and says what to do instead. Pure."""
    names = ", ".join(modules)
    pytest_file = " It is written for pytest." if "pytest" in modules else ""
    return (
        f"{path} imports {names}, which the sandbox does not have (it has only Python's standard library), so this file can never run here, however much memory the server has.{pytest_file} "
        "Do not retry it. The project's own pytest suite runs with run_repo_tests on a pushed branch; or write the check as a unittest.TestCase that uses only the standard library and call run_sandbox_tests with that."
    )


def clean_test_paths(tests: Any) -> List[str]:
    """The test files asked for, as safe relative `.py` paths. Raises SandboxError in words."""
    if isinstance(tests, str):
        tests = [tests]
    if not isinstance(tests, list) or not tests:
        raise SandboxError("Give `tests`: a list of Python test file paths in the folder (for example .sonic/tests/test_parser.py).")
    if len(tests) > MAX_TEST_FILES:
        raise SandboxError(f"Run at most {MAX_TEST_FILES} test files at a time.")
    paths = []
    for raw in tests:
        path = local_workspace.safe_relative_path(raw)
        if path is None or not path.endswith(".py"):
            raise SandboxError(f"{raw!r} is not a path to a Python test file inside the folder.")
        paths.append(path)
    return paths


def format_result(result: Dict[str, Any], *, with_edits: bool = False) -> str:
    """What the model is told. Pure."""
    where = "against the folder as it would be AFTER your proposed edits (nothing was changed or shown)" if with_edits else "against the folder as it is now"
    if result.get("error"):
        return f"ERROR: {result['error']}"
    lines: List[str] = []
    if result["load_errors"] or not result["finished"] or result["out_of_fuel"]:
        status = "COULD NOT RUN"
    elif result["failures"] or result["errors"]:
        status = "FAILED"
    elif result["ran"] == 0:
        status = "NO TESTS FOUND"
    else:
        status = "PASSED"
    detail = f"{result['ran']} test(s) ran, {result['failures']} failed, {result['errors']} errored" + (f", {result['skipped']} skipped" if result["skipped"] else "")
    lines.append(f"SANDBOX TESTS {status} {where}: {detail}.")
    for problem in result["load_errors"]:
        lines.append(f"Could not load: {problem}")
    if result["out_of_fuel"]:
        lines.append("The run used up its compute budget and was stopped (an infinite loop, or too much work for a quick check).")
    seen = (result["output"] + "\n" + result["guest_stderr"] + "\n" + " ".join(result["load_errors"]))
    if "ModuleNotFoundError" in seen or "ImportError" in seen:
        lines.append("The sandbox has only Python's standard library: no installed packages and no network. Code that imports anything else cannot run here; test code that needs only the "
                     "standard library, or give the test a stand-in for the missing module. This does not mean the code itself is wrong.")
    if status == "NO TESTS FOUND":
        lines.append("The sandbox runs unittest.TestCase classes only; test functions written for pytest are not collected, so this file has nothing it can run.")
    if result["output"]:
        lines.append(result["output"])
    elif result["guest_stderr"]:
        lines.append(result["guest_stderr"])
    if status == "PASSED":
        lines.append("A pass only covers what these tests exercise; say what they cover, not that the change is proven correct.")
    return "\n".join(lines)


async def run_tests(username: str, tests: Any, overlay: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Runs the named test files against a copy of the person's folder (with `overlay` written over it). Returns the parsed result, or {"error": text}. Never raises."""
    if not enabled():
        return {"error": "Running tests in the sandbox is switched off on this server right now."}
    try:
        paths = clean_test_paths(tests)
    except SandboxError as problem:
        return {"error": str(problem)}
    if not _slots.acquire(blocking=False):
        return {"error": "The test sandbox is busy with other runs right now. Try again in a moment."}

    def work() -> Dict[str, Any]:
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
                files = _build_tree(username, root, overlay or {})
                missing = [p for p in paths if not (Path(root) / p).is_file()]
                if missing:
                    return {"error": f"No such test file in the folder (or in your proposed edits): {', '.join(missing)}. Create it in the same `edits`, or check the path."}
                for path in paths:
                    modules = unavailable_imports((Path(root) / path).read_text(encoding="utf-8", errors="replace"), root, path)
                    if modules:
                        return {"error": unavailable_message(path, modules)}          # before the memory check: a file that can never run must not be told "try again in a moment"
                logger.info("[sandbox_tests] running %d test file(s) against a copy of %d file(s).", len(paths), files)
                raw = _run_guest(root, paths)
                return parse_output(raw["stdout"], raw["stderr"], raw["out_of_fuel"])
        except SandboxError as problem:
            return {"error": str(problem)}
        except FileNotFoundError as problem:
            return {"error": str(problem)}
        finally:
            _slots.release()

    try:
        return await asyncio.wait_for(asyncio.to_thread(work), timeout=WALL_SECONDS)
    except asyncio.TimeoutError:
        return {"error": f"The tests did not finish within {int(WALL_SECONDS)} seconds, so the result is unknown."}
    except Exception:
        logger.warning("[sandbox_tests] the run failed.", exc_info=True)
        return {"error": "The sandbox could not run the tests."}
