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


def bash_world(tmp_path, python_ok=True, venv_fails=False, pip_fails=False, start_fails=False, service_fails=False):
    """A home folder and a bin folder with a stand-in `python3` that can make a venv holding stand-in `python` and `saapp-runner`, each logging its arguments. Each step can be made to fail the way
    the real one does: the venv cannot be made, the install cannot fetch the archive, or the runner stops after saying why."""
    home, bin_dir, log = tmp_path / "home", tmp_path / "bin", tmp_path / "calls.log"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    runner_start = 'case "$1" in start) echo "Found no tests or type check to run"; exit 1;; esac\n' if start_fails else ""
    if service_fails:
        runner_start += 'case "$1" in install-service) echo "Task Scheduler would not accept the entry."; exit 1;; esac\n'
    runner = f'#!/usr/bin/env bash\necho "runner $*" >> "{log.as_posix()}"\n{runner_start}exit 0\n'
    pip_failure = 'echo "ERROR: could not download the archive (404)" >&2\nexit 1\n' if pip_fails else "exit 0\n"
    venv_python = f'#!/usr/bin/env bash\necho "venv-python $*" >> "{log.as_posix()}"\n{pip_failure}'
    venv_failure = '  echo "venv exploded: no ensurepip here" >&2\n  exit 3\n' if venv_fails else ""
    python3 = f'''#!/usr/bin/env bash
if [ "$1" = "-c" ]; then exit {0 if python_ok else 1}; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
{venv_failure}  mkdir -p "$3/bin"
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


def run_bash(tmp_path, answers, folder_name="myapp", python_ok=True, **failures):
    tmp_path.mkdir(parents=True, exist_ok=True)
    home, bin_dir, log = bash_world(tmp_path, python_ok, **failures)
    path = tmp_path / "setup.sh"
    path.write_text(script("linux", folder_name), encoding="utf-8", newline="\n")
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    done = execute([BASH, str(path)], answers, env)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls, home


def runner_calls(calls):
    return [c for c in calls if c.startswith("runner ")]


@needs_bash
def test_the_bash_script_installs_pairs_and_sets_up_the_background_runner_in_that_order(tmp_path):
    done, calls, home = run_bash(tmp_path, "y\n\n")
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls[0].startswith("make-venv ") and calls[0].endswith("/.saapp-runner/venv") and calls[1] == f"venv-python -m pip install --upgrade {INSTALL}"
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service"]
    assert runner_calls(calls)[0] == f"runner pair --server {SERVER} {CODE}" and runner_calls(calls)[1] == "runner install-service"       # a computer, not a folder


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
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service"]


@needs_bash
def test_a_python_that_is_too_old_or_missing_stops_before_installing_anything(tmp_path):
    done, calls, home = run_bash(tmp_path, "y\n", python_ok=False)
    assert done.returncode == 1 and "Python 3.11 or newer is needed" in done.stdout and calls == [] and not (home / ".saapp-runner").exists()


@needs_bash
def test_running_it_again_reuses_the_environment_installs_the_wanted_version_and_pairs_again_with_the_new_code(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    home, bin_dir, log = bash_world(tmp_path)
    path = tmp_path / "setup.sh"
    path.write_text(script("linux"), encoding="utf-8", newline="\n")
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    for _ in range(2):
        assert execute([BASH, str(path)], f"y\n{project.as_posix()}\n", env).returncode == 0
    calls = log.read_text(encoding="utf-8").splitlines()
    # one environment, but the install runs every time with --upgrade, so a new release reaches an install that already exists (the old script skipped it and never upgraded anyone)
    assert sum(c.startswith("make-venv") for c in calls) == 1 and sum("pip install --upgrade" in c for c in calls) == 2 and sum(c.startswith("runner pair") for c in calls) == 2


@needs_bash
def test_a_failed_environment_is_explained_logged_and_waits_for_the_person_instead_of_closing(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_bash(tmp_path, f"y\n{project.as_posix()}\n\n", venv_fails=True)
    assert done.returncode == 1 and "Could not make the private environment." in done.stdout and "venv exploded" in done.stdout       # what the failing command said is shown, not hidden
    assert "Nothing else was changed" in done.stdout and "Press Enter to close" in done.stdout and "setup.log" in done.stdout
    assert "venv exploded" in (home / ".saapp-runner" / "setup.log").read_text(encoding="utf-8")                                      # and kept for later
    assert runner_calls(calls) == []


@needs_bash
def test_a_failed_install_is_explained_logged_and_nothing_is_paired_or_started(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_bash(tmp_path, f"y\n{project.as_posix()}\n\n", pip_fails=True)
    assert done.returncode == 1 and "The install failed." in done.stdout and "could not download the archive (404)" in done.stdout and "Press Enter to close" in done.stdout
    assert "could not download the archive (404)" in (home / ".saapp-runner" / "setup.log").read_text(encoding="utf-8")
    assert runner_calls(calls) == []


@needs_bash
def test_a_runner_that_stops_with_an_error_is_explained_and_waits(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_bash(tmp_path, f"y\n{project.as_posix()}\n\n", service_fails=True, start_fails=True)
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service", "start"]
    assert done.returncode == 1 and "Found no tests or type check to run" in done.stdout and "The runner stopped (exit code 1)" in done.stdout and "Press Enter to close" in done.stdout


@needs_bash
def test_a_good_run_sets_up_the_background_runner_says_so_and_does_not_start_one_in_the_window(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_bash(tmp_path, f"y\n{project.as_posix()}\n\n", folder_name="myapp")
    assert done.returncode == 0 and "Done. The runner is running in the background and will start again whenever you log in." in done.stdout and "Go back to SAAPP" in done.stdout
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service"]               # the window is not holding a runner: it can be closed


@needs_bash
def test_when_the_background_entry_cannot_be_set_up_the_runner_runs_in_the_window_instead_and_says_so(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_bash(tmp_path, f"y\n{project.as_posix()}\n", folder_name="myapp", service_fails=True)
    assert done.returncode == 0 and "Task Scheduler would not accept the entry." in done.stdout and "will run in this window instead" in done.stdout
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service", "start"]


@needs_bash
def test_the_consent_text_says_the_runner_will_start_in_the_background_and_at_login_and_how_to_undo_it(tmp_path):
    done, calls, _ = run_bash(tmp_path, "n\n")
    assert "start the runner in the background now, and again every time you log in" in done.stdout and "saapp-runner uninstall-service" in done.stdout and calls == []


# ---- the Windows script ------------------------------------------------------------------------------------------------------------------------

def run_cmd(tmp_path, answers, python_ok=True, folder_name="myapp", venv_fails=False, pip_fails=False, start_fails=False, service_fails=False):
    """Runs the .cmd script against stand-in commands. The stand-ins are .cmd files, so the script text is changed in ONE way: the venv's two .exe names become .cmd (pip makes real .exe files).
    Each step can be made to fail the way the real one does: the venv cannot be made, the install cannot fetch the archive, or the runner stops after saying why."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    home, bin_dir, log = tmp_path / "home", tmp_path / "bin", tmp_path / "calls.log"
    home.mkdir()
    bin_dir.mkdir()
    # What the stand-in venv holds: written as ordinary files (copied in by the stand-in Python), not echoed line by line, so no batch escaping is needed here.
    pip_failure = "echo ERROR: could not download the archive (404) 1>&2\r\nexit /b 1\r\n" if pip_fails else "exit /b 0\r\n"
    start_failure = 'if "%1"=="start" (\r\n  echo Found no tests or type check to run\r\n  exit /b 1\r\n)\r\n' if start_fails else ""
    if service_fails:
        start_failure += 'if "%1"=="install-service" (\r\n  echo Task Scheduler would not accept the entry.\r\n  exit /b 1\r\n)\r\n'
    (bin_dir / "fake_venv_python.cmd").write_bytes(f'@echo off\r\necho venv-python %*>> "{log}"\r\n{pip_failure}'.encode("utf-8"))
    (bin_dir / "fake_runner.cmd").write_bytes(f'@echo off\r\necho runner %*>> "{log}"\r\n{start_failure}exit /b 0\r\n'.encode("utf-8"))
    venv_failure = "  echo venv exploded: no ensurepip here 1>&2\n  exit /b 3\n" if venv_fails else ""
    py = f'''@echo off
