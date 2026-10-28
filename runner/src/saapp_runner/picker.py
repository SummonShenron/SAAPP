"""The dialog on the person's own computer where they choose a project folder when SAAPP asks for one.

SAAPP can only ASK, by name ("add a project called my-app"). The runner then shows ITS OWN dialog here, on this computer, and the person chooses the folder (or says no). That choice is the consent: it
happens on the machine, outside the web page, and it is a click, not a command. The dialog runs in a separate short-lived process (`python -m saapp_runner pick-folder`), so the runner keeps polling and
answering while it is open, and a window toolkit that is missing or has no screen cannot take the runner down: the answer is then `no_picker` and SAAPP tells the person to add the folder by typing one
command instead.

The dialog is plain tkinter (it ships with Python on Windows and macOS and with most Linux Pythons). It asks twice, so a stray click cannot add a folder: first whether to choose one at all, saying what
SAAPP asked for and that checks only ever run after the person presses Run in SAAPP; then the folder; and if the chosen folder's name differs from the one asked for, once more.
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TIMEOUT_SECONDS = 15 * 60          # the same window the server gives a request before it calls it expired


def run_dialog(name: str, ask: Optional[Any] = None) -> Dict[str, Any]:
    """Shows the dialog and returns {"result": "chosen", "path": ...}, {"result": "declined"} or {"result": "no_picker"}. Runs in the child process; `ask` replaces tkinter in tests."""
    try:
        if ask is None:
            import tkinter
            from tkinter import filedialog, messagebox

            root = tkinter.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            ask = _TkAsk(root, messagebox, filedialog)
    except Exception:
        return {"result": "no_picker"}
    try:
        if not ask.confirm("SAAPP runner", f'SAAPP asked to run checks in a project called "{name}" on this computer.\n\nChecks only ever run after you press Run in SAAPP, and only in the folder you choose.\n\nChoose the folder now?'):
            return {"result": "declined"}
        chosen = ask.folder(f'Choose the folder of "{name}"')
        if not chosen:
            return {"result": "declined"}
        if Path(chosen).name.lower() != name.lower() and not ask.confirm("SAAPP runner", f'You chose a folder called "{Path(chosen).name}", but SAAPP asked for "{name}".\n\nUse it anyway?'):
            return {"result": "declined"}
        return {"result": "chosen", "path": str(chosen)}
    except Exception:
        return {"result": "no_picker"}
    finally:
        try:
            ask.close()
        except Exception:
            pass


class _TkAsk:
    def __init__(self, root: Any, messagebox: Any, filedialog: Any) -> None:
        self.root, self.messagebox, self.filedialog = root, messagebox, filedialog

    def confirm(self, title: str, text: str) -> bool:
        return bool(self.messagebox.askyesno(title, text, parent=self.root))

    def folder(self, title: str) -> str:
        return str(self.filedialog.askdirectory(title=title, mustexist=True, parent=self.root) or "")

    def close(self) -> None:
        self.root.destroy()


class Dialog:
    """A dialog running in its own process. `poll()` is None while it is open, then the answer ({"result": ...}); `cancel()` closes it."""

    def __init__(self, process: "subprocess.Popen[str]", started: float) -> None:
        self.process, self.started, self._answer = process, started, None

    def poll(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        import time

        if self._answer is not None:
            return self._answer
        if self.process.poll() is None:
            if (now if now is not None else time.monotonic()) - self.started > TIMEOUT_SECONDS:
                self.cancel()
                self._answer = {"result": "declined"}
                return self._answer
            return None
        try:
            lines = [line for line in (self.process.stdout.read() if self.process.stdout else "").splitlines() if line.strip()]
            answer = json.loads(lines[-1]) if lines else {}
        except (OSError, ValueError):
            answer = {}
        self._answer = answer if answer.get("result") in ("chosen", "declined", "no_picker") else {"result": "error"}
        return self._answer

    def cancel(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass


def start_dialog(name: str, command: Optional[List[str]] = None) -> Dialog:
    """Starts the dialog in a new process and returns at once. `command` replaces the real one in tests."""
    import time

    from . import service

    argv = command or [service.python_for_service(), "-m", "saapp_runner", "pick-folder", "--name", name]
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
    return Dialog(process, time.monotonic())


def new_picker(command: Optional[Callable[[str], List[str]]] = None) -> Callable[[str], Dialog]:
    """The function the runner calls to ask for a project folder."""
    return lambda name: start_dialog(name, command(name) if command else None)
