from datetime import datetime, timezone

import pytest

from backend.utils import llm_turns
from backend.utils import llm_usage as lu
from backend.utils import user_usage as uu

NOW = datetime(2026, 10, 10, 15, 30, tzinfo=timezone.utc)


class _Col:
    def __init__(self):
        self.rows, self.fail = [], False

    def update_one(self, flt, update, upsert=False):
        if self.fail:
            raise OSError("db down")
        row = next((r for r in self.rows if all(r.get(k) == v for k, v in flt.items())), None)
        if row is None:
            row = dict(flt)
            self.rows.append(row)
        for key, by in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + by

    def find(self, flt, projection=None):
        floor = flt["hour"]["$gte"]
        return [dict(r) for r in self.rows if r["user"] == flt["user"] and r["hour"] >= floor]


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == uu.COLLECTION
        return self.col


@pytest.fixture(autouse=True)
def _clean():
    uu.reset()
    llm_turns.reset()
    yield
    uu.reset()
    llm_turns.reset()


def test_a_turn_is_counted_for_the_person_by_the_hour_it_ended_in():
    uu.add("jack", 900, 3, now=NOW)
    uu.add("jack", 100, 1, now=NOW)
    assert uu.snapshot() == {("jack", "2026-10-10T15"): {"tokens": 1_000, "calls": 4, "turns": 2}}


def test_nobody_and_nothing_are_not_counted():
    uu.add(None, 100, 1, now=NOW)
    uu.add("  ", 100, 1, now=NOW)
    uu.add("jack", 0, 0, now=NOW)
    uu.add("jack", "garbage", 0, now=NOW)
    assert uu.snapshot() == {}


def test_what_is_saved_is_only_counts_keyed_by_person_and_hour():
    db = _DB()
    uu.add("jack", 900, 3, now=NOW)
    assert uu.flush(db) == 1 and uu.snapshot() == {}
    uu.add("jack", 100, 1, now=NOW)
    uu.flush(db)
    assert db.col.rows == [{"user": "jack", "hour": "2026-10-10T15", "tokens": 1_000, "calls": 4, "turns": 2}]
    assert uu.flush(db) == 0  # nothing new, nothing written


def test_a_failed_write_keeps_the_counts_for_the_next_flush():
    db = _DB()
    db.col.fail = True
    uu.add("jack", 900, 3, now=NOW)
    assert uu.flush(db) == 0
    assert uu.snapshot()[("jack", "2026-10-10T15")]["tokens"] == 900
    db.col.fail = False
    assert uu.flush(db) == 1 and db.col.rows[0]["tokens"] == 900


def test_with_no_database_nothing_is_lost_or_raised(monkeypatch):
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    uu.add("jack", 900, 3, now=NOW)
    assert uu.flush() == 0 and uu.snapshot()[("jack", "2026-10-10T15")]["tokens"] == 900


def test_a_person_reads_back_only_their_own_recent_rows_plus_what_is_not_saved_yet():
    db = _DB()
    uu.add("jack", 500, 1, now=NOW)
    uu.add("sam", 700, 1, now=NOW)
    uu.flush(db)
    uu.add("jack", 40, 1, now=NOW)
    rows = uu.fetch(db, "jack", "2026-10-10T15") + uu.unsaved_rows("jack")
    assert uu.tokens_since(rows, "2026-10-10T15") == 540
    assert uu.tokens_since(rows, "2026-10-10T16") == 0  # later than anything they did
    assert uu.fetch(db, "jack", "2026-10-10T16") == []


def test_a_finished_turn_adds_its_tokens_to_the_person_it_was_for():
    turn = llm_turns.begin("jack")
    llm_turns.note_call({"input": 800, "output": 200})
    llm_turns.note_call({"input": 50, "output": 10})
    llm_turns.finish(turn)
    snap = uu.snapshot()
    assert sum(slot["tokens"] for slot in snap.values()) == 1_060 and sum(slot["calls"] for slot in snap.values()) == 2


def test_a_turn_the_budget_blocked_or_with_no_person_adds_nothing():
    blocked = llm_turns.begin("jack")
    llm_turns.finish(blocked, "blocked")
    anonymous = llm_turns.begin()
    llm_turns.note_call({"input": 100, "output": 10})
    llm_turns.finish(anonymous)
    assert uu.snapshot() == {}


def test_a_turn_finished_twice_counts_once():
    turn = llm_turns.begin("jack")
    llm_turns.note_call({"input": 100, "output": 10})
    llm_turns.finish(turn)
    llm_turns.finish(turn)  # the safety net at the end of the chat endpoint runs it again
    assert sum(slot["tokens"] for slot in uu.snapshot().values()) == 110


def test_the_usage_flush_saves_the_per_person_counts_too(monkeypatch):
    db = _DB()
    monkeypatch.setattr(lu, "_flush_usage", lambda database=None: 0)
    monkeypatch.setattr(llm_turns, "flush", lambda database=None: 0)
    uu.add("jack", 300, 1, now=NOW)
    lu.flush(db)
    assert db.col.rows[0]["tokens"] == 300
