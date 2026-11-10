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
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), answers("Done with the backend. Should I read dashboard.tsx next?"), asks(call("read_repo_file", {"path": "dashboard.tsx"})), answers("Read it: the page lists rows by hand."))
    result, counts = drive(monkeypatch, llm)
    assert result["final_answer"] == "Read it: the page lists rows by hand." and counts.get("answer_sent_back_handback_offer") == 1
    assert any("you have" in t and "tool steps left" in t and "ask_choice" in t for t in sent_texts(llm))


def test_a_second_hand_back_in_the_same_turn_stands_so_it_is_never_a_loop(monkeypatch):
    offer = "Should I read dashboard.tsx next?"
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), answers(offer), answers(offer))
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

    assert {"answer_sent_back_handback_offer", "answer_sent_back_handback_verify", "answer_sent_back_handback_failed", "answer_sent_back_handback_unfinished"} <= set(self_counters.ANSWER_CHECK_KEYS)
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


# ---- an answer right after a check that failed ---------------------------------------------------------------------------------------------

FAILED = {"action_desc": "run_local_check(check=tests, files=['page.test.mjs'])", "observation": "LOCAL CHECK tests FAILED (exit code 4, 1.6 s).\nOutput: ERROR: not found: page.test.mjs"}


def test_the_last_check_failed_only_when_it_is_the_last_thing_the_turn_did():
    assert hb.last_check_failed([FAILED]) is True
    assert hb.last_check_failed([{"action_desc": "run_local_command(line=npm test)", "observation": "LOCAL COMMAND npm FAILED (exit code 1, 3 s)."}]) is True
    assert hb.last_check_failed([{**FAILED, "observation": "LOCAL CHECK tests PASSED (exit code 0, 1 s)."}]) is False
    assert hb.last_check_failed([{**FAILED, "observation": "LOCAL CHECK tests COULD NOT RUN: the runner went away."}]) is False       # not a failure of the code: another message
    assert hb.last_check_failed([FAILED, {"action_desc": "read_repo_file(path=a.py)", "observation": "x"}]) is False                  # it read something after: the failure was looked at
    assert hb.last_check_failed([]) is False and hb.last_check_failed(None) is False


def test_an_answer_right_after_a_failed_check_is_a_failed_hand_back_whatever_it_says_and_a_note_names_the_way_out():
    assert kind("All done, the edits are in.", check_failed=True, checked=True) == "failed"
    assert kind("All done.", check_failed=True, coding_turn=False) is None and kind("All done.", check_failed=True, steps_left=0) is None
    note = hb.notice("failed", 17)
    assert "17 tool steps left" in note and "pytest cannot run a JavaScript test file" in note and "run_local_command" in note and "fix it and run the check again" in note and "Do not hand the person commands" in note


def test_a_failed_check_followed_by_an_answer_is_sent_back_once_and_the_retry_stands(monkeypatch):
    class Act:
        async def __call__(self, decision):
            name = decision["tool_action"]
            if name == "run_local_check":
                return "LOCAL CHECK tests FAILED (exit code 4, 1.6 s).\nOutput: ERROR: not found"
            if name == "run_local_command":
                return "LOCAL COMMAND npm PASSED (exit code 0, 3 s)."
            return "Proposed 1 change: APPLIED: a.tsx"

    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), asks(call("run_local_check", {"check": "tests", "files": ["page.test.mjs"]})), answers("The check failed because the file was not found."),
                  asks(call("run_local_command", {"line": "npm test"})), answers("I ran the page tests with npm test: they pass."))
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    result = asyncio.run(nl.run(llm=llm, tools=EDIT_TOOLS + [{"name": "run_local_command", "description": "run a command"}], act=Act(), question="add the badge", max_steps=8))
    assert result["final_answer"].startswith("I ran the page tests") and sum(c.get("answer_sent_back_handback_failed", 0) for c in recorded) == 1
    assert any("Your last check FAILED" in t for t in sent_texts(llm))


# ---- an answer that says the work is not finished, or promises the next step in words (2026-11-09, the Office Hours run: a tested foundation, then 18 steps unused) -------------------------------------------

REAL_SHAPE = ("I've laid the foundation for tracking covered items in the meeting code, but it's not fully wired into the live flow yet.\nWhat needs to be finished:\n1. Pass coverage to the prompt context.\n"
              "2. Hook up the trigger.\n\nI'll read the context builder next, wire covered through it, and hook up the trigger.")


