"""Hardening for "Sonic says it can't edit files when it can": recognising an edit request,
scrubbing stale refusals out of what the model sees, and the mechanical guards (routing backstop,
required-action loop gate, honest note on failure) that don't depend on the model choosing to
cooperate. Every phrase below is real — taken from the threads where this went wrong."""
import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent_workflow as aw
from backend.tests.test_local_edits import FILES, _connect_folder
from backend.utils.agent_utils import (
    is_stale_capability_denial,
    looks_like_edit_request,
    scrub_stale_capability_denials,
)


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


H, A = HumanMessage, AIMessage

ASKED = "can you updatethe trace-sidebar so it just says Tracing request instead of listing all the steps?"
OFFERED = A(content="That makes sense, Jack. Would you like me to go ahead and make that update to the file?")
DENIED = A(content="I would love to, but I can only read and search your repository files, so I cannot write the changes to Chat.tsx for you.")


# ---------------------------------------------------------------------------
# looks_like_edit_request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "change the header text in Chat.tsx",
    "can you make the loading spinner spin faster in index.css",
    "add a close button to the tooltip popover",
    "fix the button color on the landing page",
    ASKED,
])
def test_direct_edit_requests_are_recognised(text):
    assert looks_like_edit_request([H(content=text)])


@pytest.mark.parametrize("text", [
    "what is the capital of france",
    "thanks, that makes sense",
    "can you make your replies shorter from now on?",
    "update my memory, I prefer dark mode",
    "i am feeling pretty low today honestly",
    "what does the greet function do?",
])
def test_ordinary_messages_are_not_edit_requests(text):
    assert not looks_like_edit_request([H(content=text)])


@pytest.mark.parametrize("follow_up", [
    "make the change",
    "APPLY THE CHANGE",
    "make the change saapp.",
    "do it",
    "yes please apply that",
    "ugh just make the edit already",
    "why wont you just make the change",
    "well you're letting me down becaues you CAN apply the change but are actively refusing to do so.",
])
@pytest.mark.parametrize("reply", [OFFERED, DENIED])
def test_follow_ups_after_an_offer_or_refusal_are_edit_requests(follow_up, reply):
    assert looks_like_edit_request([H(content=ASKED), reply, H(content=follow_up)])


def test_the_real_thread_is_still_recognised_after_its_refusals_were_scrubbed():
    """The order that matters in the app: refusals are scrubbed first, THEN the detector runs — and
    by the fourth 'make the change' the original ask is several messages back."""
    thread = [
        H(content=ASKED), OFFERED,
        H(content="make the change"), DENIED,
        H(content="APPLY THE CHANGE"), DENIED,
        H(content="well you're letting me down becaues you CAN apply the change but are actively refusing to do so."), DENIED,
        H(content="make the change saapp."),
    ]

    assert looks_like_edit_request(scrub_stale_capability_denials(thread))
    assert looks_like_edit_request(thread)


def test_a_bare_confirmation_with_no_edit_context_is_not_an_edit_request():
    messages = [H(content="what's a good pizza place"), A(content="Try Pizza Ranch."), H(content="do it")]
    assert not looks_like_edit_request(messages)


def test_thanks_after_a_refusal_is_not_an_edit_request():
    assert not looks_like_edit_request([H(content=ASKED), DENIED, H(content="no worries, thanks")])


def test_empty_history_is_not_an_edit_request():
    assert not looks_like_edit_request([])
    assert not looks_like_edit_request([A(content="hi")])


# ---------------------------------------------------------------------------
# stale refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I do not have a file-writing or editing tool enabled in this current session to physically modify Chat.tsx.",
    "I only have the ability to read and search your files.",
    "Since I am only able to read and search through your repository files, I cannot write the changes.",
    "I don't have a direct file-writing tool active to physically modify the code in your workspace.",
])
def test_refusals_to_edit_files_are_detected(text):
    assert is_stale_capability_denial(text)


