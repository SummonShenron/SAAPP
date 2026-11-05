"""Why a tool-agent turn ran on the old ReAct loop, counted by category (stage A of docs/react-sunset-plan.md)."""
import asyncio
import itertools
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from backend.services import native_loop as nl
from backend.utils import self_counters as sc

READ = {"name": "read_repo_file"}
EMAIL = {"name": "propose_send_email"}


@pytest.fixture(autouse=True)
def native_on(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")


def reason(**kw):
    base = dict(edit_turn=False, resumed=False, tools=[READ], text="how does the scheduler work?", flags=None)
    return nl.react_reason(**{**base, **kw})


def test_each_reason_is_named_by_its_category():
    assert reason() == ""
    assert reason(tools=[]) == "no_tools"
    assert reason(edit_turn=True) == "edit_turn"
    assert reason(resumed=True) == "resumed"
    assert reason(flags={"needs_send_email": True}) == "flag"
    assert reason(text="query the mongodb users collection") == "wording"
    assert reason(text="send an email to Dana") == "wording"


def test_the_switch_being_off_is_its_own_reason(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    assert reason() == "switch_off"
    assert reason(tools=[]) == "switch_off"                          # the switch is read first: with it off there are no native tools to count


def test_a_tool_that_is_offered_lifts_its_own_wording_and_flag():
    assert reason(tools=[READ, EMAIL], text="send an email to Dana") == ""
    assert reason(tools=[READ, EMAIL], flags={"needs_send_email": True}) == ""
    assert reason(tools=[READ], flags={"needs_send_email": True}) == "flag"


def test_the_reason_is_empty_exactly_when_the_turn_is_eligible():
    tool_sets = [[], [READ], [READ, EMAIL], [READ, {"name": "run_mongo_query"}, {"name": "run_repo_tests"}, {"name": "run_local_check"}]]
    texts = ["how does it work", "send an email to Dana", "query the mongodb collection", "run ci on this", "run the tests", "navigate to the site", "make an implementation plan"]
    flag_sets = [None, {"needs_send_email": True}, {"needs_gmail_lookup": True}]
    for tools, text, flags, edit_turn, resumed in itertools.product(tool_sets, texts, flag_sets, (False, True), (False, True)):
        kw = dict(edit_turn=edit_turn, resumed=resumed, tools=tools, text=text, flags=flags)
        assert (nl.react_reason(**kw) == "") == nl.eligible(**kw), kw
        assert nl.react_reason(**kw) in ("",) + nl.REACT_REASONS


def test_every_reason_has_a_registered_counter_and_the_other_way_round():
    assert {f"engine_react_{name}" for name in nl.REACT_REASONS} == set(sc.ENGINE_KEYS)
    assert set(sc.ENGINE_KEYS) <= sc.KEYS


def _run_agent(monkeypatch, engine):
    from backend.evals import behavior_eval as be

    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))

    class ToolLLM:
        async def ainvoke_tools(self, messages, tools):
            return AIMessage(content="done")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    asyncio.run(be.run_tool_agent(react_model, "how does the scheduler work?", repo=True, engine=engine, tool_llm=ToolLLM()))
    return [counts for counts in recorded if any(key.startswith("engine_react_") for key in counts)]


def test_a_turn_on_the_old_loop_is_counted_once_with_its_reason_and_a_native_turn_is_not(monkeypatch):
    assert _run_agent(monkeypatch, "react") == [{"engine_react_switch_off": 1}]
    assert _run_agent(monkeypatch, "native") == []
