#!/usr/bin/env python3
"""Check that tests really fail when the code is broken, without ever editing the real tree.

    python .claude/skills/mutation-check/mutate.py mutations.json -- backend/tests/test_thing.py backend/tests/test_other.py

`mutations.json` is a list of {"file": "backend/utils/thing.py", "old": "exact text", "new": "replacement", "label": "what this breaks"}; an optional "count" is how many occurrences to
replace (default 1). The script copies the code to a temporary folder, runs the tests once as they are (a red baseline stops it), then applies each mutation to the COPY, runs the tests, and restores it.
A mutation is CAUGHT when the tests fail, SURVIVED when they still pass (the tests do not check that behaviour), MISSING when `old` is not in the file (the code moved: fix the pattern).

WHY A COPY. Breaking files in place once got a deliberately broken line into a real commit (made while a run was active). Nothing here ever writes to the real tree.

Exit code: 0 when every mutation was caught, 1 when any survived or was missing, 2 when the baseline was already red.
Standard library only. Pytest runs inside the copy with the repo's own virtualenv Python when it finds `.venv`, else the Python running this script.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_INCLUDE = ["backend", "runner", "local/src", "docs", ".sonic", "local_function_app", "tests"]
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".venv", ".git", ".pytest_cache"}
DEFAULT_EXCLUDE = ["backend/sandbox"]          # tens of megabytes of site-packages; add --include backend/sandbox if the tests need it
ROOT_FILE_SUFFIXES = (".py", ".txt")


def find_root(start: Path) -> Path:
    for folder in [start, *start.parents]:
        if (folder / ".git").exists() or (folder / "AGENTS.md").exists():
            return folder
    return start


def copy_code(root: Path, dest: Path, include, exclude) -> None:
    excluded = {Path(p).as_posix() for p in exclude}

    def ignore(folder, names):
        rel = Path(folder).relative_to(root).as_posix()
        return [n for n in names if n in SKIP_DIR_NAMES or (f"{rel}/{n}" if rel != "." else n) in excluded]

    for item in include:
        source = root / item
        if source.is_dir():
            shutil.copytree(source, dest / item, ignore=ignore)
        elif source.is_file():
            (dest / item).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / item)
    for source in root.iterdir():
        if source.is_file() and source.suffix in ROOT_FILE_SUFFIXES:
            shutil.copy2(source, dest / source.name)


def run_tests(python: str, cwd: Path, pytest_args, timeout: int):
    args = list(pytest_args)
    for flag in ("-x", "-q"):
        if flag not in args:
            args.append(flag)
    if "no:cacheprovider" not in args:
        args += ["-p", "no:cacheprovider"]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.setdefault("GOOGLE_API_KEY", "test-not-real")          # the project's rule: tests never use a real key
    try:
        done = subprocess.run([python, "-m", "pytest", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mutations", help="a JSON file: a list of {file, old, new, label[, count]}")
    parser.add_argument("pytest_args", nargs="*", help="after `--`: the tests to run, as for pytest")
    parser.add_argument("--root", help="the repository root (default: found from the current folder)")
    parser.add_argument("--python", help="the Python to run pytest with (default: the repo's .venv, else this one)")
    parser.add_argument("--timeout", type=int, default=420, help="seconds allowed for one test run (default 420; a timeout counts as caught but is reported)")
    parser.add_argument("--include", action="append", default=[], help="an extra file or folder to copy (repeatable)")
    parser.add_argument("--exclude", action="append", default=[], help="a folder to leave out of the copy (repeatable)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary copy and print where it is")
    options = parser.parse_args()

    root = Path(options.root).resolve() if options.root else find_root(Path.cwd().resolve())
    venv = root / ".venv" / "Scripts" / "python.exe"
    venv_posix = root / ".venv" / "bin" / "python"
    python = options.python or (str(venv) if venv.exists() else str(venv_posix) if venv_posix.exists() else sys.executable)
    mutations = json.loads(Path(options.mutations).read_text(encoding="utf-8"))
    if not options.pytest_args:
        parser.error("give the tests to run after `--`")

    temp = Path(tempfile.mkdtemp(prefix="mutation-check-"))
    try:
        copy_code(root, temp, DEFAULT_INCLUDE + options.include, DEFAULT_EXCLUDE + options.exclude)
        code, output = run_tests(python, temp, options.pytest_args, options.timeout)
        if code != 0:
            print("The baseline is already red in the copy, so nothing can be learned. Fix this first (or add what the tests need with --include):")
            print("\n".join(output.strip().splitlines()[-15:]))
            return 2
        print(f"baseline green ({len(mutations)} mutations to try)", flush=True)
        caught = survived = missing = 0
        for number, mutation in enumerate(mutations, 1):
            label = mutation.get("label") or f"mutation {number}"
            target = temp / mutation["file"]
            original = target.read_text(encoding="utf-8")
            if mutation["old"] not in original:
                print(f"MISSING   {label}   (the text to change is not in {mutation['file']})", flush=True)
                missing += 1
                continue
            target.write_text(original.replace(mutation["old"], mutation["new"], int(mutation.get("count", 1))), encoding="utf-8")
            try:
                code, _ = run_tests(python, temp, options.pytest_args, options.timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            if code == 0:
                survived += 1
                print(f"SURVIVED  {label}", flush=True)
            else:
                caught += 1
                print(f"caught    {label}" + ("   (timed out)" if code == 124 else ""), flush=True)
        print(f"\ncaught {caught}, survived {survived}, missing {missing}")
        if survived:
            print("A survivor means no test checks that behaviour: add the test, then rerun just that mutation.")
        return 1 if (survived or missing) else 0
    finally:
        if options.keep:
            print(f"copy kept at {temp}")
        else:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
