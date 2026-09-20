"""Regression guard for the central risk of turning on a real LangGraph checkpointer: once a
checkpointer + thread_id is used, LangGraph silently resurrects any GraphState channel absent
from a turn's input state from whatever was checkpointed on a prior turn, however many turns
back that was. reset_transient_state (called as the very first thing coordinator_node does —
the graph's sole entry point) is what neutralizes this for every field except
paused_clarification, which is deliberately exempted so it — and only it — survives.

Uses langgraph.checkpoint.memory.InMemorySaver rather than the real MongoDBSaver: the
resurrection mechanic being tested is a property of LangGraph's own Pregel loop, not the Mongo
backend, so this validates real behavior with no Mongo dependency in tests."""
import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from backend.services import agent_workflow as aw
from backend.state.graph_state import GraphState


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _base_state(question: str):
    return {
        "workflowName": "test",
        "requestId": "test-req",
        "messages": [HumanMessage(content=question)],
        "username": "jack",
        "session_id": "s1",
        "target_scope": [],
        "documents": [],
        "relevance_grade": "",
        "loop_count": 0,
        "original_question": "",
        "attachment_summaries": [],
        "rag_mode": "strict",
    }


def _reasoner_flags(**overrides):
    flags = {
        "needs_retrieval": False, "needs_rewrite": False, "needs_summary": False,
        "needs_formatting": False, "needs_conversation": False, "needs_memory_save": False,
        "needs_memory_recall": False, "needs_paapp": False, "follow_up_intent": False,
        "needs_web_search": False, "needs_code_interpreter": False, "needs_github_search": False,
        "needs_pr_summary": False, "needs_create_pr": False, "needs_create_issue": False,
    }
    flags.update(overrides)
    return SimpleNamespace(content=json.dumps(flags))


def _register_stub_nodes_for_plan_destinations(workflow: StateGraph, overrides: dict) -> None:
    """Registers every node aw._PLAN_DESTINATIONS could route to — LangGraph validates every
    possible conditional-edge target against a registered node at compile time. `overrides`
    supplies real behavior for the specific node(s) a test cares about; everything else gets a
    harmless passthrough."""
    for node_name in aw._PLAN_DESTINATIONS.values():
        workflow.add_node(node_name, overrides.get(node_name, lambda s: s))


def _build_graph(checkpointer, overrides: dict):
    workflow = StateGraph(GraphState)
    workflow.add_node("coordinator_node", aw.coordinator_node)
    _register_stub_nodes_for_plan_destinations(workflow, overrides)
    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges("coordinator_node", aw.coordinator_router, aw._PLAN_DESTINATIONS)
    for node_name in aw._PLAN_DESTINATIONS.values():
        if node_name != "formatter_node":
            workflow.add_conditional_edges(node_name, aw.plan_continue_router, aw._PLAN_DESTINATIONS)
    workflow.add_edge("formatter_node", END)
    return workflow.compile(checkpointer=checkpointer)


@run_async
async def test_transient_fields_do_not_resurrect_across_turns(monkeypatch):
    """Turn 1 sets pending_action/drafted_code/voice_payload via a real plan-driven node; turn
    2 (a fresh, unrelated message, same thread_id — exactly how app.py builds initial_state
    every request) must NOT see any of it resurrected."""
    def stub_memory_save(state):
        return {
            **state,
            "pending_action": {"action_type": "test_write"},
            "drafted_code": "print('should not leak')",
            "voice_payload": {"source_type": "test"},
        }

    checkpointer = InMemorySaver()
    graph = _build_graph(checkpointer, {"memory_save_node": stub_memory_save})
    config = {"configurable": {"thread_id": "thread-a"}}

    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            _reasoner_flags(needs_memory_save=True),  # turn 1's reasoner call
            _reasoner_flags(),                          # turn 2's reasoner call
        ]),
    )

    turn1 = await graph.ainvoke(_base_state("remember that I like dark mode"), config=config)
    assert turn1["pending_action"] == {"action_type": "test_write"}
    assert turn1["drafted_code"] == "print('should not leak')"

    turn2 = await graph.ainvoke(_base_state("how's the weather today"), config=config)
    assert turn2["pending_action"] is None
    assert turn2["drafted_code"] is None
    assert turn2["voice_payload"] is None


@run_async
async def test_paused_clarification_survives_and_then_clears(monkeypatch):
    """The one deliberate exception: paused_clarification must survive from the turn that sets
    it into the very next turn (same thread_id), and be gone again by the turn after that."""
    def stub_tool_agent(state):
        if state.get("paused_clarification"):
            return {
                **state,
                "relevance_grade": "tool_agent",
                "content_to_format": "resumed successfully",
                "paused_clarification": None,
            }
        return {
            **state,
            "relevance_grade": "needs_clarification",
            "paused_clarification": {"original_question": state["messages"][-1].content, "attempts": []},
        }

    checkpointer = InMemorySaver()
    graph = _build_graph(checkpointer, {"tool_agent_node": stub_tool_agent})
    config = {"configurable": {"thread_id": "thread-b"}}

    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            _reasoner_flags(needs_github_search=True),  # turn 1
            _reasoner_flags(needs_github_search=True),  # turn 2 (resume)
            _reasoner_flags(),                            # turn 3
        ]),
    )

    turn1 = await graph.ainvoke(_base_state("find the bug in agent_workflow.py"), config=config)
    assert turn1["relevance_grade"] == "needs_clarification"
    assert turn1["paused_clarification"] is not None

    turn2 = await graph.ainvoke(_base_state("SummonShenron/SAAPP"), config=config)
    assert turn2["content_to_format"] == "resumed successfully"
    assert turn2["paused_clarification"] is None

    turn3 = await graph.ainvoke(_base_state("thanks, one more question"), config=config)
    assert turn3["paused_clarification"] is None


@run_async
async def test_paused_clarification_isolated_across_threads(monkeypatch):
    """A different conversation (different thread_id) must never see another conversation's
    paused clarification."""
    def stub_tool_agent(state):
        return {
            **state,
            "relevance_grade": "needs_clarification",
            "paused_clarification": {"original_question": state["messages"][-1].content, "attempts": []},
        }

    checkpointer = InMemorySaver()
    graph = _build_graph(checkpointer, {"tool_agent_node": stub_tool_agent})

    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            _reasoner_flags(needs_github_search=True),  # thread A
            _reasoner_flags(),                            # thread B
        ]),
    )

    await graph.ainvoke(_base_state("find the bug"), config={"configurable": {"thread_id": "thread-a"}})
    thread_b_result = await graph.ainvoke(
        _base_state("hello there"), config={"configurable": {"thread_id": "thread-b"}}
    )

    assert thread_b_result.get("paused_clarification") is None
