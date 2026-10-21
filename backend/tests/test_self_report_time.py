"""Self-report: how Sonic spends its time (the week's split by kind of work) and what it handled in the last day (backend/utils/self_report.py)."""
from datetime import datetime, timezone

from backend.utils import self_report as sr

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def mix(turns=400, **routes):
    base = {"kb_strict": 0, "kb_open": 0, "web": 0, "tool_output": 0, "conversational": 0}
    return {"at": NOW, "mix": {"turns": turns, "routes": {**base, **routes}}}


def test_questions_about_where_its_time_goes_are_recognised():
    for text in ("what do you spend your time on?", "What do you mostly spend your time doing", "what do people mostly ask you for?", "what kind of work do you mostly do?", "what takes up most of your time"):
        assert sr.kind_of(text) == "time", text
    assert sr.kind_of("how was your day") == "day"
    assert sr.kind_of("what are you doing this weekend?") is None


def test_the_split_is_said_in_rounded_shares_largest_first():
    built = sr.time_facts(mix(conversational=170, web=120, kb_strict=40, kb_open=22, tool_output=48))
    assert built["shares"] == [40, 30, 15, 10]                       # 42.5 -> 40, 30, 15.5 -> 15, 12 -> 10
    assert built["facts"][0].startswith("In the past week, about 40% of the chat turns I handled were talking things through")
    assert any(f.startswith("On my own, on a schedule, I run:") for f in built["facts"])


def test_a_thin_week_gives_no_split_at_all():
    built = sr.time_facts(mix(turns=60, conversational=40, web=20))
    assert built["shares"] == [] and "not enough chat turns" in built["facts"][0]
    assert "%" not in "".join(built["facts"])


def test_an_unreadable_source_says_so_and_gives_nothing_to_guess_from():
    block = sr.build_block({"mix": None}, "time")
    assert "cannot see how your week was split" in block and "Do not guess" in block


def test_the_block_forbids_feelings_about_the_work_and_invented_shares():
    block = sr.build_block(mix(conversational=300, web=100), "time")
    assert "HOW YOU SPEND YOUR TIME" in block and "never say you enjoy" in block and "never add a share" in block


def test_a_reply_may_only_use_the_listed_shares():
    snap = mix(conversational=300, web=100)
    assert sr.time_reply_issue("About 75% of it is just talking, and 25% is web lookups.", snap) is None
    assert sr.time_reply_issue("About 60% is talking things through.", snap) is not None
    assert sr.time_reply_issue("Mostly just talking things through.", snap) is None
    assert sr.time_reply_issue("About 40% is chatting.", mix(turns=20, conversational=20)) is not None            # no split on record, so no percentage
    assert sr.reply_issue("About 60% is talking things through.", snap, kind="time") is not None


def test_what_it_handled_in_the_last_day_is_added_to_the_routines_answer_without_the_status_noise():
    snap = {"at": NOW, "mode": {"known": True, "low_power": False, "paused": []}, "chats": {"turns": 43, "failed": {"quota": 0, "overloaded": 0, "other": 0}},
            "routines": [], "tasks": {"runs": 9, "failed": 1, "people": 6}}
    block = sr.activity_block(snap)
    assert "about 40 chat turns" in block and "9 scheduled task" in block and "running at full power" not in block
    assert sr.activity_block(None) == ""
    assert sr.activity_block({"at": NOW, "mode": {"known": False}, "chats": None, "routines": None, "tasks": None}) == ""


def test_the_chat_endpoint_adds_the_activity_to_the_routines_answer():
    source = open("app.py", encoding="utf-8").read()
    assert "self_report_utils.activity_block(" in source
