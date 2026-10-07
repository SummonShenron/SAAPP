from datetime import datetime, timezone

import pytest

from backend.components.constraints import build_voice_prompt
from backend.utils import time_utils as tu

# 2026-10-07 02:14 UTC is 9:14 PM on Tuesday, Oct 6 in Chicago (CDT, UTC-5): the UTC date is already "tomorrow".
UTC_NOW = datetime(2026, 10, 7, 2, 14, tzinfo=timezone.utc)


def test_local_today_is_the_users_date_not_the_servers():
    assert UTC_NOW.strftime("%Y-%m-%d") == "2026-10-07"
    assert tu.local_today("America/Chicago", UTC_NOW) == "2026-10-06"
    assert tu.local_today("Europe/London", UTC_NOW) == "2026-10-07"
    assert tu.local_today("Pacific/Auckland", UTC_NOW) == "2026-10-07"


def test_time_phrase_gives_clock_weekday_date_part_of_day_and_zone():
    assert tu.time_phrase("America/Chicago", UTC_NOW) == "9:14 PM on Tuesday, October 6, 2026 (night, America/Chicago)"


@pytest.mark.parametrize("hour,label", [
    (5, "morning"), (11, "morning"), (12, "afternoon"), (16, "afternoon"), (17, "evening"), (20, "evening"),
    (21, "night"), (23, "night"), (0, "night"), (1, "late night"), (4, "late night"),
])
def test_part_of_day_boundaries(hour, label):
    assert tu.part_of_day(hour) == label


def test_leading_zero_is_dropped_and_midnight_reads_as_12():
    assert tu.time_phrase("UTC", datetime(2026, 1, 5, 9, 5, tzinfo=timezone.utc)).startswith("9:05 AM on Monday")
    assert tu.time_phrase("UTC", datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc)).startswith("12:30 AM")


def test_unknown_or_missing_timezone_falls_back_instead_of_raising():
    assert tu.local_today("Not/AZone", UTC_NOW) == tu.local_today(tu.DEFAULT_TIMEZONE, UTC_NOW)
    assert tu.local_today(None, UTC_NOW) == tu.local_today(tu.DEFAULT_TIMEZONE, UTC_NOW)


def test_naive_datetime_is_treated_as_utc():
    assert tu.local_today("America/Chicago", datetime(2026, 10, 7, 2, 14)) == "2026-10-06"


def test_voice_prompt_carries_the_time_block_only_when_given():
    base = dict(grounding_block="G", data="", history="h", question="q")
    with_time = build_voice_prompt(**base, time_context=tu.build_time_context("America/Chicago", UTC_NOW))
    assert "CURRENT TIME" in with_time and "October 6, 2026" in with_time
    assert "CURRENT TIME" not in build_voice_prompt(**base)


def test_time_block_tells_the_model_not_to_announce_it():
    block = tu.build_time_context("America/Chicago", UTC_NOW)
    assert "don't announce it" in block
    assert "wrong part of the day" in block


def test_agent_line_has_no_braces_so_it_cannot_break_prompt_formatting():
    line = tu.build_agent_time_line("America/Chicago", UTC_NOW)
    assert "{" not in line and "}" not in line
    assert "today" in line and "2026" in line


# ---------------------------------------------------------------------------------------------------------------
# Awareness of how long it has been between messages
# ---------------------------------------------------------------------------------------------------------------
from datetime import timedelta

from langchain_core.messages import AIMessage, HumanMessage

from backend.utils.app_utils import _deserialize_messages, _serialize_messages, format_history_as_text


def _at(minutes_ago, base=UTC_NOW):
    return base - timedelta(minutes=minutes_ago)


def test_stamp_records_utc_iso_and_leaves_an_existing_stamp_alone():
    msg = tu.stamp(HumanMessage(content="hi"), UTC_NOW)
    assert tu.sent_at(msg) == UTC_NOW
    tu.stamp(msg, _at(500))
    assert tu.sent_at(msg) == UTC_NOW


def test_an_unstamped_or_garbled_message_has_no_time_rather_than_a_guess():
    assert tu.sent_at(HumanMessage(content="old")) is None
    bad = HumanMessage(content="x", additional_kwargs={"sent_at": "not a date"})
    assert tu.sent_at(bad) is None


def test_the_stamp_survives_save_and_load():
    original = [tu.stamp(HumanMessage(content="hi"), _at(90)), tu.stamp(AIMessage(content="hello"), _at(89))]
    reloaded = _deserialize_messages(_serialize_messages(original))
    assert [tu.sent_at(m) for m in reloaded] == [_at(90), _at(89)]
    assert reloaded[0].content == "hi" and isinstance(reloaded[1], AIMessage)


