"""A claim that a check passed is true only if a check ran and passed in THIS turn. Pure, no model call, nothing logged.

WHY. On 2026-10-30 Sonic told Jack "the changes pass `npx tsc -b` and the node test suite cleanly" when it had not run either, and the type-check failed. Telling the model not to (backend/utils/code_plan.py
EXECUTE_RULES) is a request; this is the check in code. Three parts:

- `claimed_kinds(text)`: which kinds of check a reply says passed (type-check, tests, lint, build). A sentence counts only when it names a kind and a passing word together and is not hedged, negated, a
  question or a plan ("should pass", "did not run", "want me to run the tests?").
- `evidence(attempts)`: which kinds really ran in the turn and how the LAST run of each ended, read from the tool results the runner and the sandbox produce ("LOCAL CHECK x PASSED", "LOCAL COMMAND `...`
  FINISHED with exit code 0"). A command line that joins several programs with && counts for each, because exit code 0 means none of them reported a problem.
- `unproven(text, status)` and `note_for(...)`: the claimed kinds with no passing run, and the one plain line that says so.

The loop sends the model back once to run the check (when it can) or reword; app.py appends the line to the composed reply when a claim is still unbacked. A check that was run and failed, with a reply that
says it passed, is the worst case, and reads the same here: the claim is not backed.
"""
import re
from typing import Any, Dict, Iterable, List, Set

KINDS = ("typecheck", "tests", "lint", "build")
LABEL = {"typecheck": "the type-check", "tests": "the tests", "lint": "the lint check", "build": "the build"}
_PLURAL = {"tests"}
PASSED, FAILED = "passed", "failed"

# What a reply calls each kind of check.
_REPLY_WORDS = {
    "typecheck": re.compile(r"\b(?:tsc|type[- ]?check(?:s|ing|ed)?|typecheck|mypy|pyright)\b", re.I),
    "tests": re.compile(r"\b(?:tests?|test suites?|pytest|unit tests?|specs?|vitest|jest)\b", re.I),
    "lint": re.compile(r"\b(?:lint(?:er|ing)?|eslint|ruff|flake8)\b", re.I),
    "build": re.compile(r"\b(?:builds?|compiles?|compilation)\b", re.I),
}
_PASSING = re.compile(r"\b(?:pass(?:es|ed|ing)?|green|clean(?:ly)?|succe(?:ed|eds|eded|ssful(?:ly)?|ss)|no (?:errors?|failures?|issues?)|without (?:errors?|failures?|issues?)|all good)\b", re.I)
# A sentence that is not a claim of something done: a plan, a hope, a condition, a question, a negation, or a report of failure.
_NOT_A_CLAIM = re.compile(
    r"\b(?:should|would|will|won'?t|might|may|could|can|hopefully|expect(?:ed|s)?|once|until|unless|whether|if|when|before|after|need(?:s)? to|want|wants|let me know|please|ready to|next|remaining|"
    r"run|confirm|verify|make|makes|making|check that|"
    r"still|haven'?t|have not|hasn'?t|did not|didn'?t|do not|don'?t|not|never|unable|couldn'?t|can'?t|cannot|wasn'?t|weren'?t|isn'?t|aren'?t|no tests?|fail(?:s|ed|ing|ure|ures)?|to make|so that)\b",
    re.I,
)
_FOLLOW_UP = re.compile(r"<<<FOLLOW_UP:.*?>>>", re.S)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")

