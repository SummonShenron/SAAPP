"""Sonic's place tools in the tool agent (backend/utils/place_questions.py, maps_tools.py, agent_workflow.py): they are part of a turn only when the person is signed in, the maps service is set up and the message is
about places or getting somewhere; the model sees OUR two small tools; a call runs off the event loop and is counted; nothing else gets them."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent_workflow as aw
from backend.services import maps_tools
from backend.utils import place_questions as pq

PLACE_QUESTIONS = [
    "can you suggest some local bars for a date tonight?", "any good coffee shops near me", "how far is Providence Park from Pioneer Square", "which one is closest to downtown?",
    "where should we go for dinner tonight", "what are some date night ideas in Portland", "is the pharmacy open now", "directions to the airport from my hotel", "best brewery on the west side",
    "I asked for local bar suggestions earlier, any more spots?", "what is there to do in Austin this weekend", "how long does it take to drive from Seattle to Portland",
]
NOT_PLACE_QUESTIONS = [
    "add a progress bar to the dashboard", "make a bar chart of usage", "fix the sidebar in Chat.tsx", "what is pub/sub", "review the repo for the park function", "hello there", "tell me about the history of coffee",
    "write a poem about a bar", "update my memory", "how does the budget guard work", "the status bar overlaps the header", "", "   ", "can you run the tests",
]


@pytest.mark.parametrize("text", PLACE_QUESTIONS)
def test_a_message_about_places_or_getting_somewhere_reads_as_one(text):
    assert pq.looks_like_place_question(text) is True, text


@pytest.mark.parametrize("text", NOT_PLACE_QUESTIONS)
def test_everything_else_does_not_and_software_words_keep_a_coding_message_out(text):
    assert pq.looks_like_place_question(text) is False, text
    assert pq.looks_like_place_question(None) is False


# ---- who gets the tools ------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(maps_tools, "available", lambda: True)


def state_for(question, username="jack", earlier=()):
    messages = []
    for text in earlier:
        messages += [HumanMessage(content=text), AIMessage(content="ok")]
    return {"username": username, "messages": messages + [HumanMessage(content=question)]}


def test_the_tools_go_only_to_a_signed_in_person_with_the_service_set_up_and_a_place_question(configured, monkeypatch):
    assert aw._place_tools_wanted(state_for("any good coffee shops near me")) is True
    assert aw._place_tools_wanted(state_for("add a progress bar to the dashboard")) is False
    assert aw._place_tools_wanted(state_for("any good coffee shops near me", username="guest-recruiter@example.com")) is False
    assert aw._place_tools_wanted({"username": "jack", "messages": []}) is False and aw._place_tools_wanted({}) is False
    monkeypatch.setattr(maps_tools, "available", lambda: False)
    assert aw._place_tools_wanted(state_for("any good coffee shops near me")) is False                 # no key, no tools: nothing is offered that cannot work


def test_a_short_follow_up_in_a_place_conversation_keeps_the_tools_and_an_old_one_does_not(configured):
    assert aw._place_tools_wanted(state_for("which one is closest?", earlier=["local bars for a date tonight?"])) is True
    assert aw._place_tools_wanted(state_for("thanks, and what about tomorrow?", earlier=["hello", "local bars for a date tonight?", "something else", "and another thing"])) is False        # only ONE earlier message counts


def test_a_place_question_reaches_the_tool_agent_even_with_no_other_flag_and_is_counted_by_why(configured):
    from backend.utils import llm_turns

    assert "place_lookup" in llm_turns.ROUTE_REASONS
    plan = aw.build_agent_plan("conversational", {**state_for("can you suggest some local bars for a date tonight?"), "reasoner_flags": {}})
    assert "tool_agent" in plan["agents"]
    plain = aw.build_agent_plan("conversational", {**state_for("hello there"), "reasoner_flags": {}})
    assert "tool_agent" not in plain["agents"]


# ---- through the tool agent ---------------------------------------------------------------------------------------------------------------------

async def drive(monkeypatch, question, decisions=(), *, engine="native", username="jack", earlier=(), phase=None):
    from backend.tests.test_tool_agent_node import _setup_github_repo

    monkeypatch.setenv("TOOL_AGENT_ENGINE", engine)
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_db", lambda: None)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {"observations": []}

    async def fake_run(**kwargs):
        for decision in decisions:
            seen["observations"].append(await kwargs["act"](decision))
        seen["prompt"] = kwargs.get("prompt_template", "")
        seen["tools"] = {t["name"]: t for t in (kwargs.get("tools") or [])}
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_run)
    monkeypatch.setattr(aw, "run_react_loop", fake_run)
    state = state_for(question, username, earlier)
    if phase:
        state["code_plan_phase"] = phase
    await aw.tool_agent_node(state)
    seen["events"] = [(c.args[0], c.args[1]) for c in emit.call_args_list]
    return seen


def run(coro):
    return asyncio.run(coro)


def test_the_model_is_given_our_two_small_tools_with_our_descriptions_on_a_place_question(configured, monkeypatch):
    seen = run(drive(monkeypatch, "can you suggest some local bars for a date tonight?"))
    assert {"mcp__maps__find_places", "mcp__maps__travel_times"} <= set(seen["tools"])
    declared = seen["tools"]["mcp__maps__find_places"]
    assert declared["description"] == maps_tools.SERVER.tools["find_places"].description and set(declared["parameters"]["properties"]) == {"query", "latitude", "longitude", "radius_meters", "count"}
    assert not any("search_places" in name or "compute_routes" in name or "resolve" in name for name in seen["tools"])


def test_a_turn_that_is_not_about_places_pays_for_none_of_it(configured, monkeypatch):
    seen = run(drive(monkeypatch, "where is the login flow implemented?"))
    assert not [name for name in seen["tools"] if name.startswith("mcp__")]
    react = run(drive(monkeypatch, "where is the login flow implemented?", engine="react"))
    assert "mcp__maps" not in react["prompt"]


def test_the_old_engines_menu_names_both_tools_on_a_place_question(configured, monkeypatch):
    react = run(drive(monkeypatch, "any good coffee shops near me", engine="react"))
    assert "- mcp__maps__find_places — args: query (" in react["prompt"] and "- mcp__maps__travel_times — args: origin (" in react["prompt"] and "NO street address or neighbourhood" in react["prompt"]


def test_a_call_runs_off_the_loop_with_the_persons_name_is_counted_and_its_result_is_what_the_model_reads(configured, monkeypatch):
    calls, counted = [], []
    monkeypatch.setattr(maps_tools, "run", lambda tool, arguments, username="": calls.append((tool, arguments, username)) or "Places from Google Maps\n[0] Teardrop Lounge | 45.5,-122.6 | https://maps")
    from backend.utils import self_counters

    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: counted.append(counts))
    decisions = [{"tool_action": "mcp__maps__find_places", "args": {"query": "cocktail bars in downtown Portland, Oregon"}}, {"tool_action": "mcp__maps__travel_times", "args": {"origin": "x", "destinations": ["y"]}}]
    seen = run(drive(monkeypatch, "can you suggest some local bars for a date tonight?", decisions))
    assert calls == [("mcp__maps__find_places", {"query": "cocktail bars in downtown Portland, Oregon"}, "jack"), ("mcp__maps__travel_times", {"origin": "x", "destinations": ["y"]}, "jack")]
    assert seen["observations"][0].startswith("Places from Google Maps")
    assert {"maps_find_places": 1} in counted and {"maps_travel_times": 1} in counted
    assert any(name == "trace_detail" and data["title"] == "Asking Google Maps..." for name, data in seen["events"])


def test_a_failed_call_is_counted_as_an_error_and_returned_as_one(configured, monkeypatch):
    monkeypatch.setattr(maps_tools, "run", lambda tool, arguments, username="": "ERROR: the daily limit for find_places was reached; try again tomorrow.")
    counted = []
    from backend.utils import self_counters

    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: counted.append(counts))
    seen = run(drive(monkeypatch, "any good coffee shops near me", [{"tool_action": "mcp__maps__find_places", "args": {"query": "coffee in Seattle"}}]))
    assert seen["observations"][0].startswith("ERROR: the daily limit") and {"maps_find_places": 1, "maps_errors": 1} in counted


def test_the_tools_cannot_be_called_in_a_turn_that_was_not_given_them_or_in_a_plan_turn(configured, monkeypatch):
    called = []
    monkeypatch.setattr(maps_tools, "run", lambda *a, **k: called.append(a) or "x")
    seen = run(drive(monkeypatch, "where is the login flow implemented?", [{"tool_action": "mcp__maps__find_places", "args": {"query": "coffee in Seattle"}}]))
    assert seen["observations"][0] == "ERROR: that tool is not available in this turn." and called == []


def test_their_output_counts_as_untrusted_and_is_never_batched_with_other_steps():
    from backend.utils import experience_capture

    for name in ("mcp__maps__find_places", "mcp__maps__travel_times"):
        assert name in aw._UNTRUSTED_CONTENT_TOOLS and name in experience_capture.UNTRUSTED_TOOLS and name not in aw.TOOL_AGENT_BATCHABLE_ACTIONS


def test_the_counters_and_the_trace_know_the_tools():
    from backend.utils import agent_utils, self_counters

    assert set(self_counters.MAPS_KEYS) <= self_counters.KEYS
    assert agent_utils.build_trace_subject("mcp__maps__find_places", {"query": "cocktail bars in downtown Portland"}) == "cocktail bars in downtown Portland"
    import pathlib

    labels = (pathlib.Path(aw.__file__).resolve().parents[2] / "local" / "src" / "components" / "traceFacts.ts").read_text(encoding="utf-8")
    assert "mcp__maps__find_places: 'Looked up places'" in labels and "mcp__maps__travel_times: 'Checked travel times'" in labels
