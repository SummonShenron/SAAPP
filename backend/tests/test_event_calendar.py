"""The calendar read-through for what is coming up (backend/utils/event_calendar.py): it fills a gap, settles an unclear time, and records a disagreement, with the person's words winning."""
import copy
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.utils import calendar_headsups as ch
from backend.utils import event_calendar as ec
from backend.utils import event_fields as ef

ROOT = pathlib.Path(__file__).resolve().parents[2]
TZ = "America/Chicago"
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)            # Sunday 10:00 AM in Chicago
DAY = "2026-10-18"


# ------------------------------------------------------------------------ a calendar and a database --------------------

def cal(id, title, start="19:00", location="", **extra):
    """A raw calendar event on DAY, local Chicago time (what the calendar read returns)."""
    if len(start) <= 5:
        start = f"{DAY}T{start}:00-05:00"
    end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    return {"id": id, "summary": title, "start": start, "end": end, "location": location, "status": "confirmed", "visibility": None, "event_type": "default",
            "recurring_event_id": None, "self_response": None, "has_attendees": False, "organizer_self": True, "creator_self": True, **extra}


def eligible(*raw):
    return ch.eligible_events(list(raw), NOW, TZ, {"enabled": True, "include_health": False, "discreet": False})


def stored(fields=None, text="has a date", id="e1", due=DAY, **extra):
    return {"id": id, "username": "u1", "text": text, "kind": "social", "due_date": due, "status": "open", "fields": fields or {}, **extra}


def said_time(value="19:00", said="7pm", **extra):
    return {"value": value, "said": said, "ambiguous": value is None, "assumed": None, "source": "user", **extra}


ASHLYN = {"with": {"value": "Ashlyn", "source": "user"}, "where": {"value": "Tidos", "source": "user"}}


class Col:
    def __init__(self):
        self.docs = []

    def find_one(self, flt=None, projection=None):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in (flt or {}).items())), None)

    def update_one(self, flt, update, upsert=False):
        self.docs.append({**flt, **update["$set"]})


class Db(dict):
    def __missing__(self, name):
        self[name] = Col()
        return self[name]


@pytest.fixture(autouse=True)
def _fresh():
    ch.clear_cache()


def switched_on():
    db = Db()
    ch.set_settings(db, "u1", {"enabled": True})
    return db


# ------------------------------------------------------------------------ what it can give ------------------------------

def test_a_timed_event_gives_its_local_time_and_a_place_and_an_all_day_one_gives_no_time():
    given = ec.calendar_fields(eligible(cal("a", "Dinner", "19:30", "Tidos"))[0], TZ)
    assert given["time"]["value"] == "19:30" and given["time"]["source"] == "calendar" and given["time"]["said"] == "7:30 PM" and given["where"] == {"value": "Tidos", "source": "calendar"}
    all_day = ec.calendar_fields(eligible(cal("b", "Conference", f"{DAY}", "Hall A"))[0], TZ)
    assert "time" not in all_day and all_day["where"]["value"] == "Hall A"


@pytest.mark.parametrize("location,place", [
    ("Tidos", "Tidos"), ("Tidos, 123 Main Street, Chicago", "Tidos"), ("123 Main Street", None), ("https://zoom.example/j/1", None), ("", None),
])
def test_a_street_address_or_link_is_never_given_as_a_place_but_the_venue_before_it_is(location, place):
    assert ec._place(location) == place


# ------------------------------------------------------------------------ which calendar event is the plan ---------------

def test_the_plan_is_matched_by_the_person_or_place_they_named_on_the_same_day():
    plans = [stored(ASHLYN)]
    events = eligible(cal("a", "Dinner with Ashlyn", "19:00"), cal("b", "Standup", "09:30"))
    assert ec.match_calendar_event(plans[0], plans, events, TZ)["id"] == "a"
    by_place = eligible(cal("c", "Drinks", "20:00", "Tidos"), cal("d", "Standup", "09:30"))
    assert ec.match_calendar_event(plans[0], plans, by_place, TZ)["id"] == "c"


