"""Repetition across replies and corrections carried forward (backend/utils/emotion_checks.py cross-reply checks, backend/utils/corrections.py), from Jack's chat review of 2026-11-23.
The replies here are written in the shapes found in the real thread (openings, "you don't have to", the same reassurance handed back). No model."""
import os
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import corrections, emotion_checks as ec, revision_reasons, self_counters
from backend.utils.emotion_utils import merge_emotional_state

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime.now(timezone.utc)
LOW = merge_emotional_state(None, {"valence": "low", "intensity": 0.7, "need": "venting"}, NOW)


# ---- the same opening, the same permission ---------------------------------------------------------------------------------------------------------

def test_the_same_first_words_as_a_recent_reply_are_a_repeat():
    earlier = ["I hear you. Waiting on her is hard.", "The quiet is the hard part tonight."]
    assert ec.reply_repeats_opening("I hear you, Jack. The no landed hard.", earlier)
    assert not ec.reply_repeats_opening("Hearing that she said no is hard.", earlier)
    assert not ec.reply_repeats_opening("I hear you.", ["a", "b", "c", "I hear you. old"][-ec.OPENING_WINDOW - 1:-ec.OPENING_WINDOW])     # outside the window
    assert ec.reply_repeats_opening("I hear you. again", ["x x x", "y y y", "I hear you. recent"])
    assert not ec.reply_repeats_opening("Ok.", ["Ok."])                                                                        # too short to judge


def test_you_dont_have_to_again_within_two_replies_is_a_repeat():
    assert ec.reply_repeats_permission("You don't have to decide tonight.", ["You don’t need to answer her yet."])
    assert ec.reply_repeats_permission("You do not have to fix it.", ["x", "you don't have to be fine."])
    assert not ec.reply_repeats_permission("You don't have to decide tonight.", ["you don't have to.", "x", "y"])            # three back: allowed again
    assert not ec.reply_repeats_permission("Take your time.", ["You don't have to decide."])


# ---- the same point handed back -----------------------------------------------------------------------------------------------------------------

TUESDAY_1 = "Tuesday is still on the calendar, and nothing in her reply took it off."
TUESDAY_2 = "Her reply answered today. Tuesday is still the plan."


def test_an_anchor_used_in_two_recent_replies_is_recycled_on_its_third_use():
    assert ec.reply_recycles_point("Tuesday is still there, whatever today brings.", [TUESDAY_1, TUESDAY_2])
    assert not ec.reply_recycles_point("Tuesday is still there.", [TUESDAY_1, "Nothing about the plan."])                  # a second use is a callback, not a loop
    assert ec.reply_recycles_point("Her 8:40 message was about her day.", ["At 8:40 she wrote back.", "x", "Her 8:40 message told you about her day."])


def test_a_quoted_phrase_or_a_four_word_point_counts_too():
    q = 'She wrote "So we should have time" and meant it.'
    assert ec.reply_recycles_point('Remember "so we should have time" from her.', [q, 'Her line "So we should have time" still stands.'])
    point = "you wanted to fall asleep beside her while she slept"
    assert ec.reply_recycles_point("You said you wanted to fall asleep beside her.", [point, "You keep picturing how you'd fall asleep beside her."])
    assert not ec.reply_recycles_point("It fell through again.", ["It fell through last night.", "When it fell through it hurt."])      # filler, not a point


def test_what_their_message_brings_up_is_not_recycled():
    assert not ec.reply_recycles_point("Not making it to Tuesday feels like the ending.", [TUESDAY_1, TUESDAY_2], message="i feel like i wont make it to tuesday")
    assert ec.reply_recycles_point("Not making it to Tuesday feels like the ending.", [TUESDAY_1, TUESDAY_2], message="i feel so alone")


def test_the_emotional_check_returns_the_new_tags_in_order_for_a_low_person_only():
    earlier = ["I hear you. The quiet is hard. You don't have to answer.", TUESDAY_1, TUESDAY_2]
    assert ec.emotional_reply_issue(LOW, "I hear you. It hurts that she said no, and you asked for so little.", earlier_replies=earlier) == "repeated_opening"
    assert ec.emotional_reply_issue(LOW, "It hurts that she said no. Tuesday is still the plan, though.", earlier_replies=earlier) == "recycled_point"
    assert ec.emotional_reply_issue(LOW, "It hurts that she said no. Tuesday is still the plan, though.", earlier_replies=earlier, latest_message="will tuesday happen?") is None
    assert ec.emotional_reply_issue(LOW, "That no landed on the thing you wanted most. You don't have to answer her tonight.",
                                    earlier_replies=["x", "y", "You don't have to fix it."]) == "repeated_permission"
    positive = merge_emotional_state(None, {"valence": "positive", "intensity": 0.7}, NOW)
    assert ec.emotional_reply_issue(positive, "I hear you. Great news.", earlier_replies=earlier) is None


