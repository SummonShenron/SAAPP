"""Which command-line programs does Sonic reach for on the person's machine (run_local_command, propose_git_commit), counted by family and by class (2026-11-20).

WHY. Jack's report: Sonic "doesn't use its grep tool as often as it probably should, or its CLI tools in general outside of tests". The existing counters say how many times each TOOL was called (llm_turns.note_tool_call:
run_local_command as one number) but not what the command lines were, so there was no way to tell whether the CLI is ignored, or used for tests only, or held back by the click every non-read command needs.
This counts, per command line, the program families in it (git, a text search, a look at files, the project's tests and build tools, anything else) and its class (read, run, change). Counts only: nothing of the line
is kept, logged or sent anywhere. Pure, no model, never raises.
"""
import re
from typing import Any, Dict, Set

FAMILIES = {
    "git": {"git"},
    "search": {"grep", "rg"},                                  # a text search through the files
    "look": {"ls", "cat", "head", "tail", "wc"},               # looking at files and their size
    "tool": {"pytest", "python", "python3", "pip", "npm", "npx", "tsc", "ruff", "node", "pnpm", "yarn", "mypy", "eslint", "vitest", "jest"},   # the project's own tests and build tools
}
_SPLIT = re.compile(r"&&|\|\||;|\||>>|>")
KEYS = tuple(f"cli_prog_{name}" for name in (*FAMILIES, "other")) + ("cli_git_grep",) + tuple(f"cli_class_{name}" for name in ("read", "run", "change"))


def families_of(line: Any) -> Set[str]:
    """The program families a command line uses: {"git", "search", "look", "tool", "other"} (a subset). `git grep` is both git and search. Pure."""
    found: Set[str] = set()
    try:
        for segment in _SPLIT.split(str(line or "")):
            words = segment.split()
            if not words:
                continue
            while words and words[0] == "cd":          # "cd sub && pytest": the cd is not the program
                words = words[2:]
            if not words:
                continue
            program = words[0].lower().rsplit("/", 1)[-1]
            matched = False
            for name, programs in FAMILIES.items():
                if program in programs:
                    found.add(name)
                    matched = True
            if program == "git" and len(words) > 1 and words[1].lower() == "grep":
                found.add("search")
            if not matched and program != "echo":
                found.add("other")
    except Exception:
        return set()
    return found


def counts_for(line: Any, klass: Any = "") -> Dict[str, int]:
    """The counter increments for one command line: one per family it uses, one for its class, and one for `git grep` when that is what it ran. {} when there is nothing to count. Pure."""
    try:
        counts: Dict[str, int] = {f"cli_prog_{name}": 1 for name in families_of(line)}
        text = str(line or "").lower()
        if re.search(r"\bgit\s+grep\b", text):
            counts["cli_git_grep"] = 1
        if klass in ("read", "run", "change"):
            counts[f"cli_class_{klass}"] = 1
        return counts
    except Exception:
        return {}
