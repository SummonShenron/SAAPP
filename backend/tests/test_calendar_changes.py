"""The pure part of Sonic's Google Calendar changes (backend/utils/calendar_changes.py): checking what the model asked for, building the exact request Google gets, and describing the card.
No network, no clock: every value below is a real Google event shape or a real tool argument."""
import pytest

from backend.utils import calendar_changes as cc
from backend.utils.calendar_changes import CalendarChangeError

TZ = "America/Chicago"


def ev(start="2026-11-24T14:00:00-06:00", end="2026-11-24T15:00:00-06:00", **extra):
    return {"id": "e1", "summary": "Dentist", "etag": '"a"', "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def all_day_ev(start="2026-11-24", end="2026-11-25", **extra):
    return {"id": "e2", "summary": "Conference", "etag": '"b"', "start": {"date": start}, "end": {"date": end}, **extra}


def change(**args):
    return cc.clean_args(args, TZ)


# ---- checking what was asked -----------------------------------------------------------------------------------------------------------------------

def test_a_new_event_is_tidied_and_its_start_read_as_the_persons_own_time():
    c = change(action="create", summary="  Lunch   with Sam ", start="2026-11-24T12:00")
    assert c["summary"] == "Lunch with Sam" and c["start"] == "2026-11-24T12:00:00" and c["all_day"] is False and c["scope"] == "this"


def test_a_time_with_an_offset_is_converted_to_the_persons_timezone():
    assert change(action="create", summary="x", start="2026-11-24T20:00:00Z")["start"] == "2026-11-24T14:00:00"
    assert change(action="create", summary="x", start="2026-07-01T20:00:00Z")["start"] == "2026-07-01T15:00:00"      # daylight time, an hour different


def test_a_date_alone_means_an_all_day_event():
    c = change(action="create", summary="Conference", start="2026-11-24")
    assert c["all_day"] is True and c["start"] == "2026-11-24"


@pytest.mark.parametrize("args,fragment", [
    ({"action": "explode"}, "create, update or delete"),
    ({"action": ""}, "create, update or delete"),
    ({"action": "create", "start": "2026-11-24T12:00"}, "needs a title"),
    ({"action": "create", "summary": "x"}, "needs a start"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "event_id": "abc"}, "no event id"),
    ({"action": "update", "summary": "x"}, "Look the event up first"),
    ({"action": "delete"}, "Look the event up first"),
    ({"action": "update", "event_id": "abc"}, "Say what to change"),
    ({"action": "update", "event_id": "ab c!"}, "not one Google would give"),
    ({"action": "delete", "event_id": "abc", "summary": "x"}, "only the event id"),
    ({"action": "delete", "event_id": "abc", "attendees": ["a@b.co"]}, "only the event id"),
    ({"action": "update", "event_id": "abc", "scope": "everything", "summary": "x"}, "this (one occurrence) or series"),
    ({"action": "create", "summary": "x", "start": "tomorrow at two"}, "couldn't read the start"),
    ({"action": "create", "summary": "x", "start": "2026-02-31"}, "not a real date"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "end": "2026-11-24T11:00"}, "end after it starts"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "end": "2026-11-24T12:00"}, "end after it starts"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "end": "2026-11-24T13:00", "duration_minutes": 30}, "end time or a duration, not both"),
    ({"action": "create", "summary": "x", "start": "2026-11-24", "end": "2026-11-25T10:00"}, "both be dates"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "all_day": True}, "not a time"),
    ({"action": "create", "summary": "x", "start": "2026-11-24", "all_day": False}, "date only"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "duration_minutes": 0}, "between 1 and"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "duration_minutes": "soon"}, "whole number"),
    ({"action": "create", "summary": "x" * 301, "start": "2026-11-24T12:00"}, "summary is too long"),
    ({"action": "create", "summary": "x", "start": "2026-11-24T12:00", "description": "y" * 4001}, "description is too long"),
    ({"action": "update", "event_id": "abc", "repeat_frequency": "weekly"}, "can't change how an existing event repeats"),
])
def test_what_cannot_be_done_is_refused_in_words_the_person_can_act_on(args, fragment):
    with pytest.raises(CalendarChangeError) as refused:
        change(**args)
    assert fragment in str(refused.value)