def test_each_new_tag_has_rewrite_guidance_and_a_counted_reason():
    for tag in ("repeated_opening", "repeated_permission", "recycled_point", "repeated_presence"):
        prompt, reason = ec.build_emotional_revision_prompt("P", "draft", tag)
        assert reason and "REJECTED DRAFT" in prompt
        assert revision_reasons.reason_for(tag) == tag and f"revised_reason_{tag}" in self_counters.KEYS


def test_the_app_checks_against_the_last_three_replies_and_their_message():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "earlier_ai_replies(chat_sessions[history_key], RECYCLE_WINDOW)], latest_message=final_state.get(\"original_question\", question))" in app
    assert ec.RECYCLE_WINDOW == 3


# ---- corrections carried forward ----------------------------------------------------------------------------------------------------------------

def test_real_corrections_are_found_at_the_start_of_a_message():
    for text in ("I was the one that asked to talk on the phone not her.. I really don't think she even cares about me..",
                 "no... thats wrong, he looks like this.", "oh i was the one that said today or tomorrow,", "She didn't actually say that, it's how I read it"):
        assert corrections.is_correction(text), text
    long_one = "i lit asked her why she kept letting me kiss her and call her names if she didnt like me and no she said literally there's no answer"
    assert not corrections.is_correction(long_one)                                                    # the phrase sits deep in the message
    assert not corrections.is_correction("she's not right for me i think")


def test_the_last_corrections_in_the_window_are_carried_in_their_words():
    msgs = [HumanMessage(content="I was the one that asked to talk on the phone, not her."), AIMessage(content="You're right.")]
    msgs += [HumanMessage(content=f"message {i}") for i in range(10)]
    assert corrections.recent_corrections(msgs) == ["I was the one that asked to talk on the phone, not her."]
    old = [HumanMessage(content="that's wrong, it was Friday")] + [HumanMessage(content=f"m {i}") for i in range(corrections.WINDOW_MESSAGES)]
    assert corrections.recent_corrections(old) == []                                                  # outside the window
    many = [HumanMessage(content=f"no, she didn't say {i}") for i in range(6)]
    assert corrections.recent_corrections(many) == [f"no, she didn't say {i}" for i in range(2, 6)]  # the last MAX_CARRIED
    assert corrections.recent_corrections([AIMessage(content="I was the one who said it")]) == []    # only their messages
    long = HumanMessage(content="I was the one " + "x" * 400)
    assert corrections.recent_corrections([long])[0].endswith("…") and len(corrections.recent_corrections([long])[0]) <= corrections.MAX_CHARS + 1


def test_the_block_says_they_override_earlier_replies_and_the_app_adds_it():
    block = corrections.build_block(["I was the one that asked."])
    assert '"I was the one that asked."' in block and "your own earlier replies included" in block and "Never state the old" in block
    assert corrections.build_block([]) == ""
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "carried_corrections = corrections_utils.recent_corrections(chat_sessions[history_key])" in app
    assert 'prompt = prompt + corrections_utils.build_block(carried_corrections)\n                tally(turn_counts, "corrections_carried")' in app
    assert "corrections_carried" in self_counters.KEYS


def test_the_opening_window_and_a_shared_first_word():
    assert not ec.reply_repeats_opening("I hear you. again", ["I hear you. oldest", "a b c", "d e f", "g h i"])            # four back: outside the window of three
    assert not ec.reply_repeats_opening("I know this is hard.", ["I hear you, Jack."])                                    # the same first word is not the same opening


def test_a_correction_phrase_past_the_start_of_a_message_does_not_count():
    assert not corrections.is_correction("so anyway we talked about the weekend plans and then " + "that's wrong somehow")
    assert corrections.is_correction("ok so that's wrong, it was Friday")
