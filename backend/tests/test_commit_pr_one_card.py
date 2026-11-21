"""Commit, push and open a pull request on ONE card, with ONE approval (backend/utils/pr_followup.py, agent_workflow.py propose_git_commit, runner_jobs.py follow_up_text).

Before: the commit and push was a runner card (the exact git line, the stronger confirmation), the pull request a different tool with a different card, so "commit, push and open a PR" in one sentence meant two
approvals of two kinds (2026-11-20, Jack). Now the runner card says in words that a pull request follows, and after a successful push the server opens it with the person's OWN token."""
import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import git_commit as gc
from backend.services import native_tools
from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_tool_agent_node import _setup_github_repo, _state
from backend.utils import pr_followup as pf
from backend.utils import runner_jobs as rj
from backend.utils import self_counters

REPO = "SummonShenron/SAAPP"


def run(coro):
    return asyncio.run(coro)


# ---- the pure pieces ----------------------------------------------------------------------------------------------------------------------------------

def make(**over):
    args = {"pr_title": None, "pr_base": None, "pr_draft": False, **over.pop("args", {})}
    return pf.build(args, repo=over.pop("repo", REPO), head=over.pop("head", "sonic/fix-x"), default_base=over.pop("default_base", "main"), subject=over.pop("subject", "fix: stop the loop"))


def test_the_pull_request_is_built_from_the_branch_the_base_and_the_commit_subject():
    follow = make()
    assert follow == {"repo": REPO, "head": "sonic/fix-x", "base": "main", "title": "fix: stop the loop", "draft": False, "merge": None}
    assert make(args={"pr_title": "Stop the plan loop", "pr_base": "develop", "pr_draft": True}) == {"repo": REPO, "head": "sonic/fix-x", "base": "develop", "title": "Stop the plan loop", "draft": True, "merge": None}


@pytest.mark.parametrize("over, why", [
    ({"head": ""}, "which branch"),
    ({"head": "main"}, "branch of its own"),
    ({"head": "feat/x", "args": {"pr_base": "feat/x"}}, "branch of its own"),
    ({"repo": ""}, "which repository"),
    ({"head": "bad branch name"}, ""),
    ({"subject": "ab"}, "title"),
])
def test_a_pull_request_that_cannot_be_arranged_is_refused_in_words_before_anything_is_shown(over, why):
    with pytest.raises(pf.PrFollowupError) as problem:
        make(**over)
    assert why in str(problem.value)


def test_the_title_is_one_plain_line_and_cut():
    follow = make(args={"pr_title": "  Fix `the`\n loop\t now  " + "x" * 300})
    assert "\n" not in follow["title"] and "`" not in follow["title"] and len(follow["title"]) <= pf.MAX_TITLE_CHARS


def test_the_card_sentence_says_everything_the_person_is_approving():
    text = pf.card_text(make(args={"pr_draft": True}))
    for part in (REPO, "fix: stop the loop", "sonic/fix-x", "into main", "as a draft", "your own GitHub token", "only if the push above succeeds"):
        assert part in text, part
    assert "ready for review" in pf.card_text(make())


