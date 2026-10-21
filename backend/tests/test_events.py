"""Events: what an upcoming thing may carry (a time, who with, where), known versus NOT KNOWN, corrections, and what the reply is told
(backend/utils/event_fields.py, backend/utils/open_loops.py; docs/memory-grounding-plan.md step 3)."""
import asyncio
import json
from datetime import date, datetime, timezone

import pytest

from backend.utils import event_fields as ef
from backend.utils import open_loops as ol

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)          # Wednesday 2:00 PM in New York
TODAY = date(2026, 10, 21)
TZ = "America/New_York"


# ------------------------------------------------------------------------ an in-memory collection ----------------------

class _Result:
    def __init__(self, matched=0, deleted=0):
        self.matched_count, self.deleted_count = matched, deleted


class _Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

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
def _fresh(monkeypatch):
    monkeypatch.setattr(ol, "_index_ready", False)
    monkeypatch.delenv("OPEN_LOOPS_ENABLED", raising=False)
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None)
    ol._events_cache.clear()


class _Resp:
    def __init__(self, content):
        self.content = content


def capture(message, loops, db=None, previous_reply="", extra=None):
    db = db or _DB()
    answer = json.dumps({"loops": loops, "resolved_ids": [], "cancelled_ids": [], **(extra or {})})

    async def invoke(prompt):
        return _Resp(answer)

    result = asyncio.run(ol.capture_open_loops("u", message, TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db, previous_reply=previous_reply))
    return result, db


DATE_TONIGHT = {"what": "has a date", "due_date": "2026-10-21", "kind": "social", "time_said": "", "with": "", "where": ""}


def event(fields=None, **over):
    return {"id": "e1", "username": "u", "text": "has a date", "due_date": "2026-10-21", "kind": "social", "status": "open", "fields": fields or {}, **over}


# ------------------------------------------------------------------------ the time ------------------------------------

@pytest.mark.parametrize("said,kind,context,value,assumed,ambiguous", [
    ("7pm", "work", "", "19:00", None, False),
    ("7:30 PM", "work", "", "19:30", None, False),
    ("7am", "social", "", "07:00", None, False),
    ("12am", "work", "", "00:00", None, False),
    ("12pm", "work", "", "12:00", None, False),
    ("19:00", "work", "", "19:00", None, False),
    ("noon", "work", "", "12:00", None, False),
    ("7", "work", "dinner tonight at 7", "19:00", None, False),              # the words around it settle it
    ("9", "work", "breakfast meeting at 9", "09:00", None, False),
    ("7", "social", "i have a date at 7", "19:00", "pm", False),                # Jack's decision: a bare hour for a social plan is assumed PM, and says so
    ("7:30", "social", "date at 7:30", "19:30", "pm", False),
    ("7", "work", "a call at 7", None, None, True),                            # no am/pm and not social: kept as said, never turned into a clock time
    ("12", "social", "lunch date at 12", None, None, True),
])
def test_a_time_is_turned_into_a_clock_time_only_when_that_is_certain(said, kind, context, value, assumed, ambiguous):
    parsed = ef.parse_time(said, kind, context)
    assert (parsed["value"], parsed["assumed"], parsed["ambiguous"]) == (value, assumed, ambiguous) and parsed["said"] == said


@pytest.mark.parametrize("said", ["", "soon", "evening", "25", "7:75", "13pm", "seven"])
def test_something_that_is_not_a_time_is_not_one(said):
    assert ef.parse_time(said, "social") is None


def test_the_time_is_said_the_way_it_was_understood():
    assert ef.describe_time({"value": "19:00", "said": "7pm", "ambiguous": False, "assumed": None}) == "7 PM"
    assert ef.describe_time({"value": "19:30", "said": "7:30", "ambiguous": False, "assumed": "pm"}) == "7:30 PM (they said 7:30; PM assumed for a social plan)"
    assert ef.describe_time({"value": None, "said": "7", "ambiguous": True, "assumed": None}) == "7 (they did not say AM or PM)"
    assert ef.describe_time(None) == "NOT KNOWN"


# ------------------------------------------------------------------------ values must be the person's own ---------------

