import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _state(question="find the bug in the auth flow", messages=None, username="jack", **overrides):
    return {
        "username": username,
        "messages": messages or [HumanMessage(content=question)],
        "documents": [],
        **overrides,
    }


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


def _http_response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_data or {})
    resp.text = text
    return resp


def _setup_github_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")


# ---------------------------------------------------------------------------
# _format_attempts_steps: display rendering only — recovery is now handled by
# reading state["paused_clarification"] directly (see reset_transient_state /
# the checkpointer), not by parsing this rendered text back out.
# ---------------------------------------------------------------------------

def test_format_attempts_steps_renders_purpose_action_and_observation():
    attempts = [
        {"purpose": "List the repo tree", "action_desc": "list_repo_tree()", "observation": "ERROR: could not fetch tree (404)"},
        {"purpose": "Check recent commits", "action_desc": "list_commits(branch=main, limit=5)", "observation": "abc1234 — fix: something"},
    ]
    rendered = aw._format_attempts_steps(attempts)

    assert "Step 1 — List the repo tree" in rendered
    assert "list_repo_tree()" in rendered
    assert "could not fetch tree (404)" in rendered
    assert "Step 2 — Check recent commits" in rendered
    assert "abc1234" in rendered


# ---------------------------------------------------------------------------
# A question for the person is simply the answer (docs/react-sunset-plan.md, stage C): there is no pause and no
# resume. The model is no longer offered "clarify"; a model that still returns it has its question shown as the answer.
# ---------------------------------------------------------------------------

@run_async
async def test_a_clarify_action_is_shown_as_the_answer_not_raised_as_a_pause(monkeypatch):
    async def act(decision):
        return "should never be called"

    async def fake_ainvoke(prompt):
        return _llm_response(action="clarify", question="Which repo did you mean: SAAPP or errAgent?")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    result = await aw.run_react_loop(question="find the bug", schema="repo=x", prompt_template="{question} | {schema} | {attempts}", act=act, max_iterations=3, node_name="test_node")
    assert result["final_answer"] == "Which repo did you mean: SAAPP or errAgent?"
    assert result["attempts"] == []


@run_async
async def test_a_tool_agent_turn_that_asks_ends_with_the_question_and_no_paused_state(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=[_llm_response(action="clarify", question="Which file did you mean?")]))
    result = await aw.tool_agent_node(_state("fix that file"))
    assert "Which file did you mean?" in result["content_to_format"]
    assert result.get("relevance_grade") != "needs_clarification"
    assert "paused_clarification" not in result and "paused_code_plan" not in result


def test_the_model_is_no_longer_offered_a_clarify_action_or_a_plan_pause():
    from backend.components.constraints import TOOL_AGENT_PROMPT
    assert '"query" or "final" or "clarify"' not in TOOL_AGENT_PROMPT
    assert 'action=clarify' not in TOOL_AGENT_PROMPT
    assert 'Ask the user a question in your "final" answer only when' in TOOL_AGENT_PROMPT


def test_a_pause_left_in_old_state_never_resumes_anything():
    state = {
        "messages": [SimpleNamespace(type="human", content="read the workflow file"), SimpleNamespace(type="ai", content="Which repo did you mean?"), SimpleNamespace(type="human", content="SummonShenron/SAAPP")],
        "paused_clarification": {"original_question": "read the workflow file", "attempts": []},
        "paused_code_plan": {"original_question": "x", "attempts": [], "files": [], "summary": ""},
    }
    assert aw.classify_intent("SummonShenron/SAAPP", state=state) != "resume_tool_agent"
