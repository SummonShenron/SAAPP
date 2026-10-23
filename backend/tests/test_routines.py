"""Sonic's routines: what is scheduled, what ran, and the words for it (backend/utils/routines.py, services/health_check.py, services/routine_scheduler.py)."""
import asyncio
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from backend.services import health_check, routine_scheduler
from backend.utils import routines as rt

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)      # 2:00 PM in New York, 11:00 AM in Los Angeles


# ------------------------------------------------------------------------ fakes ------------------------------------

class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows = sorted(self.rows, key=lambda r: r[key], reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def __iter__(self):
        return iter(self.rows)


class Runs:
    def __init__(self):
        self.rows = []

    def create_index(self, *a, **k):
        return None

    def insert_one(self, doc):
        self.rows.append(dict(doc))

    def find(self, query):
        return Cursor([r for r in self.rows if all(r.get(k) == v for k, v in query.items())])


class Claims:
    def __init__(self):
        self.docs = {}

    def create_index(self, *a, **k):
        return None

    def update_one(self, query, update, upsert=False):
        current = self.docs.get(query["routine"])
        if current is not None and current["claimed_until"] > update["$set"]["claimed_until"] - timedelta(minutes=routine_scheduler.CLAIM_MINUTES):
            raise DuplicateKeyError("held")          # what an upsert onto the unique index does while another process holds the claim
        self.docs[query["routine"]] = {"claimed_until": update["$set"]["claimed_until"]}


class FakeDb(dict):
    def __init__(self):
        super().__init__({rt.RUNS_COLLECTION: Runs(), rt.CLAIMS_COLLECTION: Claims()})


@pytest.fixture(autouse=True)
def indexes_ready(monkeypatch):
    monkeypatch.setattr(rt, "_index_ready", True)


def run_row(routine, hours_ago, status="ok", counts=None, now=NOW):
    return {"routine": routine, "status": status, "counts": counts or {}, "started_at": now - timedelta(hours=hours_ago)}


# ------------------------------------------------------------------------ the registry ----------------------------

def test_every_routine_is_listed_once_with_a_plain_description_and_a_real_interval():
    routines = rt.registry()
    assert len({r.id for r in routines}) == len(routines)
    for r in routines:
        assert r.label and r.does and r.every_hours > 0


def test_nothing_in_a_routine_description_claims_a_feeling_or_a_life():
    words = re.compile(r"\b(enjoy|love|tired|sleep|awake|lonely|excited|looking forward|morning routine|up late)\b", re.IGNORECASE)
    for r in rt.registry():
        assert not words.search(r.does) and not words.search(r.label), r.id


# ------------------------------------------------------------------------ saying when, in the person's own time ----

@pytest.mark.parametrize("moment, tz, expected", [
    (datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc), "America/New_York", "in the small hours around 2 AM"),
    (datetime(2026, 10, 17, 10, 15, tzinfo=timezone.utc), "America/New_York", "this morning around 6 AM"),
    (datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc), "America/Los_Angeles", "last night around 11 PM"),
    (datetime(2026, 10, 17, 3, 30, tzinfo=timezone.utc), "America/Los_Angeles", "yesterday evening around 8:30 PM"),
    (datetime(2026, 10, 17, 22, 0, tzinfo=timezone.utc), "America/New_York", "later this evening around 6 PM"),
    (datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc), "America/New_York", "tomorrow morning around 7 AM"),
    (datetime(2026, 10, 16, 20, 0, tzinfo=timezone.utc), "America/New_York", "yesterday afternoon around 4 PM"),
    (datetime(2026, 10, 17, 14, 40, tzinfo=timezone.utc), "America/New_York", "this morning around 10:30 AM"),
])
def test_a_time_is_put_in_words_in_the_persons_own_timezone(moment, tz, expected):
    assert rt.when_phrase(moment, tz, NOW) == expected


def test_the_same_run_is_this_morning_for_one_person_and_last_night_for_another():
    moment = datetime(2026, 10, 17, 6, 0, tzinfo=timezone.utc)
    assert rt.when_phrase(moment, "America/New_York", NOW) != rt.when_phrase(moment, "America/Los_Angeles", NOW)