def test_only_what_the_person_said_becomes_a_field_and_the_rest_stays_unknown():
    raw = {"time_said": "7", "with": "Ashlyn", "where": "Tidos"}
    fields = ef.grounded_fields(raw, kind="social", user_text="i have a date with ashlyn tonight at tidos")
    assert set(fields) == {"with", "where"}                                      # "7" was never said, so the time is NOT KNOWN
    assert fields["with"] == {"value": "Ashlyn", "source": "user"} and fields["where"]["source"] == "user"
    full = ef.grounded_fields(raw, kind="social", user_text="the date with ashlyn is at 7 at tidos")
    assert full["time"]["value"] == "19:00" and full["time"]["assumed"] == "pm" and full["time"]["source"] == "user"


def test_a_detail_the_assistant_introduced_is_dropped_but_one_the_person_confirmed_counts_as_confirmed():
    raw = {"time_said": "7", "with": "Ashlyn", "where": "Tidos"}
    invented = ef.grounded_fields(raw, kind="social", user_text="date with ashlyn tonight", previous_reply="Maybe 7 at Tidos?")
    assert set(invented) == {"with"}                                             # "yes" was not said, so nothing from the reply counts
    confirmed = ef.grounded_fields(raw, kind="social", user_text="yep", previous_reply="So the date is at 7 at Tidos, right?")
    assert confirmed["time"]["source"] == "assistant_confirmed" and confirmed["where"]["source"] == "assistant_confirmed"
    assert "with" not in confirmed                                               # Ashlyn is in neither


@pytest.mark.parametrize("value", [
    "1420 Maple Street", "55 Oak Ave", "Apt 4B", "see https://x.example", "me@x.com", "the hospital", "her", "x" * 61, "",
])
def test_an_address_a_link_something_sensitive_or_a_bare_pronoun_is_never_kept(value):
    assert ef.clean_value(value, ef.MAX_WHERE_CHARS) is None


def test_a_name_typed_in_lower_case_is_shown_as_a_name_and_a_description_is_left_alone():
    assert ef.clean_value("ashlyn", ef.MAX_WITH_CHARS) == "Ashlyn"
    assert ef.clean_value("tidos bar", ef.MAX_WHERE_CHARS) == "Tidos Bar"
    assert ef.clean_value("my mom", ef.MAX_WITH_CHARS) == "my mom" and ef.clean_value("the bar", ef.MAX_WHERE_CHARS) == "the bar"
    assert ef.clean_value("Ashlynn", ef.MAX_WITH_CHARS) == "Ashlynn"                      # already cased: untouched


def test_the_noted_line_does_not_say_who_it_is_with_twice_and_speaks_to_the_person():
    dinner = event({"with": {"value": "my mom", "source": "user"}}, text="has dinner with their mother", due_date="2026-10-22")
    assert ef.noted_line(dinner, TODAY) == "Noted: you have dinner with your mother tomorrow, time not known."
    assert ef.noted_line(event({"with": {"value": "Ashlyn", "source": "user"}}), TODAY, updated=True) == "Updated: you have a date today with Ashlyn, time not known."


def test_a_venue_a_neighbourhood_and_a_name_are_kept_and_tidied():
    assert ef.clean_value("  Tidos \n bar ", ef.MAX_WHERE_CHARS) == "Tidos bar"
    assert ef.clean_value("Studio 54", ef.MAX_WHERE_CHARS) == "Studio 54"
    assert ef.clean_value("my sister", ef.MAX_WITH_CHARS) == "my sister"
    assert ef.clean_value("a" * 41, ef.MAX_WITH_CHARS) is None


# ------------------------------------------------------------------------ fill and correct ----------------------------

def test_a_missing_detail_is_filled_and_a_different_one_is_a_correction_that_the_persons_words_win():
    old = {"time": {"value": "18:00", "said": "6", "ambiguous": False, "assumed": None, "source": "calendar"}, "where": {"value": "Tidos", "source": "user"}}
    new = {"time": {"value": "19:00", "said": "7pm", "ambiguous": False, "assumed": None, "source": "user"}, "with": {"value": "Ashlyn", "source": "user"},
           "where": {"value": "tidos", "source": "user"}}
    merged, changes = ef.merge_fields(old, new)
    assert sorted(changes) == [("corrected", "time"), ("filled", "with")]                      # "tidos" is the same place, so nothing changes there
    assert merged["time"]["value"] == "19:00" and merged["time"]["source"] == "user" and merged["with"]["value"] == "Ashlyn"
    assert old["time"]["value"] == "18:00"                                                      # the inputs are not modified


