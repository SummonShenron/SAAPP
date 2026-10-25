"""Commit and push to GitHub through its API, the way a person would after a piece of work: a short summary, the changed files, onto a branch, optionally a new branch and a pull request.

WHY THE API. Sonic runs on a server and the person's folder lives in their browser, neither of which can run git. GitHub's Git Data API does what `git commit` plus `git push` do: build a tree
from the changed files, make a commit whose parent is the branch's current head, and move the branch to it (or create the branch). It is a fast-forward only: if the branch moved since the card
was shown, GitHub refuses and nothing is overwritten.

WHAT THE PERSON APPROVES (backend/services/agent_workflow.py shows it as a card first; nothing here runs without that approval):
  the repo, the branch (the one their folder is on, or a NEW branch to create first), the files with +/- counts, the message, and optionally the pull request to open.

WHAT IS COMMITTED. Only files Sonic changed and the person applied (backend/utils/applied_changes.py), as their folder holds them now: the card carries a short fingerprint of each file's text and the
commit is refused if a file changed between the card and the approval. Text files only.

RULES (confirmed 2026-10-11, relaxed 2026-10-25 for committing to the person's current branch):
- THEIR OWN TOKEN. Never the server's shared token, and only after GitHub says that token can push to the repo.
- NEVER force, never delete a file or a branch, at most MAX_FILES files and MAX_TOTAL_BYTES of text, nothing binary.
- REFUSED PATHS: CI workflows (.github/), secret-looking files, dependency manifests and lockfiles, Dockerfile and render.yaml. Sonic's own notes file is left out.
- THE DEFAULT BRANCH is allowed when it is the branch they are on, with a plain warning on the card.
- LINE ENDINGS follow the file already in the repo (a Windows folder does not turn a whole file into a diff).
- THE MESSAGE is short and plain, and ends with a Co-authored-by trailer for Sonic.
- A FILE EDITED ELSEWHERE: the commit holds the folder's full text of each file. If the repo's copy differs from what the person approved here by more than the approved change, the card says so
  (someone else's work on GitHub would be replaced).

No file text, token or message is logged: only counts.
"""
import base64
import difflib
import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from backend.services import local_workspace

logger = logging.getLogger("SASS Logger")

API = "https://api.github.com"
CO_AUTHOR = os.getenv("SONIC_COMMIT_COAUTHOR", "Sonic Assistant <sonic-assistant@users.noreply.github.com>")
MAX_FILES = 40
MAX_FILE_BYTES = 500_000          # the same ceiling the folder sync uses, so any file Sonic can see can be committed
MAX_TOTAL_BYTES = 4_000_000
MAX_MESSAGE_CHARS = 400
MAX_SUBJECT_CHARS = 100
MAX_BRANCH_CHARS = 100
NOTES_PATH = ".sonic/notes.md"

