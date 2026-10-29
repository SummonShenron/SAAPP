"""The server's copy of the runner's command rules.

The rules that decide what a command line is, and which lines are refused, live in ONE file: runner/src/saapp_runner/commands.py. The runner uses it on the person's computer; this module loads the very same
file for the server, so a card is never shown for a line the runner would refuse, and the two cannot drift apart. The file has no imports outside the standard library.

The server does not know the person's folder, so it classifies against a stand-in project folder: what it learns is whether the line is allowed at all and how strong it is (read, run or change). The
runner then classifies again with the real folder (links, files that exist) and has the last word.
"""
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("SASS Logger")

_FILE = Path(__file__).resolve().parents[2] / "runner" / "src" / "saapp_runner" / "commands.py"
_NAME = "saapp_runner_commands_shared"

commands: Any = None
AVAILABLE = False


def _load() -> None:
    global commands, AVAILABLE
    try:
        spec = importlib.util.spec_from_file_location(_NAME, _FILE)
        module = importlib.util.module_from_spec(spec)                      # type: ignore[arg-type]
        sys.modules[_NAME] = module                                          # dataclasses look their module up here
        spec.loader.exec_module(module)                                      # type: ignore[union-attr]
        commands, AVAILABLE = module, True
    except Exception:
        sys.modules.pop(_NAME, None)
        commands, AVAILABLE = None, False
        logger.warning("[runner_commands] the shared command rules could not be loaded, so Sonic cannot offer commands on this server.")


_load()

STAND_IN_ROOT = Path("/project")


def classify(line: Any) -> Any:
    """The parsed, classified Plan for a line (raises commands.CommandError, saying why, when it is refused)."""
    if not isinstance(line, str):
        raise commands.CommandError("There is no command to run.")
    return commands.classify(line, STAND_IN_ROOT)
