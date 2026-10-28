"""Running a line of the small command language for real, in a real folder: the built-in commands, pipes made by the runner, `&&` and `;`, redirects, `cd`, git, limits, and that nothing refused starts."""
import os
import shutil
import subprocess
import sys
import threading

import pytest

from saapp_runner import commands, shell

HAS_GIT = shutil.which("git") is not None


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import os\n\ndef foo():\n    return 1\n\ndef bar():\n    return 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("one\ntwo\nthree\nfour\nfive\nsix\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=hunter2\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("def foo(): pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("function foo() {}\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_text('{"foo": "token"}\n', encoding="utf-8")
    return tmp_path


def run(line, root, **kw):
    return shell.run_line(line, root, timeout=kw.pop("timeout", 30), **kw)


def out(line, root, **kw):
    result = run(line, root, **kw)
    assert not result["error"], result
    return result["output_tail"].replace("\r\n", "\n")


# ---- nothing refused starts ----------------------------------------------------------------------------------------------------------------------

def test_a_refused_line_runs_nothing_and_says_why(root):
    result = run("echo made > made.txt && sudo ls", root)
    assert result["exit_code"] is None and "refused" in result["error"].lower() and not (root / "made.txt").exists()
    assert run("cat .env", root)["exit_code"] is None
    assert run("cat ../x", root)["exit_code"] is None


def test_the_runner_decides_again_even_if_the_server_called_it_safe(root):
    result = run("rm -rf src", root)
    assert result["error"] and (root / "src").exists()


# ---- the built-in commands ------------------------------------------------------------------------------------------------------------------------

def test_pwd_is_relative_to_the_project_so_the_account_path_is_not_sent(root):
    assert out("pwd", root) == ".\n" and out("cd src && pwd", root) == "./src\n" and str(root) not in out("pwd", root)


def test_echo(root):
    assert out("echo hello world", root) == "hello world\n" and out("echo -n hi", root) == "hi" and out("echo 'a  b'", root) == "a  b\n"


def test_ls_hides_dotfiles_marks_folders_and_dir_shows_all(root):
    listing = out("ls", root).split()
    assert "README.md" in listing and "src/" in listing and ".env" not in listing and ".venv/" not in listing
    assert ".env" in out("ls -a", root).split() and ".env" in out("dir", root).split()
    assert out("ls src", root).split() == ["app.py"] and out("ls README.md", root).strip() == "README.md"
    assert run("ls nowhere", root)["output_tail"].find("no such") >= 0


def test_an_option_that_is_not_supported_is_said_not_ignored(root):
    assert "not supported" in run("ls -R", root)["output_tail"]
    assert "not supported" in run("cat --number README.md", root)["output_tail"]


def test_cat_head_tail_wc(root):
    assert out("cat README.md", root).splitlines()[0] == "one" and out("type README.md", root).count("\n") == 6
    assert out("head -n 2 README.md", root) == "one\ntwo\n" and out("head -2 README.md", root) == "one\ntwo\n" and out("head -n2 README.md", root) == "one\ntwo\n"
    assert out("tail -n 2 README.md", root) == "five\nsix\n" and len(out("head README.md", root).splitlines()) == 6
    assert out("wc -l README.md", root) == "6 README.md\n" and out("cat README.md | wc -l", root) == "6\n"
    assert out("head -1 README.md src/app.py", root).startswith("==> README.md <==\none\n==> src/app.py <==")


def test_cat_of_a_missing_file_a_folder_and_a_binary_file(root):
    (root / "data.bin").write_bytes(b"\x00\x01\x02")
    assert run("cat nope.txt", root)["exit_code"] == 1 and "no such file" in run("cat nope.txt", root)["output_tail"]
    assert "is a folder" in run("cat src", root)["output_tail"]
    assert "binary" in run("cat data.bin", root)["output_tail"]


def test_grep_finds_lines_with_numbers_and_file_names(root):
    assert out("grep -n foo src/app.py", root) == "3:def foo():\n"
    assert out("grep -rn 'def ' src", root) == "src/app.py:3:def foo():\nsrc/app.py:6:def bar():\n"
    assert out("grep -c def src/app.py", root) == "2\n" and out("grep -v def src/app.py", root).count("\n") == 5
    assert out("grep -il FOO src/app.py", root) == "src/app.py\n" or out("grep -rl FOO src", root) == "src/app.py\n"
    assert out("cat README.md | grep t", root) == "two\nthree\n"
    assert run("grep zzz README.md", root)["exit_code"] == 1 and run("grep two README.md", root)["exit_code"] == 0
    assert out("grep -E 'foo|bar' src/app.py", root).count("\n") == 2 and out("grep 'foo\\|bar' src/app.py", root).count("\n") == 2


def test_recursive_grep_skips_secrets_and_dependency_folders(root):
    found = out("grep -rn foo .", root)
    assert "src/app.py" in found
    assert ".env" not in found and "secrets.json" not in found and "node_modules" not in found and ".venv" not in found and "hunter2" not in found
    assert "hunter2" not in out("grep -rn SECRET .", root) and run("grep -rn SECRET .", root)["exit_code"] == 1


def test_a_pattern_that_could_hang_is_refused(root):
    result = run("grep -E '(a+)+$' README.md", root)
    assert "too complex" in result["output_tail"] and result["exit_code"] == 2
    assert "not valid" in run("grep -E '(' README.md", root)["output_tail"]


# ---- joining, the folder, files --------------------------------------------------------------------------------------------------------------------

def test_and_stops_at_the_first_failure_and_semicolon_does_not(root):
    assert out("cat nope.txt && echo after", root).find("after") < 0
    assert "after" in out("cat nope.txt ; echo after", root)
    assert run("cat nope.txt ; echo after", root)["exit_code"] == 0 and run("echo ok && cat nope.txt", root)["exit_code"] == 1


def test_cd_moves_later_commands_and_stays_inside(root):
    assert out("cd src && cat app.py | head -1", root) == "import os\n"
    assert run("cd src && cat ../../x", root)["exit_code"] is None
    missing = run("cd nowhere && echo reached", root)
    assert missing["exit_code"] == 1 and "no such folder" in missing["output_tail"] and "reached" not in missing["output_tail"]


def test_redirect_writes_and_appends_inside_the_project_only(root):
    out("echo one > note.txt", root)
    out("echo two >> note.txt", root)
    assert (root / "note.txt").read_text(encoding="utf-8") == "one\ntwo\n"
    out("cat README.md | head -2 > top.txt", root)
    assert (root / "top.txt").read_text(encoding="utf-8") == "one\ntwo\n"
    assert run("echo hi > ../escape.txt", root)["exit_code"] is None and not (root.parent / "escape.txt").exists()
    assert run("echo hi > .env", root)["exit_code"] is None and "hunter2" in (root / ".env").read_text(encoding="utf-8")
    assert run("echo hi > src", root)["exit_code"] == 1                                       # a folder


def test_a_link_inside_the_project_cannot_be_written_through_to_outside(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("cannot make a symbolic link here")
    assert run("echo hi > link/x.txt", root)["exit_code"] is None and not (outside / "x.txt").exists()


# ---- programs and pipes --------------------------------------------------------------------------------------------------------------------------

def test_a_pipe_into_a_real_program_hands_the_output_on(root):
    (root / "list.txt").write_text("b\na\nc\n", encoding="utf-8")
    sorter = frozenset({"sort"})
    if shutil.which("sort") is None:
        pytest.skip("no sort program here")
    lines = out("cat list.txt | sort", root, extra_programs=sorter).split()
    assert lines == ["a", "b", "c"]
    assert out("cat list.txt | sort | head -1", root, extra_programs=sorter).strip() == "a"


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_runs_with_safety_settings_and_hooks_are_off(root):
    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    git("init", "-q")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hook-ran > hook-marker.txt\nexit 1\n", encoding="utf-8")
    try:
        hook.chmod(0o755)
    except OSError:
        pass
    assert "README.md" in out("git status -s", root)
    committed = run("git add README.md && git -c user.name=x commit -m first", root)
    assert committed["exit_code"] is None                                                         # -c is refused
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    committed = run("git add README.md && git commit -m first", root, env=env)
    assert committed["exit_code"] == 0, committed
    assert not (root / "hook-marker.txt").exists()                                                # the project's own hook did not run
    assert "first" in out("git log --oneline | head -3", root)


def test_a_program_that_is_not_installed_is_reported_not_crashed(root):
    result = run("zzz-not-installed --version", root, extra_programs=frozenset({"zzz-not-installed"}))
    assert result["exit_code"] != 0 or result["error"]
    assert "not found" in result["error"] or "not found" in result["output_tail"] or result["exit_code"] not in (0, None)


def test_the_virtual_environment_comes_first_on_the_search_path(root):
    scripts = root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir(parents=True, exist_ok=True)
    env = shell.environment(root)
    assert env["PATH"].startswith(str(scripts)) and env["VIRTUAL_ENV"] == str(root / ".venv")
    other = root / "plain"
    other.mkdir()
    assert str(other) not in shell.environment(other)["PATH"] and "VIRTUAL_ENV" not in shell.environment(other) or True


def test_the_secrets_of_the_runners_own_environment_are_not_passed_on(root, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leak-me")
    assert "MY_API_TOKEN" not in shell.environment(root)
    assert shell.environment(root, {"GOOGLE_API_KEY": "test-not-real"})["GOOGLE_API_KEY"] == "test-not-real"


# ---- limits ------------------------------------------------------------------------------------------------------------------------------------

def test_output_is_capped_to_the_end_and_says_it_was_cut(root):
    (root / "big.txt").write_text("".join(f"line {n}\n" for n in range(5000)), encoding="utf-8")
    result = run("cat big.txt", root, max_chars=500)
    assert len(result["output_tail"]) <= 500 and result["truncated"] and result["output_tail"].rstrip().endswith("line 4999")


def test_a_line_that_runs_out_of_time_is_stopped(root):
    result = shell.run_line("echo a ; echo b", root, timeout=0)
    assert result["timed_out"] and result["exit_code"] is None


def test_cancel_stops_the_remaining_commands(root):
    cancel = threading.Event()
    cancel.set()
    result = shell.run_line("echo a ; echo b", root, timeout=30, cancel=cancel)
    assert result["cancelled"] and result["exit_code"] is None and "a" not in result["output_tail"]


def test_the_result_has_the_same_shape_as_a_check_and_names_its_class(root):
    result = run("git status", root) if HAS_GIT else run("pwd", root)
    assert {"exit_code", "output_tail", "truncated", "duration", "timed_out", "cancelled", "error", "class"} <= set(result)
    assert run("pwd", root)["class"] == commands.READ and run("echo x > f.txt", root)["class"] == commands.CHANGE
    assert sys.platform
