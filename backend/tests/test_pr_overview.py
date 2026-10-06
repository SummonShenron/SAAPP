from types import SimpleNamespace

import pytest
import requests

from backend.services import github_service as gs

REPO = "Acme/OtherRepo"
BASE = "https://api.github.com"


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text

    def json(self):
        return self._data


class _GitHub:
    """A tiny fake of the GitHub endpoints the overview flow touches, recording what it was sent."""

    def __init__(self, monkeypatch, *, files=None, pr=None, commits=None, comments=None, files_status=200,
                 patch_status=200, post_status=201):
        self.files = files if files is not None else [{"filename": "app.py", "status": "modified", "additions": 3, "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"}]
        self.pr = pr
        self.commits = commits if commits is not None else []
        self.comments = comments or []
        self.files_status, self.patch_status, self.post_status = files_status, patch_status, post_status
        self.gets, self.posts, self.patches = [], [], []
        monkeypatch.setattr(gs.requests, "get", self._get)
        monkeypatch.setattr(gs.requests, "post", self._post)
        monkeypatch.setattr(gs.requests, "patch", self._patch)

    def _get(self, url, headers=None, params=None, timeout=None):
        self.gets.append((url, params))
        if url.endswith("/pulls/7/files"):
            if self.files_status != 200:
                return _Resp(self.files_status, text="Not Found")
            page = (params or {}).get("page", 1)
            return _Resp(200, self.files[(page - 1) * 100: page * 100])
        if url.endswith("/pulls/7"):
            return _Resp(200 if self.pr else 404, self.pr)
        if url.endswith("/pulls/7/commits"):
            return _Resp(200, self.commits)
        if url.endswith("/issues/7/comments"):
            return _Resp(200, self.comments)
        return _Resp(404)

    def _post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp(self.post_status, {"html_url": "https://github.com/x/pull/7#issuecomment-1"})

    def _patch(self, url, headers=None, json=None, timeout=None):
        self.patches.append((url, json))
        return _Resp(self.patch_status, {"html_url": "https://github.com/x/pull/7#issuecomment-9"})


@pytest.fixture
def llm(monkeypatch):
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        return SimpleNamespace(content="### Summary\nAdds retries.")

    monkeypatch.setattr(gs, "_call_llm_with_retry", fake)
    return prompts


PR = {"number": 7, "title": "Add retries", "body": "Flaky calls need backoff.", "user": {"login": "jack"},
      "base": {"ref": "main"}, "head": {"ref": "feat/retry", "sha": "abcdef1234567"}, "changed_files": 1, "additions": 3, "deletions": 1}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def test_the_review_is_written_from_the_prs_own_description_commits_and_diff(monkeypatch, llm):
    gh = _GitHub(monkeypatch, pr=PR, commits=[{"commit": {"message": "feat: add backoff\n\ndetails"}}])
    gs.process_pr_summary(REPO, 7, token="t")
    prompt = llm[0]
    assert "PR #7: Add retries" in prompt and "Flaky calls need backoff." in prompt
    assert "feat: add backoff" in prompt and "File: app.py" in prompt
    assert "{repo}" not in prompt and "{formatted_diffs}" not in prompt  # the old unfilled-placeholder bug


def test_files_are_paged_not_capped_at_the_first_ten(monkeypatch, llm):
    many = [{"filename": f"src/f{i}.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@\n+x"} for i in range(130)]
    gh = _GitHub(monkeypatch, files=many)
    evidence = gs.fetch_pr_evidence(REPO, 7, {})
    assert len(evidence["files"]) == 130
    assert [p for u, p in gh.gets if u.endswith("/pulls/7/files")] == [{"per_page": 100, "page": 1}, {"per_page": 100, "page": 2}]


def test_missing_pr_details_do_not_stop_a_review(monkeypatch, llm):
    _GitHub(monkeypatch, pr=None)  # the PR endpoint answers 404
    result = gs.process_pr_summary(REPO, 7, token="t")
    assert result["status"] == "comment_posted"
    assert "PR #" not in llm[0]                 # nothing about the PR itself to quote...
    assert "File: app.py" in llm[0]             # ...but the diff is still there
    assert "Tests changed alongside the source: NONE" in llm[0]


