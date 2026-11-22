"""Preparing and applying a Google Calendar change (backend/services/calendar_changes_service.py). Google is replaced by a fake that records every call it receives, so each test can say exactly what was
and was not sent. The two steps are the point: `prepare` changes NOTHING, and `apply` sends only the request that was stored with the card."""
import logging
from types import SimpleNamespace

import pytest

from backend.services import calendar_changes_service as svc
from backend.services.google_calendar_oauth import GoogleCalendarConnectionError, GoogleCalendarTemporaryError
from backend.utils.calendar_changes import CalendarChangeError

TZ = "America/Chicago"
TITLE = "Root canal with Dr Okafor"
EMAIL = "private.person@example.org"


def event(eid="evt1", **extra):
    return {"id": eid, "summary": TITLE, "etag": '"v1"', "htmlLink": f"https://calendar.example/{eid}",
            "start": {"dateTime": "2026-11-24T14:00:00-06:00"}, "end": {"dateTime": "2026-11-24T15:00:00-06:00"}, **extra}


class HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}: {TITLE} {EMAIL}")        # Google's own text can carry anything; none of it may reach the person or a log
        self.resp = SimpleNamespace(status=status)


class FakeGoogle:
    def __init__(self, events=None):
        self.events = {e["id"]: e for e in (events or [])}
        self.calls = []
        self.fail_with = None

    def _maybe_fail(self):
        if self.fail_with is not None:
            raise self.fail_with

    def get_event(self, token, event_id):
        self.calls.append(("get", event_id))
        self._maybe_fail()
        if event_id not in self.events:
            raise HttpError(404)
        return dict(self.events[event_id])

    def insert_event(self, token, body, send_updates):
        self.calls.append(("insert", body, send_updates))
        self._maybe_fail()
        return {"id": "new1", "summary": body["summary"], "htmlLink": "https://calendar.example/new1", "start": body["start"], "end": body["end"]}

    def patch_event(self, token, event_id, body, send_updates):
        self.calls.append(("patch", event_id, body, send_updates))
        self._maybe_fail()
        merged = {**self.events[event_id], **body}
        return merged

    def delete_event(self, token, event_id, send_updates):
        self.calls.append(("delete", event_id, send_updates))
        self._maybe_fail()

    def writes(self):
        return [c for c in self.calls if c[0] in ("insert", "patch", "delete")]


@pytest.fixture()
def google(monkeypatch):
    fake = FakeGoogle([event(), event("series1", recurrence=["RRULE:FREQ=WEEKLY"], start={"dateTime": "2026-11-03T14:00:00-06:00"}, end={"dateTime": "2026-11-03T15:00:00-06:00"}),
                       event("inst1", recurringEventId="series1")])

    class Oauth:
        def get_valid_access_token(self, username):
            return "tok"

    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Oauth)
    for name in ("get_event", "insert_event", "patch_event", "delete_event"):
        monkeypatch.setattr(svc.gcs, name, getattr(fake, name))
    return fake


def prep(**args):
    return svc.prepare("jack", args, TZ)


# ---- preparing: nothing is changed ---------------------------------------------------------------------------------------------------------------

def test_a_new_event_is_prepared_without_calling_google_at_all(google):
    proposal = prep(action="create", summary="Lunch", start="2026-11-24T12:00")
    assert google.calls == [] and proposal["request"]["method"] == "insert" and proposal["request"]["send_updates"] == "none"
    assert proposal["card"]["action"] == "create" and proposal["title"] == "Lunch" and proposal["etag"] is None and len(proposal["proposal_id"]) == 32


def test_a_change_reads_the_event_but_writes_nothing_and_keeps_its_etag_for_later(google):
    proposal = prep(action="update", event_id="evt1", start="2026-11-25T10:00")
    assert google.calls == [("get", "evt1")] and google.writes() == []
    assert proposal["request"] == {"method": "patch", "event_id": "evt1", "body": {"start": {"dateTime": "2026-11-25T10:00:00", "timeZone": TZ}, "end": {"dateTime": "2026-11-25T11:00:00", "timeZone": TZ}}, "send_updates": "none"}
    assert proposal["etag"] == '"v1"' and proposal["card"]["title"] == TITLE


def test_a_delete_is_prepared_against_the_exact_event_and_writes_nothing(google):
    proposal = prep(action="delete", event_id="evt1")
    assert google.writes() == [] and proposal["request"] == {"method": "delete", "event_id": "evt1", "body": None, "send_updates": "none"} and proposal["card"]["destructive"] is True


def test_deleting_one_occurrence_targets_that_occurrence_and_the_series_targets_the_series_record(google):
    this = prep(action="delete", event_id="inst1")
    assert this["request"]["event_id"] == "inst1" and {r["label"]: r["value"] for r in this["card"]["rows"]}["Applies to"] == "this occurrence only"
    google.calls.clear()
    every = prep(action="delete", event_id="inst1", scope="series")
    assert every["request"]["event_id"] == "series1" and google.calls == [("get", "inst1"), ("get", "series1")]
    assert {r["label"]: r["value"] for r in every["card"]["rows"]}["Applies to"] == "every occurrence"


def test_a_guest_list_makes_the_card_say_who_is_emailed(google):
    google.events["evt1"]["attendees"] = [{"email": "me@x.org", "self": True}, {"email": "sam@x.org"}]
    proposal = prep(action="delete", event_id="evt1")
    assert proposal["request"]["send_updates"] == "all" and "sam@x.org" in proposal["card"]["notice"]


