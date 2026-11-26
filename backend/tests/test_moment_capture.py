"""New shared moments noticed as they happen (backend/utils/moment_capture.py): a signal earns one small-model look, code refuses anything ungrounded or sensitive, and what
survives is only PROPOSED. A fake model and a fake database; the signal messages are Jack's real shapes (2026-11-25 measurement)."""
import asyncio
import pathlib
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.tests.fake_runner_db import FakeDB
from backend.utils import moment_capture as mc, self_counters, shared_moments as sm

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
CALM = {"read_this_turn": True, "valence": "positive"}

EXCHANGE = [
    HumanMessage(content="can you finish the budget banner fix"),
    AIMessage(content="Applied: the admin button now shows at the 80% notice line, and the tests pass."),
    HumanMessage(content="We did it! the budget banner finally works"),
]


@pytest.fixture(autouse=True)
def _fresh():
    mc.forget_calls()
    yield
    mc.forget_calls()


@pytest.mark.parametrize("message, signal", [
    ("We did it!", "milestone"),
    ("yep it looks like it worked, thanks saapp", "milestone"),
    ("50 cents for two asks you couldnt even complete", "verdict"),
    ("seriously you forgot all of it?", "verdict"),
    ("do you remember when we built the trace panel", "shared_past"),
    ("can you rename this function", None),
])
def test_signals_come_from_real_shapes(message, signal):
    assert mc.signal_of(message) == signal


@pytest.mark.parametrize("kwargs, ok", [
    ({"guest": False, "risk_active": False, "mood": CALM}, True),
    ({"guest": False, "risk_active": False, "mood": None}, True),
    ({"guest": True, "risk_active": False, "mood": CALM}, False),
    ({"guest": False, "risk_active": True, "mood": CALM}, False),
    ({"guest": False, "risk_active": False, "mood": {"read_this_turn": True, "valence": "distressed"}}, False),
    ({"guest": False, "risk_active": False, "mood": {"read_this_turn": True, "valence": "low"}}, False),
])
def test_a_signal_earns_a_look_only_for_a_member_outside_a_safety_context_and_a_hard_mood(kwargs, ok):
    assert mc.worth_a_look("We did it!", **kwargs) is ok
    assert mc.worth_a_look("ok thanks", guest=False, risk_active=False, mood=CALM) is False


def test_the_exchange_is_the_last_messages_as_person_and_sonic():
    text = mc.exchange_text(EXCHANGE)
    assert text.splitlines()[0].startswith("Person: can you finish") and text.splitlines()[1].startswith("Sonic: Applied:")
    assert len(mc.exchange_text([HumanMessage(content=str(i)) for i in range(20)]).splitlines()) == mc.MAX_MESSAGES


@pytest.mark.parametrize("reply, parsed", [
    ('{"moment": "the day we finally got the budget banner working", "cues": ["budget banner"]}', {"moment": "the day we finally got the budget banner working", "cues": ["budget banner"]}),
    ('Sure! {"moment": null}', None), ("no json here", None), ('{"moment": "   "}', None), ('{"moment": "x", "cues": "nope"}', {"moment": "x", "cues": []}),
])
def test_the_reply_is_read_as_a_moment_or_nothing(reply, parsed):
    assert mc.parse(reply) == parsed


def test_a_grounded_moment_keeps_only_cues_that_appear_and_is_dated_today():
    moment = mc.validate({"moment": "the day we finally got the budget banner working", "cues": ["budget banner", "rocket launch"]}, EXCHANGE, TODAY)
    assert moment == {"text": "the day we finally got the budget banner working", "when": TODAY, "cues": ["budget banner"]}


@pytest.mark.parametrize("found", [
    {"moment": "the day we got the budget banner working after 7 tries", "cues": ["budget banner"]},       # a number nobody said
    {"moment": "the day Priya and you got the budget banner working", "cues": ["budget banner"]},         # a name nobody said
    {"moment": "the day we finally got the budget banner working", "cues": ["rocket launch"]},           # no cue in the messages
    {"moment": "the night we talked about your therapy and the budget banner", "cues": ["budget banner"]},  # sensitive
])
def test_anything_ungrounded_cueless_or_sensitive_is_refused(found):
    assert mc.validate(found, EXCHANGE, TODAY) is None


def test_a_substance_joke_is_never_kept():
    messages = [HumanMessage(content="do you remember when we got you high lol")]
    assert mc.validate({"moment": "the time you joked about getting me high", "cues": ["remember"]}, messages, TODAY) is None


def _capture(db, model_reply, messages=EXCHANGE, **overrides):
    calls, counted = [], []

    async def invoke(prompt):
        calls.append(prompt)
        return model_reply

    kwargs = dict(guest=False, risk_active=False, mood=CALM, invoke=invoke, get_db_fn=lambda: db, record=counted.append, now=NOW)
    kwargs.update(overrides)
    result = asyncio.run(mc.capture("jack", messages, **kwargs))
    return result, calls, counted