REFUSED_NAMES = frozenset({
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile", "pipfile.lock", "requirements.txt", "pyproject.toml",
    "dockerfile", "render.yaml", "go.mod", "go.sum", "cargo.toml", "cargo.lock", "gemfile", "gemfile.lock",
})
REFUSED_PREFIXES = (".github/",)
_BINARY_SUFFIXES = (".docx", ".xlsx", ".pptx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".exe", ".dll")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class GitCommitError(Exception):
    """Something that stops the commit, in words for the person. Holds no file text and no token."""


class NotTextOnGitHub(GitCommitError):
    """The repository's copy of a file is not UTF-8 text (UTF-16, binary), so Sonic will not overwrite it."""


class GitHubApi:
    """A thin GitHub client for one token. Tests replace it with a fake that has the same `request`."""

    def __init__(self, token: str, session: Any = None, timeout: float = 20.0):
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        self._session = session or requests
        self._timeout = timeout

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        try:
            response = self._session.request(method, f"{API}{path}", headers=self._headers, json=body, timeout=self._timeout)
        except requests.RequestException:
            raise GitCommitError("GitHub could not be reached just now, so nothing was committed.") from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------------------------

def refusal_for_path(path: str) -> Optional[str]:
    """Why this path is never committed by Sonic, or None. Pure."""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    if path == NOTES_PATH:
        return "Sonic's own notes are not committed"
    if any(lower.startswith(prefix) for prefix in REFUSED_PREFIXES):
        return "CI workflow files are never changed by Sonic"
    if local_workspace.path_rejection(path):
        return "it looks like a secret or credentials file"
    if name in REFUSED_NAMES:
        return "dependency manifests, lockfiles, Dockerfile and render.yaml are changed by hand"
    if lower.endswith(_BINARY_SUFFIXES):
        return "only text files are committed"
    return None


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def git_blob_sha(raw: bytes) -> str:
    """The id git gives a file's bytes (sha1 of "blob <size>\\0" and the bytes), so a local file can be compared with GitHub's copy without downloading it. Pure."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def match_line_endings(new_text: str, base_text: Optional[str]) -> str:
    """The new text with the line endings the repo's copy uses (CRLF only if that copy has CRLF); LF for a new file. Pure."""
    plain = new_text.replace("\r\n", "\n")
    return plain.replace("\n", "\r\n") if base_text is not None and "\r\n" in base_text else plain


def count_changes(base_text: str, new_text: str) -> Tuple[int, int]:
    """(lines added, lines removed) going from base_text to new_text. Pure."""
    adds = dels = 0
    for line in difflib.unified_diff(base_text.splitlines(), new_text.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            adds += 1
        elif line.startswith("-") and not line.startswith("---"):
            dels += 1
    return adds, dels


def clean_message(raw: Any) -> str:
    """The commit message Sonic proposed, made plain: no control characters, no trailers of its own, short. Raises GitCommitError when nothing usable is left."""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    lines = [ln.rstrip() for ln in text.split("\n") if not re.match(r"^\s*co-authored-by\s*:", ln, re.IGNORECASE) and not re.match(r"^\s*signed-off-by\s*:", ln, re.IGNORECASE)]
    body = "\n".join(lines).strip()
    body = re.sub(r"\n{3,}", "\n\n", body)
    if len(body) < 8:
        raise GitCommitError("Give a short commit message: one or two plain sentences saying what the change does.")
    subject = body.split("\n", 1)[0]
    if len(subject) > MAX_SUBJECT_CHARS:
        # A long first line is fitted, not refused: a short subject cut at a word, with the whole original line kept as the first line of the body.
        cut = subject[:MAX_SUBJECT_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") or subject[:MAX_SUBJECT_CHARS - 1]
        body = f"{cut}…\n\n{body}"
    if len(body) > MAX_MESSAGE_CHARS:
        raise GitCommitError(f"Keep the message to {MAX_MESSAGE_CHARS} characters: one or two sentences.")
    return body


def with_trailer(message: str) -> str:
    return f"{message}\n\nCo-authored-by: {CO_AUTHOR}"


def clean_branch(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_BRANCH_CHARS or not _BRANCH_RE.match(text) or ".." in text or text.endswith(("/", ".", ".lock")) or "//" in text or "@{" in text:
        raise GitCommitError(f"{text[:40]!r} is not a branch name GitHub accepts. Use letters, numbers, dots, dashes and slashes, for example sonic/fix-clamp.")
    return text


# ---------------------------------------------------------------------------------------------------------------
# GitHub reads
# ---------------------------------------------------------------------------------------------------------------

def _explain(status: int, data: Any, action: str) -> GitCommitError:
    message = str((data or {}).get("message") or "") if isinstance(data, dict) else ""
    lowered = message.lower()
    if status in (401,):
        return GitCommitError("GitHub did not accept your token. Check it under Integrations.")
    if status == 403 or "resource not accessible" in lowered:
        return GitCommitError(f"GitHub refused: your token cannot {action} on this repository (it needs write access).")
    if status == 404:
        return GitCommitError(f"GitHub could not find that repository or branch to {action}, or your token cannot see it.")
    if status == 409:
        return GitCommitError(f"GitHub reported a conflict while trying to {action}.")
    if status == 422 and ("fast forward" in lowered or "fast-forward" in lowered or "not a fast" in lowered):
        return GitCommitError("The branch has moved on GitHub since the card was shown, so nothing was changed. Pull, then ask again.")
    if status == 422 and "already exists" in lowered:
        return GitCommitError("A branch with that name already exists on GitHub, so nothing was changed. Pick another name.")
    if "protected branch" in lowered or "required status" in lowered or "protected" in lowered:
        return GitCommitError("That branch is protected on GitHub, so a direct commit was refused. Use a new branch and a pull request.")
    return GitCommitError(f"GitHub could not {action} (error {status}).")


def repo_info(api: Any, repo: str) -> Dict[str, Any]:
    """{"default_branch", "private", "can_push"} for the repo, or GitCommitError."""
    status, data = api.request("GET", f"/repos/{repo}")
    if status != 200 or not isinstance(data, dict):
        raise _explain(status, data, "open the repository")
    return {"default_branch": data.get("default_branch") or "main", "private": bool(data.get("private")), "can_push": bool((data.get("permissions") or {}).get("push"))}


def branch_head(api: Any, repo: str, branch: str) -> Optional[Dict[str, str]]:
    """{"commit", "tree"} for the branch's head, or None when the branch is not on GitHub."""
    status, data = api.request("GET", f"/repos/{repo}/git/ref/heads/{branch}")
    if status == 404:
        return None
    if status != 200 or not isinstance(data, dict):
        raise _explain(status, data, "read the branch")
    commit_sha = (data.get("object") or {}).get("sha")
    status, commit = api.request("GET", f"/repos/{repo}/git/commits/{commit_sha}")
    if status != 200 or not isinstance(commit, dict):
        raise _explain(status, commit, "read the branch")
    return {"commit": commit_sha, "tree": (commit.get("tree") or {}).get("sha")}


def tree_entry(api: Any, repo: str, root_tree: str, path: str, cache: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """{"mode", "sha"} of the file at `path` in the tree, or None if it is not there. Walks one directory at a time, so a big repository is no problem."""
    tree_sha = root_tree
    parts = path.split("/")
    for index, part in enumerate(parts):
        if tree_sha not in cache:
            status, data = api.request("GET", f"/repos/{repo}/git/trees/{tree_sha}")
            if status != 200 or not isinstance(data, dict):
                raise _explain(status, data, "read the repository's files")
            cache[tree_sha] = {entry["path"]: entry for entry in data.get("tree", [])}
        entry = cache[tree_sha].get(part)
        if entry is None:
            return None
        if index == len(parts) - 1:
            return {"mode": entry.get("mode", "100644"), "sha": entry.get("sha"), "type": entry.get("type")} if entry.get("type") == "blob" else None
        if entry.get("type") != "tree":
            return None
        tree_sha = entry["sha"]
    return None


def blob_text(api: Any, repo: str, sha: str, path: str) -> str:
    status, data = api.request("GET", f"/repos/{repo}/git/blobs/{sha}")
    if status != 200 or not isinstance(data, dict):
        raise _explain(status, data, "read a file")
    try:
        return base64.b64decode(data.get("content", "")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise NotTextOnGitHub(f"{path} is not a text file on GitHub, so Sonic will not overwrite it.") from None


def _glob_regex(pattern: str) -> "re.Pattern[str]":
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[" and "]" in pattern[i + 1:]:
            end = pattern.index("]", i + 1)
            body = pattern[i + 1:end].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end + 1
        elif ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def gitignore_matcher(text: str):
    """A function path -> True when a .gitignore (its text, from the repository root) ignores that path, the way git does for a file that is not tracked: patterns with or without a slash, a leading
    slash, trailing-slash directory patterns, `*`, `**`, `?`, `[...]`, `!` to bring a file back, last matching line wins, and everything under an ignored directory ignored. Pure. Nested .gitignore
    files are not read."""
    rules = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        directory_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if not pattern:
            continue
        anchored = "/" in pattern
        pattern = pattern.lstrip("/")
        rules.append((negated, directory_only, anchored, _glob_regex(pattern)))

    def verdict(candidate: str, is_dir: bool) -> bool:
        ignored = False
        for negated, directory_only, anchored, regex in rules:
            if directory_only and not is_dir:
                continue
            target = candidate if anchored else candidate.rsplit("/", 1)[-1]
            if regex.match(target):
                ignored = not negated
        return ignored

    def is_ignored(path: str) -> bool:
        parts = path.split("/")
        for count in range(1, len(parts)):
            if verdict("/".join(parts[:count]), True):
                return True
        return verdict(path, False)

    return is_ignored


# Folders that tools create next to a project and that git never shows because they hold their own .gitignore ("*"); the browser sync does not upload .gitignore files, so those are not seen here.
DEFAULT_IGNORES = "\n".join([
    ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", ".hypothesis/", ".tox/", ".ipynb_checkpoints/", "__pycache__/", "*.pyc", "node_modules/", ".venv/", "venv/", ".DS_Store", "Thumbs.db",
])


def differing_from_repo(api: Any, repo: str, branch: str, files: Dict[str, str], gitignore: Optional[str] = None) -> List[str]:
    """The paths among `files` (the folder's text) that are new or different compared with the branch on GitHub, sorted: the "working changes". One call lists the whole branch with each file's git
    id and the folder's files are hashed here, so nothing is downloaded. A file counts as unchanged when it matches GitHub's copy as it is or with either line ending (a Windows folder is not a
    change to every file). A file that is not on GitHub and that the repository's .gitignore (`gitignore`, its text) ignores is not a change, as with `git status`; a tracked file always is one if it
    differs; the rules are a short built-in list of tool folders, the branch's root .gitignore, and `gitignore` (the folder's copy, if any). Nested .gitignore files are not read. Blocking. Raises GitCommitError when the branch is missing or the repository is too big to list in one call."""
    head = branch_head(api, repo, branch)
    if head is None:
        raise GitCommitError(f"The branch {branch!r} is not on GitHub yet, so there is nothing to compare your folder with. Give a `new_branch` name and Sonic will compare with the default branch.")
    status, data = api.request("GET", f"/repos/{repo}/git/trees/{head['tree']}?recursive=1")
    if status != 200 or not isinstance(data, dict):
        raise _explain(status, data, "list the repository's files")
    if data.get("truncated"):
        raise GitCommitError("This repository is too big for Sonic to compare with your folder in one go. Name the files to commit with `paths`.")
    repo_ids = {entry["path"]: entry.get("sha") for entry in data.get("tree", []) if entry.get("type") == "blob"}
    # The browser sync does not upload .gitignore files, so the repository's own root .gitignore (on the branch) is the rule book, then the folder's copy if it has one.
    repo_rules = ""
    if repo_ids.get(".gitignore"):
        try:
            repo_rules = blob_text(api, repo, repo_ids[".gitignore"], ".gitignore")
        except GitCommitError:
            repo_rules = ""
    ignored = gitignore_matcher("\n".join([DEFAULT_IGNORES, repo_rules, gitignore or ""]))
    changed = []
    for path in sorted(files):
        if path not in repo_ids and ignored(path):
            continue
        text = files[path]
        plain = text.replace("\r\n", "\n")
        same = {git_blob_sha(text.encode("utf-8")), git_blob_sha(plain.encode("utf-8")), git_blob_sha(plain.replace("\n", "\r\n").encode("utf-8"))}
        if repo_ids.get(path) not in same:
            changed.append(path)
    return changed


# ---------------------------------------------------------------------------------------------------------------
# Planning and doing
# ---------------------------------------------------------------------------------------------------------------

def check_files(files: Dict[str, str]) -> None:
    """Raises GitCommitError, in words, when these files cannot be committed. Pure."""
    if not files:
        raise GitCommitError("Nothing applied through Sonic is waiting to be committed.")
    if len(files) > MAX_FILES:
        raise GitCommitError(f"That is {len(files)} files; Sonic commits at most {MAX_FILES} at a time. Name the ones to include with `paths`.")
    total = 0
    for path, text in files.items():
        reason = refusal_for_path(path)
        if reason:
            raise GitCommitError(f"{path} cannot be committed by Sonic: {reason}.")
        size = len(text.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise GitCommitError(f"{path} is too large for Sonic to commit ({size // 1000} KB; the limit is {MAX_FILE_BYTES // 1000} KB).")
        total += size
    if total > MAX_TOTAL_BYTES:
        raise GitCommitError(f"The files add up to {total // 1000} KB; Sonic commits at most {MAX_TOTAL_BYTES // 1000} KB at a time.")


def build_plan(api: Any, repo: str, branch: str, files: Dict[str, str], expected: Optional[Dict[str, Tuple[int, int]]] = None, skip_unreadable: bool = False) -> Dict[str, Any]:
    """What committing `files` onto `branch` would do: the branch's head, and for each file whether it is new or modified, its line counts against the repo's copy, the mode to keep, and whether
    it differs from the change that was approved by more than that change (`expected`: {path: (added, removed)}). Files identical to the repo's copy are left out. With `skip_unreadable`, a file whose
    copy on GitHub is not UTF-8 text is left out and named in plan["skipped"] instead of stopping the whole commit. Blocking."""
    check_files(files)
    head = branch_head(api, repo, branch)
    if head is None:
        raise GitCommitError(f"The branch {branch!r} is not on GitHub yet. Give a `new_branch` name to create it, and Sonic will branch from the default branch.")
    cache: Dict[str, Any] = {}
    planned: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for path in sorted(files):
        entry = tree_entry(api, repo, head["tree"], path, cache)
        try:
            base_text = blob_text(api, repo, entry["sha"], path) if entry else None
        except NotTextOnGitHub:
            if not skip_unreadable:
                raise
            skipped.append(path)
            continue
        text = match_line_endings(files[path], base_text)
        if base_text is not None and text == base_text:
            continue
        adds, dels = count_changes(base_text or "", text)
        item = {"path": path, "status": "new" if entry is None else "modified", "mode": (entry or {}).get("mode", "100644"), "text": text, "base_text": base_text, "additions": adds, "deletions": dels,
                "fingerprint": fingerprint(files[path]), "drift": False}
        if expected and path in expected and base_text is not None:
            want_adds, want_dels = expected[path]
            slack = max(3, int(0.2 * max(want_adds + want_dels, 1)))
            item["drift"] = (adds - want_adds) > slack or (dels - want_dels) > slack
        planned.append(item)
    if not planned:
        if skipped:
            raise GitCommitError(f"The only files that differ ({', '.join(skipped[:3])}) are not text files on GitHub, which Sonic will not overwrite, so there is nothing to commit.")
        raise GitCommitError("Those files already match the branch on GitHub, so there is nothing to commit.")
    return {"head": head["commit"], "tree": head["tree"], "files": planned, "skipped": skipped}


MERGE_METHODS = ("squash", "merge", "rebase")


def find_open_pull_request(api: Any, repo: str, head: str, base: Optional[str] = None) -> Optional[int]:
    """The number of the open pull request from branch `head` (into `base`, when given), or None. Blocking."""
    owner = repo.split("/", 1)[0]
    status, data = api.request("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{head}&per_page=10")
    if status != 200 or not isinstance(data, list):
        raise _explain(status, data, "list the pull requests")
    for pull in data:
        if isinstance(pull, dict) and (base is None or (pull.get("base") or {}).get("ref") == base):
            return int(pull.get("number"))
    return None


def pull_request_info(api: Any, repo: str, number: int) -> Dict[str, Any]:
    """The state of a pull request as GitHub has it now, or GitCommitError."""
    status, data = api.request("GET", f"/repos/{repo}/pulls/{int(number)}")
    if status != 200 or not isinstance(data, dict):
        raise _explain(status, data, "read the pull request")
    return {
        "state": data.get("state"), "merged": bool(data.get("merged")), "draft": bool(data.get("draft")), "mergeable": data.get("mergeable"), "title": str(data.get("title") or ""),
        "head_sha": (data.get("head") or {}).get("sha"), "head_ref": (data.get("head") or {}).get("ref"), "base_ref": (data.get("base") or {}).get("ref"),
    }


def merge_pull_request(api: Any, repo: str, number: int, head_sha: str, title: str, method: str = "squash") -> Dict[str, Any]:
    """Merges the pull request, only if its head is still `head_sha` (GitHub refuses otherwise). Tries `method` first and then the other methods when the repository does not allow it. Returns
    {"sha", "method"} or raises GitCommitError in words."""
    order = [method if method in MERGE_METHODS else "squash"]
    order += [m for m in MERGE_METHODS if m not in order]
    for attempt in order:
        body: Dict[str, Any] = {"merge_method": attempt, "sha": head_sha}
        if attempt == "squash":
            body["commit_title"] = f"{title} (#{int(number)})"[:200]
        status, data = api.request("PUT", f"/repos/{repo}/pulls/{int(number)}/merge", body)
        if status == 200 and isinstance(data, dict) and data.get("merged", True):
            return {"sha": str(data.get("sha") or ""), "method": attempt}
        message = str((data or {}).get("message") or "").lower() if isinstance(data, dict) else ""
        if status == 405 and ("merge method" in message or "not allowed" in message):
            continue
        if status == 405:
            raise GitCommitError("GitHub would not merge it yet: there may be conflicts, or required checks or reviews are not finished. Nothing was merged.")
        if status == 409:
            raise GitCommitError("The pull request changed after the card was shown, so nothing was merged. Ask again to see the new card.")
        raise _explain(status, data, "merge the pull request")
    raise GitCommitError("This repository does not allow Sonic's merge methods (squash, merge or rebase), so nothing was merged.")


def pull_request_body(message: str) -> str:
    body = message.split("\n", 1)[1].strip() if "\n" in message else message
    return f"{body}\n\nOpened with Sonic Assistant."


def commit_and_push(api: Any, repo: str, plan: Dict[str, Any], message: str, branch: str, *, new_branch: Optional[str] = None, pull_request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Makes the commit and moves (or creates) the branch, then opens the pull request if one was asked for. Returns {"commit", "branch", "commit_url", "pr_url", "pr_number"}. Blocking."""
    tree_body = {"base_tree": plan["tree"], "tree": [{"path": f["path"], "mode": f["mode"], "type": "blob", "content": f["text"]} for f in plan["files"]]}
    status, tree = api.request("POST", f"/repos/{repo}/git/trees", tree_body)
    if status != 201 or not isinstance(tree, dict):
        raise _explain(status, tree, "prepare the commit")
    status, commit = api.request("POST", f"/repos/{repo}/git/commits", {"message": with_trailer(message), "tree": tree["sha"], "parents": [plan["head"]]})
    if status != 201 or not isinstance(commit, dict):
        raise _explain(status, commit, "create the commit")
    target = new_branch or branch
    if new_branch:
        status, ref = api.request("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{new_branch}", "sha": commit["sha"]})
        if status != 201:
            raise _explain(status, ref, "create the branch")
    else:
        status, ref = api.request("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": commit["sha"], "force": False})
        if status != 200:
            raise _explain(status, ref, "push to the branch")
    result = {"commit": commit["sha"], "branch": target, "commit_url": f"https://github.com/{repo}/commit/{commit['sha']}", "pr_url": None, "pr_number": None}
    if pull_request:
        status, pr = api.request("POST", f"/repos/{repo}/pulls", {
            "title": pull_request["title"], "head": target, "base": pull_request["base"], "body": pull_request_body(message), "draft": bool(pull_request.get("draft")),
        })
        if status == 201 and isinstance(pr, dict):
            result.update(pr_url=pr.get("html_url"), pr_number=pr.get("number"))
        else:
            result["pr_error"] = str(_explain(status, pr, "open the pull request"))
    return result
