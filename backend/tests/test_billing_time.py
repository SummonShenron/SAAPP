from datetime import datetime, timezone

import pytest

from backend.utils import billing_time as bt
from backend.utils import llm_turns, llm_usage


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def default_zone(monkeypatch):
    monkeypatch.delenv("LLM_BILLING_TIMEZONE", raising=False)


def test_the_day_is_googles_pacific_day_not_the_utc_one():
    assert bt.day(utc(2026, 10, 12, 0, 5)) == "2026-10-11"   # 17:05 PDT on the 11th
    assert bt.day(utc(2026, 10, 12, 6, 59)) == "2026-10-11"  # one minute before Pacific midnight
    assert bt.day(utc(2026, 10, 12, 7, 0)) == "2026-10-12"   # Pacific midnight (PDT is UTC-7)


def test_winter_time_is_eight_hours_behind_not_seven():
    assert bt.day(utc(2026, 12, 20, 7, 59)) == "2026-12-19"
    assert bt.day(utc(2026, 12, 20, 8, 0)) == "2026-12-20"


def test_the_month_turns_over_at_pacific_midnight_on_the_first():
    assert bt.month(utc(2026, 11, 1, 3, 0)) == "2026-10"  # still October in Pacific time
    assert bt.month(utc(2026, 11, 1, 6, 59)) == "2026-10"  # PDT until 02:00 on Nov 1, so Pacific midnight is 07:00 UTC
    assert bt.month(utc(2026, 11, 1, 7, 0)) == "2026-11"
    assert bt.month_start(utc(2026, 10, 20, 12)) == "2026-10-01"


def test_the_next_resets_are_pacific_midnights_with_the_right_offset():
    assert bt.next_day_start(utc(2026, 10, 10, 15)).isoformat() == "2026-10-11T00:00:00-07:00"
    assert bt.next_month_start(utc(2026, 10, 10, 15)).isoformat() == "2026-11-01T00:00:00-07:00"
    assert bt.next_month_start(utc(2026, 12, 20, 9)).isoformat() == "2027-01-01T00:00:00-08:00"
    assert bt.next_month_start(utc(2026, 12, 31, 20)).isoformat() == "2027-01-01T00:00:00-08:00"


def test_a_period_of_n_days_starts_n_minus_one_days_before_today():
    assert bt.days_back(utc(2026, 10, 12, 0, 5), 1) == "2026-10-11"
    assert bt.days_back(utc(2026, 10, 12, 0, 5), 7) == "2026-10-05"
    assert bt.days_back(utc(2026, 10, 12, 0, 5), 0) == "2026-10-11"  # never before today


def test_a_datetime_with_no_zone_is_taken_to_be_utc():
    assert bt.day(datetime(2026, 10, 12, 0, 5)) == "2026-10-11"


def test_the_zone_can_be_overridden_and_a_typo_falls_back_to_utc_instead_of_raising(monkeypatch):
    monkeypatch.setenv("LLM_BILLING_TIMEZONE", "UTC")
    assert bt.day(utc(2026, 10, 12, 0, 5)) == "2026-10-12" and bt.label() == "UTC"
    monkeypatch.setenv("LLM_BILLING_TIMEZONE", "America/Chicago")
    assert bt.day(utc(2026, 10, 12, 4, 59)) == "2026-10-11"
    monkeypatch.setenv("LLM_BILLING_TIMEZONE", "Not/AZone")
    assert bt.day(utc(2026, 10, 12, 0, 5)) == "2026-10-12"  # UTC


def test_a_person_is_told_the_zone_in_words():
    assert bt.label() == "Pacific time"


def test_the_meter_and_the_turn_counts_use_the_same_billing_day(monkeypatch):
    llm_usage.reset()
    try:
        monkeypatch.setattr(bt, "day", lambda now=None: "2026-10-11")
        llm_usage.record("m", "x.y", {"input": 5, "output": 1})
        turn = llm_turns.begin()
        llm_turns.finish(turn)
        assert {key[0] for key in llm_usage.snapshot()} == {"2026-10-11"}
        assert set(llm_turns.snapshot()) == {"2026-10-11"}
    finally:
        llm_usage.reset()


def test_reading_back_starts_from_the_billing_day(monkeypatch):
    seen = []

    class Coll:
        def find(self, query, projection=None):
            seen.append(query["day"]["$gte"])
            return []

    class DB:
        def __getitem__(self, name):
            return Coll()

    moment = utc(2026, 10, 12, 0, 5)  # the 11th in Pacific time
    llm_usage.fetch_usage_docs(DB(), 7, moment)
    llm_usage.fetch_route_docs(DB(), 7, moment)
    llm_turns.fetch_turn_docs(DB(), 7, moment)
    assert seen == ["2026-10-05"] * 3
