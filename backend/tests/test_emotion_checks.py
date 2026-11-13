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


# ---- attunement (2026-11-10, Jack's blind A/B): hedged, scripted, thin or interviewing replies to someone low -------------------------------------------------------------------------------------------------------
# Every text below is made up.

SUBSTANTIVE = ("Four days of quiet after an argument is its own kind of loud, because every hour without a reply starts to read like an answer even when it is only her avoiding the conversation. "
               "The waiting is what is wearing on you more than the fight itself.")


def test_a_reply_that_makes_them_retell_what_happened_is_a_probing_question():
    assert ec.reply_attunement_issue(SUBSTANTIVE + " What was the last thing she sent you?") == "probing_question"
    assert ec.reply_attunement_issue(SUBSTANTIVE + " What did she say when you asked her why?") == "probing_question"
    assert ec.reply_attunement_issue(SUBSTANTIVE + " Why do you think she went quiet?") == "probing_question"


def test_a_concrete_forward_question_or_no_question_at_all_is_fine():
    assert ec.reply_attunement_issue(SUBSTANTIVE + " Do you want to work out a short message to send first, or give it until your head is clearer?") is None
    assert ec.reply_attunement_issue(SUBSTANTIVE) is None
    assert ec.reply_attunement_issue("") is None


def test_two_questions_in_one_reply_is_too_many():
    assert ec.reply_attunement_issue(SUBSTANTIVE + " Do you want to send a message? Or wait until tomorrow?") == "two_questions"


def test_stock_reflective_listening_phrases_are_caught():
    for phrase in ("It sounds like you are carrying a lot.", "My read of it is that she needed space.", "Lots of people go quiet after a fight.", "Most people replay it for days.", "People often need space."):
        assert ec.reply_attunement_issue(SUBSTANTIVE + " " + phrase) == "template_phrase", phrase
    assert ec.reply_attunement_issue("That sounds awful, and the waiting is the worst of it because there is nothing to do with it.") is None      # "sounds awful" is not the template


def test_that_must_be_hard_needs_something_with_it():
    assert ec.reply_attunement_issue("That must be hard to sit with.") == "thin_comfort"
    assert ec.reply_attunement_issue("That must be hard. I am sorry you are going through it, and I am here.") == "thin_comfort"
    # long enough but still says nothing about what it is like
    padding = "I am thinking about you and I hope the evening goes gently for you and that you find something small to do with the hours you have left before bed tonight and tomorrow too."
    assert ec.reply_attunement_issue("That must be hard. " + padding) == "thin_comfort"
    # with substance (an explaining phrase and enough words) it is allowed
    assert ec.reply_attunement_issue("That must be hard. " + SUBSTANTIVE) is None


def test_hedging_is_allowed_once_and_not_more():
    assert ec.reply_attunement_issue("Four days of quiet is loud, because every hour without a reply reads like an answer. It might be only her avoiding the conversation.") is None
    assert ec.reply_attunement_issue("Four days of quiet is loud. It might be that she is avoiding it, or maybe she probably needs space, and it seems like a lot.") == "hedged"


def test_talk_about_how_such_things_go_is_allowed_once_and_regenerated_at_two():
    """Jack, 2026-11-10: someone venting wants to be heard, not told what is typical ("often", "can be", "tends to")."""
    own = "Four days of quiet after the argument is loud, because every hour without a reply starts to read like an answer."
    assert ec.reply_attunement_issue(own + " Silence after a fight can feel like a verdict.") is None
    assert ec.reply_attunement_issue(own + " Silence after a fight can feel like a verdict, and it is easy to read it as anger. It often is not.") == "generalizing"
    assert ec.reply_attunement_issue("The replaying is your mind hunting for a way to fix it. People tend to do that, and it is common to do it at night.") in ("generalizing", "template_phrase")
    assert ec.reply_attunement_issue(own + " Waiting for her to call is the part that wears on you.") is None


def test_when_x_happens_its_usually_because_of_y_is_regenerated_on_one_occurrence():
    """Jack, 2026-11-11, blind round 3: "when x happens, its usually because of y" is the reply he did not want ("yeah no shit")."""
    own = "Being told in front of everyone, with no room to answer, is a particular kind of sting."
    assert ec.reply_attunement_issue(own + " When someone goes quiet like that, it is usually because they are overwhelmed.") == "generalizing"
    assert ec.reply_attunement_issue(own + " Flatness is often what too much looks like, because the volume gets turned down.") == "generalizing"
    assert ec.reply_attunement_issue(own + " That is usually a sign you are worn out.") == "generalizing"
    assert ec.reply_attunement_issue(own + " The nod was the only thing you could manage, and the afternoon replayed it.") is None


