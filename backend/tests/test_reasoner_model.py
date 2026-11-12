"""The reasoner (the call that reads routing, feeling and risk on every turn) follows the person's base-model setting: Claude Haiku, thinking on, for a person who chose the Claude Haiku base model; the Gemini lite model for everyone else, Auto included
(backend/models/models.py reasoner_llm_for). Evidence: docs/model-eval-plan.md (Haiku with thinking 331/331 on the routing and risk evals, three risk misses in 80 calls without it)."""
import asyncio
import functools
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from backend.models import claude_llm, models
from backend.services import agent_workflow as aw
from backend.state.graph_state import GraphState
from backend.utils import self_counters as sc


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _reply(risk="none", valence="neutral"):
    return type("Reply", (), {"content": json.dumps({"needs_conversation": True, "emotional_state": {"valence": valence, "intensity": 0.2, "need": "none", "gist": "", "risk": risk}})})()


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setattr(models, "haiku_reply_available", lambda: True)
    monkeypatch.setattr(models, "_reasoner_haiku", {})
    monkeypatch.delenv("REASONER_HAIKU", raising=False)


@pytest.fixture
def node_env(monkeypatch, offered):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    monkeypatch.setattr(aw, "log_safety_event", lambda *a, **k: None)
    monkeypatch.setattr(aw, "remember_support", lambda *a, **k: None)
    haiku = type("FakeHaiku", (), {"ainvoke": AsyncMock(return_value=_reply())})()
    models._reasoner_haiku["haiku"] = haiku
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_reply()))
    return type("Env", (), {"recorded": recorded, "haiku": haiku, "lite": aw.lite_llm})


def test_a_claude_haiku_person_gets_haiku_with_thinking_on_and_the_lite_model_behind_it(offered):
    llm, kind = models.reasoner_llm_for("haiku")
    assert kind == "haiku"
    assert isinstance(llm, claude_llm.ClaudeLLM) and llm.model_name == claude_llm.DEFAULT_MODEL
    assert llm.thinking_off is False and llm.effort == "low", "thinking on is what made the risk reading match the lite model"
    assert llm.fallback is models.lite_llm, "a Claude problem must fall back to the lite model, never to an error"
    assert models.reasoner_llm_for("haiku")[0] is llm


def test_everyone_else_stays_on_the_lite_model_auto_and_gpt_included(offered):
    # Auto promises that a turn carrying a feeling never goes to Anthropic, and the reasoner reads every message, so Auto stays on the lite model
    for choice in ("gemini", "gpt", "lite", "auto", None, "", "sonnet"):
        assert models.reasoner_llm_for(choice) == (models.lite_llm, "lite"), choice


def test_a_server_that_does_not_offer_haiku_or_has_it_switched_off_uses_the_lite_model(monkeypatch, offered):
    monkeypatch.setattr(models, "haiku_reply_available", lambda: False)
    assert models.reasoner_llm_for("haiku") == (models.lite_llm, "lite")
    monkeypatch.setattr(models, "haiku_reply_available", lambda: True)
    for off in ("off", "0", "false", "no"):
        monkeypatch.setenv("REASONER_HAIKU", off)
        assert models.reasoner_llm_for("haiku") == (models.lite_llm, "lite"), off
    monkeypatch.delenv("REASONER_HAIKU")
    assert models.reasoner_llm_for("haiku")[1] == "haiku"


@run_async
async def test_a_haiku_persons_turn_is_read_by_haiku_and_counted(node_env):
    await aw.reasoner_node({"username": "jack", "reply_model": "haiku", "messages": [HumanMessage(content="hey there")]})
    node_env.haiku.ainvoke.assert_awaited_once()
    node_env.lite.ainvoke.assert_not_awaited()
    assert node_env.recorded == [{"reasoner_on_haiku": 1}]


@run_async
async def test_a_gemini_gpt_or_auto_persons_turn_and_one_with_no_setting_stay_on_the_lite_model_and_are_counted(node_env):
    for state in ({"reply_model": "gemini"}, {"reply_model": "gpt"}, {"reply_model": "auto"}, {}):
        await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="hey there")], **state})
    assert node_env.lite.ainvoke.await_count == 4
    node_env.haiku.ainvoke.assert_not_awaited()
    assert node_env.recorded == [{"reasoner_on_lite": 1}] * 4


@run_async
async def test_haikus_risk_reading_still_raises_the_safety_level(node_env):
    node_env.haiku.ainvoke = AsyncMock(return_value=_reply(risk="elevated", valence="distressed"))
    result = await aw.reasoner_node({"username": "jack", "reply_model": "haiku", "messages": [HumanMessage(content="i just can't see how any of this ends well for me")]})
    assert result["safety_state"]["level"] == "elevated"


@run_async
async def test_explicit_risk_language_still_raises_the_level_when_the_haiku_call_fails(node_env):
    node_env.haiku.ainvoke = AsyncMock(side_effect=RuntimeError("down"))
    result = await aw.reasoner_node({"username": "jack", "reply_model": "haiku", "messages": [HumanMessage(content="i want to kill myself")]})
    assert result["safety_state"]["level"] == "acute"


@run_async
async def test_the_setting_survives_the_real_graph_because_graph_state_declares_it():
    seen = {}

    async def node(state):
        seen["reply_model"] = state.get("reply_model")
        return state

    graph = StateGraph(GraphState)
    graph.add_node("n", node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    await graph.compile().ainvoke({"messages": [HumanMessage(content="hi")], "username": "jack", "reply_model": "haiku"})
    assert seen["reply_model"] == "haiku"
    source = open("app.py", encoding="utf-8").read()
    assert '"reply_model": reply_choice,' in source, "app.py must put the person's base-model setting into the turn's state"


def test_the_counters_are_registered_and_the_node_counts_before_it_calls():
    assert set(sc.REASONER_KEYS) == {"reasoner_on_haiku", "reasoner_on_lite"} and set(sc.REASONER_KEYS) <= sc.KEYS
    source = open(aw.__file__, encoding="utf-8").read()
    assert source.index('{f"reasoner_on_{reasoner_kind}": 1}') < source.index("response = await reasoner_llm.ainvoke(formatted_prompt)")
