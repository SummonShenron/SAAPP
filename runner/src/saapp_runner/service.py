"""Starting the runner by itself, in the background, every time the person logs in, so nobody types `saapp-runner start` or keeps a window open.

ONE ENTRY PER COMPUTER (one runner serves all of the computer's projects), IN THE PERSON'S OWN ACCOUNT, NEVER AN ADMINISTRATOR'S:
- Windows: a Task Scheduler task that runs at logon as the current user (no administrator needed), started with `pythonw` so there is no console window. It is made from an XML file so that it keeps
  running on battery, is not stopped after a time limit, and is started again every few minutes whenever it is not running (a task made by plain `schtasks /Create` flags are the opposite: it would
  not start on battery). It also registers the `saapp-runner://start` link, which SAAPP's "Start runner" button opens.
- macOS: a LaunchAgent in `~/Library/LaunchAgents` (loaded for the logged-in user, started at load, kept alive).
- Linux: a `systemd --user` unit in `~/.config/systemd/user` (enabled and started now).

WHAT THE ENTRY RUNS is the same thing a person would type: this Python, `-m saapp_runner start --service`. It carries no key (the key stays in `~/.saapp-runner/credentials.json`), no folder (the projects
are in `~/.saapp-runner/projects.json`) and nothing from the server. Removing it is `saapp-runner uninstall-service`.

Everything that changes the machine goes through one function (`_run`) that tests replace, so the text of each file and each command can be checked without touching the operating system; the Windows path
is also exercised for real in the tests with a harmless stand-in.
"""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from .config import home

Runner = Callable[[List[str]], Tuple[int, str]]


def _run(command: List[str]) -> Tuple[int, str]:
    """Runs one command and returns (exit code, its output). Never raises: a command that cannot start is (127, why)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=60, errors="replace")
    except (OSError, subprocess.SubprocessError) as problem:
        return 127, f"{problem.__class__.__name__}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def service_id() -> str:
    """A short stable id for this computer's runner (a hash of its home folder's path), so the entry and the lock have a name of their own and two accounts on one computer never share them."""
    return hashlib.sha256(str(home().resolve()).encode("utf-8")).hexdigest()[:8]


def label() -> str:
    """The entry's name on every system: `saapp-runner-<id>`."""
    return f"saapp-runner-{service_id()}"


def platform_kind(platform: Optional[str] = None) -> str:
    text = platform or sys.platform
    return "windows" if text.startswith("win") else "mac" if text == "darwin" else "linux"


def log_path() -> Path:
    return home() / "runner.log"


def python_for_service(python: Optional[str] = None, windows: Optional[bool] = None) -> str:
    """The Python the entry runs. On Windows the windowless `pythonw.exe` that sits beside it when there is one, so no console window appears at login."""
    exe = python or sys.executable
    if (os.name == "nt") if windows is None else windows:
        windowless = Path(exe).with_name("pythonw.exe")
        if windowless.exists():
            return str(windowless)
    return exe


def arguments() -> List[str]:
    return ["-m", "saapp_runner", "start", "--service"]


# ---------------------------------------------------------------------------------------------------------------
# What each system needs (pure: text and command lists)
# ---------------------------------------------------------------------------------------------------------------

ENTRY_VERSION = 2                 # bumped whenever what the entry registers changes, so a newer runner brings an older entry up to date (see `refresh`)
KEEPALIVE_INTERVAL = "PT5M"       # Windows: Task Scheduler starts the runner again this often whenever it is not running (a second copy is never started while it runs)

URL_SCHEME = "saapp-runner"       # Windows: `saapp-runner://start` in a link opens the start-link handler below, which is how SAAPP's "Start runner" button can start it


