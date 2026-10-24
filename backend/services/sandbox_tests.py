"""Runs Python tests against a copy of the person's connected folder inside the WASI sandbox (backend/services/python_sandbox.py), so Sonic can check a change BEFORE it asks to apply it.

WHY. Sonic used to write a change and hope. It can now write a test (for example in `.sonic/tests/`), run it against the folder AS IT WOULD BE after its proposed edits, read the real result,
and fix the change before the person sees a diff card. That is the "act, then check" half of working, with ground truth instead of reasoning about whether the code works.

WHAT RUNS. The same genuine CPython-on-WASI interpreter the `run_python` tool uses, with the same hard limits, plus a read-only mount of a fresh copy of the folder:
- NO NETWORK, no process spawning, no clock tricks: WASI preview1 has none of them to grant.
- THE FOLDER IS A COPY, MOUNTED READ-ONLY. The guest sees `/work` and nothing else (verified: listing `/` fails, writing into `/work` is refused, the host is never touched). It is a fresh
  directory per run, deleted afterwards, built only from THIS person's snapshot, so nothing crosses between people.
- BOUNDED. A fuel budget (an instruction count, so an infinite loop is trapped in well under a second), a memory ceiling, a wall-clock wait, a cap on output, and a cap on how much of
  the folder is copied (only source and small data files, never binaries). At most MAX_CONCURRENT runs at once on the server; a busy server says so instead of queueing.
- STANDARD LIBRARY ONLY. The interpreter has no installed packages. Tests of code that imports FastAPI, LangChain, pymongo and the like cannot import them and fail with
  ModuleNotFoundError; the result says so plainly, and the useful tests are of pure logic (parsers, helpers, algorithms) or use stand-ins. This is not a replacement for the project's real
  CI (run_repo_tests); it is a fast check of the part that can be checked here.

PROPOSED EDITS. `overlay` is {path: final text} for the files the proposed edits would create or change (backend/services/local_edits.py validate_edit_proposal returns it), written over
the copy, so the tests run against the result of the change. Nothing is written to the person's folder or to the server's snapshot.

NO FILE TEXT IS LOGGED: only counts.
"""
import asyncio
import gc
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
MEMORY_BYTES = int(os.getenv("SANDBOX_TEST_MEMORY_BYTES", str(256 * 1024 * 1024)))
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


def _run_guest(root: str, paths: List[str]) -> Dict[str, Any]:
    """Blocking. Runs the runner in a fresh interpreter with `root` mounted read-only at /work. Returns {"stdout", "stderr", "out_of_fuel"}."""
    from wasmtime import Store, WasiConfig

    from backend.services.python_sandbox import _get_sandbox

    engine, linker, module = _get_sandbox()
    config = WasiConfig()
    config.argv = ("python", "-c", RUNNER, *paths)
    config.preopen_dir(root, "/work", False)                  # read-only; the only capability the guest is given
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as logs:
        out_path, err_path = os.path.join(logs, "out.log"), os.path.join(logs, "err.log")
        config.stdout_file, config.stderr_file = out_path, err_path
        store = Store(engine)
        store.set_fuel(TEST_FUEL)
        store.set_limits(memory_size=MEMORY_BYTES)
        store.set_wasi(config)
        try:
            instance = linker.instantiate(store, module)
            instance.exports(store)["_start"](store)
        except Exception:
            logger.debug("sandbox_tests guest ended via trap or non-zero exit", exc_info=True)
        out_of_fuel = store.get_fuel() == 0
        stdout = Path(out_path).read_text(errors="replace") if os.path.exists(out_path) else ""
        stderr = Path(err_path).read_text(errors="replace") if os.path.exists(err_path) else ""
        del store
        gc.collect()                                           # lets go of the mount, so the copy can be deleted on every platform
    return {"stdout": stdout, "stderr": stderr, "out_of_fuel": out_of_fuel}


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
    if result["output"]:
        lines.append(result["output"])
    elif result["guest_stderr"]:
        lines.append(result["guest_stderr"])
    if status == "PASSED":
        lines.append("A pass only covers what these tests exercise; say what they cover, not that the change is proven correct.")
    return "\n".join(lines)


async def run_tests(username: str, tests: Any, overlay: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Runs the named test files against a copy of the person's folder (with `overlay` written over it). Returns the parsed result, or {"error": text}. Never raises."""
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