@pytest.mark.parametrize("setup,args,fragment", [
    (lambda g: None, {"action": "update", "event_id": "missing", "summary": "x"}, "no longer on your calendar"),
    (lambda g: g.events["evt1"].update(status="cancelled"), {"action": "delete", "event_id": "evt1"}, "already cancelled"),
    (lambda g: g.events["evt1"].update(eventType="birthday"), {"action": "update", "event_id": "evt1", "summary": "x"}, "cannot be changed from here"),
    (lambda g: g.events["evt1"].update(attendees=[{"email": "sam@x.org"}]), {"action": "update", "event_id": "evt1", "attendees": ["sam@x.org"]}, "Nothing there would change"),
    (lambda g: None, {"action": "delete"}, "Look the event up first"),
])
def test_what_cannot_be_prepared_is_refused_before_any_card_and_after_no_write(google, setup, args, fragment):
    setup(google)
    with pytest.raises(CalendarChangeError) as refused:
        prep(**args)
    assert fragment in str(refused.value) and google.writes() == []


def test_a_google_failure_while_reading_becomes_a_sentence_without_googles_own_words(google, caplog):
    for status, fragment in ((403, "would not let this account"), (401, "Reconnect Google"), (429, "busy right now"), (503, "busy right now"), (400, "did not accept that")):
        google.fail_with = HttpError(status)
        with caplog.at_level(logging.DEBUG, logger="SASS Logger"):
            with pytest.raises(CalendarChangeError) as refused:
                prep(action="delete", event_id="evt1")
        assert fragment in str(refused.value)
        assert TITLE not in str(refused.value) and EMAIL not in str(refused.value)
    assert TITLE not in caplog.text and EMAIL not in caplog.text, "nothing the person's calendar holds is ever logged"


def test_connection_problems_say_what_to_do(monkeypatch, google):
    class Missing:
        def get_valid_access_token(self, username):
            raise GoogleCalendarConnectionError("no connection")

    class Flaky:
        def get_valid_access_token(self, username):
            raise GoogleCalendarTemporaryError("Google is slow to answer; try again in a moment.")

    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Missing)
    with pytest.raises(CalendarChangeError) as none:
        prep(action="create", summary="x", start="2026-11-24T12:00")
    assert "Connect it under Integrations" in str(none.value)
    monkeypatch.setattr(svc, "GoogleCalendarOAuth", Flaky)
    with pytest.raises(CalendarChangeError) as slow:
        prep(action="create", summary="x", start="2026-11-24T12:00")
    assert "try again in a moment" in str(slow.value)
    monkeypatch.setattr(svc, "CALENDAR_LOCKED_USERS", {"jack"})
    with pytest.raises(CalendarChangeError) as locked:
        prep(action="create", summary="x", start="2026-11-24T12:00")
    assert "not available for this account" in str(locked.value)


# ---- applying: only the stored request, only if the event is unchanged -------------------------------------------------------------------------------

def test_applying_a_new_event_sends_exactly_the_stored_insert_once(google):
    proposal = prep(action="create", summary="Lunch", start="2026-11-24T12:00", attendees=["sam@x.org"])
    google.calls.clear()
    message = svc.apply(proposal)
    assert google.calls == [("insert", proposal["request"]["body"], "all")]
    assert message.startswith("Added \"Lunch\" for Tue Nov 24, 12:00 PM - 12:30 PM") and "https://calendar.example/new1" in message


def test_applying_a_change_checks_the_event_is_unchanged_then_patches_only_what_the_card_showed(google):
    proposal = prep(action="update", event_id="evt1", summary="Root canal (moved)")
    google.calls.clear()
    message = svc.apply(proposal)
    assert google.calls == [("get", "evt1"), ("patch", "evt1", {"summary": "Root canal (moved)"}, "none")]
    assert message.startswith("Changed \"Root canal (moved)\"; it is now Tue Nov 24, 2:00 PM - 3:00 PM")


def test_an_event_that_changed_since_the_card_was_drawn_is_left_alone(google):
    proposal = prep(action="delete", event_id="evt1")
    google.events["evt1"]["etag"] = '"v2"'                                     # someone moved it in the meantime
    google.calls.clear()
    with pytest.raises(CalendarChangeError) as stale:
        svc.apply(proposal)
    assert "changed after I showed you the card" in str(stale.value) and google.writes() == []


def test_applying_a_delete_deletes_that_id_and_says_so(google):
    proposal = prep(action="delete", event_id="evt1")
    google.calls.clear()
    assert svc.apply(proposal) == f"Deleted \"{TITLE}\" from your calendar." and google.calls == [("get", "evt1"), ("delete", "evt1", "none")]


def test_an_event_deleted_in_the_meantime_is_reported_not_retried(google):
    proposal = prep(action="delete", event_id="evt1")
    del google.events["evt1"]
    with pytest.raises(CalendarChangeError) as gone:
        svc.apply(proposal)
    assert "no longer on your calendar" in str(gone.value) and google.writes() == []


def test_a_google_failure_while_writing_says_nothing_was_changed_and_leaks_nothing(google, caplog):
    proposal = prep(action="create", summary="Lunch", start="2026-11-24T12:00")
    google.fail_with = HttpError(500)
    with caplog.at_level(logging.DEBUG, logger="SASS Logger"):
        with pytest.raises(CalendarChangeError) as busy:
            svc.apply(proposal)
    assert "nothing was changed" in str(busy.value).lower() and TITLE not in str(busy.value) and EMAIL not in str(busy.value)
    assert TITLE not in caplog.text and EMAIL not in caplog.text and "status 500" in caplog.text


def test_an_error_that_is_not_googles_still_becomes_a_calendar_error_not_a_crash(google):
    proposal = prep(action="create", summary="Lunch", start="2026-11-24T12:00")
    google.fail_with = RuntimeError("socket closed")
    with pytest.raises(CalendarChangeError):
        svc.apply(proposal)
