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
