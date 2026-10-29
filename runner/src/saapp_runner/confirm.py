"""The person's own rules for command lines, kept on THIS computer, and the question the runner asks them here before it changes anything.

SAAPP shows a stronger card for a command that changes things and the person presses Run there. That is not enough on its own: the card is drawn from what the server says, and a server that was
wrong (or taken over) could show one thing and ask for another. So the runner asks AGAIN, in a window of its own that no web page can click, showing the exact command it is about to run, and it runs
nothing that changes the project unless the person answers yes. If this computer cannot show a window, the command is refused.

`~/.saapp-runner/commands.toml` holds the person's own rules. It is read here, never sent to the server and never changed by it:

    confirm_changes = true                 # ask here before a command that changes things (default true; false skips the question)
    fake_keys = true                       # in the MCP server, run with made-up AI keys so a test cannot spend real money (default true); false passes no key at all (the environment is clean either way)
    allow = ["terraform-lint"]             # extra programs a line may use. They count as changes. The built-in never-list still wins over this.
    refuse = ["git push", "npm install"]   # command beginnings that are always refused, whatever SAAPP says: a program name, or a program and its first words
"""
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

from . import commands, picker
from .config import home

TIMEOUT_SECONDS = 5 * 60


def settings_path() -> Path:
    return home() / "commands.toml"


class Rules:
    """What the person set in commands.toml. `problem` says what was wrong with the file (the rules then default to the safest: ask, and allow nothing extra)."""

    def __init__(self, confirm_changes: bool = True, allow: FrozenSet[str] = frozenset(), refuse: Tuple[Tuple[str, ...], ...] = (), problem: str = "", fake_keys: bool = True) -> None:
        self.confirm_changes, self.allow, self.refuse, self.problem, self.fake_keys = confirm_changes, allow, refuse, problem, fake_keys


def load_rules() -> Rules:
    path = settings_path()
    if not path.exists():
        return Rules()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as problem:
        return Rules(problem=f"could not read {path.name}: {problem}")
    allow = data.get("allow")
    refuse = data.get("refuse")
    bad = [name for name, value in (("allow", allow), ("refuse", refuse)) if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value))]
    if bad or any(data.get(name) is not None and not isinstance(data.get(name), bool) for name in ("confirm_changes", "fake_keys")):
        return Rules(problem=f"{path.name}: allow and refuse are lists of words, and confirm_changes and fake_keys are true or false")
    return Rules(data.get("confirm_changes") is not False, frozenset(a.strip().lower() for a in allow or [] if a.strip()),
                 tuple(tuple(w.lower() for w in r.split()) for r in refuse or [] if r.strip()), fake_keys=data.get("fake_keys") is not False)


def refused_by_person(plan: "commands.Plan", rules: Rules) -> str:
    """Why one of the person's own refuse rules stops this line, or ""."""
    for command in plan.commands():
        words = tuple(w.lower() for w in command.argv)
        if words and words[0] not in commands.BUILTINS:
            words = (commands.program_name(command.argv[0]),) + words[1:]
        for rule in rules.refuse:
            if rule and words[:len(rule)] == rule:
                return f"your own rules in {settings_path().name} refuse \"{' '.join(rule)}\""
    return ""


# ---- the question ---------------------------------------------------------------------------------------------------------------------------------

def run_dialog(line: str, notes: List[str], ask: Optional[Any] = None) -> Dict[str, Any]:
    """Shows the question and returns {"result": "chosen"} for yes, {"result": "declined"} for no, {"result": "no_picker"} when no window can be shown. Runs in the child process; `ask` replaces tkinter in tests."""
    try:
        if ask is None:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            ask = _TkAsk(root, messagebox)
    except Exception:
        return {"result": "no_picker"}
    try:
        why = ("\n\nWhy it needs your yes:\n- " + "\n- ".join(notes)) if notes else ""
        yes = ask.confirm("SAAPP runner: this command changes things", f"SAAPP asked to run this in your project:\n\n{line}{why}\n\nYou already pressed Run in SAAPP. Run it now?")
        return {"result": "chosen" if yes else "declined"}
    except Exception:
        return {"result": "no_picker"}
    finally:
        try:
            ask.close()
        except Exception:
            pass


class _TkAsk:
    def __init__(self, root: Any, messagebox: Any) -> None:
        self.root, self.messagebox = root, messagebox

    def confirm(self, title: str, text: str) -> bool:
        return bool(self.messagebox.askyesno(title, text, parent=self.root, default="no"))          # No is the button that Enter presses

    def close(self) -> None:
        self.root.destroy()


def ask_blocking(asker: Callable[[str, List[str]], Any], shown: str, notes: List[str], cancel: Any, stop: Any = None, poll: float = 0.2) -> str:
    """Asks the person in the window on this computer and waits: "yes", "no", "cancelled" (the caller's `cancel` or `stop` event was set meanwhile, and the window is closed) or "none" (no window could be shown)."""
    import time

    try:
        handle = asker(shown, notes)
    except Exception:
        return "none"
    while True:
        answer = handle.poll()
        if answer is not None:
            return {"chosen": "yes", "declined": "no"}.get(answer.get("result"), "none")
        if cancel.is_set() or (stop is not None and stop.is_set()):
            handle.cancel()
            return "cancelled"
        time.sleep(poll)


def new_asker(command: Optional[Callable[[str, List[str]], List[str]]] = None) -> Callable[[str, List[str]], "picker.Dialog"]:
    """The function the runner calls to ask: it starts the question in its own process and returns a handle with poll() and cancel()."""
    def start(line: str, notes: List[str]) -> "picker.Dialog":
        import time

        from . import service

        argv = command(line, notes) if command else [service.python_for_service(), "-m", "saapp_runner", "confirm-command", "--line", line, "--notes", json.dumps(notes)]
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True)
        return picker.Dialog(process, time.monotonic() - (picker.TIMEOUT_SECONDS - TIMEOUT_SECONDS))

    return start