def test_a_time_days_away_names_the_weekday():
    future = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
    past = datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc)
    assert rt.when_phrase(future, "America/New_York", NOW).startswith("on Wednesday evening")
    assert rt.when_phrase(past, "America/New_York", NOW).startswith("last Monday evening")


def test_times_are_said_to_the_nearest_half_hour():
    assert rt._clock(datetime(2026, 10, 17, 18, 10)) == "6 PM"
    assert rt._clock(datetime(2026, 10, 17, 6, 20)) == "6:30 AM"


# ------------------------------------------------------------------------ what a run found ----------------------------

def test_a_health_check_that_found_something_gives_the_detail_to_admins_and_only_the_fact_to_everyone_else():
    counts = {"incidents_new": 2, "graph_gaps": 3, "turns": 40, "turn_failures": 6}
    admin = rt.describe_result("health_check", rt.STATUS_ATTENTION, counts, admin=True)
    other = rt.describe_result("health_check", rt.STATUS_ATTENTION, counts, admin=False)
    assert "2 new incidents" in admin and "3 documents with no knowledge-graph relationships" in admin and "6 failed chat turns out of 40" in admin
    assert other == "flagged something for the admins to look at" and "incident" not in other and "graph" not in other


def test_quiet_and_failed_runs_are_described_plainly():
    assert rt.describe_result("health_check", rt.STATUS_OK, {}, admin=False) == "found nothing unusual"
    assert rt.describe_result("health_check", rt.STATUS_FAILED, {}, admin=True) == "did not finish"
    assert rt.describe_result("reading_shelf", rt.STATUS_OK, {"items_added": 1}, admin=False) == "added one piece to the shelf"
    assert rt.describe_result("reading_shelf", rt.STATUS_OK, {"items_added": 0}, admin=False) == "found nothing worth adding"
    assert rt.describe_result("checkpoint_cleanup", rt.STATUS_OK, {"deleted": 1}, admin=False) == "cleared 1 old checkpoint"
    assert rt.describe_result("attachment_cleanup", rt.STATUS_OK, {"deleted": 4}, admin=False) == "removed 4 old attachments"
    assert rt.describe_result("attachment_cleanup", rt.STATUS_OK, {"deleted": 0}, admin=False) == "had nothing to remove"


# ------------------------------------------------------------------------ run records ------------------------------

def test_a_run_record_keeps_only_whole_number_counts_under_plain_names():
    db = FakeDb()
    rt.record_run(db, "health_check", "ok", {"incidents_new": 2, "turns": 5.0, "Bad Key": 3, "note": "a user typed this", "flag": True}, NOW, NOW)
    stored = db[rt.RUNS_COLLECTION].rows[0]
    assert stored["counts"] == {"incidents_new": 2, "turns": 5, "flag": 1}
    assert stored["expires_at"] == NOW + timedelta(days=rt.RUN_RETENTION_DAYS)


def test_an_unknown_status_is_stored_as_a_failure_not_as_a_success():
    db = FakeDb()
    rt.record_run(db, "health_check", "great", {}, NOW, NOW)
    assert db[rt.RUNS_COLLECTION].rows[0]["status"] == rt.STATUS_FAILED


