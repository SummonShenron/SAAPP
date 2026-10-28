"""What the runner tells SAAPP about its own state, as a short code from a FIXED list, never free text from this machine.

SAAPP's Settings turns each code into a sentence the person can act on ("this runner found no tests or type check in its folder"). A code, not a message, so nothing written on this computer (a path, a
command's output, a file name) travels to the server through the status, and the server can ignore any code it does not know.

The codes are ordered by how much they matter: a runner reports the first one that applies.
"""
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .execute import clean_env

FOLDER_MISSING = "folder_missing"        # the project folder this runner was set up for is gone (moved, renamed or deleted)
CONFIG_PROBLEM = "config_problem"        # the config file in the home folder could not be read, so no custom checks were loaded
NO_CHECKS_FOUND = "no_checks_found"      # nothing to run: no tests or type check was found in the folder and none is written in the config
COMMAND_MISSING = "command_missing"      # at least one check's program (python, npm, npx...) cannot be found on this computer, so that check would fail
UPDATE_FAILED = "update_failed"          # the last attempt to update this runner failed (it keeps running the version it has)
OK = "ok"

CODES = (FOLDER_MISSING, CONFIG_PROBLEM, NO_CHECKS_FOUND, COMMAND_MISSING, UPDATE_FAILED, OK)


def program_ready(words: List[str]) -> bool:
    """Whether a check's program can be found the way the runner finds it when it runs it (the same search path, so `npm.cmd` counts on Windows). A full path counts when the file is there."""
    if not words or not words[0].strip():
        return False
    program = words[0]
    if shutil.which(program, path=clean_env().get("PATH")):
        return True
    try:
        return Path(program).is_file()
    except OSError:
        return False


def assess(root: Path, checks: Dict[str, List[str]], config_ok: bool = True, update_failed: bool = False) -> Tuple[str, Dict[str, bool]]:
    """(the status code, {check name: its program can be found}) for this folder and these checks. Pure apart from looking at the file system."""
    ready = {name: program_ready(words) for name, words in checks.items()}
    if not Path(root).is_dir():
        return FOLDER_MISSING, ready
    if not config_ok:
        return CONFIG_PROBLEM, ready
    if not checks:
        return NO_CHECKS_FOUND, ready
    if not all(ready.values()):
        return COMMAND_MISSING, ready
    if update_failed:
        return UPDATE_FAILED, ready
    return OK, ready


SENTENCES: Dict[str, str] = {
    FOLDER_MISSING: "The project folder this runner was set up for could not be found. It may have been moved, renamed or deleted.",
    CONFIG_PROBLEM: "The runner could not read its config file, so only the checks it finds in the folder are available.",
    NO_CHECKS_FOUND: "No tests or type check was found in the project folder, so there is nothing to run yet.",
    COMMAND_MISSING: "A check's program (for example Python or npm) could not be found on this computer, so that check would fail.",
    UPDATE_FAILED: "The last update did not work, so this runner is still on its old version. It will not try again for a few hours; run the setup file again to update it now.",
    OK: "Ready.",
}


def describe(code: Optional[str]) -> str:
    """The sentence for a code, for the runner's own messages. Pure."""
    return SENTENCES.get(code or "", "")