class FakeGitHub:
    """The calls the follow-up makes, with the answers GitHub gives."""

    def __init__(self, *, can_push=True, base_exists=True, open_prs=None, create_status=201, create_body=None, raise_on=None):
        self.can_push, self.base_exists, self.open_prs, self.create_status, self.raise_on = can_push, base_exists, open_prs or [], create_status, raise_on
        self.create_body = create_body if create_body is not None else {"html_url": f"https://github.com/{REPO}/pull/7", "number": 7}
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.raise_on == method:
            raise gc.GitCommitError("GitHub could not be reached just now.")
        if method == "GET" and path == f"/repos/{REPO}":
            return 200, {"default_branch": "main", "private": False, "permissions": {"push": self.can_push}}
        if method == "GET" and "/git/ref/heads/" in path:
            return (200, {"object": {"sha": "abc"}}) if self.base_exists else (404, {})
        if method == "GET" and "/git/commits/" in path:
            return 200, {"tree": {"sha": "t1"}}
        if method == "GET" and "/pulls?" in path:
            return 200, self.open_prs
        if method == "POST" and path == f"/repos/{REPO}/pulls":
            return self.create_status, self.create_body
        raise AssertionError(f"unexpected call {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


def test_nothing_is_shown_when_the_token_cannot_push_or_the_target_branch_is_missing():
    pf.check_before_card(FakeGitHub(), make())
    with pytest.raises(gc.GitCommitError, match="write access"):
        pf.check_before_card(FakeGitHub(can_push=False), make())
    with pytest.raises(gc.GitCommitError, match="does not exist"):
        pf.check_before_card(FakeGitHub(base_exists=False), make())


def test_the_pull_request_is_opened_once_with_the_title_branch_base_and_draft_flag():
    api = FakeGitHub()
    opened = pf.open_after_push(api, make(args={"pr_draft": True}), "fix: stop the loop\n\nThe loop ran forever.")
    assert opened == {"url": f"https://github.com/{REPO}/pull/7", "number": 7, "existing": False}
    (post,) = api.posts()
    assert post[1] == f"/repos/{REPO}/pulls"
    assert post[2]["title"] == "fix: stop the loop" and post[2]["head"] == "sonic/fix-x" and post[2]["base"] == "main" and post[2]["draft"] is True
    assert "Opened with Sonic Assistant" in post[2]["body"]


def test_an_open_pull_request_from_the_branch_is_reported_and_never_duplicated():
    api = FakeGitHub(open_prs=[{"number": 5, "base": {"ref": "main"}}])
    opened = pf.open_after_push(api, make(), "fix: stop the loop")
    assert opened == {"url": f"https://github.com/{REPO}/pull/5", "number": 5, "existing": True}
    assert api.posts() == []


def test_a_refusal_or_a_failure_is_an_error_in_words_and_never_raises():
    assert "write access" in pf.open_after_push(FakeGitHub(create_status=403, create_body={"message": "Resource not accessible"}), make(), "m")["error"]
    assert "error 422" in pf.open_after_push(FakeGitHub(create_status=422, create_body={"message": "Validation Failed"}), make(), "m")["error"]
    assert "could not be reached" in pf.open_after_push(FakeGitHub(raise_on="GET"), make(), "m")["error"]

    class Broken:
        def request(self, *a, **k):
            raise RuntimeError("secret detail that must not be shown")

    result = pf.open_after_push(Broken(), make(), "m")
    assert result == {"error": "The pull request could not be opened just now."}


def test_what_the_model_is_told_never_claims_a_pull_request_that_was_not_opened():
    follow = make()
    assert pf.result_text(None, follow) == ""
    assert "#7 was opened: https://x/7" in pf.result_text({"url": "https://x/7", "number": 7, "existing": False}, follow)
    assert "was already open" in pf.result_text({"url": "https://x/7", "number": 7, "existing": True}, follow)
    failed = pf.result_text({"error": "GitHub could not open the pull request (error 422)."}, follow)
    assert "was NOT opened" in failed and "sonic/fix-x is on GitHub" in failed


# ---- the runner job -------------------------------------------------------------------------------------------------------------------------------------

def connect_runner(db, username="jack"):
    code = rj.create_pairing(db, username, "laptop")["code"]
    record = rj.authenticate(db, rj.exchange_pairing(db, code)["key"])
    rj.hello(db, record, {"version": "0.2.0", "root_name": "demo", "checks": [{"name": "tests", "command": "pytest -q"}]})
    return record, db[rj.RUNNERS].find_one({"_id": record["_id"]})


LINE = "git switch -c sonic/fix-x && git add -u && git commit -m 'fix: stop the loop' && git push -u origin sonic/fix-x"


def test_the_card_sentence_is_stored_and_shown_and_never_sent_to_the_runner():
    db = FakeDB()
    record, runner = connect_runner(db)
    job = rj.create_command_job(db, "jack", record["_id"], LINE, None, None, "Then Sonic opens a pull request on GitHub.")
    assert job["follow_up_text"] == "Then Sonic opens a pull request on GitHub."
    assert rj.public_job(db[rj.JOBS].find_one({"_id": job["id"]}))["follow_up_text"] == job["follow_up_text"]
    rj.approve_job(db, "jack", job["id"], confirmed=True)
    claimed = rj.claim_next(db, record)
    assert claimed["job_id"] == job["id"] and claimed["line"]
    assert "follow_up" not in json.dumps(claimed).lower() and "pull request" not in json.dumps(claimed).lower(), "the runner is only ever sent the command"


def test_a_job_without_a_follow_up_says_so_with_an_empty_string():
    db = FakeDB()
    record, _ = connect_runner(db)
    job = rj.create_command_job(db, "jack", record["_id"], "git status")
    assert job["follow_up_text"] == ""


def test_the_same_line_is_reused_only_with_the_same_follow_up():
    db = FakeDB()
    record, _ = connect_runner(db)
    first, reused = rj.create_or_reuse_command_job(db, "jack", record["_id"], LINE, None, None, "Then A.")
    again, reused_again = rj.create_or_reuse_command_job(db, "jack", record["_id"], LINE, None, None, "Then A.")
    assert reused is False and reused_again is True and again["id"] == first["id"]
    with pytest.raises(rj.RunnerError):        # a different follow-up is a different card, and one card at a time is the runner's rule
        rj.create_or_reuse_command_job(db, "jack", record["_id"], LINE, None, None, "Then B.")


# ---- through the tool agent ------------------------------------------------------------------------------------------------------------------------------

def the_person(db, record, exit_code=0, confirm=True, timed_out=False, run_seconds=0.2):
    """The other side: presses Run (with the stronger confirmation) on the card and has the runner report."""
    def play():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = db[rj.JOBS].find({"runner_id": record["_id"], "status": rj.AWAITING})
            if jobs:
                rj.approve_job(db, "jack", jobs[0]["_id"], confirmed=confirm)
                rj.claim_next(db, record)
                time.sleep(run_seconds)
                rj.complete_job(db, record, jobs[0]["_id"], {"exit_code": exit_code, "output_tail": "pushed" if exit_code == 0 else "rejected", "duration": 1.0, "timed_out": timed_out})
                return
            time.sleep(0.05)

    thread = threading.Thread(target=play, daemon=True)
    thread.start()
    return thread


def decision(**args):
    return {"tool_action": "propose_git_commit", "args": {"message": "fix: stop the loop", "new_branch": "sonic/fix-x", "paths": ["a.py"], **args}}


async def drive(monkeypatch, decisions, *, db, github, token="ghp_own_token", git_branch="feature/current", seen_tokens=None, counts=None):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_db", lambda: db)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    from backend.utils import user_settings_utils

    monkeypatch.setattr(user_settings_utils, "get_user_github_token", lambda username, *a, **k: token)

    def factory(given):
        if seen_tokens is not None:
            seen_tokens.append(given)
        return github

    monkeypatch.setattr(gc, "GitHubApi", factory)
    if counts is not None:
        monkeypatch.setattr(self_counters, "record_turn_counts", lambda c, now=None: counts.append(dict(c)))
    seen = {"observations": []}

    async def fake_run(**kwargs):
        for one in decisions:
            seen["observations"].append(await kwargs["act"](one))
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_run)
    state = _state("commit and push and open a PR", username="jack")
    state["runner_cards"] = True
    state["git_branch"] = git_branch
    await aw.tool_agent_node(state)
    seen["cards"] = [data for name, data in [(c.args[0], c.args[1]) for c in emit.call_args_list] if name == "runner_check"]
    return seen


