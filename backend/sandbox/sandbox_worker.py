"""The process that actually runs Python tests inside the WASI sandbox (backend/services/sandbox_tests.py starts it, one run per process).

WHY A SEPARATE PROCESS. The first version ran the interpreter inside the web server. On a small instance that took the whole server down (2026-10-25: the instance restarted about 30 seconds after
a test run started, with no error): compiling the 26 MB interpreter peaks at roughly 450 MB, and the compiled module then stays resident (about 220 MB) for the life of the server. In its own process
the peak is the child's alone, it is returned to the system when the run ends, and a crash, a kill for running too long, or a runaway guest can only take the child with it, never the server.

THE COMPILED INTERPRETER IS CACHED. Compiling takes about two seconds and most of that memory. The first run (or the build: `python backend/sandbox/fetch_sandbox.py` does it) writes the compiled
module next to the .wasm as python-3.12.0.cwasm; every later run loads that, which is nearly instant and small. The cache is only valid for the wasmtime version and machine that wrote it, so a
cache that will not load is thrown away and made again.

This file imports nothing from the app (only the standard library and wasmtime), so it can be run on its own with `python -I`.

USAGE: a JSON job on stdin, a JSON result on stdout:
  in:  {"wasm": path, "cwasm": path, "root": folder to mount read-only at /work, "argv": [...], "fuel": n, "memory": bytes}
  out: {"stdout", "stderr", "out_of_fuel", "peak_mb", "compiled": true when this run had to compile}
  `--precompile <wasm> <cwasm>` only writes the cache.
"""
import json
import os
import sys
import tempfile


def _peak_mb() -> int:
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            return int(counters.PeakWorkingSetSize / 1e6)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak / (1e6 if sys.platform == "darwin" else 1e3))      # bytes on macOS, kilobytes on Linux
    except Exception:
        return 0


def _engine():
    from wasmtime import Config, Engine

    config = Config()
    config.consume_fuel = True
    return Engine(config)


def _load_module(engine, wasm: str, cwasm: str):
    """(module, compiled_now). Loads the cached compiled module when it is valid; otherwise compiles the .wasm and tries to write the cache."""
    from wasmtime import Module

    if cwasm and os.path.exists(cwasm):
        try:
            return Module.deserialize_file(engine, cwasm), False
        except Exception:
            try:
                os.remove(cwasm)               # made by another wasmtime or machine: throw it away and make it again
            except OSError:
                pass
    module = Module.from_file(engine, wasm)
    if cwasm:
        try:
            temporary = f"{cwasm}.{os.getpid()}.tmp"
            with open(temporary, "wb") as handle:
                handle.write(module.serialize())
            os.replace(temporary, cwasm)       # atomic: another run never sees half a file
        except Exception:
            pass                               # a read-only folder just means no cache
    return module, True


def precompile(wasm: str, cwasm: str) -> int:
    engine = _engine()
    _, compiled = _load_module(engine, wasm, cwasm)
    print("compiled and cached" if compiled else "cache already valid")
    return 0 if os.path.exists(cwasm) else 1


def run(job: dict) -> dict:
    from wasmtime import Linker, Store, WasiConfig

    engine = _engine()
    module, compiled = _load_module(engine, job["wasm"], job.get("cwasm") or "")
    linker = Linker(engine)
    linker.define_wasi()
    config = WasiConfig()
    config.argv = tuple(job["argv"])
    config.preopen_dir(job["root"], "/work", False)                 # read-only: the only capability the guest is given
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as logs:
        out_path, err_path = os.path.join(logs, "out.log"), os.path.join(logs, "err.log")
        config.stdout_file, config.stderr_file = out_path, err_path
        store = Store(engine)
        store.set_fuel(int(job["fuel"]))
        store.set_limits(memory_size=int(job["memory"]))
        store.set_wasi(config)
        try:
            instance = linker.instantiate(store, module)
            instance.exports(store)["_start"](store)
        except Exception:
            pass                                                     # a trap or a non-zero exit: what the guest printed is the answer
        out_of_fuel = store.get_fuel() == 0
        stdout = open(out_path, encoding="utf-8", errors="replace").read() if os.path.exists(out_path) else ""
        stderr = open(err_path, encoding="utf-8", errors="replace").read() if os.path.exists(err_path) else ""
    return {"stdout": stdout, "stderr": stderr, "out_of_fuel": out_of_fuel, "peak_mb": _peak_mb(), "compiled": compiled}


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--precompile":
        return precompile(sys.argv[2], sys.argv[3])
    result = run(json.load(sys.stdin))
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
