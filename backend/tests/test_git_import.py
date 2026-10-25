"""The bundle the browser needs to bring Sonic's commit into the person's own repository (backend/services/git_import.py)."""
import base64
import pathlib

import pytest

from backend.services import commit_flow as cf
from backend.services import git_commit as gc
from backend.services import git_import as gi
from backend.services import local_workspace as lw
from backend.tests.fake_github import FakeGitHub
from backend.utils import applied_changes as ac

ROOT = pathlib.Path(__file__).resolve().parents[2]
USER = "jack"
REPO = "SummonShenron/SAAPP"
APP = "import os\n\ndef greet(name):\n    return 'hi ' + name\n"
APP_NEW = "import os\n\ndef greet(name):\n    return 'hello ' + name\n"


@pytest.fixture
def world(monkeypatch):
    fake = FakeGitHub(repo=REPO, files={"src/app.py": APP, "src/util/x.py": "x = 1\n", "README.md": "# app\n"})
    fake.add_branch("feat/x")
    lw.apply_sync(USER, "demo", [{"path": "src/app.py", "content": APP_NEW}, {"path": "docs/new.md", "content": "new\n"}, {"path": "src/util/x.py", "content": "x = 1\n"}, {"path": "README.md", "content": "# app\n"}], [], True)
    ac.record(USER, {"src/app.py": (1, 1), "docs/new.md": (1, 0)})
    monkeypatch.setattr(cf, "_own_token", lambda username: "own-token")
    monkeypatch.setattr(gc, "GitHubApi", lambda token: fake)
    return fake


def commit_on_new_branch(world):
    _, card = cf.draft(USER, {"message": "Say hello and add docs.", "new_branch": "feat/notes"}, repo=REPO, current_branch="feat/x")
    cf.execute(USER, cf.parse_card(card))
    return world.branches["feat/notes"]


def test_the_bundle_carries_the_commit_the_changed_folders_and_the_changed_files(world):
    sha = commit_on_new_branch(world)
    bundle = gi.bundle_for(USER, REPO, sha)
    assert bundle["repo"] == REPO and bundle["commit"]["sha"] == sha and bundle["commit"]["parents"] == [world.branches["feat/x"]]
    assert bundle["commit"]["message"].startswith("Say hello and add docs.") and "Co-authored-by: Sonic Assistant" in bundle["commit"]["message"]
    assert bundle["commit"]["author"] == {"name": "Sonic Person", "email": "person@example.com", "date": "2026-10-25T20:10:21Z"} and bundle["commit"]["signature"] is None
    assert sorted(f["path"] for f in bundle["files"]) == ["docs/new.md", "src/app.py"] and all(f["mode"] == "100644" for f in bundle["files"])
    assert sorted(base64.b64decode(b["content"]).decode() for b in bundle["blobs"]) == sorted(["new\n", APP_NEW])
    assert [b["sha"] for b in bundle["blobs"]] == [f["sha"] for f in bundle["files"]]
    folder_names = [[e["name"] for e in t["entries"]] for t in bundle["trees"]]
    assert bundle["trees"][0]["sha"] == bundle["commit"]["tree"] and ["README.md", "docs", "src"] in [sorted(n) for n in folder_names]
    assert not any("x.py" in names and "app.py" not in names for names in folder_names), "an unchanged folder (src/util) is not listed"
    assert {"mode", "name", "sha"} == set(bundle["trees"][0]["entries"][0])


def test_what_cannot_be_brought_in_is_refused_in_words(world, monkeypatch):
    sha = commit_on_new_branch(world)
    for repo, commit in (("not a repo", sha), (REPO, "abc"), (REPO, "g" * 40), ("a/b;rm", sha)):
        with pytest.raises(gc.GitCommitError, match="not a repository and commit"):
            gi.build_bundle(world, repo, commit)
    with pytest.raises(gc.GitCommitError):
        gi.build_bundle(world, REPO, "f" * 40)                                                  # a commit GitHub does not have
    monkeypatch.setattr(gc, "MAX_FILES", 1)
    with pytest.raises(gc.GitCommitError, match="more than 1 files"):
        gi.build_bundle(world, REPO, sha)
    monkeypatch.setattr(gc, "MAX_FILES", 40)
    monkeypatch.setattr(gi, "MAX_TREES", 1)
    with pytest.raises(gc.GitCommitError, match="too many folders"):
        gi.build_bundle(world, REPO, sha)
    monkeypatch.setattr(gi, "MAX_TREES", 80)
    monkeypatch.setattr(gc, "MAX_TOTAL_BYTES", 5)
    with pytest.raises(gc.GitCommitError, match="too large"):
        gi.build_bundle(world, REPO, sha)


def test_a_commit_that_deletes_a_file_or_has_two_parents_is_left_to_the_commands(world):
    head = world.commits[world.branches["feat/x"]]
    flat = dict(world.trees[head["tree"]])
    del flat["README.md"]
    deleting = world._commit(world._tree(flat), [world.branches["feat/x"]], "delete a file")
    with pytest.raises(gc.GitCommitError, match="deletes a file"):
        gi.build_bundle(world, REPO, deleting)
    merge = world._commit(head["tree"], [world.branches["feat/x"], world.branches["main"]], "a merge")
    with pytest.raises(gc.GitCommitError, match="exactly one parent"):
        gi.build_bundle(world, REPO, merge)


def test_it_reads_with_the_persons_own_token_and_without_one_says_so(world, monkeypatch):
    sha = commit_on_new_branch(world)

    def no_token(username):
        raise cf.NoGitHubToken("Committing uses your own GitHub token, and none is set.")

    monkeypatch.setattr(cf, "_own_token", no_token)
    with pytest.raises(cf.NoGitHubToken):
        gi.bundle_for(USER, REPO, sha)
    seen = []
    monkeypatch.setattr(cf, "_own_token", lambda username: seen.append(username) or "t")
    assert gi.bundle_for(USER, REPO, sha, api_factory=lambda token: world)["commit"]["sha"] == sha and seen == [USER]


def test_the_endpoint_is_signed_in_only_and_never_logs_text():
    import ast

    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '@app.post("/api/local-git/bundle")' in app and "git_import.bundle_for" in app and "is_guest_username(username)" in app.split("local_git_bundle")[1][:600]
    source = (ROOT / "backend" / "services" / "git_import.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1])


def test_the_committed_reply_matches_what_the_browser_looks_for(world):
    """The page finds the commit in Sonic's reply with a pattern in local/src/localGit/commitResult.ts; this pins the wording that pattern depends on."""
    import re

    source = (ROOT / "local" / "src" / "localGit" / "commitResult.ts").read_text(encoding="utf-8")
    literal = re.search(r"const PATTERN = /(.+)/;", source).group(1)
    pattern = re.compile(literal.replace("\/", "/"))
    _, card = cf.draft(USER, {"message": "Say hello and add docs.", "new_branch": "feat/notes", "open_pull_request": True}, repo=REPO, current_branch="feat/x")
    out = cf.execute(USER, cf.parse_card(card))
    found = pattern.search(out)
    assert found and found.group(1) == REPO and found.group(2) == world.branches["feat/notes"] and found.group(3) == "feat/notes" and found.group(4) == REPO