def test_one_card_one_approval_then_the_pull_request_is_opened_with_the_persons_own_token(monkeypatch):
    db, github, tokens, counts = FakeDB(), FakeGitHub(), [], []
    record, _ = connect_runner(db)
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True, pr_title="Stop the plan loop")], db=db, github=github, seen_tokens=tokens, counts=counts))
    helper.join(timeout=5)
    (card,) = seen["cards"]
    assert "git push -u origin sonic/fix-x" in card["job"]["command_shown"]
    assert "Stop the plan loop" in card["job"]["follow_up_text"] and "into main" in card["job"]["follow_up_text"] and "your own GitHub token" in card["job"]["follow_up_text"]
    assert card["job"]["needs_confirm"] is True, "the strong confirmation is the approval for all of it"
    result = seen["observations"][0]
    assert "exit code 0" in result and f"Pull request #7 was opened: https://github.com/{REPO}/pull/7" in result
    (post,) = github.posts()
    assert post[2]["title"] == "Stop the plan loop" and post[2]["head"] == "sonic/fix-x" and post[2]["base"] == "main"
    assert set(tokens) == {"ghp_own_token"}, "only the person's own token ever reached GitHub"
    assert {"commit_pr_one_card_shown": 1} in counts and {"commit_pr_one_card_opened": 1} in counts and not any("commit_pr_one_card_failed" in c for c in counts)


