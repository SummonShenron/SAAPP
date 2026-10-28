"""Three fixes from the real 2026-10-28 coding session (a 45-minute task that needed about nine Continue presses):

A. Asking for a plan ("make a plan for it", "use plan_mode") counts as the change request plan mode is for, so the plan card appears.
B. A bare yes ("yes", "finish it!!!") after Sonic offered to change a file counts as the confirmation, so propose_local_edits is required that turn and it does not read again instead.
C. When plan mode is on and a change request still cannot be a plan turn, the person is told why in one line, and the plan gate and the engine choice look at the same words.

The messages below are the real ones from that session (shortened)."""
import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent_workflow as aw
from backend.services import native_loop
from backend.services import repo_checkout as rc
from backend.tests.test_local_edits import _connect_folder, run_async
from backend.utils import agent_utils as au

ROOT = pathlib.Path(__file__).resolve().parents[2]

TASK = ("Task: make an expanded step in the execution trace panel show WHAT it read or found. EPHEMERAL: the excerpt goes only to the requester's own browser. "
        "Update backend/utils/agent_utils.py and local/src/components/TracePanel.tsx.")
TASK_NO_BROWSER = TASK.replace("browser", "screen")


def h(text):
    return HumanMessage(content=text)


def a(text):
    return AIMessage(content=text)


# ---- B: a bare yes after an offered change --------------------------------------------------------------------------------------------------------

OFFERS = [
    "...Say the word and I'll submit this file edit and run test_trace_excerpt.py to confirm the fix. <<<FOLLOW_UP: Should I apply this update to agent_utils.py and run the tests?>>>",
    "...I can drop the dead function from agent_utils.py right now and run pytest. <<<FOLLOW_UP: Should I remove the duplicate helper from agent_utils.py and run the test suite now?>>>",
    "...<<<FOLLOW_UP: Should I wire build_tool_excerpt into the read execution step in react_loop.py now?>>>",
    "Want me to read past line 70 in agent_utils.py to see if _SENSITIVE_PATH_RE is lower down, or just define the pattern directly where it belongs?",
    "...Here is the exact change ready to go. Want me to apply this update to agent_utils.py so you can run the test suite again?",
]


@pytest.mark.parametrize("offer", OFFERS)
@pytest.mark.parametrize("yes", ["yes", "yes!", "yes please.", "finish it!!!", "yes keep going", "ok", "do it", "go on"])
def test_a_bare_yes_after_sonic_offered_to_change_a_file_is_the_confirmation(offer, yes):
    assert au.looks_like_edit_request([h(TASK_NO_BROWSER), a(offer), h(yes)]) is True, (yes, offer)


@pytest.mark.parametrize("offer", [
    "Want me to remind you about the dentist tomorrow?",
    "I can summarize that article for you. Should I?",
    "Should I run pytest backend/tests/test_trace_excerpt.py now to verify the pass?",         # running a test is not changing a file
    "Here is how the loop works. Let me know if anything is unclear.",
    "",
])
def test_a_bare_yes_is_not_an_edit_when_nothing_offered_a_change_even_in_a_thread_that_had_one(offer):
    assert au.looks_like_edit_request([h(TASK_NO_BROWSER), a("Done, the file is updated."), h("thanks"), a(offer), h("yes")]) is False


def test_a_longer_reply_is_not_a_bare_yes_and_a_yes_with_nothing_before_it_is_nothing():
    assert au.looks_like_edit_request([h(TASK_NO_BROWSER), a(OFFERS[0]), h("yes but first tell me how the cap works")]) is False
    assert au.looks_like_edit_request([h("yes")]) is False
    assert au.offered_a_change("") is False and au.offered_a_change(None) is False


def test_only_the_end_of_the_last_reply_counts_as_the_offer():
    old_offer = OFFERS[0]
    later = "I looked at it and the cap is fine. " + "x" * 400 + " Nothing else to do on that one."
    assert au.looks_like_edit_request([h(TASK_NO_BROWSER), a(old_offer + later), h("yes")]) is False


def test_the_existing_confirmations_and_requests_still_work():
    assert au.looks_like_edit_request([h("change the header text in Chat.tsx")]) is True
    assert au.looks_like_edit_request([h(TASK_NO_BROWSER), a("Want me to make the change?"), h("do it")]) is True
    assert au.looks_like_edit_request([h("what does the header in Chat.tsx do?")]) is False
    assert au.looks_like_edit_request([h("make your replies shorter")]) is False


# ---- A: asking for a plan ---------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ask", ["yes make a plan for it", "yes saapp use plan_mode come on", "make a plan for the task", "its on make a fucking plan yes", "create a plan", "plan it", "use plan mode"])
def test_asking_for_a_plan_in_a_coding_thread_is_a_plan_request(ask):
    assert au.looks_like_plan_request([h(TASK_NO_BROWSER), a("Here is how I would do it."), h(ask)]) is True, ask


@pytest.mark.parametrize("ask", ["make a plan for my week", "what is a plan?", "yes", "the plan is to ship friday", "explain plan mode in detail please and also how it decides which steps are done after each edit applies and what happens when it runs out of steps"])
def test_it_is_not_a_plan_request_without_a_coding_thread_or_a_plain_ask(ask):
    assert au.looks_like_plan_request([h("hi"), a("hello"), h(ask)]) is False
    assert au.looks_like_plan_request([]) is False


def test_a_message_that_is_both_a_plan_ask_and_a_change_request_counts_on_its_own():
    assert au.looks_like_plan_request([h("make a plan to change the header in Chat.tsx")]) is True


# ---- C: which words the engine choice looks at, and why a mode was skipped --------------------------------------------------------------------------

