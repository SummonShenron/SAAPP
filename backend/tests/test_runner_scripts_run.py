"""The starter scripts, run for real against stand-in commands (backend/utils/runner_scripts.py). The bash script runs wherever bash exists; the Windows script runs on Windows. The stand-ins log what they
were asked to do, so what is checked is what the script DID: the order, the arguments, and that nothing happens before the person says yes.

Answers are sent as raw bytes: text mode would turn each newline into a carriage return and newline on Windows, which is not what a person's keyboard sends to bash."""
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.utils import runner_scripts as rs

SERVER = "https://saapp.example.com"
CODE = "ABCDEFGHJK"
INSTALL = "https://github.com/example/saapp/archive/refs/tags/runner-v0.1.0.zip#subdirectory=runner"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not available here")
needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="the .cmd script runs on Windows")


def script(kind, folder_name="myapp"):
    return rs.starter_scripts(SERVER, CODE, INSTALL, folder_name)[kind]["text"]


def execute(command, answers, env):
    done = subprocess.run(command, input=answers.encode("utf-8"), capture_output=True, env=env, timeout=90)
    return SimpleNamespace(returncode=done.returncode, stdout=done.stdout.decode("utf-8", "replace"), stderr=done.stderr.decode("utf-8", "replace"))


def real(path):
    """A path as one comparable string. Git Bash names folders its own way (C:\\x is /c/x, the temp folder is /tmp), so a path it printed is translated back with cygpath, which ships beside it."""
    text = str(path)
    if sys.platform == "win32" and text.startswith("/") and BASH:
        import pathlib

        cygpath = pathlib.Path(BASH).with_name("cygpath.exe")
        if cygpath.exists():
            text = subprocess.run([str(cygpath), "-m", text], capture_output=True, text=True).stdout.strip() or text
    return os.path.realpath(text).replace("\\", "/").lower() if sys.platform == "win32" else os.path.realpath(text)


# ---- the bash script ---------------------------------------------------------------------------------------------------------------------------

@needs_bash
def test_the_bash_script_is_valid_bash(tmp_path):
    path = tmp_path / "setup.sh"
    path.write_text(script("linux"), encoding="utf-8", newline="\n")
    done = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def bash_world(tmp_path, python_ok=True):
    """A home folder and a bin folder with a stand-in `python3` that can make a venv holding stand-in `python` and `saapp-runner`, each logging its arguments."""
    home, bin_dir, log = tmp_path / "home", tmp_path / "bin", tmp_path / "calls.log"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    runner = f'#!/usr/bin/env bash\necho "runner $*" >> "{log.as_posix()}"\nexit 0\n'
    venv_python = f'#!/usr/bin/env bash\necho "venv-python $*" >> "{log.as_posix()}"\nexit 0\n'
    python3 = f'''#!/usr/bin/env bash
if [ "$1" = "-c" ]; then exit {0 if python_ok else 1}; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
  mkdir -p "$3/bin"
  printf '%s' '{venv_python}' > "$3/bin/python"
  printf '%s' '{runner}' > "$3/bin/saapp-runner"
  chmod +x "$3/bin/python" "$3/bin/saapp-runner"
  echo "make-venv $3" >> "{log.as_posix()}"
  exit 0
fi
exit 0
'''
    for name in ("python3", "python"):                  # both names the script tries, so a machine's real Python can never be found instead of the stand-in
        stub = bin_dir / name
        stub.write_text(python3, encoding="utf-8", newline="\n")
        os.chmod(stub, 0o755)
    return home, bin_dir, log


