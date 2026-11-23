"""The repeat checks run before the reply (backend/utils/reply_bans.py): every ban is what emotion_checks would catch after the reply, worked out from the same earlier replies. No model."""
import re
from pathlib import Path

import pytest

from backend.utils import emotion_checks as ec, reply_bans, self_counters

APP = Path(__file__).resolve().parents[2].joinpath("app.py").read_text(encoding="utf-8")

EARLIER = [
    "That's a brutal thing to hear. You don't have to decide anything tonight. The 7:28 text is still sitting there.",
    "I hear you, Jack. The 7:28 text can wait until morning. You don't have to answer it. I'm right here with you.",
    "That's a brutal way to end the day. You reached out and the 7:28 text is where it stopped.",
]


def test_every_kind_of_repeat_the_after_check_catches_is_named_before():
    found = reply_bans.bans(EARLIER, "she still hasn't answered")
    assert found["openings"] == ["That's a brutal", "I hear you"]
    assert found["presence"] and found["permission"]
    assert any("7:28" in p for p in found["points"])
    # and each one is exactly what the after-reply check flags in a draft that repeats it
    assert ec.reply_repeats_opening("That's a brutal night.", EARLIER)
    assert ec.reply_repeats_presence("Okay. I'm right here with you.", EARLIER[-2:])
    assert ec.reply_repeats_permission("You don't have to text back.", EARLIER[-2:])
    assert ec.reply_recycles_point("The 7:28 text can wait.", EARLIER, "she still hasn't answered")


def test_a_point_made_only_once_before_is_not_banned():
    once = ["Okay, go on then.", "Fair enough, noted.", "The 9:15 bus is the one to catch."]
    assert reply_bans.repeated_points(once) == []
    assert not ec.reply_recycles_point("The 9:15 bus is the one.", once)


def test_a_point_their_own_message_brings_up_is_not_banned():
    assert not any("7:28" in p for p in reply_bans.bans(EARLIER, "should I answer the 7:28 text?")["points"])


def test_windows_follow_the_checks_presence_and_permission_look_back_two_openings_three():
    old = ["I'm right here with you. You don't have to.", "Okay then, go on.", "Fine, noted that one.", "Sure thing, Jack."]
    found = reply_bans.bans(old)
    assert not found["presence"] and not found["permission"]                     # three replies ago: the checks would not flag it either
    assert found["openings"] == ["Okay then, go", "Fine, noted that", "Sure thing, Jack"]
    assert not ec.reply_repeats_presence("I'm right here with you.", old[-ec.PRESENCE_WINDOW:])


def test_points_need_two_earlier_replies_and_are_quoted_readably_at_most_three():
    assert reply_bans.repeated_points(EARLIER[:1]) == []
    many = ["Mon at 3:00. Tue at 4:00. Wed at 5:00. Thu at 6:00."] * 3
    points = reply_bans.repeated_points(many)
    assert len(points) == reply_bans.MAX_POINTS
    assert all(len(p) <= reply_bans.MAX_POINT_CHARS + 1 for p in points)


def test_the_block_says_each_ban_and_nothing_when_nothing_is_banned():
    block = reply_bans.build_block(reply_bans.bans(EARLIER, "x"))
    assert "Don't open with \"That's a brutal\" or \"I hear you\"" in block
    assert "No presence line this time" in block and "No \"you don't have to...\"" in block and "7:28" in block
    assert reply_bans.build_block({"openings": [], "presence": False, "permission": False, "points": []}) == ""
    assert reply_bans.build_block(None) == ""


@pytest.mark.parametrize("state, applies, label", [
    ({"read_this_turn": True, "valence": "distressed"}, True, "distressed"),
    ({"read_this_turn": True, "valence": "low"}, True, "low"),
    ({"read_this_turn": True, "valence": "frustrated"}, True, "frustrated"),
    ({"read_this_turn": True, "valence": "low", "lift_this_turn": True}, False, "low"),
    ({"read_this_turn": False, "valence": "low"}, False, "other"),
    ({"read_this_turn": True, "valence": "positive"}, False, "other"),
    (None, False, "other"),
])
def test_it_applies_where_the_after_check_does_and_labels_the_mood_for_the_counts(state, applies, label):
    assert reply_bans.applies(state) is applies
    assert reply_bans.mood_label(state) == label


def test_the_app_shows_the_bans_from_the_same_earlier_replies_as_the_check_and_counts_checks_and_rewrites_by_mood():
    assert "if not safety_plan.active and reply_bans.applies(_mood):" in APP
    assert "[plain_quotes(r) for r in earlier_ai_replies(chat_sessions[history_key], RECYCLE_WINDOW)], final_state.get(\"original_question\", question)))" in APP
    assert "emotional_context += _bans_block" in APP
    assert re.search(r'tally\(turn_counts, "reply_fit_checked"\)\n\s+tally\(turn_counts, "fit_checked_mood", reply_bans\.mood_label', APP)
    assert re.search(r'tally\(turn_counts, "revised", revision_kind\(revision_tag\)\)\n\s+tally\(turn_counts, "revised_mood", reply_bans\.mood_label', APP)
    for key in ("reply_bans_shown", "fit_checked_mood_distressed", "revised_mood_low", "revised_mood_other"):
        assert key in self_counters.KEYS
