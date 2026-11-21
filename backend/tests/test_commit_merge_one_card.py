"""Commit, push, open a pull request AND merge it, on one card and in one turn (backend/utils/pr_followup.py, agent_workflow.py propose_git_commit merge_after).

Merging was a second card and a second approval on purpose, and merging into the default branch deploys, so this is guarded: admins only; only when the person asked for the merge in their own words in this request;
the card says so; the checks are waited for and a failed or unfinished one means no merge; the commit merged is the one that was checked; GitHub's own rules still decide; and every refusal leaves the pull request
open and says why. Nothing here touches a real GitHub: a fake serves the calls."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import git_commit as gc
from backend.services import native_tools
from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_commit_pr_one_card import FakeGitHub, REPO, connect_runner, decision, the_person
from backend.tests.test_tool_agent_node import _setup_github_repo, _state
from backend.utils import pr_followup as pf
from backend.utils import runner_jobs as rj
from backend.utils import self_counters

SHA = "a" * 40
MERGED_SHA = "d" * 40


def run(coro):
    return asyncio.run(coro)


def follow(**over):
    return {"repo": REPO, "head": "sonic/fix-x", "base": "main", "title": "fix: stop the loop", "draft": False, "merge": "squash", **over}


# ---- asking for it -----------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("messages", [
    ["commit and push and open a PR and merge it"],
    ["commit, push, open a pull request, then merge when green"],
    ["merge it too"],
    ["please merge it"],
    ["open a PR", "ok and merge it"],
    ["squash-merge it when ci passes"],
    ["commit this & merge"],
])
def test_the_words_that_ask_for_a_merge(messages):
    assert pf.asks_to_merge(messages), messages


@pytest.mark.parametrize("messages", [
    ["commit and push and open a PR"],
    ["commit and open a PR but don't merge it"],
    ["open a PR without merging"],
    ["do not merge anything yet"],
    ["fix the merge conflicts and push"],
    ["we hit a merge conflict, commit the fix"],
    ["what is a merge commit?"],
    ["open a PR and merge it", "actually don't merge"],
    ["merge it", "never mind, no merge"],
    [],
    [""],
])
def test_talk_about_merging_and_a_merge_taken_back_is_not_a_request(messages):
    assert not pf.asks_to_merge(messages), messages


def test_only_the_last_two_of_the_persons_messages_count():
    assert not pf.asks_to_merge(["open a PR and merge it", "something else", "and another thing"])


# ---- arranging it ------------------------------------------------------------------------------------------------------------------------------------

def build(**args):
    return pf.build({"merge_after": True, **args}, repo=REPO, head="sonic/fix-x", default_base="main", subject="fix: stop the loop")


def test_a_merge_is_part_of_the_arrangement_only_when_asked_for_and_the_method_defaults_to_squash():
    assert build()["merge"] == "squash"
    assert build(merge_method="Rebase")["merge"] == "rebase"
    assert pf.build({}, repo=REPO, head="sonic/fix-x", default_base="main", subject="fix: stop the loop")["merge"] is None


def test_a_bad_method_and_a_draft_are_refused_before_any_card_exists():
    with pytest.raises(pf.PrFollowupError, match="squash, merge or rebase"):
        build(merge_method="fast-forward")
    with pytest.raises(pf.PrFollowupError, match="draft"):
        build(pr_draft=True)


def test_the_card_says_everything_the_merge_will_do_and_when_it_will_not():
    text = pf.card_text(follow())
    for part in ("squash-merges it into main", "once its checks pass", "minutes", "cannot be taken back here", "Nothing is merged if a check fails or is still running",
                 "if the pull request changes after it is opened", "conflicts, required reviews"):
        assert part in text, part
    assert "merges" not in pf.card_text(follow(merge=None)), "a card for a plain pull request says nothing about a merge"


def test_the_whole_card_sentence_is_kept_and_shown_not_cut_off_where_the_merge_clause_would_be():
    text = pf.card_text(follow(title="x" * 120))
    assert len(text) > 400, "longer than the old limit"
    assert len(text) < rj.FOLLOW_UP_CHARS
    db = FakeDB()
    record, _ = connect_runner(db)
    job = rj.create_command_job(db, "jack", record["_id"], "git status", None, None, text)
    assert job["follow_up_text"] == text, "the merge clause is on the card: it is part of what the person approves"


# ---- the checks --------------------------------------------------------------------------------------------------------------------------------------

class Checks:
    def __init__(self, runs=None, legacy=None, runs_status=200):
        self.runs, self.legacy, self.runs_status = runs or [], legacy or {"state": "pending", "total_count": 0}, runs_status

    def request(self, method, path, body=None):
        if "/check-runs" in path:
            return (200, {"check_runs": self.runs}) if self.runs_status == 200 else (self.runs_status, {})
        if path.endswith("/status"):
            return 200, self.legacy
        raise AssertionError(path)


def check(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_checks_are_passed_failed_pending_or_none_by_what_github_reports():
    assert pf.checks_state(Checks([check("tests"), check("lint")]), REPO, SHA) == {"state": "passed", "failed": [], "pending": []}
    failed = pf.checks_state(Checks([check("tests", conclusion="failure"), check("lint")]), REPO, SHA)
    assert failed["state"] == "failed" and failed["failed"] == ["tests"]
    pending = pf.checks_state(Checks([check("tests"), check("build", "in_progress", None)]), REPO, SHA)
    assert pending["state"] == "pending" and pending["pending"] == ["build"]
    assert pf.checks_state(Checks([]), REPO, SHA)["state"] == "none"


def test_skipped_and_neutral_checks_are_not_failures_and_cancelled_or_timed_out_ones_are():
    assert pf.checks_state(Checks([check("a", conclusion="skipped"), check("b", conclusion="neutral")]), REPO, SHA)["state"] == "passed"
    for bad in ("cancelled", "timed_out", "startup_failure", "action_required"):
        assert pf.checks_state(Checks([check("a", conclusion=bad)]), REPO, SHA)["state"] == "failed", bad


def test_a_failure_wins_over_a_pending_check_so_a_red_run_is_not_waited_on():
    state = pf.checks_state(Checks([check("tests", conclusion="failure"), check("slow", "queued", None)]), REPO, SHA)
    assert state["state"] == "failed" and state["pending"] == ["slow"]


def test_the_older_commit_statuses_count_too():
    assert pf.checks_state(Checks([], {"state": "failure", "total_count": 2}), REPO, SHA) == {"state": "failed", "failed": ["commit statuses"], "pending": []}
    assert pf.checks_state(Checks([], {"state": "pending", "total_count": 1}), REPO, SHA)["state"] == "pending"
    assert pf.checks_state(Checks([], {"state": "success", "total_count": 1}), REPO, SHA)["state"] == "passed"
    assert pf.checks_state(Checks([], {"state": "pending", "total_count": 0}), REPO, SHA)["state"] == "none", "a pending state with no statuses is GitHub's default, not a status"


def test_checks_github_will_not_show_raise_in_words_and_so_never_merge():
    with pytest.raises(gc.GitCommitError):
        pf.checks_state(Checks(runs_status=403), REPO, SHA)


@pytest.mark.parametrize("state, elapsed, expected", [
    ({"state": "passed"}, 0, "merge"),
    ({"state": "failed"}, 0, "failed"),
    ({"state": "failed"}, 9999, "failed"),
    ({"state": "pending"}, 10, "wait"),
    ({"state": "pending"}, 600, "timeout"),
    ({"state": "none"}, 10, "wait"),
    ({"state": "none"}, 60, "merge"),
])
def test_what_to_do_after_looking_at_the_checks(state, elapsed, expected):
    assert pf.decide(state, elapsed, 600, 60) == expected


# ---- merging ----------------------------------------------------------------------------------------------------------------------------------------

class Pull:
    """A pull request as GitHub gives it, and what the merge call answers."""

    def __init__(self, sha=SHA, state="open", merged=False, draft=False, mergeable=True, merge_status=200, merge_body=None, raise_on=None):
        self.sha, self.state, self.merged, self.draft, self.mergeable = sha, state, merged, draft, mergeable
        self.merge_status, self.merge_body, self.raise_on = merge_status, merge_body, raise_on
        self.puts = []

    def request(self, method, path, body=None):
        if self.raise_on == method:
            raise RuntimeError("boom with a secret detail")
        if method == "GET" and path == f"/repos/{REPO}/pulls/7":
            return 200, {"state": self.state, "merged": self.merged, "draft": self.draft, "mergeable": self.mergeable, "title": "fix: stop the loop", "head": {"sha": self.sha, "ref": "sonic/fix-x"}, "base": {"ref": "main"}}
        if method == "PUT" and path == f"/repos/{REPO}/pulls/7/merge":
            self.puts.append(body)
            return self.merge_status, self.merge_body if self.merge_body is not None else {"merged": True, "sha": MERGED_SHA}
        raise AssertionError(f"unexpected {method} {path}")


def test_a_pull_request_is_merged_with_its_checked_commit_pinned_and_the_method_asked_for():
    api = Pull()
    out = pf.merge_now(api, follow(merge="rebase"), 7, SHA)
    assert out == {"merged": True, "sha": MERGED_SHA, "method": "rebase"}
    (put,) = api.puts
    assert put["sha"] == SHA and put["merge_method"] == "rebase"


@pytest.mark.parametrize("pull, words", [
    (Pull(state="closed"), "was closed"),
    (Pull(draft=True), "is a draft"),
    (Pull(sha="b" * 40), "new commits since its checks were looked at"),
    (Pull(mergeable=False), "conflicts with main"),
])
def test_a_pull_request_that_is_not_what_was_checked_is_not_merged_and_the_merge_call_is_never_made(pull, words):
    out = pf.merge_now(pull, follow(), 7, SHA)
    assert words in out["error"] and pull.puts == []


def test_one_that_was_already_merged_is_reported_not_merged_again():
    api = Pull(merged=True)
    out = pf.merge_now(api, follow(), 7, SHA)
    assert out["merged"] is True and out["already"] is True and api.puts == []


def test_github_refusing_is_an_error_in_words_and_never_raises():
    refused = Pull(merge_status=405, merge_body={"message": "Required status check is expected."})
    assert "would not merge it yet" in pf.merge_now(refused, follow(), 7, SHA)["error"]
    assert "changed after" in pf.merge_now(Pull(merge_status=409, merge_body={"message": "Head branch was modified."}), follow(), 7, SHA)["error"]
    assert pf.merge_now(Pull(raise_on="GET"), follow(), 7, SHA) == {"error": "The merge could not be done just now, and nothing was merged."}


def test_what_the_model_is_told_never_says_merged_unless_it_was_and_always_says_the_pull_request_is_still_open_otherwise():
    f = follow()
    assert "was MERGED into main (squash) as ddddddd" in pf.merge_result_text("merged", f, 7, {"sha": MERGED_SHA, "method": "squash"})
    assert "(it was already merged)" in pf.merge_result_text("merged", f, 7, {"sha": "", "method": "squash", "already": True})
    for kind, detail in (("failed", {"failed": ["tests"]}), ("timeout", {"pending": ["build"]}), ("error", {"error": "GitHub refused."})):
        text = pf.merge_result_text(kind, f, 7, detail)
        assert "NOT merged" in text and "MERGED into" not in text and "nothing on main changed" in text, kind
    assert "tests" in pf.merge_result_text("failed", f, 7, {"failed": ["tests"]}) and "build" in pf.merge_result_text("timeout", f, 7, {"pending": ["build"]})


# ---- through the tool agent --------------------------------------------------------------------------------------------------------------------------

class World(FakeGitHub):
    """The fake GitHub of the pull request tests, plus what the merge step reads: the pull request, its checks, and the merge call."""

    def __init__(self, checks=None, pull=None, **kw):
        super().__init__(**kw)
        self.check_sequence = list(checks if checks is not None else [[check("tests")]])
        self.pull = pull or Pull()
        self.polls = 0
        self.statuses = {"state": "pending", "total_count": 0}

    def request(self, method, path, body=None):
        if "/check-runs" in path:
            self.calls.append((method, path, body))
            self.polls += 1
            runs = self.check_sequence[min(self.polls - 1, len(self.check_sequence) - 1)]
            return 200, {"check_runs": runs}
        if path.endswith("/status") and "/commits/" in path:
            self.calls.append((method, path, body))
            return 200, self.statuses
        if (method == "GET" and path == f"/repos/{REPO}/pulls/7") or (method == "PUT" and path.endswith("/merge")):
            self.calls.append((method, path, body))
            return self.pull.request(method, path, body)
        return super().request(method, path, body)

    def merges(self):
        return self.pull.puts


async def drive(monkeypatch, decisions, *, db, github, message="commit, push, open a pull request and merge it", admin=True, token="ghp_own_token", git_branch="feature/current", counts=None, events=None):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"] if admin else ["Guest"])
    monkeypatch.setattr(aw, "get_db", lambda: db)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    from backend.utils import user_settings_utils

    monkeypatch.setattr(user_settings_utils, "get_user_github_token", lambda username, *a, **k: token)
    monkeypatch.setattr(gc, "GitHubApi", lambda given: github)
    monkeypatch.setattr(pf, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(pf, "NO_CHECKS_GRACE_SECONDS", 0.08)
    monkeypatch.setattr(pf, "MAX_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 4)          # a refusal that was removed would show a card nobody answers: fail in seconds, not after the runner's ten minutes
    if counts is not None:
        monkeypatch.setattr(self_counters, "record_turn_counts", lambda c, now=None: counts.append(dict(c)))
    seen = {"observations": []}

    async def fake_run(**kwargs):
        for one in decisions:
            seen["observations"].append(await kwargs["act"](one))
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_run)
    state = _state(message, username="jack")
    state["runner_cards"] = True
    state["git_branch"] = git_branch
    await aw.tool_agent_node(state)
    emitted = [(c.args[0], c.args[1]) for c in emit.call_args_list]
    seen["cards"] = [data for name, data in emitted if name == "runner_check"]
    seen["waiting"] = [data for name, data in emitted if name == "trace_detail" and data.get("title") == "Waiting for the checks..."]
    return seen


def merge_decision(**args):
    return decision(open_pull_request=True, merge_after=True, **args)


def go(monkeypatch, world, *, exit_code=0, **kw):
    db = FakeDB()
    record, _ = connect_runner(db)
    helper = the_person(db, record, exit_code=exit_code)
    seen = run(drive(monkeypatch, [merge_decision()], db=db, github=world, **kw))
    helper.join(timeout=5)
    return seen


def test_one_card_one_turn_commit_push_pull_request_and_merge_once_the_checks_pass(monkeypatch):
    world, counts = World(checks=[[check("tests", "in_progress", None)], [check("tests", "in_progress", None)], [check("tests")]]), []
    seen = go(monkeypatch, world, counts=counts)
    (card,) = seen["cards"]
    assert "squash-merges it into main" in card["job"]["follow_up_text"] and "once its checks pass" in card["job"]["follow_up_text"], "the merge is on the card the person approves"
    assert card["job"]["needs_confirm"] is True
    result = seen["observations"][0]
    assert f"Pull request #7 was opened: https://github.com/{REPO}/pull/7" in result and "was MERGED into main (squash) as ddddddd" in result
    (put,) = world.merges()
    assert put["sha"] == SHA and put["merge_method"] == "squash" and put["commit_title"].endswith("(#7)")
    assert world.polls == 3, "it looked at the checks until they were done, and merged only then"
    assert seen["waiting"] and "Nothing is merged unless they pass" in seen["waiting"][0]["detail"], "the wait is visible in the trace"
    assert {"commit_merge_merged": 1} in counts and not any("commit_merge_not_merged" in c for c in counts)


def test_a_failed_check_means_no_merge_and_the_pull_request_stays_open(monkeypatch):
    world, counts = World(checks=[[check("tests", conclusion="failure"), check("lint")]]), []
    seen = go(monkeypatch, world, counts=counts)
    result = seen["observations"][0]
    assert "was opened" in result and "NOT merged: these checks failed: tests" in result and "MERGED into" not in result
    assert world.merges() == []
    assert {"commit_merge_not_merged": 1} in counts and not any("commit_merge_merged" in c for c in counts)


def test_checks_still_running_after_the_longest_wait_mean_no_merge(monkeypatch):
    world = World(checks=[[check("build", "in_progress", None)]])
    seen = go(monkeypatch, world)
    assert "NOT merged: build had not finished after the longest wait" in seen["observations"][0] and world.merges() == []


def test_a_repository_with_no_checks_at_all_is_merged_after_the_grace_period_not_before(monkeypatch):
    world = World(checks=[[]])
    seen = go(monkeypatch, world)
    assert "was MERGED" in seen["observations"][0] and len(world.merges()) == 1
    assert world.polls >= 2, "it waited for checks to appear before believing there were none"


def test_new_commits_on_the_pull_request_after_its_checks_were_looked_at_mean_no_merge(monkeypatch):
    class Moves(Pull):
        def request(self, method, path, body=None):
            code, data = super().request(method, path, body)
            if method == "GET" and "head" in data:
                self.reads = getattr(self, "reads", 0) + 1
                if self.reads >= 2:
                    data = {**data, "head": {**data["head"], "sha": "b" * 40}}          # someone pushed after the checks were looked at
            return code, data

    world = World(pull=Moves())
    seen = go(monkeypatch, world)
    assert "new commits since its checks were looked at" in seen["observations"][0] and world.merges() == []


def test_github_refusing_the_merge_is_reported_and_the_pull_request_is_left_open(monkeypatch):
    world = World(pull=Pull(merge_status=405, merge_body={"message": "Required status check is expected."}))
    seen = go(monkeypatch, world)
    assert "NOT merged: GitHub would not merge it yet" in seen["observations"][0] and "MERGED into" not in seen["observations"][0]


def test_a_pull_request_github_refuses_to_open_is_never_merged_or_waited_on(monkeypatch):
    world = World(create_status=422, create_body={"message": "Validation Failed"})
    seen = go(monkeypatch, world)
    assert "was NOT opened" in seen["observations"][0] and "MERGED into" not in seen["observations"][0]
    assert world.merges() == [] and world.polls == 0, "no checks were looked at and nothing was merged"


def test_merge_after_on_its_own_still_opens_the_pull_request_it_needs(monkeypatch):
    db = FakeDB()
    record, _ = connect_runner(db)
    world = World()
    helper = the_person(db, record)
    only_merge = {"tool_action": "propose_git_commit", "args": {"message": "fix: stop the loop", "new_branch": "sonic/fix-x", "paths": ["a.py"], "merge_after": True}}
    seen = run(drive(monkeypatch, [only_merge], db=db, github=world))
    helper.join(timeout=5)
    (card,) = seen["cards"]
    assert "git push -u origin sonic/fix-x" in card["job"]["command_shown"], "a merge needs the branch pushed"
    assert "opens a pull request" in card["job"]["follow_up_text"] and "squash-merges it" in card["job"]["follow_up_text"]
    assert len(world.posts()) == 1 and len(world.merges()) == 1


def test_a_push_that_did_not_succeed_opens_nothing_and_merges_nothing(monkeypatch):
    world = World()
    seen = go(monkeypatch, world, exit_code=1)
    assert "The pull request was NOT opened" in seen["observations"][0]
    assert world.posts() == [] and world.merges() == [] and world.polls == 0


# ---- who may ask, and for what -----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["not_an_admin", "never_asked", "asked_not_to", "draft"])
def test_a_merge_that_may_not_be_part_of_this_card_is_refused_before_any_card_exists(monkeypatch, scenario):
    db = FakeDB()
    record, _ = connect_runner(db)
    world = World()
    kwargs = {}
    args = {}
    if scenario == "not_an_admin":
        kwargs["admin"] = False
    elif scenario == "never_asked":
        kwargs["message"] = "commit and push and open a pull request"
    elif scenario == "asked_not_to":
        kwargs["message"] = "commit, push and open a pull request but don't merge it"
    else:
        args["pr_draft"] = True
    seen = run(drive(monkeypatch, [merge_decision(**args)], db=db, github=world, **kwargs))
    assert seen["observations"][0].startswith("ERROR"), seen["observations"][0]
    assert seen["cards"] == [] and db[rj.JOBS].find({}) == [], "nothing was shown and no job exists"
    assert world.merges() == [] and world.polls == 0


def test_the_error_for_a_missing_request_tells_the_model_to_open_the_pull_request_and_offer_the_merge_instead(monkeypatch):
    seen = run(drive(monkeypatch, [merge_decision()], db=FakeDB(), github=World(), message="commit and open a pull request"))
    assert "did not ask for a merge" in seen["observations"][0] and "offer the merge as a next step" in seen["observations"][0]


def test_only_the_persons_own_words_can_ask_for_the_merge_not_what_sonic_wrote_or_read(monkeypatch):
    db = FakeDB()
    record, _ = connect_runner(db)
    from langchain_core.messages import AIMessage, HumanMessage

    _setup_github_repo(monkeypatch)
    state = _state("commit and open a pull request", username="jack")
    state["messages"] = [HumanMessage(content="commit and open a pull request"), AIMessage(content="I can also merge it for you, shall I merge it?")]
    humans = [m.content for m in state["messages"] if getattr(m, "type", "") == "human"]
    assert pf.asks_to_merge(humans) is False, "Sonic offering a merge is not the person asking for one"


def test_a_pull_request_with_merge_is_not_made_in_a_message_that_read_what_other_people_wrote(monkeypatch):
    db = FakeDB()
    record, _ = connect_runner(db)
    world = World()
    web = {"tool_action": "read_url", "args": {"url": "https://example.com/post"}}
    seen = run(drive(monkeypatch, [web, merge_decision()], db=db, github=world))
    assert "not opened or merged in the same message" in seen["observations"][1]
    assert seen["cards"] == [] and world.merges() == [] and world.calls == []


def test_merge_after_alone_counts_as_a_pull_request_and_is_guarded_like_one(monkeypatch):
    """The guard used to look only at open_pull_request: a call with merge_after and nothing else would have walked past it."""
    db = FakeDB()
    record, _ = connect_runner(db)
    world = World()
    web = {"tool_action": "read_url", "args": {"url": "https://example.com/post"}}
    alone = {"tool_action": "propose_git_commit", "args": {"message": "fix: stop the loop", "new_branch": "sonic/fix-x", "paths": ["a.py"], "merge_after": True}}
    seen = run(drive(monkeypatch, [web, alone], db=db, github=world))
    assert "not opened or merged in the same message" in seen["observations"][1] and world.merges() == []


def test_a_plain_pull_request_never_looks_at_checks_or_merges_anything(monkeypatch):
    db = FakeDB()
    record, _ = connect_runner(db)
    world = World()
    helper = the_person(db, record)
    seen = run(drive(monkeypatch, [decision(open_pull_request=True)], db=db, github=world, message="commit, push and open a pull request"))
    helper.join(timeout=5)
    assert "was opened" in seen["observations"][0] and "merge" not in seen["observations"][0].lower().replace("merge_after", "")
    assert world.polls == 0 and world.merges() == []
    assert "merges" not in seen["cards"][0]["job"]["follow_up_text"]


# ---- the declaration --------------------------------------------------------------------------------------------------------------------------------

def test_the_model_can_ask_for_the_merge_and_is_told_it_is_admin_only_and_needs_the_persons_request():
    properties = native_tools.REGISTRY["propose_git_commit"]["properties"]
    assert {"merge_after", "merge_method"} <= set(properties)
    assert native_tools.REGISTRY["propose_git_commit"]["required"] == ["message"]
    text = native_tools.NATIVE_DESCRIPTIONS["propose_git_commit"]
    assert "merge_after=true" in text and "only if they pass" in text and "never say it merged unless the result says so" in text
    assert "admins only" in properties["merge_after"]["description"] and "asked for the merge" in properties["merge_after"]["description"]
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    menu = source[source.index('"- propose_git_commit — args:'):source.index('"- propose_merge — args:')]
    assert "merge_after (optional, ADMINS ONLY" in menu and "ONE card and ONE turn" in menu and "Never say it merged unless the result says so" in menu


def test_the_counters_exist():
    assert {"commit_merge_merged", "commit_merge_not_merged"} <= self_counters.KEYS