def test_a_plan_is_never_matched_to_an_event_on_another_day_or_to_one_that_only_looks_alike():
    plans = [stored(ASHLYN)]
    tomorrow = eligible(cal("a", "Dinner with Ashlyn", f"2026-10-19T19:00:00-05:00"))
    assert ec.match_calendar_event(plans[0], plans, tomorrow, TZ) is None
    assert ec.match_calendar_event(plans[0], plans, eligible(cal("b", "Standup", "09:30")), TZ) is None


def test_two_calendar_events_that_both_fit_equally_are_no_match():
    plans = [stored(ASHLYN)]
    both = eligible(cal("a", "Dinner with Ashlyn", "19:00"), cal("b", "Ashlyn pickup", "21:00"))
    assert ec.match_calendar_event(plans[0], plans, both, TZ) is None


def test_with_no_name_the_plans_own_word_matches_only_when_it_is_unambiguous():
    plan = stored(text="has a date")
    assert ec.match_calendar_event(plan, [plan], eligible(cal("a", "Date night", "19:00")), TZ)["id"] == "a"
    two = eligible(cal("a", "Date night", "19:00"), cal("b", "Coffee date", "13:00"))
    assert ec.match_calendar_event(plan, [plan], two, TZ) is None                                      # which date? so none
    other_plan = stored(text="has a first date", id="e2")
    assert ec.match_calendar_event(plan, [plan, other_plan], eligible(cal("a", "Date night", "19:00")), TZ) is None   # another plan of theirs fits just as well


def test_a_plan_with_no_distinguishing_word_matches_nothing():
    plan = stored(text="has a meeting")
    assert ec.match_calendar_event(plan, [plan], eligible(cal("a", "Meeting", "11:00")), TZ) is None


# ------------------------------------------------------------------------ laying it over -------------------------------

def test_in_discreet_mode_only_a_time_is_taken_from_the_calendar_never_a_place():
    plan = stored({"with": {"value": "Ashlyn", "source": "user"}})
    events = eligible(cal("a", "Dinner with Ashlyn", "19:00", "Tidos"))
    out = ec.overlay([plan], events, TZ, use_place=False)[0]
    assert out["fields"]["time"]["source"] == "calendar" and "where" not in out["fields"]
    db = Db()
    ch.set_settings(db, "u1", {"enabled": True, "discreet": True})
    via = ec.with_calendar("u1", TZ, [plan], NOW, db=db, get_token=lambda u: "t", read_range=lambda *a: [cal("a", "Dinner with Ashlyn", "19:00", "Tidos")])
    assert "time" in via[0]["fields"] and "where" not in via[0]["fields"]


def test_a_time_the_person_never_gave_is_filled_from_the_calendar_and_marked_as_its():
    plans = [stored(ASHLYN)]
    out = ec.overlay(plans, eligible(cal("a", "Dinner with Ashlyn", "19:00", "Tidos")), TZ)[0]
    assert out["fields"]["time"] == {"value": "19:00", "said": "7 PM", "ambiguous": False, "assumed": None, "source": "calendar"}
    assert out["fields"]["with"]["source"] == "user" and "calendar_differs" not in out
    line = ef.event_line(out, ef._parse_day(DAY))
    assert "Time: 7 PM (your calendar)." in line and "NOT KNOWN" not in line


def test_a_place_the_person_never_gave_is_filled_and_one_they_gave_that_agrees_is_left():
    plan = stored({"with": {"value": "Ashlyn", "source": "user"}})
    out = ec.overlay([plan], eligible(cal("a", "Dinner with Ashlyn", "19:00", "Tidos, 12 Oak St")), TZ)[0]
    assert out["fields"]["where"] == {"value": "Tidos", "source": "calendar"}
    agree = ec.overlay([stored(ASHLYN)], eligible(cal("a", "Dinner with Ashlyn", "19:00", "Tidos Bar")), TZ)[0]
    assert agree["fields"]["where"] == {"value": "Tidos", "source": "user"} and "calendar_differs" not in agree


