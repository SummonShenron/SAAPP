"""The server's copy of the runner's rules for which pages may be looked at.

The rules (loopback only, no credentials, a real port, a bounded path) live in ONE file: runner/src/saapp_runner/localpage.py. The runner applies them on the person's computer; this module loads the very
same file for the server, so a card is never shown for an address the runner would refuse and the two cannot drift apart. The file has no imports outside the standard library. The runner has the last
word: it checks the address again, and for a look inside a preview session it also requires the port of the server it is keeping.
"""
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("SASS Logger")

_FILE = Path(__file__).resolve().parents[2] / "runner" / "src" / "saapp_runner" / "localpage.py"
_NAME = "saapp_runner_localpage_shared"

localpage: Any = None
AVAILABLE = False


def _load() -> None:
    global localpage, AVAILABLE
    try:
        spec = importlib.util.spec_from_file_location(_NAME, _FILE)
        module = importlib.util.module_from_spec(spec)                      # type: ignore[arg-type]
        sys.modules[_NAME] = module
        spec.loader.exec_module(module)                                      # type: ignore[union-attr]
        localpage, AVAILABLE = module, True
    except Exception:
        sys.modules.pop(_NAME, None)
        localpage, AVAILABLE = None, False
        logger.warning("[runner_localpage] the shared page rules could not be loaded, so Sonic cannot offer to look at local pages on this server.")


_load()