def test_a_calendar_value_never_replaces_what_the_person_said():
    merged, changes = ef.merge_fields({"where": {"value": "Tidos", "source": "user"}}, {"where": {"value": "Downtown", "source": "calendar"}})
    assert changes == [] and merged["where"]["value"] == "Tidos"


# ------------------------------------------------------------------------ capture and storage ------------------------

def test_a_message_with_a_date_and_details_is_saved_with_each_detail_and_where_it_came_from():
    result, db = capture("i have a date with ashlyn tonight at tidos", [{**DATE_TONIGHT, "with": "Ashlyn", "where": "Tidos", "time_said": "7"}])
    stored = db.col.docs[0]
    assert set(stored["fields"]) == {"with", "where"} and stored["fields"]["with"] == {"value": "Ashlyn", "source": "user"}
    assert result["noted"] == ["Noted: you have a date today with Ashlyn at Tidos, time not known."]      # the invented "7" is gone; the line says the time is not known


def test_a_later_message_that_fixes_the_time_updates_the_same_event_and_says_so():
    first, db = capture("i have a date with ashlyn tonight at tidos", [{**DATE_TONIGHT, "with": "Ashlyn", "where": "Tidos"}])
    assert len(db.col.docs) == 1
    second, _ = capture("lmao no the date is at 7 silly", [{**DATE_TONIGHT, "time_said": "7"}], db=db)
    assert len(db.col.docs) == 1                                                                 # one event, not two
    stored = db.col.docs[0]
    assert stored["fields"]["time"]["value"] == "19:00" and stored["fields"]["time"]["assumed"] == "pm" and stored["fields"]["with"]["value"] == "Ashlyn"
    assert second["noted"] == ["Updated: you have a date today with Ashlyn at Tidos, 7 PM (assumed)."]
    third, _ = capture("sorry it's at 8 actually", [{**DATE_TONIGHT, "time_said": "8"}], db=db)
    assert db.col.docs[0]["fields"]["time"]["value"] == "20:00" and db.col.docs[0]["corrected_at"] == NOW.isoformat()


def test_two_things_on_the_same_day_with_different_people_are_two_events():
    _, db = capture("i have a date with ashlyn tonight", [{**DATE_TONIGHT, "with": "Ashlyn"}])
    capture("also dinner with sam tonight", [{**DATE_TONIGHT, "what": "has dinner plans", "with": "Sam"}], db=db)
    assert [d["fields"]["with"]["value"] for d in db.col.docs] == ["Ashlyn", "Sam"]


def test_an_event_the_person_says_fell_through_is_cancelled_and_never_asked_about():
    _, db = capture("i have a date with ashlyn tonight", [{**DATE_TONIGHT, "with": "Ashlyn"}])
    event_id = db.col.docs[0]["id"]
    result, _ = capture("the date fell through", [], db=db, extra={"cancelled_ids": [event_id]})
    assert db.col.docs[0]["status"] == ol.CANCELLED and result["noted"] == ["Noted: cancelled, you have a date."]
    assert ol.select_followup(lambda: ol.list_loops(db, "u", (ol.OPEN,)), today="2026-10-23", now=NOW, last_message_at=None, source_type="conversational",
                              risk_active=False, energy_tier="open", closing=False) is None


def test_a_correction_message_with_no_future_word_still_reaches_the_model_when_something_is_coming_up():
    _, db = capture("i have a date with ashlyn tonight", [{**DATE_TONIGHT, "with": "Ashlyn"}])
    calls = []

    async def invoke(prompt):
        calls.append(prompt)
        return _Resp('{"loops": []}')

    asyncio.run(ol.capture_open_loops("u", "no, it's at 7", TZ, now=NOW, invoke=invoke, get_db_fn=lambda: db))
    assert len(calls) == 1 and "date with" not in calls[0].lower().split("user message")[1]
    assert "with Ashlyn" in calls[0]                                                              # the model is shown what is known, to tell a detail from a new thing
    quiet_db = _DB()
    asyncio.run(ol.capture_open_loops("u", "no, it's at 7", TZ, now=NOW, invoke=invoke, get_db_fn=lambda: quiet_db))
    assert len(calls) == 1                                                                         # nothing coming up: no model call for a bare "at 7"


def test_a_sensitive_place_or_an_address_leaves_that_field_unknown_and_keeps_the_event():
    _, db = capture("i have a date tonight at the hospital", [{**DATE_TONIGHT, "where": "the hospital"}])
    assert db.col.docs == [] or "where" not in db.col.docs[0]["fields"]
    _, db2 = capture("dinner at 1420 Maple Street tonight", [{**DATE_TONIGHT, "what": "has dinner", "where": "1420 Maple Street"}])
    assert "where" not in db2.col.docs[0]["fields"]