@pytest.mark.parametrize("text", [
    "I can't replace the people in your life, but I'm here.",
    "I cannot promise how she will respond, Jack.",
    "I don't have access to her messages, only what you paste.",
    "I proposed the change to Chat.tsx — click Apply to write it.",
])
def test_unrelated_cant_statements_are_not_flagged(text):
    assert not is_stale_capability_denial(text)


def test_scrub_replaces_refusals_without_mutating_the_original_messages():
    refusal = A(content="I do not have a file-writing tool enabled, so I cannot modify Chat.tsx.")
    kind = A(content="I can't promise she'll answer, but I'm here with you.")
    human = H(content="make the change")
    original = [human, refusal, kind]

    scrubbed = scrub_stale_capability_denials(original)

    assert scrubbed[0] is human
    assert scrubbed[1].content == "(I hadn't made that change yet.)"
    assert scrubbed[2] is kind  # an unrelated "I can't" is left alone
    assert "file-writing" in refusal.content  # the saved transcript is never altered
    assert original == [human, refusal, kind]


# ---------------------------------------------------------------------------
# run_react_loop required_action
# ---------------------------------------------------------------------------


PROPOSED_OK = "Proposed 1 edit(s) across 1 file(s): a.py. The user now sees a diff with an Apply button."








# ---------------------------------------------------------------------------
# reasoner routing backstop
# ---------------------------------------------------------------------------

def _classified_as(**flags):
    return AsyncMock(return_value=SimpleNamespace(content=json.dumps({"needs_conversation": True, **flags})))


@run_async
async def test_reasoner_routes_an_edit_request_to_the_tool_agent_when_a_folder_is_connected(monkeypatch):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as())  # the classifier called it plain chat
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    state = {"username": "jack", "messages": [H(content=ASKED), DENIED, H(content="make the change saapp.")]}

    result = await aw.reasoner_node(state)

    assert result["reasoner_flags"]["needs_github_search"] is True
    assert result["reasoner_flags"]["needs_conversation"] is False


@run_async
async def test_reasoner_leaves_edit_phrasing_alone_without_a_connected_folder(monkeypatch):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as())
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": False})
    state = {"username": "jack", "messages": [H(content="change the header text in Chat.tsx")]}

    result = await aw.reasoner_node(state)

    assert not result["reasoner_flags"].get("needs_github_search")


@run_async
async def test_reasoner_does_not_hijack_ordinary_chat_when_a_folder_is_connected(monkeypatch):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as())
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    state = {"username": "jack", "messages": [H(content="i am feeling pretty low today honestly")]}

    result = await aw.reasoner_node(state)

    assert not result["reasoner_flags"].get("needs_github_search")
    assert result["reasoner_flags"]["needs_conversation"] is True


@run_async
async def test_reasoner_does_not_override_a_request_another_node_owns(monkeypatch):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _classified_as(needs_create_pr=True))
    monkeypatch.setattr(aw, "workspace_status", lambda user: {"connected": True})
    state = {"username": "jack", "messages": [H(content="create a pull request to update the readme file")]}

    result = await aw.reasoner_node(state)

    assert result["reasoner_flags"]["needs_create_pr"] is True
    assert not result["reasoner_flags"].get("needs_github_search")


# ---------------------------------------------------------------------------
# tool_agent_node: required_action wiring + honest note when the turn ends without a proposal
# ---------------------------------------------------------------------------