if "%1"=="-c" exit /b {0 if python_ok else 1}
if "%1"=="-m" if "%2"=="venv" (
{venv_failure}  mkdir "%~3\\Scripts"
  copy /y "{bin_dir}\\fake_venv_python.cmd" "%~3\\Scripts\\python.cmd" >nul
  copy /y "{bin_dir}\\fake_runner.cmd" "%~3\\Scripts\\saapp-runner.cmd" >nul
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
def test_the_windows_script_installs_pairs_and_sets_up_the_background_runner_in_that_order(tmp_path):
    done, calls, home = run_cmd(tmp_path / "run", "y\n\n")
    assert [c.split(" ")[0] for c in calls] == ["make-venv", "venv-python", "runner", "runner"], (calls, done.stdout, done.stderr)       # pair, then install-service: nothing is started in the window
    assert "pip install --upgrade" in calls[1] and INSTALL in calls[1]
    assert [c.split(" ")[1] for c in calls[2:]] == ["pair", "install-service"]
    plain = [c.replace('"', "") for c in calls]
    assert plain[2] == f"runner pair --server {SERVER} {CODE}" and plain[3] == "runner install-service"             # a computer, not a folder


@needs_windows
def test_the_windows_script_keeps_going_when_python_is_a_bat_shim(tmp_path):
    # pyenv-win installs python.bat: without `call`, starting one ends the script. The stand-in py.cmd in these tests is exactly that kind of file, so every run above proves it; this pins the text.
    text = script("windows")
    assert "call %1 -c" in text and "call %PY% -m venv" in text and text.count('call "%RUNNER%"') == 3


@needs_windows
def test_the_windows_script_explains_a_failed_environment_keeps_the_log_and_waits(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_cmd(tmp_path, f"y\n{project}\n\n", venv_fails=True)
    assert done.returncode == 1 and "Could not make the private environment." in done.stdout and "venv exploded" in done.stdout          # what the failing command said is shown
    assert "Nothing else was changed" in done.stdout and "setup.log" in done.stdout and "Press any key" in done.stdout
    assert "venv exploded" in (home / ".saapp-runner" / "setup.log").read_text(encoding="utf-8", errors="replace")
    assert not [c for c in calls if c.startswith("runner")]


@needs_windows
def test_the_windows_script_explains_a_failed_install_keeps_the_log_and_pairs_nothing(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, home = run_cmd(tmp_path, f"y\n{project}\n\n", pip_fails=True)
    assert done.returncode == 1 and "The install failed." in done.stdout and "could not download the archive (404)" in done.stdout and "Press any key" in done.stdout
    assert "could not download the archive (404)" in (home / ".saapp-runner" / "setup.log").read_text(encoding="utf-8", errors="replace")
    assert not [c for c in calls if c.startswith("runner")]


@needs_windows
def test_the_windows_script_says_so_when_the_runner_stops_with_an_error(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_cmd(tmp_path, f"y\n{project}\n\n", service_fails=True, start_fails=True)
    assert [c.split(" ")[1] for c in calls if c.startswith("runner")] == ["pair", "install-service", "start"]
    assert "Found no tests or type check to run" in done.stdout and "The runner stopped." in done.stdout and "Press any key" in done.stdout


@needs_windows
def test_the_windows_script_installs_the_wanted_version_every_time_so_a_new_release_reaches_an_existing_install(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    first, calls, home = run_cmd(tmp_path / "one", f"y\n{project}\n\n")
    assert sum(c.startswith("venv-python") and "pip install --upgrade" in c for c in calls) == 1
    text = script("windows")
    assert "if exist \"%VENV%\Scripts\saapp-runner.exe\" goto installed" not in text and "pip install --upgrade" in text       # the old shortcut that skipped the install is gone


@needs_windows
def test_the_windows_script_sets_up_the_background_runner_says_so_and_falls_back_to_the_window_when_it_cannot(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_cmd(tmp_path / "ok", f"y\n{project}\n\n")
    assert "Done. The runner is running in the background and will start again whenever you log in." in done.stdout and "Go back to SAAPP" in done.stdout
    assert [c.split(" ")[1] for c in calls if c.startswith("runner")] == ["pair", "install-service"]
    done, calls, _ = run_cmd(tmp_path / "no", f"y\n{project}\n\n", service_fails=True)
    assert "Task Scheduler would not accept the entry." in done.stdout and "will run in this window instead" in done.stdout
    assert [c.split(" ")[1] for c in calls if c.startswith("runner")] == ["pair", "install-service", "start"]


@needs_bash
def test_the_bash_script_asks_nothing_but_the_first_yes_and_pairs_the_computer_with_no_folder(tmp_path):
    done, calls, _ = run_bash(tmp_path, "y\n\n")                                                       # the second newline only answers "Press Enter to close"
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Project folder" not in done.stdout and "That folder was not found" not in done.stdout
    pair = runner_calls(calls)[0]
    assert pair == f"runner pair --server {SERVER} {CODE}" and "--root" not in " ".join(calls)
    assert [c.split(" ")[1] for c in runner_calls(calls)] == ["pair", "install-service"]
    assert runner_calls(calls)[1] == "runner install-service"


@needs_bash
def test_the_bash_script_does_not_read_a_folder_even_if_one_is_typed(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    done, calls, _ = run_bash(tmp_path, f"y\n{project.as_posix()}\n")
    assert done.returncode == 0 and all("--root" not in call for call in calls)                         # the typed line was only the answer to the closing question: no folder reaches the runner