def test_an_old_loop_without_fields_still_works_everywhere():
    old = {"id": "o1", "username": "u", "text": "has a first date", "due_date": "2026-10-21", "kind": "social", "status": "open"}
    assert "Time: NOT KNOWN" in ef.build_events_block([old], TODAY)
    assert ol.public_view(old)["fields"] == {}


# ------------------------------------------------------------------------ what the reply is told ---------------------

def test_the_reply_is_told_what_is_known_and_that_the_time_is_not():
    block = ef.build_events_block([event({"with": {"value": "Ashlyn", "source": "user"}, "where": {"value": "Tidos", "source": "user"}})], TODAY)
    assert "Today, Wednesday 2026-10-21: the user has a date." in block
    assert "With: Ashlyn (you said). Where: Tidos (you said). Time: NOT KNOWN." in block
    assert "never name a time, place or person that is not listed" in block.lower() or "Never name a time, place or person that is not listed" in block


def test_a_confirmed_assumed_and_unclear_time_are_each_said_as_what_they_are():
    confirmed = ef.event_line(event({"time": {"value": "19:00", "said": "7", "ambiguous": False, "assumed": None, "source": "assistant_confirmed"}}), TODAY)
    assert "Time: 7 PM (you confirmed)." in confirmed
    assumed = ef.event_line(event({"time": {"value": "19:00", "said": "7", "ambiguous": False, "assumed": "pm", "source": "user"}}), TODAY)
    assert "PM assumed for a social plan" in assumed
    unclear = ef.event_line(event({"time": {"value": None, "said": "7", "ambiguous": True, "assumed": None, "source": "user"}}), TODAY)
    assert "7 (they did not say AM or PM)" in unclear


def test_only_open_events_in_the_next_day_or_two_are_shown_and_a_week_when_asked():
    events = [
        event(id="today", due_date="2026-10-21"), event(id="tomorrow", due_date="2026-10-22"), event(id="later", due_date="2026-10-25"),
        event(id="past", due_date="2026-10-20"), event(id="gone", due_date="2026-10-21", status="cancelled"), event(id="done", due_date="2026-10-21", status="asked"),
    ]
    assert [e["id"] for e in ef.events_for_reply(events, TODAY)] == ["today", "tomorrow"]
    assert [e["id"] for e in ef.events_for_reply(events, TODAY, wide=True)] == ["today", "tomorrow", "later"]
    assert ef.build_events_block([], TODAY) == "" and ef.build_events_block([event(status="cancelled")], TODAY) == ""


def test_at_most_three_events_are_shown():
    many = [event(id=str(i), due_date="2026-10-21") for i in range(6)]
    assert len(ef.events_for_reply(many, TODAY)) == ef.MAX_EVENTS_IN_BLOCK


@pytest.mark.parametrize("message", ["what do i have tonight?", "what's on tomorrow", "what's my schedule this week", "do i have anything planned", "what time is my date?", "my plans for today"])
def test_a_question_about_their_plans_widens_what_is_shown(message):
    assert ef.asks_about_plans(message)


@pytest.mark.parametrize("message", ["how do i fix this bug", "what is a closure", "tell me a joke"])
def test_other_questions_do_not(message):
    assert not ef.asks_about_plans(message)


# ------------------------------------------------------------------------ the reply check ---------------------------

def test_a_reply_that_names_a_time_for_an_event_with_no_known_time_is_caught_but_asking_is_fine():
    unknown = [event()]
    assert ef.reply_issue("You should nap and head downtown by 7:30.", unknown, "i have a date tonight") is not None
    assert ef.reply_issue("Dinner at 7 sounds right.", unknown, "hi") is not None
    assert ef.reply_issue("Do you know what time the date is, is it at 7?", unknown, "hi") is None        # a question is how a missing time gets filled
    assert ef.reply_issue("You said 7pm, so leave by 6:30?", unknown, "the date is at 7pm") is None       # 6:30 is inside a question; 7pm was said


def test_a_time_the_person_just_said_or_that_another_event_has_is_not_an_invention():
    unknown = [event(), event(id="e2", text="has a meeting", fields={"time": {"value": "15:00", "said": "3", "ambiguous": False, "assumed": None, "source": "user"}})]
    assert ef.reply_issue("Your meeting is at 3.", unknown, "hi") is None
    assert ef.reply_issue("7pm it is then.", unknown, "the date is at 7pm") is None


