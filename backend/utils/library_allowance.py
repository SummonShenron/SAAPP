"""Extra tool steps for reading a long library, so a review of many files is not cut off with weeks unread (docs/tool-step-budget-plan.md, step 2).

A review that has to read every file in a person's library ("what did I do in August") lists the files and then reads them, five at a time (the loops batch up to five
actions in a step). The normal budget fits ten files and not a file more: with eleven the loop hits its last step with some unread, and the answer used to cover
the rest as if it had been seen. This grants the steps the listing says are needed, and says out loud what was not read if the budget still ran out.

THE RULES (all in code, no model call):

- ONCE per turn, only after a listing came back (`list_library_files`), never from anything the model asks for.
- EXACTLY WHAT IS NEEDED: reads needed = ceil(files / READS_PER_STEP); the grant is what the budget left does not already cover (so seven files, which fit, get nothing),
  at most MAX_EXTRA_STEPS. The plan's first sketch granted ceil(files / 5) outright; granting only the shortfall keeps a confused turn from being handed steps it does not need.
- NOT IN LOW-POWER MODE: `enabled` is False there (the budget guard already caps the steps, and the extra would undo it).
- IF THE BUDGET STILL RUNS OUT, `unread_note` lists the files that were listed and never read, as plain data for the voice stage, so the reply names what it did and did not cover.

Nothing here holds a person's text beyond file names in memory for the length of one turn, and nothing is logged.
"""
import math
import os
import re
from typing import Any, Dict, Iterable, List

READS_PER_STEP = 5
MAX_EXTRA_STEPS = max(int(os.getenv("LIBRARY_EXTRA_STEPS_MAX", "4")), 0)
MAX_NAMES_SHOWN = 10
LIST_TOOL = "list_library_files"
READ_TOOL = "read_library_file"

_FILE_LINE_RE = re.compile(r"^- (.+?) \(library [^)]*?, \d+ passages?\)\s*$")


def listed_files(observation: Any) -> List[str]:
    """The file names in a `list_library_files` result, in order; [] for an error or anything else."""
    text = str(observation or "")
    if text.startswith("ERROR"):
        return []
    return [match.group(1) for match in (_FILE_LINE_RE.match(line.strip()) for line in text.splitlines()) if match]


def reads_needed(file_count: int) -> int:
    return math.ceil(max(file_count, 0) / READS_PER_STEP)


class LibraryAllowance:
    """One turn's allowance. `enabled` False (low-power mode) still lets it remember the listing, so the unread note works; it just never grants."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.listed: List[str] = []
        self.granted = 0
        self._decided = False

    def after_listing(self, tool: str, observation: Any, reads_left: int) -> int:
        """Called with each finished tool call. For a successful listing, returns the extra steps to add to the budget (0 for anything else, a second listing, or a budget
        that already covers it). `reads_left` is how many tool steps remain AFTER the current one, not counting the step kept for the answer."""
        if tool != LIST_TOOL:
            return 0
        files = listed_files(observation)
        if not files:
            return 0
        self.listed = files
        if self._decided or not self.enabled:
            return 0
        self._decided = True
        grant = min(max(reads_needed(len(files)) - max(reads_left, 0), 0), MAX_EXTRA_STEPS)
        self.granted = grant
        return grant

    def note(self, grant: int) -> str:
        """What the model is told when steps were added, so it reads in batches instead of one file at a time."""
        return (
            f"[Steps: the library has {len(self.listed)} files, which takes about {reads_needed(len(self.listed))} read steps at {READS_PER_STEP} files per step. "
            f"{grant} more tool step{'s' if grant != 1 else ''} {'were' if grant != 1 else 'was'} added so they fit. Ask for up to {READS_PER_STEP} read_library_file calls together in each step.]"
        )

    def unread(self, attempts: Iterable[Dict[str, Any]]) -> List[str]:
        """The listed files no successful read_library_file call in `attempts` named."""
        read_calls = [
            str(a.get("action_desc", "")) for a in attempts
            if str(a.get("action_desc", "")).startswith(f"{READ_TOOL}(") and not str(a.get("observation", "")).startswith("ERROR")
        ]
        return [name for name in self.listed if not any(name in call for call in read_calls)]

    def unread_note(self, attempts: Iterable[Dict[str, Any]]) -> str:
        """Plain data for the voice stage when the turn ended with listed files unread; "" when everything was read or nothing was listed."""
        missing = self.unread(attempts)
        if not self.listed or not missing:
            return ""
        shown = ", ".join(missing[:MAX_NAMES_SHOWN]) + (f" (and {len(missing) - MAX_NAMES_SHOWN} more)" if len(missing) > MAX_NAMES_SHOWN else "")
        return (
            f"NOTE (authoritative): the library has {len(self.listed)} files and this turn ran out of steps with {len(missing)} of them NOT read: {shown}. "
            "Say which files you did read and which you did not, do not describe the unread ones as covered, and offer to read them if the person asks again."
        )
