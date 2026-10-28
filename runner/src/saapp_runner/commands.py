"""The runner's own small command language: what Sonic may ask to run on your computer besides a named check, and the rules that decide it.

THERE IS NO SHELL. A real shell would hand the text to a program that expands `$(...)`, backticks, wildcards, variables and `~`, runs things in the background, and runs anything it finds. This language does
none of that. A line is a few commands joined by `&&` (then, but stop if it failed), `;` (then), or `|` (feed one's output to the next); the last command of a pipe may end with `> file` or `>> file`; `cd dir`
changes the folder for the commands after it. Everything else is refused in words. Each command's program is started directly with its arguments, so nothing in an argument is ever interpreted.

THIS FILE IS PURE (no side effects) AND IT IS THE ONLY PLACE THE RULES LIVE. The runner enforces it on your computer; SAAPP's server loads this same file to classify a line for the card and to refuse early, so
the two can never disagree. The runner never trusts the server's answer: it parses and classifies again before it runs anything.

THE CLASSES, from what a line can do (the highest one in a line wins):
- "read"   looks only. Built into the runner (`pwd ls cat head tail wc grep echo cd`) or a fixed list of read-only git commands and `--version` questions. May run without a click when the person turns that on.
- "run"    runs the project's own tools (tests, a build, a linter, a script). One click on a card, like a named check.
- "change" can change files or the project's state, or uses the network (`git commit`, `npm install`, `> file`). A stronger card, and a window on YOUR computer asks again (the one confirmation the server cannot forge).
- "refused" never runs: shells and `sudo`, network tools, deleting things recursively, force-pushing, anything that leaves the project folder or reads a secret, and any program not on the list.

BEST EFFORT, NOT A SANDBOX. Paths are checked as text (a program can still do things the arguments do not show), and `git` in a repository you did not write can run helpers named in that repository's config; the
runner turns the common ones off. What the person approves is the exact line on the card.
"""
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

MAX_CHARS = 2000
MAX_COMMANDS = 12
READ, RUN, CHANGE, REFUSED = "read", "run", "change", "refused"
_ORDER = {READ: 0, RUN: 1, CHANGE: 2}

# ---- the lists --------------------------------------------------------------------------------------------------------------------------------

BUILTINS = ("cd", "pwd", "echo", "ls", "dir", "cat", "type", "head", "tail", "wc", "grep")

# Programs that are never run, whatever the line says.
NEVER = frozenset({
    "sudo", "su", "doas", "runas", "pkexec",                                                         # becoming someone else
    "bash", "sh", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh", "wsl", "eval", "exec", "source", "xargs", "env", "nohup", "start",       # a shell, or a way to run one
    "curl", "wget", "nc", "ncat", "netcat", "ssh", "scp", "sftp", "ftp", "telnet", "rsync", "invoke-webrequest", "iwr", "irm", "certutil", "bitsadmin",         # the network
    "rm", "rmdir", "del", "erase", "rd", "shred", "format", "mkfs", "dd", "fdisk", "diskpart", "shutdown", "reboot", "halt", "poweroff", "reg", "regedit", "sc", "net", "taskkill", "kill", "killall", "pkill",
    "chmod", "chown", "chgrp", "icacls", "takeown", "mount", "umount", "crontab", "at", "schtasks", "systemctl", "launchctl", "service",
    "docker", "podman", "kubectl", "helm", "terraform", "aws", "az", "gcloud", "gh", "twine",
})

# Programs that run the project's own tools. For some, only certain sub-commands.
RUN_PROGRAMS = frozenset({"python", "python3", "py", "pytest", "node", "tsc", "eslint", "prettier", "ruff", "black", "mypy", "flake8", "isort", "pylint", "vitest", "jest", "mocha", "make", "cargo", "go",
                          "dotnet", "mvn", "gradle", "gradlew", "ctest", "cmake"})
