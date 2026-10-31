"""A commit, and optionally a push, as ONE command line for the person's own computer. Pure, no model call, nothing logged.

WHY. The commit tool (backend/services/commit_flow.py, git_commit.py) commits through GitHub's API because it was built when neither the server nor the browser could run git. A runner on the person's
computer (backend/utils/runner_jobs.py, runner/) can, and real git is better: their identity and hooks, their own credentials for the push, files the API refuses (dependency manifests, lockfiles, CI
workflows, Dockerfile), binary files, and no round trip through GitHub before the commit exists in their repository. The line is shown word for word on a runner card; a line that changes things needs the
stronger confirmation and their computer asks again; `git push --force` and `git reset --hard` are refused by the runner whatever is typed.

WHAT IS BUILT, from the pieces Sonic supplies: `git switch -c <branch>` when a new branch is asked for, the staging (named paths, or only files git already tracks, or everything when that is asked for
outright), `git commit` with the message and a Co-authored-by trailer for Sonic, and `git push -u origin <branch or HEAD>` when a push is asked for. Joined with && so the first failure stops the rest.

SAFETY. Paths are checked (no flags, no parent folders, no wildcards, no quotes, no secret-looking files). The branch name is the same check GitHub commits use. `$`, backticks and backslashes are dropped
from the message because the runner's command language refuses them even inside quotes. With no paths and no "everything", only already-tracked files are staged (`git add -u`), so a new secret file is
never picked up by accident.
"""
import re
from typing import Any, Iterable, List, Optional

MAX_SUBJECT_CHARS = 100
MAX_BODY_CHARS = 400
MAX_PATHS = 40

_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.@+][A-Za-z0-9_./@+-]*$")
_SECRET_PATH = re.compile(r"(^|/)(\.env(\..*)?|id_rsa.*|id_ed25519.*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|credentials[^/]*|secrets?\.[^/]*|\.npmrc|\.pypirc)$", re.IGNORECASE)
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")          # .env.example is a template, not a secret (the GitHub commit route allows these too)
_BAD_MESSAGE_CHARS = re.compile(r"[`$\\\x00-\x08\x0b-\x1f]")


class GitTerminalError(ValueError):
    """The request cannot be turned into a safe commit line; the message says why, in words the model can act on."""


def clean_message(message: Any) -> str:
    """A subject line, and an optional body, with what the runner's command language refuses taken out and double quotes made single. Raises GitTerminalError when nothing is left. Pure."""
    text = str(message or "").replace("\r\n", "\n").strip()
    parts = [p.strip() for p in re.split(r"\n\s*\n", text, maxsplit=1)] if "\n" in text else [text]
    subject = " ".join(parts[0].split())
    body = " ".join(parts[1].split()) if len(parts) > 1 else ""

    def tidy(piece: str) -> str:
        return _BAD_MESSAGE_CHARS.sub("", piece).replace('"', "'")

    subject, body = tidy(subject)[:MAX_SUBJECT_CHARS].strip(), tidy(body)[:MAX_BODY_CHARS].strip()
    if len(subject) < 4:
        raise GitTerminalError("Give the commit a message that says what the change does, for example \"fix: stop the plan loop after three empty turns\".")
    return subject if not body else f"{subject}\n\n{body}"


def clean_paths(paths: Any) -> List[str]:
    """The paths to stage, each checked, or [] when none were given. Pure."""
    if paths in (None, "", []):
        return []
    if isinstance(paths, str):
        paths = [paths]
    cleaned: List[str] = []
    for raw in list(paths)[:MAX_PATHS + 1]:
        path = str(raw or "").strip().replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if not path or not _SAFE_PATH.match(path) or ".." in path.split("/") or path.endswith("/"):
            raise GitTerminalError(f"{path[:60]!r} is not a plain path inside the project (no flags, wildcards, quotes or parent folders). List the files one by one.")
        if _SECRET_PATH.search(path) and not path.lower().endswith(_TEMPLATE_SUFFIXES):
            raise GitTerminalError(f"{path} looks like a file that holds a secret, so it is never committed from here. Leave it out.")
        cleaned.append(path)
    if len(cleaned) > MAX_PATHS:
        raise GitTerminalError(f"That is more than {MAX_PATHS} files. Commit a smaller set, or use everything=true after looking at git status --short.")
    return cleaned


def build_commit_line(message: Any, *, new_branch: Any = None, push: bool = False, paths: Any = None, everything: bool = False, co_author: Optional[str] = None) -> str:
    """The one command line for the runner. Raises GitTerminalError (with what to change) when it cannot be built safely. Pure."""
    from backend.services import git_commit as gc

    text = clean_message(message)
    subject, _, body = text.partition("\n\n")
    staged = clean_paths(paths)
    steps: List[str] = []
    branch = None
    if new_branch:
        try:
            branch = gc.clean_branch(new_branch)
        except gc.GitCommitError as problem:
            raise GitTerminalError(str(problem)) from None
        steps.append(f"git switch -c {branch}")
    steps.append(f"git add -- {' '.join(staged)}" if staged else ("git add -A" if everything else "git add -u"))
    commit = f'git commit -m "{subject}"' + (f' -m "{body}"' if body else "") + f' -m "Co-authored-by: {co_author or gc.CO_AUTHOR}"'
    steps.append(commit)
    if push:
        steps.append(f"git push -u origin {branch or 'HEAD'}")
    return " && ".join(steps)


def explain(line: str) -> str:
    """One plain sentence for the model about what the line will do. Pure."""
    parts = []
    if "git switch -c" in line:
        parts.append("creates the branch")
    parts.append("stages the files" if "git add" in line else "")
    parts.append("commits")
    if "git push" in line:
        parts.append("pushes")
    return ", ".join(p for p in parts if p)
