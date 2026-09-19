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


def _state(question="find the bug in the auth flow", messages=None, username="jack"):
    return {
        "username": username,
        "messages": messages or [HumanMessage(content=question)],
        "documents": [],
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
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")


# ---------------------------------------------------------------------------
# _format_attempts_steps / _recover_attempts_from_steps_text round-trip
# ---------------------------------------------------------------------------

def test_attempts_steps_round_trip():
    attempts = [
        {"purpose": "List the repo tree", "action_desc": "list_repo_tree()", "observation": "ERROR: could not fetch tree (404)"},
        {"purpose": "Check recent commits", "action_desc": "list_commits(branch=main, limit=5)", "observation": "abc1234 — fix: something"},
    ]
    rendered = aw._format_attempts_steps(attempts)
    recovered = aw._recover_attempts_from_steps_text(rendered)

    assert recovered == attempts


def test_recover_attempts_from_steps_text_returns_empty_for_plain_text():
    assert aw._recover_attempts_from_steps_text("just a normal message with no steps in it") == []


# ---------------------------------------------------------------------------
# run_react_loop: action="clarify" raises _ClarificationNeeded
# ---------------------------------------------------------------------------

@run_async
async def test_run_react_loop_raises_clarification_needed():
    async def act(decision):
        return "should never be called"

    async def fake_ainvoke(prompt):
        return _llm_response(action="clarify", question="Which repo did you mean — SAAPP or errAgent?")

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        raised = None
        try:
            await aw.run_react_loop(
                question="find the bug",
                schema="repo=x",
                prompt_template="{question} | {schema} | {attempts}",
                act=act,
                max_iterations=3,
                node_name="test_node",
            )
        except aw._ClarificationNeeded as e:
            raised = e
        assert raised is not None
        assert "SAAPP or errAgent" in raised.question
        assert raised.attempts == []
    finally:
        aw.lite_llm.ainvoke = orig


# ---------------------------------------------------------------------------
# tool_agent_node: pauses for clarification instead of guessing
# ---------------------------------------------------------------------------

@run_async
async def test_tool_agent_node_pauses_with_clarification(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=_llm_response(
            action="clarify", question="Which repo — SummonShenron/SAAPP or SummonShenron/errAgent?"
        )),
    )

    result = await aw.tool_agent_node(_state("read the core workflow file and suggest an idea"))

    assert result["relevance_grade"] == "needs_clarification"
    assert aw.CLARIFICATION_CARD_MARKER in result["content_to_format"]
    assert "SummonShenron/SAAPP or SummonShenron/errAgent" in result["content_to_format"]
    assert result["messages"][-1].content == result["content_to_format"]


@run_async
async def test_tool_agent_node_clarification_includes_steps_so_far(monkeypatch):
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="List the repo tree", tool_action="list_repo_tree", args={}),
        _llm_response(action="clarify", question="I found two files named similarly — which one did you mean?"),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(
        aw.requests, "get",
        lambda url, headers=None, params=None: (
            _http_response(200, {"default_branch": "main"}) if url.endswith("/repos/SummonShenron/SAAPP")
            else _http_response(200, {"tree": [{"path": "a.py", "type": "blob"}, {"path": "b.py", "type": "blob"}]})
        ),
    )

    result = await aw.tool_agent_node(_state())

    assert "What I've checked so far" in result["content_to_format"]
    assert "List the repo tree" in result["content_to_format"]


# ---------------------------------------------------------------------------
# classify_intent: detects a reply to a paused clarification
# ---------------------------------------------------------------------------

def test_classify_intent_detects_clarification_reply():
    prev_ai_message = SimpleNamespace(
        type="ai",
        content=f"**{aw.CLARIFICATION_CARD_MARKER}:**\n\nWhich repo did you mean?",
    )
    human_original = SimpleNamespace(type="human", content="read the workflow file")
    new_reply = SimpleNamespace(type="human", content="SummonShenron/SAAPP")
    state = {"messages": [human_original, prev_ai_message, new_reply]}

    intent = aw.classify_intent("SummonShenron/SAAPP", state=state)

    assert intent == "resume_tool_agent"


def test_build_agent_plan_routes_resume_tool_agent():
    plan = aw.build_agent_plan("resume_tool_agent", {"reasoner_flags": {}})
    assert plan["agents"] == ["tool_agent", "formatter"]


# ---------------------------------------------------------------------------
# Full resume: a genuinely separate second turn continues instead of restarting
# ---------------------------------------------------------------------------

@run_async
async def test_tool_agent_node_resumes_with_recovered_attempts(monkeypatch):
    """Mirrors the real app.py flow: turn 2 is a brand-new state dict connected to turn 1 only
    via the persisted messages list — pending_action never survives between real turns (see
    classify_intent's card_marker comment), so resuming has to work off the message text alone."""
    _setup_github_repo(monkeypatch)

    original_question = HumanMessage(content="read the core workflow file and suggest an idea")
    clarification_card = SimpleNamespace(
        type="ai",
        content=(
            f"**{aw.CLARIFICATION_CARD_MARKER}:**\n\nWhich repo did you mean?\n\n"
            "**What I've checked so far:**\n\n"
            "**Step 1 — List the repo tree:**\n```\nlist_repo_tree()\n```\n"
            "**Result:**\n```\nERROR: could not fetch tree (404)\n```"
        ),
    )
    user_answer = HumanMessage(content="SummonShenron/SAAPP")

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="Found it — here's the idea.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    turn2_state = _state(messages=[original_question, clarification_card, user_answer])
    result = await aw.tool_agent_node(turn2_state)

    assert result["relevance_grade"] == "tool_agent"
    assert "Found it" in result["content_to_format"]
    # The recovered attempt from turn 1 shows up in the very first prompt built for turn 2 —
    # proof the loop resumed with it seeded in rather than starting from zero.
    assert "List the repo tree" in captured_prompts[0]
    assert "could not fetch tree" in captured_prompts[0]
    # The original ask and the user's clarifying answer both reached the loop's question.
    assert "read the core workflow file" in captured_prompts[0]
    assert "SummonShenron/SAAPP" in captured_prompts[0]
