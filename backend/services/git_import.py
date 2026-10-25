"""What the browser needs to bring a commit Sonic made on GitHub into the person's own repository (local/src/localGit/importCommit.ts): the commit's fields, the folder listings along the way, and the
files it changed.

WHY THE SERVER. GitHub's API accepts a browser's request only with a token, and the person's token lives here, never in the browser. This reads with THEIR OWN token (the same one that made the commit)
and hands back plain data. It changes nothing on GitHub and nothing on their machine; the browser checks every object against the ids GitHub reports before writing anything.

WHAT IS SENT: the commit (tree, parent, author, committer, message, signature), the listing of every folder that holds a change, and each changed file's content (base64, exactly as GitHub stores it).
ONLY commits with one parent, that add or change files (never delete one), within the same limits as a commit (files, size). Anything else is refused in words, and the person keeps the copy-paste commands.

Nothing here logs a path, a message or a file: only counts.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from backend.services import commit_flow, git_commit as gc

logger = logging.getLogger("SASS Logger")

MAX_TREES = 80
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REGULAR_MODES = ("100644", "100755")


def _get(api: Any, path: str, action: str) -> Dict[str, Any]:
    status, data = api.request("GET", path)
    if status != 200 or not isinstance(data, dict):
        raise gc._explain(status, data, action)
    return data


def _person(raw: Any) -> Dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    return {"name": str(raw.get("name") or ""), "email": str(raw.get("email") or ""), "date": str(raw.get("date") or "")}


def _tree_of(api: Any, repo: str, commit_sha: Optional[str]) -> Optional[str]:
    """The folder-listing id of a commit as GitHub has it, or None when there is no such commit there (a commit made only on the person's machine) or none was asked about."""
    if not commit_sha or not _SHA_RE.match(commit_sha):
        return None
    status, data = api.request("GET", f"/repos/{repo}/git/commits/{commit_sha}")
    return (data.get("tree") or {}).get("sha") if status == 200 and isinstance(data, dict) else None


def build_bundle(api: Any, repo: str, commit_sha: str, compare_with: Optional[str] = None) -> Dict[str, Any]:
    """The bundle for one commit, or GitCommitError in words. `compare_with` is the commit the person's own branch is at now: when Sonic merged a pull request, whether that commit holds exactly the
    merged content (`compare_tree` equal to the commit's tree) is what makes switching their branch safe. Blocking (GitHub calls)."""
    if not _REPO_RE.match(repo or "") or not _SHA_RE.match(commit_sha or ""):
        raise gc.GitCommitError("That is not a repository and commit this can read.")
    commit = _get(api, f"/repos/{repo}/git/commits/{commit_sha}", "read the commit")
    parents = [p.get("sha") for p in commit.get("parents") or [] if isinstance(p, dict)]
    if len(parents) != 1:
        raise gc.GitCommitError("Only a commit with exactly one parent can be brought in this way.")
    root = (commit.get("tree") or {}).get("sha")
    parent_root = (_get(api, f"/repos/{repo}/git/commits/{parents[0]}", "read the commit before it").get("tree") or {}).get("sha")
    if not root or not parent_root:
        raise gc.GitCommitError("GitHub did not give the commit's folder listing.")

    trees: List[Dict[str, Any]] = []
    files: List[Dict[str, str]] = []

    def listing(tree_sha: Optional[str]) -> List[Dict[str, str]]:
        if not tree_sha:
            return []
        data = _get(api, f"/repos/{repo}/git/trees/{tree_sha}", "read a folder listing")
        if data.get("truncated"):
            raise gc.GitCommitError("A folder in this repository is too large to read in full, so it was left to the commands.")
        return [{"mode": str(e.get("mode")), "name": str(e.get("path")), "sha": str(e.get("sha")), "type": str(e.get("type"))} for e in data.get("tree") or []]

    def walk(new_sha: str, old_sha: Optional[str], prefix: str) -> None:
        if len(trees) >= MAX_TREES:
            raise gc.GitCommitError("The commit touches too many folders to bring in this way.")
        new_entries, old_entries = listing(new_sha), {e["name"]: e for e in listing(old_sha)}
        trees.append({"sha": new_sha, "entries": [{"mode": e["mode"], "name": e["name"], "sha": e["sha"]} for e in new_entries]})
        names = {e["name"] for e in new_entries}
        if any(name not in names for name in old_entries):
            raise gc.GitCommitError("The commit deletes a file, which this does not bring in. Use the commands.")
        for entry in new_entries:
            old = old_entries.get(entry["name"])
            if old and old["sha"] == entry["sha"] and old["mode"] == entry["mode"]:
                continue
            if entry["type"] == "tree":
                walk(entry["sha"], old["sha"] if old and old["type"] == "tree" else None, f"{prefix}{entry['name']}/")
            elif entry["type"] == "blob" and entry["mode"] in _REGULAR_MODES:
                files.append({"path": f"{prefix}{entry['name']}", "sha": entry["sha"], "mode": entry["mode"]})
            else:
                raise gc.GitCommitError("The commit changes a link or a submodule, which this does not bring in. Use the commands.")
            if len(files) > gc.MAX_FILES:
                raise gc.GitCommitError(f"The commit changes more than {gc.MAX_FILES} files, which this does not bring in. Use the commands.")

    walk(root, parent_root, "")
    if not files:
        raise gc.GitCommitError("The commit changes no files that can be brought in.")
    blobs, total = [], 0
    for file in files:
        blob = _get(api, f"/repos/{repo}/git/blobs/{file['sha']}", "read a file")
        content = str(blob.get("content") or "")
        total += len(content) * 3 // 4
        if blob.get("encoding") != "base64" or total > gc.MAX_TOTAL_BYTES:
            raise gc.GitCommitError("The files in this commit are too large to bring in this way. Use the commands.")
        blobs.append({"sha": file["sha"], "content": content.replace("\n", "")})
    verification = commit.get("verification") or {}
    logger.info("[git import] built a bundle of %d folder listing(s) and %d file(s).", len(trees), len(files))
    return {
        "repo": repo,
        "commit": {
            "sha": commit_sha, "tree": root, "parents": parents, "author": _person(commit.get("author")), "committer": _person(commit.get("committer")),
            "message": str(commit.get("message") or ""), "signature": verification.get("signature") or None,
        },
        "trees": trees, "blobs": blobs, "files": files, "compare_tree": _tree_of(api, repo, compare_with),
    }


def bundle_for(username: str, repo: str, commit_sha: str, compare_with: Optional[str] = None, api_factory: Optional[Any] = None) -> Dict[str, Any]:
    """build_bundle with the person's own token. Blocking."""
    api = (api_factory or gc.GitHubApi)(commit_flow._own_token(username))
    return build_bundle(api, repo, commit_sha, compare_with)
