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


def build(*args, **kwargs):
    """googleapiclient's build, imported when a Google service is first used: the library costs the server about 14 MB and most turns never touch Google (backend/utils/server_memory.py)."""
    from googleapiclient.discovery import build as _build

    return _build(*args, **kwargs)

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
            # The series a repeating event belongs to, so a meeting still matches after its title is edited (backend/utils/meetings.py).
            "recurring_event_id": event.get("recurringEventId"),
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


def list_events_in_range(access_token: str, start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    """Events between two aware datetimes on the primary calendar, with what calendar heads-ups (backend/utils/calendar_headsups.py) need to tell a person's own plans from invitations
    from strangers: the person's own reply, who organized it, whether it has other attendees, its visibility and status. Repeats are expanded (singleEvents). Never returns the description."""
    service = _service(access_token)
    result = service.events().list(
        calendarId="primary",
        timeMin=start.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        timeMax=end.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        singleEvents=True, orderBy="startTime", maxResults=60,
    ).execute()
    items = []
    for event in result.get("items", []):
        attendees = event.get("attendees") or []
        mine = next((a for a in attendees if a.get("self")), None)
        items.append({
            "id": event.get("id"),
            "summary": event.get("summary", ""),
            "start": (event.get("start") or {}).get("dateTime", (event.get("start") or {}).get("date")),
            "end": (event.get("end") or {}).get("dateTime", (event.get("end") or {}).get("date")),
            "location": event.get("location", ""),
            "status": event.get("status"),
            "visibility": event.get("visibility"),
            "event_type": event.get("eventType"),
            "recurring_event_id": event.get("recurringEventId"),
            "self_response": (mine or {}).get("responseStatus"),
            "has_attendees": len([a for a in attendees if not a.get("self")]) > 0,
            "organizer_self": bool((event.get("organizer") or {}).get("self")),
            "creator_self": bool((event.get("creator") or {}).get("self")),
        })
    return items


# ---- what the calendar-change tools use (backend/utils/calendar_changes.py, backend/services/calendar_changes_service.py) ----------------------------------------------------------

def _day_bounds(first_date: str, last_date: str, tz: str) -> tuple:
    """(timeMin, timeMax) as UTC RFC3339 for the start of `first_date` to the end of `last_date` in the person's own timezone. Uses zoneinfo for the real offset ON THOSE DAYS (a range across a daylight-saving change is right)."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    first = datetime.datetime.combine(datetime.date.fromisoformat(first_date), datetime.time.min, tzinfo=zone)
    after = datetime.datetime.combine(datetime.date.fromisoformat(last_date) + datetime.timedelta(days=1), datetime.time.min, tzinfo=zone)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return first.astimezone(datetime.timezone.utc).strftime(fmt), after.astimezone(datetime.timezone.utc).strftime(fmt)


def list_events_detailed(access_token: str, first_date: str, last_date: str, tz: str, query: str | None = None, max_results: int = 25) -> list[dict]:
    """Events from `first_date` to `last_date` (inclusive, the person's timezone) on the primary calendar, repeats expanded, with what a change needs: the event id, end, location, whether it repeats, the
    reminders and how many OTHER people are on it. Never returns the description. An empty list means nothing is scheduled."""
    service = _service(access_token)
    time_min, time_max = _day_bounds(first_date, last_date, tz)
    request = {"calendarId": "primary", "timeMin": time_min, "timeMax": time_max, "singleEvents": True, "orderBy": "startTime", "maxResults": max(1, min(int(max_results), 50))}
    if query:
        request["q"] = query
    items = []
    for event in service.events().list(**request).execute().get("items", []):
        if event.get("status") == "cancelled":
            continue
        start, end = event.get("start") or {}, event.get("end") or {}
        reminders = event.get("reminders") or {}
        items.append({
            "id": event.get("id"),
            "summary": event.get("summary", "Untitled event"),
            "start": start.get("dateTime", start.get("date")),
            "end": end.get("dateTime", end.get("date")),
            "all_day": "date" in start and "dateTime" not in start,
            "location": event.get("location", ""),
            "recurring_event_id": event.get("recurringEventId"),
            "reminders": "default" if reminders.get("useDefault") else [{"method": o.get("method"), "minutes": o.get("minutes")} for o in (reminders.get("overrides") or [])],
            "other_attendees": len([a for a in (event.get("attendees") or []) if not a.get("self") and not a.get("resource")]),
            "html_link": event.get("htmlLink"),
        })
    return items


def get_event(access_token: str, event_id: str) -> dict:
    """The raw event as Google has it (with its etag), for a change to be checked against and built from."""
    return _service(access_token).events().get(calendarId="primary", eventId=event_id).execute()


def insert_event(access_token: str, body: dict, send_updates: str) -> dict:
    return _service(access_token).events().insert(calendarId="primary", body=body, sendUpdates=send_updates).execute()


def patch_event(access_token: str, event_id: str, body: dict, send_updates: str) -> dict:
    """Changes only the fields in `body` (patch, not update, so nothing else on the event is overwritten)."""
    return _service(access_token).events().patch(calendarId="primary", eventId=event_id, body=body, sendUpdates=send_updates).execute()


def delete_event(access_token: str, event_id: str, send_updates: str) -> None:
    _service(access_token).events().delete(calendarId="primary", eventId=event_id, sendUpdates=send_updates).execute()