def test_control_characters_are_stripped_and_a_description_keeps_its_line_breaks():
    c = change(action="create", summary="a\x00b\nc", start="2026-11-24T12:00", description="line one\nline two\x07")
    assert c["summary"] == "ab c" and c["description"] == "line one\nline two"


def test_invitations_are_checked_deduplicated_and_limited():
    assert change(action="create", summary="x", start="2026-11-24T12:00", attendees=["Sam@Example.com", "sam@example.com", "kim@x.org"])["attendees"] == ["sam@example.com", "kim@x.org"]
    assert change(action="create", summary="x", start="2026-11-24T12:00", attendees="a@b.co, c@d.org")["attendees"] == ["a@b.co", "c@d.org"]
    for bad in (["not an address"], ["a@b"], ["a@b.co; evil@x.org <x>"]):
        with pytest.raises(CalendarChangeError):
            change(action="create", summary="x", start="2026-11-24T12:00", attendees=bad)
    with pytest.raises(CalendarChangeError) as many:
        change(action="create", summary="x", start="2026-11-24T12:00", attendees=[f"p{n}@x.org" for n in range(21)])
    assert "at most 20" in str(many.value)


def test_reminders_are_checked_one_by_one():
    ok = change(action="update", event_id="abc", reminders=[{"minutes": 30}, {"minutes": 1440, "method": "email"}, 30])
    assert ok["reminders"] == {"overrides": [{"method": "popup", "minutes": 30}, {"method": "email", "minutes": 1440}]}      # a bare number is minutes, a repeat is dropped
    assert change(action="update", event_id="abc", reminders=[])["reminders"] == {"overrides": []}                          # an empty list means none at all
    assert change(action="update", event_id="abc", reminders_default=True)["reminders"] == {"default": True}
    for bad, fragment in [
        ([{"minutes": 10, "method": "sms"}], "popup or email"), ([{"minutes": -5}], "between 0 and"), ([{"minutes": 40321}], "between 0 and"),
        ([{"method": "popup"}], "needs minutes"), ([{"minutes": n} for n in range(6)], "at most 5"), ("30 minutes", "list"), (["soon"], "needs minutes"),
    ]:
        with pytest.raises(CalendarChangeError) as refused:
            change(action="update", event_id="abc", reminders=bad)
        assert fragment in str(refused.value), bad
    with pytest.raises(CalendarChangeError) as both:
        change(action="update", event_id="abc", reminders=[{"minutes": 5}], reminders_default=True)
    assert "not both" in str(both.value)


def test_a_repeat_is_checked_and_weekdays_may_be_named_in_full():
    c = change(action="create", summary="x", start="2026-11-24T12:00", repeat_frequency="Weekly", repeat_weekdays=["monday", "WE", "mo"], repeat_count=10)
    assert c["repeat"] == {"frequency": "weekly", "interval": 1, "count": 10, "until": None, "weekdays": ["MO", "WE"]}
    for args, fragment in [
        ({"repeat_interval": 2}, "how often"), ({"repeat_frequency": "hourly"}, "daily, weekly"), ({"repeat_frequency": "weekly", "repeat_count": 3, "repeat_until": "2027-01-01"}, "not both"),
        ({"repeat_frequency": "daily", "repeat_weekdays": ["MO"]}, "weekly repeat"), ({"repeat_frequency": "weekly", "repeat_weekdays": ["funday"]}, "not a day"),
        ({"repeat_frequency": "weekly", "repeat_count": 0}, "between 1 and"), ({"repeat_frequency": "weekly", "repeat_until": "later"}, "couldn't read"),
    ]:
        with pytest.raises(CalendarChangeError) as refused:
            change(action="create", summary="x", start="2026-11-24T12:00", **args)
        assert fragment in str(refused.value), args


# ---- the request for a new event -------------------------------------------------------------------------------------------------------------------

def test_a_new_timed_event_lasts_half_an_hour_unless_told_and_uses_the_calendar_default_reminders():
    body = cc.insert_body(change(action="create", summary="Lunch", start="2026-11-24T12:00"))
    assert body == {"summary": "Lunch", "start": {"dateTime": "2026-11-24T12:00:00", "timeZone": TZ}, "end": {"dateTime": "2026-11-24T12:30:00", "timeZone": TZ}, "reminders": {"useDefault": True}}