def test_noting_a_run_never_breaks_the_work_it_follows(monkeypatch):
    monkeypatch.setattr(rt, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    rt.record_run_safely("checkpoint_cleanup", "ok", {"deleted": 1})      # must not raise


def test_the_next_run_is_the_last_one_plus_the_interval_and_unknown_when_it_has_never_run():
    routine = rt.Routine("x", "X", "does x", 6.0)
    assert rt.next_due_at(routine, None, NOW) is None
    last = {"started_at": NOW - timedelta(hours=2)}
    assert rt.next_due_at(routine, last, NOW) == NOW + timedelta(hours=4)
    overdue = {"started_at": NOW - timedelta(hours=20)}
    assert rt.next_due_at(routine, overdue, NOW) == NOW + timedelta(minutes=10)


# ------------------------------------------------------------------------ being asked ------------------------------

@pytest.mark.parametrize("message", [
    "what are you doing later?",
    "What do you have planned today?",
    "what's on your schedule",
    "what is on your agenda tonight",
    "what did you do last night?",
    "what did you do today",
    "what have you been up to today?",
    "do you do anything on your own when I'm away?",
    "do you run any background jobs?",
    "what do you do when I'm not here",
    "do you have any scheduled tasks",
])
def test_questions_about_what_sonic_has_planned_or_did_are_recognised(message):
    assert rt.asks_about_routines(message)


@pytest.mark.parametrize("message", [
    "what is the plan for the migration?",
    "what are we doing later?",
    "what did you do to the file?",
    "what did you do wrong in that function",
    "can you schedule a meeting for me tomorrow",
    "what are you?",
    "tell me about your day",
])
def test_ordinary_questions_are_left_alone(message):
    assert not rt.asks_about_routines(message)


# ------------------------------------------------------------------------ the answer ------------------------------

def seeded_db():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows += [
        run_row("health_check", 2, rt.STATUS_ATTENTION, {"incidents_new": 2, "graph_gaps": 1}),
        run_row("reading_shelf", 5, counts={"items_added": 1}),
        run_row("checkpoint_cleanup", 3, counts={"deleted": 7}),
    ]
    return db


def test_the_answer_lists_what_is_coming_up_and_what_ran_in_the_persons_time():
    text = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=True, now=NOW)
    assert "YOUR SCHEDULED ROUTINES" in text
    assert "Health check" in text and "Next run" in text
    assert "ran this morning" in text or "ran earlier" in text or "ran this" in text
    assert "added one piece to the shelf" in text and "cleared 7 old checkpoints" in text


def test_a_routine_that_has_never_run_is_said_not_to_have_run_and_gets_no_invented_time():
    text = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=True, now=NOW)
    attachment_line = [line for line in text.splitlines() if "Attachment tidy-up" in line]
    assert attachment_line and all("Next run" not in line and "ran " not in line for line in attachment_line)
    assert "Not run yet since it was added" in text


def test_only_admins_hear_what_the_health_check_found():
    admin = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=True, now=NOW)
    other = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=False, now=NOW)
    assert "2 new incidents" in admin and "1 document with no knowledge-graph relationships" in admin
    # the job's general description is public; what it FOUND is not
    assert "2 new incident" not in other and "no knowledge-graph relationships" not in other
    assert "flagged something for the admins to look at" in other


def test_with_nothing_scheduled_or_run_the_answer_says_so():
    class Empty(FakeDb):
        pass
    db = Empty()
    old = rt.registry
    try:
        rt.registry = lambda: []
        text = rt.build_routines_context(db, "America/New_York", is_admin=False, now=NOW)
    finally:
        rt.registry = old
    assert "Nothing is scheduled or has run recently." in text


EVENING = datetime(2026, 10, 17, 23, 35, tzinfo=timezone.utc)      # 6:35 PM in Chicago


def evening_db():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows += [
        run_row("health_check", 0.1, now=EVENING),          # next due 6 hours later: after midnight tonight in Chicago
        run_row("checkpoint_cleanup", 0.1, now=EVENING),    # next due in 24 hours: tomorrow evening
        run_row("attachment_cleanup", 0.1, now=EVENING),
    ]
    return db


def test_the_small_hours_after_midnight_count_as_tonight_for_someone_asking_in_the_evening():
    text = rt.build_routines_context(evening_db(), "America/Chicago", is_admin=False, now=EVENING)
    tonight = text[text.index("Later today and tonight"):text.index("Tomorrow:")]
    assert "Health check" in tonight and "after midnight tonight" in tonight and "nothing is scheduled" not in tonight
    tomorrow = text[text.index("Tomorrow:"):text.index("Not run yet")]
    assert "Conversation tidy-up" in tomorrow and "Health check" not in tomorrow


