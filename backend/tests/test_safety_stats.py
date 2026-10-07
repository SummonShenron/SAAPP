from datetime import datetime, timedelta, timezone

from backend.utils import safety_stats as ss

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def ev(kind=None, minutes_ago=10, username="jack", **extra):
    doc = {"username": username, "level": extra.pop("level", "acute"), "source": extra.pop("source", "language"),
           "at": NOW - timedelta(minutes=minutes_ago), **extra}
    if kind:
        doc["kind"] = kind
    return doc


def test_an_empty_window_reports_zeros_and_no_rates():
    s = ss.summarize_safety_events([], NOW)
    assert s["risk_raised"] == 0 and s["risk_turns"] == 0
    assert s["revised_rate"] is None and s["last_resort_rate"] is None and s["line_rate"] is None


def test_an_older_record_with_no_kind_counts_as_a_risk_raised_event():
    s = ss.summarize_safety_events([ev(), ev(level="elevated", source="reasoner")], NOW)
    assert s["risk_raised"] == 2
    assert s["by_level"] == {"acute": 1, "elevated": 1}
    assert s["by_detector"] == {"language": 1, "reasoner": 1}


def test_people_are_counted_never_named():
    s = ss.summarize_safety_events([ev(username="alice"), ev(username="alice"), ev(username="bob")], NOW)
    assert s["people_with_risk_raised"] == 2
    assert "alice" not in str(s) and "bob" not in str(s)


def test_events_outside_the_window_are_ignored():
    old = ev(minutes_ago=60 * 24 * 9)
    future = ev(minutes_ago=-30)
    s = ss.summarize_safety_events([old, future, ev()], NOW, days=7)
    assert s["risk_raised"] == 1


def test_rates_use_risk_turns_as_the_denominator():
    events = [ev("risk_turn")] * 4 + [ev("reply_revised"), ev("line_in_reply"), ev("line_in_reply"), ev("last_resort_line")]
    s = ss.summarize_safety_events(events, NOW)
    assert s["risk_turns"] == 4
    assert s["revised_rate"] == 0.25 and s["line_rate"] == 0.5 and s["last_resort_rate"] == 0.25


def test_ladder_answers_are_counted_per_rung_and_status_and_unknown_rungs_are_dropped():
    events = [
        ev("ladder_answer", rung="friends", status="unavailable"), ev("ladder_answer", rung="friends", status="available"),
        ev("ladder_answer", rung="immediate_family", status="unavailable"), ev("ladder_answer", rung="bogus", status="available"),
    ]
    s = ss.summarize_safety_events(events, NOW)
    assert s["ladder"]["friends"] == {"unavailable": 1, "available": 1}
    assert s["ladder"]["immediate_family"] == {"unavailable": 1}
    assert "bogus" not in s["ladder"]


def test_outage_fallbacks_and_per_day_counts():
    events = [ev("outage_fallback"), ev(minutes_ago=5), ev(minutes_ago=60 * 26)]
    s = ss.summarize_safety_events(events, NOW)
    assert s["outage_fallbacks"] == 1
    assert s["per_day"] == {"2026-10-06": 1, "2026-10-07": 1}


def test_naive_timestamps_are_read_as_utc():
    naive = {"username": "jack", "level": "acute", "source": "language", "at": (NOW - timedelta(minutes=5)).replace(tzinfo=None)}
    assert ss.summarize_safety_events([naive], NOW)["risk_raised"] == 1


def test_the_text_report_shows_the_warning_line_and_never_raises_on_an_empty_summary():
    text = ss.format_summary(ss.summarize_safety_events([], NOW))
    assert "should stay near 0" in text and "n/a" in text
    full = ss.format_summary(ss.summarize_safety_events([ev(), ev("risk_turn"), ev("last_resort_line")], NOW))
    assert "100.0%" in full


class _Cursor(list):
    def limit(self, n):
        self.limited = n
        return self


class _Col:
    def __init__(self):
        self.query = None

    def find(self, query, projection):
        self.query, self.projection = query, projection
        return _Cursor([ev()])


def test_fetch_asks_only_for_the_window_with_no_database_id_and_a_cap():
    col = _Col()
    result = ss.fetch_recent_events({ss.SAFETY_EVENTS_COLLECTION: col}, 7, NOW)
    assert len(result) == 1
    assert col.query == {"at": {"$gte": NOW - timedelta(days=7)}}
    assert col.projection == {"_id": 0}
