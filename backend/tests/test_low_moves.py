"""Fighting mirroring (2026-11-23): the low-mood moves (backend/utils/low_moves.py), "I hear you" rationed, the reflect-back instruction gone, stacked stock validation rewritten once, and Sonic's
own material back on in a moderate low. Fact shapes are memory_utils.UserFact's; mood states are emotion_utils' own. No database, no model."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.utils import emotion_checks, emotion_utils, low_moves, revision_reasons, self_counters
from backend.utils.memory_utils import UserFact

NOW = datetime.now(timezone.utc)
APP = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(encoding="utf-8")


def _fact(text, category="career", source="explicit", goal_status=None, days_ago=1, active=True):
    when = (NOW - timedelta(days=days_ago)).isoformat()
    # updated_at is today for every fact (a cleanup touched them all): the order must come from when they were told
    return UserFact(id=text[:12], username="u", category=category, fact=text, source=source, created_at=when, updated_at=NOW.isoformat(), goal_status=goal_status, active=active)


def _mood(valence="low", need="venting", intensity=0.8, lift=False, read=True):
    return {"valence": valence, "need": need, "intensity": intensity, "updated_at": NOW.isoformat(), "turns_since": 0, "read_this_turn": read, "lift_this_turn": lift}


def _ai(kind=None, text="ok"):
    return SimpleNamespace(type="ai", content=text, additional_kwargs={low_moves.MOVE_MARK: kind} if kind else {})


EVIDENCE = [
    _fact("Jack completed a four-round onsite interview at Casey's AI Center of Excellence on Monday, November 16, 2026.", days_ago=7),
    _fact("Jack migrated six tools to the native loop and removed the legacy ReAct setup.", category="project", goal_status="achieved", days_ago=10),
    _fact("Jack has preferred dark mode since 2019.", category="preference"),                              # dated, but not an evidence category
    _fact("Jack has been sober from alcohol since October 3.", category="goal"),                           # sensitive
    _fact("Jack works somewhere.", category="career"),                                                      # nothing specific, not finished
]
PATTERNS = [
    _fact("Jack maintains a recurring cycle of self-criticism and anxiety regarding his relationships and work.", category="pattern", source="pattern"),
    _fact("Jack uses running and weight training as a grounding mechanism when his work week gets heavy.", category="pattern", source="pattern"),
]


def _choose(message, mood=None, facts=EVIDENCE + PATTERNS, interests=("learning guitar", "Elden Ring"), recent=(), risk=False, closing=False):
    return low_moves.choose(message, mood if mood is not None else _mood(), facts=facts, interests=list(interests), recent_messages=list(recent), risk_active=risk, closing=closing)


@pytest.mark.parametrize("message", ["I'm such a failure", "i cant do anything right lately", "what's wrong with me", "I ruin everything", "nobody wants me around", "I’m not good enough"])
def test_a_harsh_word_about_themselves_gets_a_push_back_with_specific_true_things(message):
    move = _choose(message)
    assert move["kind"] == "push_back"
    assert move["items"] == [EVIDENCE[0].fact, EVIDENCE[1].fact]                     # dated or finished, newest first; no trait, nothing sensitive, nothing vague


def test_without_anything_specific_to_point_to_there_is_no_push_back():
    assert _choose("I'm a failure", facts=EVIDENCE[2:], interests=()) is None


def test_a_pattern_that_shares_words_with_the_message_is_offered_and_a_clinical_one_never_is():
    move = _choose("work week was heavy and i skipped training again")
    assert move == {"kind": "pattern", "items": [PATTERNS[1].fact]}
    assert _choose("my relationships and work keep falling apart, self-criticism everywhere", interests=()) is None


def test_a_pattern_needs_two_shared_words():
    assert low_moves.matching_pattern("training today", PATTERNS) is None
    assert low_moves.matching_pattern("training all week", PATTERNS) == PATTERNS[1].fact


def test_company_offers_what_they_like_when_they_want_to_be_heard_never_a_sensitive_interest():
    move = _choose("today just sucked", mood=_mood(need="distraction"), interests=["drinking with friends", "learning guitar", "Elden Ring"])
    assert move == {"kind": "company", "items": ["learning guitar", "Elden Ring"]}
    assert _choose("today just sucked", mood=_mood(need="solving")) is None              # they want help with something concrete
    assert _choose("today just sucked") is None                                          # 2026-11-27: someone venting is no longer offered "want to...?" (16 of 18 moves, ignored)


def test_company_never_offers_a_plan_or_an_appointment_only_something_they_do():
    real_shapes = ["The user engages in strength training exercises including shoulder presses and bicep curls.",
                   "The user plans to build a dedicated portfolio website to showcase their projects.",
                   "The user has a planned visit with Kelsey on November 20, 2026, and a significant meeting on November 24, 2026."]
    assert _choose("today just sucked", mood=_mood(need="distraction"), interests=real_shapes) == {"kind": "company", "items": [real_shapes[0]]}


def test_the_same_kind_is_not_used_again_within_the_gap_and_comes_back_after_it():
    recent = [_ai("push_back"), _ai(), _ai(), _ai()]                                   # three replies since: still too soon (a literal, so the gap itself is pinned)
    assert _choose("I'm a failure", recent=recent, interests=()) is None
    assert _choose("I'm a failure", recent=recent + [_ai()], interests=())["kind"] == "push_back"


@pytest.mark.parametrize("kwargs", [
    {"risk": True}, {"closing": True},
    {"mood": _mood(valence="frustrated")}, {"mood": _mood(valence="neutral")}, {"mood": _mood(lift=True)}, {"mood": _mood(read=False)}, {"mood": {}},
])
def test_no_move_in_a_safety_context_a_closing_a_lift_a_frustration_or_a_mood_not_read_now(kwargs):
    assert _choose("I'm such a failure", **kwargs) is None


def test_each_block_carries_its_items_and_tells_the_reply_what_not_to_do():
    push = low_moves.build_block({"kind": "push_back", "items": ["a", "b"]})
    assert "- a\n- b" in push and "never quote their harsh words back" in push
    assert "not as a diagnosis" in low_moves.build_block({"kind": "pattern", "items": ["p"]})
    assert "not as a fix" in low_moves.build_block({"kind": "company", "items": ["guitar"]})
    assert low_moves.build_block(None) == "" and low_moves.build_block({"kind": "other"}) == ""


def test_the_low_prompt_no_longer_asks_for_their_words_back_and_rations_i_hear_you():
    fresh = _mood()
    with_line = emotion_utils.build_emotional_context(fresh, NOW)
    held = emotion_utils.build_emotional_context(fresh, NOW, hear_you_ok=False)
    assert "reflect their specific situation and words back" not in with_line
    assert "don't summarise it" in with_line and "never two of them together" in with_line
    assert emotion_utils.HEAR_YOU_RULE in with_line and emotion_utils.HEAR_YOU_HELD_RULE not in with_line
    assert emotion_utils.HEAR_YOU_RULE not in held and emotion_utils.HEAR_YOU_HELD_RULE in held


@pytest.mark.parametrize("reply, opened", [
    ("I hear you, Jack. That's a lot.", True), ("Oh, Jack. I hear you.", True), ("Jack, I hear you.", True), ("I’m here. I hear you.", False), ("That's rough.", False),
])
def test_an_i_hear_you_opening_is_recognised(reply, opened):
    assert emotion_checks.opened_with_hear_you(["Fine.", reply]) is opened


def test_stacked_stock_validation_in_the_first_paragraph_is_rewritten_once_but_one_line_or_a_spread_is_not():
    stacked = "I hear you, Jack. It makes complete sense that you're scared.\n\nHere's one thing."
    single = "I hear you. The interview went four rounds, and you finished every one."
    spread = "I hear you.\n\nThe onsite went four rounds. It makes sense to be tired after that."
    assert emotion_checks.reply_stacks_validation(stacked)
    assert not emotion_checks.reply_stacks_validation(single)
    assert not emotion_checks.reply_stacks_validation(spread)
    assert emotion_checks.reply_stacks_validation("That’s so hard. It’s completely understandable.")          # curly apostrophes too
    assert emotion_checks.emotional_reply_issue(_mood(need="reassurance"), stacked) == "stacked_validation"
    assert emotion_checks.emotional_reply_issue(_mood(need="reassurance"), single) is None
    prompt, reason = emotion_checks.build_emotional_revision_prompt("P", stacked, "stacked_validation")
    assert "at most one short acknowledgement" in reason and "REJECTED DRAFT" in prompt
    assert "stacked_validation" in revision_reasons.REASONS


def test_own_material_comes_back_in_a_moderate_low_but_not_a_strong_low_or_distress():
    assert emotion_utils.own_material_tier(_mood(intensity=0.3), NOW, work_context=False) == "open"
    assert emotion_utils.own_material_tier(_mood(intensity=0.9), NOW, work_context=False) != "open"
    assert emotion_utils.own_material_tier(_mood(valence="distressed", intensity=0.3), NOW, work_context=False) != "open"
    assert emotion_utils.energy_tier(_mood(intensity=0.3), NOW) != "open"                     # the person-facing extras still see the low


def test_the_app_passes_the_rationing_marks_the_move_and_counts_both():
    assert "hear_you_ok = not opened_with_hear_you(earlier_ai_replies(chat_sessions[history_key], HEAR_YOU_GAP))" in APP
    assert "safety=safety_plan.active, hear_you_ok=hear_you_ok" in APP
    assert re.search(r"if not is_guest_username\(username\) and not safety_plan\.active and _mood\.get\(\"read_this_turn\"\)", APP)
    assert "emotional_context += low_moves.build_block(low_move)" in APP
    assert 'ai_message.additional_kwargs[low_moves.MOVE_MARK] = low_move["kind"]' in APP
    for key in ("hear_you_held", *(f"low_move_{k}" for k in low_moves.KINDS)):
        assert key in self_counters.KEYS