def test_a_network_failure_fetching_details_falls_back_to_the_files_alone(monkeypatch, llm):
    gh = _GitHub(monkeypatch)
    real_get = gh._get

    def flaky(url, headers=None, params=None, timeout=None):
        if url.endswith("/pulls/7") or url.endswith("/commits"):
            raise requests.ConnectionError("reset")
        return real_get(url, headers=headers, params=params, timeout=timeout)

    monkeypatch.setattr(gs.requests, "get", flaky)
    evidence = gs.fetch_pr_evidence(REPO, 7, {})
    assert evidence["files"] and evidence["pr"] is None and evidence["commits"] == []


def test_an_unreadable_file_list_is_an_error_and_nothing_is_posted(monkeypatch, llm):
    gh = _GitHub(monkeypatch, files_status=404)
    assert gs.process_pr_summary(REPO, 7, token="t") is None
    assert gh.posts == [] and gh.patches == [] and llm == []


# ---------------------------------------------------------------------------
# The comment: one per PR, updated in place; never an error in public
# ---------------------------------------------------------------------------

def test_a_new_overview_is_posted_with_a_marker_and_the_commit_it_covers(monkeypatch, llm):
    gh = _GitHub(monkeypatch, pr=PR)
    result = gs.process_pr_summary(REPO, 7, token="t")
    url, payload = gh.posts[0]
    assert url == f"{BASE}/repos/{REPO}/issues/7/comments"
    assert payload["body"].startswith(gs.OVERVIEW_MARKER)
    assert "**Sonic Assistant PR Overview**" in payload["body"] and "Adds retries." in payload["body"]
    assert "Updated for commit `abcdef1`" in payload["body"]
    assert result["status"] == "comment_posted"


def test_a_later_push_updates_the_existing_overview_instead_of_adding_another(monkeypatch, llm):
    gh = _GitHub(monkeypatch, pr=PR, comments=[
        {"id": 1, "body": "someone else's comment"},
        {"id": 42, "body": f"{gs.OVERVIEW_MARKER}\n**Sonic Assistant PR Overview**\n\nold"},
    ])
    result = gs.process_pr_summary(REPO, 7, token="t")
    assert gh.posts == []
    url, payload = gh.patches[0]
    assert url == f"{BASE}/repos/{REPO}/issues/comments/42"
    assert "Adds retries." in payload["body"] and "old" not in payload["body"]
    assert result["status"] == "comment_updated"


def test_when_the_update_is_refused_a_new_comment_is_posted(monkeypatch, llm):
    gh = _GitHub(monkeypatch, comments=[{"id": 42, "body": gs.OVERVIEW_MARKER}], patch_status=403)
    result = gs.process_pr_summary(REPO, 7, token="t")
    assert len(gh.patches) == 1 and len(gh.posts) == 1
    assert result["status"] == "comment_posted"


def test_a_failed_post_is_reported_as_failed(monkeypatch, llm):
    _GitHub(monkeypatch, post_status=403)
    assert gs.process_pr_summary(REPO, 7, token="t")["status"] == "comment_failed"


def test_a_model_failure_posts_nothing_and_never_leaks_the_error_into_a_public_pr(monkeypatch):
    gh = _GitHub(monkeypatch, comments=[{"id": 42, "body": f"{gs.OVERVIEW_MARKER}\ngood earlier overview"}])

    def boom(prompt):
        raise RuntimeError("429 quota exceeded for key sk-secret")

    monkeypatch.setattr(gs, "_call_llm_with_retry", boom)
    result = gs.process_pr_summary(REPO, 7, token="t")
    assert result["status"] == "summary_failed"
    assert gh.posts == [] and gh.patches == []  # the good earlier overview is left alone


def test_an_empty_model_answer_is_not_posted(monkeypatch):
    gh = _GitHub(monkeypatch)
    monkeypatch.setattr(gs, "_call_llm_with_retry", lambda prompt: SimpleNamespace(content="   "))
    assert gs.process_pr_summary(REPO, 7, token="t")["status"] == "summary_failed"
    assert gh.posts == []