def test_with_every_time_known_nothing_is_checked():
    known = [event({"time": {"value": "19:00", "said": "7pm", "ambiguous": False, "assumed": None, "source": "user"}})]
    assert ef.reply_issue("Be there by 9.", known, "hi") is None


# ------------------------------------------------------------------------ the follow-up and the cache ----------------

def test_the_follow_up_may_use_a_name_the_person_gave():
    loop = event({"with": {"value": "Ashlyn", "source": "user"}, "where": {"value": "Tidos", "source": "user"}}, due_date="2026-10-20")
    block = ol.build_open_loop_block(loop, "2026-10-21")
    assert "with Ashlyn, at Tidos" in block and "you may use these names" in block
    assert "with" not in ol.build_open_loop_block(event(due_date="2026-10-20"), "2026-10-21").split('"has a date"')[1].split(".")[0]


def test_the_list_is_kept_for_a_minute_and_dropped_when_something_changes(monkeypatch):
    db = _DB()
    reads = []
    monkeypatch.setattr(ol, "load_open_loops", lambda username: reads.append(username) or [event()])
    assert ol.load_open_loops_cached("u") and ol.load_open_loops_cached("u") and reads == ["u"]
    ol.save_events(db, "u", [{"loop": {"text": "has a call", "due_date": "2026-10-22", "kind": "work"}, "fields": {}}], NOW)
    ol.load_open_loops_cached("u")
    assert reads == ["u", "u"]
    ol.delete_loop(db, "u", "nothing")
    ol.load_open_loops_cached("u")
    assert reads == ["u", "u", "u"]


# ------------------------------------------------------------------------ the person edits an event (Memory page) ---------

def _stored(db=None, message="i have a date with ashlyn tonight at tidos", **fields):
    db = db or _DB()
    capture(message, [{**DATE_TONIGHT, "with": "Ashlyn", "where": "Tidos", **fields}], db=db)
    return db, db.col.docs[0]["id"]


def edit(db, event_id, **changes):
    return ol.edit_event(db, "u", event_id, changes, NOW, TODAY)


def test_a_time_they_type_is_kept_as_theirs_and_cannot_be_a_bare_hour():
    db, event_id = _stored()
    shown = edit(db, event_id, time="7 PM")
    assert shown["fields"]["time"]["value"] == "19:00" and shown["fields"]["time"]["source"] == "user_edit" and shown["fields"]["time"]["display"] == "7 PM"
    assert edit(db, event_id, time="20:30")["fields"]["time"]["display"] == "8:30 PM"          # what a time box on the page sends
    for bad, message in (("7", "AM or PM"), ("12", "AM or PM"), ("soon", "isn't a time"), ("25:00", "isn't a time")):
        with pytest.raises(ol.EventEditError, match=message):
            edit(db, event_id, time=bad)
    assert db.col.docs[0]["fields"]["time"]["value"] == "20:30"                                # a refused edit changed nothing


def test_clearing_a_detail_takes_it_back_to_not_known():
    db, event_id = _stored(message="i have a date with ashlyn tonight at tidos at 7", time_said="7")
    assert "time" in db.col.docs[0]["fields"]
    shown = edit(db, event_id, time="", where=None)
    assert set(shown["fields"]) == {"with"} and db.col.docs[0].get("corrected_at") is None    # clearing is not a correction
    assert "Time: NOT KNOWN" in ef.event_line(ol.list_loops(db, "u")[0], TODAY)


def test_changing_who_it_is_with_marks_a_correction_and_what_is_written_is_labelled_as_theirs():
    db, event_id = _stored()
    shown = edit(db, event_id, **{"with": "Sam"})
    assert shown["fields"]["with"]["value"] == "Sam" and shown["fields"]["with"]["source"] == "user_edit" and shown["corrected_at"] == NOW.isoformat()
    assert "With: Sam (you wrote this)" in ef.event_line(ol.list_loops(db, "u")[0], TODAY)           # and the reply is told it that way


