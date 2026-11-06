"""A second opinion from the stronger model when the cheap one is stuck (backend/utils/advisor.py, wired in native_loop.run and agent_workflow.tool_agent_node)."""
import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from backend.services import agent_workflow as aw
from backend.services import native_loop as nl
from backend.tests.test_native_loop import Acts, ToolLLM, answers, asks, call
from backend.utils import advisor as ad
from backend.utils import self_counters as sc

STEP = "s2: add the schema [backend/services/native_tools.py]"
TOOLS = [{"name": "search_code", "description": "find"}, {"name": "read_repo_file", "description": "read"}]
BLOCKED = "BLOCKED: the file does not look the way the plan expects."
NEXT_CALL = "Call read_repo_file on backend/services/native_tools.py, then edit the schema there."


def run(coro):
    return asyncio.run(coro)


class Ask:
    """A stand-in for the strong model: records every prompt, answers with `reply` (or raises)."""

    def __init__(self, reply=NEXT_CALL, boom=False):
        self.prompts, self.reply, self.boom = [], reply, boom

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.boom:
            raise RuntimeError("the model is down")
        return SimpleNamespace(content=self.reply)


async def next_step():
    return STEP


async def no_step_left():
    return None


def go(llm, *, ask=None, plan_progress=next_step, question="finish the plan", tools=TOOLS, acts=None, **kwargs):
    advisor = ad.Advisor(ask) if ask is not None else None
    acts = acts or Acts({"search_code": "found", "read_repo_file": "file text"})
    result = run(nl.run(llm=llm, tools=tools, act=acts, question=question, advisor=advisor, plan_progress=plan_progress, **kwargs))
    return result, advisor


def sent(llm):
    """Every plain human note the loop added, in order."""
    seen = []
    for messages, _tools in llm.calls:
        for message in messages:
            content = getattr(message, "content", "")
            if getattr(message, "type", "") == "human" and isinstance(content, str) and content not in seen:
                seen.append(content)
    return seen


# ---- the early answer on a plan turn ------------------------------------------------------------------------------------------------------

def test_an_early_answer_is_told_once_for_free_to_do_the_next_step_and_then_stands_without_an_advisor():
    llm = ToolLLM(answers("Step one is done."), answers("Done again."))
    result, _ = go(llm)
    assert len(llm.calls) == 2 and result["final_answer"] == "Done again."
    assert any(STEP in text and "do it now" in text.lower() for text in sent(llm))


def test_a_second_early_answer_asks_the_stronger_model_once_and_its_advice_goes_back_into_the_conversation():
    ask = Ask()
    llm = ToolLLM(answers("Step one is done."), answers("Really done."), asks(call("read_repo_file", {"path": "backend/services/native_tools.py"})), answers("Now finished."))
    result, advisor = go(llm, ask=ask)
    assert len(llm.calls) == 4 and result["final_answer"] == "Now finished."          # told, asked, acted, then the answer stands (each is used once)
    assert len(ask.prompts) == 1 and advisor.counts == {"advisor_early_answer": 1}
    assert any(NEXT_CALL in text and "advice only" in text.lower() for text in sent(llm))
    assert STEP in ask.prompts[0] and "Really done." in ask.prompts[0]


def test_the_brief_is_small_whatever_the_turn_has_grown_to():
    ask = Ask()
    request = "please " + "x" * 1500 + " TAIL_OF_THE_REQUEST"
    big = [asks(call("search_code", {"query": f"q{i}"})) for i in range(6)]
    llm = ToolLLM(*big, answers("one"), answers("two"), answers("three"))
    go(llm, ask=ask, question=request, plan_progress=next_step)
    (prompt,) = ask.prompts
    brief = prompt.split("BRIEF", 1)[1]
    assert len(brief) <= ad.BRIEF_MAX_CHARS + 5 and "TAIL_OF_THE_REQUEST" not in brief
    assert brief.count("search_code(") <= ad.RECENT_STEPS                          # the last few steps only, not the six it took


def test_advice_that_says_blocked_lets_the_answer_stand():
    ask = Ask(reply=BLOCKED)
    llm = ToolLLM(answers("one"), answers("The file differs from the plan, so I stopped."))
    result, advisor = go(llm, ask=ask)
    assert len(llm.calls) == 2 and result["final_answer"].startswith("The file differs")
    assert advisor.counts == {"advisor_early_answer": 1} and ad.is_blocked(BLOCKED)


def test_a_failed_second_opinion_changes_nothing_and_is_counted():
    ask = Ask(boom=True)
    llm = ToolLLM(answers("one"), answers("two"))
    result, advisor = go(llm, ask=ask)
    assert result["final_answer"] == "two" and advisor.counts == {"advisor_failed": 1}


