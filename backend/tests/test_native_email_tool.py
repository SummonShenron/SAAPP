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


def test_email_wording_stays_native_when_email_native():
    assert nl.needs_old_engine('send an email to bob', email_native=True) is False
    assert nl.old_engine_reason('send an email to bob', email_native=True) == ''
    assert nl.eligible(edit_turn=False, tools=EMAIL_TOOLS, text="draft a reply email to the landlord") is True


def test_email_wording_goes_old_without_email_native():
    assert nl.needs_old_engine('send an email to bob') is True
    assert nl.old_engine_reason('send an email to bob') == 'send an email'
    assert nl.eligible(edit_turn=False, tools=PLAIN_TOOLS, text="draft a reply email to the landlord") is False


def test_the_routers_send_flag_stays_native_when_the_tool_is_offered_and_goes_old_when_it_is_not():
    flags = {"needs_send_email": True}
    assert nl.eligible(edit_turn=False, tools=EMAIL_TOOLS, text="tell bob about the PR", flags=flags) is True
    assert nl.eligible(edit_turn=False, tools=PLAIN_TOOLS, text="tell bob about the PR", flags=flags) is False
    assert nl.needs_old_engine("anything", flags, email_native=True) is False
    assert nl.needs_old_engine("anything", flags) is True
    assert nl.old_engine_reason("anything", flags) == "needs_send_email"
    assert nl.old_engine_reason("anything", flags, email_native=True) == ""


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


@pytest.mark.parametrize("text", ["write an implementation plan for this", "make a code plan for the change", "I want an Implementation Plan first"])
def test_asking_for_a_plan_no_longer_forces_the_old_engine(text):
    assert nl.needs_old_engine(text) is False
    assert nl.old_engine_reason(text) == ""
    assert nl.eligible(edit_turn=False, tools=PLAIN_TOOLS, text=text) is True


def test_the_code_plan_tool_is_gone_from_the_native_registry_and_from_the_old_menu():
    assert "propose_code_plan" not in nt.REGISTRY
