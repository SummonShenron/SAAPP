"""Relative days in a note or fact become real dates when it is saved (backend/utils/date_resolve.py), in the person's own timezone, and only when there is one clear answer."""
import re
from datetime import datetime, timezone

import pytest

from backend.utils.date_resolve import MAX_ANNOTATIONS, annotate_dates

WED_AFTERNOON = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)      # Wednesday 2:00 PM in New York
NY = "America/New_York"


def annotated(text, now=WED_AFTERNOON, tz=NY):
    return annotate_dates(text, tz, now)


@pytest.mark.parametrize("text,expected", [
    ("Has a date with Ashlyn tonight.", "Has a date with Ashlyn tonight (Wednesday 2026-10-21)."),
    ("Has an interview tomorrow.", "Has an interview tomorrow (Thursday 2026-10-22)."),
    ("Has a call tomorrow morning.", "Has a call tomorrow (Thursday 2026-10-22) morning."),
    ("Went to the dentist yesterday.", "Went to the dentist yesterday (Tuesday 2026-10-20)."),
    ("Was out late last night.", "Was out late last night (Tuesday 2026-10-20)."),
    ("Has a meeting this afternoon.", "Has a meeting this afternoon (Wednesday 2026-10-21)."),
    ("Has an interview Friday.", "Has an interview Friday (2026-10-23)."),
    ("Has an interview on friday.", "Has an interview on friday (2026-10-23)."),
    ("Has a dentist appointment this Saturday.", "Has a dentist appointment this Saturday (2026-10-24)."),
    ("Saw the doctor last Friday.", "Saw the doctor last Friday (2026-10-16)."),
    ("Is flying out on Monday.", "Is flying out on Monday (2026-10-26)."),
])
def test_a_clear_relative_day_gets_its_real_date_right_after_the_words(text, expected):
    assert annotated(text) == expected


@pytest.mark.parametrize("text", [
    "Has a meeting Wednesday.",                      # said on a Wednesday: today, or a week on?
    "Has a flight next Friday.",                     # two different days to two different people
    "Plays chess every Monday.",                     # not one day
    "Is busy this weekend.", "Starts next week.", "Will call soon.", "Likes Wednesday's quiz night.",
    "Has a dog named Max.", "Works at Principal.", "",
])
def test_an_unclear_one_is_left_exactly_as_written(text):
    assert annotated(text) == text


def test_the_date_is_the_persons_own_day_not_the_servers():
    late_ny_evening = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)       # still Wednesday 10 PM in New York, already Thursday in UTC
    assert annotated("Has a date tonight.", late_ny_evening) == "Has a date tonight (Wednesday 2026-10-21)."
    assert annotated("Has a date tonight.", late_ny_evening, tz="UTC") == "Has a date tonight (Thursday 2026-10-22)."
    assert annotated("Has a date tomorrow.", late_ny_evening) == "Has a date tomorrow (Thursday 2026-10-22)."


def test_running_it_twice_changes_nothing():
    once = annotated("Has a date tonight and an interview Friday.")
    assert annotated(once) == once
    assert annotated("Has a date tonight (Wednesday 2026-10-21).") == "Has a date tonight (Wednesday 2026-10-21)."


def test_it_adds_a_date_and_nothing_else():
    original = "Has a date with Ashlyn tonight at Tidos and an interview on Friday."
    result = annotated(original)
    stripped = re.sub(r" \((?:[A-Z][a-z]+ )?\d{4}-\d{2}-\d{2}\)", "", result)
    assert stripped == original                                                 # no time, place or name appears, and the person's wording is intact
    assert "7" not in result.replace("2026-10-21", "").replace("2026-10-23", "")


def test_the_same_words_twice_get_one_date_and_a_note_full_of_days_gets_only_the_first_few():
    assert annotated("Has a call tomorrow, then lunch tomorrow.").count("(") == 1
    crowded = annotated("Mon plans: yesterday, tonight, tomorrow, Friday, Saturday, Sunday.")
    assert crowded.count("(") == MAX_ANNOTATIONS


def test_a_weekday_inside_another_word_or_a_range_is_not_a_day():
    assert annotated("Open Monday-Friday.") == "Open Monday-Friday."
    assert annotated("The Fridayish crowd.") == "The Fridayish crowd."


def test_it_is_wired_into_the_two_places_a_note_or_fact_is_written():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    app_utils = (root / "backend" / "utils" / "app_utils.py").read_text(encoding="utf-8")
    workflow = (root / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    index = app_utils[app_utils.index("async def index_conversation_turn"):]
    assert "annotate_dates(summary_text" in index and index.index("annotate_dates(summary_text") > index.index("summarize_turn(")
    node = workflow[workflow.index("memory_grounding.ground_fact(fact_text"):]
    assert "annotate_dates(fact_text" in node[:1500] and node.index("annotate_dates(fact_text") < node.index("save_user_fact(")