def test_a_push_that_did_not_succeed_opens_nothing_and_says_so(monkeypatch):
    db, github, counts = FakeDB(), FakeGitHub(), []
    record, _ = connect_runner(db)
    helper = the_person(db, record, exit_code=1)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github, counts=counts))
    helper.join(timeout=5)
    assert "The pull request was NOT opened, because the push did not finish successfully." in seen["observations"][0]
    assert github.posts() == [] and not any("commit_pr_one_card_opened" in c for c in counts)


def test_a_run_that_timed_out_opens_nothing_even_if_it_reported_a_zero_exit(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    helper = the_person(db, record, exit_code=0, timed_out=True)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github))
    helper.join(timeout=5)
    assert "NOT opened" in seen["observations"][0] and github.posts() == []


def test_a_pull_request_from_the_branch_the_folder_is_already_on_uses_that_branch_as_its_head(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True, new_branch=None)], db=db, github=github, git_branch="feature/current"))
    helper.join(timeout=5)
    assert "origin HEAD" in seen["cards"][0]["job"]["command_shown"]
    (post,) = github.posts()
    assert post[2]["head"] == "feature/current", "the pull request is from the branch the push put on GitHub, not a guess"


def test_with_no_new_branch_and_no_known_current_branch_nothing_is_shown(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True, new_branch=None)], db=db, github=github, git_branch=""))
    assert seen["observations"][0].startswith("ERROR") and "which branch" in seen["observations"][0]
    assert seen["cards"] == []


def test_a_push_still_running_when_the_first_wait_ends_is_waited_for_so_the_cards_promise_is_kept(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.8)
    helper = the_person(db, record, run_seconds=2.0)          # approved at once, and still running when the first wait ends
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github))
    helper.join(timeout=8)
    assert "was opened" in seen["observations"][0] and len(github.posts()) == 1


def test_a_card_nobody_pressed_run_on_opens_nothing(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.6)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github))
    assert "NOT opened" in seen["observations"][0] and github.posts() == []


def test_a_pull_request_that_cannot_be_opened_after_a_good_push_says_the_push_worked(monkeypatch):
    db, github, counts = FakeDB(), FakeGitHub(create_status=422, create_body={"message": "Validation Failed"}), []
    record, _ = connect_runner(db)
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github, counts=counts))
    helper.join(timeout=5)
    result = seen["observations"][0]
    assert "was NOT opened" in result and "is on GitHub" in result and "was opened:" not in result
    assert {"commit_pr_one_card_failed": 1} in counts and not any("commit_pr_one_card_opened" in c for c in counts)


