"""Committing and pushing to GitHub through its API (backend/services/git_commit.py), against a small in-memory GitHub."""
import pytest

from backend.services import git_commit as gc
from backend.tests.fake_github import FakeGitHub

APP = "import os\n\ndef greet(name):\n    return 'hi ' + name\n"
APP_NEW = "import os\n\ndef greet(name):\n    return 'hello ' + name\n"


def github(**kw):
    files = {"src/app.py": APP, "README.md": "# app\n", "run.sh": "echo hi\n", "src/deep/mod.py": "x = 1\n"}
    files.update(kw.pop("files", {}))
    return FakeGitHub(files=files, **kw)


# ---- pure helpers ---------------------------------------------------------------------------------------------------------------------------

def test_the_message_is_plain_short_and_has_no_trailers_of_its_own():
    assert gc.clean_message("Fix greeting wording\n\nIt now says hello.") == "Fix greeting wording\n\nIt now says hello."
    cleaned = gc.clean_message("Fix greeting wording.\n\nCo-authored-by: Someone <a@b.c>\nSigned-off-by: X <x@y.z>\x07")
    assert cleaned == "Fix greeting wording." and "Co-authored" not in cleaned and "\x07" not in cleaned
    for bad, fragment in (("", "short commit message"), ("hi", "short commit message"), ("Fix it.\n\n" + "y" * 450, "400 characters")):
        with pytest.raises(gc.GitCommitError, match=fragment):
            gc.clean_message(bad)


def test_every_commit_ends_with_the_sonic_co_author_trailer():
    message = gc.with_trailer("Fix greeting wording.")
    assert message == f"Fix greeting wording.\n\nCo-authored-by: {gc.CO_AUTHOR}" and "Sonic Assistant" in gc.CO_AUTHOR


@pytest.mark.parametrize("name,ok", [("feat/x", True), ("sonic/fix-clamp", True), ("v1.2", True), ("", False), ("a b", False), ("a..b", False), ("-x", False), ("x/", False), ("a//b", False), ("a.lock", False), ("a@{b", False)])
def test_branch_names_must_be_ones_github_accepts(name, ok):
    if ok:
        assert gc.clean_branch(name) == name
    else:
        with pytest.raises(gc.GitCommitError):
            gc.clean_branch(name)


@pytest.mark.parametrize("path,refused", [
    (".github/workflows/ci.yml", True), (".env", True), ("config/.env.production", True), ("package.json", True), ("sub/requirements.txt", True), ("Dockerfile", True), ("render.yaml", True),
    (".sonic/notes.md", True), ("report.docx", True), ("logo.png", True), ("src/app.py", False), (".sonic/tests/test_a.py", False), ("README.md", False),
])
def test_paths_sonic_never_commits_are_refused_with_a_reason(path, refused):
    assert bool(gc.refusal_for_path(path)) is refused


def test_line_endings_follow_the_file_already_in_the_repo_and_changes_are_counted():
    assert gc.match_line_endings("a\r\nb\r\n", "x\ny\n") == "a\nb\n"
    assert gc.match_line_endings("a\nb\n", "x\r\ny\r\n") == "a\r\nb\r\n"
    assert gc.match_line_endings("a\r\nb\n", None) == "a\nb\n"
    assert gc.count_changes("a\nb\nc\n", "a\nB\nc\nd\n") == (2, 1) and gc.count_changes("", "x\ny\n") == (2, 0)


def test_too_many_files_a_big_file_and_nothing_at_all_are_refused():
    with pytest.raises(gc.GitCommitError, match="Nothing applied"):
        gc.check_files({})
    with pytest.raises(gc.GitCommitError, match=f"at most {gc.MAX_FILES}"):
        gc.check_files({f"f{n}.py": "x" for n in range(gc.MAX_FILES + 1)})
    with pytest.raises(gc.GitCommitError, match="too large"):
        gc.check_files({"big.py": "x" * (gc.MAX_FILE_BYTES + 1)})
    with pytest.raises(gc.GitCommitError, match="cannot be committed by Sonic: CI workflow"):
        gc.check_files({".github/workflows/ci.yml": "x"})


# ---- reading GitHub ---------------------------------------------------------------------------------------------------------------------------

def test_the_repo_report_says_whether_the_token_can_push_and_whether_it_is_private():
    assert gc.repo_info(github(), "owner/app") == {"default_branch": "main", "private": True, "can_push": True}
    assert gc.repo_info(github(can_push=False, private=False), "owner/app") == {"default_branch": "main", "private": False, "can_push": False}
    fake = github()
    fake.fail["GET /repos/owner/app"] = (404, "Not Found")
    with pytest.raises(gc.GitCommitError, match="could not find that repository"):
        gc.repo_info(fake, "owner/app")


