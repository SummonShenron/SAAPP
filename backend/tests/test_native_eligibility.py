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
    "run a query on the mongodb users collection",
    "what's in the database for this user?",
    "open the site in the browser and take a screenshot",
    "run the tests for this branch",
    "write an implementation plan for this",
    "add this to my doc",
    "summarize the attached json and append it to my weekly reports google doc please?",
    "so now please append it to the weekly reports doc",
    "put the summary in my google doc",
    "save that to the notes document",
    "send an email to Dana about the invoice",
    "draft a reply email to the landlord",
])
def test_requests_that_need_a_tool_only_the_old_engine_has_do_not_go_native(text):
    assert ok(text) is False


@pytest.mark.parametrize("text", [
    "what does the doc say about retries?",
    "explain the docstring of this function",
    "find my budget spreadsheet in drive",
    "Search my Gmail inbox for the last two messages from Harper.jack",
    "feel free to read the last two emails from myself",
    "what's in my inbox",
    "read the attachment in that message",
    "what's on my calendar tomorrow?",
    "find the doc in my Google Drive",
    "How does an invoice end up with the status overdue?",
    "scan the whole repo and tell me every file that reads GRACE_DAYS",
    "what changed in the last few commits, and is there an open pull request?",
    "did the latest deploy go out ok?",
    "can you check your own logs for errors?",
    "look up the python docs for functools.cache",
    "where is the scheduler started?",   # "scheduler" is not "schedule"
])
def test_read_only_requests_including_gmail_drive_and_the_calendar_go_native(text):
    assert ok(text) is True


def test_the_routers_reading_keeps_a_send_request_off_the_native_engine_but_lookups_may_go_native():
    assert ok("anything", flags={"needs_send_email": True}) is False
    for flag in ("needs_gmail_lookup", "needs_drive_lookup", "needs_calendar_lookup", "needs_github_search"):
        assert ok("anything", flags={flag: True}) is True


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
        "browser_click", "browser_navigate", "browser_read_text", "browser_screenshot", "browser_type",
        "propose_append_target_doc", "propose_code_plan", "propose_send_email", "run_mongo_query", "run_repo_tests", "run_snippet",
    ], "the old engine's menu changed: decide whether each new tool needs the old engine, and update _OLD_ENGINE_ONLY_RE"


def test_in_the_real_tool_agent_a_gmail_request_runs_the_old_engine_and_a_repo_question_the_native_one(monkeypatch):
    from backend.evals import behavior_eval as be
    from backend.utils import llm_turns

    class ToolLLM:
        async def ainvoke_tools(self, messages, tools):
            return AIMessage(content="done")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    for question, engine in (("query the mongodb users collection", "react"), ("where is GRACE_DAYS read?", "native"),
                             ("search my gmail inbox for the last two messages", "native")):
        turn = llm_turns.begin()
        asyncio.run(be.run_tool_agent(react_model, question, repo=True, engine="native", tool_llm=ToolLLM()))
        assert turn.engine == engine, question


def test_a_yes_after_a_send_request_stays_with_the_old_engine():
    from backend.evals import behavior_eval as be
    from backend.utils import llm_turns

    class ToolLLM:
        async def ainvoke_tools(self, messages, tools):
            return AIMessage(content="done")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    history = [HumanMessage(content="can you draft a reply email to the landlord?"), AIMessage(content="Want me to write it?")]
    turn = llm_turns.begin()
    asyncio.run(be.run_tool_agent(react_model, "yes, go ahead", repo=True, engine="native", tool_llm=ToolLLM(), history=history))
    assert turn.engine == "react"


def test_the_native_engine_offers_the_gmail_drive_and_calendar_tools_and_runs_them_through_the_shared_dispatcher(monkeypatch):
    from backend.evals import behavior_eval as be
    from backend.services import agent_workflow as aw
    from backend.utils import llm_turns

    class Oauth:
        def get_valid_access_token(self, username):
            return "token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", Oauth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    asked = []
    monkeypatch.setattr(aw, "search_messages", lambda token, query, limit: asked.append((token, query, limit)) or [
        {"id": "m1", "subject": "Cutting costs", "from": "harper.jack@example.com", "date": "Oct 11", "snippet": "check the cache"}])

    seen = {"offered": None, "results": []}

    class ToolLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke_tools(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                seen["offered"] = {t["name"] for t in tools}
                return AIMessage(content="", tool_calls=[{"name": "search_gmail", "args": {"query": "from:harper.jack"}, "id": "g1", "type": "tool_call"}])
            seen["results"] = [str(m.content) for m in messages if m.type == "tool"]
            return AIMessage(content="I couldn't read it.")

    async def react_model(prompt):
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    turn = llm_turns.begin()
    asyncio.run(be.run_tool_agent(react_model, "search my gmail inbox for the last two messages from Harper.jack", repo=True, engine="native", tool_llm=ToolLLM()))
    assert turn.engine == "native"
    assert {"search_gmail", "read_gmail_message", "get_gmail_attachment", "search_drive_files", "read_drive_file", "list_google_calendar_events"} <= seen["offered"]
    # The call went through the shared dispatcher with the model's own arguments, and what the mailbox returned came back to the model.
    assert asked == [("token", "from:harper.jack", 10)]
    assert seen["results"] and "[m1] Cutting costs" in seen["results"][0] and "harper.jack@example.com" in seen["results"][0]


def test_the_new_tools_have_schemas_with_the_argument_names_the_dispatcher_reads():
    reg = nt.REGISTRY
    assert set(reg["search_gmail"]["properties"]) == {"query", "max_results"} and reg["search_gmail"]["required"] == ["query"]
    assert set(reg["get_gmail_attachment"]["properties"]) == {"message_id", "attachment_id"}
    assert set(reg["read_gmail_message"]["properties"]) == {"message_id"} and set(reg["read_drive_file"]["properties"]) == {"file_id"}
    assert set(reg["search_drive_files"]["properties"]) == {"query", "max_results"}
    assert set(reg["list_google_calendar_events"]["properties"]) == {"date"} and reg["list_google_calendar_events"]["required"] == []
