"""Replays REAL changes from this repository's own history as coding tasks, and grades the result by running the tests.

Each task is a change that was really made (a commit) with tests that came with it. The model is handed the repository as it was just BEFORE the change, as the person's connected folder, and a ticket asking for the
change; it works through the app's own edit path (orientation, the project's guidance file, `propose_local_edits` checked against the files). Nothing is written to that copy. The edits it proposes are applied to a
second copy, the tests the real commit added or changed are dropped on top (the model never sees them), and pytest decides: the task passes when they pass.

Why this and not keywords in the answer (behavior_eval.py's codegen scenarios): a correct change that is written another way is not marked wrong, and one that looks right but breaks is not marked right. A task is
only worth running when the real change passes its own tests and the untouched base fails them (`--validate`, no model, no cost).

    python -m backend.evals.replay_eval --list
    python -m backend.evals.replay_eval --validate
    python -m backend.evals.replay_eval --tool-model haiku,sonnet --runs 1 --ceiling 1.00 --save results.json

Real model calls cost money: nothing runs a model without --tool-model. `docs/` and the like are left out of the copy the model sees (a plan written before the change would hand it the answer).
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.evals import behavior_eval, eval_models

REPO = Path(__file__).resolve().parents[2]
HIDDEN_PREFIXES = ("docs/", ".claude/", ".github/")   # plans and notes written around a change would give its answer away
SKIPPED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ico", ".woff", ".woff2", ".zip", ".pptx", ".docx", ".mp4", ".svg", ".lock", ".map")
MAX_FILE_BYTES = 400_000
TEST_TIMEOUT_SECONDS = 420
SECRET_ENV = ("MONGO_URI", "MONGODB_URI", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "GITHUB_TOKEN", "GOOGLE_API_KEY", "LLM_PRICES")


@dataclass
class Task:
    id: str
    commit: str            # the real change; its first parent is the state the model is given
    prompt: str            # the ticket: what the person asks for, with the names the tests check
    tests: List[str]       # test files, as they are at `commit`, that decide the task
    notes: str = ""
    # (path, import line): what the real change imported that a model often leaves out. A run that fails with a NameError is tested a second time with this line added, to say whether the missing import was
    # the ONLY thing wrong (a crash at import stops every test, so it hides anything else a change got wrong). Diagnostic only: it never turns a miss into a pass.
    repair: Optional[Any] = None
    # Why this task is not in a default run: its hidden tests pin exact wording or source layout, so a correct change can fail them (see the plan's replay section). Runs only with --only or --all.
    brittle: str = ""

    @property
    def base(self) -> str:
        return f"{self.commit}^1"


TASKS: List[Task] = [
    Task(
        "quiet_by_risk_level", "b448b2a4",
        "Sonic stays quiet for a fixed 7 days (RISK_QUIET_DAYS in backend/utils/initiation.py) after a safety escalation, which is far too long once the person is out of it. Change it so the quiet lasts only until the "
        "safety layer itself would relax that level. In backend/utils/initiation.py: replace RISK_QUIET_DAYS with RISK_QUIET_HOURS, a dict {RISK_ELEVATED: ELEVATED_HOURS, RISK_ACUTE: AFTER_CRISIS_HOURS, "
        "RISK_IMMINENT: AFTER_CRISIS_HOURS} (all five names are in backend/utils/safety_utils.py); give note_risk a fourth parameter, level=RISK_IMMINENT (its first three stay as they are), so it is quiet for "
        "RISK_QUIET_HOURS.get(level, AFTER_CRISIS_HOURS) hours from `now` (it must still only ever extend the quiet, never shorten it); make risk_escalated(username, level=RISK_IMMINENT, now=None) pass the level "
        "through; and in app.py hand the risk level in force at the call site (the `risk_level` variable) to it, as `spawn_background_task(asyncio.to_thread(initiation_utils.risk_escalated, username, risk_level))`. "
        "Update the comments that say a week.",
        ["backend/tests/test_initiation.py", "backend/tests/test_ops_digest.py"],
        "a small change across three places with a new constant, a changed signature and a call site; the hidden tests pin the names",
        repair=("backend/utils/initiation.py", "from backend.utils.safety_utils import AFTER_CRISIS_HOURS, ELEVATED_HOURS, RISK_ACUTE, RISK_ELEVATED, RISK_IMMINENT"),
    ),
    Task(
        "page_open_errors", "fafcc6f6",
        "When a look cannot open a page, the runner says 'The page could not be opened (<code>). Is the server running on that port?' for every error, even ones the look itself caused, which blames the person's "
        "setup wrongly. Find where the runner builds that message (under runner/src/saapp_runner/). Add a pure function open_error(error_text) to runner/src/saapp_runner/localpage.py and use it where that message is "
        "built. It takes the error text Chrome or Edge reports, uses the existing one_line(error_text, 60) for the code, and returns a message that always starts with 'The page could not be opened' and stays under 400 "
        "characters. By code: ERR_CONNECTION_REFUSED, ERR_CONNECTION_RESET or ERR_EMPTY_RESPONSE: say 'nothing answered on that port' and ask whether the server is running there; ERR_NAME_NOT_RESOLVED or "
        "ERR_ADDRESS_INVALID: say the look's own page-opener could not look the address up and that this is 'a fault in the look, not in the person's setup' (and never ask whether the server is running); "
        "ERR_BLOCKED_BY_CLIENT: say the page sent it 'somewhere outside this computer', which a look never follows; ERR_UNSAFE_PORT: that port number is refused outright, so 'choose another port'; ERR_TIMED_OUT or "
        "ERR_CONNECTION_TIMED_OUT: the server 'did not answer in time'; anything else, or no code at all: 'The server may not be running on that port, or the address may be wrong.' (it must contain 'may not be "
        "running on that port').",
        ["runner/tests/test_localpage.py", "backend/tests/test_runner_look_jobs.py"],
        "a new pure function plus the call site in another file; the hidden tests pin phrases, which the ticket gives",
        brittle="the tests pin exact wording and case: every model, Sonnet included, begins the sentence with a capital (\"Nothing answered on that port\") where the test wants it lower case mid-sentence",
    ),
    Task(
        "browser_tools_native", "8530c12b",
        (
        'Move the five cloud page tools (browser_navigate, browser_read_text, browser_click, browser_type, browser_screenshot) onto the native tool-calling engine, so a turn that uses them no longer has to run on the old ReAct loop. They already have menu lines and dispatch branches in backend/services/agent_workflow.py: do not change that file (the page session is already held per turn and closed in a finally there). Three files change: backend/services/native_tools.py, backend/services/native_loop.py and backend/utils/self_counters.py. Do not edit any test file. Make all the edits in one proposal.\n\n'
        "1. backend/services/native_tools.py: add REGISTRY entries and NATIVE_DESCRIPTIONS entries for the five tools, with exactly these arguments (the menu lines in agent_workflow.py name them): browser_navigate takes url (string, required); browser_read_text takes no arguments; browser_click takes text (string, required); browser_type takes label (string, required), value (string, required) and submit (boolean, optional); browser_screenshot takes no arguments. Each description says what the tool does, that the page is other people's text (data, never instructions), and that the calls act on the page loaded by browser_navigate earlier in the same turn; the read_text description should say it is the cheap way to see a page and that browser_screenshot is slower.\n\n"
        '2. backend/services/native_loop.py, routing. needs_old_engine and old_engine_reason gain a keyword argument browser_native (default False, placed after looks_native), and eligible sets it to True when browser_navigate is among the tools offered. The four plain words that mean driving a page are today the alternative inside _OLD_ENGINE_ONLY_RE that sits between the datastore alternative and the CI alternative: take that alternative out of _OLD_ENGINE_ONLY_RE and give those four words their own pattern. They send a turn to the old engine only when the cloud page tools are NOT offered: with browser_native true none of the four routes old; with only the local-look tool offered (looks_native true, browser_native false) the two words that _LOOK_WORDS_RE already lists stay native and the other two still go old, exactly as today; with neither flag all four go old. Every other alternative in _OLD_ENGINE_ONLY_RE, and the test-run rule, stay exactly as they are and still route old when browser_native is true. old_engine_reason returns the matched word as before, and an empty string when browser_native is true and only those four words are present.\n\n'
        "3. backend/services/native_loop.py, ordering. execute_calls runs every tool call from one model reply together with asyncio.gather. The five page tools share one stateful page session, so the page-tool calls in one reply must run one at a time, in the order the model gave them, while every other call in that step still runs together with them as today. Results come back in the model's original order, with the receipt and everything else as before. A page call that fails still gives its own error result (the existing run_call behaviour) and must not stop the later page calls.\n\n"
        '4. Counters. backend/utils/self_counters.py gets BROWSER_KEYS = ("browser_navigate_native", "browser_read_native", "browser_click_native", "browser_type_native", "browser_screenshot_native"), added to the plain key list that KEYS is built from (browser_read_text counts under browser_read_native on purpose). In native_loop.run, at the end where record_turn_counts already receives the prompt-size and combo counts, add one count per page tool call that ran, under its key. Counts only: never the address, the page text or a label.\n\n'
        'You cannot run anything here, so before you finish check by reading that every name your new code uses is defined or imported in the file it is used in.'
        ),
        ["backend/tests/test_native_browser_tools.py", "backend/tests/test_native_browser_session_teardown.py", "backend/tests/test_native_eligibility.py", "backend/tests/test_native_loop.py"],
        "the browser-tool migration to the native engine (PR #152, done by Sonic on Sonnet, 2026-11-03/04): five schemas, a routing flag, ordered page calls and five counters across three files. Written with the final names and behaviour as a full spec (the original brief had less), and the model cannot run anything or use plan mode here, so a slip such as a missing import is not caught the way a real session would catch it",
    ),
]


# ---------------------------------------------------------------------------------------------------------------
# Getting a repository state onto disk, and running tests against one
# ---------------------------------------------------------------------------------------------------------------

def _git(*args: str, binary: bool = False) -> Any:
    done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, check=True)
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def extract(rev: str, dest: str) -> int:
    """The tracked files of `rev` written under `dest`, leaving out what a model should not see (hidden prefixes), binaries and very large files. Returns how many files were written."""
    archive = _git("archive", "--format=tar", rev, binary=True)
    written = 0
    root = Path(dest).resolve()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if not member.isfile() or member.size > MAX_FILE_BYTES:
                continue
            name = member.name
            if name.startswith(HIDDEN_PREFIXES) or name.lower().endswith(SKIPPED_SUFFIXES):
                continue
            target = (root / name).resolve()
            if root not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
            written += 1
    return written


def overlay_tests(task: Task, dest: str) -> None:
    """The real commit's version of each deciding test file, written over whatever is there (the model's own tests included)."""
    for path in task.tests:
        target = Path(dest) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_git("show", f"{task.commit}:{path}", binary=True))


def run_tests(task: Task, folder: str) -> Dict[str, Any]:
    """pytest on the task's tests inside `folder`, importing the code in `folder` (not this checkout), with no keys or database in the environment."""
    env = {key: value for key, value in os.environ.items() if key not in SECRET_ENV}
    env.update({"GOOGLE_API_KEY": "test-not-real", "USE_DB": "false", "PYTHONPATH": os.pathsep.join([folder, str(Path(folder) / "runner" / "src")]), "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", *task.tests, "-q", "-p", "no:cacheprovider", "--no-header"],
            cwd=folder, env=env, capture_output=True, timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "summary": f"timed out after {TEST_TIMEOUT_SECONDS}s", "tail": ""}
    text = done.stdout.decode("utf-8", "replace") + done.stderr.decode("utf-8", "replace")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return {"passed": done.returncode == 0, "summary": lines[-1] if lines else "no output", "tail": "\n".join(lines[-12:])}