def test_nothing_happens_when_there_is_no_plan_step_left_or_no_plan_at_all():
    llm = ToolLLM(answers("All done."))
    result, _ = go(llm, ask=Ask(), plan_progress=no_step_left)
    assert len(llm.calls) == 1 and result["final_answer"] == "All done."
    llm = ToolLLM(answers("Just an answer."))
    result, _ = go(llm, ask=Ask(), plan_progress=None)
    assert len(llm.calls) == 1 and result["final_answer"] == "Just an answer."


# ---- the repeated call ----------------------------------------------------------------------------------------------------------------------

def test_a_third_identical_call_gets_advice_in_its_result_before_the_loop_gives_up():
    ask = Ask(reply="Search for the class name instead of the file name.")
    same = {"query": "NativeTools"}
    llm = ToolLLM(asks(call("search_code", same)), asks(call("search_code", same)), asks(call("search_code", same)), answers("ok"))
    result, advisor = go(llm, ask=ask, plan_progress=None)
    results = [m.content for m in llm.calls[-1][0] if isinstance(m, ToolMessage)]
    assert "already made this exact call" in results[1] and "ADVICE from a stronger model" not in results[1]
    assert "ADVICE from a stronger model" in results[2] and "Search for the class name" in results[2]
    assert advisor.counts == {"advisor_repeat": 1} and len(ask.prompts) == 1 and "search_code" in ask.prompts[0]


# ---- the signals that need no plan -------------------------------------------------------------------------------------------------------------

EDIT_TOOLS = TOOLS + [{"name": "propose_local_edits", "description": "make a change"}, {"name": "run_local_check", "description": "run a check"}]
REQUIRED = {
    "tool_action": "propose_local_edits", "nudge_after_step": 3, "max_final_rejections": 2, "nudge": "NUDGE: propose it now.",
    "final_notice": "FINAL NOTICE: you can make it, call propose_local_edits.", "steps_notice": "Your next tool step must be propose_local_edits.",
}
PROPOSED = "Proposed 1 edit(s) across 1 file(s): a.py. APPLIED: the person applied the edits."


def test_a_change_that_is_still_not_proposed_after_the_two_send_backs_asks_the_advisor_once_no_plan_needed():
    ask = Ask()
    llm = ToolLLM(answers("I would change the greeting."), answers("Here is what to change."), answers("It needs a new line."), asks(call("propose_local_edits", {"summary": "s", "edits": []})), answers("Proposed it."))
    result, advisor = go(llm, ask=ask, plan_progress=None, tools=EDIT_TOOLS, acts=Acts({"propose_local_edits": PROPOSED}), required_action=REQUIRED, max_steps=8)
    assert result["final_answer"] == "Proposed it." and advisor.counts == {"advisor_no_proposal": 1}
    assert any(NEXT_CALL in text for text in sent(llm)) and "keeps answering in words" in ask.prompts[0]