def run_bash(tmp_path, answers, folder_name="myapp", python_ok=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    home, bin_dir, log = bash_world(tmp_path, python_ok)
    path = tmp_path / "setup.sh"
    path.write_text(script("linux", folder_name), encoding="utf-8", newline="\n")
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = execute([BASH, str(path)], answers, env)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls, home


def runner_calls(calls):
    return [c for c in calls if c.startswith("runner ")]


@needs_bash
def test_the_bash_script_installs_pairs_sets_up_and_starts_in_that_order(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_bash(tmp_path, f"y\n{project.as_posix()}\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls[0].startswith("make-venv ") and calls[0].endswith("/.saapp-runner/venv") and calls[1] == f"venv-python -m pip install {INSTALL}"
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "start"]
    pair = runner_calls(calls)[0]
    assert pair.startswith(f"runner pair --server {SERVER} --root ") and pair.endswith(f" {CODE}")
    assert all(real(c.split("--root ")[1].split(" ")[0]) == real(project) for c in runner_calls(calls)[:3])


@needs_bash
def test_declining_changes_nothing_and_pressing_enter_alone_counts_as_no(tmp_path):
    done, calls, home = run_bash(tmp_path / "a", "n\n")
    assert done.returncode == 1 and "Cancelled. Nothing was changed." in done.stdout and calls == [] and not (home / ".saapp-runner").exists()
    done, calls, home = run_bash(tmp_path / "b", "\n")
    assert done.returncode == 1 and calls == [] and not (home / ".saapp-runner").exists()


@needs_bash
def test_a_carriage_return_after_an_answer_is_tolerated(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_bash(tmp_path, f"y\r\n{project.as_posix()}\r\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "start"]


@needs_bash
def test_a_python_that_is_too_old_or_missing_stops_before_installing_anything(tmp_path):
    done, calls, home = run_bash(tmp_path, "y\n", python_ok=False)
    assert done.returncode == 1 and "Python 3.11 or newer is needed" in done.stdout and calls == [] and not (home / ".saapp-runner").exists()


@needs_bash
def test_a_folder_that_does_not_exist_stops_before_pairing(tmp_path):
    done, calls, _ = run_bash(tmp_path, f"y\n{(tmp_path / 'nowhere').as_posix()}\n")
    assert done.returncode == 1 and "That folder was not found" in done.stdout and runner_calls(calls) == []


@needs_bash
@pytest.mark.parametrize("how", ["plain", "double quotes", "single quotes", "padded", "escaped spaces"])
def test_a_folder_with_spaces_is_found_however_it_was_typed_or_dragged_in(tmp_path, how):
    project = tmp_path / "my app"
    project.mkdir()
    path = project.as_posix()
    typed = {"plain": path, "double quotes": f'"{path}"', "single quotes": f"'{path}'", "padded": f"   {path}   ", "escaped spaces": path.replace(" ", "\\ ")}[how]
    done, calls, _ = run_bash(tmp_path / "run", f"y\n{typed}\ny\n", folder_name="my app")
    assert done.returncode == 0, (how, done.stdout, done.stderr)
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "start"]
    assert real(runner_calls(calls)[0].split("--root ")[1].rsplit(" ", 1)[0]) == real(project)


@needs_bash
def test_a_folder_named_differently_from_the_connected_one_asks_before_going_on(tmp_path):
    project = tmp_path / "other-name"
    project.mkdir()
    declined, calls, _ = run_bash(tmp_path / "a", f"y\n{project.as_posix()}\nn\n", folder_name="myapp")
    assert declined.returncode == 1 and 'but SAAPP has "myapp" connected' in declined.stdout and runner_calls(calls) == []
    accepted, calls, _ = run_bash(tmp_path / "b", f"y\n{project.as_posix()}\ny\n", folder_name="myapp")
    assert accepted.returncode == 0 and [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "start"]


@needs_bash
def test_running_it_again_reuses_the_environment_but_pairs_again_with_the_new_code(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    home, bin_dir, log = bash_world(tmp_path)
    path = tmp_path / "setup.sh"
    path.write_text(script("linux"), encoding="utf-8", newline="\n")
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    for _ in range(2):
        assert execute([BASH, str(path)], f"y\n{project.as_posix()}\n", env).returncode == 0
    calls = log.read_text(encoding="utf-8").splitlines()
    assert sum(c.startswith("make-venv") for c in calls) == 1 and sum("pip install" in c for c in calls) == 1 and sum(c.startswith("runner pair") for c in calls) == 2


# ---- the Windows script ------------------------------------------------------------------------------------------------------------------------

def run_cmd(tmp_path, answers, python_ok=True, folder_name="myapp"):
    """Runs the .cmd script against stand-in commands. The stand-ins are .cmd files, so the script text is changed in ONE way: the venv's two .exe names become .cmd (pip makes real .exe files)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    home, bin_dir, log = tmp_path / "home", tmp_path / "bin", tmp_path / "calls.log"
    home.mkdir()
    bin_dir.mkdir()
    py = f'''@echo off
if "%1"=="-c" exit /b {0 if python_ok else 1}
if "%1"=="-m" if "%2"=="venv" (
  mkdir "%~3\\Scripts"
  > "%~3\\Scripts\\python.cmd" echo @echo off
  >> "%~3\\Scripts\\python.cmd" echo echo venv-python %%*^>^> "{log}"
  > "%~3\\Scripts\\saapp-runner.cmd" echo @echo off
  >> "%~3\\Scripts\\saapp-runner.cmd" echo echo runner %%*^>^> "{log}"
  echo make-venv %~3>> "{log}"
  exit /b 0
)
exit /b 0
'''
    for name in ("py.cmd", "python.cmd", "python3.cmd"):         # every name the script tries, so a machine's real Python can never be found instead of the stand-in
        (bin_dir / name).write_text(py, encoding="utf-8", newline="")
    text = script("windows", folder_name).replace("python.exe", "python.cmd").replace("saapp-runner.exe", "saapp-runner.cmd")
    path = tmp_path / "setup.cmd"
    path.write_bytes(text.encode("utf-8"))
    env = {**os.environ, "USERPROFILE": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    # The answers come from a FILE, not a pipe: cmd.exe hands only the first `set /p` any data when its input is a pipe (checked), while a file behaves like the console.
    answers_file = tmp_path / "answers.txt"
    answers_file.write_bytes(answers.replace("\n", "\r\n").encode("utf-8"))
    with answers_file.open("rb") as handle:
        raw = subprocess.run(["cmd", "/c", str(path)], stdin=handle, capture_output=True, env=env, timeout=90)
    done = SimpleNamespace(returncode=raw.returncode, stdout=raw.stdout.decode("utf-8", "replace"), stderr=raw.stderr.decode("utf-8", "replace"))
    calls = [c.strip() for c in log.read_text(encoding="utf-8", errors="replace").splitlines()] if log.exists() else []
    return done, calls, home


@needs_windows
def test_the_windows_script_declines_cleanly_and_changes_nothing(tmp_path):
    done, calls, home = run_cmd(tmp_path, "n\n\n")
    assert "Cancelled. Nothing was changed." in done.stdout and calls == [] and not (home / ".saapp-runner").exists()


@needs_windows
def test_the_windows_script_stops_before_installing_when_python_is_too_old(tmp_path):
    done, calls, home = run_cmd(tmp_path, "y\n\n", python_ok=False)
    assert "Python 3.11 or newer is needed" in done.stdout and calls == [] and not (home / ".saapp-runner").exists()


@needs_windows
def test_the_windows_script_installs_pairs_sets_up_and_starts_in_that_order(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_cmd(tmp_path / "run", f"y\n{project}\n\n")
    assert [c.split(" ")[0] for c in calls] == ["make-venv", "venv-python", "runner", "runner"], (calls, done.stdout, done.stderr)
    assert "pip install" in calls[1] and INSTALL in calls[1]
    assert [c.split(" ")[1] for c in calls[2:]] == ["pair", "start"]
    plain = [c.replace('"', "") for c in calls]                                           # the script quotes its arguments, which is what lets a folder name hold spaces
    assert f"--server {SERVER}" in plain[2] and plain[2].endswith(CODE) and all(str(project) in c for c in plain[2:])


@needs_windows
def test_the_windows_script_handles_a_quoted_folder_with_spaces_a_missing_one_and_no_answer(tmp_path):
    project = tmp_path / "my app"
    project.mkdir()
    done, calls, _ = run_cmd(tmp_path / "a", f'y\n"{project}"\n\n', folder_name="my app")
    assert [c.split(" ")[1] for c in calls if c.startswith("runner")] == ["pair", "start"], (calls, done.stdout)
    done, calls, _ = run_cmd(tmp_path / "b", f"y\n{tmp_path / 'nowhere'}\n\n")
    assert "That folder was not found" in done.stdout and not [c for c in calls if c.startswith("runner")]
    done, calls, _ = run_cmd(tmp_path / "c", "y\n\n\n")
    assert "No folder was given" in done.stdout and not [c for c in calls if c.startswith("runner")]


@needs_windows
def test_the_windows_script_keeps_going_when_python_is_a_bat_shim(tmp_path):
    # pyenv-win installs python.bat: without `call`, starting one ends the script. The stand-in py.cmd in these tests is exactly that kind of file, so every run above proves it; this pins the text.
    text = script("windows")
    assert "call %1 -c" in text and "call %PY% -m venv" in text and text.count('call "%RUNNER%"') == 2