def test_a_low_reading_regenerates_a_scripted_reply_whatever_the_need_and_a_good_reply_passes():
    for need in ("venting", "reassurance", "solving", "connection"):
        assert ec.emotional_reply_issue(_state("low", need), "It sounds like you are carrying a lot.") == "template_phrase", need
    assert ec.emotional_reply_issue(_state("low", "venting"), SUBSTANTIVE) is None
    # the older checks still win first
    assert ec.emotional_reply_issue(_state("distressed", "venting"), "You should talk to someone. It sounds like a lot.") == "venting_advice"
    # a positive feeling is not held to it
    assert ec.emotional_reply_issue(_state("positive", "connection"), "It sounds like you are carrying a lot.") is None


def test_every_attunement_tag_has_a_revision_reason_the_model_is_told():
    for tag in ("probing_question", "two_questions", "template_phrase", "thin_comfort", "hedged", "generalizing", "restating", "repeated_presence"):
        assert ec._REVISION_REASONS[tag].strip(), tag


def test_the_eval_scorer_uses_the_same_patterns_as_the_live_check():
    from backend.evals import character_eval as ce

    assert ce._PROBE_RE is ec.PROBE_RE and ce._STATE_ASK_RE is ec.STATE_ASK_RE and ce.EXPLAIN_RE is ec.EXPLAIN_RE


MESSAGE = ("My best friend and I haven't spoken in four days after the fight, and every time my phone buzzes I check it, and it is never her, so I just sit there waiting.")


def test_a_reply_that_opens_by_handing_the_message_back_is_flagged_restating():
    # three copied words in a row
    assert ec.reply_attunement_issue("Every time my phone buzzes you check it. Nothing from her yet is its own kind of ache.", MESSAGE) == "restating"
    # two separate pairs of content words copied
    assert ec.reply_attunement_issue("Four days after the fight, with your best friend gone quiet. That weight sits on the whole day.", MESSAGE) == "restating"
    # most of the opening's content words are the message's own
    assert ec.reply_attunement_issue("Waiting phone, checking buzzes, never her.", MESSAGE) == "restating"


def test_a_reply_that_opens_with_something_new_is_not_flagged_restating():
    fresh = "A silence that long starts to feel like a verdict, and the buzzing makes you hope all over again each time. Nothing about it is petty."
    assert ec.reply_attunement_issue(fresh, MESSAGE) is None
    # a shared word or two is not restating, and a copy that sits after the opening is not read
    assert ec.reply_attunement_issue("The waiting is the worst part, and it wears you down quietly. Nothing about it is silly.", MESSAGE) is None
    later = "That kind of quiet is heavy to carry around, and it deserves more than a shrug. It would make sense to feel worn out by now. Four days after the fight, every buzz of your phone."
    assert ec.reply_attunement_issue(later, MESSAGE) is None


def test_restating_needs_the_message_and_ranks_below_the_other_tags():
    echo = "Every time my phone buzzes you check it. Nothing from her yet is its own kind of ache."
    assert ec.reply_attunement_issue(echo) is None and ec.reply_attunement_issue(echo, "") is None
    assert ec.reply_attunement_issue("It sounds like every time my phone buzzes you check it.", MESSAGE) == "template_phrase"
    # a short message gives too little to judge a ratio by, but a copied run of words still counts
    assert ec.reply_attunement_issue("Quiet nights feel long, lonely and loud.", "Tonight is quiet and lonely.") is None
    assert ec.reply_attunement_issue("Tonight is quiet and lonely, yes. That is a lot to sit in.", "Tonight is quiet and lonely.") == "restating"


def test_the_emotional_check_passes_the_message_through_for_a_hurting_person_only():
    echo = "Every time my phone buzzes you check it. Nothing from her yet is its own kind of ache."
    assert ec.emotional_reply_issue(_state("low", "venting"), echo, MESSAGE) == "restating"
    assert ec.emotional_reply_issue(_state("low", "venting"), echo) is None
    assert ec.emotional_reply_issue(_state("positive", "connection"), echo, MESSAGE) is None
    assert ec.build_emotional_revision_prompt("PROMPT", echo, "restating")[1] == ec._REVISION_REASONS["restating"]


