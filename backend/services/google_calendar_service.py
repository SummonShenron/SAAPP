"""Google Calendar API operations (create/update/list events), separate from OAuth/connection
management (backend/services/google_calendar_oauth.py). Every function here takes an
already-resolved per-user access token and never touches Mongo or a username directly — callers
resolve the token via GoogleCalendarOAuth.get_valid_access_token(username) first.

Replaces PAAPP's legacy backend/tools/calendar_tool.py (separate repo), which built one
process-wide `service = get_calendar_service()` off a single global token.json — here the
credentials are per-call, since the token is per-user now.
"""
import datetime
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger("SASS Logger")


def _service(access_token: str):
    return build("calendar", "v3", credentials=Credentials(token=access_token))


def list_events_for_day(access_token: str, date_iso: str, tz: str) -> list[dict]:
    """Returns a list of {summary, start, end} dicts for the given day, in the given IANA
    timezone. An empty list means the calendar genuinely has nothing scheduled that day."""
    service = _service(access_token)
    time_min = f"{date_iso}T00:00:00"
    time_max = f"{date_iso}T23:59:59"
    events_result = service.events().list(
        calendarId="primary",
        timeMin=f"{time_min}{_offset_suffix(tz)}",
        timeMax=f"{time_max}{_offset_suffix(tz)}",
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    items = []
    for event in events_result.get("items", []):
        items.append({
            "id": event.get("id"),
            "summary": event.get("summary", "Untitled event"),
            "start": event["start"].get("dateTime", event["start"].get("date")),
            "end": event["end"].get("dateTime", event["end"].get("date")),
        })
    return items


def create_event(access_token: str, summary: str, start_iso: str, duration_minutes: int, tz: str) -> dict:
    """Creates an event and returns {id, summary, html_link, start, end}."""
    service = _service(access_token)
    start_dt = datetime.datetime.fromisoformat(start_iso)
    end_dt = start_dt + datetime.timedelta(minutes=int(duration_minutes))

    body = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
        "reminders": {"useDefault": True},
    }
    created = service.events().insert(calendarId="primary", body=body).execute()
    return {
        "id": created.get("id"),
        "summary": created.get("summary"),
        "html_link": created.get("htmlLink"),
        "start": created["start"].get("dateTime"),
        "end": created["end"].get("dateTime"),
    }


def update_event(access_token: str, event_id: str, updates: dict, tz: str) -> dict:
    """Patches an existing event's summary and/or time. `updates` may contain `summary`,
    `start_iso`, and `duration_minutes` — only the fields present are changed."""
    service = _service(access_token)
    event = service.events().get(calendarId="primary", eventId=event_id).execute()

    if "summary" in updates:
        event["summary"] = updates["summary"]

    if "start_iso" in updates:
        start_dt = datetime.datetime.fromisoformat(updates["start_iso"])
        duration = int(updates.get("duration_minutes", 30))
        end_dt = start_dt + datetime.timedelta(minutes=duration)
        event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": tz}
        event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": tz}

    updated = service.events().update(calendarId="primary", eventId=event_id, body=event).execute()
    return {
        "id": updated.get("id"),
        "summary": updated.get("summary"),
        "html_link": updated.get("htmlLink"),
        "start": updated["start"].get("dateTime"),
        "end": updated["end"].get("dateTime"),
    }


def find_event_by_summary_on_day(access_token: str, search_summary: str, event_date_iso: str, tz: str) -> dict | None:
    """Looks up an event by keyword on a given day — used by the update-event write action to
    resolve a plain-language reference ("my meeting with Sam") to a real event id before calling
    update_event."""
    service = _service(access_token)
    time_min = f"{event_date_iso}T00:00:00{_offset_suffix(tz)}"
    time_max = f"{event_date_iso}T23:59:59{_offset_suffix(tz)}"
    events_result = service.events().list(
        calendarId="primary", timeMin=time_min, timeMax=time_max, q=search_summary, singleEvents=True,
    ).execute()
    items = events_result.get("items", [])
    return items[0] if items else None


def _offset_suffix(tz: str) -> str:
    """Google's events().list wants an RFC3339 timestamp; a bare date+time string needs an
    explicit offset or Google interprets it as UTC regardless of the timeZone we intend. Rather
    than hand-rolling DST-aware offset math, resolve the real offset for this instant via
    zoneinfo, which already accounts for daylight saving.
    """
    from zoneinfo import ZoneInfo
    offset = datetime.datetime.now(ZoneInfo(tz)).strftime("%z")
    return f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
