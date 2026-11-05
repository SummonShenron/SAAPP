"""propose_send_email on the native engine: the routing (wording and the router's flag), the approval signal, and the plan wording that no longer forces the old engine."""
import asyncio

import pytest
from langchain_core.messages import AIMessage

from backend.services import native_loop as nl
from backend.services import native_tools as nt
from backend.utils.agent_utils import _UnsafeActionRequested

EMAIL_TOOLS = [{"name": "search_code"}, {"name": "propose_send_email"}]
PLAIN_TOOLS = [{"name": "search_code"}]


@pytest.fixture(autouse=True)
def native_on(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")





def test_the_tools_offer_follows_the_menu_line_so_nobody_gets_it_unless_the_menu_has_it():
    declared = nt.declarations_from_menu(["- propose_send_email — args: to, subject, body"])
    assert [d["name"] for d in declared] == ["propose_send_email"]
    assert "approval card" in declared[0]["description"]                       # the native description, not the old menu line
    assert nt.declarations_from_menu(["- search_code — args: query"]) and "propose_send_email" not in [d["name"] for d in nt.declarations_from_menu(["- search_code — args: query"])]


def test_a_native_call_stops_the_loop_for_the_caller_to_build_the_approval_card_and_nothing_runs():
    """The same signal the old loop raises: the caller (agent_workflow) turns it into the approval card; the dispatcher is never asked to send anything."""
    ran = []

    async def act(decision):
        ran.append(decision)
        return "ok"

    class LLM:
        def __init__(self):
            self.replies = [AIMessage(content="", tool_calls=[{"name": "propose_send_email", "args": {"to": "bob@example.com", "subject": "PR summary", "body": "Three PRs merged."}, "id": "e1"}])]

        async def ainvoke_tools(self, messages, tools):
            return self.replies.pop(0)

    with pytest.raises(_UnsafeActionRequested) as raised:
        asyncio.run(nl.run(llm=LLM(), tools=[{"name": "search_code", "description": "find"}, {"name": "propose_send_email", "description": "email"}], act=act, question="email bob the summary",
                           is_unsafe=lambda d: d["tool_action"] == "propose_send_email"))
    assert raised.value.decision["tool_action"] == "propose_send_email"
    assert raised.value.decision["args"] == {"to": "bob@example.com", "subject": "PR summary", "body": "Three PRs merged."}
    assert ran == []


def test_registry_schema():
    entry = nt.REGISTRY['propose_send_email']
    assert set(entry['properties']) >= {'to', 'subject', 'body'}
    assert entry['required'] == ['to', 'subject', 'body']


def test_not_an_action_tool():
    assert 'propose_send_email' not in nl.ACTION_TOOLS                          # an approval card is not an applied action: it must not earn steps back



def test_the_code_plan_tool_is_gone_from_the_native_registry_and_from_the_old_menu():
    assert "propose_code_plan" not in nt.REGISTRY
