import asyncio
import functools
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from backend.components.constraints import build_voice_prompt, GROUNDING_BLOCKS
from backend.services import agent_workflow as aw
from backend.utils import emotion_utils as eu

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _state(valence="distressed", intensity=0.8, minutes_ago=0, turns_since=0):
    return {
        "valence": valence,
        "intensity": intensity,
        "updated_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "turns_since": turns_since,
    }


# ---------------------------------------------------------------------------
# effective_intensity
# ---------------------------------------------------------------------------

def test_effective_intensity_none_state_is_zero():
    assert eu.effective_intensity(None, NOW) == 0.0


def test_effective_intensity_no_decay_when_fresh():
    assert eu.effective_intensity(_state(intensity=0.8), NOW) == pytest.approx(0.8)


def test_effective_intensity_halves_at_one_half_life():
    state = _state(intensity=0.8, minutes_ago=eu.EMOTION_HALF_LIFE_MINUTES)
    assert eu.effective_intensity(state, NOW) == pytest.approx(0.4, rel=0.01)


def test_effective_intensity_decays_per_intervening_turn():
    state = _state(intensity=0.8, turns_since=3)
    assert eu.effective_intensity(state, NOW) == pytest.approx(0.8 * eu.EMOTION_TURN_DECAY ** 3)


def test_effective_intensity_malformed_state_is_zero():
    assert eu.effective_intensity({"intensity": "lots", "updated_at": "nope"}, NOW) == 0.0


# ---------------------------------------------------------------------------
# normalize_reading
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    None, "sad", [], {}, {"valence": "furious", "intensity": 0.9},
    {"valence": "low", "intensity": "very"}, {"valence": "low", "intensity": float("nan")},
    {"valence": "neutral", "intensity": 0.9}, {"valence": "low", "intensity": 0},
])
def test_normalize_reading_malformed_or_neutral_becomes_neutral_zero(raw):
    assert eu.normalize_reading(raw) == ("neutral", 0.0)


def test_normalize_reading_clamps_intensity_to_range():
    assert eu.normalize_reading({"valence": "distressed", "intensity": 7}) == ("distressed", 1.0)
    assert eu.normalize_reading({"valence": " LOW ", "intensity": 0.5}) == ("low", 0.5)


# ---------------------------------------------------------------------------
# merge_emotional_state — the core scenario is the neutral-reading case
# ---------------------------------------------------------------------------

