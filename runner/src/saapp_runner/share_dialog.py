"""The window on the person's own computer that asks whether to send a project's files to SAAPP.

SAAPP can only ASK ("let Sonic read this project's files"). The runner shows this window, here, and nothing is sent unless the person says yes in it: that answer is the consent, made on the machine and
outside the web page, the same as choosing a folder (picker.py). It runs in its own short-lived process (`python -m saapp_runner share-dialog`) so the runner keeps answering while it is open, and a window
toolkit that is missing or has no screen is `no_picker`, never a crash and never an upload.
"""
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import picker


def question(name: str, root: Path, summary: str) -> str:
    """What the window says. It names the folder, how much would be sent and what is never sent, and says how to stop. Pure."""
    return (
        f'SAAPP asked to let Sonic read the files of your project "{name}" ({root}).\n\n'
        f"If you say yes, this runner sends {summary} from that folder to the SAAPP server, so Sonic can read them in chat.\n\n"
        "Never sent: secrets (.env files, keys, credentials), node_modules, build folders, lock files, git history and files over 500 KB.\n\n"
        "You can take them back any time with Stop sharing in SAAPP, and they are removed from the server after a while without use.\n\n"
        "Send these files now?"
    )


def run_dialog(name: str, root: Path, summary: str, ask: Optional[Any] = None) -> Dict[str, Any]:
    """Shows the window and returns {"result": "chosen"} (yes), {"result": "declined"} or {"result": "no_picker"}. Runs in the child process; `ask` replaces tkinter in tests."""
    try:
        if ask is None:
            import tkinter
            from tkinter import filedialog, messagebox

            window = tkinter.Tk()
            window.withdraw()
            try:
                window.attributes("-topmost", True)
            except Exception:
                pass
            ask = picker._TkAsk(window, messagebox, filedialog)
    except Exception:
        return {"result": "no_picker"}
    try:
        return {"result": "chosen"} if ask.confirm("SAAPP runner", question(name, Path(root), summary)) else {"result": "declined"}
    except Exception:
        return {"result": "no_picker"}
    finally:
        try:
            ask.close()
        except Exception:
            pass


def start_dialog(name: str, root: Path, summary: str, command: Optional[List[str]] = None) -> picker.Dialog:
    """Starts the window in a new process and returns at once. `command` replaces the real one in tests."""
    import time

    from . import service

    argv = command or [service.python_for_service(), "-m", "saapp_runner", "share-dialog", "--name", name, "--root", str(root), "--summary", summary]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
    return picker.Dialog(process, time.monotonic())


def new_starter(command: Optional[Callable[[str, Path, str], List[str]]] = None) -> Callable[[str, Path, str], picker.Dialog]:
    """The function the runner calls to ask whether to send a project's files."""
    return lambda name, root, summary: start_dialog(name, root, summary, command(name, root, summary) if command else None)
