"""Runs a line of the small command language (commands.py) on this computer. There is NO SHELL anywhere in it.

- The line is classified AGAIN here, on this machine, by the runner's own copy of the rules: whatever the server said about it is not trusted, and a refused line never starts anything.
- The programs it runs are started directly with a list of words (execute.run), in a clean environment, with a time limit, a capped output and the whole process tree killed on a timeout. Pipes are
  made by this module, not by an operating system shell: each stage's output is handed to the next stage's input.
- The small built-in commands (pwd, echo, ls, dir, cat, type, head, tail, wc, grep, cd) are implemented here in Python, the same on every system, and re-check every path they touch: inside the
  project folder, and not a file that holds secrets. `grep -r` skips such files and the usual dependency folders instead of reading them.
- git is run with a few safety settings the card does not show (no hooks, no pager, no external diff program, no prompts), and the project's own virtual environment (.venv or venv) is put first on the
  search path, as if it were activated, so `python`, `pip` and `pytest` mean the project's own.
- IT IS NOT A SANDBOX. A command the person approved runs with their own privileges in their own project. The control is what the language allows, what the card showed, and the person's click.
"""
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from . import commands, execute

PIPE_CHARS = 1_000_000            # how much of one stage's output is handed on to the next
READ_BYTES = 2_000_000            # how much of one file a built-in command reads
GREP_FILES = 20_000
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".next", "target"})
_CMD_SPECIAL = re.compile(r"[&|<>^%\"!\r\n]")
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[*+][^)]*\)\s*[*+{]")


class _Stage:
    """What one command produced."""

    def __init__(self, code: int = 0, out: str = "", err: str = "") -> None:
        self.code, self.out, self.err = code, out, err
        self.truncated = self.timed_out = self.cancelled = False
        self.error = ""


# ---- the environment ------------------------------------------------------------------------------------------------------------------------------

def venv_dir(root: Path) -> Optional[Path]:
    for name in (".venv", "venv"):
        scripts = root / name / ("Scripts" if os.name == "nt" else "bin")
        if scripts.is_dir():
            return scripts
    return None


def environment(root: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The clean environment, with the project's virtual environment first on the search path when it has one."""
    env = execute.clean_env(extra)
    scripts = venv_dir(root)
    if scripts is not None:
        env["PATH"] = str(scripts) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(scripts.parent)
    return env


# ---- reading and writing files for the built-in commands --------------------------------------------------------------------------------------------

def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def _read(root: Path, cwd: Path, name: str) -> Tuple[Optional[str], str]:
    """(text, "") or (None, why not). Re-checks the path: inside the project, not a secrets file."""
    try:
        path = commands.check_path(root, cwd, name, reading=True)
    except commands.CommandError as problem:
        return None, str(problem)
    if path.is_dir():
        return None, f"{name}: is a folder"
    try:
        with path.open("rb") as handle:
            data = handle.read(READ_BYTES + 1)
    except OSError:
        return None, f"{name}: no such file"
    if _is_binary(data):
        return None, f"{name}: binary file, not shown"
    text = data[:READ_BYTES].decode("utf-8", errors="replace")
    return text + ("\n[the rest of this file was not read]\n" if len(data) > READ_BYTES else ""), ""


def _split_options(args: List[str], allowed: str, name: str) -> Tuple[str, List[str], str]:
    """(single-letter options, the other words, a complaint). `--` ends the options. A long option or a letter not in `allowed` is a complaint, never silently ignored."""
    letters, rest = "", []
    done = False
    for arg in args:
        if done or not arg.startswith("-") or arg == "-":
            rest.append(arg)
        elif arg == "--":
            done = True
        elif arg.startswith("--"):
            return "", [], f"{name}: the option {arg} is not supported here."
        else:
            for letter in arg[1:]:
                if letter not in allowed:
                    return "", [], f"{name}: the option -{letter} is not supported here."
                letters += letter
    return letters, rest, ""


def _count_option(args: List[str]) -> Tuple[Optional[int], List[str], str]:
    """For head and tail: the number of lines (-n 5, -n5 or -5) and the other words."""
    count, rest, index = 10, [], 0
    while index < len(args):
        arg = args[index]
        if arg == "-n" and index + 1 < len(args):
            if not args[index + 1].isdigit():
                return None, [], f"{args[index + 1]} is not a number of lines."
            count, index = int(args[index + 1]), index + 2
            continue
        match = re.fullmatch(r"-n?(\d+)", arg)
        if match:
            count = int(match.group(1))
        elif arg.startswith("-") and arg != "-":
            return None, [], f"the option {arg} is not supported here."
        else:
            rest.append(arg)
        index += 1
    return count, rest, ""


def _sources(root: Path, cwd: Path, names: List[str], stdin: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """[(label, text)] for the files named (or the piped input when none), and the complaints."""
    if not names:
        return [("", stdin)], []
    got, complaints = [], []
    for name in names:
        text, why = _read(root, cwd, name)
        if text is None:
            complaints.append(why)
        else:
            got.append((name, text))
    return got, complaints


# ---- the built-in commands ------------------------------------------------------------------------------------------------------------------------

def _pwd(root: Path, cwd: Path) -> _Stage:
    relative = cwd.relative_to(root).as_posix()
    return _Stage(0, ("." if relative == "." else "./" + relative) + "\n")          # relative to the project, so the path of the person's account is not sent anywhere


def _echo(args: List[str]) -> _Stage:
    newline = not (args and args[0] == "-n")
    words = args[1:] if not newline else args
    return _Stage(0, " ".join(words) + ("\n" if newline else ""))


def _ls(name: str, args: List[str], root: Path, cwd: Path) -> _Stage:
    letters, targets, complaint = _split_options(args, "alAh1", name)
    if complaint:
        return _Stage(2, "", complaint + "\n")
    show_hidden = name == "dir" or any(letter in letters for letter in "aA")
    out, err, code = [], [], 0
    for target in targets or ["."]:
        try:
            path = commands.check_path(root, cwd, target, reading=False)
        except commands.CommandError as problem:
            err.append(str(problem))
            code = 2
            continue
        if path.is_dir():
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
            names = [entry.name + ("/" if entry.is_dir() else "") for entry in entries if show_hidden or not entry.name.startswith(".")]
            if len(targets) > 1:
                out.append(f"{target}:")
            out.extend(names)
        elif path.exists():
            out.append(target)
        else:
            err.append(f"{target}: no such file or folder")
            code = 2
    return _Stage(code, "".join(line + "\n" for line in out), "".join(line + "\n" for line in err))


def _cat(args: List[str], root: Path, cwd: Path, stdin: str) -> _Stage:
    letters, names, complaint = _split_options(args, "n", "cat")
    if complaint:
        return _Stage(2, "", complaint + "\n")
    got, complaints = _sources(root, cwd, names, stdin)
    text = "".join(body for _, body in got)
    if "n" in letters:
        text = "".join(f"{number:6}\t{line}" for number, line in enumerate(text.splitlines(True), 1))
    return _Stage(1 if complaints else 0, text, "".join(c + "\n" for c in complaints))


def _head_tail(name: str, args: List[str], root: Path, cwd: Path, stdin: str) -> _Stage:
    count, names, complaint = _count_option(args)
    if count is None:
        return _Stage(2, "", f"{name}: {complaint}\n")
    got, complaints = _sources(root, cwd, names, stdin)
    out = []
    for label, text in got:
        lines = text.splitlines(True)
        shown = lines[:count] if name == "head" else (lines[-count:] if count else [])
        if len(got) > 1:
            out.append(f"==> {label} <==\n")
        out.append("".join(shown) + ("" if not shown or shown[-1].endswith("\n") else "\n"))
    return _Stage(1 if complaints else 0, "".join(out), "".join(c + "\n" for c in complaints))


def _wc(args: List[str], root: Path, cwd: Path, stdin: str) -> _Stage:
    letters, names, complaint = _split_options(args, "lwc", "wc")
    if complaint:
        return _Stage(2, "", complaint + "\n")
    got, complaints = _sources(root, cwd, names, stdin)
    out = []
    for label, text in got:
        numbers = {"l": text.count("\n"), "w": len(text.split()), "c": len(text.encode("utf-8"))}
        picked = [numbers[letter] for letter in "lwc" if letter in letters] or [numbers["l"], numbers["w"], numbers["c"]]
        out.append(" ".join(str(n) for n in picked) + (f" {label}" if label else "") + "\n")
    return _Stage(1 if complaints else 0, "".join(out), "".join(c + "\n" for c in complaints))


def _walk(root: Path, start: Path) -> List[Path]:
    """The files under a folder worth searching: no dependency folders, no secrets files, nothing that leads out of the project."""
    found: List[Path] = []
    stack = [start]
    while stack and len(found) < GREP_FILES:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if not commands._inside(root, entry):
                continue
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and entry.name.lower() not in commands.SENSITIVE_PARTS:
                    stack.append(entry)
            elif not commands.SENSITIVE_NAMES.match(entry.name):
                found.append(entry)
    return found


def _grep(args: List[str], root: Path, cwd: Path, stdin: str, deadline: float) -> _Stage:
    letters, rest, complaint = _split_options(args, "nirRlcvFEwHh", "grep")
    if complaint:
        return _Stage(2, "", complaint + "\n")
    if not rest:
        return _Stage(2, "", "grep: needs a pattern.\n")
    pattern, names = rest[0], rest[1:]
    if "F" in letters:
        pattern = re.escape(pattern)
    else:
        if _NESTED_QUANTIFIER.search(pattern):
            return _Stage(2, "", "grep: that pattern is too complex to run safely. Use a simpler one.\n")
        if "E" not in letters:
            pattern = pattern.replace("\\|", "|").replace("\\(", "(").replace("\\)", ")") if "\\|" in pattern else pattern
    if "w" in letters:
        pattern = rf"\b(?:{pattern})\b"
    try:
        regex = re.compile(pattern, re.IGNORECASE if "i" in letters else 0)
    except re.error as problem:
        return _Stage(2, "", f"grep: the pattern is not valid ({problem}).\n")
    recursive = "r" in letters or "R" in letters
    files: List[Tuple[str, Optional[Path]]] = []
    err: List[str] = []
    if not names and not recursive:
        files = [("(standard input)", None)]
    for target in names or (["."] if recursive else []):
        try:
            path = commands.check_path(root, cwd, target, reading=not recursive)
        except commands.CommandError as problem:
            err.append(str(problem))
            continue
        if path.is_dir():
            if not recursive:
                err.append(f"{target}: is a folder (use -r)")
                continue
            files.extend((p.relative_to(root).as_posix(), p) for p in _walk(root, path))
        elif path.exists():
            files.append((target, path))
        else:
            err.append(f"{target}: no such file")
    with_name = (recursive or len(files) > 1) and "h" not in letters
    out, matched = [], False
    for label, path in files:
        if time.monotonic() > deadline:
            err.append("grep: ran out of time; the results so far are shown.")
            break
        if path is None:
            text = stdin
        else:
            try:
                with path.open("rb") as handle:
                    data = handle.read(READ_BYTES)
            except OSError:
                continue
            if _is_binary(data):
                continue
            text = data.decode("utf-8", errors="replace")
        hits = 0
        for number, line in enumerate(text.splitlines(), 1):
            found = bool(regex.search(line[:4000]))
            if found == ("v" in letters):
                continue
            hits += 1
            matched = True
            if "l" not in letters and "c" not in letters:
                out.append((f"{label}:" if with_name else "") + (f"{number}:" if "n" in letters else "") + line + "\n")
        if "c" in letters:
            out.append((f"{label}:" if with_name else "") + f"{hits}\n")
        elif "l" in letters and hits:
            out.append(label + "\n")
    return _Stage(0 if matched else (2 if err and not files else 1), "".join(out), "".join(e + "\n" for e in err))


def _builtin(command: commands.Command, root: Path, cwd: Path, stdin: str, deadline: float) -> _Stage:
    name, args = command.argv[0].lower(), command.argv[1:]
    if name == "pwd":
        return _pwd(root, cwd)
    if name == "echo":
        return _echo(args)
    if name in ("ls", "dir"):
        return _ls(name, args, root, cwd)
    if name in ("cat", "type"):
        return _cat(args, root, cwd, stdin)
    if name in ("head", "tail"):
        return _head_tail(name, args, root, cwd, stdin)
    if name == "wc":
        return _wc(args, root, cwd, stdin)
    if name == "grep":
        return _grep(args, root, cwd, stdin, deadline)
    return _Stage()                                  # cd inside a pipeline changes nothing


# ---- running programs -----------------------------------------------------------------------------------------------------------------------------

def _program_argv(command: commands.Command, env: Dict[str, str]) -> Tuple[List[str], Dict[str, str], str]:
    """(the words to run, the environment, a refusal). The program is named, found on the search path, and git gets the runner's safety settings."""
    program = commands.program_name(command.argv[0])
    args = list(command.argv[1:])
    env = dict(env)
    if program == "git":
        args = list(commands.GIT_SAFETY_ARGS) + args
        env.update(commands.GIT_ENV)
    found = (shutil.which(program, path=env.get("PATH")) or program).lower()
    if os.name == "nt" and found.endswith((".cmd", ".bat")) and any(_CMD_SPECIAL.search(a) for a in args):
        return [], env, f"{program} runs through cmd.exe on Windows, which can act on special characters, and one of the words has one. Leave those characters out."
    return [program, *args], env, ""


def _external(command: commands.Command, cwd: Path, env: Dict[str, str], stdin: Optional[str], deadline: float, limit: int, cancel: Optional[threading.Event], root: Path) -> _Stage:
    argv, env, refusal = _program_argv(command, env)
    if refusal:
        return _Stage(126, "", refusal + "\n")
    remaining = max(1.0, deadline - time.monotonic())
    done = execute.run(argv, cwd, remaining, limit, cancel, env, stdin_data=None if stdin is None else stdin.encode("utf-8"), separate_stderr=True)
    stage = _Stage(done["exit_code"] if done["exit_code"] is not None else 1, done["output_tail"], done.get("stderr_tail", ""))
    stage.truncated, stage.timed_out, stage.cancelled, stage.error = done["truncated"], done["timed_out"], done["cancelled"], done["error"]
    return stage


def _write_redirect(root: Path, cwd: Path, redirect: Tuple[str, str], text: str) -> str:
    """Writes a command's output to a file inside the project. Returns a complaint, or ""."""
    try:
        path = commands.check_path(root, cwd, redirect[1], reading=False)
        if path.is_dir():
            return f"{redirect[1]} is a folder"
        with path.open("a" if redirect[0] == ">>" else "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except commands.CommandError as problem:
        return str(problem)
    except OSError as problem:
        return f"could not write {redirect[1]}: {problem.__class__.__name__}"
    return ""


def _pipeline(pipeline: List[commands.Command], root: Path, cwd: Path, env: Dict[str, str], deadline: float, max_chars: int, cancel: Optional[threading.Event]) -> _Stage:
    """The stages of one pipe, each one's output handed to the next. The result is the last stage's exit code and output, with every stage's error output after it."""
    data: Optional[str] = None
    errors: List[str] = []
    stage = _Stage()
    truncated = False
    for index, command in enumerate(pipeline):
        last = index == len(pipeline) - 1
        if cancel is not None and cancel.is_set():
            stage.cancelled = True
            break
        if command.argv[0].lower() in commands.BUILTINS:
            stage = _builtin(command, root, cwd, data or "", deadline)
        else:
            stage = _external(command, cwd, env, data, deadline, max_chars if last and not command.redirect else PIPE_CHARS, cancel, root)
        truncated = truncated or stage.truncated
        if stage.err:
            errors.append(stage.err)
        if stage.timed_out or stage.cancelled or stage.error:
            break
        data = stage.out
    stage.truncated = truncated
    if pipeline[-1].redirect and not (stage.timed_out or stage.cancelled or stage.error):
        complaint = _write_redirect(root, cwd, pipeline[-1].redirect, stage.out)
        stage.out = ""
        if complaint:
            errors.append(complaint + "\n")
            stage.code = stage.code or 1
    stage.err = "".join(errors)
    return stage


# ---- the whole line -------------------------------------------------------------------------------------------------------------------------------

def run_line(text: str, root: Any, timeout: float = 900, max_chars: int = 6000, cancel: Optional[threading.Event] = None, env: Optional[Dict[str, str]] = None,
             extra_programs: FrozenSet[str] = frozenset()) -> Dict[str, Any]:
    """Classifies the line again and runs it. Returns the same shape as execute.run: {"exit_code", "output_tail", "truncated", "duration", "timed_out", "cancelled", "error"} (+ "class").
    A refused line returns an "error" saying why and has run nothing. `env` is added to the clean environment (the fake AI keys)."""
    started = time.monotonic()
    result: Dict[str, Any] = {"exit_code": None, "output_tail": "", "truncated": False, "duration": 0.0, "timed_out": False, "cancelled": False, "error": "", "class": ""}
    root = Path(root).resolve()
    try:
        plan = commands.classify(text, root, extra_programs)
    except commands.CommandError as problem:
        result["error"] = f"The runner refused it: {problem}"
        return result
    result["class"] = plan.klass
    deadline = started + timeout
    base_env = environment(root, env)
    out = execute._Tail(max_chars)
    cwd = root
    code = 0
    for joiner, pipeline in plan.steps:
        if joiner == "&&" and code != 0:
            continue
        if cancel is not None and cancel.is_set():
            result["cancelled"] = True
            break
        if time.monotonic() >= deadline:
            result["timed_out"] = True
            break
        if len(pipeline) == 1 and pipeline[0].argv[0].lower() == "cd":
            target = commands.check_path(root, cwd, pipeline[0].argv[1], reading=False)
            if target.is_dir():
                cwd, code = target, 0
            else:
                out.add(f"cd: {pipeline[0].argv[1]}: no such folder\n")
                code = 1
            continue
        stage = _pipeline(pipeline, root, cwd, base_env, deadline, max_chars, cancel)
        piece = stage.out + (("" if not stage.out or stage.out.endswith("\n") or not stage.err else "\n") + stage.err)
        out.add(piece)
        result["truncated"] = result["truncated"] or stage.truncated
        code = stage.code
        if stage.timed_out or stage.cancelled or stage.error:
            result["timed_out"], result["cancelled"], result["error"] = stage.timed_out, stage.cancelled, stage.error
            break
    result["output_tail"] = out.done()
    result["truncated"] = result["truncated"] or out.cut
    result["duration"] = round(time.monotonic() - started, 2)
    if not (result["timed_out"] or result["cancelled"] or result["error"]):
        result["exit_code"] = code
    return result