def test_a_new_event_can_carry_an_end_or_a_duration_a_place_notes_guests_and_its_own_reminders():
    body = cc.insert_body(change(action="create", summary="Review", start="2026-11-24T12:00", duration_minutes=90, location="Room 4", description="Bring the draft",
                                 attendees=["sam@x.org"], reminders=[{"minutes": 10}, {"minutes": 60, "method": "email"}]))
    assert body["end"]["dateTime"] == "2026-11-24T13:30:00" and body["location"] == "Room 4" and body["description"] == "Bring the draft"
    assert body["attendees"] == [{"email": "sam@x.org"}]
    assert body["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}, {"method": "email", "minutes": 60}]}
    assert cc.insert_body(change(action="create", summary="x", start="2026-11-24T12:00", end="2026-11-24T14:15"))["end"]["dateTime"] == "2026-11-24T14:15:00"


def test_an_all_day_event_ends_the_next_day_because_google_counts_the_end_date_as_excluded():
    body = cc.insert_body(change(action="create", summary="Conference", start="2026-11-24"))
    assert body["start"] == {"date": "2026-11-24"} and body["end"] == {"date": "2026-11-25"}
    assert cc.insert_body(change(action="create", summary="Trip", start="2026-11-24", end="2026-11-27"))["end"] == {"date": "2026-11-27"}


def test_a_repeating_event_carries_an_rrule_and_an_end_date_is_the_end_of_that_day_in_the_right_form():
    weekly = change(action="create", summary="Standup", start="2026-11-24T09:00", repeat_frequency="weekly", repeat_weekdays=["MO", "WE"], repeat_count=10)
    assert cc.insert_body(weekly)["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"]
    every_other = change(action="create", summary="x", start="2026-11-24T09:00", repeat_frequency="daily", repeat_interval=2)
    assert cc.insert_body(every_other)["recurrence"] == ["RRULE:FREQ=DAILY;INTERVAL=2"]
    until_timed = change(action="create", summary="x", start="2026-11-24T09:00", repeat_frequency="monthly", repeat_until="2026-12-31")
    assert cc.insert_body(until_timed)["recurrence"] == ["RRULE:FREQ=MONTHLY;UNTIL=20270101T055959Z"]              # 23:59:59 in Chicago (UTC-6) is 05:59:59 UTC the next day
    until_all_day = change(action="create", summary="x", start="2026-11-24", repeat_frequency="yearly", repeat_until="2030-11-24")
    assert cc.insert_body(until_all_day)["recurrence"] == ["RRULE:FREQ=YEARLY;UNTIL=20301124"]                      # an all-day event takes a plain date


# ---- the request that changes an event -------------------------------------------------------------------------------------------------------------

def test_a_rename_sends_only_the_title():
    assert cc.patch_body(change(action="update", event_id="e1", summary="Dentist (moved)"), ev()) == {"summary": "Dentist (moved)"}


def test_a_new_start_keeps_the_events_own_length_not_a_default_half_hour():
    body = cc.patch_body(change(action="update", event_id="e1", start="2026-11-25T10:00"), ev())          # an hour long
    assert body["start"] == {"dateTime": "2026-11-25T10:00:00", "timeZone": TZ} and body["end"] == {"dateTime": "2026-11-25T11:00:00", "timeZone": TZ}


def test_an_end_or_a_duration_alone_keeps_the_start_in_the_persons_own_time():
    ended = cc.patch_body(change(action="update", event_id="e1", end="2026-11-24T16:00"), ev())
    assert ended["start"] == {"dateTime": "2026-11-24T14:00:00", "timeZone": TZ} and ended["end"]["dateTime"] == "2026-11-24T16:00:00"
    longer = cc.patch_body(change(action="update", event_id="e1", duration_minutes=45), ev())
    assert longer["start"]["dateTime"] == "2026-11-24T14:00:00" and longer["end"]["dateTime"] == "2026-11-24T14:45:00"


def test_an_event_read_back_in_utc_is_still_moved_by_the_persons_own_clock():
    utc_event = ev(start="2026-11-24T20:00:00Z", end="2026-11-24T21:00:00Z")                              # the same 2 pm in Chicago
    assert cc.patch_body(change(action="update", event_id="e1", duration_minutes=45), utc_event)["start"]["dateTime"] == "2026-11-24T14:00:00"


def test_a_change_to_a_whole_series_keeps_the_series_own_first_date_and_takes_only_the_time_of_day():
    master = ev(start="2026-11-03T14:00:00-06:00", end="2026-11-03T15:00:00-06:00", recurrence=["RRULE:FREQ=WEEKLY"])
    body = cc.patch_body(change(action="update", event_id="e1", scope="series", start="2026-12-01T09:30"), master)
    assert body["start"]["dateTime"] == "2026-11-03T09:30:00" and body["end"]["dateTime"] == "2026-11-03T10:30:00"


def test_all_day_and_timed_events_convert_only_with_what_they_need():
    with pytest.raises(CalendarChangeError) as needs_time:
        cc.patch_body(change(action="update", event_id="e2", all_day=False), all_day_ev())
    assert "all day" in str(needs_time.value) and "start" in str(needs_time.value)
    timed = cc.patch_body(change(action="update", event_id="e2", start="2026-11-24T09:00"), all_day_ev())
    assert timed["start"]["dateTime"] == "2026-11-24T09:00:00" and timed["end"]["dateTime"] == "2026-11-24T09:30:00"
    to_all_day = cc.patch_body(change(action="update", event_id="e1", all_day=True), ev())
    assert to_all_day["start"] == {"date": "2026-11-24"} and to_all_day["end"] == {"date": "2026-11-25"}
    with pytest.raises(CalendarChangeError):
        cc.patch_body(change(action="update", event_id="e1", start="2026-11-24T09:00", all_day=False, end="2026-11-24T10:00") | {"start": "2026-11-24"}, ev())


def test_inviting_someone_to_an_existing_event_adds_to_the_guests_and_never_drops_or_repeats_one():
    existing = ev(attendees=[{"email": "me@x.org", "self": True, "responseStatus": "accepted"}, {"email": "sam@x.org", "responseStatus": "needsAction"}])
    body = cc.patch_body(change(action="update", event_id="e1", attendees=["sam@x.org", "kim@x.org"]), existing)
    assert [a["email"] for a in body["attendees"]] == ["me@x.org", "sam@x.org", "kim@x.org"] and body["attendees"][0]["responseStatus"] == "accepted"


def test_inviting_someone_already_on_the_event_changes_nothing():
    existing = ev(attendees=[{"email": "me@x.org", "self": True}, {"email": "sam@x.org"}])
    assert cc.patch_body(change(action="update", event_id="e1", attendees=["SAM@x.org"]), existing) == {}


def test_reminders_on_an_existing_event_replace_its_current_ones():
    assert cc.patch_body(change(action="update", event_id="e1", reminders=[{"minutes": 15}]), ev())["reminders"] == {"useDefault": False, "overrides": [{"method": "popup", "minutes": 15}]}
    assert cc.patch_body(change(action="update", event_id="e1", reminders_default=True), ev())["reminders"] == {"useDefault": True}
    assert cc.patch_body(change(action="update", event_id="e1", reminders=[]), ev())["reminders"] == {"useDefault": False, "overrides": []}


# ---- who gets an email -----------------------------------------------------------------------------------------------------------------------------

def test_nobody_is_emailed_unless_other_people_are_involved():
    with_guest = ev(attendees=[{"email": "me@x.org", "self": True}, {"email": "sam@x.org"}])
    alone = ev(attendees=[{"email": "me@x.org", "self": True}])
    c_new = change(action="create", summary="x", start="2026-11-24T12:00")
    assert cc.send_updates(c_new, None, cc.insert_body(c_new)) == "none"
    c_invite = change(action="create", summary="x", start="2026-11-24T12:00", attendees=["sam@x.org"])
    assert cc.send_updates(c_invite, None, cc.insert_body(c_invite)) == "all"
    gone = change(action="delete", event_id="e1")
    assert cc.send_updates(gone, with_guest, {}) == "all" and cc.send_updates(gone, alone, {}) == "none" and cc.send_updates(gone, ev(), {}) == "none"
    moved = change(action="update", event_id="e1", start="2026-11-25T10:00")
    assert cc.send_updates(moved, with_guest, cc.patch_body(moved, with_guest)) == "all" and cc.send_updates(moved, alone, cc.patch_body(moved, alone)) == "none"
    reminder = change(action="update", event_id="e1", reminders=[{"minutes": 5}])
    assert cc.send_updates(reminder, with_guest, cc.patch_body(reminder, with_guest)) == "none"          # a reminder is the person's own: nobody else is told
    invited = change(action="update", event_id="e1", attendees=["kim@x.org"])
    assert cc.send_updates(invited, alone, cc.patch_body(invited, alone)) == "all"


# ---- the words on the card -------------------------------------------------------------------------------------------------------------------------

def test_times_are_described_the_way_a_person_says_them():
    assert cc.describe_when("2026-11-24T14:00:00-06:00", "2026-11-24T15:00:00-06:00", False, TZ) == "Tue Nov 24, 2:00 PM - 3:00 PM"
    assert cc.describe_when("2026-11-24T20:00:00Z", "2026-11-24T21:30:00Z", False, TZ) == "Tue Nov 24, 2:00 PM - 3:30 PM"          # shown in the person's timezone
    assert cc.describe_when("2026-11-24T00:00:00", "2026-11-24T12:30:00", False, TZ) == "Tue Nov 24, 12:00 AM - 12:30 PM"
    assert cc.describe_when("2026-11-24T22:00:00", "2026-11-25T01:00:00", False, TZ) == "Tue Nov 24, 10:00 PM - Wed Nov 25, 1:00 AM"
    assert cc.describe_when("2026-11-24", "2026-11-25", True, TZ) == "Tue Nov 24 (all day)"
    assert cc.describe_when("2026-11-24", "2026-11-27", True, TZ) == "Tue Nov 24 - Thu Nov 26 (all day)"


def test_reminders_and_repeats_are_described_in_plain_words():
    assert cc.describe_reminders({"default": True}) == "the calendar's default reminders" and cc.describe_reminders({"overrides": []}) == "no reminders" and cc.describe_reminders(None) == ""
    assert cc.describe_reminders({"overrides": [{"method": "popup", "minutes": 30}, {"method": "email", "minutes": 1440}, {"method": "popup", "minutes": 60}, {"method": "popup", "minutes": 0}]}) == (
        "30 minutes before (popup), 1 day before (email), 1 hour before (popup), at the start (popup)")
    assert cc.describe_repeat({"frequency": "weekly", "interval": 1, "count": 10, "until": None, "weekdays": ["MO", "WE"]}) == "every week on MO, WE, 10 times"
    assert cc.describe_repeat({"frequency": "daily", "interval": 2, "count": None, "until": "2026-12-31", "weekdays": []}) == "every 2 days, until 2026-12-31"
    assert cc.describe_repeat({"frequency": "yearly", "interval": 1, "count": None, "until": None, "weekdays": []}) == "every year, with no end date"
    assert cc.describe_repeat(None) == ""


def rows(card):
    return {r["label"]: r["value"] for r in card["rows"]}


def test_the_card_for_a_new_event_shows_what_will_be_added_and_who_is_emailed():
    c = change(action="create", summary="Review", start="2026-11-24T12:00", location="Room 4", attendees=["sam@x.org"], repeat_frequency="weekly", repeat_count=4)
    body = cc.insert_body(c)
    card = cc.card_data(c, None, body, cc.send_updates(c, None, body), False)
    assert card["action"] == "create" and card["title"] == "Review" and card["destructive"] is False
    assert (card["heading"], card["apply_label"]) == ("Add to your calendar", "Add event")
    assert rows(card) == {"When": "Tue Nov 24, 12:00 PM - 12:30 PM (America/Chicago)", "Where": "Room 4", "Repeats": "every week, 4 times", "Invite": "sam@x.org", "Reminders": "the calendar's default"}
    assert card["notice"] == "Google will email an invitation to sam@x.org"


def test_the_card_for_a_change_shows_the_event_as_it_is_now_then_what_changes():
    existing = ev(location="Main St")
    c = change(action="update", event_id="e1", summary="Dentist (moved)", start="2026-11-25T10:00", reminders=[{"minutes": 15}])
    body = cc.patch_body(c, existing)
    card = cc.card_data(c, existing, body, cc.send_updates(c, existing, body), False)
    assert card["title"] == "Dentist" and card["destructive"] is False and (card["heading"], card["apply_label"]) == ("Change an event", "Save changes")
    assert rows(card) == {"Now": "Tue Nov 24, 2:00 PM - 3:00 PM", "Where now": "Main St", "New title": "Dentist (moved)", "New time": "Wed Nov 25, 10:00 AM - 11:00 AM (America/Chicago)", "Reminders": "15 minutes before (popup)"}
    assert card["notice"] == "Nobody else is emailed."
    assert "Applies to" not in rows(card)                                                                      # a one-off event has no this-or-series choice to state


def test_the_card_for_a_repeating_event_says_whether_it_is_this_occurrence_or_every_one():
    existing = ev(recurringEventId="series1")
    this = change(action="delete", event_id="e1")
    assert rows(cc.card_data(this, existing, {}, "none", False))["Applies to"] == "this occurrence only"
    every = change(action="delete", event_id="e1", scope="series")
    assert rows(cc.card_data(every, existing, {}, "none", True))["Applies to"] == "every occurrence"


def test_the_card_for_a_delete_is_marked_careful_and_names_the_people_who_will_be_told():
    existing = ev(attendees=[{"email": "me@x.org", "self": True}, {"email": "sam@x.org"}, {"email": "room@resource.calendar.google.com", "resource": True}])
    c = change(action="delete", event_id="e1")
    card = cc.card_data(c, existing, {}, cc.send_updates(c, existing, {}), False)
    assert card["action"] == "delete" and card["destructive"] is True and card["title"] == "Dentist" and (card["heading"], card["apply_label"]) == ("Delete from your calendar", "Delete event")
    assert card["notice"] == "Google will tell the other people on it that it was cancelled: sam@x.org"       # a meeting room is not a person who is emailed


def test_an_event_with_no_title_still_gets_a_card():
    nameless = {"id": "e9", "start": {"dateTime": "2026-11-24T14:00:00-06:00"}, "end": {"dateTime": "2026-11-24T15:00:00-06:00"}}
    assert cc.card_data(change(action="delete", event_id="e9"), nameless, {}, "none", False)["title"] == "Untitled event"


# ---- reading the calendar --------------------------------------------------------------------------------------------------------------------------

def test_a_look_up_defaults_to_today_and_is_limited_to_a_month():
    assert cc.clean_range(None, None, "2026-11-22") == ("2026-11-22", "2026-11-22")
    assert cc.clean_range("2026-11-24", "2026-12-24", "2026-11-22") == ("2026-11-24", "2026-12-24")           # 31 days counting both ends
    for first, last, fragment in [("2026-11-24", "2026-12-25", "31 days"), ("2026-11-24", "2026-11-20", "before"), ("yesterday", None, "look like"), ("2026-11-24", "soon", "look like")]:
        with pytest.raises(CalendarChangeError) as refused:
            cc.clean_range(first, last, "2026-11-22")
        assert fragment in str(refused.value)


def test_each_event_is_listed_with_the_id_a_change_needs_and_what_it_must_know():
    line = cc.event_line({"id": "abc_123", "summary": "Standup", "start": "2026-11-24T09:00:00-06:00", "end": "2026-11-24T09:15:00-06:00", "all_day": False, "location": "Room 4",
                          "recurring_event_id": "series", "reminders": [{"method": "popup", "minutes": 10}], "other_attendees": 3}, note="our check-in")
    assert line.startswith("- 2026-11-24T09:00:00-06:00: Standup (our check-in) (until 2026-11-24T09:15:00-06:00; repeats; at Room 4; reminders: 10 min popup; 3 other people on it)")
    assert line.endswith("[id: abc_123]")
    assert cc.event_line({"id": "x", "summary": "Plain", "start": "2026-11-24", "end": "2026-11-25", "all_day": True, "reminders": "default"}) == "- 2026-11-24: Plain (until 2026-11-25; all day; default reminders) [id: x]"
    assert "no reminders" in cc.event_line({"id": "x", "summary": "p", "start": "s", "reminders": []})