def test_an_answer_that_says_what_is_left_or_promises_the_next_step_is_an_unfinished_hand_back():
    assert kind(REAL_SHAPE, checked=True) == "unfinished"
    for ending in (
        "The badge is added, but it is not yet connected to the tab menu.",
        "Done with the backend. What's left is the page rows.",
        "That covers it. The remaining work is the tests for the page.",
        "Tests pass. I'll now wire the state into the context builder.",
        "Next I will update the frontend rows.",
        "The helpers are in; the trigger still needs to be hooked up.",
    ):
        assert kind(ending, checked=True) == "unfinished", ending


def test_a_finished_answer_and_ordinary_wording_are_not_unfinished():
    for ending in (
        "The change is in and the tests pass. Nothing else is needed.",
        "Done. I'll keep it short: the badge now updates when you approve a page.",
        "It is wired through the context builder and the page, and both test files pass.",
        "Let me know if anything looks off.",
        "I changed one file. The other files were already correct.",
    ):
        assert kind(ending, checked=True) is None, ending
    assert kind(REAL_SHAPE, checked=True, coding_turn=False) is None and kind(REAL_SHAPE, checked=True, steps_left=0) is None


def test_the_unfinished_note_names_the_steps_the_way_out_and_the_ask_choice_exit():
    note = hb.notice("unfinished", 18)
    assert "18 tool steps left" in note and "Do it now" in note and "really blocks you" in note and "ask_choice" in note and "list of what still needs doing" in note


class Ending:
    """What the trace is told at the end of a turn."""

    def __init__(self):
        self.steps = []

    async def emit_step(self, title, detail, step_id, tool=None, subject=None, last_step=None, turn_end=None):
        self.steps.append({"title": title, "tool": tool, "turn_end": turn_end})


def run_turn(monkeypatch, llm, **kwargs):
    recorded, ending = [], Ending()
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))

    class Act:
        async def __call__(self, decision):
            return {"propose_local_edits": "Proposed 1 change: APPLIED: src/app.py", "run_local_check": "LOCAL CHECK tests PASSED (exit code 0, 1 s)."}.get(decision["tool_action"], "result of " + decision["tool_action"])

    result = asyncio.run(nl.run(llm=llm, tools=EDIT_TOOLS, act=Act(), question="make the meeting remember covered items", max_steps=8, emit_step=ending.emit_step, **kwargs))
    counts = {}
    for batch in recorded:
        for k, v in batch.items():
            counts[k] = counts.get(k, 0) + v
    return result, counts, ending


def test_an_unfinished_answer_is_sent_back_once_for_free_and_the_work_that_follows_stands(monkeypatch):
    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), asks(call("run_local_check", {"check": "tests"})), answers(REAL_SHAPE),
                  asks(call("read_repo_file", {"path": "context.py"})), answers("Wired it through the context builder; both test files pass."))
    result, counts, ending = run_turn(monkeypatch, llm)
    assert result["final_answer"].startswith("Wired it") and counts.get("answer_sent_back_handback_unfinished") == 1 and not counts.get("advisor_handback")
    assert ending.steps[-1]["turn_end"] == "answered"


def test_handing_the_work_back_again_after_the_free_send_back_asks_the_stronger_model_once(monkeypatch):
    from backend.utils import advisor as advisor_module

    asked = []

    async def strong(prompt):
        asked.append(prompt)
        return "Read context.py, then pass covered_indexes(run) into build_meeting_text there."

    llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), asks(call("run_local_check", {"check": "tests"})), answers(REAL_SHAPE), answers(REAL_SHAPE),
                  asks(call("read_repo_file", {"path": "context.py"})), answers("Done: wired and tested."))
    result, counts, ending = run_turn(monkeypatch, llm, advisor=advisor_module.Advisor(strong))
    assert result["final_answer"] == "Done: wired and tested." and counts.get("answer_sent_back_handback_unfinished") == 1 and counts.get("advisor_handback") == 1
    assert len(asked) == 1 and "ended its turn handing work back" in asked[0] and "WHAT IT SAID INSTEAD OF ACTING" in asked[0]
    assert ending.steps[-1]["turn_end"] == "answered"


def test_a_blocked_opinion_or_no_advisor_leaves_the_answer_standing_and_the_ending_says_it_stopped_early(monkeypatch):
    from backend.utils import advisor as advisor_module

    async def blocked(prompt):
        return "BLOCKED: the run document has no place to keep this and that is a decision for the person."

    for kwargs in ({}, {"advisor": advisor_module.Advisor(blocked)}):
        llm = ToolLLM(asks(call("propose_local_edits", {"summary": "s", "edits": []})), asks(call("run_local_check", {"check": "tests"})), answers(REAL_SHAPE), answers(REAL_SHAPE))
        result, counts, ending = run_turn(monkeypatch, llm, **kwargs)
        assert result["final_answer"] == REAL_SHAPE and counts.get("turn_end_stopped_early") == 1 and "turn_end_answered" not in counts
        assert ending.steps[-1]["turn_end"] == "stopped_early" and ending.steps[-1]["title"] == "Stopped early: work left, steps unused"


