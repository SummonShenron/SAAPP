import pytest

from backend.components.constraints import REASONER_PROMPT
from backend.utils import emotion_checks as ec
from backend.utils import emotion_utils as eu

NOW = None  # build_emotional_context takes a clock; these tests use a fixed one below

from datetime import datetime, timezone

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _state(valence, need, intensity=0.8, **extra):
    return {
        "valence": valence, "intensity": intensity, "updated_at": NOW.isoformat(), "turns_since": 0,
        "read_this_turn": True, "need": need, "gist": "", **extra,
    }


# ---------------------------------------------------------------------------
# Advice detection (a venting reply must not give it)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "You should talk to someone about this.",
    "Have you tried journaling about it?",
    "Have you considered taking a few days off?",
    "I'd recommend setting some boundaries with her.",
    "Maybe try to get some sleep tonight.",
    "It might help to write down what you want to say.",
    "Here are a few things that can help right now.",
    "You could try reaching out to a friend.",
    "A few tips: be gentle with yourself.",
    "What helps:\n- take a walk\n- call a friend\n- sleep",
    "Some steps:\n1. breathe\n2. rest",
])
def test_advice_shaped_replies_are_detected(reply):
    assert ec.reply_gives_advice(reply) is True


@pytest.mark.parametrize("reply", [
    "That sounds exhausting, and it makes sense you're angry. What happened with your boss today?",
    "I won't try to fix this, I just want to hear it. Tell me more.",
    "You don't need to have it figured out tonight.",
    "I'm not going to tell you to do anything. I'm here.",
    "She said that to you in front of everyone? That would sting for anyone.",
    "One bullet in the middle of a sentence - like this - is not a list.",
    # Real replies from the live model that an earlier, looser version wrongly flagged as advice:
    "When someone uses their position to put you down publicly, it violates the basic respect you should be able to expect at work.",
    "You do not need to try to solve this or figure out your next steps while the shock is still this fresh.",
    "Here's what I'm hearing: you were blindsided, and the anger is making it hard to think.",
    "You shouldn't have to carry the weight of their unprofessional behavior.",
])
def test_listening_replies_are_not_flagged_as_advice(reply):
    assert ec.reply_gives_advice(reply) is False


# ---------------------------------------------------------------------------
# Curiosity detection (a celebration reply must have some)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "That's so exciting! What did their profile say that made you swipe right?",
    "Congrats! Who reached out first, you or them?",
    "No way. Send me a pic, I want to see!",
    "How are you feeling about it? And what's the first thing you want to say to them?",
    "Amazing. I'd love to see their profile if you're up for it.",
])
def test_replies_with_real_curiosity_pass(reply):
    assert ec.reply_shows_curiosity(reply) is True


@pytest.mark.parametrize("reply", [
    "That's so exciting! Hope it goes well.",
    "Congratulations, that's wonderful news!",
    "Nice! How are you feeling about it?",
    "Are you excited? What do you think will happen?",
    "That's great. How are you doing?",
    "Omg love that! Tell me everything.",  # a bare "tell me more" is the generic version of curiosity
])
def test_excited_only_or_generic_question_replies_fail(reply):
    assert ec.reply_shows_curiosity(reply) is False


def test_a_question_that_lives_only_in_the_follow_up_tag_is_not_the_assistant_being_curious():
    # The real baseline reply for a Hinge match: its only question was in the tag, which the UI
    # renders as a clickable suggested message for the USER, not as the assistant asking anything.
    reply = "Omg love that! That's so exciting! Tell me everything.  <<<FOLLOW_UP: What caught your eye on her profile? >>>"
    assert ec.reply_shows_curiosity(reply) is False
    assert ec.emotional_reply_issue(_state("excited", "celebration"), reply) == "celebration_no_question"


def test_a_real_question_in_the_body_counts_even_when_a_follow_up_tag_is_also_present():
    reply = "That's so exciting! What caught your eye on her profile? <<<FOLLOW_UP: What should I message first? >>>"
    assert ec.reply_shows_curiosity(reply) is True


def test_advice_words_inside_the_follow_up_tag_are_not_advice_the_assistant_gave():
    reply = "That sounds awful, and anyone would be furious. I'm here. <<<FOLLOW_UP: You should talk to HR, right? >>>"
    assert ec.reply_gives_advice(reply) is False
    assert ec.reply_body(reply) == "That sounds awful, and anyone would be furious. I'm here."


