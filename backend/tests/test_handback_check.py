"""An answer that hands the next step back to the person while the loop still has steps (backend/utils/handback_check.py, the send-back in native_loop.run, the trace note in tool_agent_node).

Why (2026-11-09, three real runs, the wording below is a made-up copy of the shapes): "Should I read the dashboard next?" with 15 steps left, "draft first, or wire the rates?" with 1 left, and "to verify on your end, run pytest" after a correct
four-file change, with a runner paired and online. The send-back is mechanical, once a turn, only on a coding turn and only while a step remains."""
import asyncio
import pathlib

import pytest

from backend.services import agent_workflow as aw
from backend.services import native_loop as nl
from backend.tests.test_native_loop import TOOLS, ToolLLM, answers, asks, call
from backend.utils import handback_check as hb

ROOT = pathlib.Path(__file__).resolve().parents[2]
OFFERED = {"run_local_check", "propose_local_edits", "read_repo_file"}


def kind(answer, **kwargs):
    base = {"coding_turn": True, "edited": True, "offered": OFFERED, "steps_left": 5}
    return hb.kind(answer, **{**base, **kwargs})


# ---- what counts as a hand-back ---------------------------------------------------------------------------------------------------------

def test_an_ending_that_offers_to_carry_on_with_something_it_can_do_itself_is_an_offer():
    for ending in (
        "The backend side is in. Should I read dashboard.tsx next to see how the page picks up the rates?",
        "Want me to draft the exact row additions first, or should we wire up the last-step rates?",
        "That covers the counters. Would you like me to update the tests as well?",
        "Do you want me to run a search for the other callers?",
        "Let me know if you'd like me to check the page too?",
    ):
        assert kind(ending, edited=False) == "offer", ending


def test_a_question_about_the_person_or_a_choice_only_they_can_make_is_not_an_offer():
    for ending in (
        "Which of the two layouts do you prefer?",
        "Is it the settings page or the account page you meant?",
        "Does that match what you wanted?",
        "I changed the label. Anything else you want to look at?",
        "Should the card use the accent colour or the muted one?",
    ):
        assert kind(ending, edited=False) is None, ending


def test_telling_the_person_to_run_it_themselves_after_an_edit_is_a_verify_hand_back_only_when_a_check_tool_exists():
    ending = "To verify them on your end, run:\n`python -m pytest backend/tests/test_x.py -q`"
    assert kind(ending) == "verify"
    assert kind("You can run the tests with pytest to confirm it.") == "verify"
    assert kind("Per your workflow, revert the edits locally first to watch the new tests fail, then run pytest.") == "verify"
    assert kind(ending, offered={"read_repo_file"}) is None                                  # no way to run it: the model cannot do it itself
    assert kind(ending, edited=False) is None                                                # nothing was changed, so nothing to verify
    assert kind(ending, checked=True) is None                                                # it already ran a check this turn


def test_nothing_is_sent_back_off_a_coding_turn_or_without_a_step_left_or_for_an_answer_with_no_hand_back_at_its_end():
    offer = "Should I read dashboard.tsx next?"
    assert kind(offer, coding_turn=False) is None and kind(offer, steps_left=0) is None
    assert kind("The change is in and the tests pass. Nothing else is needed.") is None
    assert kind("") is None and kind(None) is None
    assert kind("Should I read dashboard.tsx next? " + "It is done. " * 120, edited=False) is None          # an old question far from the end is not the ending
    assert kind("If you want me to update the tests as well, say so and I will.", edited=False) is None       # an offer said as a statement leaves the next move to them, and is not a question put to them
    assert kind("Want me to update the tests " + "Then the rest is just detail. " * 40 + "Is that clear?", edited=False) is None   # the offer sits outside the end of the answer; only a question near the end ties to it


def test_a_follow_up_tag_is_not_part_of_the_answer_and_the_end_is_what_counts():
    assert kind("All done. <<<FOLLOW_UP: Should I read the dashboard next?>>>", edited=False) is None
    assert kind("Here is what I did: " + "details. " * 200 + "Should I read the dashboard next?", edited=False) == "offer"


def test_the_note_names_the_steps_left_the_way_to_do_it_and_the_ask_choice_exit():
    offer = hb.notice("offer", 15)
    assert "15 tool steps left" in offer and "ask_choice" in offer and "permission" in offer
    assert "1 tool step left" in hb.notice("offer", 1)
    verify = hb.notice("verify", 3, {"run_local_check", "run_local_command"})
    assert "run_local_check, with `files`" in verify and "not the whole suite" in verify and "revert" in verify and "run_local_command" in verify
    assert "run_local_command." in hb.notice("verify", 3, {"run_local_command"}).split("Run it yourself now: ")[1][:40] or "run_local_command" in hb.notice("verify", 3, {"run_local_command"})


def test_the_trace_note_says_whether_this_turn_could_run_checks_and_counts_it():
    title, detail, key = hb.verify_note(["tests"], True)
    assert title == "Checks available" and "tests" in detail and "run_local_check" in detail and "run_local_command" in detail and key == "coding_turns_can_verify"
    assert hb.verify_note([], True)[2] == "coding_turns_can_verify" and "run_local_check" not in hb.verify_note([], True)[1]
    title, detail, key = hb.verify_note([], False)
    assert title == "No test runner connected" and "should say what it did not verify" in detail and key == "coding_turns_no_runner"
    assert hb.verify_note(["", "  "], False)[2] == "coding_turns_no_runner"