def test_when_the_calendar_disagrees_the_persons_value_stays_and_the_difference_is_recorded():
    plan = stored({**ASHLYN, "time": said_time("19:00", "7pm")})
    out = ec.overlay([plan], eligible(cal("a", "Dinner with Ashlyn", "18:00", "Tidos")), TZ)[0]
    assert out["fields"]["time"]["value"] == "19:00" and out["fields"]["time"]["source"] == "user"          # their words win
    assert out["calendar_differs"]["time"]["value"] == "18:00"
    line = ef.event_line(out, ef._parse_day(DAY))
    assert "Time: 7 PM (you said)." in line and "Their calendar has the time as 6 PM instead" in line and "use what they said" in line
    block = ef.build_events_block([out], ef._parse_day(DAY))
    assert "your calendar has" in block.lower()


def test_a_different_place_is_a_difference_too():
    plan = stored({"where": {"value": "Tidos", "source": "user"}, "with": {"value": "Ashlyn", "source": "user"}})
    out = ec.overlay([plan], eligible(cal("a", "Dinner with Ashlyn", "19:00", "Giordano's")), TZ)[0]
    assert out["fields"]["where"]["value"] == "Tidos" and out["calendar_differs"]["where"]["value"] == "Giordano's"


def test_an_unclear_time_is_settled_by_the_calendar_and_contradicted_only_by_a_different_hour():
    unclear = stored({**ASHLYN, "time": said_time(None, "7")})
    settled = ec.overlay([unclear], eligible(cal("a", "Dinner with Ashlyn", "19:00")), TZ)[0]
    assert settled["fields"]["time"]["value"] == "19:00" and settled["fields"]["time"]["source"] == "calendar" and "calendar_differs" not in settled
    differs = ec.overlay([unclear], eligible(cal("a", "Dinner with Ashlyn", "18:00")), TZ)[0]
    assert differs["fields"]["time"]["value"] is None and differs["calendar_differs"]["time"]["value"] == "18:00"


def test_a_time_that_was_only_assumed_is_confirmed_by_the_calendar_or_contradicted_by_it():
    assumed = stored({**ASHLYN, "time": {**said_time("19:00", "7"), "assumed": "pm"}})
    confirmed = ec.overlay([assumed], eligible(cal("a", "Dinner with Ashlyn", "19:00")), TZ)[0]
    assert confirmed["fields"]["time"]["source"] == "calendar" and confirmed["fields"]["time"]["assumed"] is None
    morning = ec.overlay([assumed], eligible(cal("a", "Dinner with Ashlyn", "11:00")), TZ)[0]       # (a morning event that has not started yet, so it is still eligible)
    assert morning["fields"]["time"]["assumed"] == "pm" and morning["calendar_differs"]["time"]["value"] == "11:00"


def test_an_all_day_calendar_event_never_gives_or_contradicts_a_time():
    plan = stored({**ASHLYN, "time": said_time("19:00", "7pm")})
    out = ec.overlay([plan], eligible(cal("a", "Ashlyn visiting", f"{DAY}")), TZ)[0]
    assert out["fields"]["time"]["value"] == "19:00" and "calendar_differs" not in out


def test_overlay_changes_nothing_in_what_it_was_given_and_leaves_unmatched_plans_alone():
    plan, other = stored(ASHLYN), stored(text="has a flight", id="e2")
    before = copy.deepcopy([plan, other])
    out = ec.overlay([plan, other], eligible(cal("a", "Dinner with Ashlyn", "19:00")), TZ)
    assert [plan, other] == before and out[1] is other and "time" in out[0]["fields"]


# ------------------------------------------------------------------------ the reply is told, and checked ----------------

def test_the_block_says_which_details_came_from_the_calendar():
    out = ec.overlay([stored(ASHLYN)], eligible(cal("a", "Dinner with Ashlyn", "19:00")), TZ)
    block = ef.build_events_block(out, ef._parse_day(DAY))
    assert "(your calendar)" in block and 'say "your calendar has..."' in block
    assert "your calendar" not in ef.build_events_block([stored(ASHLYN)], ef._parse_day(DAY)).lower()