def test_a_real_moment_is_proposed_never_approved_and_counted():
    db = FakeDB()
    ok, calls, counted = _capture(db, '{"moment": "the day we finally got the budget banner working", "cues": ["budget banner"]}')
    assert ok and len(calls) == 1 and "Person: We did it! the budget banner finally works" in calls[0]
    [stored] = sm.list_for(db, "jack")
    assert stored["status"] == sm.PROPOSED and stored["source"] == "captured" and stored["when"] == TODAY
    assert counted == [{"moment_capture_asked": 1, "moment_capture_proposed": 1}]


def test_nothing_found_or_a_refused_moment_stores_nothing_and_says_which():
    db = FakeDB()
    assert _capture(db, '{"moment": null}')[2] == [{"moment_capture_asked": 1}]
    mc.forget_calls()
    assert _capture(db, '{"moment": "the day we got it working after 7 tries", "cues": ["budget banner"]}')[2] == [{"moment_capture_asked": 1, "moment_capture_refused": 1}]
    assert sm.list_for(db, "jack") == []


def test_one_look_every_few_hours_and_none_while_enough_moments_wait():
    db = FakeDB()
    first = _capture(db, '{"moment": null}')
    second = _capture(db, '{"moment": null}')
    assert len(first[1]) == 1 and second[1] == []                                     # the second signal inside CALL_GAP_HOURS: no call
    mc.forget_calls()
    for i in range(mc.MAX_WAITING):
        sm.propose(db, "jack", {"text": f"waiting moment number {i}", "when": TODAY, "cues": ["cue"]}, now=NOW)
    assert _capture(db, '{"moment": "the day we finally got the budget banner working", "cues": ["budget banner"]}')[1] == []


def test_no_call_without_a_signal_for_a_guest_or_without_a_database():
    plain = [HumanMessage(content="can you rename this function")]
    assert _capture(FakeDB(), "{}", plain)[1] == []
    assert _capture(FakeDB(), "{}", guest=True)[1] == []
    assert _capture(None, "{}")[0] is False


def test_a_failing_model_never_raises():
    async def boom(prompt):
        raise RuntimeError("down")

    assert asyncio.run(mc.capture("jack", EXCHANGE, guest=False, risk_active=False, mood=CALM, invoke=boom, get_db_fn=FakeDB, record=lambda c: None)) is False


def test_wired_after_the_turn_behind_the_cheap_gates_and_the_budget():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    gate = source.index("if moment_capture.worth_a_look(question, guest=is_guest_username(username), risk_active=risk_level != RISK_NONE, mood=final_state.get(\"emotional_state\"))")
    assert 'budget_guard.allows("shared moments", budget_state)' in source[gate: gate + 300]
    assert "spawn_background_task(moment_capture.capture(" in source[gate: gate + 500]
    for key in ("moment_capture_asked", "moment_capture_proposed", "moment_capture_refused"):
        assert key in self_counters.KEYS


def test_nothing_of_a_moment_is_logged():
    source = (ROOT / "backend" / "utils" / "moment_capture.py").read_text(encoding="utf-8")
    for line in [l for l in source.splitlines() if "logger." in l]:
        assert "%s" not in line and "{" not in line, line


def _history(common_word_share):
    """A conversation of 200 of the person's messages where 'worked' is in the given share of them, 'kms' in one."""
    messages = []
    for i in range(200):
        text = "it worked again" if i < int(200 * common_word_share) else "something else entirely"
        messages.append(HumanMessage(content=text if i != 7 else "the kms error"))
    return messages


def test_a_cue_must_have_a_word_that_is_rare_in_what_the_person_writes():
    history = _history(0.3)
    assert mc.specific_cues(["it worked", "kms error", "worked again"], history) == ["kms error"]           # "worked" and "again" are both in 30% of their messages
    assert mc.specific_cues(["it worked"], history[:50]) == ["it worked"]            # too few messages to judge: every cue stays


def test_a_moment_whose_cues_are_all_common_is_not_kept():
    history = _history(0.3) + [AIMessage(content="It worked: the kms error is gone."), HumanMessage(content="We did it! it worked")]
    assert mc.validate({"moment": "the day we got rid of the kms error", "cues": ["it worked"]}, history, TODAY) is None
    kept = mc.validate({"moment": "the day we got rid of the kms error", "cues": ["it worked", "kms error"]}, history, TODAY)
    assert kept is not None and kept["cues"] == ["kms error"]


def test_the_prompt_keeps_the_persons_framing_and_says_null_when_the_last_message_is_about_something_else():
    from backend.components.constraints import MOMENT_CAPTURE_PROMPT

    assert "never a softened" in MOMENT_CAPTURE_PROMPT and "what the Person's LAST message is reacting to" in MOMENT_CAPTURE_PROMPT
    assert "the answer is null" in MOMENT_CAPTURE_PROMPT