def test_a_message_saved_before_stamping_still_loads_without_a_time():
    reloaded = _deserialize_messages([{"type": "human", "content": "old"}])
    assert reloaded[0].content == "old" and tu.sent_at(reloaded[0]) is None


@pytest.mark.parametrize("seconds,expected", [
    (20 * 60, "about 20 minutes"), (60, "about 1 minute"), (3 * 3600, "about 3 hours"), (3600, "about 1 hour"),
    (2 * 86400, "about 2 days"), (21 * 86400, "about 3 weeks"), (90 * 86400, "about 3 months"),
])
def test_describe_gap_rounds_to_a_human_phrase(seconds, expected):
    assert tu.describe_gap(seconds) == expected


def test_time_block_mentions_a_real_gap_and_never_says_anything_about_feelings():
    block = tu.build_time_context("America/Chicago", UTC_NOW, last_message_at=_at(180))
    assert "about 3 hours ago" in block
    assert "coming back after a pause" in block
    assert "Acknowledge the gap lightly" in block
    for feeling_word in ("felt", "feel", "mood", "emotion", "energy", "tone"):
        assert feeling_word not in block.lower()


def test_with_an_emotional_context_the_gap_is_pure_fact_and_defers_the_tone_to_it():
    block = tu.build_time_context("America/Chicago", UTC_NOW, last_message_at=_at(180), defer_to_emotion=True)
    assert "about 3 hours ago" in block
    assert "emotional and safety guidance in this prompt sets your tone" in block
    assert "Don't remark on the gap" in block
    # the two suggestions that could pull against an energy ceiling or the returning-user rules are gone
    assert "Acknowledge the gap" not in block
    assert "coming back after a pause" not in block


def test_a_gap_note_never_contradicts_the_carried_emotion_rules_in_the_same_prompt():
    from backend.utils.emotion_utils import build_emotional_context
    state = {"valence": "distressed", "intensity": 0.9, "updated_at": _at(30).isoformat(), "turns_since": 1,
             "need": "venting", "gist": "a rough day with their boss", "positive_peak": 0.0}
    emo = build_emotional_context(state, UTC_NOW)
    assert emo, "the scenario needs an active emotional context"
    time_block = tu.build_time_context("America/Chicago", UTC_NOW, last_message_at=_at(30), defer_to_emotion=bool(emo))
    prompt = build_voice_prompt(grounding_block="G", data="", history="h", question="q", emotional_context=emo, time_context=time_block)
    # the emotional rules are intact, and the time block comes first so they have the last word
    assert "EMOTIONAL CONTEXT" in prompt
    assert prompt.index("CURRENT TIME") < prompt.index("EMOTIONAL CONTEXT")
    # nothing in the time block tells the model to relax, move on, or assume the feeling has passed
    lowered = time_block.lower()
    for relax in ("still how they feel", "moved on", "no longer", "cheer", "lighten", "upbeat"):
        assert relax not in lowered


def test_time_block_says_nothing_about_a_short_pause_a_first_message_or_a_future_stamp():
    assert "previous message" not in tu.build_time_context("UTC", UTC_NOW, last_message_at=_at(5))
    assert "previous message" not in tu.build_time_context("UTC", UTC_NOW, last_message_at=None)
    assert "previous message" not in tu.build_time_context("UTC", UTC_NOW, last_message_at=UTC_NOW + timedelta(hours=2))


def test_history_shows_a_marker_only_across_a_long_pause_between_stamped_messages():
    history = [
        tu.stamp(HumanMessage(content="rough day"), _at(60 * 50)),
        tu.stamp(AIMessage(content="I'm here"), _at(60 * 50 - 1)),
        tu.stamp(HumanMessage(content="thanks"), _at(60 * 50 - 3)),
        tu.stamp(HumanMessage(content="new question"), _at(1)),
    ]
    text = format_history_as_text(history)
    assert text.count("later]") == 1
    assert "[about 2 days later]\nUser: new question" in text
    assert "later]" not in text.split("User: new question")[0].split("User: thanks")[0]


def test_history_without_stamps_is_unchanged():
    history = [HumanMessage(content="a"), AIMessage(content="b"), HumanMessage(content="c")]
    assert format_history_as_text(history) == "User: a\nAssistant: b\nUser: c"