def test_a_file_in_a_nested_directory_is_found_with_its_mode_and_a_missing_one_is_none():
    fake = github()
    head = gc.branch_head(fake, "owner/app", "main")
    cache = {}
    assert gc.tree_entry(fake, "owner/app", head["tree"], "src/deep/mod.py", cache)["type"] == "blob"
    assert gc.tree_entry(fake, "owner/app", head["tree"], "src/nope.py", cache) is None
    assert gc.tree_entry(fake, "owner/app", head["tree"], "src/app.py/x.py", cache) is None
    assert gc.branch_head(fake, "owner/app", "no-such-branch") is None


# ---- planning ---------------------------------------------------------------------------------------------------------------------------------

def test_a_plan_has_new_and_modified_files_with_counts_keeps_the_mode_and_drops_unchanged_files():
    fake = github()
    fake.trees[fake.commits[fake.branches["main"]]["tree"]]["run.sh"] = ("100755", fake.trees[fake.commits[fake.branches["main"]]["tree"]]["run.sh"][1])
    files = {"src/app.py": APP_NEW, "docs/new.md": "# new\n", "README.md": "# app\n", "run.sh": "echo hi there\n"}
    plan = gc.build_plan(fake, "owner/app", "main", files)
    by = {f["path"]: f for f in plan["files"]}
    assert sorted(by) == ["docs/new.md", "run.sh", "src/app.py"]                                  # README.md matches the repo, so it is not in the commit
    assert by["src/app.py"]["status"] == "modified" and (by["src/app.py"]["additions"], by["src/app.py"]["deletions"]) == (1, 1)
    assert by["docs/new.md"]["status"] == "new" and by["docs/new.md"]["mode"] == "100644" and by["run.sh"]["mode"] == "100755"
    assert len(by["src/app.py"]["fingerprint"]) == 8


def test_a_plan_follows_the_repos_line_endings_so_a_windows_folder_is_not_a_whole_file_diff():
    fake = github(files={"win.txt": "one\r\ntwo\r\n"})
    plan = gc.build_plan(fake, "owner/app", "main", {"win.txt": "one\ntwo\nthree\n"})
    assert plan["files"][0]["text"] == "one\r\ntwo\r\nthree\r\n" and (plan["files"][0]["additions"], plan["files"][0]["deletions"]) == (1, 0)


def test_a_file_that_differs_from_the_approved_change_by_more_than_that_change_is_flagged():
    base = "".join(f"line {n}\n" for n in range(40))
    fake = github(files={"big.py": base})
    local = "".join(f"line {n}\n" for n in range(40)).replace("line 3\n", "line three\n") + "".join(f"extra {n}\n" for n in range(30))
    plan = gc.build_plan(fake, "owner/app", "main", {"big.py": local}, expected={"big.py": (1, 1)})
    assert plan["files"][0]["drift"] is True
    ok = gc.build_plan(fake, "owner/app", "main", {"big.py": base.replace("line 3\n", "line three\n")}, expected={"big.py": (1, 1)})
    assert ok["files"][0]["drift"] is False


def test_a_branch_that_is_not_on_github_nothing_to_commit_and_a_binary_file_are_refused_in_words():
    fake = github()
    with pytest.raises(gc.GitCommitError, match="not on GitHub yet"):
        gc.build_plan(fake, "owner/app", "local-only", {"src/app.py": APP_NEW})
    with pytest.raises(gc.GitCommitError, match="already match the branch"):
        gc.build_plan(fake, "owner/app", "main", {"src/app.py": APP})
    binary = github()
    root = binary.commits[binary.branches["main"]]["tree"]
    binary.blobs[binary.trees[root]["README.md"][1]] = b"\xff\xfe\x00\x80"
    with pytest.raises(gc.GitCommitError, match="not a text file"):
        gc.build_plan(binary, "owner/app", "main", {"README.md": "text now\n"})


# ---- committing -------------------------------------------------------------------------------------------------------------------------------