# What a command line or check name runs.
_RUN_WORDS = {
    "typecheck": re.compile(r"\b(?:tsc|mypy|pyright|type[- ]?check)\b", re.I),
    "tests": re.compile(r"\b(?:pytest|vitest|jest|unittest|node\s+--test|npm\s+(?:run\s+)?test|npx\s+(?:vitest|jest))\b", re.I),
    "lint": re.compile(r"\b(?:ruff|eslint|flake8|lint)\b", re.I),
    "build": re.compile(r"\b(?:vite\s+build|npm\s+run\s+build|cargo\s+build|go\s+build|make\s+build|webpack)\b", re.I),
}
_TESTS_BY_NAME = re.compile(r"(?:^|[-_ ])tests?(?:$|[-_ ])", re.I)          # a check NAMED "tests" or "backend-tests" (a command line that only mentions a tests folder is not a test run)
_COMMAND_HEAD = re.compile(r"LOCAL COMMAND `([^`]*)` FINISHED with exit code (-?\d+)")
_CHECK_HEAD = re.compile(r"LOCAL CHECK (\S+) (PASSED|FAILED)\b")
_SANDBOX_TOOLS = ("run_sandbox_tests", "run_repo_tests")


def claimed_kinds(text: str) -> Set[str]:
    """The kinds of check the reply says passed. Pure."""
    found: Set[str] = set()
    for sentence in _SENTENCES.split(_FOLLOW_UP.sub(" ", str(text or ""))):
        sentence = sentence.strip()
        if len(sentence) < 8 or sentence.endswith("?") or _NOT_A_CLAIM.search(sentence) or not _PASSING.search(sentence):
            continue
        for kind, words in _REPLY_WORDS.items():
            if words.search(sentence):
                found.add(kind)
    return found


def _kinds_run(line: str) -> Set[str]:
    return {kind for kind, words in _RUN_WORDS.items() if words.search(line)}


def evidence(attempts: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """{kind: "passed" | "failed"} for the checks this turn ran, the last run of each kind winning. `attempts` are the loop's receipts: {"purpose": "Run <tool>", "observation": <result text>}. Pure."""
    status: Dict[str, str] = {}
    for attempt in attempts or []:
        tool = str(attempt.get("purpose") or "").removeprefix("Run ").strip()
        result = str(attempt.get("observation") or "")
        if result.startswith("ERROR"):
            continue
        if tool == "run_local_command":
            head = _COMMAND_HEAD.search(result)
            if head:
                for kind in _kinds_run(head.group(1)):
                    status[kind] = PASSED if int(head.group(2)) == 0 else FAILED
        elif tool == "run_local_check":
            head = _CHECK_HEAD.search(result)
            if head:
                for kind in _kinds_run(head.group(1)) | ({"tests"} if _TESTS_BY_NAME.search(head.group(1)) else set()):
                    status[kind] = PASSED if head.group(2) == "PASSED" else FAILED
        elif tool in _SANDBOX_TOOLS:
            status["tests"] = FAILED if re.search(r"\b(?:fail(?:ed|ures?)?|errors?)\b", result[:3000], re.I) else PASSED
    return status


def unproven(text: str, status: Dict[str, str]) -> List[str]:
    """The kinds the reply says passed that have no passing run in this turn, in a fixed order. Pure."""
    claimed = claimed_kinds(text)
    return [kind for kind in KINDS if kind in claimed and (status or {}).get(kind) != PASSED]


def note_for(kinds: Iterable[str], status: Dict[str, str]) -> str:
    """One plain line for the end of a reply, or "". Pure."""
    parts = [f"{LABEL[kind]} " + ("failed" if (status or {}).get(kind) == FAILED else ("were" if kind in _PLURAL else "was") + " not run") + " in this turn" for kind in KINDS if kind in set(kinds)]
    if not parts:
        return ""
    return "_Not checked: " + "; ".join(parts) + ". Treat what I said about " + ("it" if len(parts) == 1 else "them") + " as unverified._"


def send_back_notice(kinds: Iterable[str], can_run: bool) -> str:
    """What the model is told when its answer claims a pass that no run backs. Pure."""
    names = ", ".join(LABEL[kind] for kind in KINDS if kind in set(kinds))
    if can_run:
        return (f"CHECK YOUR CLAIM: your answer says {names} passed, but no passing run of it happened in this turn. Run it now with run_local_check or run_local_command, then answer from the result; "
                "or, if you cannot run it, say plainly that it was not run.")
    return f"CHECK YOUR CLAIM: your answer says {names} passed, but no run of it happened in this turn and none is possible here. Say plainly that it was not run."