def insert_import(text: str, line: str) -> str:
    """`line` added after the last top-level import of `text` (a multi-line parenthesised import counts to its closing bracket); a file that already has it is returned unchanged."""
    if line in text:
        return text
    lines = text.split(chr(10))
    last = -1
    index = 0
    while index < len(lines):
        if lines[index].startswith(("import ", "from ")):
            end = index
            if "(" in lines[index] and ")" not in lines[index]:
                while end < len(lines) - 1 and ")" not in lines[end]:
                    end += 1
            last = end
            index = end
        index += 1
    lines.insert(last + 1, line)
    return chr(10).join(lines)


def apply_proposals(proposals: List[Dict[str, Any]], folder: str) -> Dict[str, Any]:
    """The model's proposals (what propose_local_edits emitted) applied in order to `folder` with the app's own validator. A proposal that does not apply cleanly is skipped and counted: the person would have
    seen an error and a retry, so a later good one still lands."""
    from backend.services.local_edits import validate_edit_proposal

    def read(path: str) -> Optional[str]:
        target = Path(folder) / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    touched: List[str] = []
    skipped = 0
    for proposal in proposals:
        result = validate_edit_proposal(proposal.get("edits"), read)
        if not result.get("ok"):
            skipped += 1
            continue
        for path, text in (result.get("texts") or {}).items():
            target = Path(folder) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            touched.append(path)
        for card in result.get("files") or []:
            if card.get("kind") == "delete" and (Path(folder) / card["path"]).is_file():
                (Path(folder) / card["path"]).unlink()
                touched.append(card["path"])
    return {"touched": sorted(set(touched)), "skipped": skipped}


