# Python sandbox interpreter

`backend/services/python_sandbox.py` runs `run_python` (a `tool_agent_node` action) inside a
genuine, separate CPython interpreter compiled to WASI, via [wasmtime](https://wasmtime.dev/).
See that file's module docstring for why this is a real security boundary and not the usual
restricted-`exec()` illusion.

## Setup

```bash
python backend/sandbox/fetch_sandbox.py
```

Downloads `python-3.12.0.wasm` (~26MB) from
[vmware-labs/webassembly-language-runtimes](https://github.com/vmware-labs/webassembly-language-runtimes/releases/tag/python%2F3.12.0%2B20231211-040d5a6)
and verifies it against the committed `python-3.12.0.wasm.sha256sum`. The `.wasm` file itself
is gitignored — it's a downloadable third-party binary, not something to commit.

If `run_python` returns `"Python sandbox interpreter not found at ..."`, run the fetch script
above.

## What it can and can't do

- No filesystem access — no directory is ever preopened for the guest, so `open(...)` against
  any real path fails with `FileNotFoundError` from inside the sandbox.
- No network access — WASI preview1 (what this build targets) has no socket syscall at all;
  there's nothing to block because the capability doesn't exist on the platform.
- No process spawning — `os.system`/`subprocess` aren't meaningfully present; WASI has no
  process-spawn syscall for CPython to bind them to.
- Hard-capped execution via `wasmtime`'s fuel accounting (an instruction-count budget, not a
  signal-based timeout) — a genuine infinite loop is trapped in a fraction of a second,
  deterministically, regardless of what the code is doing.
- A small AST-level import allowlist (`SAFE_IMPORT_ALLOWLIST` in `python_sandbox.py`) rejects
  obviously out-of-scope code fast, with a clear message — this is a convenience layer, not
  the security boundary; the capability restrictions above are.