def test_a_time_from_the_calendar_counts_as_known_and_the_calendars_other_time_may_be_mentioned():
    plan = stored(ASHLYN)
    filled = ec.overlay([plan], eligible(cal("a", "Dinner with Ashlyn", "19:00")), TZ)
    assert ef.reply_issue("Your calendar has dinner at 7, so leave by 6:15.", filled, "hi") is None      # nothing is NOT KNOWN now: no check
    clash = ec.overlay([stored({**ASHLYN, "time": said_time("19:00", "7pm")})], eligible(cal("a", "Dinner with Ashlyn", "18:00")), TZ)
    assert ef.reply_issue("You said 7, though your calendar says 6.", clash, "hi") is None
    still_unknown = [stored(ASHLYN)]
    assert ef.reply_issue("Dinner is at 7.", still_unknown, "hi") is not None


# ------------------------------------------------------------------------ who it is for ----------------------------------

class Reads:
    def __init__(self, events, fail=False):
        self.events, self.fail, self.calls = events, fail, 0

    def read(self, token, start, end):
        self.calls += 1
        if self.fail:
            raise RuntimeError("calendar down")
        return self.events


def run(db, plans, reads, now=NOW):
    return ec.with_calendar("u1", TZ, plans, now, db=db, get_token=lambda u: "tok", read_range=reads.read)


def test_without_calendar_heads_ups_switched_on_the_calendar_is_never_read():
    reads = Reads([cal("a", "Dinner with Ashlyn")])
    plans = [stored(ASHLYN)]
    assert run(Db(), plans, reads) is plans and reads.calls == 0


def test_switched_on_it_is_read_once_an_hour_and_laid_over_the_plans():
    db, reads = switched_on(), Reads([cal("a", "Dinner with Ashlyn", "19:00")])
    first = run(db, [stored(ASHLYN)], reads)
    second = run(db, [stored(ASHLYN)], reads)
    assert first[0]["fields"]["time"]["source"] == "calendar" and second[0]["fields"]["time"]["value"] == "19:00" and reads.calls == 1


def test_nothing_to_overlay_reads_nothing():
    db, reads = switched_on(), Reads([cal("a", "Dinner with Ashlyn")])
    assert run(db, [], reads) == [] and reads.calls == 0


def test_an_event_the_heads_ups_would_never_use_is_never_used_here_either():
    db = switched_on()
    for hidden in (cal("a", "Dinner with Ashlyn", visibility="private"), cal("b", "Dinner with Ashlyn", self_response="declined"),
                   cal("c", "Dinner with Ashlyn", has_attendees=True, organizer_self=False, creator_self=False),
                   cal("d", "Therapy with Ashlyn"), cal("e", "Dinner with Ashlyn", status="cancelled")):
        ch.clear_cache()
        plans = [stored(ASHLYN)]
        assert run(db, plans, Reads([hidden])) == plans, hidden["id"]


def test_a_calendar_that_is_not_connected_or_cannot_be_read_leaves_the_plans_as_they_were():
    from backend.services.google_calendar_oauth import GoogleCalendarConnectionError

    db = switched_on()
    plans = [stored(ASHLYN)]
    assert run(db, plans, Reads([], fail=True)) == plans
    ch.clear_cache()

    def not_connected(username):
        raise GoogleCalendarConnectionError("not connected")

    assert ec.with_calendar("u1", TZ, plans, NOW, db=db, get_token=not_connected, read_range=lambda *a: []) == plans


def test_a_failure_anywhere_is_swallowed_and_the_plans_come_back_unchanged(monkeypatch):
    db = switched_on()
    plans = [stored(ASHLYN)]
    monkeypatch.setattr(ec, "overlay", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert run(db, plans, Reads([cal("a", "Dinner with Ashlyn")])) == plans


# ------------------------------------------------------------------------ wiring and hygiene -----------------------------

def test_it_is_wired_into_the_reply_before_the_block_is_built_and_nothing_is_logged():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = source[source.index("events_context, events_shown"):source.index("# Something coming up on their calendar")]
    assert "event_calendar.with_calendar" in block and block.index("with_calendar") < block.index("build_events_block")
    module = (ROOT / "backend" / "utils" / "event_calendar.py").read_text(encoding="utf-8")
    assert module.count("logger.") == 1                                          # the one fixed warning, with no title, time or place in it
    assert "title" not in module[module.index("logger.warning("):module.index("logger.warning(") + 120]
