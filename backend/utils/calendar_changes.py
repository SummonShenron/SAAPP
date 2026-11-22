"""Google Calendar changes Sonic can propose: create, edit, delete, reminders, repeats, invitations. The pure part: checking what the model asked for, building the exact request Google will get, and describing the card the
person approves. No network, no database, no clock of its own (the caller passes the timezone), so all of it is tested directly (backend/tests/test_calendar_changes.py). The part that talks to Google and keeps the
proposal until it is approved is backend/services/calendar_changes_service.py.

WHY THIS REPLACED "FIND IT BY KEYWORD". The older path (create_calendar_event / update_calendar_event in agent_workflow.py) drafted JSON from the person's sentence and then found the event by a keyword on one day, taking the
FIRST match. Fine for a rename, risky for a delete. Here the model first LOOKS events up (list_google_calendar_events returns ids), then proposes a change to one exact event id; the card shows the event as Google has it, and
approving applies the request that was stored with the card, to that id, only if the event is unchanged since.

WHAT A CHANGE CAN CARRY (every field optional except what the action needs):
  action          create | update | delete
  event_id        update/delete: an id from list_google_calendar_events
  scope           "this" (default: this occurrence) or "series" (every occurrence of a repeating event)
  summary, location, description
  start, end      "YYYY-MM-DDTHH:MM" in the person's own timezone, or "YYYY-MM-DD" for an all-day event; duration_minutes instead of end
  attendees       email addresses to invite (an update ADDS them); Google emails each one an invitation, and the card says so
  reminders       up to 5 of {minutes, method: popup|email}; reminders_default=true goes back to the calendar's own default
  repeat_*        create only: frequency daily|weekly|monthly|yearly, interval, count OR until, weekdays (MO..SU)

Anything outside those limits is refused with a sentence the person can act on (CalendarChangeError), never silently trimmed.
"""
import datetime as dt
import re
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.utils.change_error import ChangeError

