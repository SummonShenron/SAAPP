"""Schedule windows: a task may run "sometime in a window" at a quiet moment, decided by code, with the reason stored. See docs/schedule-windows-plan.md."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.services import user_task_scheduler as scheduler
from backend.utils import budget_guard as bg
from backend.utils import user_activity as ua
from backend.utils import user_tasks as ut

NOW = datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)          # Monday 9:00 AM in Chicago


# ------------------------------------------------------------------------ a small in-memory database ------------------

def _match(doc, flt):
    for key, want in flt.items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in want):
                return False
            continue
        have = doc.get(key)
        if isinstance(want, dict):
            for op, value in want.items():
                if op == "$lte" and not (have is not None and have <= value):
                    return False
                if op == "$gte" and not (have is not None and have >= value):
                    return False
        elif have != want:
            return False
    return True


class Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs if _match(d, flt or {})]

    def find_one(self, flt=None, projection=None):
        hits = self.find(flt)
        return hits[0] if hits else None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _match(d, flt):
                d.update(update["$set"])
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            self.docs.append(dict(update["$set"]))
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find_one_and_update(self, flt, update, sort=None, projection=None, return_document=None):
        hits = [d for d in self.docs if _match(d, flt)]
        if sort:
            hits.sort(key=lambda d: d.get(sort[0][0]))
        if not hits:
            return None
        hits[0].update(update["$set"])
        return dict(hits[0])

    def delete_many(self, flt):
        hits = [d for d in self.docs if _match(d, flt)]
        for d in hits:
            self.docs.remove(d)
        return SimpleNamespace(deleted_count=len(hits))

    def delete_one(self, flt):
        return self.delete_many(flt)


class Db(dict):
    def __missing__(self, name):
        self[name] = Col()
        return self[name]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(ut, "_index_ready", False)
    ua.reset_memory()


SPEC = {"title": "Python jobs", "topic": "remote python developer jobs", "details": "", "cadence": "daily", "hour": 8, "tz": "America/Chicago"}


def make(db, user="u1", **over):
    clean, issue = ut.validate_spec({**SPEC, **over})
    assert issue is None, issue
    task, issue = ut.create_task(db, user, clean, NOW - timedelta(days=1))
    assert issue is None, issue
    return task


def due_in_window(db, user="u1", opened_minutes_ago=30, closes_in_minutes=90, **over):
    """A windowed task whose window opened `opened_minutes_ago` and closes in `closes_in_minutes`, and which is due now."""
    task = make(db, user, window_hours=2, **over)
    db[ut.COLLECTION].update_one({"id": task["id"]}, {"$set": {
        "next_run_at": NOW - timedelta(minutes=5), "window_starts_at": NOW - timedelta(minutes=opened_minutes_ago), "window_ends_at": NOW + timedelta(minutes=closes_in_minutes),
    }})
    return task


class Resp:
    def __init__(self, content):
        self.content = content


def _results(*urls):
    return [{"title": f"Result {i}", "url": u, "snippet": "a role"} for i, u in enumerate(urls)]


def run_pass(db, now=NOW, allowed=lambda owner: True, headroom=None, idle=None):
    async def invoke(prompt):
        return Resp('{"headline": "h", "items": [{"n": 1, "note": "n"}]}')

    return asyncio.run(scheduler.run_due(
        db, invoke, allowed, now, headroom=headroom, idle=idle,
        search=lambda q: _results("https://jobs.example.org/a"), fetch=lambda url: SimpleNamespace(ok=True, text="A role page", url=url, status=200),
    ))


@pytest.fixture(autouse=True)
def stub_runner(monkeypatch):
    """The run itself is not what is tested here: a run that finds one thing, with no search or model."""
    async def fake_run(db, task, invoke, **kwargs):
        return {"status": ut.OK, "items": [{"title": "t"}], "headline": "One.", "new_keys": []}

    from backend.services import user_task_runner

    monkeypatch.setattr(user_task_runner, "run_task", fake_run)


def runs_of(db):
    return sorted(db[ut.RUNS_COLLECTION].docs, key=lambda r: str(r["started_at"]))


# ------------------------------------------------------------------------ what is accepted and how it is said ---------

def test_a_window_is_exact_two_or_three_hours_and_nothing_else():
    for hours in (0, 2, 3):
        spec, issue = ut.validate_spec({**SPEC, "window_hours": hours})
        assert issue is None and spec["window_hours"] == hours
    assert ut.validate_spec(SPEC)[0]["window_hours"] == 0                      # a task made before windows existed is exact
    for bad in (1, 5, 24, -2, "soon"):
        assert ut.validate_spec({**SPEC, "window_hours": bad})[0] is None


def test_the_window_is_said_in_plain_words():
    assert ut.describe_schedule({"cadence": "daily", "hour": 13, "window_hours": 2}) == "every day between 1 and 3 PM"
    assert ut.describe_schedule({"cadence": "daily", "hour": 11, "window_hours": 3}) == "every day between 11 AM and 2 PM"
    assert ut.describe_schedule({"cadence": "weekly", "weekday": 0, "hour": 22, "window_hours": 3}) == "every Monday between 10 PM and 1 AM"
    assert ut.describe_schedule({"cadence": "daily", "hour": 8}) == "every day around 8 AM"                      # exact tasks read as before


# ------------------------------------------------------------------------ when a windowed task is due -----------------

def test_an_exact_task_has_no_window_and_a_windowed_one_is_due_inside_its_first_stretch():
    exact = ut.schedule_fields({**SPEC, "id": "a"}, NOW)
    assert exact["window_starts_at"] is None and exact["window_ends_at"] is None
    windowed = ut.schedule_fields({**SPEC, "id": "a", "window_hours": 3}, NOW)
    start, end, due = windowed["window_starts_at"], windowed["window_ends_at"], windowed["next_run_at"]
    assert end - start == timedelta(hours=3) and start <= due < start + timedelta(minutes=int(3 * 60 * ut.WINDOW_JITTER_SHARE))
    assert start == ut.next_run_at(SPEC, NOW)                                                         # the window opens at the hour the person chose


def test_the_jitter_is_the_same_every_time_it_is_asked_and_differs_between_tasks():
    again = [ut.window_jitter_minutes("task-1", "2026-10-20", 3) for _ in range(5)]
    assert len(set(again)) == 1
    spread = {ut.window_jitter_minutes(f"task-{i}", "2026-10-20", 3) for i in range(200)}
    assert len(spread) > 40 and max(spread) < 3 * 60 * ut.WINDOW_JITTER_SHARE and min(spread) >= 0
    assert ut.window_jitter_minutes("task-1", "2026-10-20", 0) == 0


def test_a_task_made_with_a_window_stores_it_and_changing_the_window_moves_the_schedule():
    db = Db()
    task = make(db, window_hours=3)
    assert task["window_ends_at"] - task["window_starts_at"] == timedelta(hours=3) and ut.is_windowed(task)
    changed, issue = ut.update_task(db, "u1", task["id"], {"window_hours": 0}, NOW)
    assert issue is None and changed["window_ends_at"] is None and not ut.is_windowed(changed)
    assert ut.public_task(changed)["window_hours"] == 0 and ut.public_task(task)["window_hours"] == 3


# ------------------------------------------------------------------------ waiting for a quiet moment ----------------

def test_a_windowed_task_waits_while_its_owner_is_here_and_says_so_in_the_reason_when_it_runs():
    db = Db()
    task = due_in_window(db)
    counts = run_pass(db, idle=lambda owner, now: 5.0)
    assert counts == {"ran": 0, "found": 0, "skipped": 0, "waited": 1}
    waiting = ut.get_task(db, "u1", task["id"])
    assert waiting["lease_until"] is None and waiting["waited_for"] == ["quiet"]
    assert NOW < waiting["next_run_at"] <= waiting["window_ends_at"]                                  # looked at again later, never past the window
    later = NOW + timedelta(minutes=ut.DEFER_MINUTES + 1)
    counts = run_pass(db, now=later, idle=lambda owner, now: 45.0)
    assert counts["ran"] == 1
    run = runs_of(db)[0]
    assert run["reason"] == "Waited for you to be away for a while, then ran inside the window."
    assert run["window"] == {"starts_at": task_window(waiting)[0], "ends_at": task_window(waiting)[1]} and run["late_minutes"] == 41
    assert ut.get_task(db, "u1", task["id"])["waited_for"] == [] and ut.get_task(db, "u1", task["id"])["window_ends_at"] > later      # moved on to the next window


def task_window(task):
    return task["window_starts_at"], task["window_ends_at"]


def test_a_task_that_never_had_to_wait_says_how_long_its_owner_had_been_away():
    db = Db()
    due_in_window(db)
    assert run_pass(db, idle=lambda owner, now: 120.0)["ran"] == 1
    assert runs_of(db)[0]["reason"] == f"Ran inside the window, after at least {ut.QUIET_MINUTES} minutes with no activity from you."


def test_a_person_with_no_activity_on_record_counts_as_away():
    db = Db()
    due_in_window(db)
    assert run_pass(db, idle=lambda owner, now: None)["ran"] == 1
    assert runs_of(db)[0]["reason"] == "Ran inside the window."


def test_when_the_window_closes_the_task_runs_anyway():
    db = Db()
    due_in_window(db, closes_in_minutes=0)
    assert run_pass(db, idle=lambda owner, now: 1.0)["ran"] == 1
    assert runs_of(db)[0]["reason"] == "The window closed, so it ran anyway."


def test_a_task_waiting_is_looked_at_again_no_later_than_the_end_of_its_window():
    db = Db()
    task = due_in_window(db, closes_in_minutes=4)
    run_pass(db, idle=lambda owner, now: 1.0)
    assert ut.get_task(db, "u1", task["id"])["next_run_at"] == NOW + timedelta(minutes=4)


# ------------------------------------------------------------------------ the budget -------------------------------

def test_a_windowed_task_waits_for_headroom_and_an_exact_one_gives_way():
    db = Db()
    windowed = due_in_window(db, "u1")
    exact = make(db, "u2")
    db[ut.COLLECTION].update_one({"id": exact["id"]}, {"$set": {"next_run_at": NOW - timedelta(minutes=5)}})
    counts = run_pass(db, headroom=lambda: False, idle=lambda owner, now: 120.0)
    assert counts == {"ran": 0, "found": 0, "skipped": 1, "waited": 1}
    assert ut.get_task(db, "u1", windowed["id"])["waited_for"] == ["headroom"]
    assert ut.get_task(db, "u2", exact["id"])["next_run_at"] > NOW                                    # skipped to its next time, not retried every pass
    counts = run_pass(db, now=NOW + timedelta(minutes=11), headroom=lambda: True, idle=lambda owner, now: 120.0)
    assert counts["ran"] == 1 and runs_of(db)[0]["reason"] == "Waited for room in the usage budget, then ran inside the window."


def test_a_window_that_has_closed_runs_even_without_headroom_but_not_in_low_power_mode():
    db = Db()
    due_in_window(db, "u1", closes_in_minutes=0)
    assert run_pass(db, headroom=lambda: False)["ran"] == 1
    db2 = Db()
    task = due_in_window(db2, "u1", closes_in_minutes=0)
    counts = run_pass(db2, allowed=lambda owner: False)
    assert counts["ran"] == 0 and counts["skipped"] == 1 and ut.get_task(db2, "u1", task["id"])["next_run_at"] > NOW


def test_low_power_mode_before_the_window_closes_only_makes_it_wait():
    db = Db()
    task = due_in_window(db)
    counts = run_pass(db, allowed=lambda owner: False)
    assert counts["waited"] == 1 and counts["skipped"] == 0
    assert ut.get_task(db, "u1", task["id"])["next_run_at"] <= ut.get_task(db, "u1", task["id"])["window_ends_at"]


# ------------------------------------------------------------------------ fairness ------------------------------------

def test_one_person_cannot_take_every_slot_in_a_pass():
    db = Db()
    first = make(db, "u1")
    second = make(db, "u1", title="Other jobs", topic="remote rust developer jobs")
    third = make(db, "u2")
    for task in (first, second, third):
        db[ut.COLLECTION].update_one({"id": task["id"]}, {"$set": {"next_run_at": NOW - timedelta(minutes=5)}})
    # u1's two tasks are the most overdue; u2's is behind them
    db[ut.COLLECTION].update_one({"id": first["id"]}, {"$set": {"next_run_at": NOW - timedelta(minutes=30)}})
    db[ut.COLLECTION].update_one({"id": second["id"]}, {"$set": {"next_run_at": NOW - timedelta(minutes=20)}})
    counts = run_pass(db)
    assert counts["ran"] == 2 and counts["waited"] == 1
    assert {r["username"] for r in runs_of(db)} == {"u1", "u2"}
    assert ut.get_task(db, "u1", second["id"])["next_run_at"] == NOW + timedelta(minutes=ut.DEFER_MINUTES)       # u1's second one takes the next pass


# ------------------------------------------------------------------------ exact tasks and manual runs ----------------

def test_an_exact_task_runs_as_it_always_did_and_says_so():
    db = Db()
    task = make(db)
    db[ut.COLLECTION].update_one({"id": task["id"]}, {"$set": {"next_run_at": NOW - timedelta(minutes=5)}})
    assert run_pass(db, headroom=lambda: True, idle=lambda owner, now: 1.0)["ran"] == 1             # being active does not hold an exact task
    run = runs_of(db)[0]
    assert run["reason"] == "Ran at its scheduled time." and run["window"] is None and run["late_minutes"] is None


def test_a_run_the_person_started_says_so():
    db = Db()
    task = make(db, window_hours=2)
    run = asyncio.run(scheduler.run_one(db, task, None, "manual", now=NOW))
    assert run["reason"] == "You ran it yourself." and run["window"] is None
    assert ut.public_run(run)["reason"] == "You ran it yourself."


def test_the_run_history_shows_the_window_a_run_landed_in():
    db = Db()
    due_in_window(db)
    run_pass(db, idle=lambda owner, now: 60.0)
    shown = ut.public_run(runs_of(db)[0])
    assert shown["window"]["starts_at"] < shown["window"]["ends_at"] and shown["reason"]


def test_what_sonic_says_about_a_windowed_task_is_the_window_and_never_a_clock_time():
    db = Db()
    task = make(db, window_hours=2)
    text = ut.build_tasks_context(db, "u1", "America/Chicago", NOW)
    assert "between 8 and 10 AM" in text and "its window opens" in text and "quiet moment" in text and "next run" not in text
    assert task["title"] in text


# ------------------------------------------------------------------------ activity ------------------------------------

def test_activity_is_one_timestamp_written_at_most_every_few_minutes():
    db = Db()
    assert ua.touch(db, "u1", NOW) is True
    assert ua.touch(db, "u1", NOW + timedelta(seconds=30)) is False                                   # too soon: no second write
    assert len(db[ua.COLLECTION].docs) == 1 and set(db[ua.COLLECTION].docs[0]) == {"username", "last_active_at", "last_active_dt"}
    assert ua.touch(db, "u2", NOW) is True and len(db[ua.COLLECTION].docs) == 2
    assert ua.touch(None, "u3", NOW) is False and ua.touch(db, None, NOW) is False


def test_idle_minutes_come_from_the_timestamp_and_are_none_without_one():
    db = Db()
    assert ua.idle_minutes(db, "u1", NOW) is None
    ua.touch(db, "u1", NOW - timedelta(minutes=42))
    assert ua.idle_minutes(db, "u1", NOW) == pytest.approx(42.0)
    assert ua.idle_minutes(db, "u1", NOW - timedelta(minutes=60)) == 0.0                              # never negative


def test_a_failed_write_is_tried_again_on_the_next_turn_and_never_raises():
    class Broken(Db):
        def __missing__(self, name):
            raise RuntimeError("db down")

    assert ua.touch(Broken(), "u1", NOW) is False
    db = Db()
    assert ua.touch(db, "u1", NOW) is True


def test_forgetting_a_person_removes_their_timestamp():
    db = Db()
    ua.touch(db, "u1", NOW)
    ua.touch(db, "u2", NOW)
    assert ua.delete_for_user(db, "u1") == 1 and [d["username"] for d in db[ua.COLLECTION].docs] == ["u2"]


# ------------------------------------------------------------------------ the headroom rule ---------------------------

def test_scheduled_work_stops_at_a_share_of_the_shared_budget_and_ignores_a_persons_own_limit(monkeypatch):
    monkeypatch.delenv("LLM_SCHEDULED_HEADROOM", raising=False)
    state = {"detail": [{"scope": "day", "fraction": 0.5}, {"scope": "month", "fraction": 0.3}, {"scope": "my_day", "fraction": 0.99}]}
    assert bg.scheduled_headroom(state) is True                                                       # their own day is judged by allows(), not here
    assert bg.scheduled_headroom({"detail": [{"scope": "day", "fraction": 0.66}]}) is False
    assert bg.scheduled_headroom({"detail": [{"scope": "day", "fraction": 0.64}, {"scope": "five", "fraction": 0.7}]}) is False
    assert bg.scheduled_headroom({"level": "off", "detail": []}) is True and bg.scheduled_headroom(None) is True


def test_the_headroom_limit_can_be_set(monkeypatch):
    monkeypatch.setenv("LLM_SCHEDULED_HEADROOM", "0.4")
    assert bg.scheduled_headroom({"detail": [{"scope": "day", "fraction": 0.5}]}) is False
    monkeypatch.setenv("LLM_SCHEDULED_HEADROOM", "5")
    assert bg.scheduled_headroom_limit() == 1.0
