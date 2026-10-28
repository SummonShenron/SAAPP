"""The starter scripts a person downloads from Settings to set up the local runner (docs/local-runner-plan.md, "Installing it").

Each script is short, readable and shown before it is downloaded. It says what it is about to do and asks first; it checks for a suitable Python and installs nothing system-wide; it makes a private
environment in the person's own folder, installs the PINNED release of `saapp-runner` into it, pairs THIS COMPUTER with the one-time code, and starts the runner in the background. The script asks nothing but
the first yes: it does not ask for a folder. The project is added afterwards on the person's computer, in a window the runner shows when the page asks for it (the folder connected in the chat is asked
for automatically, by name), so a project is only ever allowed by something the person does on their own machine.

THE SCRIPT CARRIES A ONE-TIME PAIRING CODE, NEVER A KEY. A key in a Downloads folder would be a leak; the code works once, for a few minutes.

EVERYTHING PUT INTO A SCRIPT IS CHECKED FIRST (the server address, the code, the install address, the folder name), against a strict list of characters that cannot end a quote or start a command in either
shell. A value that does not pass raises ValueError and no script is made; nothing is escaped and hoped for.
"""
import os
import re
from typing import Dict, List

from backend.utils import runner_jobs

# The version the server wants is runner_jobs.WANTED_RUNNER_VERSION (it must equal runner/pyproject.toml and saapp_runner.__version__: a test fails when they differ), and the install address is BUILT
# from it, so the release tag the starter script installs can never disagree with the version: a release is "bump the version in those three places, commit, tag runner-v<version>, push".
RUNNER_VERSION = runner_jobs.WANTED_RUNNER_VERSION
RUNNER_REPO_DEFAULT = "SummonShenron/SAAPP"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_CODE_RE = re.compile(rf"^[{runner_jobs._CODE_ALPHABET}]{{10}}$")      # exactly the letters and digits the server makes codes from, so the two can never disagree
_SERVER_RE = re.compile(r"^(https://[A-Za-z0-9.-]{1,200}(:\d{1,5})?|http://(localhost|127\.0\.0\.1)(:\d{1,5})?)$")
_INSTALL_RE = re.compile(r"^https://[A-Za-z0-9._~:/?#@+,=&%-]{1,400}$")
_FOLDER_RE = re.compile(r"[^A-Za-z0-9 ._-]")


def release_tag() -> str:
    return f"runner-v{RUNNER_VERSION}"


def install_url() -> str:
    """The address of this version's release archive on GitHub (no `git` needed to install from it), built from RUNNER_VERSION. SAAPP_RUNNER_REPO ("owner/name") points a fork at its own repository.
    ValueError when that setting is not a plain owner/name."""
    repo = (os.getenv("SAAPP_RUNNER_REPO") or RUNNER_REPO_DEFAULT).strip()
    if not _REPO_RE.match(repo):
        raise ValueError("SAAPP_RUNNER_REPO must look like owner/name.")
    return f"https://github.com/{repo}/archive/refs/tags/{release_tag()}.zip#subdirectory=runner"


def install_address():
    """(address, "") when the install address can be built, else ("", why). Never raises: the page shows the reason instead of a download."""
    try:
        return install_url(), ""
    except ValueError as problem:
        return "", str(problem)


def public_server_url(configured: str, scheme: str, host: str) -> str:
    """The address the runner should pair with: the configured public address if there is one, else the one this request arrived on (https unless it is this machine). ValueError when it is not usable."""
    text = (configured or "").strip().rstrip("/")
    if not text:
        local = (host or "").split(":")[0] in ("localhost", "127.0.0.1")
        text = f"{'http' if local and scheme == 'http' else 'https'}://{host}"
    if not _SERVER_RE.match(text):
        raise ValueError("The server address is not one a script can safely carry.")
    return text


def _checked(server_url: str, code: str, install_url: str, folder_name: str) -> Dict[str, str]:
    if not _SERVER_RE.match(server_url or ""):
        raise ValueError("The server address must be https:// (or this machine over http), with only plain characters.")
    if not _CODE_RE.match(code or ""):
        raise ValueError("The pairing code is not in the form the server makes.")
    if not _INSTALL_RE.match(install_url or "") or "%" in (install_url or "").replace("%20", ""):
        raise ValueError("The install address must be an https:// address with only plain characters.")
    return {"server": server_url, "code": code, "install": install_url, "folder": _FOLDER_RE.sub("", folder_name or "").strip()[:60]}