ACTIONS = ("create", "update", "delete")
SCOPES = ("this", "series")
MAX_ATTENDEES = 20
MAX_REMINDERS = 5
MAX_REMINDER_MINUTES = 40320          # four weeks: Google's own ceiling
MAX_DURATION_MINUTES = 14 * 24 * 60
DEFAULT_DURATION_MINUTES = 30
LIMITS = {"summary": 300, "location": 300, "description": 4000}
FREQUENCIES = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY", "yearly": "YEARLY"}
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WEEKDAY_NAMES = {"monday": "MO", "tuesday": "TU", "wednesday": "WE", "thursday": "TH", "friday": "FR", "saturday": "SA", "sunday": "SU"}
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EVENT_ID = re.compile(r"^[A-Za-z0-9_\-]{1,1024}$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CalendarChangeError(ChangeError):
    """Something the person (or the model) can fix: the message is written to be shown as it is."""


# ---- checking what was asked -----------------------------------------------------------------------------------------------------------------------

def _text(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    text = _CONTROL.sub("", str(value)).strip()
    if field != "description":
        text = " ".join(text.split())
    if len(text) > LIMITS[field]:
        raise CalendarChangeError(f"The {field} is too long (the limit is {LIMITS[field]} characters).")
    return text


def _whole(value: Any, field: str, low: int, high: int) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise CalendarChangeError(f"The {field} must be a whole number.")
    if not low <= number <= high:
        raise CalendarChangeError(f"The {field} must be between {low} and {high}.")
    return number


def parse_when(value: Any, tz: str, field: str = "start") -> Tuple[Optional[str], bool]:
    """(the time as a plain local ISO string or a date, whether it is a date only). A time with an offset is converted to the person's timezone; a bare time is taken as theirs. None when not given."""
    if value is None or str(value).strip() == "":
        return None, False
    raw = str(value).strip()
    if _DATE_ONLY.match(raw):
        try:
            dt.date.fromisoformat(raw)
        except ValueError:
            raise CalendarChangeError(f"The {field} \"{raw}\" is not a real date.")
        return raw, True
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise CalendarChangeError(f"I couldn't read the {field} \"{raw}\". Use a date and time like 2026-11-24T14:00, or just a date like 2026-11-24 for an all-day event.")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(tz)).replace(tzinfo=None)
    return parsed.replace(microsecond=0).isoformat(), False


def clean_emails(values: Any) -> List[str]:
    if values in (None, ""):
        return []
    if isinstance(values, str):
        values = re.split(r"[,;\s]+", values)
    emails: List[str] = []
    for value in values:
        email = str(value).strip().lower()
        if not email:
            continue
        if not _EMAIL.match(email):
            raise CalendarChangeError(f"\"{value}\" is not an email address I can invite.")
        if email not in emails:
            emails.append(email)
    if len(emails) > MAX_ATTENDEES:
        raise CalendarChangeError(f"I invite at most {MAX_ATTENDEES} people to one event.")
    return emails


def clean_reminders(values: Any, use_default: Any) -> Optional[Dict[str, Any]]:
    """None (leave reminders alone), {"default": True}, or {"overrides": [{"method", "minutes"}, ...]} (an empty list means no reminders at all)."""
    if use_default and values:
        raise CalendarChangeError("Choose either the calendar's default reminders or specific ones, not both.")
    if use_default:
        return {"default": True}
    if values is None:
        return None
    if not isinstance(values, list):
        raise CalendarChangeError("Reminders must be a list of {minutes, method}.")
    if len(values) > MAX_REMINDERS:
        raise CalendarChangeError(f"An event can have at most {MAX_REMINDERS} reminders.")
    overrides: List[Dict[str, Any]] = []
    for item in values:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            item = {"minutes": item}
        if not isinstance(item, dict):
            raise CalendarChangeError("Each reminder needs minutes (how long before the event) and a method (popup or email).")
        method = str(item.get("method") or "popup").strip().lower()
        if method not in ("popup", "email"):
            raise CalendarChangeError("A reminder's method must be popup or email.")
        minutes = _whole(item.get("minutes"), "reminder minutes", 0, MAX_REMINDER_MINUTES)
        if minutes is None:
            raise CalendarChangeError("Each reminder needs minutes (how long before the event it goes off).")
        entry = {"method": method, "minutes": minutes}
        if entry not in overrides:
            overrides.append(entry)
    return {"overrides": overrides}


def clean_repeat(args: Dict[str, Any], tz: str, all_day: bool) -> Optional[Dict[str, Any]]:
    frequency = str(args.get("repeat_frequency") or "").strip().lower()
    if not frequency:
        if any(args.get(k) not in (None, "", []) for k in ("repeat_interval", "repeat_count", "repeat_until", "repeat_weekdays")):
            raise CalendarChangeError("Say how often it repeats (daily, weekly, monthly or yearly).")
        return None
    if frequency not in FREQUENCIES:
        raise CalendarChangeError("An event can repeat daily, weekly, monthly or yearly.")
    interval = _whole(args.get("repeat_interval"), "repeat interval", 1, 99) or 1
    count = _whole(args.get("repeat_count"), "repeat count", 1, 999)
    until, until_is_date = parse_when(args.get("repeat_until"), tz, "repeat end date")
    if count and until:
        raise CalendarChangeError("Give a repeat count or an end date, not both.")
    weekdays = []
    for day in args.get("repeat_weekdays") or []:
        code = _WEEKDAY_NAMES.get(str(day).strip().lower(), str(day).strip().upper()[:2])
        if code not in WEEKDAYS:
            raise CalendarChangeError(f"\"{day}\" is not a day of the week (use MO, TU, WE, TH, FR, SA, SU).")
        if code not in weekdays:
            weekdays.append(code)
    if weekdays and frequency != "weekly":
        raise CalendarChangeError("Days of the week only go with a weekly repeat.")
    return {"frequency": frequency, "interval": interval, "count": count, "until": (until or "")[:10] or None, "weekdays": weekdays}


def clean_args(args: Dict[str, Any], tz: str) -> Dict[str, Any]:
    """The model's arguments, checked and normalised into one plain dict (kept with the proposal). Raises CalendarChangeError for anything the person can fix."""
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    if action not in ACTIONS:
        raise CalendarChangeError("The action must be create, update or delete.")
    event_id = str(args.get("event_id") or "").strip() or None
    if event_id and not _EVENT_ID.match(event_id):
        raise CalendarChangeError("That event id is not one Google would give me. Look the event up again and use its id.")
    scope = str(args.get("scope") or "this").strip().lower()
    if scope not in SCOPES:
        raise CalendarChangeError("The scope must be this (one occurrence) or series (every occurrence).")
    if action == "create" and event_id:
        raise CalendarChangeError("A new event has no event id.")
    if action in ("update", "delete") and not event_id:
        raise CalendarChangeError("Look the event up first (list_google_calendar_events) and give me its event id.")

    start, start_date_only = parse_when(args.get("start"), tz, "start")
    end, end_date_only = parse_when(args.get("end"), tz, "end")
    if start and end and start_date_only != end_date_only:
        raise CalendarChangeError("The start and end must both be dates (an all-day event) or both be times.")
    explicit_all_day = args.get("all_day")
    all_day = start_date_only or end_date_only or bool(explicit_all_day)
    if explicit_all_day and start and not start_date_only:
        raise CalendarChangeError("An all-day event takes a date like 2026-11-24, not a time.")
    if explicit_all_day is False and start_date_only:
        raise CalendarChangeError("That start is a date only. Give a time like 2026-11-24T14:00 for a timed event.")
    duration = _whole(args.get("duration_minutes"), "duration", 1, MAX_DURATION_MINUTES)
    if end and duration:
        raise CalendarChangeError("Give an end time or a duration, not both.")
    if start and end and end <= start:
        raise CalendarChangeError("The event has to end after it starts.")

    change: Dict[str, Any] = {
        "action": action, "event_id": event_id, "scope": scope, "timezone": tz,
        "summary": _text(args.get("summary"), "summary"), "location": _text(args.get("location"), "location"), "description": _text(args.get("description"), "description"),
        "start": start, "end": end, "duration_minutes": duration, "all_day": all_day if (start or end or explicit_all_day is not None) else None,
        "attendees": clean_emails(args.get("attendees")),
        "reminders": clean_reminders(args.get("reminders"), args.get("reminders_default")),
        "repeat": clean_repeat(args, tz, all_day),
    }
    if action == "create":
        if not change["summary"]:
            raise CalendarChangeError("A new event needs a title.")
        if not start:
            raise CalendarChangeError("A new event needs a start (a date and time, or a date for an all-day event).")
    if action == "update":
        if change["repeat"]:
            raise CalendarChangeError("I can't change how an existing event repeats yet. Delete the series and create it again, or change this occurrence.")
        changed = [k for k in ("summary", "location", "description", "start", "end", "duration_minutes", "reminders") if change.get(k) not in (None, "")]
        if not changed and not change["attendees"] and change["all_day"] is None:
            raise CalendarChangeError("Say what to change (the title, time, location, description, reminders or who to invite).")
    if action == "delete":
        extras = [k for k in ("summary", "location", "description", "start", "end", "duration_minutes", "reminders", "repeat") if change.get(k) not in (None, "")]
        if extras or change["attendees"]:
            raise CalendarChangeError("A delete takes only the event id (and the scope).")
    return change


# ---- reading an event as Google returns it -------------------------------------------------------------------------------------------------------

def event_times(event: Dict[str, Any]) -> Tuple[str, str, bool]:
    """(start, end, all_day) of a raw Google event."""
    start, end = event.get("start") or {}, event.get("end") or {}
    all_day = "date" in start and "dateTime" not in start
    return (start.get("dateTime") or start.get("date") or ""), (end.get("dateTime") or end.get("date") or ""), all_day


def other_attendees(event: Dict[str, Any]) -> List[str]:
    return [a.get("email", "") for a in (event.get("attendees") or []) if not a.get("self") and not a.get("resource") and a.get("email")]


def _local(value: str, tz: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    return parsed.astimezone(ZoneInfo(tz)).replace(tzinfo=None) if parsed.tzinfo else parsed


def describe_when(start: str, end: str, all_day: bool, tz: str) -> str:
    """"Tue Nov 24, 2:00-3:00 PM", "Tue Nov 24 - Thu Nov 26 (all day)". Pure."""
    if not start:
        return "(no time)"
    if all_day:
        first = dt.date.fromisoformat(start[:10])
        last = dt.date.fromisoformat(end[:10]) - dt.timedelta(days=1) if end else first
        label = f"{first:%a %b} {first.day}"
        if last > first:
            label += f" - {last:%a %b} {last.day}"
        return label + " (all day)"
    first = _local(start, tz)
    last = _local(end, tz) if end else first

    def clock(d: dt.datetime) -> str:
        return f"{d.hour % 12 or 12}:{d:%M} {'AM' if d.hour < 12 else 'PM'}"

    if first.date() == last.date():
        return f"{first:%a %b} {first.day}, {clock(first)} - {clock(last)}"
    return f"{first:%a %b} {first.day}, {clock(first)} - {last:%a %b} {last.day}, {clock(last)}"


def describe_reminders(reminders: Optional[Dict[str, Any]]) -> str:
    if reminders is None:
        return ""
    if reminders.get("default"):
        return "the calendar's default reminders"
    overrides = reminders.get("overrides") or []
    if not overrides:
        return "no reminders"

    def one(item: Dict[str, Any]) -> str:
        minutes = item["minutes"]
        if minutes and minutes % 1440 == 0:
            span = f"{minutes // 1440} day{'s' if minutes // 1440 != 1 else ''}"
        elif minutes and minutes % 60 == 0:
            span = f"{minutes // 60} hour{'s' if minutes // 60 != 1 else ''}"
        else:
            span = f"{minutes} minute{'s' if minutes != 1 else ''}"
        return f"{span} before ({'email' if item['method'] == 'email' else 'popup'})" if minutes else f"at the start ({'email' if item['method'] == 'email' else 'popup'})"

    return ", ".join(one(item) for item in overrides)


def describe_repeat(repeat: Optional[Dict[str, Any]]) -> str:
    if not repeat:
        return ""
    names = {"daily": "day", "weekly": "week", "monthly": "month", "yearly": "year"}
    unit = names[repeat["frequency"]]
    text = f"every {unit}" if repeat["interval"] == 1 else f"every {repeat['interval']} {unit}s"
    if repeat.get("weekdays"):
        text += " on " + ", ".join(repeat["weekdays"])
    if repeat.get("count"):
        text += f", {repeat['count']} times"
    elif repeat.get("until"):
        text += f", until {repeat['until']}"
    else:
        text += ", with no end date"
    return text


# ---- the request Google will get -------------------------------------------------------------------------------------------------------------------

def rrule(repeat: Dict[str, Any], start: str, all_day: bool, tz: str) -> str:
    """RFC 5545 RRULE line. A repeat that ends on a date ends at the end of that day: a date for an all-day event, the end of that day in UTC for a timed one (Google rejects a UNTIL of the other kind)."""
    parts = [f"FREQ={FREQUENCIES[repeat['frequency']]}"]
    if repeat.get("interval", 1) != 1:
        parts.append(f"INTERVAL={repeat['interval']}")
    if repeat.get("weekdays"):
        parts.append("BYDAY=" + ",".join(repeat["weekdays"]))
    if repeat.get("count"):
        parts.append(f"COUNT={repeat['count']}")
    elif repeat.get("until"):
        last = dt.date.fromisoformat(repeat["until"])
        if all_day:
            parts.append("UNTIL=" + last.strftime("%Y%m%d"))
        else:
            end_of_day = dt.datetime.combine(last, dt.time(23, 59, 59), tzinfo=ZoneInfo(tz)).astimezone(dt.timezone.utc)
            parts.append("UNTIL=" + end_of_day.strftime("%Y%m%dT%H%M%SZ"))
    return "RRULE:" + ";".join(parts)


def _time_field(value: str, all_day: bool, tz: str) -> Dict[str, str]:
    return {"date": value[:10]} if all_day else {"dateTime": value, "timeZone": tz}


def _reminders_body(reminders: Dict[str, Any]) -> Dict[str, Any]:
    if reminders.get("default"):
        return {"useDefault": True}
    return {"useDefault": False, "overrides": list(reminders.get("overrides") or [])}


def _resolve_times(change: Dict[str, Any], start: str, all_day: bool, existing_minutes: Optional[int]) -> Tuple[str, str]:
    """(start, end) for a change: the end the person gave, else start + the duration they gave, else the event's own length (an update) or the default (a new event)."""
    if change.get("end"):
        return start, change["end"]
    if all_day:
        days = max(1, (change.get("duration_minutes") or (existing_minutes or 1440)) // 1440)
        return start, (dt.date.fromisoformat(start[:10]) + dt.timedelta(days=days)).isoformat()
    minutes = change.get("duration_minutes") or existing_minutes or DEFAULT_DURATION_MINUTES
    return start, (dt.datetime.fromisoformat(start) + dt.timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def insert_body(change: Dict[str, Any]) -> Dict[str, Any]:
    tz, all_day = change["timezone"], bool(change.get("all_day"))
    start, end = _resolve_times(change, change["start"], all_day, None)
    body: Dict[str, Any] = {"summary": change["summary"], "start": _time_field(start, all_day, tz), "end": _time_field(end, all_day, tz)}
    if change.get("location"):
        body["location"] = change["location"]
    if change.get("description"):
        body["description"] = change["description"]
    if change.get("attendees"):
        body["attendees"] = [{"email": email} for email in change["attendees"]]
    body["reminders"] = _reminders_body(change["reminders"]) if change.get("reminders") is not None else {"useDefault": True}
    if change.get("repeat"):
        body["recurrence"] = [rrule(change["repeat"], start, all_day, tz)]
    return body


def _existing_minutes(event: Dict[str, Any], tz: str) -> Optional[int]:
    start, end, all_day = event_times(event)
    if not (start and end):
        return None
    if all_day:
        return max(1, (dt.date.fromisoformat(end[:10]) - dt.date.fromisoformat(start[:10])).days) * 1440
    return max(1, int((_local(end, tz) - _local(start, tz)).total_seconds() // 60))


def patch_body(change: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    """Only what changes, against the event as Google returned it. Times: a new start keeps the event's own length unless an end or duration is given; an end or duration alone keeps its start. For a whole series the new
    start keeps the series' own first DATE and takes only the time of day (the model's date is one occurrence's). An all-day event made timed needs a start time; a timed one made all-day keeps its date."""
    tz = change["timezone"]
    body: Dict[str, Any] = {}
    for field in ("summary", "location", "description"):
        if change.get(field) is not None:
            body[field] = change[field]
    cur_start, _cur_end, cur_all_day = event_times(event)
    wants_time = bool(change.get("start") or change.get("end") or change.get("duration_minutes") or (change.get("all_day") is not None and change["all_day"] != cur_all_day))
    if wants_time:
        all_day = cur_all_day if change.get("all_day") is None else bool(change["all_day"])
        own = cur_start[:10] if cur_all_day else _local(cur_start, tz).replace(microsecond=0).isoformat()      # the event's own start, in the person's timezone
        if change.get("start"):
            start = change["start"]
            if change.get("scope") == "series" and not all_day and "T" in start and not cur_all_day:
                start = own[:10] + "T" + start.split("T", 1)[1]
        elif all_day == cur_all_day:
            start = own
        elif all_day:
            start = own[:10]
        else:
            raise CalendarChangeError("That event is all day. Give the time it should start, like 2026-11-24T14:00.")
        if not all_day and "T" not in start:
            raise CalendarChangeError("That event has a time: give the new start as a date and time like 2026-11-24T14:00.")
        keep_minutes = _existing_minutes(event, tz) if all_day == cur_all_day else None
        start, end = _resolve_times(change, start, all_day, keep_minutes)
        body["start"], body["end"] = _time_field(start, all_day, tz), _time_field(end, all_day, tz)
    if change.get("attendees"):
        have = list(event.get("attendees") or [])
        known = {a.get("email", "").lower() for a in have}
        fresh = [{"email": email} for email in change["attendees"] if email not in known]
        if fresh:                                    # someone already on the event is not a change, and must not produce a card that changes nothing
            body["attendees"] = have + fresh
    if change.get("reminders") is not None:
        body["reminders"] = _reminders_body(change["reminders"])
    return body


def send_updates(change: Dict[str, Any], event: Optional[Dict[str, Any]], body: Dict[str, Any]) -> str:
    """"all" when other people will be told (new invitations, or an event that already has other attendees being moved, changed or cancelled), else "none": nothing goes to anyone but the person."""
    if change["action"] == "create":
        return "all" if change.get("attendees") else "none"
    others = other_attendees(event or {})
    if change["action"] == "delete":
        return "all" if others else "none"
    invited_new = bool(change.get("attendees")) and len(body.get("attendees") or []) > len(event.get("attendees") or [])
    material = any(k in body for k in ("start", "end", "summary", "location", "description"))
    return "all" if invited_new or (others and material) else "none"


# ---- the card --------------------------------------------------------------------------------------------------------------------------------------

def _short(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def card_data(change: Dict[str, Any], event: Optional[Dict[str, Any]], body: Dict[str, Any], notify: str, series: bool) -> Dict[str, Any]:
    """What the page draws for the approval card: {action, title, rows: [{label, value}], notice, destructive}. A create shows the new event; an update or delete shows the event AS GOOGLE HAS IT NOW, then what changes;
    `notice` says plainly who will be emailed (or that nobody will). Plain data, so the page needs no parsing of text. `heading` and `apply_label` are the words on the card and its button."""
    tz = change["timezone"]
    action = change["action"]
    rows: List[Dict[str, str]] = []

    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    if action == "create":
        start, end, all_day = event_times(body)
        title = body["summary"]
        add("When", f"{describe_when(start, end, all_day, tz)} ({tz})")
        add("Where", body.get("location", ""))
        add("Notes", _short(body.get("description", "")))
        add("Repeats", describe_repeat(change.get("repeat")))
        add("Invite", ", ".join(change.get("attendees") or []))
        add("Reminders", describe_reminders(change["reminders"]) if change.get("reminders") is not None else "the calendar's default")
    else:
        title = (event or {}).get("summary") or "Untitled event"
        start, end, all_day = event_times(event or {})
        recurring = bool((event or {}).get("recurringEventId")) or bool((event or {}).get("recurrence"))
        if recurring:
            add("Applies to", "every occurrence" if series else "this occurrence only")
        add("Now", describe_when(start, end, all_day, tz))
        add("Where now", (event or {}).get("location", ""))
        if action == "update":
            if "summary" in body:
                add("New title", body["summary"])
            if "start" in body:
                new_start, new_end, new_all_day = event_times(body)
                add("New time", f"{describe_when(new_start, new_end, new_all_day, tz)} ({tz})" + (", the time of day for every occurrence" if series else ""))
            if "location" in body:
                add("New location", body["location"] or "(removed)")
            if "description" in body:
                add("New notes", _short(body["description"]) or "(removed)")
            add("Invite", ", ".join(change.get("attendees") or []))
            if change.get("reminders") is not None:
                add("Reminders", describe_reminders(change["reminders"]))
    if notify == "all":
        if action == "delete":
            notice = "Google will tell the other people on it that it was cancelled: " + ", ".join(other_attendees(event or {}))
        elif change.get("attendees"):
            notice = "Google will email an invitation to " + ", ".join(change["attendees"])
        else:
            notice = "Google will tell the other people on it about the change: " + ", ".join(other_attendees(event or {}))
    else:
        notice = "Nobody else is emailed."
    heading, apply_label = {"create": ("Add to your calendar", "Add event"), "update": ("Change an event", "Save changes"), "delete": ("Delete from your calendar", "Delete event")}[action]
    return {"action": action, "heading": heading, "apply_label": apply_label, "title": title, "rows": rows, "notice": notice, "destructive": action == "delete"}


# ---- reading the calendar for a change ---------------------------------------------------------------------------------------------------------------

MAX_RANGE_DAYS = 31


def clean_range(date: Any, end_date: Any, today: str) -> Tuple[str, str]:
    """(first day, last day) as YYYY-MM-DD for a calendar look-up: `date` defaults to `today`, `end_date` to the same day, at most MAX_RANGE_DAYS days. Raises CalendarChangeError."""
    try:
        first = dt.date.fromisoformat(str(date or today).strip()[:10])
        last = dt.date.fromisoformat(str(end_date).strip()[:10]) if end_date else first
    except ValueError:
        raise CalendarChangeError("Dates must look like 2026-11-24.")
    if last < first:
        raise CalendarChangeError("The end date is before the start date.")
    if (last - first).days >= MAX_RANGE_DAYS:
        raise CalendarChangeError(f"Look at {MAX_RANGE_DAYS} days or fewer at a time.")
    return first.isoformat(), last.isoformat()


def event_line(item: Dict[str, Any], note: str = "") -> str:
    """One line per event for the tool's result: the start and title first, then the id the model must use to change it, the end, and what a change needs to know. Pure."""
    extras: List[str] = [f"until {item.get('end')}"] if item.get("end") else []
    if item.get("all_day"):
        extras.append("all day")
    if item.get("recurring_event_id"):
        extras.append("repeats")
    if item.get("location"):
        extras.append(f"at {item['location']}")
    reminders = item.get("reminders")
    if reminders == "default":
        extras.append("default reminders")
    elif reminders:
        extras.append("reminders: " + ", ".join(f"{r.get('minutes')} min {r.get('method')}" for r in reminders))
    elif reminders == []:
        extras.append("no reminders")
    if item.get("other_attendees"):
        extras.append(f"{item['other_attendees']} other people on it")
    tail = f" ({'; '.join(extras)})" if extras else ""
    return f"- {item.get('start')}: {item.get('summary')}" + (f" ({note})" if note else "") + tail + f" [id: {item.get('id')}]"

