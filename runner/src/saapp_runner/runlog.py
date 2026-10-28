"""The runner's own log, for when it runs in the background with no window (started at login): `~/.saapp-runner/runner.log`.

IT HOLDS EVENTS, NOT YOUR PROJECT. A log is the first thing a person is asked to paste when something goes wrong, so every line goes through `clean` first: every project's path and the home folder become
`<project>` and `~`, and the lines that spell out a check's command (`can run: tests = ...`) are left out. What stays is what happened: started, connected, which check ran, its exit code and how long it took,
the status it reported. Nothing from a command's output is ever written here (the runner sends that to SAAPP, not to a file). The file is cut down when it passes 200 KB, keeping the newest half.
"""
import time
from pathlib import Path
from typing import Callable, Iterable

from .config import home

MAX_BYTES = 200_000


def log_path() -> Path:
    return home() / "runner.log"


def clean(line: str, roots: Iterable[Path]) -> str:
    """The line as it may be written, or "" when it should not be written at all. Every project's path becomes `<project>` and the home folder `~`. Pure."""
    text = str(line)
    if text.lstrip().startswith(("can run:", "note:")):
        return ""
    for real, shown in [*((str(Path(root).resolve()), "<project>") for root in roots), (str(home().parent), "~"), (str(Path.home()), "~")]:
        if real:
            text = text.replace(real, shown)
    return " ".join(text.split())


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size > MAX_BYTES:
            data = path.read_bytes()[-MAX_BYTES // 2:]
            path.write_bytes(data[data.find(b"\n") + 1:] if b"\n" in data else data)
    except OSError:
        pass


def file_say(roots: Callable[[], Iterable[Path]]) -> Callable[[str], None]:
    """A `say` that appends cleaned, time-stamped lines to the log instead of printing (printing has nowhere to go without a window). `roots()` gives the projects' folders at the moment of each
    line, so a project added later is cleaned too. Never raises."""
    path = log_path()

    def say(line: str) -> None:
        try:
            current = list(roots())
        except Exception:
            current = []
        text = clean(line, current)
        if not text:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _trim(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    return say


def tail(lines: int = 8) -> str:
    """The last few lines of the log, for `saapp-runner status`. "" when there is none."""
    try:
        return "\n".join(log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