@pytest.mark.parametrize("field,value,message", [
    ("where", "1420 Maple Street", "street addresses"), ("where", "https://x.example", "links"), ("with", "my therapist", "health, legal"),
    ("with", "her", "name"), ("where", "x" * 61, "under 60"),
])
def test_a_value_that_cannot_be_kept_is_refused_with_the_reason(field, value, message):
    db, event_id = _stored()
    before = dict(db.col.docs[0])
    with pytest.raises(ol.EventEditError, match=message):
        edit(db, event_id, **{field: value})
    assert db.col.docs[0] == before


def test_the_day_can_be_moved_within_the_next_two_months_and_the_expiry_moves_with_it():
    db, event_id = _stored()
    shown = edit(db, event_id, due_date="2026-10-24")
    assert shown["due_date"] == "2026-10-24" and db.col.docs[0]["expires_at"].date() == date(2026, 11, 1)
    for bad in ("2026-10-20", "2027-03-01", "tomorrow"):
        with pytest.raises(ol.EventEditError):
            edit(db, event_id, due_date=bad)


def test_an_event_can_be_cancelled_and_restored_and_one_already_asked_about_cannot():
    db, event_id = _stored()
    assert edit(db, event_id, status="cancelled")["status"] == "cancelled"
    assert ef.events_for_reply(ol.list_loops(db, "u", (ol.OPEN, ol.CANCELLED)), TODAY) == []         # the reply is never told about it
    assert edit(db, event_id, status="open")["status"] == "open"
    ol.set_status(db, "u", event_id, ol.ASKED, NOW)
    with pytest.raises(ol.EventEditError, match="already asked"):
        edit(db, event_id, status="cancelled")
    with pytest.raises(ol.EventEditError):
        edit(db, event_id, status="resolved")


def test_checking_that_it_is_right_is_recorded_and_changes_nothing_else():
    db, event_id = _stored()
    before = {k: v for k, v in db.col.docs[0].items() if k != "confirmed_at"}
    shown = edit(db, event_id, confirm=True)
    assert shown["confirmed_at"] == NOW.isoformat() and {k: v for k, v in db.col.docs[0].items() if k != "confirmed_at"} == before


def test_an_unknown_event_is_none_and_an_empty_edit_writes_nothing(monkeypatch):
    db, event_id = _stored()
    assert edit(db, "no-such-id", time="7 PM") is None
    writes = []
    monkeypatch.setattr(db.col, "update_one", lambda *a, **k: writes.append(a))
    edit(db, event_id)
    assert writes == []


def test_one_persons_edit_cannot_touch_anothers_event():
    db, event_id = _stored()
    assert ol.edit_event(db, "someone-else", event_id, {"with": "Sam"}, NOW, TODAY) is None
    assert db.col.docs[0]["fields"]["with"]["value"] == "Ashlyn"


def test_an_edit_drops_the_cached_list_so_the_next_message_sees_it(monkeypatch):
    db, event_id = _stored()
    monkeypatch.setattr(ol, "load_open_loops", lambda username: ol.list_loops(db, username))
    assert ol.load_open_loops_cached("u")[0]["fields"]["with"]["value"] == "Ashlyn"
    edit(db, event_id, **{"with": "Sam"})
    assert ol.load_open_loops_cached("u")[0]["fields"]["with"]["value"] == "Sam"


def test_the_page_lists_what_is_coming_up_and_what_was_cancelled(monkeypatch):
    db, event_id = _stored()
    capture("dinner with sam tomorrow", [{**DATE_TONIGHT, "what": "has dinner plans", "due_date": "2026-10-22", "with": "Sam"}], db=db)
    edit(db, event_id, status="cancelled")
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    assert [(e["due_date"], e["status"]) for e in ol.upcoming_for_page("u")] == [("2026-10-21", "cancelled"), ("2026-10-22", "open")]


def test_the_edit_endpoint_is_for_signed_in_people_and_maps_the_errors():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    start = source.index('@app.patch("/api/open-loops/{loop_id}")')
    endpoint = source[start:source.index('@app.delete("/api/open-loops/{loop_id}")')]
    assert "Depends(get_current_user)" in endpoint and "is_guest_username(username)" in endpoint and "status_code=403" in endpoint
    assert "EventEditError" in endpoint and "status_code=400" in endpoint and "status_code=404" in endpoint and "asyncio.to_thread" in endpoint


def test_it_is_wired_into_the_reply_prompt_the_stream_and_the_reply_check():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "event_fields.build_events_block(" in source and "open_loops_utils.load_open_loops_cached" in source
    assert "event_fields.reply_issue(" in source and "'event': 'noted'" in source
