"""Triggers and polls this repo's own "Patchy Tests" GitHub Actions workflow
(.github/workflows/patchy-tests.yml) so tool_agent_node can get REAL pass/fail ground truth on a
test command instead of just reasoning about whether proposed code would work. Deliberately reuses
existing CI infrastructure rather than building a new sandbox — python_sandbox.py's WASM sandbox
has zero filesystem access by design (that's its actual security boundary), so it structurally
can never verify real application code against real dependencies; a bigger WASM sandbox wouldn't
change that. This repo's own CI already checks out the real branch and installs the real
requirements.txt, which is exactly what's needed.

Every public function here is synchronous (plain `requests`/`time.sleep`), matching every other
GitHub action in agent_workflow.py — callers run this via asyncio.to_thread, same as the rest of
_dispatch_github. Never raises: every failure mode returns an "ERROR: ..." string, matching this
codebase's established convention for tool_agent_node observations.
"""
import os
import re
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger("SASS Logger")

WORKFLOW_FILE = "patchy-tests.yml"
DISPATCH_LOOKUP_RETRIES = 6
DISPATCH_LOOKUP_DELAY_SECONDS = 2
POLL_INTERVAL_SECONDS = 8
DEFAULT_MAX_WAIT_SECONDS = int(os.getenv("CI_TEST_RUN_TIMEOUT_SECONDS", "240"))
_FAILURE_LOG_TAIL_CHARS = 2000
_SNIPPET_MAX_CHARS = 20000

# Mirrors .github/workflows/patchy-tests.yml's own validation exactly — checked here first so an
# invalid command fails immediately instead of burning a real CI dispatch plus minutes of polling
# only to discover the workflow itself was always going to reject it.
_SAFE_TEST_COMMAND_RE = re.compile(
    r'^(python -m )?pytest [a-zA-Z0-9_./-]+(::[a-zA-Z_][a-zA-Z0-9_]*)*$'
)
_UNSAFE_SUBSTRINGS = ("..", ";", "|", ">", "<")


def _validate_test_commands(test_commands: str) -> Optional[str]:
    """Returns a human-readable rejection reason if any line is invalid, else None."""
    lines = [line.strip() for line in (test_commands or "").strip().splitlines() if line.strip()]
    if not lines:
        return "no test command given"
    for line in lines:
        if not _SAFE_TEST_COMMAND_RE.match(line):
            return f"'{line}' doesn't match the required 'pytest <path>[::Name::test_name]' shape"
        if any(bad in line for bad in _UNSAFE_SUBSTRINGS):
            return f"'{line}' contains a disallowed character"
    return None


def _parse_iso_to_epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _fetch_run_log(repo: str, run_id: int, headers: dict, api_base: str) -> str:
    """Best-effort: grabs the job's log so the caller has real detail to work with, not just a
    bare verdict — used on failure only by run_repo_tests (a passing test's happy-path detail
    doesn't matter there), but unconditionally by run_python_snippet (the printed output IS the
    point of running a snippet, pass or fail). Never raises — returns "" on any problem, since the
    verdict itself (returned by the caller regardless) is the part that always matters."""
    try:
        jobs_res = requests.get(f"{api_base}/repos/{repo}/actions/runs/{run_id}/jobs", headers=headers)
        jobs = jobs_res.json().get("jobs", [])
        if not jobs:
            return ""
        job_id = jobs[0]["id"]
        log_res = requests.get(f"{api_base}/repos/{repo}/actions/jobs/{job_id}/logs", headers=headers)
        if log_res.status_code != 200:
            return ""
        return f"Log excerpt (last {_FAILURE_LOG_TAIL_CHARS} chars):\n{log_res.text[-_FAILURE_LOG_TAIL_CHARS:]}"
    except Exception:
        logger.exception("[ci_test_runner] Could not fetch log for run %s", run_id)
        return ""