_BASH = r'''#!/usr/bin/env bash
# SAAPP runner setup (@VERSION@). Read it before you run it: it is short, says what it will do, and asks first.
# Run it with:  bash <this file>
SERVER="@SERVER@"
CODE="@CODE@"
INSTALL_URL="@INSTALL@"
HOME_DIR="$HOME/.saapp-runner"
VENV="$HOME_DIR/venv"
LOG="$HOME_DIR/setup.log"

say() { printf '%s\n' "$*"; }
# A failure is said in words, kept in the log, and the window waits (a window that closes by itself hides what went wrong).
fail() {
  say ""
  say "$1"
  say "Nothing else was changed. What happened is saved in: $LOG"
  printf 'Press Enter to close. '
  read -r _
  exit 1
}

say "SAAPP runner setup"
say ""
say "This will:"
say "  1. check that Python 3.11 or newer is installed (it installs nothing system-wide)"
say "  2. make a private environment in $VENV and install saapp-runner into it, from:"
say "       $INSTALL_URL"
say "  3. pair this computer with $SERVER, using a one-time code"
say "  4. start the runner in the background now, and again every time you log in (undo with: saapp-runner uninstall-service)"
say ""
say "The runner works only in project folders you choose, in a window it shows on this computer when you add a project in SAAPP. It finds each project's tests and type checks by itself, runs only"
say "those, and only after you press Run in SAAPP. You turn them on or off in SAAPP's settings."
printf 'Continue? [y/N] '
read -r ANSWER
ANSWER="${ANSWER%$'\r'}"
case "$ANSWER" in y|Y|yes|YES|Yes) ;; *) say "Cancelled. Nothing was changed."; exit 1 ;; esac

PY=""
for CANDIDATE in python3 python; do
  if command -v "$CANDIDATE" >/dev/null 2>&1 && "$CANDIDATE" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$CANDIDATE"
    break
  fi
done
if [ -z "$PY" ]; then
  say "Python 3.11 or newer is needed and was not found. Install it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

mkdir -p "$HOME_DIR" && chmod 700 "$HOME_DIR"
: > "$LOG"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV" >> "$LOG" 2>&1 || { tail -n 15 "$LOG"; fail "Could not make the private environment."; }
fi
# Every run installs the version this page asks for (--upgrade), so a new release reaches an existing install; when it is already there pip has nothing to do.
say "Installing the SAAPP runner (this can take a minute)..."
"$VENV/bin/python" -m pip install --upgrade "$INSTALL_URL" >> "$LOG" 2>&1 || { tail -n 15 "$LOG"; fail "The install failed."; }
RUNNER="$VENV/bin/saapp-runner"
[ -x "$RUNNER" ] || fail "The install finished but the runner program was not found."

"$RUNNER" pair --server "$SERVER" "$CODE" || exit 1
say ""
say "Setting the runner to start by itself in the background..."
if "$RUNNER" install-service; then
  say ""
  say "Done. The runner is running in the background and will start again whenever you log in."
  say "Go back to SAAPP: it should show as Connected within a minute. If a window asks you to choose your project's folder, choose it; you can add more projects any time in SAAPP's settings."
  printf 'Press Enter to close. '
  read -r _
  exit 0
fi
say ""
say "That could not be set up, so the runner will run in this window instead. Leave it open while you work; press Ctrl+C to stop it."
"$RUNNER" start
STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  say ""
  say "The runner stopped (exit code $STATUS). If you did not press Ctrl+C, the message above says why."
  printf 'Press Enter to close. '
  read -r _
fi
exit "$STATUS"
'''

