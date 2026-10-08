import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import self_counters as sc

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def test_the_key_list_is_closed_and_contains_no_room_for_anything_about_a_person():
    assert "turns_total" in sc.KEYS and "revised_identity" in sc.KEYS and "route_conversational" in sc.KEYS
    for key in sc.KEYS:
        assert key.replace("_", "").isalpha(), key
        for forbidden in ("user", "session", "text", "message", "name", "email"):
            assert forbidden not in key


def test_a_turn_starts_as_one_turn():
    assert sc.new_turn_counts() == {"turns_total": 1}


def test_tally_counts_plain_and_grouped_keys():
    counts = sc.new_turn_counts()
    sc.tally(counts, "reply_fit_checked")
    sc.tally(counts, "route", "conversational")
    sc.tally(counts, "revised", "identity")
    sc.tally(counts, "revised", "identity")
    assert counts == {"turns_total": 1, "reply_fit_checked": 1, "route_conversational": 1, "revised_identity": 2}


def test_unknown_keys_and_values_never_get_recorded_except_as_other_for_the_open_ended_groups():
    counts = {}
    sc.tally(counts, "something_else")
    sc.tally(counts, "route", "a made up route")
    sc.tally(counts, "revised", "mystery")
    sc.tally(counts, "nope", "x")
    assert counts == {}
    sc.tally(counts, "reward_failed", "a model-written tag with free text in it")
    sc.tally(counts, "reward_failed", None)
    sc.tally(counts, "turn_failed", "surprise")
    assert counts == {"reward_failed_other": 2, "turn_failed_other": 1}


@pytest.mark.parametrize("tag,kind", [
    ("safety", "safety"), ("identity", "identity"), ("closing_engagement", "closing"),
    ("venting_advice", "emotional"), ("celebration_no_question", "emotional"), (None, "emotional"),
])
def test_revision_kind_collapses_the_emotional_tags(tag, kind):
    assert sc.revision_kind(tag) == kind


class _Col:
    def __init__(self):
        self.calls, self.indexes = [], []

    def update_one(self, flt, update, upsert=False):
        self.calls.append((flt, update, upsert))

    def create_index(self, *a, **k):
        self.indexes.append((a, k))


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == sc.COUNTERS_COLLECTION
        return self.col


@pytest.fixture(autouse=True)
def _reset_index(monkeypatch):
    monkeypatch.setattr(sc, "_index_ready", False)


