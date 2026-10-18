"""A meeting the person forgot to end closes itself, with its notes (backend/services/meeting_sweep.py, backend/utils/meetings.py)."""
import asyncio
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.services import meeting_sweep as sweep
from backend.tests.test_meetings import NOW, SPEC, Db, Resp, make, message
from backend.utils import meetings as mt

ROOT = pathlib.Path(__file__).resolve().parents[2]
LATER = NOW + timedelta(hours=4)
ANSWER = '{"summary": "Applied to three roles.", "decisions": [], "action_items": ["Follow up Friday"]}'


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(mt, "_index_ready", False)


def said():
    return [message("human", "I applied to three roles", 1), message("ai", "Which ones?", 2)]


def sweep_once(db, now=LATER, allowed=lambda owner: True, messages=None, answer=ANSWER):
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        return Resp(answer)

    result = asyncio.run(sweep.close_stale(db, invoke, lambda user, session: messages if messages is not None else said(), allowed, now))
    return result, calls


def running(db, user="u1", at=NOW, session="chat-1"):
    meeting = make(db, user)
    run, _ = mt.start_run(db, user, meeting["id"], session, at)
    return meeting, run


def test_a_meeting_left_quiet_for_hours_closes_itself_and_keeps_its_notes_marked_as_automatic():
    db = Db()
    meeting, run = running(db)
    result, calls = sweep_once(db)
    assert result == {"closed": 1, "with_notes": 1, "deferred": 0} and len(calls) == 1
    assert mt.active_run(db, "u1", "chat-1") is None and mt.get_run(db, "u1", run["id"])["status"] == mt.ENDED and mt.get_run(db, "u1", run["id"])["automatic"] is True
    note = mt.notes_for_meeting(db, "u1", meeting["id"])[0]
    assert note["summary"] == "Applied to three roles." and mt.public_note(note)["automatic"] is True


def test_a_meeting_still_in_use_is_left_alone_and_a_turn_in_it_keeps_it_alive():
    db = Db()
    meeting, run = running(db)
    assert sweep_once(db, now=NOW + timedelta(hours=2))[0]["closed"] == 0                 # quiet for under three hours
    mt.touch_run(db, run["id"], NOW + timedelta(hours=3))                               # a turn in the meeting
    assert sweep_once(db, now=NOW + timedelta(hours=5))[0]["closed"] == 0               # two hours since that turn
    assert sweep_once(db, now=NOW + timedelta(hours=6, minutes=1))[0]["closed"] == 1


def test_a_chat_turn_in_the_meeting_is_what_keeps_it_alive():
    db = Db()
    meeting, run = running(db)
    mt.build_context_for_session("u1", "chat-1", db=db, now=NOW + timedelta(hours=2))
    assert mt.get_run(db, "u1", run["id"])["last_activity_at"] == (NOW + timedelta(hours=2)).isoformat()
    mt.build_context_for_session("u1", "other-chat", db=db, now=NOW + timedelta(hours=9))         # another conversation's turn does not
    assert mt.get_run(db, "u1", run["id"])["last_activity_at"] == (NOW + timedelta(hours=2)).isoformat()


def test_a_meeting_open_far_too_long_closes_even_if_it_is_still_being_used():
    db = Db()
    meeting, run = running(db)
    mt.touch_run(db, run["id"], NOW + timedelta(hours=12))
    assert [reason for _, reason in mt.stale_runs(db, NOW + timedelta(hours=12, minutes=5))] == ["too_long"]
    assert sweep_once(db, now=NOW + timedelta(hours=12, minutes=5))[0]["closed"] == 1


def test_a_meeting_where_nothing_was_said_closes_without_a_note():
    db = Db()
    meeting, run = running(db)
    result, calls = sweep_once(db, messages=[message("ai", "Shall we begin?", 1)])
    assert result["closed"] == 1 and result["with_notes"] == 0 and mt.notes_for_meeting(db, "u1", meeting["id"]) == []


def test_in_low_power_mode_the_closing_waits_and_gives_up_on_notes_only_after_a_day():
    db = Db()
    meeting, run = running(db)
    result, calls = sweep_once(db, allowed=lambda owner: False)
    assert result == {"closed": 0, "with_notes": 0, "deferred": 1} and calls == [] and mt.active_run(db, "u1", "chat-1") is not None
    result, calls = sweep_once(db, now=NOW + timedelta(hours=25), allowed=lambda owner: False)
    assert result["closed"] == 1 and result["with_notes"] == 0 and calls == [] and mt.notes_for_meeting(db, "u1", meeting["id"]) == []


def test_only_one_closer_writes_the_notes_even_if_the_person_pressed_end_at_the_same_moment():
    db = Db()
    meeting, run = running(db)
    assert mt.end_run(db, "u1", run["id"], NOW + timedelta(hours=3, minutes=59))        # the person got there first
    result, calls = sweep_once(db)
    assert result["closed"] == 0 and calls == []
    meeting2, run2 = running(db, user="u2")
    first, _ = sweep_once(db)
    second, calls2 = sweep_once(db)
    assert first["closed"] == 1 and second == {"closed": 0, "with_notes": 0, "deferred": 0} and calls2 == []


def test_only_the_runs_of_the_person_whose_meeting_it_is_are_closed_with_their_own_notes():
    db = Db()
    meeting, _ = running(db, user="u1", session="chat-1")
    other, _ = running(db, user="u2", session="chat-2", at=NOW + timedelta(hours=3, minutes=30))     # only half an hour old
    result, _ = sweep_once(db)
    assert result["closed"] == 1 and mt.active_run(db, "u2", "chat-2") is not None
    assert len(mt.notes_for_meeting(db, "u1", meeting["id"])) == 1 and mt.notes_for_meeting(db, "u2", other["id"]) == []


def test_a_failure_while_closing_one_meeting_never_stops_the_sweep(monkeypatch):
    db = Db()
    running(db, user="u1", session="chat-1")
    running(db, user="u2", session="chat-2")

    def load(user, session):
        if user == "u1":
            raise RuntimeError("database went away")
        return said()

    result = asyncio.run(sweep.close_stale(db, lambda p: Resp(ANSWER), load, lambda owner: True, LATER))
    assert result["closed"] == 2 and result["with_notes"] == 1


def test_it_is_started_with_the_app_and_logs_nothing_that_was_said():
    import ast
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "meeting_sweep.run_meeting_sweep(get_db, note_llm.invoke, load_session_messages, allowed)" in app and "meeting_sweep_task.cancel()" in app
    tree = ast.parse((ROOT / "backend" / "services" / "meeting_sweep.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1])