PACKAGE_MANAGERS = frozenset({"npm", "yarn", "pnpm", "pip", "pip3", "uv", "poetry"})
RUN_SUBCOMMANDS = {
    "npm": {"test", "run", "run-script", "ls", "list", "outdated", "audit", "view", "explain", "why", "root", "bin", "prefix", "pack"},
    "yarn": {"test", "run", "ls", "list", "outdated", "why"},
    "pnpm": {"test", "run", "ls", "list", "outdated", "why"},
    "pip": {"list", "show", "freeze", "check"}, "pip3": {"list", "show", "freeze", "check"}, "poetry": {"show", "check"},
}
CHANGE_SUBCOMMANDS = {
    "npm": {"install", "i", "ci", "add", "update", "up", "uninstall", "remove", "rm", "link", "dedupe", "prune", "rebuild"},
    "yarn": {"install", "add", "remove", "upgrade"}, "pnpm": {"install", "i", "add", "remove", "update"},
    "pip": {"install", "uninstall"}, "pip3": {"install", "uninstall"}, "poetry": {"install", "add", "remove", "update", "lock"}, "uv": {"sync", "add", "remove", "lock"},
}
NEVER_SUBCOMMANDS = {"npm": {"publish", "unpublish", "deprecate", "login", "logout", "adduser", "token", "owner", "access", "star", "config", "set"}, "yarn": {"publish", "login", "npm"}, "pnpm": {"publish", "login"},
                     "cargo": {"publish", "install", "login", "yank", "owner"}, "go": {"install", "get", "env"}, "poetry": {"publish", "config"}, "dotnet": {"nuget", "publish", "tool"}}
NPX_PACKAGES = frozenset({"tsc", "eslint", "prettier", "vitest", "jest", "mocha", "ruff", "playwright", "tsx"})
PYTHON_MODULES_RUN = frozenset({"pytest", "unittest", "ruff", "black", "mypy", "flake8", "isort", "pylint", "compileall", "doctest", "timeit", "py_compile", "pyflakes"})
PYTHON_MODULES_CHANGE = frozenset({"pip", "venv", "ensurepip"})
FLAGS_THAT_RUN_CODE = frozenset({"-c", "-e", "--eval", "-p", "--print", "-x"})              # python -c, node -e: code written on the spot, which no card could show in full

GIT_READ = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "blame", "describe", "shortlog", "grep", "cat-file", "rev-list", "name-rev", "diff-tree", "diff-index", "merge-base",
                      "show-ref", "count-objects", "check-ignore", "var", "version", "whatchanged", "reflog"})