def test_merge_first_reading_with_no_prior_is_stored():
    merged = eu.merge_emotional_state(None, {"valence": "distressed", "intensity": 0.8}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["intensity"] == 0.8
    assert merged["turns_since"] == 0


def test_merge_neutral_reading_preserves_significant_prior_across_a_topic_change():
    prior = _state("distressed", 0.8, minutes_ago=2)
    merged = eu.merge_emotional_state(prior, {"valence": "neutral", "intensity": 0.0}, NOW)

    assert merged["valence"] == "distressed"
    assert merged["intensity"] == 0.8
    assert merged["updated_at"] == prior["updated_at"]  # not refreshed — only decay moves forward
    assert merged["turns_since"] == 1


def test_merge_missing_reading_behaves_like_neutral():
    prior = _state("low", 0.6)
    merged = eu.merge_emotional_state(prior, None, NOW)
    assert merged["valence"] == "low"
    assert merged["turns_since"] == 1


def test_merge_neutral_reading_drops_a_fully_decayed_prior():
    prior = _state("low", 0.4, minutes_ago=eu.EMOTION_HALF_LIFE_MINUTES * 6)
    assert eu.merge_emotional_state(prior, {"valence": "neutral", "intensity": 0.0}, NOW) is None


def test_merge_stronger_new_negative_reading_replaces_prior():
    prior = _state("low", 0.4)
    merged = eu.merge_emotional_state(prior, {"valence": "distressed", "intensity": 0.9}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["intensity"] == 0.9
    assert merged["turns_since"] == 0


def test_merge_weaker_same_polarity_reading_does_not_replace_prior():
    prior = _state("distressed", 0.9)
    merged = eu.merge_emotional_state(prior, {"valence": "low", "intensity": 0.3}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["intensity"] == 0.9


def test_merge_clear_opposite_polarity_reading_overrides_prior():
    prior = _state("distressed", 0.9)
    merged = eu.merge_emotional_state(prior, {"valence": "excited", "intensity": 0.7}, NOW)
    assert merged["valence"] == "excited"


def test_merge_faint_opposite_polarity_reading_does_not_erase_real_distress():
    prior = _state("distressed", 0.9)
    merged = eu.merge_emotional_state(prior, {"valence": "positive", "intensity": 0.1}, NOW)
    assert merged["valence"] == "distressed"


# ---------------------------------------------------------------------------
# build_emotional_context
# ---------------------------------------------------------------------------

def test_build_emotional_context_empty_without_state():
    assert eu.build_emotional_context(None, NOW) == ""


def test_build_emotional_context_empty_once_decayed_below_threshold():
    state = _state("distressed", 0.5, minutes_ago=eu.EMOTION_HALF_LIFE_MINUTES * 4)
    assert eu.build_emotional_context(state, NOW) == ""


def test_build_emotional_context_present_for_a_significant_negative_state():
    context = eu.build_emotional_context(_state("distressed", 0.8, minutes_ago=5), NOW)
    assert "distressed" in context
    assert "gentle" in context
    # Must instruct the model to stay silent about the mechanism, never announce it.
    assert "never mention you're tracking it" in context


def _fresh(valence="distressed", intensity=0.8, need="none"):
    return {**_state(valence, intensity), "read_this_turn": True, "need": need}


def test_context_for_a_fresh_low_message_asks_for_a_fuller_listening_reply_not_a_shorter_one():
    context = eu.build_emotional_context(_fresh("low", 0.7), NOW)
    assert "fuller, unhurried reply" in context
    assert "takes priority over mirroring" in context  # beats the persona's match-their-terseness rule
    assert "no headers or bullet lists" in context
    assert "brief" not in context.lower()  # never trims length for distress
    assert "never mention you're tracking it" in context


@pytest.mark.parametrize("need,expected", [
    ("venting", "want to be heard"),
    ("solving", "real help"),
    ("reassurance", "steadiness"),
    ("distraction", "break from it"),
    ("none", "follow their lead"),
])
def test_context_for_a_fresh_low_message_adapts_to_what_the_user_needs(need, expected):
    assert expected in eu.build_emotional_context(_fresh("distressed", 0.8, need), NOW)


def test_context_unknown_need_is_treated_as_none():
    assert "follow their lead" in eu.build_emotional_context(_fresh("low", 0.7, "fix-it"), NOW)


def test_context_for_a_fresh_frustrated_message_is_direct_without_groveling():
    context = eu.build_emotional_context(_fresh("frustrated", 0.7), NOW)
    assert "without grovelling" in context
    assert "lead with the fix" in context
    assert "fuller, unhurried" not in context


def test_context_carried_onto_a_later_topic_stays_gentle_and_never_applies_a_stale_need():
    carried = {**_state("distressed", 0.8, turns_since=2), "read_this_turn": False, "need": "venting"}
    context = eu.build_emotional_context(carried, NOW)
    assert "gentle" in context
    assert "still be weighing" in context
    assert "want to be heard" not in context


def test_context_for_carried_frustration_is_low_fluff():
    carried = {**_state("frustrated", 0.8, turns_since=1), "read_this_turn": False}
    assert "low-fluff" in eu.build_emotional_context(carried, NOW)


# ---------------------------------------------------------------------------
# merge: what this message wants is recorded for THIS turn only
# ---------------------------------------------------------------------------

def test_merge_records_need_and_marks_the_reading_as_this_turn():
    merged = eu.merge_emotional_state(None, {"valence": "low", "intensity": 0.6, "need": "venting"}, NOW)
    assert merged["need"] == "venting"
    assert merged["read_this_turn"] is True


def test_merge_unrecognized_need_becomes_none():
    merged = eu.merge_emotional_state(None, {"valence": "low", "intensity": 0.6, "need": "fix-it"}, NOW)
    assert merged["need"] == "none"


def test_merge_neutral_turn_carries_the_feeling_but_clears_need_and_this_turn():
    prior = {**_state("distressed", 0.8), "read_this_turn": True, "need": "venting"}
    merged = eu.merge_emotional_state(prior, {"valence": "neutral", "intensity": 0.0}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["read_this_turn"] is False
    assert merged["need"] == "none"


def test_merge_weaker_follow_up_still_updates_this_turns_need_while_keeping_the_stronger_prior():
    """Distressed (venting), then a weaker 'ok so what should I do?': the feeling carried is still
    the stronger one, but the reply must now be about solving."""
    prior = {**_state("distressed", 0.9), "read_this_turn": True, "need": "venting"}
    merged = eu.merge_emotional_state(prior, {"valence": "low", "intensity": 0.3, "need": "solving"}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["intensity"] == 0.9
    assert merged["need"] == "solving"
    assert merged["read_this_turn"] is True


def test_frustrated_counts_as_negative_polarity_for_recovery_overrides():
    prior = _state("frustrated", 0.8)
    assert eu.merge_emotional_state(prior, {"valence": "positive", "intensity": 0.6}, NOW)["valence"] == "positive"


# ---------------------------------------------------------------------------
# build_voice_prompt wiring
# ---------------------------------------------------------------------------

def _voice_prompt(**extra):
    return build_voice_prompt(
        grounding_block=GROUNDING_BLOCKS["conversational"],
        data="", history="", question="hi", **extra,
    )


def test_voice_prompt_includes_emotional_context_when_given():
    assert "EMOTIONAL CONTEXT" in _voice_prompt(emotional_context="\n\nEMOTIONAL CONTEXT: stay gentle\n")


def test_voice_prompt_unchanged_when_emotional_context_empty():
    assert _voice_prompt(emotional_context="") == _voice_prompt()
    assert "EMOTIONAL CONTEXT" not in _voice_prompt()


# ---------------------------------------------------------------------------
# reasoner_node: reads the state off the existing classification call, strips it out of the
# boolean-only routing flags, and carries it across turns (neutral turn must not erase it).
# ---------------------------------------------------------------------------

def _flags_response(**emotional):
    flags = {"needs_conversation": True, "emotional_state": emotional}
    return SimpleNamespace(content=json.dumps(flags))


@run_async
async def test_reasoner_stores_emotional_state_and_keeps_flags_boolean_only(monkeypatch):
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=_flags_response(valence="distressed", intensity=0.8)),
    )
    state = {"messages": [HumanMessage(content="I got some really bad news today")]}

    result = await aw.reasoner_node(state)

    assert "emotional_state" not in result["reasoner_flags"]
    assert result["reasoner_flags"]["needs_conversation"] is True
    assert result["emotional_state"]["valence"] == "distressed"
    assert result["emotional_state"]["intensity"] == 0.8


@run_async
async def test_reasoner_neutral_follow_up_turn_preserves_prior_emotional_state(monkeypatch):
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=_flags_response(valence="neutral", intensity=0.0)),
    )
    prior = {
        "valence": "distressed", "intensity": 0.8,
        "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0,
    }
    state = {
        "messages": [HumanMessage(content="anyway, how does the retry logic work?")],
        "emotional_state": prior,
    }

    result = await aw.reasoner_node(state)

    assert result["emotional_state"]["valence"] == "distressed"
    assert result["emotional_state"]["turns_since"] == 1


@run_async
async def test_reasoner_flags_without_emotional_state_key_leave_prior_state_in_place(monkeypatch):
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"needs_conversation": True}))),
    )
    prior = {
        "valence": "low", "intensity": 0.6,
        "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0,
    }
    state = {"messages": [HumanMessage(content="what time is it")], "emotional_state": prior}

    result = await aw.reasoner_node(state)

    assert result["emotional_state"]["valence"] == "low"


