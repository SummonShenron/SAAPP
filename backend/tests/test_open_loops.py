import asyncio
import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.utils import open_loops as ol
from backend.utils.identity_checks import identity_reply_issue

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)  # 10:00 on Friday 9 October in Chicago
TODAY = "2026-10-09"
TZ = "America/Chicago"


# ---- an in-memory stand-in for the one collection this module uses -------------------------------------------------

class _Result:
    def __init__(self, matched=0, deleted=0):
        self.matched_count = matched
        self.deleted_count = deleted


class _Col:
    def __init__(self):
        self.docs = []
        self.indexes = []

    def create_index(self, field, **kwargs):
        self.indexes.append((field, kwargs))

    def find(self, flt, projection=None):
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())][:1]
        for d in hits:
            d.update(update["$set"])
        return _Result(matched=len(hits))

    def delete_one(self, flt):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())][:1]
        for d in hits:
            self.docs.remove(d)
        return _Result(deleted=len(hits))

    def delete_many(self, flt):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())]
        for d in hits:
            self.docs.remove(d)
        return _Result(deleted=len(hits))


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == ol.COLLECTION
        return self.col


@pytest.fixture(autouse=True)
def _fresh_index_flag(monkeypatch):
    monkeypatch.setattr(ol, "_index_ready", False)
    monkeypatch.delenv("OPEN_LOOPS_ENABLED", raising=False)


def loop(text="has a first date", due="2026-10-08", kind="social", status=ol.OPEN, id="a1"):
    return {"id": id, "username": "u", "text": text, "due_date": due, "kind": kind, "status": status}


# ---- the gate ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "i have a big date tomorrow night",
    "interview on Friday, a bit nervous",
    "interview Friday, wish me luck",
    "lunch with sam next tuesday",
    "flying out next week for the conference",
    "the demo is this weekend",
    "presenting on Oct 14",
    "deadline is 10/15",
    "in two weeks i start the new project",
    "dinner tonight with my sister",
])
def test_future_language_passes_the_gate(message):
    assert ol.should_extract(message)


@pytest.mark.parametrize("message", [
    "how do i fix this null pointer",
    "what's the capital of france",
    "thanks, that worked",
    "",
    "   ",
])
def test_ordinary_messages_do_not(message):
    assert not ol.should_extract(message)


def test_a_past_tense_report_only_passes_when_a_loop_is_due_to_close():
    report = "the date went great, we got dinner"
    assert not ol.should_extract(report, has_due_loop=False)
    assert ol.should_extract(report, has_due_loop=True)


def test_a_huge_message_is_not_sent_to_the_model():
    assert not ol.should_extract("tomorrow " * 1000)


@pytest.mark.parametrize("text", [
    "has a doctor's appointment", "is starting therapy", "has a court date", "is going to a funeral",
    "has a biopsy on Friday", "meets with a lawyer", "has a custody hearing",
])
def test_sensitive_things_are_recognized(text):
    assert ol.is_sensitive(text)


@pytest.mark.parametrize("text", ["has a first date", "is giving a team presentation", "is flying to Denver"])
def test_ordinary_things_are_not_sensitive(text):
    assert not ol.is_sensitive(text)


# ---- validating what the model returned ----------------------------------------------------------------------------

TODAY_DATE = date(2026, 10, 9)


def test_a_good_loop_is_cleaned_and_kept():
    out = ol.validate_loop({"what": "  has a   first date ", "due_date": "2026-10-10", "kind": "Social"}, TODAY_DATE)
    assert out == {"text": "has a first date", "due_date": "2026-10-10", "kind": "social"}


