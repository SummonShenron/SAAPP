"""While a meeting (Office Hours) runs in a conversation, the whole meeting stays word for word and no fold runs until it passes MEETING_MAX_MESSAGES.

Why (2026-11-08): a 60-message meeting went back to its first agenda item and re-asked items it had covered, because a fold keeps only the last few messages plus a short summary with no agenda progress, and the window had
already passed the meeting's opening. Yesterday's 22-message meeting never lost its place. These tests fix the numbers, so a deployment's own CONVERSATION_FOLD_AT_MESSAGES does not change them."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import conversation_summary as cs
from backend.utils import meetings
from backend.utils.time_utils import SENT_AT_KEY

T0 = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_numbers(monkeypatch):
    monkeypatch.setattr(cs, "FOLD_AT_MESSAGES", 20)
    monkeypatch.setattr(cs, "FOLD_AT_CHARS", 12000)
    monkeypatch.setattr(cs, "KEEP_TAIL_MESSAGES", 6)
    monkeypatch.setattr(cs, "HARD_MAX_MESSAGES", 22)
    monkeypatch.setattr(cs, "MEETING_MAX_MESSAGES", 80)


def chat(total, first_minute=0):
    """`total` alternating messages, one a minute from T0 + first_minute, each stamped the way the app stamps them."""
    out = []
    for i in range(total):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"message {i}", additional_kwargs={SENT_AT_KEY: (T0 + timedelta(minutes=first_minute + i)).isoformat()}))
    return out


def test_the_meeting_starts_at_the_first_message_sent_at_or_after_the_run_began():
    messages = chat(30)
    assert cs.meeting_start(messages, T0 + timedelta(minutes=10)) == 10
    assert cs.meeting_start(messages, T0 + timedelta(minutes=10, seconds=1)) == 11
    assert cs.meeting_start(messages, T0 + timedelta(hours=5)) is None          # no message that late
    assert cs.meeting_start(messages, None) is None                              # no meeting
    assert cs.meeting_count(messages, T0 + timedelta(minutes=10)) == 20 and cs.meeting_count(messages, None) == 0
    unstamped = [HumanMessage(content="old"), *chat(3)]
    assert cs.meeting_start(unstamped, T0) == 1                                  # a message saved before stamping is never guessed to be in the meeting


def test_without_a_meeting_the_window_is_what_it_was():
    messages = chat(60)
    assert len(cs.history_window(messages, None, active=True)) == 22
    assert len(cs.history_window(messages, None, active=True, meeting_since=None)) == 22
    assert len(cs.history_window(messages, None, active=False)) == cs.PLAIN_WINDOW


def test_a_running_meeting_keeps_its_first_message_in_the_window_however_long_it_gets():
    messages = chat(100)
    since = T0 + timedelta(minutes=60)                                           # the meeting began 40 messages ago
    window = cs.history_window(messages, None, active=True, meeting_since=since)
    assert len(window) == 40 and window[0].content == "message 60"               # not the last 22
    just_started = cs.history_window(messages, None, active=True, meeting_since=T0 + timedelta(minutes=95))
    assert len(just_started) == 22 and just_started[0].content == "message 78"   # a meeting that began inside the ordinary window changes nothing


def test_a_meeting_window_never_exceeds_the_cap_and_never_starts_after_the_normal_one():
    messages = chat(200)
    assert len(cs.history_window(messages, None, active=True, meeting_since=T0 + timedelta(minutes=10))) == 80      # a 190-message meeting is capped
    summary = {"folded_upto": 150, "anchor": cs.anchor_of(messages[149]), "items": []}
    short = cs.history_window(messages, summary, active=True, meeting_since=T0 + timedelta(minutes=195))
    assert len(short) == 22 and short[0].content == "message 178"                # the meeting began inside the ordinary window: the ordinary window
    early = cs.history_window(messages, summary, active=True, meeting_since=T0 + timedelta(minutes=120))
    assert early[0].content == "message 120"                                     # a meeting that began BEFORE the fold point is read from its own start


def test_the_fold_waits_while_the_meeting_is_under_the_cap_and_resumes_after():
    messages = chat(60)
    since = T0 + timedelta(minutes=20)                                           # 40 messages in the meeting, 60 unfolded: a fold is normally due
    assert cs.due(None, messages) is True
    assert cs.due(None, messages, meeting_since=since) is False                  # held
    assert cs.due(None, messages, force=True, meeting_since=since) is True       # the person's own request still folds
    long_meeting = chat(100)
    assert cs.due(None, long_meeting, meeting_since=T0) is True                  # 100 messages in the meeting: past the cap, the fold runs again
    assert cs.due(None, messages, meeting_since=T0 + timedelta(hours=5)) is True # no message that late: not a meeting, the normal rule applies


def test_prepare_turn_passes_the_meeting_through(monkeypatch):
    monkeypatch.setattr(cs, "load_cached", lambda *a, **k: None)
    messages = chat(70)
    plain = cs.prepare_turn("jack", "s1", messages)
    held = cs.prepare_turn("jack", "s1", messages, meeting_since=T0 + timedelta(minutes=30))
    assert plain["history_max"] == 22 and held["history_max"] == 40 and held["window"][0].content == "message 30"


def test_the_run_start_is_read_from_the_run_and_the_app_hands_it_to_every_place_that_decides_a_window():
    assert meetings.run_started_at(None) is None
    assert meetings.run_started_at({"started_at": "2026-11-08T21:23:09.023979+00:00"}) == datetime(2026, 11, 8, 21, 23, 9, 23979, tzinfo=timezone.utc)
    source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "prepare_turn, username, session_id, chat_sessions[history_key]," in source and "meeting_since=meeting_since" in source
    assert source.count("meeting_since=meeting_since") >= 3                       # prepare_turn, due and capture
    assert 'due(convo_turn["summary"], chat_sessions[history_key], meeting_since=meeting_since)' in source