def real_files(task: Task) -> List[str]:
    """The files the real change touched, apart from the tests it came with and the notes around it."""
    names = _git("diff", "--name-only", task.base, task.commit).splitlines()
    return sorted(name for name in names if name not in task.tests and not name.startswith(HIDDEN_PREFIXES) and "/tests/" not in name)


# ---------------------------------------------------------------------------------------------------------------
# Validating a task (no model)
# ---------------------------------------------------------------------------------------------------------------

def validate(task: Task) -> Dict[str, Any]:
    """A task is fit to run when the real change passes its own tests and the state before it fails them."""
    out: Dict[str, Any] = {"task": task.id}
    with tempfile.TemporaryDirectory(prefix="replay_validate_") as workdir:
        base_dir, real_dir = str(Path(workdir) / "base"), str(Path(workdir) / "real")
        extract(task.base, base_dir)
        overlay_tests(task, base_dir)
        out["base"] = run_tests(task, base_dir)
        extract(task.commit, real_dir)
        out["real"] = run_tests(task, real_dir)
    out["fit"] = (not out["base"]["passed"]) and out["real"]["passed"]
    return out


# ---------------------------------------------------------------------------------------------------------------
# One model run on one task
# ---------------------------------------------------------------------------------------------------------------

async def _never_called(prompt: Any) -> Any:
    raise RuntimeError("the react engine is not used by the replay eval")