def test_the_stopped_early_words_and_counter_and_the_advisor_kind_are_in_place():
    from backend.utils import advisor as advisor_module
    from backend.utils import loop_progress as lp

    said = lp.explain("stopped_early", steps_left=18)
    assert said["title"] == "Stopped early: work left, steps unused" and "18 steps unused" in said["detail"] and "1 step unused" in lp.explain("stopped_early", steps_left=1)["detail"]
    assert lp.counts("stopped_early", None) == {"turn_end_stopped_early": 1}
    assert "handback" in advisor_module.KINDS and "handing work back" in advisor_module.build_brief("handback", "do the thing", None, [], model_answer="I will do it next")


def test_every_kind_the_check_can_return_has_a_counter_and_is_covered_by_the_once_per_turn_guard():
    from backend.utils import self_counters

    assert set(hb.KINDS) == {"offer", "verify", "failed", "unfinished", "choice"}
    assert {f"answer_sent_back_handback_{k}" for k in hb.KINDS} <= set(self_counters.ANSWER_CHECK_KEYS)
    source = (ROOT / "backend" / "services" / "native_loop.py").read_text(encoding="utf-8")
    assert source.count("for k in handback_check.KINDS") == 2                         # the free send-back and the advisor hand-off both read the same list


# ---- a choice put to the person in words, with ask_choice in the menu (2026-11-10) ------------------------------------------------------------------------------------------------------------------------------

OPTIONS_IN_WORDS = ("I could not finish this in this mode. The open piece is the trigger itself:\n1. Match on reply: when Sonic asks agenda item N and you reply, mark it covered. Simple and cheap.\n"
                    "2. Judge the turn: run a lightweight model call after each meeting turn. More flexible, one extra call.\n\nWhich approach do you want to go with?")


def test_options_listed_in_words_with_a_question_are_a_choice_hand_back_only_when_ask_choice_is_offered():
    assert kind(OPTIONS_IN_WORDS, edited=False, offered=OFFERED | {"ask_choice"}) == "choice"
    assert kind(OPTIONS_IN_WORDS, edited=False, offered=OFFERED) is None                                    # no card to use: the words stand
    assert kind(OPTIONS_IN_WORDS, edited=False, offered={"ask_choice"}, coding_turn=False) is None
    note = hb.notice("choice", 10)
    assert "10 tool steps left" in note and "ask_choice" in note and "recommended" in note and "Do not list the options" in note


def test_a_list_that_is_not_a_choice_or_a_question_that_is_not_about_choosing_is_not_a_choice_hand_back():
    for ending in (
        "What changed:\n1. Added the helper to meetings.py.\n2. Added tests for it.\nBoth test files pass.",
        "Two things I found:\n- the helper is unused\n- the tests do not cover the trigger\nDoes that match what you expected?",
        "Which file is it in?",
    ):
        assert kind(ending, edited=False, offered=OFFERED | {"ask_choice"}) not in ("choice",), ending


def test_a_question_about_code_that_ends_by_offering_to_make_a_change_is_not_a_hand_back(monkeypatch):
    """The over-correction guard: nothing was changed and no change was asked for, so "want me to update X?" is a good answer and nothing is forced."""
    for ending in ("The helper is only used in tests. Want me to update the page to call it?", "That function is unfinished: it is not wired up. I'll leave it unless you want me to hook it up.",
                   "There are two ways to do it:\n1. Match on reply: cheap.\n2. Judge the turn: flexible.\n\nWhich approach do you prefer?"):
        llm = ToolLLM(asks(call("read_repo_file", {"path": "a.py"})), answers(ending))
        result, counts = drive(monkeypatch, llm)
        assert result["final_answer"] == ending and "handback" not in str(counts), ending
    sent = drive(monkeypatch, ToolLLM(asks(call("read_repo_file", {"path": "a.py"})), answers("It is fine. Should I read dashboard.tsx next?")))[1]
    assert "handback" not in str(sent)


def test_a_turn_that_must_propose_an_edit_is_in_scope_even_before_any_edit_exists():
    source = (ROOT / "backend" / "services" / "native_loop.py").read_text(encoding="utf-8")
    assert "return claim_checking and (required_action is not None or plan_budget is not None or edited_this_turn())" in source
    assert source.count("handback_in_scope() and") == 3                                  # the free send-back, the advisor hand-off and the stopped-early ending