@pytest.mark.parametrize("raw", [
    None, "a date", {}, {"what": "", "due_date": "2026-10-10", "kind": "social"},
    {"what": "has a date", "due_date": "2026-10-08", "kind": "social"},          # yesterday
    {"what": "has a date", "due_date": "2027-03-01", "kind": "social"},          # too far out
    {"what": "has a date", "due_date": "tomorrow", "kind": "social"},            # not a date
    {"what": "has a date", "due_date": "2026-13-45", "kind": "social"},
    {"what": "has a date", "due_date": "2026-10-10", "kind": "sensitive"},       # never stored
    {"what": "has a date", "due_date": "2026-10-10", "kind": "romance"},         # unknown kind
    {"what": "has a doctor's appointment", "due_date": "2026-10-10", "kind": "event"},
    {"what": "x" * 200, "due_date": "2026-10-10", "kind": "event"},
])
def test_bad_proposals_are_dropped(raw):
    assert ol.validate_loop(raw, TODAY_DATE) is None


def test_today_is_a_valid_due_date_because_tonight_is_today():
    assert ol.validate_loop({"what": "has dinner plans", "due_date": TODAY, "kind": "social"}, TODAY_DATE)


def test_the_same_thing_on_the_same_day_is_a_duplicate_but_a_different_day_is_not():
    mine = {"text": "has a first date", "due_date": "2026-10-10"}
    assert ol.is_duplicate(mine, [{"text": "has a date tomorrow night", "due_date": "2026-10-10"}])
    assert not ol.is_duplicate(mine, [{"text": "has a first date", "due_date": "2026-10-11"}])
    assert not ol.is_duplicate(mine, [{"text": "is giving a presentation", "due_date": "2026-10-10"}])


# ---- storage -------------------------------------------------------------------------------------------------------

def test_saved_loops_have_an_owner_a_status_and_a_ttl_a_week_after_their_day():
    db = _DB()
    saved = ol.save_loops(db, "u", [{"text": "has a first date", "due_date": "2026-10-10", "kind": "social"}], NOW)
    assert saved == 1
    doc = db.col.docs[0]
    assert doc["username"] == "u" and doc["status"] == ol.OPEN and doc["asked_at"] is None and doc["id"]
    assert doc["expires_at"] == datetime(2026, 10, 18, tzinfo=timezone.utc)  # due + 7 days + 1
    assert {field for field, _ in db.col.indexes} == {"expires_at", "username"}


def test_duplicates_are_not_saved_twice_and_the_per_user_cap_holds():
    db = _DB()
    one = {"text": "has a first date", "due_date": "2026-10-10", "kind": "social"}
    assert ol.save_loops(db, "u", [one], NOW) == 1
    assert ol.save_loops(db, "u", [dict(one, text="has a date")], NOW) == 0
    many = [{"text": f"has event number{i}", "due_date": f"2026-11-{i + 1:02d}", "kind": "event"} for i in range(20)]
    ol.save_loops(db, "u", many, NOW)
    assert len(ol.list_loops(db, "u")) == ol.MAX_OPEN_LOOPS


def test_loops_belong_to_one_user_and_only_open_ones_are_listed_by_default():
    db = _DB()
    ol.save_loops(db, "u", [{"text": "has an interview", "due_date": "2026-10-10", "kind": "work"}], NOW)
    ol.save_loops(db, "other", [{"text": "has a trip", "due_date": "2026-10-10", "kind": "travel"}], NOW)
    mine = ol.list_loops(db, "u")
    assert [d["text"] for d in mine] == ["has an interview"]
    assert ol.set_status(db, "u", mine[0]["id"], ol.ASKED, NOW)
    assert ol.list_loops(db, "u") == []
    assert len(ol.list_loops(db, "u", (ol.ASKED,))) == 1


def test_one_user_cannot_change_or_delete_another_users_loop():
    db = _DB()
    ol.save_loops(db, "u", [{"text": "has an interview", "due_date": "2026-10-10", "kind": "work"}], NOW)
    loop_id = db.col.docs[0]["id"]
    assert not ol.set_status(db, "other", loop_id, ol.RESOLVED, NOW)
    assert not ol.delete_loop(db, "other", loop_id)
    assert ol.delete_loop(db, "u", loop_id)
    assert db.col.docs == []