@run_async
async def test_reasoner_classification_failure_keeps_prior_state_and_uses_safe_fallback_flags(monkeypatch):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=RuntimeError("llm down")))
    prior = {
        "valence": "low", "intensity": 0.6,
        "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0,
    }
    state = {"messages": [HumanMessage(content="hello")], "emotional_state": prior}

    result = await aw.reasoner_node(state)

    assert result["reasoner_flags"]["needs_conversation"] is True
    assert result["emotional_state"]["valence"] == "low"


# ---------------------------------------------------------------------------
# Carrying the feeling across a topic change: the gist, the one-time acknowledgement, the decay.
# Measured against the real model, the old abstract "stay gentle" block changed nothing visible in
# the reply, so these pin the concrete behaviors that did.
# ---------------------------------------------------------------------------

def _carried(turns_since, gist="a breakup; Tina ended things tonight", valence="distressed", intensity=0.8):
    return {**_state(valence, intensity, turns_since=turns_since), "read_this_turn": False, "need": "solving", "gist": gist}


def test_normalize_gist_flattens_trims_and_bounds_what_goes_into_a_prompt():
    assert eu.normalize_gist({"gist": '  "a breakup;\n Tina ended\tthings"  '}) == "a breakup; Tina ended things"
    assert len(eu.normalize_gist({"gist": "x" * 500})) <= 120


@pytest.mark.parametrize("raw", [None, "gist", {"gist": None}, {"gist": 7}, {"gist": ["a"]}, {}])
def test_normalize_gist_ignores_anything_that_is_not_a_string(raw):
    assert eu.normalize_gist(raw) == ""


def test_merge_stores_the_gist_with_a_new_reading():
    merged = eu.merge_emotional_state(None, {"valence": "distressed", "intensity": 0.8, "gist": "a breakup"}, NOW)
    assert merged["gist"] == "a breakup"


def test_merge_neutral_turn_keeps_the_gist_so_the_cause_survives_a_topic_change():
    prior = {**_state("distressed", 0.8), "gist": "a breakup"}
    merged = eu.merge_emotional_state(prior, {"valence": "neutral", "intensity": 0.0}, NOW)
    assert merged["gist"] == "a breakup"