def test_a_second_reading_streak_notice_asks_the_advisor(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    ask = Ask()
    edit = asks(call("propose_local_edits", {"summary": "s", "edits": []}))
    reads = [asks(call("read_repo_file", {"path": f"f{n}.py"})) for n in range(7)]
    llm = ToolLLM(edit, *reads, answers("done"))
    _, advisor = go(llm, ask=ask, plan_progress=None, tools=EDIT_TOOLS, acts=Acts({"propose_local_edits": PROPOSED, "read_repo_file": "text"}), max_steps=14)
    assert advisor.counts == {"advisor_reading_streak": 1} and "told twice to stop reading" in ask.prompts[0]
    assert any(NEXT_CALL in text for text in sent(llm))


def test_a_check_that_fails_a_second_time_asks_the_advisor(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    ask = Ask()
    check = lambda n: asks(call("run_local_check", {"check": f"tests{n}"}))
    llm = ToolLLM(check(1), check(2), answers("It fails."))
    outputs = iter(["LOCAL CHECK tests FAILED (exit 1)" + chr(10) + "assertion one", "LOCAL CHECK tests FAILED (exit 1)" + chr(10) + "assertion two, a different failure"])

    async def acts(decision):
        return next(outputs)

    _, advisor = go(llm, ask=ask, plan_progress=None, tools=EDIT_TOOLS, acts=acts, max_steps=8)
    assert advisor.counts == {"advisor_failing_check": 1} and "failed a second time" in ask.prompts[0]


def test_the_signals_without_a_plan_do_nothing_without_an_advisor():
    llm = ToolLLM(answers("a"), answers("b"), answers("c"))
    result, _ = go(llm, plan_progress=None, tools=EDIT_TOOLS, acts=Acts({}), required_action=REQUIRED, max_steps=8)
    assert result["final_answer"] == "c" and len(llm.calls) == 3                    # the two send-backs, then the answer stands


# ---- the allowance ------------------------------------------------------------------------------------------------------------------------------

def test_each_kind_once_and_no_more_than_the_allowance_a_turn(monkeypatch):
    advisor = ad.Advisor(Ask(), max_consults=2)
    assert advisor.available("early_answer") and advisor.available("repeat") and not advisor.available("something_else")
    assert run(advisor.consult("early_answer", "b")) and not advisor.available("early_answer") and run(advisor.consult("early_answer", "b")) is None
    one = ad.Advisor(Ask(), max_consults=1)
    assert run(one.consult("repeat", "b")) and run(one.consult("early_answer", "b")) is None          # the turn's allowance is used up
    monkeypatch.setattr(ad, "ENABLED", False)
    assert not ad.Advisor(Ask()).available("repeat")


def test_the_advice_is_cut_and_an_empty_one_counts_as_failed():
    long = ad.Advisor(Ask(reply="y" * 5000))
    assert len(run(long.consult("repeat", "b"))) == ad.ADVICE_MAX_CHARS
    empty = ad.Advisor(Ask(reply="   "))
    assert run(empty.consult("repeat", "b")) is None and empty.counts == {"advisor_failed": 1}


def test_an_advisor_is_made_whenever_there_is_a_higher_tier_to_ask_except_in_low_power_mode(monkeypatch):
    strong = SimpleNamespace(ainvoke=Ask())
    assert isinstance(ad.for_turn(False, strong), ad.Advisor)
    assert ad.for_turn(True, strong) is None            # low-power mode
    assert ad.for_turn(False, None) is None             # a top tier has none
    monkeypatch.setattr(ad, "ENABLED", False)
    assert ad.for_turn(False, strong) is None


def test_the_adviser_is_the_next_tier_up_of_the_same_provider_and_a_top_tier_has_none(monkeypatch):
    from backend.models import claude_llm, models

    haiku, sonnet = claude_llm.ClaudeLLM(), claude_llm.ClaudeLLM(model_name=claude_llm.SONNET_MODEL)
    monkeypatch.setattr(models, "plan_review_llm", lambda: "the-sonnet-reader")
    assert models.advisor_llm_for(haiku) == "the-sonnet-reader"                 # Haiku has Sonnet
    assert models.advisor_llm_for(sonnet) is None                               # Sonnet is a top tier
    assert models.advisor_llm_for(models.lite_llm) is models.lite_llm_deep      # the default Gemini lite model has Gemini's deeper one: the same provider, nothing new disclosed
    assert models.advisor_llm_for(models.lite_llm_deep) is None
    assert models.advisor_llm_for(object()) is None                             # GPT or anything else: none configured
    monkeypatch.setattr(models, "plan_review_llm", lambda: None)                # no Claude key or price for Sonnet
    assert models.advisor_llm_for(haiku) is None


# ---- counted, registered and wired -----------------------------------------------------------------------------------------------------------

def test_the_loop_records_what_it_did_by_kind(monkeypatch):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    go(ToolLLM(answers("one"), answers("two"), answers("three")), ask=Ask())
    counts = recorded[-1]
    assert counts.get("keep_going_sent_back") == 1 and counts.get("advisor_early_answer") == 1


def test_the_counters_are_registered_and_the_turn_wires_the_advisor_the_loop_and_the_plan_step():
    assert set(sc.ADVISOR_KEYS) == {"keep_going_sent_back", "advisor_early_answer", "advisor_repeat", "advisor_no_proposal", "advisor_reading_streak", "advisor_failing_check", "advisor_failed"} and set(sc.ADVISOR_KEYS) <= sc.KEYS
    source = open(aw.__file__, encoding="utf-8").read()
    assert 'advisor_module.for_turn(budget_guard.low_power(budget_state), advisor_llm_for(kwargs["llm"]))' in source
    assert "plan_progress=_plan_next_step if plan_phase == code_plan.EXECUTE_PHASE else None" in source


def test_a_brief_with_every_part_at_its_largest_is_still_cut_to_the_limit():
    attempts = [{"action_desc": "read_repo_file(path=" + "p" * 300 + ")", "observation": "o" * 5000} for _ in range(5)]
    brief = ad.build_brief("early_answer", "r" * 5000, "s" * 2000, attempts, model_answer="a" * 3000, repeated="c" * 2000)
    assert len(brief) <= ad.BRIEF_MAX_CHARS
    assert brief.startswith("WHY YOU ARE ASKED") and "THE PERSON'S REQUEST" in brief
