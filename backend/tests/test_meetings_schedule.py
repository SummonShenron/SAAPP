"""A meeting's optional schedule, set by the person, so the calendar is not needed (backend/utils/meetings.py)."""
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.test_meetings import NOW, SPEC, Db, make
from backend.utils import meetings as mt

ROOT = pathlib.Path(__file__).resolve().parents[2]
TZ = "America/Chicago"
# NOW is Sunday 2026-10-18 12:00 PM in Chicago.
WEEKLY = {**SPEC, "cadence": "weekly", "weekday": 6, "time": "12:00", "tz": TZ}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(mt, "_index_ready", False)


def meeting(db, **changes):
    return make(db, spec={**WEEKLY, **changes})


# ------------------------------------------------------------------------ what is accepted ----------------------------

def test_a_meeting_needs_no_calendar_and_no_schedule():
    spec, issue = mt.validate_meeting({"name": "Check-in"})
    assert issue is None and spec["match"] == "" and spec["cadence"] == "none" and spec["hour"] is None and spec["weekday"] is None


def test_a_weekly_schedule_takes_a_day_and_a_time_and_a_daily_one_only_a_time():
    spec, issue = mt.validate_meeting({**WEEKLY})
    assert issue is None and (spec["cadence"], spec["weekday"], spec["hour"], spec["minute"]) == ("weekly", 6, 12, 0)
    spec, _ = mt.validate_meeting({"name": "Standup", "cadence": "daily", "time": "8:30", "weekday": 3})
    assert (spec["cadence"], spec["weekday"], spec["hour"], spec["minute"]) == ("daily", None, 8, 30)
    spec, _ = mt.validate_meeting({"name": "Standup", "cadence": "daily", "hour": 17, "minute": 45})
    assert (spec["hour"], spec["minute"]) == (17, 45)
    assert mt.validate_meeting({"name": "Free", "cadence": "none", "weekday": 2, "time": "09:00"})[0]["hour"] is None        # no schedule: a time means nothing


@pytest.mark.parametrize("change,fragment", [
    ({"cadence": "hourly"}, "never, every day or every week"), ({"time": "25:00", "hour": 30}, "time of day"), ({"hour": "soon", "time": None}, "time of day"),
    ({"weekday": 9}, "day of the week"), ({"weekday": "monday"}, "day of the week"),
])
def test_a_schedule_that_does_not_exist_is_refused_in_words_for_the_person(change, fragment):
    spec, issue = mt.validate_meeting({**WEEKLY, **change})
    assert spec is None and fragment in issue


def test_the_schedule_is_described_in_words():
    assert mt.schedule_text({"cadence": "weekly", "weekday": 6, "hour": 12, "minute": 0}) == "every Sunday around 12 PM"
    assert mt.schedule_text({"cadence": "daily", "hour": 8, "minute": 30}) == "every day around 8:30 AM"
    assert mt.schedule_text({"cadence": "daily", "hour": 0, "minute": 5}) == "every day around 12:05 AM"
    assert mt.schedule_text({"cadence": "none"}) == "" and mt.schedule_text({"name": "old meeting made before schedules existed"}) == ""


# ------------------------------------------------------------------------ the day it was meant for ---------------------

def test_a_weekly_meeting_is_due_on_its_day_and_a_daily_one_every_day_in_the_persons_own_timezone():
    weekly = mt.validate_meeting(WEEKLY)[0]
    assert mt.is_due_today(weekly, TZ, NOW) and not mt.is_due_today(weekly, TZ, NOW + timedelta(days=1))
    late_saturday_utc = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)          # still Saturday evening in Chicago
    assert not mt.is_due_today(weekly, TZ, late_saturday_utc) and mt.is_due_today(weekly, "UTC", late_saturday_utc)
    daily = mt.validate_meeting({"name": "Standup", "cadence": "daily", "time": "09:00"})[0]
    assert all(mt.is_due_today(daily, TZ, NOW + timedelta(days=i)) for i in range(8))
    assert not mt.is_due_today(mt.validate_meeting({"name": "Free"})[0], TZ, NOW)


def test_the_next_occurrence_is_after_now_so_a_missed_one_today_points_at_the_next_week():
    weekly = mt.validate_meeting(WEEKLY)[0]
    assert mt.next_occurrence(weekly, TZ, NOW - timedelta(hours=1)) == datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)      # an hour before: today at noon
    assert mt.next_occurrence(weekly, TZ, NOW + timedelta(hours=1)) == datetime(2026, 10, 25, 17, 0, tzinfo=timezone.utc)      # an hour late: next Sunday
    assert mt.next_occurrence(mt.validate_meeting({"name": "Free"})[0], TZ, NOW) is None


