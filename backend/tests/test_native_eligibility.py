import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import native_loop as nl
from backend.services import native_tools as nt


@pytest.fixture(autouse=True)
def native_on(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")


TOOLS = [{"name": "search_code"}]


def ok(text="", flags=None, **kw):
    return nl.eligible(edit_turn=False, resumed=False, tools=TOOLS, text=text, flags=flags, **kw)


@pytest.mark.parametrize("text", [
    "Search my Gmail inbox for the last two messages from Harper.jack",
    "feel free to read the last two emails from myself",
    "what's in my inbox",
    "read the attachment in that message",
    "what's on my calendar tomorrow?",
    "find the doc in my Google Drive",
    "run a query on the mongodb users collection",
    "what's in the database for this user?",
    "open the site in the browser and take a screenshot",
    "run the tests for this branch",
    "write an implementation plan for this",
    "add this to my doc",
])
def test_requests_that_need_a_tool_only_the_old_engine_has_do_not_go_native(text):
    assert ok(text) is False


@pytest.mark.parametrize("text", [
    "How does an invoice end up with the status overdue?",
    "scan the whole repo and tell me every file that reads GRACE_DAYS",
    "what changed in the last few commits, and is there an open pull request?",
    "did the latest deploy go out ok?",
    "can you check your own logs for errors?",
    "look up the python docs for functools.cache",
    "where is the scheduler started?",   # "scheduler" is not "schedule"
])
def test_read_only_repo_web_and_ops_questions_still_go_native(text):
    assert ok(text) is True


@pytest.mark.parametrize("flag", ["needs_gmail_lookup", "needs_drive_lookup", "needs_calendar_lookup", "needs_send_email"])
def test_the_routers_own_reading_also_keeps_a_turn_off_the_native_engine(flag):
    assert ok("anything", flags={flag: True}) is False
    assert ok("anything", flags={flag: False, "needs_github_search": True}) is True


def test_the_other_reasons_for_the_old_engine_still_apply_and_off_means_off(monkeypatch):
    assert nl.eligible(edit_turn=True, resumed=False, tools=TOOLS) is False
    assert nl.eligible(edit_turn=False, resumed=True, tools=TOOLS) is False
    assert nl.eligible(edit_turn=False, resumed=False, tools=[]) is False
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    assert ok("how does the scheduler work?") is False


def test_the_pattern_covers_every_tool_the_native_engine_lacks_in_the_old_menu():
    """The list of what the native engine cannot do, checked against the real menu, so a new old-engine tool cannot be forgotten silently."""
    from backend.evals import behavior_eval as be

    async def model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "x"}))

    import re

    seen = asyncio.run(be.run_tool_agent(model, "check my gmail inbox", repo=True, engine="react"))
    lacking = sorted(set(re.findall(r"^- ([a-z_]+) —", seen.menu, re.M)) - set(nt.REGISTRY))
    assert lacking == [
        "browser_click", "browser_navigate", "browser_read_text", "browser_screenshot", "browser_type", "get_gmail_attachment",
        "list_google_calendar_events", "propose_append_target_doc", "propose_code_plan", "propose_send_email", "read_drive_file",
        "read_gmail_message", "run_mongo_query", "run_repo_tests", "run_snippet", "search_drive_files", "search_gmail",
    ], "the old engine's menu changed: decide whether each new tool needs the old engine, and update _OLD_ENGINE_ONLY_RE"


def test_in_the_real_tool_agent_a_gmail_request_runs_the_old_engine_and_a_repo_question_the_native_one(monkeypatch):
    from backend.evals import behavior_eval as be
    from backend.utils import llm_turns

    class ToolLLM:
        async def ainvoke_tools(self, messages, tools):
            return AIMessage(content="done")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    for question, engine in (("search my gmail inbox for the last two messages", "react"), ("where is GRACE_DAYS read?", "native")):
        turn = llm_turns.begin()
        asyncio.run(be.run_tool_agent(react_model, question, repo=True, engine="native", tool_llm=ToolLLM()))
        assert turn.engine == engine, question


def test_a_yes_after_an_email_question_stays_with_the_old_engine():
    from backend.evals import behavior_eval as be
    from backend.utils import llm_turns

    class ToolLLM:
        async def ainvoke_tools(self, messages, tools):
            return AIMessage(content="done")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    history = [HumanMessage(content="can you check my inbox for the invoice email?"), AIMessage(content="Want me to search for it?")]
    turn = llm_turns.begin()
    asyncio.run(be.run_tool_agent(react_model, "yes, go ahead", repo=True, engine="native", tool_llm=ToolLLM(), history=history))
    assert turn.engine == "react"