def test_delete_all_removes_only_that_users_loops():
    db = _DB()
    ol.save_loops(db, "u", [{"text": "has an interview", "due_date": "2026-10-10", "kind": "work"}], NOW)
    ol.save_loops(db, "other", [{"text": "has a trip", "due_date": "2026-10-10", "kind": "travel"}], NOW)
    assert ol.delete_all_loops(db, "u") == 1
    assert [d["username"] for d in db.col.docs] == ["other"]


def test_the_page_view_exposes_no_internal_fields():
    assert set(ol.public_view({**loop(), "expires_at": 1, "created_at": "x", "username": "u"})) == {"id", "text", "due_date", "kind", "status"}


# ---- choosing a follow-up ------------------------------------------------------------------------------------------

BASE = dict(today=TODAY, now=NOW, last_message_at=None, source_type="conversational", risk_active=False, energy_tier="open", closing=False)


def pick(loops, **overrides):
    return ol.select_followup(lambda: loops, **{**BASE, **overrides})


def test_a_loop_whose_day_has_passed_is_offered_at_the_start_of_a_conversation():
    assert pick([loop(due="2026-10-08")])["id"] == "a1"


def test_a_loop_is_not_offered_on_its_own_day_or_before_it():
    assert pick([loop(due=TODAY)]) is None
    assert pick([loop(due="2026-10-10")]) is None


def test_a_loop_expires_a_week_after_its_day():
    assert pick([loop(due="2026-10-02")]) is not None  # exactly 7 days
    assert pick([loop(due="2026-10-01")]) is None


def test_only_an_open_loop_is_offered_so_an_asked_one_never_repeats():
    assert pick([loop(status=ol.ASKED)]) is None
    assert pick([loop(status=ol.RESOLVED)]) is None


def test_the_most_recent_loop_is_chosen_when_several_are_due():
    chosen = pick([loop(due="2026-10-05", id="old"), loop(due="2026-10-08", id="new")])
    assert chosen["id"] == "new"


@pytest.mark.parametrize("overrides", [
    {"risk_active": True},
    {"closing": True},
    {"energy_tier": "subdued"},
    {"energy_tier": "easing"},
    {"source_type": "kb_strict"},
    {"source_type": "web"},
    {"source_type": "tool_output"},
])
def test_it_stays_quiet_under_safety_a_held_down_mood_a_closing_or_a_non_conversational_route(overrides):
    assert pick([loop()], **overrides) is None


def test_it_is_not_offered_mid_conversation_but_is_after_a_real_pause():
    assert pick([loop()], last_message_at=NOW - timedelta(minutes=10)) is None
    assert pick([loop()], last_message_at=NOW - timedelta(hours=1, minutes=59)) is None
    assert pick([loop()], last_message_at=NOW - timedelta(hours=2)) is not None
    assert pick([loop()], last_message_at=NOW - timedelta(days=1)) is not None


def test_the_store_is_not_even_read_when_a_cheap_gate_already_says_no():
    calls = []
    for overrides in ({"risk_active": True}, {"closing": True}, {"energy_tier": "subdued"}, {"source_type": "web"},
                      {"last_message_at": NOW - timedelta(minutes=5)}):
        ol.select_followup(lambda: calls.append(1) or [loop()], **{**BASE, **overrides})
    assert calls == []


def test_a_failing_store_means_no_follow_up_not_an_error():
    def boom():
        raise RuntimeError("db down")

    assert ol.select_followup(boom, **BASE) is None


def test_a_loop_with_a_broken_date_is_ignored():
    assert pick([loop(due="not-a-date")]) is None


# ---- the prompt block ----------------------------------------------------------------------------------------------

def test_the_block_asks_once_defers_to_the_question_and_forbids_a_claim_of_waiting():
    block = ol.build_open_loop_block(loop(), TODAY)
    assert "OPEN LOOP" in block and "has a first date" in block and "yesterday" in block
    assert "ask once" in block and "Answer what they actually asked first" in block
    assert "already told you how it went, don't ask" in block
    for forbidden in ("thinking about them", "waiting", "wondering", "looking forward"):
        assert forbidden in block  # named so the model knows not to say them
    assert "keep track" in block
    assert ol.build_open_loop_block(None, TODAY) == ""


