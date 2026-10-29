"""Running a command with limits (saapp_runner/execute.py): no shell, a clean environment, a time limit that kills the whole tree, a capped output, a way to cancel."""
import os
import sys
import textwrap
import threading
import time

import pytest

from saapp_runner import execute as ex

PY = sys.executable


def py(code, **kwargs):
    """Runs a snippet of Python through the real runner code and returns its result."""
    return ex.run([PY, "-c", textwrap.dedent(code)], cwd=kwargs.pop("cwd", os.getcwd()), timeout=kwargs.pop("timeout", 30), **kwargs)


# ---- building the command --------------------------------------------------------------------------------------------------------------------

def test_on_windows_a_program_the_runner_starts_gets_no_console_window_and_elsewhere_nothing_changes():
    assert ex.no_window(windows=True) == {"creationflags": 0x08000000}          # CREATE_NO_WINDOW: the runner has no console (pythonw), so a test run opened a black one
    assert ex.no_window(windows=False) == {}


def test_a_run_passes_that_option_to_the_program_it_starts(monkeypatch):
    seen = {}
    real = ex.subprocess.Popen

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(ex.subprocess, "Popen", spy)
    ex.run([sys.executable, "-c", "print('hi')"], os.getcwd(), 30)
    assert seen.get("creationflags", 0) == ex.no_window().get("creationflags", 0)
    if os.name == "nt":
        assert seen["creationflags"] == 0x08000000


def test_files_replace_the_placeholder_as_separate_words_that_cannot_be_read_as_options():
    assert ex.build_argv(["pytest", "-q", "{files}"], ["a.py", "t/b.py"]) == ["pytest", "-q", "./a.py", "./t/b.py"]
    assert ex.build_argv(["pytest", "-q", "{files}"], []) == ["pytest", "-q"]
    assert ex.build_argv(["npm", "test"], []) == ["npm", "test"]


def test_files_given_to_a_check_that_takes_none_are_refused():
    with pytest.raises(ValueError):
        ex.build_argv(["npm", "test"], ["a.py"])


def test_files_are_never_passed_to_a_cmd_or_bat_program_on_windows(monkeypatch):
    monkeypatch.setattr(ex.shutil, "which", lambda name, path=None: {"npm": "C:\\Program Files\\nodejs\\npm.CMD", "tool": "C:\\bin\\tool.bat", "python": "C:\\Python\\python.exe"}.get(name))
    assert ex.safe_for_files(["npm", "test"], windows=True) is False
    assert ex.safe_for_files(["tool", "{files}"], windows=True) is False
    assert ex.safe_for_files(["python", "-m", "pytest", "{files}"], windows=True) is True
    assert ex.safe_for_files(["npm", "test"], windows=False) is True                        # elsewhere there is no cmd.exe to act on the arguments


def test_a_file_name_with_shell_characters_stays_one_harmless_word(tmp_path):
    name = "a b; echo pwned && x.py"
    assert ex.build_argv([PY, "{files}"], [name]) == [PY, "./" + name]


# ---- running it ------------------------------------------------------------------------------------------------------------------------------

def test_a_command_that_succeeds_reports_its_output_and_exit_code_zero():
    result = py("print('hello'); print('there')")
    assert result["exit_code"] == 0 and result["output_tail"].split() == ["hello", "there"] and result["timed_out"] is False and result["cancelled"] is False and result["error"] == ""


def test_a_failing_command_reports_its_exit_code_and_what_it_wrote_to_the_error_stream():
    result = py("import sys; print('out'); print('oops', file=sys.stderr); sys.exit(3)")
    assert result["exit_code"] == 3 and "out" in result["output_tail"] and "oops" in result["output_tail"]


def test_a_command_that_cannot_be_found_is_reported_not_raised():
    result = ex.run(["definitely-not-a-real-program-xyz"], cwd=os.getcwd(), timeout=5)
    assert result["exit_code"] is None and result["error"].startswith("command not found: definitely-not-a-real-program-xyz") and result["output_tail"] == ""


def test_the_command_runs_in_the_project_folder(tmp_path):
    assert os.path.realpath(py("import os; print(os.getcwd())", cwd=tmp_path)["output_tail"].strip()) == os.path.realpath(str(tmp_path))


def test_the_shells_secrets_do_not_reach_the_command(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "super-secret-value")
    monkeypatch.setenv("GOOGLE_API_KEY", "another-secret")
    result = py("import os; print('MY_API_KEY' in os.environ, 'GOOGLE_API_KEY' in os.environ, 'PATH' in os.environ, os.environ.get('PYTHONUNBUFFERED'))")
    assert result["output_tail"].split() == ["False", "False", "True", "1"]
    assert "super-secret-value" not in result["output_tail"]


def test_the_environment_keeps_only_what_a_program_needs(monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "x")
    env = ex.clean_env({"EXTRA": "1"})
    assert "SOME_TOKEN" not in env and env["EXTRA"] == "1" and env["NO_COLOR"] == "1" and "PATH" in env


# ---- the limits ------------------------------------------------------------------------------------------------------------------------------

def test_a_command_that_runs_too_long_is_stopped_and_reported_as_timed_out():
    started = time.monotonic()
    result = py("import time; print('start', flush=True); time.sleep(60)", timeout=1.5)
    assert result["timed_out"] is True and result["exit_code"] is None and "start" in result["output_tail"] and time.monotonic() - started < 20


def _alive(pid):
    if os.name == "nt":
        import subprocess

        shown = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in shown
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_a_child_the_command_started_is_stopped_too(tmp_path):
    # A test runner starts children. Killing only the parent would leave them running on the person's machine, so the whole tree must go.
    pid_file = tmp_path / "child.pid"
    code = f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        open({str(pid_file)!r}, "w").write(str(child.pid))
        print("parent started a child", flush=True)
        time.sleep(120)
    """
    started = time.monotonic()
    result = py(code, timeout=2.5)
    assert result["timed_out"] is True and pid_file.exists()
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(child), "the child the command started was left running"
    assert time.monotonic() - started < 30


def test_a_long_output_keeps_only_its_end_and_says_the_start_was_cut():
    result = py("print('x' * 50000); print('THE END')", max_chars=1000)
    assert result["truncated"] is True and len(result["output_tail"]) <= 1000 and result["output_tail"].rstrip().endswith("THE END") and result["exit_code"] == 0


def test_a_short_output_is_not_marked_as_cut():
    assert py("print('short')", max_chars=1000)["truncated"] is False


def test_output_that_is_not_valid_text_does_not_break_the_run():
    result = py("import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end\\n')")
    assert result["exit_code"] == 0 and result["output_tail"].startswith("ok ") and result["output_tail"].strip().endswith("end")


def test_a_command_can_be_cancelled_while_it_runs():
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    started = time.monotonic()
    result = py("import time; time.sleep(60)", cancel=cancel, timeout=60)
    assert result["cancelled"] is True and result["exit_code"] is None and result["timed_out"] is False and time.monotonic() - started < 20


def test_there_is_no_shell_so_nothing_in_a_word_is_expanded_or_chained(tmp_path):
    marker = tmp_path / "pwned.txt"
    result = ex.run([PY, "-c", "import sys; print(sys.argv[1])", f"x; echo bad > {marker}"], cwd=str(tmp_path), timeout=10)
    assert result["exit_code"] == 0 and "x; echo bad" in result["output_tail"] and not marker.exists()