def test_with_no_job_due_before_the_morning_the_answer_says_nothing_is_scheduled_for_tonight():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows += [run_row(r, 0.1, now=EVENING) for r in ("checkpoint_cleanup", "attachment_cleanup", "reading_shelf")]
    db[rt.RUNS_COLLECTION].rows.append(run_row("health_check", 0.1, now=EVENING))
    # the health check is the only thing near: take it away by pretending it ran just now and runs once a day
    original = rt.registry
    rt.registry = lambda: [r if r.id != "health_check" else rt.Routine("health_check", "Health check", "looks things over", 24.0) for r in original()]
    try:
        text = rt.build_routines_context(db, "America/Chicago", is_admin=False, now=EVENING)
    finally:
        rt.registry = original
    assert "Later today and tonight (through the small hours after midnight): nothing is scheduled." in text


def test_a_run_just_after_midnight_is_called_after_midnight_tonight_for_someone_asking_in_the_evening():
    moment = datetime(2026, 10, 18, 5, 35, tzinfo=timezone.utc)      # 12:35 AM in Chicago, a few hours from now
    assert rt.when_phrase(moment, "America/Chicago", EVENING) == "after midnight tonight, around 12:30 AM"
    assert "tomorrow" in rt.when_phrase(moment, "America/Chicago", datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc))   # asked at 9 AM: it is tomorrow


def test_a_job_due_later_the_same_day_is_listed_under_today():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows.append(run_row("health_check", 5, now=EVENING - timedelta(hours=0)))   # last ran 5 hours ago: next in about an hour
    text = rt.build_routines_context(db, "America/Chicago", is_admin=False, now=EVENING)
    start = text.index("Later today")
    ends = [text.index(m, start) for m in ("Tomorrow", "Further out", "Not run yet", "Done recently", "You are not around") if m in text[start:]]
    today_block = text[start:min(ends)]
    assert "Health check" in today_block and "nothing is scheduled" not in today_block


def test_the_instructions_forbid_feelings_and_invented_jobs():
    text = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=False, now=NOW)
    for rule in ("Keep the day words exactly as written", "Never say you were awake, tired, up late, busy, or", "never add a job, a time or a result that is not listed",
                 "If they ask about a time when nothing is listed, say nothing is scheduled then"):
        assert rule in text


def test_a_run_older_than_two_days_is_not_offered_as_what_it_did_today():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows.append(run_row("reading_shelf", rt.RECENT_WINDOW_HOURS + 6, counts={"items_added": 1}))
    assert "Done recently" not in rt.build_routines_context(db, "America/New_York", is_admin=False, now=NOW)


# ------------------------------------------------------------------------ the scheduler ---------------------------

def test_a_due_routine_runs_once_and_is_written_down():
    db = FakeDb()
    calls = []

    async def runner(_db):
        calls.append(1)
        return rt.STATUS_OK, {"turns": 3}

    ran = asyncio.run(routine_scheduler.run_due(db, NOW, {"health_check": runner}))
    assert ran == 1 and calls == [1]
    stored = db[rt.RUNS_COLLECTION].rows[0]
    assert stored["routine"] == "health_check" and stored["status"] == rt.STATUS_OK and stored["counts"] == {"turns": 3}


def test_a_routine_that_ran_recently_is_left_alone():
    db = FakeDb()
    db[rt.RUNS_COLLECTION].rows.append(run_row("health_check", 1))
    ran = asyncio.run(routine_scheduler.run_due(db, NOW, {"health_check": lambda _db: (_ for _ in ()).throw(AssertionError("must not run"))}))
    assert ran == 0


def test_a_second_process_cannot_start_the_same_routine_while_the_first_holds_it():
    db = FakeDb()
    assert routine_scheduler.claim(db, "health_check", NOW) is True
    assert routine_scheduler.claim(db, "health_check", NOW + timedelta(minutes=1)) is False


