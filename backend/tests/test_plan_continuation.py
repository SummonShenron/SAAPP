"""Regression guard for the real bug found while explaining the graph's routing: build_agent_plan
can queue more than one real agent (e.g. ["memory_save", "retriever"] when two reasoner flags
fire in the same turn), but nothing in the graph ever routed back to coordinator_node to consume
anything past the first entry — every plan-driven node had a static edge straight to
formatter_node, silently dropping the rest of the queue. plan_continue_router (and, for the
retrieval sub-loop specifically, route_after_grading_with_plan_continuation) fixes this. These
tests exercise the real compiled graph machinery, not hand-built dicts, since that's the only way
to catch this class of bug — see test_graph_state_field_propagation.py for the same rationale."""
import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.documents import Document
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


def _register_stub_nodes_for_plan_destinations(workflow: StateGraph) -> None:
    """LangGraph validates every possible conditional-edge target against a registered node at
    compile time — since these tests use the real aw._PLAN_DESTINATIONS mapping (matching real
    production usage), every node it could possibly name needs to exist, even the ones this
    particular test doesn't care about. Registers a harmless passthrough for anything not
    already added."""
    for node_name in aw._PLAN_DESTINATIONS.values():
        if node_name not in workflow.nodes:
            workflow.add_node(node_name, lambda s: s)


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


@run_async
async def test_plan_continuation_executes_every_queued_agent(monkeypatch):
    """Two independent reasoner flags (needs_memory_save + needs_paapp) both firing must run
    BOTH agents in sequence, not just the first."""
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=_reasoner_flags(needs_memory_save=True, needs_paapp=True)),
    )

    executed = []

    def stub_memory_save(state):
        executed.append("memory_save")
        return state

    def stub_paapp(state):
        executed.append("paapp")
        return state

    workflow = StateGraph(GraphState)
    workflow.add_node("coordinator_node", aw.coordinator_node)
    workflow.add_node("memory_save_node", stub_memory_save)
    workflow.add_node("paapp_node", stub_paapp)
    workflow.add_node("formatter_node", lambda s: s)
    _register_stub_nodes_for_plan_destinations(workflow)
    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges("coordinator_node", aw.coordinator_router, aw._PLAN_DESTINATIONS)
    workflow.add_conditional_edges("memory_save_node", aw.plan_continue_router, aw._PLAN_DESTINATIONS)
    workflow.add_conditional_edges("paapp_node", aw.plan_continue_router, aw._PLAN_DESTINATIONS)
    workflow.add_edge("formatter_node", END)
    graph = workflow.compile()

    await graph.ainvoke(_base_state("do a thing"))

    assert executed == ["memory_save", "paapp"]


@run_async
async def test_retrieval_does_not_swallow_a_queued_step_after_it(monkeypatch):
    """The Part C fix specifically: a plan of [retriever, summarizer] must reach summarizer_node
    once grading finishes successfully, not stop at formatter_node the way retrieval used to
    unconditionally do regardless of what else was queued."""
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            _reasoner_flags(needs_retrieval=True, needs_summary=True),
            SimpleNamespace(content="yes, this document is relevant"),  # grading_node's own call
        ]),
    )

    executed = []

    def stub_retrieve(state):
        executed.append("retrieve")
        return {**state, "documents": [Document(page_content="x", metadata={})]}

    def stub_summarizer(state):
        executed.append("summarizer")
        return state

    workflow = StateGraph(GraphState)
    workflow.add_node("coordinator_node", aw.coordinator_node)
    workflow.add_node("retrieve_node", stub_retrieve)
    workflow.add_node("grade_documents_node", aw.grading_node)
    workflow.add_node("rewrite_query_node", lambda s: s)
    workflow.add_node("summarizer_node", stub_summarizer)
    workflow.add_node("formatter_node", lambda s: s)
    _register_stub_nodes_for_plan_destinations(workflow)
    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges("coordinator_node", aw.coordinator_router, aw._PLAN_DESTINATIONS)
    workflow.add_edge("retrieve_node", "grade_documents_node")
    workflow.add_conditional_edges(
        "grade_documents_node", aw.route_after_grading_with_plan_continuation,
        {**aw._PLAN_DESTINATIONS, "rewrite_query_node": "rewrite_query_node"},
    )
    workflow.add_conditional_edges("summarizer_node", aw.plan_continue_router, aw._PLAN_DESTINATIONS)
    workflow.add_edge("formatter_node", END)
    graph = workflow.compile()

    await graph.ainvoke(_base_state("summarize the docs about x"))

    assert executed == ["retrieve", "summarizer"]