_CMD = r'''@echo off
rem SAAPP runner setup (@VERSION@). Read it before you run it: it is short, says what it will do, and asks first.
rem Every program is started with "call": running a .bat or .cmd file without it would end this script (pyenv-win, for example, installs python.bat).
set "SERVER=@SERVER@"
set "CODE=@CODE@"
set "INSTALL_URL=@INSTALL@"
set "HOME_DIR=%USERPROFILE%\.saapp-runner"
set "VENV=%HOME_DIR%\venv"
set "LOG=%HOME_DIR%\setup.log"

echo SAAPP runner setup
echo.
echo This will:
echo   1. check that Python 3.11 or newer is installed (it installs nothing system-wide)
echo   2. make a private environment in %VENV% and install saapp-runner into it, from:
echo        %INSTALL_URL%
echo   3. pair this computer with %SERVER%, using a one-time code
echo   4. start the runner in the background now, and again every time you log in (undo with: saapp-runner uninstall-service)
echo.
echo The runner works only in project folders you choose, in a window it shows on this computer when you add a project in SAAPP. It finds each project's tests and type checks by itself, runs only
echo those, and only after you press Run in SAAPP. You turn them on or off in SAAPP's settings.
set "ANSWER="
set /p "ANSWER=Continue? [y/N] "
if /i "%ANSWER%"=="y" goto confirmed
if /i "%ANSWER%"=="yes" goto confirmed
echo Cancelled. Nothing was changed.
pause
exit /b 1
:confirmed

set "PY="
for %%C in (py python python3) do call :trypython %%C
if not defined PY (
  echo Python 3.11 or newer is needed and was not found. Install it from https://www.python.org/downloads/ and run this again.
  pause
  exit /b 1
)

if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
> "%LOG%" echo SAAPP runner setup log
if exist "%VENV%\Scripts\python.exe" goto havevenv
call %PY% -m venv "%VENV%" >> "%LOG%" 2>&1
if errorlevel 1 (
  set "WHAT=Could not make the private environment."
  goto failed
)
:havevenv
rem Every run installs the version this page asks for (--upgrade), so a new release reaches an existing install; when it is already there pip has nothing to do.
echo Installing the SAAPP runner (this can take a minute)...
call "%VENV%\Scripts\python.exe" -m pip install --upgrade "%INSTALL_URL%" >> "%LOG%" 2>&1
if errorlevel 1 (
  set "WHAT=The install failed."
  goto failed
)
set "RUNNER=%VENV%\Scripts\saapp-runner.exe"
if not exist "%RUNNER%" (
  set "WHAT=The install finished but the runner program was not found."
  goto failed
)

call "%RUNNER%" pair --server "%SERVER%" %CODE%
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Setting the runner to start by itself in the background...
call "%RUNNER%" install-service
if not errorlevel 1 (
  echo.
  echo Done. The runner is running in the background and will start again whenever you log in.
  echo Go back to SAAPP: it should show as Connected within a minute. If a window asks you to choose your project's folder, choose it; you can add more projects any time in SAAPP's settings.
  pause
  exit /b 0
)
echo.
echo That could not be set up, so the runner will run in this window instead. Leave it open while you work; press Ctrl+C to stop it.
call "%RUNNER%" start
if errorlevel 1 (
  echo.
  echo The runner stopped. If you did not press Ctrl+C, the message above says why.
)
pause
exit /b 0

:failed
rem A failure is said in words, kept in the log, and the window waits (a window that closes by itself hides what went wrong).
echo.
type "%LOG%"
echo.
echo %WHAT%
echo Nothing else was changed. What happened is saved in: %LOG%
pause
exit /b 1

:trypython
if defined PY exit /b 0
call %1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%1"
exit /b 0
'''


def _fill(template: str, values: Dict[str, str], windows: bool) -> str:
    text = template.replace("@VERSION@", RUNNER_VERSION)
    for name, value in values.items():
        text = text.replace(f"@{name.upper()}@", value.replace("%", "%%") if windows else value)
    return text.replace("\n", "\r\n") if windows else text


def starter_scripts(server_url: str, code: str, install_url: str, folder_name: str = "") -> Dict[str, Dict[str, str]]:
    """The three starter scripts as {kind: {"filename", "text"}}: Windows (a .cmd), macOS and Linux (bash). ValueError when any value to be put in them is not safe to."""
    values = _checked(server_url, code, install_url, folder_name)
    bash = _fill(_BASH, values, False)
    return {
        "windows": {"filename": "saapp-runner-setup.cmd", "text": _fill(_CMD, values, True)},
        "mac": {"filename": "saapp-runner-setup.command", "text": bash},
        "linux": {"filename": "saapp-runner-setup.sh", "text": bash},
    }


def terminal_commands(server_url: str, code: str, install_url: str) -> List[str]:
    """The same setup as commands to type, for someone who would rather not run a script. The project folder is theirs to fill in (or to choose in the window SAAPP's Add a project opens)."""
    values = _checked(server_url, code, install_url, "")
    return [
        f'pipx install --force "{values["install"]}"',
        f'saapp-runner pair --server {values["server"]} {values["code"]}',
        "saapp-runner install-service",
        "saapp-runner add-project --root <your project folder>",
    ]