def windows_task_xml(python: str, user: str) -> str:
    """The Task Scheduler definition: at this user's logon and every few minutes after (so a runner that ended for ANY reason is back within minutes), as this user without elevation, no time limit,
    allowed on battery. "Restart on failure" is not enough: Task Scheduler does not count the exit code the runner uses after an update (75) as a failure, so on 2026-10-29 the runner updated itself,
    exited, and stayed stopped for 16 hours. A copy is never started while one runs (MultipleInstancesPolicy IgnoreNew, and the runner's own lock)."""
    args = " ".join(f'"{part}"' if re.search(r"\s", part) else part for part in arguments())
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape("SAAPP runner. Runs the checks you approve in SAAPP on this computer. Remove it with: saapp-runner uninstall-service")}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>{KEEPALIVE_INTERVAL}</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(home()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def mac_plist(python: str) -> str:
    """The LaunchAgent: started when it is loaded (at login), kept alive, output to the runner's log."""
    import plistlib

    data: Dict[str, Any] = {
        "Label": f"com.saapp.{label()}", "ProgramArguments": [python, *arguments()], "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
        "WorkingDirectory": str(home()), "StandardOutPath": str(log_path()), "StandardErrorPath": str(log_path()),
    }
    return plistlib.dumps(data).decode("utf-8")


def linux_unit(python: str) -> str:
    """The user unit: starts with the user's session, restarts on failure."""
    def quote(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "\n".join([
        "[Unit]", "Description=SAAPP runner (runs the checks you approve in SAAPP)", "After=network-online.target", "",
        "[Service]", "ExecStart=" + " ".join(quote(part) for part in [python, *arguments()]), f"WorkingDirectory={quote(str(home()))}", "Restart=on-failure", "RestartSec=30", "",
        "[Install]", "WantedBy=default.target", "",
    ])


def entry_path(kind: Optional[str] = None) -> Path:
    """Where the entry's definition file lives (Windows keeps its task inside Task Scheduler, so the XML is only a temporary file in the runner's own folder)."""
    kind = kind or platform_kind()
    if kind == "mac":
        return Path.home() / "Library" / "LaunchAgents" / f"com.saapp.{label()}.plist"
    if kind == "linux":
        return Path.home() / ".config" / "systemd" / "user" / f"{label()}.service"
    return home() / f"{label()}.task.xml"


def _uid() -> str:
    return str(os.getuid()) if hasattr(os, "getuid") else "0"


def _user() -> str:
    domain, name = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return f"{domain}\\{name}" if domain and name else name


# ---------------------------------------------------------------------------------------------------------------
# Doing it
# ---------------------------------------------------------------------------------------------------------------

def _register_windows(run: Runner, name: str, path: Path, python: str, user: str) -> Tuple[bool, str]:
    """Hands Task Scheduler the entry's definition (replacing an earlier one; a copy that is running right now keeps running)."""
    path.write_text(windows_task_xml(python, user), encoding="utf-16")
    code, output = run(["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"])
    path.unlink(missing_ok=True)                           # Task Scheduler keeps its own copy; the file was only a way in
    if code != 0:
        return False, f"Task Scheduler would not accept the entry (exit code {code}). {_last_line(output)}"
    return True, ""


def protocol_commands(python: str) -> List[List[str]]:
    """The registry entries that make `saapp-runner://...` a link this computer knows: in the person's own hive (no administrator), and what it runs is fixed: `start-link`, which starts the runner and
    nothing else. The link's text is passed along only so the browser has somewhere to send it; the handler never reads it."""
    base = rf"HKCU\Software\Classes\{URL_SCHEME}"
    return [
        ["reg", "add", base, "/ve", "/d", "URL:SAAPP runner", "/f"],
        ["reg", "add", base, "/v", "URL Protocol", "/d", "", "/f"],
        ["reg", "add", base + r"\shell\open\command", "/ve", "/d", f'"{python}" -m saapp_runner start-link "%1"', "/f"],
    ]


def _own_home() -> bool:
    """True for the person's own runner (the default home), False when SAAPP_RUNNER_HOME points somewhere else: another account's runner, or a test. The `saapp-runner://` link is one per Windows user, so
    only the person's own runner registers or removes it (a test, or a second runner kept in a folder of its own, must never take the link over)."""
    return not os.environ.get("SAAPP_RUNNER_HOME")


def register_protocol(run: Runner = _run, python: Optional[str] = None, kind: Optional[str] = None) -> bool:
    """Windows only: registers the `saapp-runner://` link. True when every entry was written. Never raises."""
    if (kind or platform_kind()) != "windows" or not _own_home():
        return False
    try:
        return all(run(command)[0] == 0 for command in protocol_commands(python or python_for_service(windows=True)))
    except Exception:
        return False


def unregister_protocol(run: Runner = _run, kind: Optional[str] = None) -> None:
    if (kind or platform_kind()) == "windows" and _own_home():
        run(["reg", "delete", rf"HKCU\Software\Classes\{URL_SCHEME}", "/f"])


def _stamp() -> Path:
    return home() / "entry-version"


def _write_stamp() -> None:
    try:
        _stamp().write_text(str(ENTRY_VERSION), encoding="utf-8")
    except OSError:
        pass


def refresh(run: Runner = _run, kind: Optional[str] = None, python: Optional[str] = None, user: Optional[str] = None) -> bool:
    """Brings this computer's start-at-login entry up to date when this version of the runner needs something in it that an older one did not (ENTRY_VERSION). Called once when the runner starts by
    itself. Windows only: macOS and Linux already restart a runner that ends. Re-registering does not stop the copy that is running (checked on this machine). True when it changed the entry. Never raises."""
    if (kind or platform_kind()) != "windows":
        return False
    try:
        if _stamp().read_text(encoding="utf-8").strip() == str(ENTRY_VERSION):
            return False
    except OSError:
        pass
    try:
        if not state(run, "windows")["installed"]:           # only an entry that is already there is brought up to date; a runner started by hand never makes one
            return False
        home().mkdir(parents=True, exist_ok=True)
        py = python or python_for_service(windows=True)
        done, _ = _register_windows(run, label(), entry_path("windows"), py, user if user is not None else _user())
        if done:
            register_protocol(run, py)
            _write_stamp()
        return done
    except Exception:
        return False


def _detached(popen: Callable[..., Any], command: List[str]) -> bool:
    """Starts a program that outlives this one, with no window and no connection to it. False when it could not be started."""
    flags = 0x00000008 | 0x08000000 | 0x00000200            # DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        popen(command, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True
    except (OSError, ValueError):
        return False


def restart_soon(kind: Optional[str] = None, popen: Callable[..., Any] = subprocess.Popen, run: Runner = _run) -> bool:
    """Windows: asks Task Scheduler to start the runner again a few seconds from now, from a helper that outlives it. Used right after an update, when the runner exits so the new version can start:
    the repeating trigger would bring it back within minutes anyway, this makes it seconds. Another system's entry restarts it by itself, and with no entry there is nothing to ask. Never raises."""
    if (kind or platform_kind()) != "windows":
        return False
    try:
        if not state(run, "windows")["installed"]:
            return False
    except Exception:
        return False
    return _detached(popen, ["cmd", "/c", f"ping -n 4 127.0.0.1 >nul & schtasks /Run /TN {label()}"])


def start_now(run: Runner = _run, popen: Callable[..., Any] = subprocess.Popen, kind: Optional[str] = None) -> bool:
    """What the "Start runner" link does (Windows): starts the runner now, through its entry when there is one, else directly with no window. True when something was started. Never raises."""
    if (kind or platform_kind()) != "windows":
        return False
    try:
        if state(run, "windows")["installed"]:
            return run(["schtasks", "/Run", "/TN", label()])[0] == 0
        return _detached(popen, [python_for_service(windows=True), *arguments()])
    except Exception:
        return False


def install(run: Runner = _run, kind: Optional[str] = None, python: Optional[str] = None, user: Optional[str] = None) -> Tuple[bool, str]:
    """Registers the entry and starts it now. Returns (done, a sentence for the person). Replaces an earlier entry. Never raises."""
    kind = kind or platform_kind()
    name, path = label(), entry_path(kind)
    try:
        home().mkdir(parents=True, exist_ok=True)
        py = python or python_for_service(windows=(kind == "windows"))
        if kind == "windows":
            done, why = _register_windows(run, name, path, py, user if user is not None else _user())
            if not done:
                return False, why
            register_protocol(run, py)                         # the "Start runner" link in SAAPP; a failure here never stops the entry itself
            _write_stamp()
            run(["schtasks", "/Run", "/TN", name])
        elif kind == "mac":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(mac_plist(py), encoding="utf-8")
            run(["launchctl", "bootout", f"gui/{_uid()}/com.saapp.{name}"])         # an earlier copy, if any (an error when there is none is fine)
            code, output = run(["launchctl", "bootstrap", f"gui/{_uid()}", str(path)])
            if code != 0:
                return False, f"launchctl would not load the entry (exit code {code}). {_last_line(output)}"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(linux_unit(py), encoding="utf-8")
            code, output = run(["systemctl", "--user", "daemon-reload"])
            if code == 0:
                code, output = run(["systemctl", "--user", "enable", "--now", f"{name}.service"])
            if code != 0:
                return False, f"systemd would not start the entry (exit code {code}); this computer may not use systemd for your login. {_last_line(output)}"
    except OSError as problem:
        return False, f"Could not write the entry ({problem.__class__.__name__})."
    return True, "The runner will now start by itself whenever you log in, and it is starting now."


def uninstall(run: Runner = _run, kind: Optional[str] = None) -> Tuple[bool, str]:
    """Stops and removes the entry. (True, ...) also when there was none. Never raises."""
    kind = kind or platform_kind()
    name = label()
    try:
        if kind == "windows":
            run(["schtasks", "/End", "/TN", name])
            unregister_protocol(run, kind)
            _stamp().unlink(missing_ok=True)
            code, output = run(["schtasks", "/Delete", "/TN", name, "/F"])
            if code != 0 and "cannot find" not in output.lower() and "does not exist" not in output.lower():
                return False, f"Task Scheduler would not remove the entry (exit code {code}). {_last_line(output)}"
        elif kind == "mac":
            run(["launchctl", "bootout", f"gui/{_uid()}/com.saapp.{name}"])
            entry_path(kind).unlink(missing_ok=True)
        else:
            run(["systemctl", "--user", "disable", "--now", f"{name}.service"])
            entry_path(kind).unlink(missing_ok=True)
            run(["systemctl", "--user", "daemon-reload"])
    except OSError as problem:
        return False, f"Could not remove the entry ({problem.__class__.__name__})."
    return True, "The runner will no longer start by itself, and the background copy is stopped."


def state(run: Runner = _run, kind: Optional[str] = None) -> Dict[str, bool]:
    """{"installed": there is an entry, "running": it is running now}. Asks the operating system; False for both when it cannot tell. Never raises."""
    kind = kind or platform_kind()
    name = label()
    try:
        if kind == "windows":
            code, output = run(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"])
            return {"installed": code == 0, "running": code == 0 and bool(re.search(r"^\s*Status:\s*Running\s*$", output, re.MULTILINE | re.IGNORECASE))}
        if kind == "mac":
            code, output = run(["launchctl", "print", f"gui/{_uid()}/com.saapp.{name}"])
            return {"installed": code == 0, "running": code == 0 and "state = running" in output}
        enabled, _ = run(["systemctl", "--user", "is-enabled", f"{name}.service"])
        active, _ = run(["systemctl", "--user", "is-active", f"{name}.service"])
        return {"installed": enabled == 0, "running": active == 0}
    except Exception:
        return {"installed": False, "running": False}


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""
