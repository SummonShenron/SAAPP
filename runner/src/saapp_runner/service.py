"""Starting the runner by itself, in the background, every time the person logs in, so nobody types `saapp-runner start` or keeps a window open.

ONE ENTRY PER COMPUTER (one runner serves all of the computer's projects), IN THE PERSON'S OWN ACCOUNT, NEVER AN ADMINISTRATOR'S:
- Windows: a Task Scheduler task that runs at logon as the current user (no administrator needed), started with `pythonw` so there is no console window. It is made from an XML file so that it keeps
  running on battery, is not stopped after a time limit, and is restarted if it ends (a task made by plain `schtasks /Create` flags are the opposite: it would not start on battery).
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

def windows_task_xml(python: str, user: str) -> str:
    """The Task Scheduler definition: at this user's logon, as this user without elevation, no time limit, allowed on battery, restarted when it ends."""
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

def install(run: Runner = _run, kind: Optional[str] = None, python: Optional[str] = None, user: Optional[str] = None) -> Tuple[bool, str]:
    """Registers the entry and starts it now. Returns (done, a sentence for the person). Replaces an earlier entry. Never raises."""
    kind = kind or platform_kind()
    name, path = label(), entry_path(kind)
    try:
        home().mkdir(parents=True, exist_ok=True)
        py = python or python_for_service(windows=(kind == "windows"))
        if kind == "windows":
            path.write_text(windows_task_xml(py, user if user is not None else _user()), encoding="utf-16")
            code, output = run(["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"])
            path.unlink(missing_ok=True)                       # Task Scheduler keeps its own copy; the file was only a way in
            if code != 0:
                return False, f"Task Scheduler would not accept the entry (exit code {code}). {_last_line(output)}"
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