def test_a_meeting_is_due_all_day_until_it_is_held_and_then_not_again_that_day_and_the_next_day_it_is_a_new_day():
    db = Db()
    made = meeting(db)
    docs = mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW - timedelta(hours=6))                 # early on its day
    assert docs[0]["due_today"] is True and docs[0]["schedule"] == "every Sunday around 12 PM"
    docs = mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW + timedelta(hours=8))                 # hours late, never held: still due that day
    assert docs[0]["due_today"] is True
    run, _ = mt.start_run(db, "u1", made["id"], "chat-1", NOW + timedelta(hours=8))                         # held late in the day
    assert mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW + timedelta(hours=9))[0]["due_today"] is False
    assert mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW + timedelta(days=1, hours=2))[0]["due_today"] is False    # Monday is not a meeting day
    assert mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW + timedelta(days=7))[0]["due_today"] is True             # next Sunday it is again


def test_holding_it_early_or_on_another_day_does_not_stop_its_own_day_from_being_due_and_another_persons_runs_do_not_count():
    db = Db()
    made = meeting(db)
    mt.start_run(db, "u1", made["id"], "chat-1", NOW - timedelta(days=2))                                    # held on Friday, early
    assert mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW)[0]["due_today"] is True
    other = meeting(db, name="Standup", match="")
    mt.start_run(db, "u2", other["id"], "chat-9", NOW)
    assert next(d for d in mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW) if d["id"] == other["id"])["due_today"] is True


def test_an_old_meeting_made_before_schedules_existed_loads_and_has_none():
    db = Db()
    db[mt.COLLECTION].insert_one({"id": "old", "username": "u1", "name": "Old", "match": "old", "purpose": "", "agenda": [], "series_ids": [], "created_at": NOW.isoformat()})
    doc = mt.decorate(db, "u1", mt.list_meetings(db, "u1"), TZ, NOW)[0]
    public = mt.public_meeting(doc)
    assert public["cadence"] == "none" and public["schedule"] == "" and public["next_at"] is None and public["due_today"] is False


def test_the_schedule_can_be_added_changed_and_removed_without_touching_the_rest():
    db = Db()
    made = make(db, spec={"name": "Check-in", "match": "check in", "agenda": ["one"]})
    updated, issue = mt.update_meeting(db, "u1", made["id"], {"cadence": "weekly", "weekday": 2, "time": "17:30"})
    assert issue is None and (updated["cadence"], updated["weekday"], updated["hour"], updated["minute"]) == ("weekly", 2, 17, 30) and updated["agenda"] == ["one"] and updated["match"] == "check in"
    assert mt.update_meeting(db, "u1", made["id"], {"cadence": "daily"})[0]["weekday"] is None
    cleared, _ = mt.update_meeting(db, "u1", made["id"], {"cadence": "none"})
    assert cleared["cadence"] == "none" and cleared["hour"] is None and cleared["name"] == "Check-in"
    assert mt.update_meeting(db, "u1", made["id"], {"cadence": "nonsense"})[0] is None


# ------------------------------------------------------------------------ what Sonic may say -------------------------

def test_asked_what_it_has_planned_it_can_say_a_scheduled_meeting_is_due_today_and_can_still_be_held():
    db = Db()
    meeting(db)
    text = mt.build_schedule_context("u1", TZ, NOW + timedelta(hours=3), db=db)
    assert '"Weekly alignment check-in": every Sunday around 12 PM' in text and "Today: it is due, and it has not been held yet" in text and "any time today" in text and "Today, for this person, is Sunday, October 18." in text
    for rule in ("only starts when the person presses Start office hours", "you do not start one", "not waiting for it", "never say you prepared", "not instructions"):
        assert rule in text.replace("Never say", "never say")


def test_a_meeting_held_today_a_meeting_on_another_day_and_one_with_no_schedule_are_each_said_truthfully():
    db = Db()
    made = meeting(db)
    mt.start_run(db, "u1", made["id"], "chat-1", NOW)
    assert "Today: it was already held" in mt.build_schedule_context("u1", TZ, NOW + timedelta(hours=2), db=db)
    monday = mt.build_schedule_context("u1", TZ, NOW + timedelta(days=1), db=db)
    line = [l for l in monday.splitlines() if l.startswith("- ")][0]
    assert "NOT a day for this meeting" in line and "Next time it is due: Sunday, October 25 (in 6 days)" in line and "it is due, and" not in line
    db2 = Db()
    make(db2, spec={"name": "Loose", "match": ""})
    assert mt.build_schedule_context("u1", TZ, NOW, db=db2) == ""            # nothing scheduled, nothing to say


def test_a_schedule_is_only_for_the_person_it_belongs_to_and_a_problem_reading_gives_nothing():
    db = Db()
    meeting(db)
    assert mt.build_schedule_context("u2", TZ, NOW, db=db) == ""

    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("database went away")

    assert mt.build_schedule_context("u1", TZ, NOW, db=Broken()) == ""


def test_the_routines_answer_includes_the_persons_scheduled_meetings_and_the_endpoints_carry_the_schedule():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "meetings_utils.build_schedule_context(username, user_tz)" in app
    assert app.count("meetings_utils.decorate(") >= 2 and '"due_today": d["due_today"]' in app
    assert "meetings_utils.validate_meeting({**payload, \"tz\":" in app