# ---------------------------------------------------------------------------
# emotional_reply_issue: only a need read from THIS message is checked
# ---------------------------------------------------------------------------

def test_venting_with_advice_is_an_issue():
    assert ec.emotional_reply_issue(_state("distressed", "venting"), "You should talk to someone.") == "venting_advice"


def test_venting_answered_by_listening_is_fine():
    assert ec.emotional_reply_issue(_state("distressed", "venting"), "That sounds awful. Tell me what happened.") is None


def test_celebration_without_curiosity_is_an_issue():
    assert ec.emotional_reply_issue(_state("excited", "celebration"), "That's amazing, so happy for you!") == "celebration_no_question"


def test_celebration_with_a_specific_question_is_fine():
    assert ec.emotional_reply_issue(_state("excited", "celebration"), "That's amazing! What drew you to them?") is None


def test_a_carried_feeling_from_an_earlier_turn_checks_nothing():
    state = {**_state("distressed", "venting"), "read_this_turn": False}
    assert ec.emotional_reply_issue(state, "You should talk to someone.") is None


def test_a_lift_while_still_carrying_distress_checks_nothing():
    state = {**_state("distressed", "venting"), "lift_this_turn": True}
    assert ec.emotional_reply_issue(state, "You should talk to someone.") is None


def test_other_needs_and_missing_state_check_nothing():
    assert ec.emotional_reply_issue(_state("distressed", "solving"), "You should talk to someone.") is None
    assert ec.emotional_reply_issue(_state("distressed", "reassurance"), "Here are some tips.") is None
    assert ec.emotional_reply_issue(None, "You should") is None
    assert ec.emotional_reply_issue(_state("distressed", "venting"), "") is None


def test_a_need_that_does_not_match_the_valence_is_ignored():
    # "celebration" on a negative reading, or "venting" on a positive one, is a mislabel; never act on it.
    assert ec.emotional_reply_issue(_state("distressed", "celebration"), "Wow.") is None
    assert ec.emotional_reply_issue(_state("excited", "venting"), "You should celebrate.") is None


def test_revision_prompt_keeps_the_original_names_the_problem_and_includes_the_draft():
    prompt, reason = ec.build_emotional_revision_prompt("ORIGINAL PROMPT", "My rejected draft.", "venting_advice")
    assert prompt.startswith("ORIGINAL PROMPT")
    assert "My rejected draft." in prompt
    assert "never mention this note" in prompt
    assert "no advice" in reason
    prompt, reason = ec.build_emotional_revision_prompt("P", "d", "celebration_no_question")
    assert "genuinely curious question" in reason


# ---------------------------------------------------------------------------
# The "celebration" need: recognized by the reasoner and turned into concrete behavior
# ---------------------------------------------------------------------------

def test_the_reasoner_prompt_teaches_the_celebration_need():
    assert '"celebration"' in REASONER_PROMPT
    assert "mundane positive remark" in REASONER_PROMPT


def test_celebration_is_a_recognized_need_and_unknown_ones_still_are_not():
    assert eu.normalize_need({"need": "celebration"}) == "celebration"
    assert eu.normalize_need({"need": "cheering"}) == "none"


def test_a_celebration_reading_survives_the_merge_with_its_gist():
    merged = eu.merge_emotional_state(
        None, {"valence": "excited", "intensity": 0.8, "need": "celebration", "gist": "matched with someone on Hinge"}, NOW
    )
    assert merged["need"] == "celebration" and merged["gist"] == "matched with someone on Hinge"


def test_fresh_celebration_context_asks_for_enthusiasm_and_one_specific_question():
    state = {**_state("excited", "celebration"), "gist": "matched with someone on Hinge"}
    context = eu.build_emotional_context(state, NOW)
    assert "(matched with someone on Hinge)" in context
    assert "ONE specific question" in context
    assert "under-engaging" in context
    assert "how are you feeling about it?" in context  # named as the thing to avoid
    assert "never mention you're tracking it" in context


def test_celebration_context_is_only_for_the_message_that_shared_the_news():
    carried = {**_state("excited", "celebration"), "read_this_turn": False}
    context = eu.build_emotional_context(carried, NOW)
    assert "ONE specific question" not in context
    assert "Let that warmth carry naturally" in context


def test_a_plain_positive_reading_keeps_the_generic_warmth_guidance():
    context = eu.build_emotional_context(_state("positive", "none"), NOW)
    assert "ONE specific question" not in context
    assert "Let that warmth carry naturally" in context