# ---- through the loop --------------------------------------------------------------------------------------------------------------------

EDIT_TOOLS = TOOLS + [{"name": "propose_local_edits", "description": "propose a change"}, {"name": "run_local_check", "description": "run a check"}]


def drive(monkeypatch, llm, tools=EDIT_TOOLS, **kwargs):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))

    class Act:
        async def __call__(self, decision):
            name = decision["tool_action"]
            return {"propose_local_edits": "Proposed 1 change: APPLIED: src/app.py", "run_local_check": "PASSED (exit 0)\n12 passed"}.get(name, f"result of {name}")

    result = asyncio.run(nl.run(llm=llm, tools=tools, act=Act(), question="add the counters to the page", max_steps=kwargs.pop("max_steps", 8), **kwargs))
    counts = {}
    for batch in recorded:
        for k, v in batch.items():
            counts[k] = counts.get(k, 0) + v
    return result, counts


def sent_texts(llm):
    seen = []
    for messages, _tools in llm.calls:
        for message in messages:
            content = getattr(message, "content", "")
            if getattr(message, "type", "") == "human" and isinstance(content, str) and content not in seen:
                seen.append(content)
    return seen


def test_an_offer_to_carry_on_is_sent_back_once_and_the_next_answer_stands(monkeypatch):
    llm = ToolLLM(asks(call("read_repo_file", {"path": "a.py"})), answers("Done with the backend. Should I read dashboard.tsx next?"), asks(call("read_repo_file", {"path": "dashboard.tsx"})), answers("Read it: the page lists rows by hand."))
    result, counts = drive(monkeypatch, llm)
    assert result["final_answer"] == "Read it: the page lists rows by hand." and counts.get("answer_sent_back_handback_offer") == 1
    assert any("you have" in t and "tool steps left" in t and "ask_choice" in t for t in sent_texts(llm))


def test_a_second_hand_back_in_the_same_turn_stands_so_it_is_never_a_loop(monkeypatch):
    offer = "Should I read dashboard.tsx next?"
    llm = ToolLLM(asks(call("read_repo_file", {"path": "a.py"})), answers(offer), answers(offer))
    result, counts = drive(monkeypatch, llm)
    assert result["final_answer"] == offer and counts.get("answer_sent_back_handback_offer") == 1 and len(llm.calls) == 3


def test_telling_the_person_to_run_the_tests_after_an_applied_edit_is_sent_back_to_run_them_itself(monkeypatch):
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), answers("The change is in. To verify on your end, run pytest backend/tests/test_x.py."),
                  asks(call("run_local_check", {"check": "tests", "files": ["backend/tests/test_x.py"]})), answers("I ran the tests for what I changed: 12 passed."))
    result, counts = drive(monkeypatch, llm)
    assert result["final_answer"].startswith("I ran the tests") and counts.get("answer_sent_back_handback_verify") == 1
    assert any("Run it yourself now: run_local_check" in t for t in sent_texts(llm))


def test_a_turn_that_already_ran_the_check_is_not_sent_back_for_mentioning_how_to_rerun_it(monkeypatch):
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), asks(call("run_local_check", {"check": "tests"})), answers("It passes. You can re-run it later with pytest on your end."))
    result, counts = drive(monkeypatch, llm)
    assert "handback" not in str(counts) and result["final_answer"].startswith("It passes")


def test_a_turn_with_no_check_tool_is_not_told_to_run_what_it_cannot(monkeypatch):
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), answers("The change is in. To verify on your end, run pytest."))
    result, counts = drive(monkeypatch, llm, tools=TOOLS + [{"name": "propose_local_edits", "description": "propose a change"}])
    assert result["final_answer"].endswith("run pytest.") and "handback" not in str(counts)


def test_nothing_is_sent_back_when_the_budget_is_used_up(monkeypatch):
    llm = ToolLLM(asks(call("read_repo_file", {"path": "a.py"})), asks(call("read_repo_file", {"path": "b.py"})), answers("Should I read dashboard.tsx next?"))
    result, counts = drive(monkeypatch, llm, max_steps=2)
    assert "handback" not in str(counts)


# ---- the wiring --------------------------------------------------------------------------------------------------------------------------

def test_the_keys_are_registered_and_the_trace_note_is_emitted_and_counted_for_a_coding_turn_with_a_folder():
    from backend.utils import self_counters

    assert {"answer_sent_back_handback_offer", "answer_sent_back_handback_verify"} <= set(self_counters.ANSWER_CHECK_KEYS)
    assert set(self_counters.VERIFY_KEYS) == {"coding_turns_can_verify", "coding_turns_no_runner"} and set(self_counters.VERIFY_KEYS) <= set(self_counters.KEYS)
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    start = source.index("# Whether this coding turn can run checks itself")
    block = source[start: start + 1500]
    assert "if local_workspace_handle is not None and can_apply_here:" in block and "handback_check.verify_note(check_names, commands_ok)" in block
    assert 'await safe_emit_event("trace_detail", {"node": "tool_agent_node", "title": verify_title, "detail": verify_detail})' in block and "record_turn_counts, {verify_key: 1}" in block


def test_the_module_logs_nothing_and_touches_nothing():
    import ast

    source = (ROOT / "backend" / "utils" / "handback_check.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "error", "exception", "debug")]
    assert "get_db" not in source and "open(" not in source
