"""A small in-memory GitHub for the commit tests: one repository, its branches, commits, trees and blobs, and the Git Data API routes the commit code uses.

Trees are stored flat ({path: (mode, blob sha)}); `GET /git/trees/{sha}` answers with one directory level, the way GitHub does, so the code that walks a path one directory at a time is
really exercised. Failures can be injected per route with `fail`.
"""
import base64
import hashlib
import re
from typing import Any, Dict, Optional, Tuple


def _sha(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


class FakeGitHub:
    def __init__(self, repo: str = "owner/app", files: Optional[Dict[str, str]] = None, default_branch: str = "main", private: bool = True, can_push: bool = True):
        self.repo, self.default_branch, self.private, self.can_push = repo, default_branch, private, can_push
        self.blobs: Dict[str, bytes] = {}
        self.trees: Dict[str, Dict[str, Tuple[str, str]]] = {}
        self.commits: Dict[str, Dict[str, Any]] = {}
        self.branches: Dict[str, str] = {}
        self.pulls = []
        self._subtrees: Dict[str, Tuple[str, str]] = {}
        self.merged_pulls, self.closed_pulls, self.merge_calls = set(), set(), []
        self.merge_methods = {"squash", "merge", "rebase"}
        self.mergeable = True
        self.truncate_listing = False                          # answer the whole-branch listing as GitHub does for a huge repository
        self.calls = []
        self.fail: Dict[str, Tuple[int, str]] = {}           # "METHOD /path-prefix" -> (status, message)
        flat = {path: ("100644", self._blob(text.encode("utf-8"))) for path, text in (files or {}).items()}
        tree = self._tree(flat)
        commit = self._commit(tree, [], "initial")
        self.branches[default_branch] = commit

    # ---- storage ----
    def _blob(self, raw: bytes) -> str:
        sha = hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()        # the real git id, so the code that compares a local file with the repo by hash is exercised
        self.blobs[sha] = raw
        return sha

    def _tree(self, flat: Dict[str, Tuple[str, str]]) -> str:
        sha = _sha("tree", repr(sorted(flat.items())))
        self.trees[sha] = dict(flat)
        return sha

    def _commit(self, tree: str, parents, message: str) -> str:
        sha = _sha("commit", tree, ",".join(parents), message)
        self.commits[sha] = {"tree": tree, "parents": list(parents), "message": message}
        return sha

    def add_branch(self, name: str, from_branch: Optional[str] = None) -> None:
        self.branches[name] = self.branches[from_branch or self.default_branch]

    def text_at(self, branch: str, path: str) -> Optional[str]:
        flat = self.trees[self.commits[self.branches[branch]]["tree"]]
        entry = flat.get(path)
        return self.blobs[entry[1]].decode("utf-8") if entry else None

    def mode_at(self, branch: str, path: str) -> Optional[str]:
        entry = self.trees[self.commits[self.branches[branch]]["tree"]].get(path)
        return entry[0] if entry else None

    def move_branch_elsewhere(self, branch: str) -> None:
        """Someone else pushes to the branch."""
        tree = self.commits[self.branches[branch]]["tree"]
        self.branches[branch] = self._commit(tree, [self.branches[branch]], "someone else's commit")

    # ---- the API ----
    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
        self.calls.append((method, path, body))
        for key, (status, message) in self.fail.items():
            verb, prefix = key.split(" ", 1)
            if verb == method and path.startswith(prefix):
                return status, {"message": message}
        base = f"/repos/{self.repo}"
        if method == "GET" and path == base:
            return 200, {"default_branch": self.default_branch, "private": self.private, "permissions": {"push": self.can_push}}
        match = re.match(rf"{re.escape(base)}/git/ref/heads/(.+)$", path)
        if method == "GET" and match:
            branch = match.group(1)
            return (200, {"object": {"sha": self.branches[branch]}}) if branch in self.branches else (404, {"message": "Not Found"})
        match = re.match(rf"{re.escape(base)}/git/commits/(\w+)$", path)
        if method == "GET" and match:
            commit = self.commits.get(match.group(1))
            if not commit:
                return 404, {"message": "Not Found"}
            who = {"name": "Sonic Person", "email": "person@example.com", "date": "2026-10-25T20:10:21Z"}
            return 200, {"sha": match.group(1), "tree": {"sha": commit["tree"]}, "parents": [{"sha": p} for p in commit["parents"]], "message": commit["message"], "author": who, "committer": who,
                         "verification": {"verified": False, "signature": None}}
        match = re.match(rf"{re.escape(base)}/git/trees/([^/?]+)\?recursive=1$", path)
        if method == "GET" and match:
            flat = self.trees.get(match.group(1))
            if flat is None:
                return 404, {"message": "Not Found"}
            return 200, {"truncated": self.truncate_listing, "tree": [{"path": p, "mode": mode, "type": "blob", "sha": blob} for p, (mode, blob) in sorted(flat.items())]}
        match = re.match(rf"{re.escape(base)}/git/trees/([^/]+)$", path)
        if method == "GET" and match:
            return self._list_tree(match.group(1))
        match = re.match(rf"{re.escape(base)}/git/blobs/(\w+)$", path)
        if method == "GET" and match:
            raw = self.blobs.get(match.group(1))
            return (200, {"content": base64.b64encode(raw).decode(), "encoding": "base64"}) if raw is not None else (404, {"message": "Not Found"})
        if method == "POST" and path == f"{base}/git/trees":
            flat = dict(self.trees[body["base_tree"]])
            for entry in body["tree"]:
                flat[entry["path"]] = (entry["mode"], self._blob(entry["content"].encode("utf-8")))
            return 201, {"sha": self._tree(flat)}
        if method == "POST" and path == f"{base}/git/commits":
            return 201, {"sha": self._commit(body["tree"], body["parents"], body["message"])}
        if method == "POST" and path == f"{base}/git/refs":
            name = body["ref"].replace("refs/heads/", "", 1)
            if name in self.branches:
                return 422, {"message": "Reference already exists"}
            self.branches[name] = body["sha"]
            return 201, {"ref": body["ref"]}
        match = re.match(rf"{re.escape(base)}/git/refs/heads/(.+)$", path)
        if method == "PATCH" and match:
            name = match.group(1)
            if name not in self.branches:
                return 404, {"message": "Not Found"}
            if self.branches[name] not in self.commits[body["sha"]]["parents"]:
                return 422, {"message": "Update is not a fast forward"}
            self.branches[name] = body["sha"]
            return 200, {"ref": f"refs/heads/{name}"}
        if method == "POST" and path == f"{base}/pulls":
            self.pulls.append(body)
            return 201, {"html_url": f"https://github.com/{self.repo}/pull/{len(self.pulls)}", "number": len(self.pulls)}
        match = re.match(rf"{re.escape(base)}/pulls/(\d+)$", path)
        if method == "GET" and match:
            number = int(match.group(1))
            if not 1 <= number <= len(self.pulls):
                return 404, {"message": "Not Found"}
            pull = self.pulls[number - 1]
            return 200, {"state": "closed" if number in self.merged_pulls or number in self.closed_pulls else "open", "merged": number in self.merged_pulls, "draft": bool(pull.get("draft")),
                         "mergeable": self.mergeable, "title": pull["title"], "head": {"ref": pull["head"], "sha": self.branches[pull["head"]]}, "base": {"ref": pull["base"]}}
        match = re.match(rf"{re.escape(base)}/pulls/(\d+)/merge$", path)
        if method == "PUT" and match:
            number = int(match.group(1))
            pull = self.pulls[number - 1]
            self.merge_calls.append(body)
            if body["merge_method"] not in self.merge_methods:
                return 405, {"message": f"{body['merge_method'].capitalize()} merges are not allowed on this repository (merge method)."}
            if not self.mergeable:
                return 405, {"message": "Pull Request is not mergeable"}
            if body.get("sha") != self.branches[pull["head"]]:
                return 409, {"message": "Head branch was modified. Review and try the merge again."}
            head_commit = self.commits[self.branches[pull["head"]]]
            parents = [self.branches[pull["base"]]] if body["merge_method"] == "squash" else [self.branches[pull["base"]], self.branches[pull["head"]]]
            merged = self._commit(head_commit["tree"], parents, f"merge {body['merge_method']} {number}")
            self.branches[pull["base"]] = merged
            self.merged_pulls.add(number)
            return 200, {"sha": merged, "merged": True}
        return 404, {"message": f"unhandled {method} {path}"}

    def _list_tree(self, ident: str) -> Tuple[int, Any]:
        if ident in self._subtrees:                           # a folder's id is made from what is in it, as on GitHub, so an unchanged folder has the same id in every commit
            root, prefix = self._subtrees[ident]
        else:
            root, prefix = ident, ""
        flat = self.trees.get(root)
        if flat is None:
            return 404, {"message": "Not Found"}
        entries: Dict[str, Dict[str, str]] = {}
        for path, (mode, blob) in flat.items():
            if prefix and not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            name, _, tail = rest.partition("/")
            if tail:
                child = prefix + name + "/"
                content = sorted((p, m, b) for p, (m, b) in flat.items() if p.startswith(child))
                sub_id = _sha("subtree", child, repr(content))
                self._subtrees[sub_id] = (root, child)
                entries[name] = {"path": name, "mode": "040000", "type": "tree", "sha": sub_id}
            else:
                entries[name] = {"path": name, "mode": mode, "type": "blob", "sha": blob}
        return 200, {"tree": list(entries.values())}
