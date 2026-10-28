"""The starter scripts a person downloads from Settings to set up the local runner (docs/local-runner-plan.md, "Installing it").

Each script is short, readable and shown before it is downloaded. It says what it is about to do and asks first; it checks for a suitable Python and installs nothing system-wide; it makes a private
environment in the person's own folder, installs the PINNED release of `saapp-runner` into it, pairs that project with the one-time code, offers to set up the project's checks, and starts the runner.

THE SCRIPT CARRIES A ONE-TIME PAIRING CODE, NEVER A KEY. A key in a Downloads folder would be a leak; the code works once, for a few minutes.

EVERYTHING PUT INTO A SCRIPT IS CHECKED FIRST (the server address, the code, the install address, the folder name), against a strict list of characters that cannot end a quote or start a command in either
shell. A value that does not pass raises ValueError and no script is made; nothing is escaped and hoped for.
"""
import re
from typing import Dict, List

from backend.utils import runner_jobs

SCRIPT_VERSION = "0.1.0"
_CODE_RE = re.compile(rf"^[{runner_jobs._CODE_ALPHABET}]{{10}}$")      # exactly the letters and digits the server makes codes from, so the two can never disagree
_SERVER_RE = re.compile(r"^(https://[A-Za-z0-9.-]{1,200}(:\d{1,5})?|http://(localhost|127\.0\.0\.1)(:\d{1,5})?)$")
_INSTALL_RE = re.compile(r"^https://[A-Za-z0-9._~:/?#@+,=&%-]{1,400}$")
_FOLDER_RE = re.compile(r"[^A-Za-z0-9 ._-]")


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
EXPECTED="@FOLDER@"
HOME_DIR="$HOME/.saapp-runner"
VENV="$HOME_DIR/venv"

say() { printf '%s\n' "$*"; }

say "SAAPP runner setup"
say ""
say "This will:"
say "  1. check that Python 3.11 or newer is installed (it installs nothing system-wide)"
say "  2. make a private environment in $VENV and install saapp-runner into it, from:"
say "       $INSTALL_URL"
say "  3. pair the project you choose with $SERVER, using a one-time code"
say "  4. offer to set up that project's checks, then start the runner"
say ""
say "The runner runs only the checks you define, and only after you press Run in SAAPP."
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

if [ ! -x "$VENV/bin/saapp-runner" ]; then
  mkdir -p "$HOME_DIR" && chmod 700 "$HOME_DIR"
  "$PY" -m venv "$VENV" || { say "Could not make the environment. Nothing else was changed."; exit 1; }
  "$VENV/bin/python" -m pip install "$INSTALL_URL" || { say "The install failed. Nothing else was changed."; exit 1; }
fi
RUNNER="$VENV/bin/saapp-runner"

say ""
if [ -n "$EXPECTED" ]; then say "SAAPP has the folder \"$EXPECTED\" connected."; fi
printf 'Project folder (drag the folder here, or type its path): '
read -r FOLDER
FOLDER="${FOLDER%$'\r'}"
FOLDER="${FOLDER#"${FOLDER%%[![:space:]]*}"}"
FOLDER="${FOLDER%"${FOLDER##*[![:space:]]}"}"
case "$FOLDER" in
  \"*\") FOLDER="${FOLDER:1:${#FOLDER}-2}" ;;
  \'*\') FOLDER="${FOLDER:1:${#FOLDER}-2}" ;;
esac
FOLDER="${FOLDER//\\ / }"
case "$FOLDER" in "~"*) FOLDER="$HOME${FOLDER#"~"}" ;; esac
if [ ! -d "$FOLDER" ]; then say "That folder was not found: $FOLDER"; exit 1; fi
FOLDER="$(cd "$FOLDER" && pwd)"
if [ -n "$EXPECTED" ] && [ "$(basename "$FOLDER")" != "$EXPECTED" ]; then
  say "Note: this folder is called \"$(basename "$FOLDER")\", but SAAPP has \"$EXPECTED\" connected."
  printf 'Use it anyway? [y/N] '
  read -r ANSWER
ANSWER="${ANSWER%$'\r'}"
  case "$ANSWER" in y|Y|yes|YES|Yes) ;; *) say "Cancelled."; exit 1 ;; esac
fi

"$RUNNER" pair --server "$SERVER" --root "$FOLDER" "$CODE" || exit 1
say ""
"$RUNNER" init --root "$FOLDER"
say ""
say "Starting the runner. Leave this window open while you work; press Ctrl+C to stop it."
exec "$RUNNER" start --root "$FOLDER"
'''

_CMD = r'''@echo off
rem SAAPP runner setup (@VERSION@). Read it before you run it: it is short, says what it will do, and asks first.
rem Every program is started with "call": running a .bat or .cmd file without it would end this script (pyenv-win, for example, installs python.bat).
set "SERVER=@SERVER@"
set "CODE=@CODE@"
set "INSTALL_URL=@INSTALL@"
set "EXPECTED=@FOLDER@"
set "HOME_DIR=%USERPROFILE%\.saapp-runner"
set "VENV=%HOME_DIR%\venv"

echo SAAPP runner setup
echo.
echo This will:
echo   1. check that Python 3.11 or newer is installed (it installs nothing system-wide)
echo   2. make a private environment in %VENV% and install saapp-runner into it, from:
echo        %INSTALL_URL%
echo   3. pair the project you choose with %SERVER%, using a one-time code
echo   4. offer to set up that project's checks, then start the runner
echo.
echo The runner runs only the checks you define, and only after you press Run in SAAPP.
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

if exist "%VENV%\Scripts\saapp-runner.exe" goto installed
if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
call %PY% -m venv "%VENV%"
if errorlevel 1 (
  echo Could not make the environment. Nothing else was changed.
  pause
  exit /b 1
)
call "%VENV%\Scripts\python.exe" -m pip install "%INSTALL_URL%"
if errorlevel 1 (
  echo The install failed. Nothing else was changed.
  pause
  exit /b 1
)
:installed
set "RUNNER=%VENV%\Scripts\saapp-runner.exe"

echo.
if defined EXPECTED echo SAAPP has the folder "%EXPECTED%" connected.
set "FOLDER="
set /p "FOLDER=Project folder (drag the folder into this window, or type its path): "
if not defined FOLDER (
  echo No folder was given.
  pause
  exit /b 1
)
set FOLDER=%FOLDER:"=%
if not exist "%FOLDER%\" (
  echo That folder was not found.
  pause
  exit /b 1
)

call "%RUNNER%" pair --server "%SERVER%" --root "%FOLDER%" %CODE%
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
call "%RUNNER%" init --root "%FOLDER%"
echo.
echo Starting the runner. Leave this window open while you work; press Ctrl+C to stop it.
call "%RUNNER%" start --root "%FOLDER%"
pause
exit /b 0

:trypython
if defined PY exit /b 0
call %1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%1"
exit /b 0
'''


def _fill(template: str, values: Dict[str, str], windows: bool) -> str:
    text = template.replace("@VERSION@", SCRIPT_VERSION)
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
    """The same setup as commands to type, for someone who would rather not run a script. The folder is theirs to fill in."""
    values = _checked(server_url, code, install_url, "")
    return [
        f'pipx install "{values["install"]}"',
        f'saapp-runner pair --server {values["server"]} --root <your project folder> {values["code"]}',
        "saapp-runner init --root <your project folder>",
        "saapp-runner start --root <your project folder>",
    ]