async def run_task(task: Task, tool_model: str) -> Dict[str, Any]:
    behavior_eval.ACTIVE_TOOL_MODEL = tool_model
    behavior_eval.ACTIVE_ENGINE = "native"
    behavior_eval.ACTIVE_MEMORY = False
    started = time.time()
    record: Dict[str, Any] = {"task": task.id, "model": tool_model or "default", "passed": False, "why": ""}
    with tempfile.TemporaryDirectory(prefix="replay_run_") as workdir:
        seen_dir, work_dir = str(Path(workdir) / "seen"), str(Path(workdir) / "work")
        extract(task.base, seen_dir)
        extract(task.base, work_dir)
        proposals: List[Dict[str, Any]] = []
        try:
            observation = await behavior_eval.run_tool_agent(_never_called, task.prompt, engine="native", local_root=seen_dir, proposals=proposals)
            record["calls"] = observation.usage.get("calls", 0)
            record["input"], record["output"], record["cached"] = (observation.usage.get(key, 0) for key in ("input", "output", "cached"))
            record["by_model"] = observation.by_model
            record["cost"] = behavior_eval.cost_of(observation.by_model)
            record["steps"] = len([a for a in observation.actions if a != "(final)"])
            record["actions"] = observation.actions
            record["answer"] = observation.answer[:700]       # what the model said it did: code in this repository, never anyone's messages
        except eval_models.CostCeilingExceeded:
            raise
        except Exception as exc:  # a crash is a failed run, reported with its kind only
            record["why"] = f"the run crashed: {type(exc).__name__}: {str(exc)[:120]}"
            record["crashed"] = True
            return record
        record["proposals"] = len(proposals)
        if not proposals:
            record["why"] = "no change was proposed"
            record["seconds"] = round(time.time() - started)
            return record
        applied = apply_proposals(proposals, work_dir)
        record["touched"], record["skipped_proposals"] = applied["touched"], applied["skipped"]
        record["real_files"] = real_files(task)
        record["edits"] = json.dumps([edit for proposal in proposals for edit in (proposal.get("edits") or [])], default=str)[:30000]      # the change itself: code of this repository
        overlay_tests(task, work_dir)
        tests = run_tests(task, work_dir)
        record["passed"], record["why"] = tests["passed"], "" if tests["passed"] else tests["summary"]
        record["tests_tail"] = tests["tail"] if not tests["passed"] else ""
        if not tests["passed"] and task.repair and "NameError" in tests["tail"]:
            path, line = task.repair
            target = Path(work_dir) / path
            if target.is_file():
                target.write_text(insert_import(target.read_text(encoding="utf-8"), line), encoding="utf-8")
                repaired = run_tests(task, work_dir)
                record["with_import_added"] = {"passed": repaired["passed"], "summary": repaired["summary"], "tail": "" if repaired["passed"] else repaired["tail"]}
    record["seconds"] = round(time.time() - started)
    return record


# ---------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------