def test_the_restating_check_is_off_in_the_app_because_regenerating_did_not_beat_the_first_draft():
    import pathlib

    # 10 blind real-message pairs (2026-11-11): regenerated 3, first draft 3, both 3, neither 1; the regenerated reply twice lost for wrong energy. The check stays in emotion_checks for the eval and for later; the app does not pass the message.
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    call = source[source.index('emotional_issue = None if safety_plan.active else emotional_reply_issue('):]
    call = call.split(chr(10))[0]
    assert 'emotional_reply_issue(final_state.get("emotional_state"), checked_reply, earlier_replies=' in call and "plain_quotes(question)" not in call      # the earlier replies are the one thing added after (2026-11-13)
    assert "earlier_ai_replies(chat_sessions[history_key])" in call
    assert ec.emotional_reply_issue(_state("low", "venting"), "Every time my phone buzzes you check it. Nothing from her yet is its own kind of ache.") is None


# ---- a presence line said again within the last two replies (2026-11-13: it closed 8 of 10 replies in one real hour) -------------------------------------------------------------------------------------------------------
STAMPED = "That was a heavy afternoon to carry out of a room. I'm right here with you."
PLAIN = "That was a heavy afternoon to carry out of a room, and the last line you did not get to finish is the one still open."


def test_a_presence_line_is_repeated_only_when_one_of_the_last_two_replies_said_one():
    assert ec.reply_repeats_presence(STAMPED, [PLAIN, STAMPED]) is True
    assert ec.reply_repeats_presence(STAMPED, [STAMPED, PLAIN]) is True
    assert ec.reply_repeats_presence(STAMPED, [STAMPED, PLAIN, PLAIN]) is False                 # three replies back is far enough
    assert ec.reply_repeats_presence(STAMPED, [PLAIN, PLAIN]) is False and ec.reply_repeats_presence(STAMPED, []) is False and ec.reply_repeats_presence(STAMPED, None) is False
    assert ec.reply_repeats_presence(PLAIN, [STAMPED, STAMPED]) is False                        # a reply without one is never the repeat


def test_the_presence_lines_it_knows_and_the_ones_it_leaves_alone():
    for line in ("I'm right here with you.", "i am right here.", "I’m right here with you", "I'll be right here.", "I'm here with you.", "I am still here for you.", "I've got you."):
        assert ec.PRESENCE_LINE_RE.search(line), line
    for line in ("You are not alone in this.", "The right answer is here.", "Here is the file you asked for.", "I got the file you asked for."):
        assert not ec.PRESENCE_LINE_RE.search(line), line
    assert ec.reply_repeats_presence("Something specific. I’m right here with you.", ["I'm right here with you."]) is True            # typographic apostrophes
    assert ec.reply_repeats_presence("Something specific.\n<<<FOLLOW_UP: I'm right here with you>>>", [STAMPED]) is False                # the follow-up tag is not the reply


def test_earlier_ai_replies_reads_the_assistant_messages_of_a_real_history_oldest_first():
    from langchain_core.messages import AIMessage, HumanMessage

    history = [HumanMessage(content="one"), AIMessage(content="reply one"), HumanMessage(content="two"), AIMessage(content="reply two"), HumanMessage(content="three"), AIMessage(content="reply three"), HumanMessage(content="four")]
    assert ec.earlier_ai_replies(history) == ["reply two", "reply three"]
    assert ec.earlier_ai_replies(history, 1) == ["reply three"] and ec.earlier_ai_replies([]) == [] and ec.earlier_ai_replies(None) == []
    assert ec.earlier_ai_replies([AIMessage(content=[{"type": "text", "text": "not plain"}]), AIMessage(content="plain")]) == ["plain"]


def test_a_hurting_person_gets_a_presence_line_regenerated_only_when_it_was_just_said_and_the_older_checks_come_first():
    earlier = [PLAIN, STAMPED]
    assert ec.emotional_reply_issue(_state("low", "venting"), STAMPED, earlier_replies=earlier) == "repeated_presence"
    assert ec.emotional_reply_issue(_state("low", "venting"), STAMPED) is None and ec.emotional_reply_issue(_state("low", "venting"), STAMPED, earlier_replies=[PLAIN, PLAIN]) is None
    assert ec.emotional_reply_issue(_state("positive", "connection"), STAMPED, earlier_replies=earlier) is None                           # a good mood is not held to it
    assert ec.emotional_reply_issue(_state("low", "venting"), "It sounds like a lot. I'm right here with you.", earlier_replies=earlier) == "template_phrase"
    assert ec.build_emotional_revision_prompt("PROMPT", STAMPED, "repeated_presence")[1] == ec._REVISION_REASONS["repeated_presence"]


def test_the_app_hands_the_check_the_replies_already_in_the_history():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "from backend.utils.emotion_checks import earlier_ai_replies, emotional_reply_issue" in source
