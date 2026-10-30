"""The support ladder was too eager (2026-10-30, read from the person's own conversation):
- "I'm not gonna kill myself just because I got cancelled on" was raised to acute (negation),
- the reasoner re-raised the lowest level on message after message, each restarting the watch,
- "I'm fine", "nope not today" and "please stop asking" did not end the ladder, and
- a "thanks" was answered with the next question about their people.
"""
import datetime
from datetime import timezone

import pytest

from backend.components.constraints import REASONER_PROMPT
from backend.utils import safety_utils as su

T0 = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + datetime.timedelta(minutes=minutes)


@pytest.mark.parametrize("message", [
    "im ont gonna kill myself just because i got canceled on",
    "I'm not going to kill myself over this",
    "i would never kill myself",
    "no way I'd actually end my life over a text",
    "i dont want to die, i just want her to answer",
    "I'm not suicidal, I'm just sad",
])
def test_a_denied_statement_is_not_risk_language(message):
    assert su.detect_risk_language(message) == su.RISK_NONE


@pytest.mark.parametrize("message, expected", [
    ("I can't stop, I will kill myself", su.RISK_ACUTE),
    ("I don't know, I just want to die", su.RISK_ELEVATED),
    ("I don't want to be here anymore", su.RISK_ELEVATED),
    ("I'm not going to lie, I want to kill myself", su.RISK_ACUTE),
    ("no. I want to kill myself", su.RISK_ACUTE),
    ("I'm not fine, I want to end my life tonight", su.RISK_IMMINENT),
])
def test_real_statements_are_still_found(message, expected):
    assert su.detect_risk_language(message) == expected


@pytest.mark.parametrize("text", [
    "ugh saapp no im not gonna hurt mysefl please stop asking me.",
    "im fine saapp im just mad",
    "i appreciate it but im fine. ill be fine saapp.",
    "saapp youre way overblowing it, im fine. you dont need to go through the ladder with me",
    "ill be okay tonight saapp i promise",
])
def test_saying_they_are_fine_or_to_stop_is_a_denial(text):
    assert su.detect_denial(text)


@pytest.mark.parametrize("text", ["nope not today", "no one really", "my sister could come over", "i feel numb", ""])
def test_answers_to_the_ladder_are_not_a_denial(text):
    assert not su.detect_denial(text)


def test_a_model_reading_at_the_level_in_force_does_not_restart_the_watch():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    assert state["turns_since"] == 0
    for step in range(1, 15):
        state = su.merge_safety_state(state, su.RISK_ELEVATED, at(step * 10), from_model=True)
    assert state["updated_at"] == at(0).isoformat() and state["turns_since"] == 14      # fourteen readings later it is still the first one's watch
    assert su.effective_risk(state, at(250)) == su.RISK_NONE      # so it relaxes on schedule instead of being renewed for ever


def test_explicit_language_or_a_higher_level_still_restarts_it():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    again = su.merge_safety_state(state, su.RISK_ELEVATED, at(30), from_model=False)           # their own words
    assert again["updated_at"] == at(30).isoformat() and again["turns_since"] == 0
    higher = su.merge_safety_state(state, su.RISK_ACUTE, at(30), from_model=True)
    assert higher["level"] == su.RISK_ACUTE and higher["escalated"] is True


def test_a_denial_at_the_lowest_level_marks_the_ladder_declined_and_a_new_raise_clears_it():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(5), from_model=True, declined=True)
    assert state["declined"] is True and su.effective_risk(state, at(6)) == su.RISK_ELEVATED
    raised = su.merge_safety_state(state, su.RISK_ELEVATED, at(10), from_model=False)           # new explicit language
    assert "declined" not in raised


def test_a_denial_does_not_apply_at_acute():
    state = su.merge_safety_state(None, su.RISK_ACUTE, at(0))
    state = su.merge_safety_state(state, su.RISK_NONE, at(5), declined=True)
    assert "declined" not in state


def _plan(state, message, level=su.RISK_ELEVATED, fresh=False):
    return su.plan_safety_turn(level, fresh, state, "", message)


def test_after_a_denial_the_reply_is_told_not_to_ask_about_their_people():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(5), from_model=True, declined=True)
    plan = _plan(state, "i just, im so afraid i wont see her again")
    assert "THEIR PEOPLE" not in plan.context and "do not ask about their family" in plan.context
    assert not plan.line_required and plan.reply_issue("I hear you. That fear makes sense.") is None


def test_a_denial_still_gives_the_number_when_they_ask_for_it():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(5), from_model=True, declined=True)
    plan = _plan(state, "can you give me the number for 988")
    assert plan.line_required


def test_without_a_denial_the_ladder_still_asks_the_next_group():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), support_updates={"immediate_family": su.UNAVAILABLE}, from_model=True)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(5), from_model=True)
    assert "THEIR PEOPLE" in _plan(state, "nope not today").context


def test_a_thank_you_is_not_answered_with_the_next_ladder_question():
    state = su.merge_safety_state(None, su.RISK_ELEVATED, at(0), from_model=True)
    state = su.merge_safety_state(state, su.RISK_ELEVATED, at(5), from_model=True)
    assert su.is_acknowledgement("thanks saapp..") and su.is_acknowledgement("ok")
    assert not su.is_acknowledgement("thanks, but I keep thinking about it all night")
    ack = _plan(state, "thanks saapp..")
    assert "THEIR PEOPLE" not in ack.context and "NEXT STEP" not in ack.context
    assert "THEIR PEOPLE" in _plan(state, "i don't know who to call").context


def test_acute_is_never_softened_by_a_denial_or_a_thank_you():
    state = su.merge_safety_state(None, su.RISK_ACUTE, at(0))
    assert "THEIR PEOPLE" in _plan(state, "thanks", level=su.RISK_ACUTE).context
    assert "THEIR PEOPLE" in _plan(state, "im fine", level=su.RISK_ACUTE).context


def test_the_reasoner_is_told_that_heavy_feelings_alone_are_not_risk_and_to_rate_only_this_message():
    flat = " ".join(REASONER_PROMPT.split())
    assert "Heavy feelings alone are \"none\"" in flat
    assert "Rate THIS message only" in flat
    assert "I promise I'm okay" in flat
