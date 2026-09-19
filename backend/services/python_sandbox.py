"""WASI-sandboxed Python execution for tool_agent_node's run_python action.

Real security boundary, not a restricted-exec() illusion. A naive "sandbox" built by
stripping __builtins__ and blocking import names inside an in-process exec() is a
well-documented dead end in CPython — code can reach os/subprocess through ordinary object
introspection (e.g. ().__class__.__bases__[0].__subclasses__()) even with __import__
removed. Nothing here relies on the guest code failing to find an escape hatch, because the
escape hatch doesn't exist at the WASM level: code runs inside a genuine, separate CPython
interpreter compiled to WASI (WebAssembly System Interface) via wasmtime. WASM has no
ambient authority — the guest cannot touch the filesystem or network unless we explicitly
grant capabilities, and we grant none.

Verified directly against this exact module (not just asserted): a file-read attempt against
a real path on the host raised FileNotFoundError from inside the sandbox (WASI reports no
such path exists, because none were preopened via WasiConfig.preopen_dir — which is never
called here), os.system doesn't even exist as an attribute on this build's os module (WASI
has no process-spawn syscall for CPython to bind it to), and there is no socket syscall in
WASI preview1 at all for network access to hang off of. An infinite `while True: pass` loop
was hard-trapped in 0.02s via fuel exhaustion, a deterministic instruction-count cutoff that
(unlike a signal-based Python timeout) cannot be evaded by a blocking C-level call.

The import allowlist below is NOT what makes this safe — it's a fast, friendly pre-filter
that rejects obviously out-of-scope code before spending a sandbox invocation on it. Even an
unlisted import would still be structurally unable to do anything dangerous once inside the
sandbox; the capability restriction above is what actually enforces the boundary.

Interpreter binary: python-3.12.0.wasm from vmware-labs/webassembly-language-runtimes
(https://github.com/vmware-labs/webassembly-language-runtimes/releases/tag/python%2F3.12.0%2B20231211-040d5a6),
sha256 e5dc5a398b07b54ea8fdb503bf68fb583d533f10ec3f930963e02b9505f7a763 — verify this against
sandbox/python-3.12.0.wasm.sha256sum if you ever re-download it.
"""

import ast
import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("SASS Logger")

SANDBOX_WASM_PATH = Path(
    os.getenv("PYTHON_SANDBOX_WASM_PATH")
    or (Path(__file__).resolve().parent.parent / "sandbox" / "python-3.12.0.wasm")
)

# Pure-computation stdlib only — nothing here does I/O, spawns processes, or touches the
# network even outside the sandbox, so this list is a UX/clarity choice (fast, specific
# rejection messages) rather than the thing standing between the guest and anything unsafe.
SAFE_IMPORT_ALLOWLIST = {
    "math", "statistics", "random", "itertools", "functools", "operator",
    "collections", "heapq", "bisect", "datetime", "string", "re", "json",
    "textwrap", "decimal", "fractions", "copy", "typing", "dataclasses", "enum",
}

# Fuel is an instruction-count budget, not wall-clock time — calibrated empirically (see
# module docstring): 50,000,000 fuel traps a tight infinite loop in ~0.02s, so 400,000,000
# gives real small-snippet workloads room to run while still capping runaway loops at a
# small fraction of a second in practice. DEFAULT_TIMEOUT_SECONDS is a wall-clock backstop
# for the agent's own patience, not a security control — see run_python_sandboxed.
DEFAULT_FUEL = 400_000_000
DEFAULT_TIMEOUT_SECONDS = 5.0


