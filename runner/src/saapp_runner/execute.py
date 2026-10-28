"""Runs one approved check on this machine, with limits.

- NO SHELL. The command is a list of words and is run directly (`subprocess` with a list, never a string), so nothing in it is expanded, piped or chained.
- A CLEAN ENVIRONMENT. Only what a program needs to start (the search path, the temp and home folders, and a few settings) is passed on; the secrets in your shell (API keys, tokens) are not.
- A TIME LIMIT. When it passes, the WHOLE tree of processes the command started is killed (a test runner starts children, and one left holding the output open would hang this), then the run is reported as timed out.
- A CAPPED OUTPUT. Everything is read so the command never blocks on a full pipe, but only the last `max_chars` are kept, and the result says when the start was cut.
- IT IS NOT A SANDBOX. The command runs with your own privileges, in your own project, because you chose it and pressed Run. These limits stop a runaway or a leak, not a hostile command.
"""
import codecs
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from .config import PLACEHOLDER
from .files import as_argument

_ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
             "PROGRAMFILES", "LANG", "LC_ALL", "VIRTUAL_ENV", "NODE_PATH")


def clean_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment a check runs in: the few variables a program needs, and unbuffered, colourless output."""
    env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "CI": "1"})
    env.update(extra or {})
    return env


def build_argv(words: List[str], files: List[str]) -> List[str]:
    """The command with `{files}` replaced by the files (each as ./path), or by nothing. Files given to a command that has no `{files}` are refused."""
    if PLACEHOLDER not in words:
        if files:
            raise ValueError("this check does not take files.")
        return list(words)
    out: List[str] = []
    for word in words:
        if word == PLACEHOLDER:
            out.extend(as_argument(f) for f in files)
        else:
            out.append(word)
    return out


def safe_for_files(words: List[str], windows: Optional[bool] = None) -> bool:
    """Whether files may be passed to this command. On Windows a `.cmd` or `.bat` program (npm and npx are `npm.cmd` and `npx.cmd`) is run through cmd.exe, which can act on special characters in its
    arguments, so file names are never passed to one: such a check must be defined without `{files}`. Elsewhere there is no such risk."""
    if (os.name == "nt") if windows is None else windows:
        program = (shutil.which(words[0], path=clean_env().get("PATH")) or words[0]).lower()
        return not program.endswith((".cmd", ".bat"))
    return True


def kill_tree(proc: "subprocess.Popen[bytes]") -> None:
    """Ends the process and everything it started. Windows needs taskkill for the tree; elsewhere the command was started in its own process group."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            group = os.getpgid(proc.pid)
            os.killpg(group, signal.SIGTERM)
            for _ in range(10):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                os.killpg(group, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class _Tail:
    """The end of a stream, kept up to `limit` characters, with whether anything before it was dropped."""

    def __init__(self, limit: int) -> None:
        self.limit, self.text, self.cut = limit, "", False

    def add(self, piece: str) -> None:
        self.text += piece
        if len(self.text) > self.limit * 2:
            self.text, self.cut = self.text[-self.limit:], True

    def done(self) -> str:
        if len(self.text) > self.limit:
            self.text, self.cut = self.text[-self.limit:], True
        return self.text


def run(argv: List[str], cwd: Any, timeout: float, max_chars: int = 6000, cancel: Optional[threading.Event] = None, env: Optional[Dict[str, str]] = None,
        stdin_data: Optional[bytes] = None, separate_stderr: bool = False) -> Dict[str, Any]:
    """Runs `argv` in `cwd` and returns {"exit_code", "output_tail", "truncated", "duration", "timed_out", "cancelled", "error"}. `exit_code` is None when it could not start, was killed for time, or was cancelled.
    `stdin_data` is fed to the program's input (a command line's pipe); with `separate_stderr` the program's error output is kept apart in "stderr_tail" instead of being mixed into the output."""
    started = time.monotonic()
    result: Dict[str, Any] = {"exit_code": None, "output_tail": "", "stderr_tail": "", "truncated": False, "duration": 0.0, "timed_out": False, "cancelled": False, "error": ""}
    program = shutil.which(argv[0], path=(env or clean_env()).get("PATH")) or argv[0]       # finds npm.cmd and the like the way a shell would
    kwargs: Dict[str, Any] = {"cwd": str(cwd), "env": env or clean_env(), "stdin": subprocess.DEVNULL if stdin_data is None else subprocess.PIPE, "stdout": subprocess.PIPE,
                              "stderr": subprocess.PIPE if separate_stderr else subprocess.STDOUT}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([program, *argv[1:]], **kwargs)
    except FileNotFoundError:
        result["error"] = f"command not found: {argv[0]}. Use its full path in your config, or start the runner from the environment that has it."
        return result
    except OSError as problem:
        result["error"] = f"could not start it: {problem.__class__.__name__}"
        return result
    tail, error_tail = _Tail(max_chars), _Tail(max_chars)

    def read(stream: Any, into: _Tail) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = stream.read1(4096)               # whatever has arrived, so a quiet command does not wait on a full buffer
            if not chunk:
                break
            into.add(decoder.decode(chunk))
        into.add(decoder.decode(b"", final=True))

    def feed() -> None:
        try:
            assert proc.stdin is not None and stdin_data is not None
            proc.stdin.write(stdin_data)
        except (OSError, ValueError):
            pass                                     # the program stopped reading (head does): that is fine
        finally:
            try:
                proc.stdin.close()                   # type: ignore[union-attr]
            except (OSError, ValueError, AttributeError):
                pass

    assert proc.stdout is not None
    readers = [threading.Thread(target=read, args=(proc.stdout, tail), daemon=True)]
    if separate_stderr and proc.stderr is not None:
        readers.append(threading.Thread(target=read, args=(proc.stderr, error_tail), daemon=True))
    if stdin_data is not None:
        readers.append(threading.Thread(target=feed, daemon=True))
    for reader in readers:
        reader.start()
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            result["cancelled"] = True
            kill_tree(proc)
            break
        if time.monotonic() - started > timeout:
            result["timed_out"] = True
            kill_tree(proc)
            break
        time.sleep(0.1)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
    for reader in readers:
        reader.join(timeout=5)
    result["output_tail"], result["truncated"] = tail.done(), tail.cut
    result["stderr_tail"] = error_tail.done()
    result["duration"] = round(time.monotonic() - started, 2)
    if not (result["timed_out"] or result["cancelled"]):
        result["exit_code"] = proc.returncode
    return result
