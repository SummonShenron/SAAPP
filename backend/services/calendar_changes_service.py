"""Preparing and applying a Google Calendar change: the part that talks to Google (the checking and building is backend/utils/calendar_changes.py; the card that waits for Apply or Decline is
backend/services/change_cards.py).

TWO STEPS, NEVER ONE. `prepare` looks at the event as Google has it now, builds the exact request (an insert, a patch of only the changed fields, or a delete) and the card the person will see, and CHANGES NOTHING.
`apply` runs only that stored request, after the person pressed Apply on the card. Before it touches an existing event it fetches it again and refuses if Google's copy has changed since the card was drawn (the etag),
so a card can never act on something other than what it showed.

Both are blocking (network calls): call them off the event loop. No event text, title, address or person is ever written to a log; failures are logged by kind only. A failure the person can act on is a
CalendarChangeError whose message is shown as it is; the raw Google error text is never passed on.
"""
import logging
import uuid
from typing import Any, Dict

from backend.services import google_calendar_service as gcs
from backend.services.google_calendar_oauth import GoogleCalendarConnectionError, GoogleCalendarOAuth, GoogleCalendarTemporaryError
from backend.utils import calendar_changes as cc
from backend.utils.calendar_changes import CalendarChangeError
from backend.utils.google_calendar_utils import CALENDAR_LOCKED_USERS

logger = logging.getLogger("SASS Logger")

# Event kinds Google will not let anyone edit here (birthdays come from Contacts; the rest have their own rules).
_NOT_EDITABLE_TYPES = ("birthday", "fromGmail")


def _token(username: str) -> str:
    if username in CALENDAR_LOCKED_USERS:
        raise CalendarChangeError("Google Calendar is not available for this account.")
    try:
        return GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarTemporaryError as error:
        raise CalendarChangeError(str(error))
    except GoogleCalendarConnectionError:
        raise CalendarChangeError("No Google Calendar is connected. Connect it under Integrations first.")


def _status(error: Exception) -> int:
    return int(getattr(getattr(error, "resp", None), "status", 0) or 0)


def _explain(error: Exception, doing: str) -> CalendarChangeError:
    """A sentence for the person from a Google failure, by its status only (the raw text can carry anything)."""
    status = _status(error)
    kind = type(error).__name__
    logger.warning("[calendar change] Google refused or failed while %s (status %s, %s).", doing, status or "none", kind)
    if status in (404, 410):
        return CalendarChangeError("That event is no longer on your calendar. Look your calendar up again to see what is there.")
    if status == 403:
        return CalendarChangeError("Google would not let this account do that. It may not be the organizer of that event, or the calendar is read-only.")
    if status == 401:
        return CalendarChangeError("Google no longer accepts the connection. Reconnect Google under Integrations.")
    if status == 429 or status >= 500:
        return CalendarChangeError("Google Calendar is busy right now. Nothing was changed; try again in a moment.")
    return CalendarChangeError("Google Calendar did not accept that, so nothing was changed.")


def _get(token: str, event_id: str) -> Dict[str, Any]:
    try:
        event = gcs.get_event(token, event_id)
    except CalendarChangeError:
        raise
    except Exception as error:
        raise _explain(error, "reading the event")
    if event.get("status") == "cancelled":
        raise CalendarChangeError("That event was already cancelled.")
    if event.get("eventType") in _NOT_EDITABLE_TYPES:
        raise CalendarChangeError("That kind of event (a birthday or one Gmail added) cannot be changed from here.")
    return event


def prepare(username: str, args: Dict[str, Any], tz: str) -> Dict[str, Any]:
    """The proposal for these arguments: {proposal_id, username, action, title, card, request, etag, subject, apply}; `apply(proposal)` is what runs after Apply (backend/services/change_cards.py). Raises CalendarChangeError. Changes nothing."""
    change = cc.clean_args(args, tz)
    token = _token(username)
    action = change["action"]
    series = False
    target = None
    if action == "create":
        body = cc.insert_body(change)
        notify = cc.send_updates(change, None, body)
        request = {"method": "insert", "event_id": None, "body": body, "send_updates": notify}
        card = cc.card_data(change, None, body, notify, False)
        etag = None
    else:
        target = _get(token, change["event_id"])
        parent = target.get("recurringEventId")
        if change["scope"] == "series" and parent:
            target = _get(token, parent)         # the series' own record: a change to it reaches every occurrence
            series = True
        elif change["scope"] == "series" and target.get("recurrence"):
            series = True
        change["scope"] = "series" if series else "this"
        if action == "delete":
            body = {}
            notify = cc.send_updates(change, target, body)
            request = {"method": "delete", "event_id": target["id"], "body": None, "send_updates": notify}
        else:
            body = cc.patch_body(change, target)
            if not body:
                raise CalendarChangeError("Nothing there would change: the event already looks like that.")
            notify = cc.send_updates(change, target, body)
            request = {"method": "patch", "event_id": target["id"], "body": body, "send_updates": notify}
        card = cc.card_data(change, target, body, notify, series)
        etag = target.get("etag")
    return {"proposal_id": uuid.uuid4().hex, "username": username, "action": action, "title": card["title"], "card": card, "request": request, "etag": etag, "timezone": tz, "subject": "calendar", "apply": apply}


def _describe_result(action: str, event: Dict[str, Any], tz: str, title: str) -> str:
    start, end, all_day = cc.event_times(event)
    when = cc.describe_when(start, end, all_day, tz) if start else ""
    link = f" Link: {event['htmlLink']}" if event.get("htmlLink") else ""
    if action == "create":
        return f"Added \"{event.get('summary') or title}\" for {when}.{link}"
    return f"Changed \"{event.get('summary') or title}\"; it is now {when}.{link}"


def apply(proposal: Dict[str, Any]) -> str:
    """Runs the stored request and returns one plain sentence saying what happened (raises CalendarChangeError with the reason when it could not). Only called after Apply."""
    token = _token(proposal["username"])
    request, tz, action = proposal["request"], proposal["timezone"], proposal["action"]
    if request["method"] in ("patch", "delete"):
        current = _get(token, request["event_id"])
        if proposal.get("etag") and current.get("etag") != proposal["etag"]:
            raise CalendarChangeError("That event changed after I showed you the card, so I did not touch it. Ask me again and I will show you the new version.")
    try:
        if request["method"] == "insert":
            event = gcs.insert_event(token, request["body"], request["send_updates"])
            return _describe_result("create", event, tz, proposal["title"])
        if request["method"] == "patch":
            event = gcs.patch_event(token, request["event_id"], request["body"], request["send_updates"])
            return _describe_result("update", event, tz, proposal["title"])
        gcs.delete_event(token, request["event_id"], request["send_updates"])
        return f"Deleted \"{proposal['title']}\" from your calendar."
    except CalendarChangeError:
        raise
    except Exception as error:
        raise _explain(error, {"insert": "adding the event", "patch": "changing the event", "delete": "deleting the event"}[request["method"]])
