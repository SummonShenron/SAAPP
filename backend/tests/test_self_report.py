"""Self-report: Sonic's answer to "how are things going / how was your day" comes from records, in fixed sentences, and never says all is fine when it is not."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import self_report as sr
from backend.utils import routines as rt
from backend.utils.identity_checks import identity_reply_issue

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)      # 2:00 PM in New York


@pytest.fixture(autouse=True)
def fresh_cache():
    sr.reset_cache()
    yield
    sr.reset_cache()


def routine_rows(health="ok", health_hours_ago=3, shelf_hours_ago=20, health_counts=None):
    rows = []
    for routine in rt.registry():
        if routine.id == "health_check":
            rows.append({"id": routine.id, "label": routine.label, "every_hours": routine.every_hours, "status": health,
                         "counts": health_counts or {}, "started_at": NOW - timedelta(hours=health_hours_ago)})
        elif routine.id == "reading_shelf":
            rows.append({"id": routine.id, "label": routine.label, "every_hours": routine.every_hours, "status": "ok",
                         "counts": {"items_added": 1}, "started_at": NOW - timedelta(hours=shelf_hours_ago)})
        else:
            rows.append({"id": routine.id, "label": routine.label, "every_hours": routine.every_hours, "status": "ok",
                         "counts": {"deleted": 2}, "started_at": NOW - timedelta(hours=9)})
    return rows


def snap(low_power=False, known=True, turns=48, failed=None, rows="default", tasks="default"):
    return {
        "at": NOW,
        "mode": {"known": known, "low_power": low_power, "paused": ["making pictures", "deep thinking"] if low_power else []},
        "chats": {"turns": turns, "failed": failed or {"quota": 0, "overloaded": 0, "other": 0}},
        "routines": routine_rows() if rows == "default" else rows,
        "tasks": {"runs": 9, "failed": 0, "people": 7} if tasks == "default" else tasks,
    }


def text(snapshot, kind="status", admin=False):
    return sr.build_block(snapshot, kind, tz_name="America/New_York", admin=admin, now=NOW)


# ------------------------------------------------------------------------ which questions ----------------------------

@pytest.mark.parametrize("message", [
    "how are things going on your end?", "any downtime lately?", "have you had any downtime?", "were you down earlier? it felt slow",
    "is everything running ok on your end?", "are you having issues right now?", "how have things been running?", "what's your status?",
    "did anything break today?", "how are things with you?",
])
def test_status_questions_are_recognised(message):
    assert sr.kind_of(message) is not None


@pytest.mark.parametrize("message", ["how was your day?", "tell me about your day", "how's your week been?"])
def test_day_questions_are_recognised(message):
    assert sr.kind_of(message) == "day"


@pytest.mark.parametrize("message", [
    "how's it going?", "how are you?", "how have you been?", "hey", "is everything ok?", "are you working on that?",
    "my deploy is down, any ideas?", "how are things going with my tasks?", "is the build running?",
])
def test_greetings_and_other_topics_are_not_status_questions(message):
    assert sr.kind_of(message) is None


def test_a_status_question_skips_the_tool_agent_even_with_a_code_folder_connected():
    from langchain_core.messages import HumanMessage

    from backend.services import agent_workflow as aw

    for message in ("how are things going on your end?", "any downtime lately?", "how was your day?"):
        state = {"messages": [HumanMessage(content=message)], "local_workspace": {"name": "local-rag"}}
        assert aw.build_agent_plan("conversational", state)["agents"] == ["conversational"], message


# ------------------------------------------------------------------------ the facts ----------------------------------

def test_a_normal_snapshot_is_said_to_be_normal_and_has_no_issues():
    built = sr.build_facts(snap(), tz_name="America/New_York", now=NOW)
    assert built["issues"] == [] and built["unreadable"] == []
    joined = " ".join(built["facts"])
    assert "full power" in joined and "No chat turn failed" in joined and "health check ran" in joined
    assert "OVERALL: nothing is wrong" in text(snap())


def test_a_degraded_snapshot_lists_each_problem_and_forbids_saying_all_is_fine():
    rough = snap(low_power=True, failed={"quota": 0, "overloaded": 3, "other": 0}, rows=routine_rows(health="attention", health_counts={"turn_failures": 3}),
                 tasks={"runs": 9, "failed": 2, "people": 7})
    built = sr.build_facts(rough, tz_name="America/New_York", now=NOW)
    assert {"low_power", "failed_turns", "attention", "failed_tasks"} <= set(built["issues"])
    block = text(rough)
    assert "low-power mode" in block and "3 chat turns failed" in block and "overloaded" in block
    assert "you must not say everything is fine" in block


def test_a_routine_that_is_late_is_a_problem_and_says_when_it_last_ran():
    late = snap(rows=routine_rows(health_hours_ago=40))          # every 6 hours: 40 is far past due
    built = sr.build_facts(late, tz_name="America/New_York", now=NOW)
    assert "late" in built["issues"]
    assert any("is late" in f and "last ran" in f for f in built["facts"])


def test_a_failed_run_is_not_called_ok_and_is_a_problem():
    failed = snap(rows=routine_rows(health="failed"))
    built = sr.build_facts(failed, tz_name="America/New_York", now=NOW)
    assert "failed" in built["issues"] and any("did not finish" in f for f in built["facts"])


def test_non_admins_hear_only_that_the_health_check_flagged_something():
    flagged = snap(rows=routine_rows(health="attention", health_counts={"incidents_new": 2, "graph_gaps": 1}))
    plain = " ".join(sr.build_facts(flagged, tz_name="America/New_York", admin=False, now=NOW)["facts"])
    assert "flagged something for the admins" in plain and "incident" not in plain and "knowledge-graph" not in plain
    admin = " ".join(sr.build_facts(flagged, tz_name="America/New_York", admin=True, now=NOW)["facts"])
    assert "2 new incidents" in admin


def test_a_source_that_cannot_be_read_is_said_to_be_unreadable_and_blocks_all_is_fine():
    partial = snap(known=False)
    built = sr.build_facts(partial, now=NOW)
    assert built["unreadable"] and built["issues"] == []
    block = text(partial)
    assert "could not read" in block.lower() and "do not say everything is fine" in block


def test_with_no_snapshot_at_all_the_block_says_it_cannot_see_its_status():
    block = sr.build_block(None, "status")
    assert "cannot see your own status" in block and "do not guess" in block and "do not call it quiet" in block


def test_a_snapshot_whose_sources_all_failed_builds_without_raising_and_says_nothing_about_how_things_are():
    empty = {"at": NOW, "mode": {"known": False, "low_power": False, "paused": []}, "chats": None, "routines": None, "tasks": None}
    built = sr.build_facts(empty, now=NOW)
    assert len(built["unreadable"]) == 4 and built["facts"] == []
    block = text(empty)
    assert "cannot see your own status" in block and "OVERALL" not in block


# ------------------------------------------------------------------------ privacy -------------------------------------

def test_few_chat_turns_are_not_counted_out_loud():
    quiet = " ".join(sr.build_facts(snap(turns=6), now=NOW)["facts"])
    assert "chat turns so far" not in quiet and "6" not in quiet.split("health check")[0]


def test_chat_counts_are_rounded_down_not_exact():
    facts = " ".join(sr.build_facts(snap(turns=43), now=NOW)["facts"])
    assert "about 40 chat turns" in facts and "43" not in facts
    facts = " ".join(sr.build_facts(snap(turns=128), now=NOW)["facts"])
    assert "about 120 chat turns" in facts


def test_a_failure_among_few_turns_says_a_few_not_how_many():
    facts = " ".join(sr.build_facts(snap(turns=8, failed={"quota": 0, "overloaded": 1, "other": 0}), now=NOW)["facts"])
    assert "A few chat turns failed" in facts and "1 chat turn" not in facts


def test_scheduled_task_numbers_need_enough_people_behind_them():
    few = " ".join(sr.build_facts(snap(tasks={"runs": 4, "failed": 1, "people": 2}), now=NOW)["facts"])
    sentence = few[few.index("Some scheduled tasks"):]
    assert sentence == "Some scheduled tasks ran for people in the last day. A few of them failed."
    many = " ".join(sr.build_facts(snap(tasks={"runs": 40, "failed": 3, "people": 12}), now=NOW)["facts"])
    assert "40 scheduled tasks ran" in many and "3 of them failed" in many
    admin = " ".join(sr.build_facts(snap(tasks={"runs": 4, "failed": 1, "people": 2}), admin=True, now=NOW)["facts"])
    assert "4 scheduled tasks ran" in admin


def test_no_fact_contains_a_name_or_anything_a_person_wrote():
    for snapshot in (snap(), snap(low_power=True, failed={"quota": 2, "overloaded": 1, "other": 1}, tasks={"runs": 30, "failed": 4, "people": 9})):
        for fact in sr.build_facts(snapshot, tz_name="America/New_York", now=NOW)["facts"]:
            assert "@" not in fact and "user" not in fact.lower()


def test_the_facts_themselves_pass_the_identity_check():
    rough = snap(low_power=True, failed={"quota": 1, "overloaded": 1, "other": 1}, rows=routine_rows(health="attention", health_hours_ago=30), tasks={"runs": 30, "failed": 4, "people": 9})
    for snapshot in (snap(), rough):
        for fact in sr.build_facts(snapshot, tz_name="America/New_York", now=NOW)["facts"]:
            assert identity_reply_issue(fact) is None, fact


# ------------------------------------------------------------------------ the day -------------------------------------

def test_the_day_framing_answers_from_the_records_and_never_says_it_has_no_days():
    block = text(snap(), kind="day")
    assert "how your day has been" in block and "Do not say you do not have days" in block
    assert "never say you were tired, busy" in block


def test_the_status_framing_does_not_talk_about_days():
    assert "Do not say you do not have days" not in text(snap(), kind="status")


# ------------------------------------------------------------------------ the reply check ---------------------------

@pytest.mark.parametrize("reply", [
    "Everything's running smoothly on my end.", "All good here, no issues today.", "Things are running fine.", "No downtime at all.",
    "Nothing has failed, everything is up.",
])
def test_saying_all_is_fine_while_something_is_wrong_is_caught(reply):
    rough = snap(low_power=True)
    assert sr.reply_issue(reply, rough) is not None


def test_saying_all_is_fine_while_part_of_the_status_is_unreadable_is_caught():
    assert sr.reply_issue("Everything is running fine.", snap(known=False)) is not None
    assert sr.reply_issue("Everything is running fine.", None) is not None


def test_saying_things_are_quiet_when_nothing_could_be_read_is_caught_even_with_a_hedge():
    blind = {"at": NOW, "mode": {"known": False, "low_power": False, "paused": []}, "chats": None, "routines": None, "tasks": None}
    for reply in ("All quiet on my side, though part of my status log isn't loading.", "The system's up and running smoothly, but I can't see everything.",
                  "Nothing showing red on my end."):
        assert sr.reply_issue(reply, blind) is not None, reply
    assert sr.reply_issue("I can't see my own status right now, so I can't say how things are going.", blind) is None


def test_admitting_a_gap_makes_all_is_fine_a_scoped_claim_when_part_of_the_status_is_readable():
    assert sr.reply_issue("Everything I can see is running fine, but I can't see whether low-power mode is on.", snap(known=False)) is None


def test_naming_the_trouble_then_saying_the_rest_is_fine_is_not_a_false_claim():
    rough = snap(low_power=True, failed={"quota": 0, "overloaded": 3, "other": 0})
    assert sr.reply_issue("I'm in low-power mode and three turns failed, but everything else is running fine.", rough) is None
    assert sr.reply_issue("Nothing has failed and everything is running fine.", rough) is not None


def test_saying_all_is_fine_when_it_is_fine_is_allowed():
    assert sr.reply_issue("Everything's running smoothly: the health check found nothing unusual.", snap()) is None


def test_a_reply_that_names_the_problem_is_allowed():
    rough = snap(low_power=True)
    assert sr.reply_issue("I'm in low-power mode right now, so some extras are paused.", rough) is None


# ------------------------------------------------------------------------ the snapshot ------------------------------

def test_the_snapshot_is_shared_for_a_minute(monkeypatch):
    calls = []
    monkeypatch.setattr(sr, "_mode", lambda db, now: calls.append("mode") or {"known": True, "low_power": False, "paused": []})
    monkeypatch.setattr(sr, "_chats", lambda db, now: {"turns": 1, "failed": {"quota": 0, "overloaded": 0, "other": 0}})
    monkeypatch.setattr(sr, "_routine_rows", lambda db, now: [])
    monkeypatch.setattr(sr, "_task_runs", lambda db, now: {"runs": 0, "failed": 0, "people": 0})
    first = sr.snapshot(object(), NOW)
    second = sr.snapshot(object(), NOW)
    assert first is second and calls == ["mode"]
    third = sr.snapshot(object(), NOW, use_cache=False)
    assert third is not first and calls == ["mode", "mode"]


def test_a_source_that_raises_is_left_unread_and_the_others_still_come_through(monkeypatch):
    def boom(db, now):
        raise RuntimeError("database is down")

    monkeypatch.setattr(sr, "_mode", boom)
    monkeypatch.setattr(sr, "_chats", lambda db, now: {"turns": 30, "failed": {"quota": 0, "overloaded": 0, "other": 0}})
    monkeypatch.setattr(sr, "_routine_rows", boom)
    monkeypatch.setattr(sr, "_task_runs", lambda db, now: {"runs": 0, "failed": 0, "people": 0})
    result = sr.snapshot(object(), NOW, use_cache=False)
    assert result["mode"]["known"] is False and result["routines"] is None and result["chats"]["turns"] == 30
    assert "cannot" in text(result).lower() or "could not read" in text(result).lower()


class Coll:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query, projection=None):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in query.items())), None)

    def find(self, query, projection=None):
        since = query["started_at"]["$gte"]
        return Cursor([d for d in self.docs if d["started_at"] >= since])


class Cursor(list):
    def limit(self, n):
        return Cursor(self[:n])


def test_chat_counts_are_read_from_todays_counter_document():
    from backend.utils import self_counters

    db = {self_counters.COUNTERS_COLLECTION: Coll([{"day": "2026-10-21", "counts": {"turns_total": 51, "turn_failed_overloaded": 2, "turn_failed_quota": 1}}])}
    chats = sr._chats(db, NOW)
    assert chats["turns"] == 51 and chats["failed"] == {"quota": 1, "overloaded": 2, "other": 0}
    assert sr._chats({self_counters.COUNTERS_COLLECTION: Coll([])}, NOW) == {"turns": 0, "failed": {"quota": 0, "overloaded": 0, "other": 0}}


def test_task_runs_count_runs_failures_and_people_in_the_last_day_only():
    from backend.utils import user_tasks

    docs = [
        {"username": "a", "status": "ok", "started_at": NOW - timedelta(hours=2)},
        {"username": "a", "status": "failed", "started_at": NOW - timedelta(hours=5)},
        {"username": "b", "status": "nothing_new", "started_at": NOW - timedelta(hours=20)},
        {"username": "c", "status": "ok", "started_at": NOW - timedelta(hours=30)},
    ]
    counted = sr._task_runs({user_tasks.RUNS_COLLECTION: Coll(docs)}, NOW)
    assert counted == {"runs": 3, "failed": 1, "people": 2}
