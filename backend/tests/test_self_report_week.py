"""'How's your week been?' answered from Sonic's own recent record (backend/utils/self_report.py: kind "week", week_block, _record). No model; the record is read from the shared FakeDB."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import self_report as sr

NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("message", ["How have you been lately?", "how have you been doing this week?",
                                     "how's work been going for you"])
def test_questions_about_its_week_are_the_week_kind(message):
    assert sr.kind_of(message) == "week"


@pytest.mark.parametrize("message, kind", [("how are you?", None), ("hey, how's it going?", None), ("how was your day", "day"), ("any downtime?", "status"),
                                           ("show me how your weekly report works", None), ("how was my week, can you summarise it?", None), ("what's new with you?", None)])
def test_a_greeting_and_other_questions_are_not_the_week_kind(message, kind):
    assert sr.kind_of(message) == kind


def test_the_week_block_says_what_happened_and_never_invents():
    block = sr.week_block({"record": [{"on": "2026-11-19", "what": "On 2026-11-19, it had 10 setbacks in its coding work and 6 edits that landed."}]})
    assert "On 2026-11-19, it had 10 setbacks" in block and "in the first person" in block and "not a feeling" in block and "Never add a day, a number" in block
    assert "plainly" in sr.week_block({"record": []}) and "Never invent a day or a number" in sr.week_block({"record": []})
    assert "cannot see your own record" in sr.week_block({"record": None}) and "cannot see your own record" in sr.week_block(None)
    assert sr.build_block({"record": []}, "week") == sr.week_block({"record": []})


def test_the_record_reads_recent_approved_pattern_notes_newest_first():
    db = FakeDB()
    day = lambda n: (NOW - timedelta(days=n)).date().isoformat()
    rows = [
        {"_id": 1, "signal": "pattern", "status": "approved", "on": day(2), "what": "two days ago"},
        {"_id": 2, "signal": "pattern", "status": "approved", "on": day(1), "what": "yesterday"},
        {"_id": 3, "signal": "pattern", "status": "approved", "on": day(sr.RECORD_DAYS + 3), "what": "too old"},
        {"_id": 4, "signal": "pattern", "status": "pending", "on": day(1), "what": "not approved"},
        {"_id": 5, "signal": "answer_check", "status": "approved", "on": day(1), "what": "not a pattern note"},
    ]
    for row in rows:
        db["sonic_experiences"].insert_one(row)
    assert [r["what"] for r in sr._record(db, NOW)] == ["yesterday", "two days ago"]
    for i in range(sr.RECORD_MAX + 3):
        db["sonic_experiences"].insert_one({"_id": 100 + i, "signal": "pattern", "status": "approved", "on": day(3), "what": f"more {i}"})
    assert len(sr._record(db, NOW)) == sr.RECORD_MAX


def test_the_day_answer_may_add_its_record_and_says_nothing_extra_without_one():
    snap = {"record": [{"on": "2026-11-19", "what": "On 2026-11-19, it had 10 setbacks."}]}
    assert "YOUR OWN RECENT RECORD" in sr.record_addition(snap) and "On 2026-11-19, it had 10 setbacks." in sr.record_addition(snap)
    assert sr.record_addition({"record": []}) == "" and sr.record_addition(None) == ""
    assert sr.build_block(snap, "day").endswith(sr.record_addition(snap))
    assert sr.kind_of("how's your week been?") == "day"                     # the day answer keeps the phrasings it had; the record rides along with it


def test_the_status_check_does_not_judge_a_week_answer():
    unreadable = None
    assert sr.reply_issue("Everything is running fine.", unreadable, kind="status") is not None
    assert sr.reply_issue("Everything is running fine.", unreadable, kind="week") is None


def test_the_snapshot_carries_the_record():
    db = FakeDB()
    db["sonic_experiences"].insert_one({"_id": 1, "signal": "pattern", "status": "approved", "on": NOW.date().isoformat(), "what": "today"})
    snap = sr.snapshot(db, NOW, use_cache=False)
    assert snap["record"] == [{"on": NOW.date().isoformat(), "what": "today"}]