def test_block_style_model_content_is_joined(monkeypatch):
    gh = _GitHub(monkeypatch)
    monkeypatch.setattr(gs, "_call_llm_with_retry", lambda prompt: SimpleNamespace(content=[{"text": "Part one."}, "Part two."]))
    gs.process_pr_summary(REPO, 7, token="t")
    assert "Part one.\nPart two." in gh.posts[0][1]["body"]


def test_the_token_it_is_given_is_the_one_used_on_every_call(monkeypatch, llm):
    seen = []
    gh = _GitHub(monkeypatch)
    for name in ("_get", "_post", "_patch"):
        original = getattr(gh, name)

        def wrapper(*a, _o=original, **k):
            seen.append(k.get("headers", {}).get("Authorization"))
            return _o(*a, **k)

        monkeypatch.setattr(gs.requests, name.lstrip("_"), wrapper)
    gs.process_pr_summary(REPO, 7, token="ghp_given")
    assert seen and set(seen) == {"Bearer ghp_given"}


def test_with_no_token_anywhere_nothing_is_attempted(monkeypatch, llm):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    gh = _GitHub(monkeypatch)
    assert gs.process_pr_summary(REPO, 7) is None
    assert gh.gets == [] and gh.posts == []


# ---------------------------------------------------------------------------
# The PR Sonic drafts on request: written from commits, patches and the repo's own template
# ---------------------------------------------------------------------------

import base64 as _b64

from backend.components.constraints import DRAFT_PR_PROMPT
from backend.services import agent_workflow as aw


def _compare_fake(monkeypatch, *, template=None, files=None, commits=None, status=200):
    seen = []

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append(url)
        if "/compare/" in url:
            return _Resp(status, {
                "commits": commits if commits is not None else [
                    {"commit": {"message": "feat: add backoff\n\nbody"}}, {"commit": {"message": "Merge branch 'main'"}},
                ],
                "files": files if files is not None else [
                    {"filename": "app.py", "status": "modified", "additions": 4, "deletions": 1, "patch": "@@ -1 +1 @@\n-a\n+b"},
                    {"filename": "package-lock.json", "status": "modified", "additions": 900, "deletions": 800, "patch": "LOCK-CHURN"},
                ],
            })
        if template is not None and url.endswith("/contents/.github/pull_request_template.md"):
            return _Resp(200, {"content": _b64.b64encode(template.encode()).decode()})
        return _Resp(404)

    monkeypatch.setattr(aw.requests, "get", fake_get)
    return seen


def test_the_draft_is_given_commit_subjects_patches_and_a_tests_note(monkeypatch):
    _compare_fake(monkeypatch)
    text = aw.fetch_branch_diff_summary("o/r", "main", "feat", token="t")
    assert "feat: add backoff" in text and "Merge branch" not in text
    assert "File: app.py (modified, +4/-1)" in text and "@@ -1 +1 @@" in text
    assert "LOCK-CHURN" not in text and "lockfile/generated" in text
    assert "Tests changed alongside the source: NONE" in text
    assert "full diff of all 1" in text


def test_the_draft_follows_the_repos_own_pr_template_when_it_has_one(monkeypatch):
    _compare_fake(monkeypatch, template="## What\n\n## Checklist\n- [ ] Tests added")
    text = aw.fetch_branch_diff_summary("o/r", "main", "feat", token="t")
    assert "PR TEMPLATE (follow its headings and checklist)" in text
    assert "- [ ] Tests added" in text


def test_no_template_means_no_template_section(monkeypatch):
    _compare_fake(monkeypatch)
    assert "PR TEMPLATE" not in aw.fetch_branch_diff_summary("o/r", "main", "feat", token="t")


def test_an_unreachable_comparison_still_degrades_to_the_old_message(monkeypatch):
    _compare_fake(monkeypatch, status=404)
    assert aw.fetch_branch_diff_summary("o/r", "main", "feat", token="t") == "No diff context available."


def test_the_draft_prompt_fills_in_and_asks_for_grounded_sections():
    prompt = DRAFT_PR_PROMPT.format(context="CTX", user_message="open a PR for the retry work")
    assert "CTX" in prompt and "open a PR for the retry work" in prompt
    assert "### Summary" in prompt and "### Testing" in prompt and "Never claim anything was run" in prompt
    assert "PR TEMPLATE" in prompt  # told to follow it when present
