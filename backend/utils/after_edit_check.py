"""Which of the person's own checks fits a change they just applied, so the check runs (or is named) in the same turn instead of being left to the model's choice.

Measured 2026-11-27: with a runner connected on 104 coding turns, only 3 of 23 edits since 11-17 were checked afterwards (32% before), and the loader change was called done with a logo test failing.
After an Apply, code picks ONE check from what changed and the project's own files:

- Python files changed: the project's pytest check (one that takes `{files}`), limited to the tests that belong to the change: test files that were changed, plus `test_<name>.py` for each changed
  module found anywhere in the folder. Never the whole suite (this repository's takes 15 minutes). No matching test file: no Python check.
- TypeScript changed: the type check (`tsc`), which is whole-project but quick.
- A JavaScript/TypeScript test file changed (`.test.mjs`, `.test.ts`, ...) and there is no type check: the project's JS test script.

It runs without a press only when the person turned that on for this runner (runner_jobs settings `after_edit_auto`, off until they do); otherwise the Apply result names the check so the model runs it
(the person then presses Run on the usual card). Pure: no I/O, no clock.
"""

from __future__ import annotations

import posixpath
from typing import Any, Dict, Iterable, List, Optional

MAX_TEST_FILES = 6
WAIT_SECONDS = 240          # how long an automatic run may hold the turn; a slower check is reported as not finished and the model can carry on
_JS_TEST_SUFFIXES = (".test.mjs", ".test.js", ".test.ts", ".test.tsx", ".spec.ts", ".spec.js")
_TS_SUFFIXES = (".ts", ".tsx")


def _is_python_test(path: str) -> bool:
    name = posixpath.basename(path)
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _check(checks: Iterable[Dict[str, Any]], *, takes_files: bool = False, word: str = "", name: str = "") -> Optional[Dict[str, Any]]:
    for item in checks:
        command = str(item.get("command") or "")
        if item.get("ready") is False:
            continue
        if name and item.get("name") != name:
            continue
        if word and word not in command:
            continue
        if takes_files and "{files}" not in command:
            continue
        return item
    return None


def python_tests_for(changed: Iterable[str], folder_paths: Iterable[str]) -> List[str]:
    """The test files that belong to a change: changed test files first, then `test_<module>.py` / `<module>_test.py` found anywhere in the folder for each changed module. At most MAX_TEST_FILES."""
    changed = [str(path) for path in changed if str(path).endswith(".py")]
    found: List[str] = [path for path in changed if _is_python_test(path)]
    wanted = {f"test_{posixpath.basename(path)}" for path in changed if not _is_python_test(path)}
    wanted |= {posixpath.basename(path)[:-3] + "_test.py" for path in changed if not _is_python_test(path)}
    for path in folder_paths:
        if posixpath.basename(str(path)) in wanted and str(path) not in found:
            found.append(str(path))
    return found[:MAX_TEST_FILES]


def pick(changed: Iterable[str], checks: Iterable[Dict[str, Any]], folder_paths: Iterable[str]) -> Optional[Dict[str, Any]]:
    """{"check": name, "files": [...]} for the one check that fits the applied change, or None when none of the project's checks fits it."""
    changed = [str(path) for path in changed if path]
    checks = list(checks or [])
    if not changed or not checks:
        return None
    tests = python_tests_for(changed, folder_paths)
    if tests:
        pytest_check = _check(checks, takes_files=True, word="pytest")
        if pytest_check:
            return {"check": pytest_check["name"], "files": tests}
    if any(path.endswith(_TS_SUFFIXES) for path in changed):
        types = _check(checks, word="tsc")
        if types:
            return {"check": types["name"], "files": []}
    if any(path.endswith(_JS_TEST_SUFFIXES) for path in changed):
        js_tests = _check(checks, word="npm") or _check(checks, name="js-tests")
        if js_tests:
            return {"check": js_tests["name"], "files": []}
    return None


def describe(choice: Dict[str, Any]) -> str:
    files = choice.get("files") or []
    return f"{choice['check']}" + (f" on {', '.join(files)}" if files else "")


def suggestion(choice: Dict[str, Any]) -> str:
    """The Apply result's line when the check does not run by itself: the exact call, so checking costs the model no thinking."""
    files = choice.get("files") or []
    call = f"run_local_check with check \"{choice['check']}\"" + (f" and files {files}" if files else "")
    return f" A check of theirs fits this change: call {call} now, before you answer, and fix what it finds."


def result_note(choice: Dict[str, Any], formatted: str, passed: bool) -> str:
    """The Apply result's line after the check ran by itself: its real result, and what to do with it."""
    head = f"\n\nTHEIR CHECK RAN BY ITSELF AFTER THE APPLY ({describe(choice)}):\n{formatted}"
    if passed:
        return head + "\nIt passed: do not run the same check again for this change."
    return head + "\nIf it failed because of this change, fix that now with propose_local_edits before you answer. If the failure is not from this change, say so plainly in your answer."