def _find_disallowed_import(code: str) -> str | None:
    """AST-level pre-filter — see module docstring for why this isn't the security boundary.
    Returns a human-readable reason string if code should be rejected before running, else
    None. Catches both static `import x` / `from x import y` and a dynamic __import__(...)/
    importlib.import_module(...) call, so a computed module name can't dodge the static check
    (this still isn't load-bearing for safety, just for giving a clear, fast rejection)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in SAFE_IMPORT_ALLOWLIST:
                    return f"import of '{alias.name}' is not on the safe list"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in SAFE_IMPORT_ALLOWLIST:
                return f"import of '{node.module}' is not on the safe list"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ("__import__", "import_module"):
                return f"dynamic import via '{name}(...)' is not allowed"
    return None


_sandbox_lock = threading.Lock()
_engine = None
_linker = None
_module = None


def _get_sandbox():
    """Lazily builds the wasmtime Engine/Linker/Module once and reuses them — reloading the
    ~26MB module from disk on every call would add needless latency to every invocation.
    The module itself is stateless and safe to share; each call still gets its own fresh
    Store (see _run_in_sandbox), i.e. its own fresh interpreter instance with no state
    carried over from a previous call, matching the "no persistent state" requirement."""
    global _engine, _linker, _module
    if _module is not None:
        return _engine, _linker, _module

    with _sandbox_lock:
        if _module is not None:
            return _engine, _linker, _module

        from wasmtime import Config, Engine, Linker, Module

        if not SANDBOX_WASM_PATH.exists():
            raise FileNotFoundError(
                f"Python sandbox interpreter not found at {SANDBOX_WASM_PATH}. Download "
                "python-3.12.0.wasm from vmware-labs/webassembly-language-runtimes (see "
                "backend/sandbox/README.md) and place it there."
            )

        engine_cfg = Config()
        engine_cfg.consume_fuel = True
        engine = Engine(engine_cfg)
        linker = Linker(engine)
        linker.define_wasi()
        module = Module.from_file(engine, str(SANDBOX_WASM_PATH))

        _engine, _linker, _module = engine, linker, module
        return _engine, _linker, _module


def _run_in_sandbox(code: str, fuel: int) -> tuple[str, str]:
    """Blocking — always called via a thread (see run_python_sandboxed). Returns
    (stdout, stderr). Never raises for guest-code failures: a trap (fuel exhausted, an
    uncaught exception in the guest, a denied filesystem/capability access) just means the
    guest's own stderr — read from its redirected log file below — already has the real
    Python traceback, which is what actually gets returned as the "error" field."""
    from wasmtime import Store, WasiConfig

    engine, linker, module = _get_sandbox()

    wasi_config = WasiConfig()
    wasi_config.argv = ("python", "-c", code)
    # No preopen_dir call anywhere in this file — zero filesystem capabilities granted to
    # the guest. No network configuration exists to grant in WASI preview1 at all; there is
    # no socket syscall for this build's libc to bind to, so there's nothing to "block" —
    # it's structurally absent from the platform, not merely disabled.

    with tempfile.TemporaryDirectory() as chroot:
        out_path = os.path.join(chroot, "out.log")
        err_path = os.path.join(chroot, "err.log")
        wasi_config.stdout_file = out_path
        wasi_config.stderr_file = err_path

        store = Store(engine)
        store.set_fuel(fuel)
        store.set_wasi(wasi_config)

        try:
            instance = linker.instantiate(store, module)
            start = instance.exports(store)["_start"]
            start(store)
        except Exception:
            logger.debug("python_sandbox guest run ended via trap/non-zero exit", exc_info=True)

        stdout = Path(out_path).read_text(errors="replace") if os.path.exists(out_path) else ""
        stderr = Path(err_path).read_text(errors="replace") if os.path.exists(err_path) else ""
        return stdout, stderr


async def run_python_sandboxed(
    code: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    fuel: int = DEFAULT_FUEL,
) -> dict:
    """The run_python tool_agent_node action. Never raises to the caller — every failure
    mode (bad syntax, a disallowed import, the sandbox binary missing, a real exception in
    the guest code, fuel exhaustion, a timeout) comes back as {"output": str, "error": str}
    instead, so the ReAct loop's `act()` dispatch never needs its own try/except for this
    action specifically.

    timeout_seconds is a wall-clock backstop on how long the agent loop waits, not the
    actual safety control — cancelling an awaited asyncio.to_thread call doesn't force-kill
    the underlying OS thread, so a runaway guest keeps running in the background until fuel
    (the real, deterministic bound) runs out, even after this returns a timeout error."""
    if not isinstance(code, str) or not code.strip():
        return {"output": "", "error": "No code provided."}

    disallowed = _find_disallowed_import(code)
    if disallowed:
        return {"output": "", "error": f"Rejected before running: {disallowed}."}

    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.to_thread(_run_in_sandbox, code, fuel),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return {"output": "", "error": f"Timed out after {timeout_seconds}s."}
    except FileNotFoundError as e:
        logger.error(str(e))
        return {"output": "", "error": str(e)}
    except Exception as e:
        logger.exception("Unexpected error running the Python sandbox.")
        return {"output": "", "error": f"Sandbox error: {e}"}

    return {"output": stdout, "error": stderr}