def format_report(records: List[Dict[str, Any]]) -> str:
    models = sorted({r["model"] for r in records})
    lines = []
    for model in models:
        lines.append(f"=== {model}")
        lines.append(f"{'task':24} {'pass':>5} {'cost/run':>9} {'calls':>6} {'steps':>6}  files touched (real change: files it touched)")
        for task in TASKS:
            mine = [r for r in records if r["model"] == model and r["task"] == task.id]
            if not mine:
                continue
            ok = sum(1 for r in mine if r["passed"])
            costs = [r["cost"] for r in mine if r.get("cost") is not None]
            calls = [r.get("calls", 0) for r in mine]
            steps = [r.get("steps", 0) for r in mine]
            touched = sorted({p for r in mine for p in r.get("touched", [])})
            lines.append(f"{task.id:24} {ok}/{len(mine):<3} {(sum(costs) / len(costs)) if costs else float('nan'):9.4f} {sum(calls) / len(calls):6.1f} {sum(steps) / len(steps):6.1f}  {touched} (real: {real_files(task)})")
            for record in mine:
                if not record["passed"]:
                    lines.append(f"    miss: {record['why']}")
                    if record.get("with_import_added"):
                        lines.append(f"      with the missing import added: {'PASSES, so that was the only error' if record['with_import_added']['passed'] else 'still fails: ' + record['with_import_added']['summary']}")
        total = [r for r in records if r["model"] == model]
        lines.append(f"overall: {sum(1 for r in total if r['passed'])}/{len(total)} passed, ${sum(r.get('cost') or 0 for r in total):.3f} spent on this model")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay real changes from this repository's history and grade them by their own tests.")
    parser.add_argument("--list", action="store_true", help="list the tasks and exit")
    parser.add_argument("--validate", action="store_true", help="check each task: the real change passes its tests and the state before it fails them (no model is called)")
    parser.add_argument("--only", type=str, default="", help="comma-separated task ids")
    parser.add_argument("--all", action="store_true", help="include the tasks left out of a default run because their tests are brittle")
    parser.add_argument("--tool-model", type=str, default="", help="the model(s) to work with, as for behavior_eval (default, haiku, sonnet, mini, ...; comma-separated)")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--ceiling", type=float, default=eval_models.DEFAULT_CEILING_USD, help="stop starting runs once the estimated spend passes this many dollars")
    parser.add_argument("--save", type=str, default="", help="write the records to this JSON file")
    args = parser.parse_args(argv)

    wanted = {part.strip() for part in args.only.split(",") if part.strip()}
    chosen = [task for task in TASKS if (task.id in wanted if wanted else (args.all or not task.brittle))]
    if args.list:
        for task in chosen:
            print(f"{task.id:24} {task.commit} tests={task.tests}\n    {task.notes}" + (f"\n    NOT IN A DEFAULT RUN: {task.brittle}" if task.brittle else ""))
        return 0
    if args.validate:
        bad = 0
        for task in chosen:
            result = validate(task)
            print(f"{task.id}: base {'FAILS' if not result['base']['passed'] else 'passes (bad)'} [{result['base']['summary']}] | real change {'passes' if result['real']['passed'] else 'FAILS (bad)'} [{result['real']['summary']}] -> {'fit' if result['fit'] else 'NOT FIT'}")
            if not result["real"]["passed"]:
                print(result["real"]["tail"])
            bad += 0 if result["fit"] else 1
        return 1 if bad else 0

    names = [part.strip() for part in args.tool_model.split(",") if part.strip()]
    if not names:
        print("no model named: pass --tool-model (a run costs money; --validate and --list do not)")
        return 2
    models = ["" if name == "default" else name for name in names]
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))      # before the key check below, which reads the environment
    for name in models:
        if name and name != "deep" and eval_models.is_candidate(name) and eval_models.missing_key(name):
            print(f"{name}: {eval_models.missing_key(name)}")
            return 2
    tracker = eval_models.SpendTracker(args.ceiling)
    records: List[Dict[str, Any]] = []

    async def go() -> None:
        with eval_models.tracking(tracker):
            for model in models:
                for task in chosen:
                    for _ in range(args.runs):
                        tracker.check()
                        print(f"running {task.id} on {model or 'default'} ...", flush=True)
                        record = await run_task(task, model)
                        records.append(record)
                        print(f"  {'PASS' if record['passed'] else 'MISS'} {record.get('why', '')[:140]} | cost ${record.get('cost') or 0:.4f} | {tracker.line()}", flush=True)

    try:
        asyncio.run(go())
    except eval_models.CostCeilingExceeded as exc:
        print(f"STOPPED: {exc}; {len(records)} run(s) finished")
    print(format_report(records))
    if args.save:
        Path(args.save).write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M"), "records": records}, indent=1, default=str), encoding="utf-8")
        print(f"saved to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