async def _run_turn(monkeypatch, question, attempts, extra_state=None):
    from backend.services import repo_checkout as rc
    from backend.tests.test_tool_agent_node import _setup_github_repo

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    captured = {}

    async def fake_native_run(**kwargs):
        captured.update(kwargs)
        return {"final_answer": "I looked around.", "attempts": attempts, "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    state = {"username": "jack", "messages": [H(content=question)], "documents": [], **(extra_state or {})}
    result = await aw.tool_agent_node(state)
    return result["content_to_format"], captured


@run_async
async def test_edit_turn_requires_the_proposal_and_says_so_honestly_when_none_was_made(monkeypatch):
    _connect_folder()

    text, captured = await _run_turn(monkeypatch, "change the greeting text in src/app.py", attempts=[])

    assert captured["required_action"] is aw._LOCAL_EDIT_REQUIRED_ACTION
    assert "IS connected and edits CAN be proposed" in text
    assert "local-rag" in text


@run_async
async def test_no_failure_note_when_a_proposal_was_made(monkeypatch):
    _connect_folder()
    attempts = [{"purpose": "p", "action_desc": "propose_local_edits(summary=s)", "observation": PROPOSED_OK}]

    text, _ = await _run_turn(monkeypatch, "change the greeting text in src/app.py", attempts=attempts)

    assert "IS connected" not in text


@run_async
async def test_a_plain_question_with_a_connected_folder_is_not_an_edit_turn(monkeypatch):
    _connect_folder()

    text, captured = await _run_turn(monkeypatch, "what does the greet function do?", attempts=[])

    assert captured["required_action"] is None
    assert "IS connected" not in text


@run_async
async def test_edit_wording_without_a_connected_folder_is_not_an_edit_turn(monkeypatch):
    text, captured = await _run_turn(monkeypatch, "change the greeting text in src/app.py", attempts=[])

    assert captured["required_action"] is None
    assert "IS connected" not in text


def _offered(captured):
    return {tool["name"] for tool in captured["tools"]}


@run_async
async def test_a_browser_with_no_folder_gets_no_edit_tools_even_though_the_server_holds_the_folder(monkeypatch):
    """The server's copy belongs to the person, so a phone sees it too; a diff proposed there has no Apply button (the page said folder_expected: false)."""
    _connect_folder()

    text, captured = await _run_turn(monkeypatch, "change the greeting text in src/app.py", attempts=[], extra_state={"folder_in_browser": False})

    assert captured["required_action"] is None                                    # not forced to propose what cannot be applied
    assert "propose_local_edits" not in _offered(captured)
    assert "propose_commit" in _offered(captured)                                  # a commit needs no folder in this browser: it works from the server's copy and their GitHub token (a phone said "no tool options" without it)
    assert "THIS BROWSER HAS NO FOLDER CONNECTED" in captured["environment_note"]
    assert "do the work by calling propose_local_edits" not in captured["environment_note"]
    assert "IS connected" not in text                                              # the "it was connected and you did not propose" note would be false here


@run_async
async def test_the_browser_that_holds_the_folder_and_a_page_that_does_not_say_keep_the_edit_tools(monkeypatch):
    _connect_folder()
    for extra in ({"folder_in_browser": True}, {}):                               # {}: an older page that sends nothing keeps today's behaviour
        _, captured = await _run_turn(monkeypatch, "change the greeting text in src/app.py", attempts=[], extra_state=extra)
        assert captured["required_action"] is aw._LOCAL_EDIT_REQUIRED_ACTION, extra
        assert "propose_local_edits" in _offered(captured), extra
        assert "do the work by calling propose_local_edits" in captured["environment_note"], extra


@run_async
async def test_a_browser_with_no_folder_can_still_read_the_copy_on_the_server(monkeypatch):
    _connect_folder()

    _, captured = await _run_turn(monkeypatch, "what does the greet function do?", attempts=[], extra_state={"folder_in_browser": False})

    assert "read_repo_file" in _offered(captured)
    assert "read-only from this device" not in captured["environment_note"]       # that wording is for the trace line; the model is told in the LOCAL WORKSPACE note
    assert "THIS BROWSER HAS NO FOLDER CONNECTED" in captured["environment_note"]


def test_the_page_says_whether_it_holds_the_folder_and_the_server_reads_it_three_ways():
    source = open("app.py", encoding="utf-8").read()
    assert "folder_expected: Optional[bool] = None" in source                      # None = an older page that does not say: keeps today's behaviour
    assert '"folder_in_browser": request.folder_expected is not False' in source
    assert "folder_usable_here = folder_connected and (demo_turn or request.folder_expected is not False)" in source
    assert "if folder_usable_here:\n        messages_state = scrub_stale_capability_denials" in source
    assert "if folder_usable_here and not is_guest_username(username) and not demo_turn:" in source          # plan mode is never offered on the demo project
    assert "not (folder_usable_here and is_stale_capability_denial(full_response))" in source
