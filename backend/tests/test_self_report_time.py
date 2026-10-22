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


# ---- first person: Sonic is the program that runs its jobs, so it says "I", never "the server" or "the system" (Jack, 2026-10-22) ------------------------------------------------

def _without_instruction(block):
    return block.replace(sr.FIRST_PERSON, "").replace('Say "I" for what you track and do, never "the system" or "the server".', "")


def test_every_block_about_its_own_work_tells_it_to_say_i_and_names_no_server_or_system_itself():
    from datetime import datetime, timezone

    snap = {"at": NOW, "mode": {"known": True, "low_power": False, "paused": []}, "chats": {"turns": 43, "failed": {"quota": 0, "overloaded": 0, "other": 0}}, "routines": [],
            "tasks": {"runs": 9, "failed": 0, "people": 6}, "goals": None, "mix": {"turns": 400, "routes": {"conversational": 300, "web": 100}}}
    for kind in ("status", "day", "time"):
        block = sr.build_block(snap, kind)
        assert sr.FIRST_PERSON in block, kind
        assert "the server" not in _without_instruction(block).lower() and "the system" not in _without_instruction(block).lower(), kind
    activity = sr.activity_block(snap)
    assert sr.FIRST_PERSON in activity and "the server" not in _without_instruction(activity).lower()


def test_the_goals_block_says_i_for_what_it_tracks_and_does():
    from backend.utils import goals as goals_utils

    view = {"id": "coding", "title": "t", "statement": "s", "signal": "x", "lever": None, "guardrail": None, "measure_only": True, "state": "not_enough_data",
            "numbers": {"taken": 0, "passed": 0, "sample": 0, "rate": None, "previous_sample": 0, "previous_rate": None, "made": 0, "held_back": 0, "people": {"measured": 0, "stretched": 0, "any": 0}, "details": [], "said": []},
            "target_rate": 0.8, "min_sample": 20, "rate_label": "r", "sample_label": "s", "rate_phrase": "p", "people_gated": False, "approval": {"approved": False, "by": None, "at": None, "stale": False},
            "guardrail_tripped": False, "lever_applies": False, "revision": 1}
    block = sr.build_block({"goals": [view]}, "goals")
    assert 'Say "I" for what you track and do, never "the system" or "the server"' in block


def test_the_routines_answer_the_reading_note_and_the_profile_say_i_too():
    from backend.components.sonic_profile import SELF_TRUTHS
    from backend.utils import routines as rt

    class Db:
        def __getitem__(self, name):
            class Coll:
                def find(self, *a, **k):
                    class Cur:
                        def sort(self, *a): return self
                        def limit(self, *a): return self
                        def __iter__(self): return iter([])
                    return Cur()
            return Coll()

    block = rt.build_routines_context(Db(), "America/Chicago", is_admin=False, now=NOW)
    assert 'say \\"I\\"' in block.replace('say "I"', 'say \\"I\\"') and 'never "the server" or "the system"' in block
    assert "The server really does" not in block and "you read a web page" in SELF_TRUTHS and "the server reads" not in SELF_TRUTHS