def test_the_engine_text_is_the_latest_message_and_the_one_human_message_before_it():
    messages = [h("first"), a("reply"), h("second"), a("reply two"), h("third")]
    assert au.recent_user_text("third", messages) == "third\nsecond"
    assert au.recent_user_text("only", [h("only")]) == "only"
    assert au.recent_user_text("x", []) == "x"


def test_the_word_that_sent_a_turn_to_the_other_engine_is_named():
    assert native_loop.old_engine_reason("goes only to the requester's own browser") == "browser"
    assert native_loop.old_engine_reason("Query the DATABASE for me") == "database"
    assert native_loop.old_engine_reason("please run the tests") == "run the tests"
    assert native_loop.old_engine_reason("please run the tests", test_runs_native=True) == ""
    assert native_loop.old_engine_reason("move the header", {"needs_send_email": True}) == "needs_send_email"
    assert native_loop.old_engine_reason("make the greeting friendlier") == ""


# ---- through the tool agent: the real gate ----------------------------------------------------------------------------------------------------------

async def _turn(monkeypatch, messages, *, phase="plan"):
    """Runs tool_agent_node with a folder connected and plan mode asking for a plan turn. Returns what the loop was given and what was emitted."""
    from backend.tests.test_tool_agent_node import _setup_github_repo

    _connect_folder()
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_db", lambda: None)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {}

    async def fake_loop(**kwargs):
        seen["tools"] = [t["name"] for t in (kwargs.get("tools") or [])]
        seen["required"] = kwargs.get("required_action")
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_loop)
    monkeypatch.setattr(aw, "run_react_loop", fake_loop)
    await aw.tool_agent_node({"username": "jack", "messages": messages, "documents": [], "code_plan_phase": phase})
    seen["events"] = [(c.args[0], c.args[1]) for c in emit.call_args_list]
    seen["noted"] = [data["text"] for name, data in seen["events"] if name == "plan_skipped"]
    return seen


@run_async
async def test_asking_for_a_plan_gets_the_plan_card_tool_where_it_used_to_get_a_text_plan(monkeypatch):
    seen = await _turn(monkeypatch, [h(TASK_NO_BROWSER), a("Here is the investigation."), h("yes make a plan for it")])
    assert "submit_plan" in seen["tools"] and not seen["noted"]


@run_async
async def test_a_word_in_the_message_before_no_longer_lets_the_plan_gate_say_yes_and_the_engine_say_no(monkeypatch):
    # "browser" is in the task message, two messages back from the ask. Before: the gate looked only at the latest message and approved a plan turn, the engine choice looked at two and
    # ran the old engine, which has no plan card. Now both look at the same words and the person is told.
    seen = await _turn(monkeypatch, [h(TASK), a("Here is the investigation."), h("go ahead and make a plan for the task")])
    assert "submit_plan" not in seen["tools"]
    assert len(seen["noted"]) == 1 and '"browser"' in seen["noted"][0] and "Plan first is on" in seen["noted"][0]


@run_async
async def test_a_plan_the_gate_dropped_does_not_leave_the_turn_forced_to_edit_when_only_a_plan_was_asked_for(monkeypatch):
    seen = await _turn(monkeypatch, [h(TASK), a("ok"), h("make a plan for the task")])
    assert "submit_plan" not in seen["tools"]
    assert not seen["required"]                                                                  # a request for a plan must not become "you must propose an edit"


@run_async
async def test_no_line_is_shown_for_a_question_that_was_never_a_change_request(monkeypatch):
    seen = await _turn(monkeypatch, [h("what does the greeting function in src/app.py do?")])
    assert "submit_plan" not in seen["tools"] and not seen["noted"]


@run_async
async def test_with_plan_mode_off_nothing_changes_and_nothing_is_said(monkeypatch):
    seen = await _turn(monkeypatch, [h(TASK), a("ok"), h("make a plan for the task")], phase=None)
    assert "submit_plan" not in seen["tools"] and not seen["noted"]


def test_the_note_reaches_the_page_as_the_noted_line_the_page_already_shows():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'event.get("name") == "plan_skipped"' in source and "'event': 'noted'" in source
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "payload.event === 'noted'" in chat


def test_both_the_gate_and_the_engine_choice_use_the_one_shared_text():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert source.count("recent_user_text(latest_message_content, state.get(\"messages\", []))") == 2
    assert "needs_old_engine(latest_message_content" not in source


# ---- a change described in words, with no file name (2026-10-28, 6 PM: "add one to the user message bubbles too") ---------------------------------------------

@pytest.mark.parametrize("ask", [
    "okay, i'd like you to try one more thing today, you know the timestamp you added to the ai messages? i want you to add one to the user message bubbles too.",
    "add a timestamp to the user bubbles", "make the hearts show only on hover", "change the mascot animation to be slower", "add a dropdown for the model choice",
    "tweak the layout so the checkbox sits left", 
])
def test_a_change_described_in_words_about_the_screen_is_an_edit_request(ask):
    assert au.looks_like_edit_request([h(ask)]) is True, ask


@pytest.mark.parametrize("ask", ["make your replies shorter", "update my memory", "what is a timestamp?", "how do bubbles work in chat apps?", "tell me about the layout of the project", "remind me about hover states later"])
def test_questions_and_changes_to_sonic_itself_are_still_not_edit_requests(ask):
    assert au.looks_like_edit_request([h(ask)]) is False, ask


@run_async
async def test_that_request_in_plan_mode_now_gets_the_plan_card_tool(monkeypatch):
    seen = await _turn(monkeypatch, [h("okay, i'd like you to try one more thing today, you know the timestamp you added to the ai messages? i want you to add one to the user message bubbles too.")])
    assert "submit_plan" in seen["tools"] and not seen["noted"]