def test_merge_stronger_same_kind_reading_without_a_gist_keeps_what_it_was_about():
    prior = {**_state("low", 0.5), "gist": "a performance plan at work"}
    merged = eu.merge_emotional_state(prior, {"valence": "distressed", "intensity": 0.9}, NOW)
    assert merged["valence"] == "distressed"
    assert merged["gist"] == "a performance plan at work"


def test_merge_a_recovery_does_not_inherit_the_old_painful_gist():
    prior = {**_state("distressed", 0.4), "gist": "a breakup"}
    merged = eu.merge_emotional_state(prior, {"valence": "positive", "intensity": 0.8}, NOW)
    assert merged["valence"] == "positive"
    assert merged["gist"] == ""


def test_first_reply_after_the_shift_gets_one_specific_acknowledgement_and_a_calm_register():
    context = eu.build_emotional_context(_carried(turns_since=1), NOW)
    assert "(a breakup; Tina ended things tonight)" in context
    assert "ONE short sentence" in context
    assert "not a recap" in context
    assert "I've got you" in context  # named as a stock line to avoid, not used
    assert "no \"let's dive in\"" in context and "no exclamation marks" in context and "no emoji" in context
    assert "plain, warm prose" in context
    assert "never mention you're tracking it" in context


def test_the_carried_block_says_it_outranks_the_personas_match_their_energy_rule():
    # Abstract wording had no effect on the real model; saying outright that this wins over the
    # persona's "be playful/expressive back" is what changed the reply.
    assert "takes priority over the guidance in your voice description" in eu.build_emotional_context(_carried(1), NOW)
    assert "takes priority over the guidance in your voice description" in eu.build_emotional_context(_carried(3), NOW)


def test_later_replies_only_keep_the_register_and_do_not_acknowledge_it_again():
    context = eu.build_emotional_context(_carried(turns_since=2), NOW)
    assert "ONE short sentence" not in context
    assert "Don't bring the painful topic up" in context
    assert "no exclamation marks" in context
    assert "(a breakup; Tina ended things tonight)" in context


def test_the_one_time_acknowledgement_window_is_configurable(monkeypatch):
    monkeypatch.setattr(eu, "EMOTION_TOUCH_MAX_TURNS", 3)
    assert "ONE short sentence" in eu.build_emotional_context(_carried(turns_since=3), NOW)
    assert "ONE short sentence" not in eu.build_emotional_context(_carried(turns_since=4), NOW)


def test_carried_block_without_a_gist_has_no_dangling_parenthesis():
    context = eu.build_emotional_context(_carried(turns_since=1, gist=""), NOW)
    assert "painful. They've" in context
    assert "()" not in context


def test_acute_distress_survives_ten_ordinary_messages():
    # Regression: with the old 45 min / 0.9-per-message defaults a 0.8 reading fell to the injection
    # threshold after about ten messages, i.e. the attunement ended while the person was still raw.
    state = _state("distressed", 0.8, minutes_ago=30, turns_since=10)
    assert eu.effective_intensity(state, NOW) > eu.EMOTION_INJECTION_THRESHOLD * 1.4
    assert eu.build_emotional_context({**state, "read_this_turn": False}, NOW) != ""


def test_it_still_fades_eventually_rather_than_lingering_all_session():
    state = _state("distressed", 0.8, minutes_ago=60 * 6, turns_since=40)
    assert eu.build_emotional_context({**state, "read_this_turn": False}, NOW) == ""


def test_reasoner_prompt_asks_for_a_gist():
    from backend.components.constraints import REASONER_PROMPT
    assert '"gist"' in REASONER_PROMPT


@run_async
async def test_reasoner_carries_the_gist_through_a_neutral_topic_change(monkeypatch):
    monkeypatch.setattr(
        aw.lite_llm, "ainvoke",
        AsyncMock(side_effect=[
            _flags_response(valence="distressed", intensity=0.9, need="venting", gist="Tina broke up with the user tonight"),
            _flags_response(valence="neutral", intensity=0.0, need="none", gist=""),
        ]),
    )
    first = await aw.reasoner_node({"messages": [HumanMessage(content="tina broke up with me")]})
    second = await aw.reasoner_node({
        "messages": [HumanMessage(content="anyway, how does the retry logic work?")],
        "emotional_state": first["emotional_state"],
    })

    assert second["emotional_state"]["gist"] == "Tina broke up with the user tonight"
    assert second["emotional_state"]["turns_since"] == 1
    assert "Tina broke up" in eu.build_emotional_context(second["emotional_state"], datetime.now(timezone.utc))