def _dispatch_and_wait(
    repo: str, branch: str, inputs: dict, headers: dict, api_base: str, max_wait_seconds: int,
) -> dict | str:
    """Shared by run_repo_tests and run_python_snippet: dispatches patchy-tests.yml on `branch`
    with the given workflow_dispatch `inputs`, locates the resulting run (workflow_dispatch's own
    response never includes a run id, so this looks it up by branch+event shortly afterward), and
    polls until it completes or max_wait_seconds elapses. Returns {"run_id", "run_url",
    "conclusion"} on completion, or an "ERROR: ..." string on any failure — callers only need to
    handle those two shapes, not the dispatch/lookup/poll mechanics themselves."""
    dispatch_url = f"{api_base}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    dispatch_time = time.time()
    try:
        res = requests.post(dispatch_url, headers=headers, json={"ref": branch, "inputs": inputs})
    except Exception as e:
        return f"ERROR: could not dispatch the workflow: {e}"
    if res.status_code != 204:
        return f"ERROR: could not dispatch the workflow ({res.status_code}): {res.text[:300]}"

    run = None
    runs_url = f"{api_base}/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
    for _ in range(DISPATCH_LOOKUP_RETRIES):
        time.sleep(DISPATCH_LOOKUP_DELAY_SECONDS)
        try:
            list_res = requests.get(
                runs_url, headers=headers,
                params={"branch": branch, "event": "workflow_dispatch", "per_page": 5},
            )
        except Exception:
            continue
        if list_res.status_code != 200:
            continue
        candidates = [
            r for r in list_res.json().get("workflow_runs", [])
            if r.get("created_at") and _parse_iso_to_epoch(r["created_at"]) >= dispatch_time - 5
        ]
        if candidates:
            run = min(candidates, key=lambda r: r["created_at"])
            break
    if run is None:
        return (
            "ERROR: dispatched the workflow but could not locate the resulting run — "
            "check the repo's Actions tab directly"
        )

    run_id = run["id"]
    run_url = run["html_url"]
    run_status_url = f"{api_base}/repos/{repo}/actions/runs/{run_id}"

    elapsed = DISPATCH_LOOKUP_RETRIES * DISPATCH_LOOKUP_DELAY_SECONDS
    while elapsed < max_wait_seconds:
        try:
            status_res = requests.get(run_status_url, headers=headers)
        except Exception as e:
            return f"ERROR: could not check run status: {e}"
        if status_res.status_code != 200:
            return f"ERROR: could not check run status ({status_res.status_code})"
        data = status_res.json()
        if data.get("status") == "completed":
            return {"run_id": run_id, "run_url": run_url, "conclusion": data.get("conclusion") or "unknown"}
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    return f"ERROR: run did not finish within {max_wait_seconds}s — check it directly: {run_url}"


def run_repo_tests(
    repo: str, branch: str, test_commands: str, headers: dict, api_base: str,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
) -> str:
    """Dispatches patchy-tests.yml on `branch` with the given pytest command(s), polls until it
    finishes (or max_wait_seconds elapses), and returns the real pass/fail result — optionally
    with a failure log excerpt. Blocking; the caller runs this via asyncio.to_thread."""
    invalid_reason = _validate_test_commands(test_commands)
    if invalid_reason:
        return f"ERROR: invalid test_commands — {invalid_reason}"

    result = _dispatch_and_wait(repo, branch, {"test_commands": test_commands}, headers, api_base, max_wait_seconds)
    if isinstance(result, str):
        return result

    summary = f"Test run {result['conclusion'].upper()}: {result['run_url']}"
    if result["conclusion"] == "failure":
        log_excerpt = _fetch_run_log(repo, result["run_id"], headers, api_base)
        return f"{summary}\n{log_excerpt}" if log_excerpt else summary
    return summary


def run_python_snippet(
    repo: str, branch: str, snippet: str, headers: dict, api_base: str,
    max_wait_seconds: int = DEFAULT_MAX_WAIT_SECONDS,
) -> str:
    """The Tier 1 real-execution path (see docs/coding-agent-roadmap.md): dispatches
    patchy-tests.yml's ad hoc Python snippet step on `branch`, so a proposed function/fix gets
    actually imported and run against this repo's real installed dependencies — not just reasoned
    about — before being presented as verified. Runs in the same ephemeral, secret-free GitHub
    Actions runner run_repo_tests already uses (never on the production host, never with
    production credentials), which is what makes this safe to run against an arbitrary repo/token
    once per-user repos exist, not just this one. Unlike run_repo_tests (which only fetches the
    log on failure, since a passing test's happy-path detail doesn't matter), this ALWAYS returns
    the log excerpt — the printed output is the actual point of running a snippet, not just a
    pass/fail verdict. Blocking; the caller runs this via asyncio.to_thread."""
    if not (snippet or "").strip():
        return "ERROR: no snippet given"
    if len(snippet) > _SNIPPET_MAX_CHARS:
        return f"ERROR: snippet is {len(snippet)} chars, over the {_SNIPPET_MAX_CHARS}-char limit"

    result = _dispatch_and_wait(repo, branch, {"python_snippet": snippet}, headers, api_base, max_wait_seconds)
    if isinstance(result, str):
        return result

    summary = f"Snippet run {result['conclusion'].upper()}: {result['run_url']}"
    log_excerpt = _fetch_run_log(repo, result["run_id"], headers, api_base)
    return f"{summary}\n{log_excerpt}" if log_excerpt else summary