def test_a_commit_on_the_current_branch_moves_the_branch_keeps_the_mode_and_carries_the_trailer():
    fake = github()
    fake.add_branch("feat/x")
    plan = gc.build_plan(fake, "owner/app", "feat/x", {"src/app.py": APP_NEW})
    before = fake.branches["feat/x"]
    result = gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.", "feat/x")
    assert fake.branches["feat/x"] == result["commit"] != before and fake.commits[result["commit"]]["parents"] == [before]
    assert fake.text_at("feat/x", "src/app.py") == APP_NEW and fake.text_at("main", "src/app.py") == APP, "only the named branch moves"
    assert fake.commits[result["commit"]]["message"] == f"Say hello instead of hi.\n\nCo-authored-by: {gc.CO_AUTHOR}"
    assert result["branch"] == "feat/x" and result["pr_url"] is None and result["commit_url"].endswith(result["commit"])
    assert all(call[2].get("force") is False for call in fake.calls if call[0] == "PATCH"), "never a forced push"


def test_a_new_branch_is_created_from_the_head_and_a_pull_request_is_opened_into_the_target():
    fake = github()
    plan = gc.build_plan(fake, "owner/app", "main", {"src/app.py": APP_NEW})
    result = gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.\n\nIt now greets warmly.", "main", new_branch="sonic/hello",
                                pull_request={"title": "Say hello", "base": "main", "draft": True})
    assert fake.branches["sonic/hello"] == result["commit"] and fake.text_at("main", "src/app.py") == APP, "main is untouched"
    assert fake.pulls == [{"title": "Say hello", "head": "sonic/hello", "base": "main", "body": "It now greets warmly.\n\nOpened with Sonic Assistant.", "draft": True}]
    assert result["pr_url"] == "https://github.com/owner/app/pull/1" and result["pr_number"] == 1


def test_a_branch_that_moved_since_the_card_is_not_overwritten():
    fake = github()
    fake.add_branch("feat/x")
    plan = gc.build_plan(fake, "owner/app", "feat/x", {"src/app.py": APP_NEW})
    fake.move_branch_elsewhere("feat/x")
    moved = fake.branches["feat/x"]
    with pytest.raises(gc.GitCommitError, match="moved on GitHub"):
        gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.", "feat/x")
    assert fake.branches["feat/x"] == moved


@pytest.mark.parametrize("route,status,message,fragment", [
    ("PATCH /repos/owner/app/git/refs", 403, "Resource not accessible by personal access token", "cannot"),
    ("PATCH /repos/owner/app/git/refs", 422, "Cannot update this protected ref (protected branch)", "protected"),
    ("POST /repos/owner/app/git/trees", 404, "Not Found", "could not find"),
    ("POST /repos/owner/app/git/commits", 401, "Bad credentials", "did not accept your token"),
])
def test_github_refusals_become_plain_sentences_and_leave_nothing_half_done(route, status, message, fragment):
    fake = github()
    fake.add_branch("feat/x")
    plan = gc.build_plan(fake, "owner/app", "feat/x", {"src/app.py": APP_NEW})
    fake.fail[route] = (status, message)
    before = dict(fake.branches)
    with pytest.raises(gc.GitCommitError, match=fragment):
        gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.", "feat/x")
    assert fake.branches == before


def test_an_existing_branch_name_is_refused_and_a_failed_pull_request_still_reports_the_commit():
    fake = github()
    plan = gc.build_plan(fake, "owner/app", "main", {"src/app.py": APP_NEW})
    fake.add_branch("taken")
    with pytest.raises(gc.GitCommitError, match="already exists"):
        gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.", "main", new_branch="taken")
    fake.fail["POST /repos/owner/app/pulls"] = (422, "Validation Failed")
    result = gc.commit_and_push(fake, "owner/app", plan, "Say hello instead of hi.", "main", new_branch="sonic/ok", pull_request={"title": "t", "base": "main"})
    assert fake.branches["sonic/ok"] == result["commit"] and result["pr_url"] is None and "could not open the pull request" in result["pr_error"]


def test_a_network_failure_is_a_plain_sentence_and_no_token_text_is_ever_in_an_error():
    import requests

    class Down:
        def request(self, *a, **k):
            raise requests.ConnectionError("boom https://api.github.com secret-token-xyz")

    with pytest.raises(gc.GitCommitError) as caught:
        gc.GitHubApi("secret-token-xyz", session=Down()).request("GET", "/repos/owner/app")
    assert "secret-token-xyz" not in str(caught.value) and "could not be reached" in str(caught.value)


def test_a_long_first_line_is_fitted_not_refused():
    long_line = "Add the working changes across the backend, the browser app and the function app so the commit tool can open branches and pull requests"
    fitted = gc.clean_message(long_line)
    subject, rest = fitted.split("\n\n", 1)
    assert len(subject) <= gc.MAX_SUBJECT_CHARS and subject.endswith("…") and not subject[:-1].endswith(" ") and rest == long_line
    assert gc.clean_message("x" * 120).split("\n")[0].endswith("…")
