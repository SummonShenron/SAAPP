"""A conversation about a project OUTSIDE the connected repo (backend/utils/repo_scope.py, agent_workflow.py): once the person says so, the repo tools are off until they say they are back.
Before this, every rule that picks the repo tools said "code-like, or a short follow-up to a coding turn, is repo work", so a person who said "this isn't about the repo" still had each later message sent to
list_repo_tree and search_literal (2026-11-20, Jack: tool turns wasted looking for files that were never in the repo)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage as H

from backend.services import agent_workflow as aw
from backend.tests.test_tool_agent_node import GROUNDING_CHECK_MARKER, _llm_response, _setup_github_repo, run_async
from backend.utils import repo_scope as rs
from backend.utils import self_counters

SAYS_OUTSIDE = [
    "this isn't about the repo, it's my other project",
    "that's not about this repo at all",
    "this is not in the connected folder",
    "it has nothing to do with this repo",
    "nothing to do with the codebase",
    "this is outside the connected repo",
    "outside of the repo, I'm building a game",
    "other than this repo, I have another app",
    "this is for my other project",
    "I'm asking about a different project I'm building",
    "can you help with another project of mine?",
    "it's unrelated to this repo",
    "the project isn't connected to sonic",
    "That is NOT ABOUT THE REPO.",
]
SAYS_NOTHING = [
    "should I split this into a separate repo?",
    "fix the login in this repo",
    "how does the router work",
    "the app isn't working",
    "I'm not sure about this approach",
    "what is a monorepo?",
    "not about to give up on this",
    "my other friend is a dentist",
    "",
    "   ",
    "this repo is big",
]
SAYS_BACK = [
    "back to the repo",
    "ok back to this repo now",
    "okay, the repo again",
    "check the repo for that",
    "look in the codebase",
    "search the connected folder for it",
    "read the repository",
    "see github.com/facebook/react",
]


@pytest.mark.parametrize("text", SAYS_OUTSIDE)
def test_the_words_that_put_a_conversation_outside_the_repo(text):
    assert rs.says_outside(text) and not rs.says_back(text), text


@pytest.mark.parametrize("text", SAYS_NOTHING)
def test_ordinary_talk_about_repos_and_projects_says_nothing(text):
    assert not rs.says_outside(text) and not rs.says_back(text), text


@pytest.mark.parametrize("text", SAYS_BACK)
def test_the_words_that_bring_the_repo_back(text):
    assert rs.says_back(text) and not rs.says_outside(text), text


def test_a_message_that_orders_a_look_and_says_it_is_elsewhere_stays_outside():
    assert rs.says_outside("look at my project, it's not in this repo")
    assert rs.says_outside("check the repo, it's not in this repo")


def test_the_last_word_decides_and_a_silent_message_changes_nothing():
    said = H(content="this isn't about the repo, it's my other project")
    quiet = H(content="how should I structure the auth middleware?")
    back = H(content="ok back to the repo")
    assert rs.outside_repo([said]) is True
    assert rs.outside_repo([said, AIMessage(content="sure"), quiet]) is True, "it lasts the whole conversation"
    assert rs.outside_repo([said, quiet, back]) is False
    assert rs.outside_repo([back, said]) is True
    assert rs.outside_repo([said, back, said, quiet]) is True
    assert rs.outside_repo([quiet]) is False and rs.outside_repo([]) is False


def test_only_what_the_person_wrote_counts_not_the_assistants_replies():
    ai = AIMessage(content="this isn't about the repo, so I won't look in it")
    assert rs.outside_repo([H(content="hello"), ai]) is False
    assert rs.outside_repo([H(content="ok back to the repo"), AIMessage(content="this is outside the connected repo")]) is False


def test_message_content_can_be_a_list_of_blocks_and_nothing_raises():
    blocks = SimpleNamespace(type="human", content=[{"type": "text", "text": "this isn't about the repo"}])
    assert rs.outside_repo([blocks]) is True
    for garbage in (None, [None], [object()], [SimpleNamespace(type="human", content=None)], 5):
        assert rs.outside_repo(garbage) is False, garbage
    assert rs.says_outside(None) is False and rs.says_back(None) is False


def test_menu_lines_lose_only_the_named_tools():
    menu = ["- list_repo_tree — no args", "- read_repo_file — args: path", "- web_search — args: query", "- search_library — args: query", "- propose_commit — args: message"]
    kept = rs.without_repo_lines(menu, aw._REPO_ONLY_TOOLS)
    assert kept == ["- web_search — args: query", "- search_library — args: query"]


def test_every_tool_that_reads_searches_or_changes_the_repo_is_in_the_removed_set():
    for name in ("list_repo_tree", "read_repo_file", "search_code", "find_file", "trace_symbol", "search_literal", "diff_branches", "list_commits", "list_pull_requests",
                 "propose_local_edits", "propose_commit", "propose_git_commit", "propose_merge"):
        assert name in aw._REPO_ONLY_TOOLS, name
    for name in ("web_search", "search_library", "read_library_file", "search_memory", "read_url", "run_snippet"):
        assert name not in aw._REPO_ONLY_TOOLS, name


def test_the_counter_exists():
    assert "repo_scope_outside_turns" in self_counters.KEYS


# ---- the state helper -----------------------------------------------------------------------------------------------------------------------------

def test_the_state_helper_follows_the_conversation_and_a_named_github_repo_brings_the_repo_back():
    said = [H(content="this isn't about the repo, it's my other project"), AIMessage(content="ok"), H(content="how do I structure the auth middleware?")]
    assert aw._outside_repo({"messages": said}) is True
    assert aw._outside_repo({"messages": said + [AIMessage(content="..."), H(content="compare it with facebook/react")]}) is False
    assert aw._outside_repo({"messages": []}) is False and aw._outside_repo({}) is False


# ---- the reasoner and the router ---------------------------------------------------------------------------------------------------------------------

def _classified_as(**flags):
    return AsyncMock(return_value=SimpleNamespace(content=json.dumps({"needs_conversation": False, **flags})))


OUTSIDE_THREAD = [H(content="this isn't about the repo, it's my other project"), AIMessage(content="Got it."), H(content="how should I structure the auth middleware in it?")]
PLAIN_THREAD = [H(content="how should I structure the auth middleware in it?")]


@pytest.fixture
def counts(monkeypatch):
    seen = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda c, now=None: seen.append(dict(c)))
    return seen


@run_async
async def test_the_repo_flag_is_cleared_and_the_turn_goes_to_plain_conversation(monkeypatch, counts):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_github_search=True, follow_up_intent=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    result = await aw.reasoner_node({"username": "jack", "messages": list(OUTSIDE_THREAD)})
    assert result["reasoner_flags"]["needs_github_search"] is False
    assert result["reasoner_flags"]["needs_conversation"] is True
    plan = aw.build_agent_plan("conversational", result)
    assert "tool_agent" not in plan["agents"] and "conversational" in plan["agents"], plan
    assert {"repo_scope_outside_turns": 1} in counts


@run_async
async def test_without_the_statement_the_same_turn_still_goes_to_the_repo_tools(monkeypatch, counts):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_github_search=True, follow_up_intent=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    result = await aw.reasoner_node({"username": "jack", "messages": list(PLAIN_THREAD)})
    assert result["reasoner_flags"]["needs_github_search"] is True
    assert "tool_agent" in aw.build_agent_plan("conversational", result)["agents"]
    assert not any("repo_scope_outside_turns" in c for c in counts), "nothing is counted when the repo was not switched off"


@run_async
async def test_other_tools_the_turn_needs_stay_and_the_repo_flag_alone_goes(monkeypatch, counts):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_github_search=True, needs_web_search=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    result = await aw.reasoner_node({"username": "jack", "messages": list(OUTSIDE_THREAD)})
    flags = result["reasoner_flags"]
    assert flags["needs_github_search"] is False and flags["needs_web_search"] is True
    assert flags["needs_conversation"] is False, "a turn that still needs the web is not forced into plain conversation"
    assert "tool_agent" in aw.build_agent_plan("conversational", result)["agents"]


@run_async
async def test_the_edit_backstop_does_not_pull_an_outside_project_back_to_the_repo(monkeypatch, counts):
    """With a folder connected, an edit-shaped message is sent to the tool agent whatever the classifier said. Not when the person said the project is elsewhere."""
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_conversation=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    thread = [H(content="this isn't about the repo, it's my other project"), AIMessage(content="ok"), H(content="change the header text in Chat.tsx and fix the login")]
    result = await aw.reasoner_node({"username": "jack", "messages": thread})
    assert not result["reasoner_flags"].get("needs_github_search")
    control = await aw.reasoner_node({"username": "jack", "messages": [H(content="change the header text in Chat.tsx and fix the login")]})
    assert control["reasoner_flags"]["needs_github_search"] is True, "the backstop still works for a person who said nothing"


@run_async
async def test_naming_a_github_repo_brings_the_repo_tools_back(monkeypatch, counts):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_github_search=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    thread = list(OUTSIDE_THREAD) + [AIMessage(content="..."), H(content="how does facebook/react do it?")]
    result = await aw.reasoner_node({"username": "jack", "messages": thread})
    assert result["reasoner_flags"]["needs_github_search"] is True
    assert not any("repo_scope_outside_turns" in c for c in counts)


# ---- the tool agent's menu ---------------------------------------------------------------------------------------------------------------------------

async def _first_prompt(monkeypatch, messages):
    """Runs the real tool agent on a thread with a scripted model; returns the first prompt it sent."""
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": fallback)       # the helper above fakes a repo for every text; here the real "no repo named" answer is wanted
    prompts = []

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        prompts.append(prompt)
        return _llm_response(action="final", answer="Put the auth check in one middleware.", show_work=True)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    await aw.tool_agent_node({"username": "jack", "messages": messages, "documents": []})
    assert prompts, "the model was never called"
    return prompts[0]


@run_async
async def test_an_outside_conversation_is_told_so_and_offered_no_repo_tool(monkeypatch):
    prompt = await _first_prompt(monkeypatch, list(OUTSIDE_THREAD))
    assert "NOT THIS REPO" in prompt
    for name in ("list_repo_tree", "read_repo_file", "search_literal", "find_file", "search_code", "propose_commit", "diff_branches"):
        assert f"- {name} —" not in prompt, name


@run_async
async def test_the_same_question_without_the_statement_still_gets_the_repo_tools(monkeypatch):
    prompt = await _first_prompt(monkeypatch, list(PLAIN_THREAD))
    assert "NOT THIS REPO" not in prompt
    for name in ("list_repo_tree", "read_repo_file", "search_literal"):
        assert f"- {name} —" in prompt, name


@run_async
async def test_bringing_the_repo_back_gives_the_tools_back(monkeypatch):
    thread = list(OUTSIDE_THREAD) + [AIMessage(content="..."), H(content="ok back to the repo, where is the login flow?")]
    prompt = await _first_prompt(monkeypatch, thread)
    assert "NOT THIS REPO" not in prompt and "- read_repo_file —" in prompt
