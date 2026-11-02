import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.utils import emotion_checkin as ec
from backend.utils import self_counters
from backend.utils.emotion_utils import build_emotional_context, merge_emotional_state
from backend.utils.identity_checks import identity_reply_issue

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
FEELING = {"valence": "distressed", "intensity": 0.9, "need": "venting", "gist": "a rough day at work"}
NEUTRAL = {"valence": "neutral", "intensity": 0.0}
CALM = dict(risk_active=False, closing=False, guest=False)


def carried(turns, *, minutes_ago=20, reading=FEELING, then=None):
    """The state the real merge produces: a feeling shown `minutes_ago`, then `turns` neutral messages (or `then` readings) on other topics."""
    state = merge_emotional_state(None, reading, NOW - timedelta(minutes=minutes_ago))
    for index in range(turns):
        state = merge_emotional_state(state, (then or {}).get(index, NEUTRAL), NOW)
    return state


def human():
    return SimpleNamespace(type="human", additional_kwargs={})


def ai(checked_in=False):
    return SimpleNamespace(type="ai", additional_kwargs={ec.CHECKIN_MARK: True} if checked_in else {})


def talk(turns):
    return [m for _ in range(turns) for m in (human(), ai())]


def due(state, messages=(), **overrides):
    return ec.due(state, NOW, list(messages), **{**CALM, **overrides})


# ---- when it is due ----------------------------------------------------------------------------------------------

def test_a_carried_feeling_with_no_lift_is_checked_on_after_enough_messages():
    assert due(carried(ec.CHECKIN_AFTER_TURNS), talk(ec.CHECKIN_AFTER_TURNS)) is True
    assert due(carried(ec.CHECKIN_AFTER_TURNS - 1), talk(ec.CHECKIN_AFTER_TURNS - 1)) is False  # too soon: the early one-sentence touch has its own rule


def test_a_low_feeling_counts_too_but_frustration_does_not():
    low = {"valence": "low", "intensity": 0.9, "need": "venting", "gist": "a rough week"}
    assert due(carried(ec.CHECKIN_AFTER_TURNS, reading=low)) is True
    assert due(carried(ec.CHECKIN_AFTER_TURNS, reading={"valence": "frustrated", "intensity": 0.9})) is False
    assert due(carried(ec.CHECKIN_AFTER_TURNS, reading={"valence": "excited", "intensity": 0.9})) is False


def test_a_feeling_that_has_decayed_away_is_not_asked_about():
    assert due(carried(ec.CHECKIN_AFTER_TURNS, minutes_ago=60 * 12)) is False
    assert due(carried(ec.CHECKIN_AFTER_TURNS, reading={"valence": "low", "intensity": 0.2})) is False


def test_a_lift_or_a_recovery_ends_it():
    small_lift = carried(ec.CHECKIN_AFTER_TURNS, then={2: {"valence": "positive", "intensity": 0.15}})
    assert small_lift["positive_peak"] >= 0.1 and due(small_lift) is False  # a flicker of humour: the ceiling is "easing", they are not asked
    recovered = carried(ec.CHECKIN_AFTER_TURNS, then={2: {"valence": "positive", "intensity": 0.6}})
    assert due(recovered) is False


@pytest.mark.parametrize("override", [{"risk_active": True}, {"closing": True}, {"guest": True}])
def test_it_is_quiet_inside_a_safety_context_on_a_closing_message_and_for_a_guest(override):
    state = carried(ec.CHECKIN_AFTER_TURNS)
    assert due(state) is True
    assert due(state, **override) is False


def test_a_message_that_is_itself_about_the_feeling_or_the_lift_is_left_to_the_ordinary_blocks():
    state = carried(ec.CHECKIN_AFTER_TURNS)
    assert due(dict(state, read_this_turn=True)) is False
    assert due(dict(state, lift_this_turn=True)) is False
    assert due(None) is False and due({}) is False


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EMOTION_CHECKIN_ENABLED", "false")
    assert due(carried(ec.CHECKIN_AFTER_TURNS)) is False
    monkeypatch.delenv("EMOTION_CHECKIN_ENABLED")
    assert due(carried(ec.CHECKIN_AFTER_TURNS)) is True


# ---- spacing and the cap, read from the marks on Sonic's own replies -----------------------------------------------

def test_the_marks_are_counted_from_the_conversation_with_no_other_state():
    assert ec.turns_since_checkin(talk(5)) is None
    assert ec.turns_since_checkin([human(), ai(True), human(), ai(), human(), ai()]) == 2
    assert ec.checkins_within([human(), ai(True), human(), ai(), human(), ai(True)], 2) == 1  # only the last two messages of the person's count
    assert ec.checkins_within([human(), ai(True), human(), ai(), human(), ai(True)], 5) == 2


def test_a_second_check_in_waits_a_full_spacing_after_the_first():
    state = carried(ec.CHECKIN_AFTER_TURNS + 4)
    just_after = [human(), ai(True)] + talk(ec.CHECKIN_AFTER_TURNS - 1)
    assert due(state, just_after) is False
    later = [human(), ai(True)] + talk(ec.CHECKIN_AFTER_TURNS)
    assert due(state, later) is True