def test_a_routine_that_blows_up_is_recorded_as_failed_and_the_schedule_carries_on():
    db = FakeDb()

    async def broken(_db):
        raise RuntimeError("boom")

    ran = asyncio.run(routine_scheduler.run_due(db, NOW, {"health_check": broken}))
    assert ran == 1 and db[rt.RUNS_COLLECTION].rows[0]["status"] == rt.STATUS_FAILED


def test_the_routines_that_have_their_own_loops_are_not_run_from_the_scheduler():
    assert set(routine_scheduler.RUNNERS) == {"health_check"}


# ------------------------------------------------------------------------ the health check ------------------------

class Sdk:
    def __init__(self, incidents=(), deploy_status="live", fail=False):
        self.incidents, self.deploy_status, self.fail = list(incidents), deploy_status, fail

    async def list_incidents(self, since=None, limit=None, status=None):
        if self.fail:
            raise RuntimeError("errAgent is down")
        return self.incidents

    async def latest_deploy(self):
        return {"status": "ok", "latestDeploy": {"status": self.deploy_status}}


@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(health_check, "_turn_counts", lambda db, now: {"turns": 40, "turn_failures": 0})
    monkeypatch.setattr(health_check, "_graph_gaps", lambda db: 0)
    monkeypatch.setattr(health_check, "_budget_limited", lambda db, now: 0)
    monkeypatch.setattr("backend.services.ops_incidents.ops_available", lambda: False)


def test_a_quiet_look_is_ok(quiet):
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk()))
    assert status == rt.STATUS_OK and counts["incidents_new"] == 0 and counts["checks_failed"] == 0


def test_a_new_incident_or_a_failed_deploy_is_flagged(quiet):
    fresh = {"id": "inc_1", "created_at": (NOW - timedelta(hours=1)).isoformat()}
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(incidents=[fresh])))
    assert status == rt.STATUS_ATTENTION and counts["incidents_new"] == 1
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(deploy_status="build_failed")))
    assert status == rt.STATUS_ATTENTION and counts["deploy_failed"] == 1


def test_an_old_incident_is_not_new(quiet):
    old = {"id": "inc_1", "created_at": (NOW - timedelta(hours=30)).isoformat()}
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(incidents=[old])))
    assert status == rt.STATUS_OK and counts["incidents_new"] == 0


def test_a_graph_with_gaps_a_failing_chat_or_a_spent_budget_each_raises_a_flag(monkeypatch, quiet):
    monkeypatch.setattr(health_check, "_graph_gaps", lambda db: 3)
    assert asyncio.run(health_check.run_health_check(object(), NOW))[0] == rt.STATUS_ATTENTION
    monkeypatch.setattr(health_check, "_graph_gaps", lambda db: 0)
    monkeypatch.setattr(health_check, "_turn_counts", lambda db, now: {"turns": 40, "turn_failures": 9})
    assert asyncio.run(health_check.run_health_check(object(), NOW))[0] == rt.STATUS_ATTENTION
    monkeypatch.setattr(health_check, "_turn_counts", lambda db, now: {"turns": 40, "turn_failures": 0})
    monkeypatch.setattr(health_check, "_budget_limited", lambda db, now: 1)
    assert asyncio.run(health_check.run_health_check(object(), NOW))[0] == rt.STATUS_ATTENTION


def test_one_look_failing_does_not_stop_the_others_and_is_counted(monkeypatch, quiet):
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(fail=True)))
    assert counts["checks_failed"] == 1 and status == rt.STATUS_OK


def test_when_nothing_at_all_can_be_looked_at_it_is_a_failure_not_all_clear(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(health_check, "_turn_counts", boom)
    monkeypatch.setattr(health_check, "_graph_gaps", boom)
    monkeypatch.setattr(health_check, "_budget_limited", boom)
    status, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(fail=True)))
    assert counts["checks_failed"] == 4 and status == rt.STATUS_FAILED


def test_a_health_check_result_holds_only_numbers(quiet):
    fresh = {"id": "inc_1", "created_at": (NOW - timedelta(hours=1)).isoformat(), "error_message": "TypeError: secret user text"}
    _, counts = asyncio.run(health_check.run_health_check(object(), NOW, sdk=Sdk(incidents=[fresh])))
    assert all(isinstance(v, int) for v in counts.values()) and "secret" not in repr(counts)


