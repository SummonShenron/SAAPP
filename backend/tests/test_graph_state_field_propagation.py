"""Regression guard for a real bug: state["write_action"] was set by build_agent_plan but
never declared in GraphState's TypedDict, so LangGraph silently dropped it between nodes —
propose_write_node always saw write_action=None and silently no-opped. Calling nodes directly
with a hand-built dict (as most of this suite's tests do) can't catch this class of bug, since
it bypasses LangGraph's actual state-channel merging entirely. This test exercises the real
compiled graph machinery instead."""
import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END

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


def test_write_action_is_declared_in_graph_state():
    """The direct regression guard: this exact field must be declared, or LangGraph drops it."""
    assert "write_action" in GraphState.__annotations__


@run_async
async def test_write_action_survives_real_graph_execution_into_propose_write_node(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "needs_retrieval": False, "needs_rewrite": False, "needs_summary": False,
            "needs_formatting": False, "needs_conversation": False, "needs_memory_save": False,
            "needs_memory_recall": False, "needs_paapp": False, "follow_up_intent": False,
            "needs_web_search": False, "needs_code_interpreter": False, "needs_github_search": False,
            "needs_pr_summary": False, "needs_create_pr": True, "needs_create_issue": False,
        })))
    )
    monkeypatch.setattr(aw, "fetch_branch_diff_summary", lambda repo, base, head: "Commits (0):\n\nFiles Changed (0):\n")
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=json.dumps({
            "title": "feat: test", "body": "### Summary\n- test"
        })))
    )

    # Minimal graph reusing the REAL nodes and REAL router, mirroring create_workflow's actual
    # wiring for this path, without needing the full graph's unrelated vector-store dependencies.
    workflow = StateGraph(GraphState)
    workflow.add_node("coordinator_node", aw.coordinator_node)
    workflow.add_node("propose_write_node", aw.propose_write_node)
    workflow.add_node("formatter_node", lambda s: s)
    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges(
        "coordinator_node", aw.coordinator_router,
        {"propose_write_node": "propose_write_node", "formatter_node": "formatter_node"},
    )
    workflow.add_edge("propose_write_node", "formatter_node")
    workflow.add_edge("formatter_node", END)
    graph = workflow.compile()

    result = await graph.ainvoke(_base_state("create a pull request to merge feat/x into main in repo owner/repo"))

    assert result.get("write_action") == "create_pr"
    assert result.get("pending_action") is not None
    assert result["pending_action"]["action_type"] == "create_pr"
    assert result.get("relevance_grade") == "hitl_approval_required"


@run_async
async def test_approval_works_across_two_truly_independent_turns(monkeypatch):
    """The real bug, reproduced faithfully: app.py rebuilds initial_state from scratch every
    HTTP request (see app.py's initial_state construction) — pending_action is NEVER part of
    it, only the stored message history is. A test that keeps reusing the same Python state
    dict across "turns" can't catch that, since the dict object itself smuggles pending_action
    through in a way two real, independent requests never could. This test builds two
    completely separate state dicts, connected only by a shared stored message list, exactly
    like two real chat turns."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            SimpleNamespace(content=json.dumps({
                "needs_retrieval": False, "needs_rewrite": False, "needs_summary": False,
                "needs_formatting": False, "needs_conversation": False, "needs_memory_save": False,
                "needs_memory_recall": False, "needs_paapp": False, "follow_up_intent": False,
                "needs_web_search": False, "needs_code_interpreter": False, "needs_github_search": False,
                "needs_pr_summary": False, "needs_create_pr": True, "needs_create_issue": False,
            })),
            SimpleNamespace(content=json.dumps({
                "needs_retrieval": False, "needs_rewrite": False, "needs_summary": False,
                "needs_formatting": False, "needs_conversation": False, "needs_memory_save": False,
                "needs_memory_recall": False, "needs_paapp": False, "follow_up_intent": True,
                "needs_web_search": False, "needs_code_interpreter": False, "needs_github_search": False,
                "needs_pr_summary": False, "needs_create_pr": False, "needs_create_issue": False,
            })),
        ])
    )
    monkeypatch.setattr(aw, "fetch_branch_diff_summary", lambda repo, base, head: "Commits (0):\n\nFiles Changed (0):\n")
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=json.dumps({
            "title": "feat: test", "body": "### Summary\n- test"
        })))
    )
    created = SimpleNamespace(status_code=201, json=lambda: {"html_url": "https://github.com/owner/repo/pull/1", "number": 1}, text="")
    merged = SimpleNamespace(status_code=200, json=lambda: {}, text="")
    monkeypatch.setattr(aw.requests, "post", lambda *a, **k: created)
    monkeypatch.setattr(aw.requests, "put", lambda *a, **k: merged)

    def _make_graph():
        workflow = StateGraph(GraphState)
        workflow.add_node("coordinator_node", aw.coordinator_node)
        workflow.add_node("propose_write_node", aw.propose_write_node)
        workflow.add_node("execute_write_node", aw.execute_write_node)
        workflow.add_node("formatter_node", lambda s: s)
        workflow.add_edge(START, "coordinator_node")
        workflow.add_conditional_edges(
            "coordinator_node", aw.coordinator_router,
            {
                "propose_write_node": "propose_write_node",
                "execute_write_node": "execute_write_node",
                "formatter_node": "formatter_node",
            },
        )
        workflow.add_edge("propose_write_node", "formatter_node")
        workflow.add_edge("execute_write_node", "formatter_node")
        workflow.add_edge("formatter_node", END)
        return workflow.compile()

    # --- "Turn 1": a real HTTP request proposing the PR ---
    stored_messages = [HumanMessage(content="create a pull request to merge feat/x into main in repo owner/repo")]
    turn1_state = _base_state("")
    turn1_state["messages"] = list(stored_messages)
    turn1_result = await _make_graph().ainvoke(turn1_state)

    # The approval card is what actually gets persisted to chat history — not pending_action.
    card_text = turn1_result["messages"][-1].content
    assert "Ready to create a Pull Request" in card_text
    stored_messages = list(turn1_result["messages"])

    # --- "Turn 2": a brand-new, independent request — no pending_action carried over at all,
    # exactly like a fresh app.py initial_state built only from stored history. ---
    stored_messages.append(HumanMessage(content="approve"))
    turn2_state = _base_state("")
    turn2_state["messages"] = stored_messages
    assert "pending_action" not in turn2_state or turn2_state.get("pending_action") is None

    turn2_result = await _make_graph().ainvoke(turn2_state)

    assert turn2_result.get("relevance_grade") == "action_complete"
    assert "pull/1" in turn2_result.get("content_to_format", "")