def test_no_more_than_the_cap_in_one_stretch():
    state = carried(ec.CHECKIN_AFTER_TURNS * 2)
    one = [human(), ai(True)] + talk(ec.CHECKIN_AFTER_TURNS)
    assert due(state, one) is True
    two = [human(), ai(True)] + talk(ec.CHECKIN_AFTER_TURNS) + [human(), ai(True)] + talk(ec.CHECKIN_AFTER_TURNS)
    assert ec.checkins_within(two, ec.CHECKIN_SPAN_FACTOR * ec.CHECKIN_AFTER_TURNS) >= ec.CHECKIN_MAX
    assert due(state, two) is False


# ---- what counts as having checked in -----------------------------------------------------------------------------

@pytest.mark.parametrize("reply,expected", [
    ("The retry loop caps at five attempts. How are you holding up after the day at work?", True),
    ("Here is the fix.\n\nHow has the rest of your day been, after that rough start? No need to get into it.", True),
    ("The retry loop caps at five attempts. Want me to run the tests?", False),     # a question about the work
    ("Should I add a test for it? Do you want me to run it now?", False),
    ("The retry loop caps at five attempts. Hope that helps.", False),               # no question
    ("Is the retry loop capped at five?", False),                                      # a question, but not to them
    # endings of real replies from the app's model, sampled 2026-11-02 for a due check-in (the skip line was 13 to 22 words, so a 12-word limit missed most of them)
    ("That is the API side of things, how are you holding up after the rough morning with your boss? No need to go into it if you just want to focus on code.", True),
    ("Use tenacity if you want a prebuilt one. How are you holding up after the rough morning with your boss? No need to go into it if you'd rather just keep working through Python questions, just wanted to check in.", True),
    ("That covers the logic. How are you doing right now after that rough morning with your boss? No need to answer if you'd rather just stick to the code.", True),
    ("That is the core structure. Can you share your config? I can look at it.", False),                   # a question with "you" that is about the work
    ("That is the core structure. Does that make sense to you?", False),
    ("Here is the fix. How are you holding up after earlier? Only if you feel like saying, and a very long closing line that goes on and on and on and on past the limit for a skip line of this kind.", False),
    ("Here is the fix. How are you holding up after earlier? Is the loop capped at five?", False),      # a work question after it: not a check-in that ends the reply
    ("Done. How are you doing after earlier? <<<FOLLOW_UP: Run the tests>>>", True),  # the follow-up tag is not part of the reply
    ("Done. <<<FOLLOW_UP: How are you holding up?>>>", False),
    ("", False),
])
def test_a_check_in_counts_only_when_the_reply_ends_on_a_question_to_the_person(reply, expected):
    assert ec.asked(reply) is expected


def test_the_block_asks_for_one_question_at_the_end_and_overrides_the_carried_register():
    block = ec.build_block()
    assert "overrides the line above about not bringing the painful topic up" in block
    assert "ONE short, warm question" in block and "very end of the reply" in block
    assert "no hotline" in block.lower() and "I'm here for you" in block   # named as things NOT to do
    carried_register = build_emotional_context(carried(ec.CHECKIN_AFTER_TURNS), NOW)
    assert "Don't bring the painful topic up" in carried_register           # the line the block says it overrides really is in the same prompt


@pytest.mark.parametrize("line", [
    "How are you holding up after the thing with your boss earlier? No need to get into it if not.",
    "How has the rest of your day been, after that rough start?",
])
def test_the_kind_of_sentence_the_block_asks_for_passes_the_identity_check(line):
    assert identity_reply_issue(line) is None


# ---- the wiring ---------------------------------------------------------------------------------------------------

def test_the_app_adds_the_block_marks_the_reply_and_counts_each_step():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "checkin_offered = emotion_checkin.due(",
        "risk_active=risk_level != RISK_NONE, closing=is_closing_message(question), guest=is_guest_username(username)",
        "emotional_context += emotion_checkin.build_block()",
        'tally(turn_counts, "emotion_checkin_offered")',
        "if checkin_offered and emotion_checkin.asked(full_response):",
        "ai_message.additional_kwargs[emotion_checkin.CHECKIN_MARK] = True",
        'tally(turn_counts, "emotion_checkin_asked")',
        "answers_checkin = bool(previous_marks.get(emotion_checkin.CHECKIN_MARK))",
        '"emotion_checkin_still_low" if energy_tier(',
    ):
        assert needle in source, needle
    assert source.index("emotional_context = safety_plan.context + build_emotional_context(") < source.index("checkin_offered = emotion_checkin.due(")


def test_the_counters_know_the_check_in_keys():
    assert {"emotion_checkin_offered", "emotion_checkin_asked", "emotion_checkin_eased", "emotion_checkin_still_low"} <= self_counters.KEYS
    counts = {}
    self_counters.tally(counts, "emotion_checkin_asked")
    assert counts == {"emotion_checkin_asked": 1}
