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
from backend.tests.test_react_loop_retry_enforcement import GROUNDING_CHECK_MARKER, _llm_response
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

async def _run_loop(responses, act, *, required=aw._LOCAL_EDIT_REQUIRED_ACTION, max_iterations=8):
    prompts = []
    index = {"n": 0}

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        prompts.append(prompt)
        response = responses[min(index["n"], len(responses) - 1)]
        index["n"] += 1
        return response

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="change the header", schema="repo=x", prompt_template="{question} | {schema} | {attempts}",
            act=act, max_iterations=max_iterations, node_name="test_node", required_action=required,
        )
    finally:
        aw.lite_llm.ainvoke = orig
    return result, prompts


PROPOSE = _llm_response(action="query", purpose="Propose it", tool_action="propose_local_edits", args={"summary": "s", "edits": []})
PROPOSED_OK = "Proposed 1 edit(s) across 1 file(s): a.py. The user now sees a diff with an Apply button."


@run_async
async def test_final_without_the_required_action_is_rejected_twice_then_accepted():
    acted = []

    async def act(decision):
        acted.append(decision)
        return "unused"

    result, prompts = await _run_loop(
        [_llm_response(action="final", answer="first"), _llm_response(action="final", answer="second"),
         _llm_response(action="final", answer="third")],
        act,
    )

    assert result["final_answer"] == "third"
    assert acted == []
    assert "propose_local_edits" in prompts[1] and "IS connected" in prompts[1]


@run_async
async def test_final_is_accepted_immediately_once_the_action_succeeded():
    acted = []

    async def act(decision):
        acted.append(decision)
        return PROPOSED_OK

    result, _ = await _run_loop([PROPOSE, _llm_response(action="final", answer="Proposed it.")], act)

    assert result["final_answer"] == "Proposed it."
    assert len(acted) == 1


@run_async
async def test_a_failed_proposal_does_not_count_as_done():
    async def act(decision):
        return "ERROR: these edits could not be applied cleanly, so nothing was proposed"

    result, prompts = await _run_loop(
        [PROPOSE, _llm_response(action="final", answer="gave up"), _llm_response(action="final", answer="gave up"),
         _llm_response(action="final", answer="really gave up")],
        act,
    )

    assert result["final_answer"] == "really gave up"
    assert any("IS connected" in p for p in prompts)


@run_async
async def test_after_a_read_the_loop_is_told_to_propose_instead_of_reading_more():
    read = lambda path: _llm_response(action="query", purpose="Read", tool_action="read_repo_file", args={"path": path})

    async def act(decision):
        return PROPOSED_OK if decision["tool_action"] == "propose_local_edits" else "file contents"

    _, prompts = await _run_loop(
        [read("a.py"), read("b.py"), read("c.py"), PROPOSE, _llm_response(action="final", answer="done")], act,
    )

    marker = "your next action must be propose_local_edits"
    assert not any(marker in p for p in prompts[:3])  # not nagged before it has had a few steps
    assert marker in prompts[3]


@run_async
async def test_no_nudge_before_anything_has_been_read():
    async def act(decision):
        return "No similar file paths found."

    find = _llm_response(action="query", purpose="Find", tool_action="find_file", args={"query": "x"})
    _, prompts = await _run_loop(
        [find, _llm_response(action="query", purpose="Find", tool_action="find_file", args={"query": "y"}),
         _llm_response(action="query", purpose="Find", tool_action="find_file", args={"query": "z"}),
         _llm_response(action="query", purpose="Find", tool_action="find_file", args={"query": "w"}),
         _llm_response(action="final", answer="x")],
        act,
    )

    assert not any("your next action must be propose_local_edits" in p for p in prompts)


@run_async
async def test_without_a_required_action_nothing_changes():
    async def act(decision):
        return "unused"

    result, _ = await _run_loop([_llm_response(action="final", answer="just answering")], act, required=None)

    assert result["final_answer"] == "just answering"


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

async def _run_turn(monkeypatch, question, attempts):
    from backend.services import repo_checkout as rc
    from backend.tests.test_tool_agent_node import _setup_github_repo

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    captured = {}

    async def fake_run_react_loop(**kwargs):
        captured.update(kwargs)
        return {"final_answer": "I looked around.", "attempts": attempts, "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)
    state = {"username": "jack", "messages": [H(content=question)], "documents": []}
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