# ------------------------------------------------------------------------ the wiring -------------------------------

def test_the_app_starts_the_scheduler_and_answers_from_the_real_records_only_when_it_is_safe_to():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "routine_scheduler.run_routine_schedule(get_db)" in app and "routine_task.cancel()" in app
    block = app[app.index("routines_context = \"\""):app.index("routines_context=routines_context")]
    assert "risk_level == RISK_NONE" in block and 'source_type == "conversational"' in block and "not is_closing_message(question)" in block
    assert "routines_utils.build_routines_context(" in block and "is_admin=is_admin_user" in block


def test_every_loop_that_runs_a_routine_writes_down_its_runs():
    for file, routine in (("backend/services/shelf_refresh.py", "reading_shelf"), ("backend/services/checkpoint_retention.py", "checkpoint_cleanup"),
                          ("backend/services/attachment_retention.py", "attachment_cleanup")):
        assert f'"{routine}"' in (ROOT / file).read_text(encoding="utf-8"), file
    assert {r.id for r in rt.registry()} == {"health_check", "reading_shelf", "checkpoint_cleanup", "attachment_cleanup"}


def test_the_voice_prompt_has_a_place_for_the_routines():
    from backend.components.constraints import build_voice_prompt
    prompt = build_voice_prompt("G", "D", "H", "Q", routines_context="\nROUTINES-MARKER\n")
    assert "ROUTINES-MARKER" in prompt
    assert "ROUTINES-MARKER" not in build_voice_prompt("G", "D", "H", "Q")


# ---- how it is said, and when its jobs run (2026-10-22: "too mechanical", and a health check moved to "later tonight") ---------------------------------------------------------------

SCHEDULE = """- Health check. Next run later this morning around 7:30 AM. It looks over things.
- Reading refresh. Next run later this afternoon around 1:30 PM. It reads one page.
- Conversation tidy-up. Next run tomorrow morning around 6:30 AM. It clears out old checkpoints.
- Attachment tidy-up. Next run tomorrow morning around 6:30 AM. It removes photos."""


@pytest.mark.parametrize("reply", [
    "Pretty light on my end. Just a reading refresh set for this afternoon and a health check later tonight to make sure everything stays stable.",
    "I've got a health check this afternoon.", "Tonight I read a page for my shelf.", "The tidy-ups run tomorrow afternoon.",
])
def test_a_job_put_in_a_different_part_of_the_day_is_caught(reply):
    assert rt.reply_issue(reply, SCHEDULE) == "put a job in a different part of the day than its schedule says"


@pytest.mark.parametrize("reply", [
    "Pretty light on my end. I have a health check coming up later this morning, and then I'll read an article for my shelf this afternoon.",
    "Quiet today. The health check ran in the small hours and found nothing unusual, and the tidy-ups ran this morning.",
    "A health check later today and some reading this afternoon.", "Nothing big. The reading refresh ran yesterday afternoon.", "Tomorrow morning I tidy up old chats.",
    "Light day, a health check and a bit of reading.", "",
])
def test_replies_that_keep_each_job_in_its_part_of_the_day_or_leave_the_time_out_pass(reply):
    assert rt.reply_issue(reply, SCHEDULE) is None


def test_with_no_schedule_lines_there_is_nothing_to_check():
    assert rt.reply_issue("A health check tonight.", "Nothing is scheduled or has run recently.") is None
    assert rt.reply_issue("anything", "") is None


def test_the_schedule_lines_lead_with_the_job_and_its_time_and_the_block_says_how_full_the_day_is():
    text = rt.build_routines_context(seeded_db(), "America/New_York", is_admin=False, now=NOW)
    assert re.search(r"- Health check\. Next run [^.]+\. It ", text) and "HOW FULL TODAY IS:" in text
    assert "HOW TO SAY IT: like a person telling a friend about their day" in text and "never close with \"here whenever you need me\"" in text
    for banned in ("automated", "scheduled", "routine", "process", "run through", "background"):
        assert f"\\\"{banned}\\\"" in text.replace('"', '\\"') or banned in text