def test_an_open_pull_request_is_reported_and_a_second_one_is_not_made(monkeypatch):
    db, github = FakeDB(), FakeGitHub(open_prs=[{"number": 5, "base": {"ref": "main"}}])
    record, _ = connect_runner(db)
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=github))
    helper.join(timeout=5)
    assert "#5 was already open" in seen["observations"][0] and github.posts() == []


@pytest.mark.parametrize("scenario", ["no_token", "cannot_push", "no_base", "no_branch_of_its_own"])
def test_a_pull_request_that_could_not_be_opened_is_refused_before_any_card_exists(monkeypatch, scenario):
    db = FakeDB()
    record, _ = connect_runner(db)
    github = FakeGitHub(can_push=scenario != "cannot_push", base_exists=scenario != "no_base")
    args = {"open_pull_request": True}
    kwargs = {"token": None} if scenario == "no_token" else {}
    if scenario == "no_branch_of_its_own":
        args["new_branch"] = None
        kwargs["git_branch"] = "main"
    seen = run(drive(monkeypatch, [decision(**args)], db=db, github=github, **kwargs))
    assert seen["observations"][0].startswith("ERROR"), seen["observations"][0]
    assert seen["cards"] == [] and db[rj.JOBS].find({}) == [], "nothing was shown and no job exists: the person is never asked to push what could not be followed through"


def test_a_plain_commit_keeps_the_old_path_with_no_github_call_and_no_extra_sentence(monkeypatch):
    db, github, counts = FakeDB(), FakeGitHub(), []
    record, _ = connect_runner(db)
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(push=True)], db=db, github=github, counts=counts))
    helper.join(timeout=5)
    (card,) = seen["cards"]
    assert card["job"]["follow_up_text"] == "" and github.calls == []
    assert "Pull request" not in seen["observations"][0] and not any(k.startswith("commit_pr_one_card") for c in counts for k in c)


def test_a_pull_request_is_not_opened_in_a_message_that_read_what_other_people_wrote(monkeypatch):
    db, github = FakeDB(), FakeGitHub()
    record, _ = connect_runner(db)
    web = {"tool_action": "read_url", "args": {"url": "https://example.com/post"}}
    seen = run(drive(monkeypatch, [web, decision(open_pull_request=True)], db=db, github=github))
    assert "not opened or merged in the same message" in seen["observations"][1]
    assert seen["cards"] == [] and github.calls == []


def test_looking_at_git_status_first_does_not_block_it_because_that_text_is_the_persons_own(monkeypatch):
    assert "run_local_command" in aw._UNTRUSTED_CONTENT_TOOLS and "run_local_command" not in aw._THIRD_PARTY_TEXT_TOOLS
    for name in ("web_search", "read_url", "read_gmail_message", "read_drive_file", "search_library", "browser_navigate"):
        assert name in aw._THIRD_PARTY_TEXT_TOOLS, name


# ---- the tool's declaration and menu --------------------------------------------------------------------------------------------------------------------

def test_the_model_can_ask_for_the_pull_request_and_is_told_it_is_one_card():
    properties = native_tools.REGISTRY["propose_git_commit"]["properties"]
    assert {"open_pull_request", "pr_title", "pr_base", "pr_draft"} <= set(properties)
    assert native_tools.REGISTRY["propose_git_commit"]["required"] == ["message"], "the pull request is always optional"
    text = native_tools.NATIVE_DESCRIPTIONS["propose_git_commit"]
    assert "open_pull_request=true" in text and "ONE approval" in text and "never say it was opened unless the result says so" in text
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    menu = source[source.index('"- propose_git_commit — args:'):source.index('"- propose_merge — args:')]
    assert "open_pull_request (optional" in menu and "ONE card, ONE approval" in menu