def test_the_day_is_described_the_way_a_person_would():
    assert ol.describe_when("2026-10-08", TODAY) == "yesterday"
    assert ol.describe_when("2026-10-06", TODAY) == "on Tuesday, October 6"
    assert ol.describe_when("garbage", TODAY) == "recently"


# ---- parsing -------------------------------------------------------------------------------------------------------

def test_the_models_json_is_parsed_even_with_fences_or_chatter():
    assert ol.parse_extraction('```json\n{"loops": [], "resolved_ids": []}\n```') == {"loops": [], "resolved_ids": []}
    assert ol.parse_extraction('Sure! {"loops": [{"what": "x"}]} hope that helps')["loops"][0]["what"] == "x"
    assert ol.parse_extraction("no json here") == {}
    assert ol.parse_extraction("{broken") == {}
    assert ol.parse_extraction("") == {}


# ---- capture -------------------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, content):
        self.content = content


def run_capture(message, answer, db=None, **kwargs):
    db = db or _DB()
    calls = []

    async def invoke(prompt):
        calls.append(prompt)
        if isinstance(answer, Exception):
            raise answer
        return _Resp(answer)

    result = asyncio.run(ol.capture_open_loops(
        "u", message, TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db, **kwargs,
    ))
    return result, db, calls


def test_a_future_message_is_captured_with_its_resolved_date(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    result, db, calls = run_capture(
        "i have a big date tomorrow night",
        '{"loops": [{"what": "has a first date", "due_date": "2026-10-10", "kind": "social"}], "resolved_ids": []}',
    )
    assert result == {"captured": 1, "resolved": 0}
    assert db.col.docs[0]["due_date"] == "2026-10-10"
    # The model is told the user's own calendar date and weekday so it never does that arithmetic blind.
    assert "2026-10-09" in calls[0] and "Friday" in calls[0]


def test_a_message_with_no_time_language_never_reaches_the_model_or_the_store():
    db = _DB()
    result, _, calls = run_capture("how do i fix this null pointer", "{}", db=db)
    assert result == {"captured": 0, "resolved": 0} and calls == [] and db.col.docs == []


def test_a_sensitive_message_is_never_captured():
    result, db, calls = run_capture("i have a doctor's appointment tomorrow", '{"loops": []}')
    assert result["captured"] == 0 and calls == [] and db.col.docs == []


def test_a_sensitive_proposal_from_the_model_is_dropped_even_if_the_message_looked_fine(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    result, db, _ = run_capture(
        "i have something on friday",
        '{"loops": [{"what": "has a hospital visit", "due_date": "2026-10-10", "kind": "event"},'
        ' {"what": "has a thing", "due_date": "2026-10-10", "kind": "sensitive"}]}',
    )
    assert result["captured"] == 0 and db.col.docs == []


def test_nothing_is_captured_while_a_safety_context_is_active():
    result, db, calls = run_capture("i have an interview tomorrow", '{"loops": []}', risk_active=True)
    assert result["captured"] == 0 and calls == [] and db.col.docs == []


def test_the_kill_switch_turns_the_whole_thing_off(monkeypatch):
    monkeypatch.setenv("OPEN_LOOPS_ENABLED", "false")
    assert not ol.open_loops_enabled()
    result, db, calls = run_capture("i have an interview tomorrow", '{"loops": []}')
    assert calls == [] and db.col.docs == []


def test_reporting_on_a_due_loop_closes_it_without_asking_later(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    db = _DB()
    db.col.insert_one(loop(due="2026-10-08", id="abc", text="has a first date") | {"username": "u"})
    result, _, calls = run_capture("the date went great, we got dinner", '{"loops": [], "resolved_ids": ["abc", "not-mine"]}', db=db)
    assert result == {"captured": 0, "resolved": 1}
    assert db.col.docs[0]["status"] == ol.RESOLVED
    assert "abc: has a first date" in calls[0]


def test_a_past_tense_message_without_a_due_loop_costs_nothing():
    result, _, calls = run_capture("the date went great", '{"loops": []}')
    assert calls == []


def test_a_model_failure_or_garbage_never_raises_and_stores_nothing():
    result, db, _ = run_capture("interview tomorrow", RuntimeError("model down"))
    assert result == {"captured": 0, "resolved": 0} and db.col.docs == []
    result, db, _ = run_capture("interview tomorrow", "not json at all")
    assert result["captured"] == 0 and db.col.docs == []


def test_no_database_means_nothing_happens():
    async def invoke(prompt):
        raise AssertionError("the model must not be called without a store")

    result = asyncio.run(ol.capture_open_loops("u", "interview tomorrow", TZ, now=NOW, invoke=invoke, get_db_fn=lambda: None))
    assert result == {"captured": 0, "resolved": 0}


def test_at_most_three_proposals_are_considered_from_one_message(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    loops = ",".join(f'{{"what": "has event number{i} thing", "due_date": "2026-10-1{i}", "kind": "event"}}' for i in range(6))
    result, _, _ = run_capture("a busy week: monday, tuesday, wednesday...", '{"loops": [' + loops + ']}')
    assert result["captured"] == 3


# ---- the wording backstop ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "I was wondering how your date went!",
    "I've been wondering how the interview went.",
    "I've been thinking about your interview.",
    "I was thinking about the presentation all week.",
])
def test_a_follow_up_that_claims_it_was_thinking_about_them_is_flagged(reply):
    assert identity_reply_issue(reply) is not None, reply


@pytest.mark.parametrize("reply", [
    "How did the date go?",
    "Hey, how did the interview end up going?",
    "Last week you mentioned a presentation. How did it go?",
    "I was wondering how it compares to the old version.",
    "I'm curious how the migration works under the hood.",
    "How did Friday go?",
])
def test_an_ordinary_how_did_it_go_is_fine(reply):
    assert identity_reply_issue(reply) is None, reply


# ---- wiring --------------------------------------------------------------------------------------------------------

def _app_source():
    return (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


def test_the_app_selects_the_loop_offers_it_counts_it_marks_it_asked_and_captures_after_the_reply():
    source = _app_source()
    for needle in (
        "open_loops_utils.select_followup",
        "open_loop_context=open_loops_utils.build_open_loop_block(open_loop_entry, today_local)",
        'tally(turn_counts, "open_loop_offered")',
        "open_loops_utils.mark_asked",
        "open_loops_utils.capture_open_loops(",
        "relatable_entry = None if open_loop_entry else select_relatable(",
        "risk_level != RISK_NONE or open_loop_entry",
        "is_guest_username(username)",
    ):
        assert needle in source, needle


def test_encouragement_takes_the_turns_one_proactive_slot_over_a_loop():
    source = _app_source()
    encouragement_at = source.index('tally(turn_counts, "encouragement_offered")')
    assert "open_loop_entry = None" in source[encouragement_at: encouragement_at + 300]


def test_forgetting_everything_also_forgets_the_loops_and_the_endpoints_require_a_signed_in_user():
    source = _app_source()
    clear = source[source.index("async def clear_memory"): source.index('@app.get("/api/open-loops")')]
    assert "forget_all_loops" in clear
    for route in ('@app.get("/api/open-loops")', '@app.delete("/api/open-loops/{loop_id}")'):
        start = source.index(route)
        assert "Depends(get_current_user)" in source[start: start + 200], route


def test_the_counters_know_the_new_keys_and_the_voice_prompt_carries_the_block():
    from backend.components.constraints import build_voice_prompt
    from backend.utils.self_counters import KEYS

    assert {"open_loop_captured", "open_loop_offered", "open_loop_resolved"} <= KEYS
    prompt = build_voice_prompt("G", "d", "h", "q", open_loop_context="\nOPEN LOOP (optional, once): x\n")
    assert "OPEN LOOP (optional, once): x" in prompt
    assert "OPEN LOOP" not in build_voice_prompt("G", "d", "h", "q")