def test_the_chat_endpoint_checks_a_routines_answer_against_its_schedule():
    source = open("app.py", encoding="utf-8").read()
    assert "routines_utils.reply_issue(full_response, routines_context)" in source


# ------------------------------------------------------------------------ asked about a run that happened, pushback, and curly apostrophes ------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Good morning saapp, how did your job you ran last night go?",
    "did your health check run last night?",
    "how did the job you ran last night go?",
    "But you have jobs you run on your own?",
    "you do run jobs",
    "I was just curious what you’ve been up to",           # a phone's curly apostrophe
    "I was just curious what you've been up to",
    "tell me what you did last night",
])
def test_questions_about_its_own_runs_and_pushback_after_a_denial_are_recognised(message):
    assert rt.asks_about_routines(message) is True


@pytest.mark.parametrize("message", [
    "how did my job interview go?",
    "how did the job go? my boss was mad",
    "do you like your job?",
    "my job ran late today",
    "what do you think of my jobs page?",
])
def test_the_persons_own_job_is_not_a_question_about_its_jobs(message):
    assert rt.asks_about_routines(message) is False


def test_an_insisting_reply_counts_only_right_after_a_reply_about_its_jobs():
    denial = "No, I don't run jobs on my own. I don't have a scheduler or background processes."
    assert rt.follows_routines_ask("Yes you do…… and yes you can lol", denial) is True
    assert rt.follows_routines_ask("yes you do", "Sounds good, what's next for you?") is False
    assert rt.follows_routines_ask("yes you do, and then we should plan the whole interview prep for next week properly", denial) is False   # not short: a new subject
    assert rt.follows_routines_ask("", denial) is False


def test_the_last_reply_is_found_in_messages_of_either_shape():
    class Msg:
        def __init__(self, type_, content):
            self.type, self.content = type_, content
    assert rt.previous_reply([Msg("human", "hi"), Msg("ai", "hello there"), Msg("human", "yes you do")]) == "hello there"
    assert rt.previous_reply([{"role": "assistant", "content": "from a dict"}, {"role": "user", "content": "x"}]) == "from a dict"
    assert rt.previous_reply([Msg("ai", [{"type": "text", "text": "in parts"}])]) == "in parts"
    assert rt.previous_reply([]) == ""


def test_a_reply_that_denies_having_jobs_is_an_issue_when_the_record_lists_them():
    for reply in ["No, I don't run jobs on my own. I only act when you ask me something.", "I don’t have a scheduler or background processes running overnight."]:
        assert rt.reply_issue(reply, SCHEDULE) == "denied running any jobs of its own, when the record it was given lists them"
    assert rt.reply_issue("I ran a health check this morning and it came back clean.", SCHEDULE) is None


def test_the_wiring_uses_the_followup_and_the_denial_check():
    app = open(ROOT / "app.py", encoding="utf-8").read()
    flow = open(ROOT / "backend/services/agent_workflow.py", encoding="utf-8").read()
    assert "routines_utils.follows_routines_ask(question, routines_utils.previous_reply(chat_sessions[history_key]))" in app
    assert "routines.follows_routines_ask(_content_of(_messages[-1]), routines.previous_reply(_messages[:-1]))" in flow


@pytest.mark.parametrize("message", [
    "what were you up to last night?", "what were you doing while I was asleep", "what'd you do overnight", "what ran last night?",
    "what jobs ran last night", "did any jobs run overnight?",
])
def test_what_were_you_up_to_last_night_is_the_same_question_as_what_jobs_ran(message):
    assert rt.asks_about_routines(message) is True


@pytest.mark.parametrize("message", ["what were you doing in that last answer?", "what were you thinking?", "what were you up to when you said that"])
def test_what_were_you_doing_without_a_time_is_left_alone(message):
    assert rt.asks_about_routines(message) is False