def test_one_upsert_per_turn_into_the_days_document_with_only_counts(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    sc.record_turn_counts({"turns_total": 1, "route_kb_strict": 1, "reply_fit_checked": 1}, NOW)
    assert len(db.col.calls) == 1
    flt, update, upsert = db.col.calls[0]
    assert flt == {"day": "2026-10-14"} and upsert is True
    assert update["$inc"] == {"counts.turns_total": 1, "counts.route_kb_strict": 1, "counts.reply_fit_checked": 1}
    assert set(update) == {"$inc", "$setOnInsert"} and set(update["$setOnInsert"]) == {"day_dt"}


def test_only_fixed_keys_can_reach_the_database_so_no_user_or_text_field_is_possible(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    sc.record_turn_counts({"turns_total": 1, "username": 3, "notes": "my private message", "revised_nope": 5}, NOW)
    update = db.col.calls[0][1]
    assert update["$inc"] == {"counts.turns_total": 1}
    assert "private" not in str(db.col.calls) and "username" not in str(db.col.calls)


def test_nothing_is_written_for_an_empty_tally_or_with_no_database(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    sc.record_turn_counts({}, NOW)
    sc.record_turn_counts({"unknown": 4}, NOW)
    assert db.col.calls == []
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    sc.record_turn_counts({"turns_total": 1}, NOW)  # must not raise


def test_the_indexes_are_created_once_a_ttl_and_a_unique_day(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    sc.record_turn_counts({"turns_total": 1}, NOW)
    sc.record_turn_counts({"turns_total": 1}, NOW)
    assert len(db.col.indexes) == 2
    assert any(k.get("expireAfterSeconds") == sc.RETENTION_SECONDS for _, k in db.col.indexes)
    assert any(k.get("unique") for _, k in db.col.indexes)


def test_counting_can_never_break_a_reply(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    sc.record_turn_counts({"turns_total": 1}, NOW)  # must not raise
    sc.record_turn_counts({"turns_total": "not a number"}, NOW)  # must not raise


def day(days_ago, **counts):
    return {"day": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d"), "counts": counts}


def test_summary_compares_this_period_with_the_one_before_and_reports_the_change():
    docs = [
        day(2, turns_total=50, reply_fit_checked=40, revised_identity=4, reward_evaluated=10, reward_failed_hallucination=1),
        day(9, turns_total=50, reply_fit_checked=50, revised_identity=1, reward_evaluated=10),
    ]
    s = sc.summarize_counters(docs, NOW, days=7)
    assert s["totals"]["turns_total"] == 50 and s["previous_totals"]["turns_total"] == 50
    assert s["rates"]["revised_identity"] == 0.1 and s["previous_rates"]["revised_identity"] == 0.02
    assert s["change"]["revised_identity"] == 0.08
    assert s["rates"]["reward_failed"] == 0.1 and s["previous_rates"]["reward_failed"] == 0.0


def test_days_outside_both_windows_and_unknown_keys_are_ignored():
    docs = [day(60, turns_total=999), day(1, turns_total=10, username=5, route_web=2), {"day": None, "counts": {"turns_total": 5}}]
    s = sc.summarize_counters(docs, NOW, days=7)
    assert s["totals"] == {"turns_total": 10, "route_web": 2}
    assert s["rates"]["route_web"] == 0.2


def test_an_empty_history_gives_no_rates_not_zero_division():
    s = sc.summarize_counters([], NOW)
    assert all(v is None for v in s["rates"].values()) and all(v is None for v in s["change"].values())
    assert "n/a" in sc.format_summary(s)


def test_the_self_reference_counters_are_in_the_closed_list_and_in_the_report():
    assert "relatable_offered" in sc.KEYS and "self_mention" in sc.KEYS
    counts = {}
    sc.tally(counts, "relatable_offered")
    sc.tally(counts, "self_mention")
    assert counts == {"relatable_offered": 1, "self_mention": 1}
    s = sc.summarize_counters([day(1, turns_total=50, relatable_offered=2, self_mention=5)], NOW)
    assert s["rates"]["relatable_offered"] == 0.04 and s["rates"]["self_mention"] == 0.1
    assert "relatable line was offered: 4.0%" in sc.format_summary(s)


def test_the_text_report_shows_rates_trend_and_routes():
    docs = [day(1, turns_total=100, reply_fit_checked=80, revised_safety=4, route_conversational=80, route_kb_strict=20),
            day(8, turns_total=100, reply_fit_checked=80, revised_safety=2)]
    text = sc.format_summary(sc.summarize_counters(docs, NOW, days=7))
    assert "safety" in text and "5.0%" in text and "2.5%" in text and "+2.5 pts" in text
    assert "conversational 80.0%" in text and "kb_strict 20.0%" in text


class _FindCol:
    def find(self, query, projection):
        self.query, self.projection = query, projection
        return [{"day": "2026-10-13", "counts": {"turns_total": 3}}]


def test_fetch_asks_for_two_periods_and_only_the_fields_it_needs():
    col = _FindCol()
    docs = sc.fetch_counter_docs({sc.COUNTERS_COLLECTION: col}, 7, NOW)
    assert docs and col.query == {"day": {"$gte": "2026-09-30"}}
    assert col.projection == {"_id": 0, "day": 1, "counts": 1}


def test_the_chat_stream_tallies_each_signal_and_flushes_on_both_exits():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "turn_counts = new_turn_counts()",
        'tally(turn_counts, "route", source_type)',
        'tally(turn_counts, "reply_fit_checked")',
        'tally(turn_counts, "revised", revision_kind(revision_tag))',
        'tally(turn_counts, "reward_evaluated")',
        'tally(turn_counts, "reward_failed", verdict.get("tag"))',
        'tally(turn_counts, "turn_failed", failed.kind)',
    ):
        assert needle in source, needle
    assert source.count("await asyncio.to_thread(record_turn_counts, turn_counts)") == 2