GIT_CHANGE = frozenset({"add", "commit", "restore", "switch", "checkout", "merge", "rebase", "cherry-pick", "revert", "stash", "pull", "fetch", "push", "mv", "tag", "branch", "remote", "apply", "am", "submodule", "worktree"})
GIT_NETWORK = frozenset({"pull", "fetch", "push"})
GIT_NEVER_OPTIONS = frozenset({"-c", "-C", "--exec-path", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env", "--no-replace-objects"})
GIT_DESTRUCTIVE = [                                                                                          # (subcommand, a flag or word whose presence makes it refused)
    ("reset", "--hard"), ("reset", "--merge"), ("clean", "-f"), ("clean", "--force"), ("clean", "-fd"), ("clean", "-fx"), ("clean", "-fdx"), ("checkout", "-f"), ("checkout", "--force"), ("switch", "-f"),
    ("switch", "--force"), ("switch", "--discard-changes"), ("push", "--force"), ("push", "-f"), ("push", "--force-with-lease"), ("push", "--delete"), ("push", "-d"), ("push", "--mirror"), ("branch", "-D"),
    ("branch", "--delete"), ("branch", "-d"), ("stash", "drop"), ("stash", "clear"), ("reflog", "expire"), ("reflog", "delete"), ("gc", "--prune"), ("filter-branch", ""), ("update-ref", "-d"),
]
GIT_BRANCH_READ_FLAGS = frozenset({"-a", "-r", "-v", "-vv", "--list", "-l", "--show-current", "--all", "--remotes", "--contains", "--merged", "--no-merged", "--sort", "--format"})
GIT_TAG_READ_FLAGS = frozenset({"-l", "--list", "-n", "--sort", "--contains", "--merged", "--no-merged", "--format"})
GIT_DIFF_NEVER_FLAGS = ("--output", "-o", "--ext-diff", "--textconv", "--open-files-in-pager", "-O")
GIT_ENV = {"GIT_PAGER": "cat", "PAGER": "cat", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_EXTERNAL_DIFF": "", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
GIT_SAFETY_ARGS = ["-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "core.hooksPath=", "-c", "diff.external=", "-c", "protocol.ext.allow=never"]   # inserted by the runner, not shown on the card

SENSITIVE_NAMES = re.compile(r"^(\.env(\..*)?|.*\.(pem|key|p12|pfx|kdbx|keystore|jks|ppk)|id_(rsa|dsa|ecdsa|ed25519)(\..*)?|\.npmrc|\.pypirc|\.netrc|\.git-credentials|credentials(\.json)?|secrets?(\..*)?|"
                             r"\.dockercfg|terraform\.tfstate(\.backup)?|\.htpasswd)$", re.IGNORECASE)
SENSITIVE_PARTS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".azure"})


# ---- what a parsed line looks like ---------------------------------------------------------------------------------------------------------------

class CommandError(Exception):
    """A line (or a command in it) that is refused or not understood, said in words for the person and for Sonic."""


@dataclass
class Command:
    argv: List[str]
    redirect: Optional[Tuple[str, str]] = None          # (">" or ">>", the file)
    klass: str = READ
    notes: List[str] = field(default_factory=list)


@dataclass
class Plan:
    """steps: [(how it joins the one before: "" first, "&&" or ";", [the commands of one pipeline])]."""
    steps: List[Tuple[str, List[Command]]]
    klass: str = READ
    notes: List[str] = field(default_factory=list)

    def commands(self) -> List[Command]:
        return [command for _, pipeline in self.steps for command in pipeline]


# ---- reading the line -----------------------------------------------------------------------------------------------------------------------------

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_UNSUPPORTED = {"$": "variables and $(...) are not expanded here, so they are not allowed", "`": "backticks are not allowed", "(": "parentheses and sub-commands are not allowed", ")": "parentheses and sub-commands are not allowed",
                "{": "braces are not allowed", "}": "braces are not allowed", "<": "input redirection (<) is not allowed", "*": "wildcards are not expanded here: name the files, or put the pattern in quotes",
                "?": "wildcards are not expanded here: name the files, or put the pattern in quotes", "[": "wildcards are not expanded here: name the files, or put the pattern in quotes",
                "~": "~ is not expanded here: use a path inside the project", "!": "history expansion (!) is not allowed"}


def tokenize(text: str) -> List[Tuple[str, str]]:
    """The line as [("word", text) or ("op", operator)]. Raises CommandError for anything this language does not do. Quotes: 'single' is literal; "double" allows \\" and \\\\ and nothing is expanded."""
    if not isinstance(text, str) or not text.strip():
        raise CommandError("There is no command to run.")
    if len(text) > MAX_CHARS:
        raise CommandError(f"The line is longer than {MAX_CHARS} characters.")
    if any(ord(ch) < 32 and ch not in "\t" for ch in text) or "\x7f" in text:
        raise CommandError("A command is one line of plain text: no newlines or control characters (join commands with &&).")
    tokens: List[Tuple[str, str]] = []
    word, started, i, n = [], False, 0, len(text)

    def flush() -> None:
        nonlocal word, started
        if started:
            tokens.append(("word", "".join(word)))
        word, started = [], False

    while i < n:
        ch = text[i]
        if ch in " \t":
            flush()
            i += 1
        elif ch == "'":
            end = text.find("'", i + 1)
            if end < 0:
                raise CommandError("A single quote was opened and never closed.")
            word.append(text[i + 1:end])
            started = True
            i = end + 1
        elif ch == '"':
            i += 1
            while True:
                if i >= n:
                    raise CommandError("A double quote was opened and never closed.")
                c = text[i]
                if c == '"':
                    i += 1
                    break
                if c in "$`":
                    raise CommandError("$ and backticks are not expanded here, so they are not allowed even inside quotes.")
                if c == "\\" and i + 1 < n and text[i + 1] in '"\\':
                    word.append(text[i + 1])
                    i += 2
                    continue
                word.append(c)
                i += 1
            started = True
        elif ch == "&":
            if text[i:i + 2] != "&&":
                raise CommandError("Running something in the background (&) is not allowed. Use && to run commands one after another.")
            flush()
            tokens.append(("op", "&&"))
            i += 2
        elif ch == "|":
            if text[i:i + 2] == "||":
                raise CommandError("|| is not supported. Use && (then, if it worked) or ; (then).")
            flush()
            tokens.append(("op", "|"))
            i += 1
        elif ch == ";":
            flush()
            tokens.append(("op", ";"))
            i += 1
        elif ch == ">":
            flush()
            if text[i:i + 2] == ">>":
                tokens.append(("op", ">>"))
                i += 2
            else:
                tokens.append(("op", ">"))
                i += 1
        elif ch in _UNSUPPORTED and not (ch == "~" and started):
            raise CommandError(_UNSUPPORTED[ch].capitalize() + ".")
        elif ch == "#" and not started:
            raise CommandError("Comments (#) are not allowed.")
        else:
            word.append(ch)
            started = True
            i += 1
    flush()
    return tokens


def parse(text: str) -> Plan:
    """The line as a Plan (not yet classified). Raises CommandError. Grammar: pipeline (( && | ; ) pipeline)*, where a pipeline is command ( | command )* and only its last command may end with > or >> and a file."""
    tokens = tokenize(text)
    steps: List[Tuple[str, List[Command]]] = []
    pipeline: List[Command] = []
    argv: List[str] = []
    redirect: Optional[Tuple[str, str]] = None
    expecting_file: Optional[str] = None
    joiner = ""

    def end_command(last_of_pipeline: bool) -> None:
        nonlocal argv, redirect, expecting_file
        if expecting_file:
            raise CommandError(f"{expecting_file} needs a file name after it.")
        if not argv:
            raise CommandError("A command is missing: there is nothing between two operators, or the line starts or ends with one.")
        if redirect and not last_of_pipeline:
            raise CommandError("Only the last command of a pipe can send its output to a file.")
        if _ENV_ASSIGNMENT.match(argv[0]):
            raise CommandError("Setting a variable in front of a command (NAME=value command) is not allowed.")
        pipeline.append(Command(argv=argv, redirect=redirect))
        argv, redirect = [], None

    for kind, value in tokens:
        if kind == "word":
            if expecting_file:
                redirect, expecting_file = (expecting_file, value), None
            elif redirect:
                raise CommandError("Only one file can follow > or >>, and nothing may come after it but the next && ; or |.")
            else:
                argv.append(value)
        elif value in (">", ">>"):
            if expecting_file or redirect:
                raise CommandError("Only one > or >> is allowed per command.")
            expecting_file = value
        elif value == "|":
            end_command(False)
        else:                                                                                              # && or ;
            end_command(True)
            steps.append((joiner, pipeline))
            pipeline, joiner = [], value
    end_command(True)
    steps.append((joiner, pipeline))
    plan = Plan(steps=steps)
    if len(plan.commands()) > MAX_COMMANDS:
        raise CommandError(f"At most {MAX_COMMANDS} commands in one line.")
    return plan


# ---- paths ------------------------------------------------------------------------------------------------------------------------------------

def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def check_path(root: Path, cwd: Path, text: str, *, reading: bool) -> Path:
    """A path argument, resolved against the folder the command runs in. Raises CommandError unless it stays inside the project, and (for reading) unless it is not a secret."""
    if "://" in text:
        raise CommandError("Web addresses are not allowed in a command.")
    if text.startswith("~") or re.match(r"^\\\\", text):
        raise CommandError(f"{text} is outside the project. Only paths inside the project folder are allowed.")
    path = Path(text)
    target = path if path.is_absolute() else cwd / path
    if not _inside(root, target):
        raise CommandError(f"{text} is outside the project. Only paths inside the project folder are allowed.")
    parts = {part.lower() for part in Path(text).parts}
    if reading and (SENSITIVE_NAMES.match(Path(text).name) or parts & SENSITIVE_PARTS):
        raise CommandError(f"{text} looks like a file that holds secrets or credentials, so it is not read here.")
    return target.resolve()


def _looks_like_path(arg: str) -> bool:
    return ("/" in arg or "\\" in arg or arg in (".", "..") or arg.startswith(("./", ".\\", "../", "..\\"))) and "://" not in arg


def _path_args_ok(root: Path, cwd: Path, argv: List[str], *, reading: bool) -> None:
    for arg in argv[1:]:
        if "://" in arg:
            raise CommandError("Web addresses are not allowed in a command.")
        value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
        if (not arg.startswith("-") or "=" in arg) and _looks_like_path(value):
            check_path(root, cwd, value, reading=reading)


def program_name(argv0: str) -> str:
    """"git", from "git", "Git.EXE" or "git.cmd". A name with a folder in it is refused (programs are found on the search path, never by a path the line gives)."""
    if "/" in argv0 or "\\\\" in argv0 or "\\" in argv0:
        raise CommandError(f"{argv0} names a file by path. Name the program (git, python, npm) and it is found the usual way.")
    return re.sub(r"\.(exe|cmd|bat|com|ps1)$", "", argv0.lower())


# ---- classifying one command ------------------------------------------------------------------------------------------------------------------------

def _git(argv: List[str], extra: FrozenSet[str]) -> Tuple[str, List[str]]:
    rest = argv[1:]
    options = [a for a in rest if a.startswith("-")]
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        if rest[index] in GIT_NEVER_OPTIONS or rest[index].startswith(("--git-dir", "--work-tree", "--exec-path", "--namespace", "-c", "-C")) and rest[index] != "--no-pager":
            raise CommandError(f"git {rest[index]} changes where or how git runs, so it is not allowed. Run git in the project folder as it is.")
        index += 1
    if index >= len(rest):
        raise CommandError("git needs a sub-command, for example: git status.")
    sub, args = rest[index], rest[index + 1:]
    for name, flag in GIT_DESTRUCTIVE:
        if sub == name and (flag == "" or flag in args or any(a.startswith(flag + "=") for a in args) or (flag.startswith("-") and not flag.startswith("--") and len(flag) == 2 and any(a.startswith("-") and not a.startswith("--") and flag[1] in a for a in args))):
            raise CommandError(f"git {sub} {flag} throws work away or rewrites history, so it is never run here. Do it yourself if you really mean it.".replace("  ", " "))
    if sub in ("clone", "init", "config", "daemon", "gc", "prune", "fsck", "bisect", "rm", "bundle", "archive", "fast-import", "send-email", "request-pull", "credential", "instaweb", "help", "difftool", "mergetool",
               "svn", "p4", "filter-repo", "replace", "notes", "maintenance", "sparse-checkout", "lfs"):
        raise CommandError(f"git {sub} is not allowed here.")
    if sub == "diff" or sub == "show" or sub == "log":
        for arg in args:
            if arg in GIT_DIFF_NEVER_FLAGS or arg.startswith("--output"):
                raise CommandError(f"git {sub} {arg} can run another program or write a file, so it is not allowed.")
    if sub in GIT_READ:
        if sub == "reflog" and args and args[0] != "show":
            raise CommandError("Only git reflog (or git reflog show) is allowed.")
        return READ, []
    if sub == "branch":
        listing = all(a in GIT_BRANCH_READ_FLAGS or a.startswith(("--sort=", "--format=", "--contains=", "--merged=", "--no-merged=")) for a in args if a.startswith("-")) and not [a for a in args if not a.startswith("-")][:1] \
            or any(a in ("--list", "-l", "--show-current") for a in args)
        return (READ, []) if listing else (CHANGE, ["changes your branches"])
    if sub == "tag":
        return (READ, []) if not [a for a in args if not a.startswith("-")] and all(a in GIT_TAG_READ_FLAGS or a.startswith(("--sort=", "--format=", "--contains=", "--merged=", "--no-merged=")) for a in args) \
            else (CHANGE, ["creates or changes a tag"])
    if sub == "remote":
        return (READ, []) if not args or args == ["-v"] or args[:1] == ["get-url"] else (CHANGE, ["changes where the project sends its code, or contacts the remote"])
    if sub == "stash":
        return (READ, []) if args[:1] in (["list"], ["show"]) else (CHANGE, ["moves your uncommitted work aside or back"])
    if sub in GIT_CHANGE:
        notes = {"push": ["sends your commits off this computer (git push)"], "pull": ["fetches from the network and changes your files"], "fetch": ["fetches from the network"],
                 "commit": ["makes a commit"], "add": ["stages files"], "checkout": ["changes your files and branch"], "switch": ["changes your branch"], "merge": ["merges into your files"],
                 "rebase": ["rewrites commits"], "restore": ["changes your files"]}.get(sub, [f"changes the project (git {sub})"])
        return CHANGE, notes + (["uses the network"] if sub in GIT_NETWORK and "network" not in " ".join(notes) else [])
    raise CommandError(f"git {sub} is not on the list of git commands this runner knows.")


def classify_command(command: Command, root: Path, cwd: Path, extra_programs: FrozenSet[str] = frozenset()) -> Tuple[str, List[str]]:
    """(class, why) for one command. Raises CommandError (with the reason) for a refused one. `cwd` is the folder it will run in (inside the project)."""
    argv = command.argv
    name = argv[0].lower()
    notes: List[str] = []
    if name in BUILTINS:
        if name in ("cat", "type", "head", "tail", "wc", "ls", "dir", "grep"):
            _builtin_paths(command, root, cwd)
        elif name == "cd":
            if len(argv) != 2:
                raise CommandError("cd takes one folder inside the project.")
            check_path(root, cwd, argv[1], reading=False)
        klass = READ
    else:
        program = program_name(argv[0])
        if program in NEVER:
            raise CommandError(f"{program} is never run here (it can become another user, start a shell, use the network, or delete things).")
        reading_only = False
        if len(argv) == 2 and argv[1] in ("--version", "-V", "-version", "version") and (program in RUN_PROGRAMS or program in PACKAGE_MANAGERS or program == "git"):
            klass, notes, reading_only = READ, [], True
        elif program == "git":
            klass, notes = _git(argv, extra_programs)
        elif program in PACKAGE_MANAGERS:
            sub = next((a for a in argv[1:] if not a.startswith("-")), "")
            if sub in NEVER_SUBCOMMANDS.get(program, ()):
                raise CommandError(f"{program} {sub} is not allowed here (it publishes, signs in, or changes settings).")
            if sub in CHANGE_SUBCOMMANDS.get(program, ()):
                klass, notes = CHANGE, [f"changes the project's files and uses the network ({program} {sub})"]
            elif sub in RUN_SUBCOMMANDS.get(program, ()):
                klass = RUN
            else:
                raise CommandError(f"{program} {sub} is not on the list of {program} commands this runner knows.".replace("  ", " "))
        elif program == "npx":
            package = next((a for a in argv[1:] if not a.startswith("-")), "")
            if package not in NPX_PACKAGES:
                raise CommandError(f"npx {package} would download and run a package, so only these are allowed: {', '.join(sorted(NPX_PACKAGES))}.")
            klass = RUN
        elif program in ("python", "python3", "py"):
            klass, notes = _python(argv)
        elif program in RUN_PROGRAMS:
            sub = next((a for a in argv[1:] if not a.startswith("-")), "")
            if sub in NEVER_SUBCOMMANDS.get(program, ()):
                raise CommandError(f"{program} {sub} is not allowed here.")
            if any(a in FLAGS_THAT_RUN_CODE for a in argv[1:]) and program in ("node",):
                raise CommandError(f"{program} with code typed on the line is not allowed: put the code in a file and run the file.")
            klass, notes = (CHANGE, [f"rewrites files ({program} {sub})".replace("  ", " ")]) if _writes_files(program, argv) else (RUN, [])
        elif program in extra_programs:
            klass, notes = CHANGE, [f"{program} is a program you allowed in your own config"]
        else:
            raise CommandError(f"{program} is not on the list of programs this runner will run. (You can allow one in your own config file: [commands] allow = [\"{program}\"].)")
        _path_args_ok(root, cwd, argv, reading=reading_only or klass == READ)
    if command.redirect:
        operator, target = command.redirect
        destination = check_path(root, cwd, target, reading=False)
        if ".git" in {part.lower() for part in destination.relative_to(root.resolve()).parts}:
            raise CommandError("Writing inside .git is not allowed.")
        if SENSITIVE_NAMES.match(destination.name) or {p.lower() for p in destination.relative_to(root.resolve()).parts} & SENSITIVE_PARTS:
            raise CommandError(f"{target} looks like a file that holds secrets, so nothing is written to it here.")
        klass, notes = CHANGE, notes + [f"writes to {target}" + (" (adds to it)" if operator == ">>" else " (replaces it)")]
    return klass, notes


def _writes_files(program: str, argv: List[str]) -> bool:
    """Whether a tool in RUN_PROGRAMS rewrites files as it runs (a formatter or a fixer), so it is a change and not just a run."""
    args = argv[1:]
    sub = next((a for a in args if not a.startswith("-")), "")
    if program in ("black", "isort"):
        return not any(a in ("--check", "--diff", "--check-only", "-c") for a in args)
    if program == "ruff":
        return "--fix" in args or "--unsafe-fixes" in args or (sub == "format" and not any(a in ("--check", "--diff") for a in args))
    if program == "eslint":
        return "--fix" in args
    if program == "prettier":
        return any(a in ("--write", "-w") for a in args)
    if program == "cargo":
        return (sub == "fmt" and "--check" not in args) or sub in ("fix", "add", "remove", "update", "new", "init")
    if program == "go":
        return sub in ("fmt", "mod", "generate", "work")
    if program == "dotnet":
        return sub in ("format", "restore", "new", "add", "remove")
    return False


def _python(argv: List[str]) -> Tuple[str, List[str]]:
    args = argv[1:]
    if not args:
        raise CommandError("python with no script would wait for typed input. Give it a script or -m module.")
    if any(a in FLAGS_THAT_RUN_CODE for a in args):
        raise CommandError("python -c (code typed on the line) is not allowed: put the code in a file and run the file, so it can be read first.")
    index = 0
    while index < len(args) and args[index] in ("-u", "-B", "-O", "-OO", "-s", "-S", "-E", "-I", "-q", "-X", "-W") and True:
        index += 2 if args[index] in ("-X", "-W") else 1                         # options that do not change what runs
    if index >= len(args):
        raise CommandError("python with no script would wait for typed input. Give it a script or -m module.")
    args = args[index:]
    if args[0] == "-m":
        module = args[1].split(".")[0] if len(args) > 1 else ""
        if module in PYTHON_MODULES_CHANGE:
            sub = next((a for a in args[2:] if not a.startswith("-")), "")
            if module == "pip" and sub in ("list", "show", "freeze", "check"):
                return RUN, []
            return CHANGE, [f"changes the python environment (python -m {module} {sub})".replace("  ", " ")]
        if module in PYTHON_MODULES_RUN:
            return RUN, []
        raise CommandError(f"python -m {module} is not on the list of modules this runner knows. Allowed: {', '.join(sorted(PYTHON_MODULES_RUN | PYTHON_MODULES_CHANGE))}.")
    if args[0].startswith("-"):
        raise CommandError(f"python {args[0]} is not allowed here.")
    return RUN, []


def _builtin_paths(command: Command, root: Path, cwd: Path) -> None:
    name, args = command.argv[0].lower(), command.argv[1:]
    values = [a for a in args if not a.startswith("-")]
    if name == "grep":
        values = values[1:]                      # the first word that is not an option is the pattern, not a path
    if name in ("head", "tail"):
        values = [v for v in values if not v.isdigit()]
    for value in values:
        check_path(root, cwd, value, reading=True)


# ---- classifying a line ---------------------------------------------------------------------------------------------------------------------------

def classify(text: str, root: Path, extra_programs: FrozenSet[str] = frozenset()) -> Plan:
    """Parses and classifies a line for a project folder. Returns the Plan with each command's class and why, and the line's class (the highest). Raises CommandError, saying why, for a line that is refused.
    `cd` is followed so that later commands are checked in the folder they will really run in."""
    root = Path(root).resolve()
    plan = parse(text)
    cwd = root
    klass = READ
    notes: List[str] = []
    for _, pipeline in plan.steps:
        for command in pipeline:
            if command.argv[0].lower() == "cd":
                classify_command(command, root, cwd, extra_programs)             # checks it (one folder, inside the project)
                cwd = check_path(root, cwd, command.argv[1], reading=False)
                command.klass, command.notes = READ, []
                continue
            command.klass, command.notes = classify_command(command, root, cwd, extra_programs)
            if _ORDER[command.klass] > _ORDER[klass]:
                klass = command.klass
            notes.extend(n for n in command.notes if n not in notes)
    plan.klass, plan.notes = klass, notes
    return plan


def render(plan: Plan) -> str:
    """The line as the card and the log show it: each command with its words quoted where needed, joined the way they were. For reading; never run through a shell."""
    def word(w: str) -> str:
        return shlex.quote(w) if re.search(r"[\s'\"&|;<>$`*?\[\](){}~!#]", w) or not w else w

    out: List[str] = []
    for joiner, pipeline in plan.steps:
        if joiner:
            out.append(f" {joiner} ")
        out.append(" | ".join(" ".join(word(w) for w in c.argv) + (f" {c.redirect[0]} {word(c.redirect[1])}" if c.redirect else "") for c in pipeline))
    return "".join(out)


def summary(plan: Plan) -> Dict[str, object]:
    """What SAAPP shows about a line: its class, why, and how many commands. JSON-able."""
    return {"class": plan.klass, "notes": plan.notes, "commands": len(plan.commands())}
