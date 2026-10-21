"""Calendar read-through for what is coming up (docs/memory-grounding-plan.md, step 4): when someone has told Sonic about a plan and their calendar has it too, the calendar can fill the gap
Sonic would otherwise have to call NOT KNOWN (the time, the place), and when the two disagree Sonic says so.

THE RULES, and why each is there:
- READ-ONLY, AT THE MOMENT OF THE REPLY. Nothing from the calendar is stored. The reply is handed the events it is about to be told of, with the calendar's time and place laid over them for that one turn.
  A stored copy would go stale the moment the calendar changed, and would put calendar data in Sonic's own database for no reason.
- ONLY FOR SOMEONE WHO TURNED CALENDAR HEADS-UPS ON, through the same read, the same hour of cache and the same filters as the heads-ups (backend/utils/calendar_headsups.py): nothing private, nothing
  declined, nothing sensitive beyond what they allowed, nothing from a stranger's invitation. A person who never said Sonic may look at their calendar is never looked at.
- ONLY THE PLANS THE PERSON TOLD SONIC ABOUT. A calendar event is used only to complete or check an event they mentioned, on the same day, and only when it is clearly the same thing: the
  person or place they named appears in its title or place, or it is the one calendar event that day sharing the plan's key word (and no other plan of theirs that day shares it). A doubtful
  match is no match: a wrong time stated as "your calendar says" would be worse than a gap.
- WHAT IT CAN GIVE: a time (never from an all-day event) and a place (never a street address; the part before the first comma is used when the whole is one). Not who it is with (the
  calendar's attendee names are never read).
- THEIR WORDS WIN (Jack, 2026-10-21). A detail the person gave is kept as they said it. When the calendar says something else, the reply is told both, to use theirs and mention the difference once.
  A calendar value only ever FILLS a gap, or SETTLES an unclear one (a "7" with no AM or PM that the calendar says is 7 PM, or an assumed PM that the calendar confirms).
- No title, time or place is logged.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils import calendar_headsups as headsups
from backend.utils import event_fields, name_aliases
from backend.utils.time_utils import local_now

logger = logging.getLogger("SASS Logger")

_TOKEN_RE = re.compile(r"[a-z0-9']+")
# Words that say nothing about WHICH plan it is.
_STOP_WORDS = frozenset({
    "has", "have", "had", "the", "and", "with", "their", "your", "user", "some", "something", "plans", "plan", "coming", "going", "will", "for", "from", "that", "this",
    "meeting", "call", "event", "time", "night", "out", "get", "got", "see", "bar", "restaurant",
})


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 3 and t not in _STOP_WORDS}


def _local_day(cal_event: Dict[str, Any], tz_name: str) -> Optional[str]:
    start = headsups.event_start(cal_event, tz_name)
    if start is None:
        return None
    return (start.date() if cal_event.get("all_day") else local_now(tz_name, start).date()).isoformat()


def _place(location: str) -> Optional[str]:
    """The place from a calendar location: the whole thing when it is fine to keep, else what comes before the first comma ("Tidos, 123 Main St" gives "Tidos"); None for an address."""
    text = " ".join((location or "").split())
    if not text:
        return None
    for candidate in (text, text.split(",")[0].strip()):
        if candidate and not event_fields.value_problem(candidate, event_fields.MAX_WHERE_CHARS):
            return candidate
    return None


def calendar_fields(cal_event: Dict[str, Any], tz_name: str) -> Dict[str, Dict[str, Any]]:
    """The time and place a calendar event can give, as event fields with source "calendar"."""
    fields: Dict[str, Dict[str, Any]] = {}
    if not cal_event.get("all_day"):
        start = headsups.event_start(cal_event, tz_name)
        if start is not None:
            local = local_now(tz_name, start)
            value = f"{local.hour:02d}:{local.minute:02d}"
            fields["time"] = {"value": value, "said": event_fields.format_clock(value), "ambiguous": False, "assumed": None, "source": event_fields.SOURCE_CALENDAR}
    place = _place(cal_event.get("location", ""))
    if place:
        fields["where"] = {"value": place, "source": event_fields.SOURCE_CALENDAR}
    return fields


def _name_hits(named: set, words: set, denied: Iterable[str] = ()) -> int:
    """How many of the names the person gave appear among `words`, spelled the same or as a near-identical variant ("Ashlyn" in a title that says "Ashlynn")."""
    return sum(1 for name in named if any(name == word or name_aliases.same_person(name, word, denied) for word in words))


def match_calendar_event(event: Dict[str, Any], siblings: List[Dict[str, Any]], cal_events: List[Dict[str, Any]], tz_name: str, denied: Iterable[str] = ()) -> Optional[Dict[str, Any]]:
    """The calendar event that is clearly this plan, or None. Same local day, then: the person or place they named is in its title or place (the one with the most such words, a tie is
    no match); or the plan's own key word is in exactly one calendar title that day and in no other plan of theirs that day."""
    day = event.get("due_date")
    same_day = [c for c in cal_events if _local_day(c, tz_name) == day]
    if not same_day:
        return None
    fields = event.get("fields") or {}
    named = _tokens(str((fields.get("with") or {}).get("value", ""))) | _tokens(str((fields.get("where") or {}).get("value", "")))
    if named:
        scored = sorted(((_name_hits(named, _tokens(c.get("title", "")) | _tokens(c.get("location", "")), denied), c) for c in same_day), key=lambda pair: pair[0], reverse=True)
        if scored and scored[0][0] >= 1 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return scored[0][1]
    key_words = _tokens(event.get("text", ""))
    if not key_words:
        return None
    sharing = [c for c in same_day if key_words & _tokens(c.get("title", ""))]
    if len(sharing) != 1:
        return None
    others = [s for s in siblings if s is not event and s.get("due_date") == day and key_words & _tokens(s.get("text", ""))]
    return sharing[0] if not others else None


def _hour_minute(field: Dict[str, Any]) -> Optional[tuple]:
    """(hour on a 12-hour dial, minute) of a stored time, from its clock value or, when that is unclear, from what was said ("7")."""
    source = field.get("value") or field.get("said") or ""
    found = re.match(r"\s*(\d{1,2})(?::(\d{2}))?", str(source))
    return (int(found.group(1)) % 12, int(found.group(2) or 0)) if found else None


def _agree(key: str, stored: Dict[str, Any], cal: Dict[str, Any]) -> bool:
    if key == "time":
        if stored.get("value") and not stored.get("ambiguous"):
            return stored["value"] == cal["value"]
        return _hour_minute(stored) == _hour_minute(cal)             # an unclear "7" is not contradicted by 7 PM
    ours, theirs = _tokens(str(stored.get("value", ""))) or {str(stored.get("value", "")).casefold()}, _tokens(str(cal.get("value", ""))) or {str(cal.get("value", "")).casefold()}
    return ours <= theirs or theirs <= ours


def overlay(events: List[Dict[str, Any]], cal_events: List[Dict[str, Any]], tz_name: str, *, use_place: bool = True, denied: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Copies of `events` with the calendar's time and place laid over each plan it clearly matches: a gap is filled (source "calendar"), an unclear or merely assumed time is settled,
    and a real disagreement is recorded under "calendar_differs" with the person's value left as it was. `use_place` False leaves the place alone (the person's discreet setting: a place is
    what someone glancing at the screen would see). Pure; the inputs are not modified."""
    out: List[Dict[str, Any]] = []
    for event in events:
        match = match_calendar_event(event, events, cal_events, tz_name, denied)
        if match is None:
            out.append(event)
            continue
        given = calendar_fields(match, tz_name)
        fields = {key: dict(value) for key, value in (event.get("fields") or {}).items() if isinstance(value, dict)}
        differs: Dict[str, Dict[str, Any]] = {}
        for key in ("time", "where") if use_place else ("time",):
            cal = given.get(key)
            if not cal:
                continue
            stored = fields.get(key)
            if stored is None:
                fields[key] = cal
            elif _agree(key, stored, cal):
                if key == "time" and (stored.get("ambiguous") or stored.get("assumed") or stored.get("source") == event_fields.SOURCE_CALENDAR):
                    fields[key] = cal                                       # the calendar settles what was unclear or only assumed
            else:
                differs[key] = cal
        out.append({**event, "fields": fields, **({"calendar_differs": differs} if differs else {})})
    return out


def with_calendar(username: str, tz_name: str, events: List[Dict[str, Any]], now: Optional[datetime] = None, *, db: Any = None, get_token: Any = None, read_range: Any = None) -> List[Dict[str, Any]]:
    """`events` with the person's calendar laid over them, or `events` unchanged when there is nothing to do or anything is wrong: they have not turned calendar heads-ups on, there is no
    connected calendar, it cannot be read, or no plan matches. Blocking (the calendar read is cached for an hour). Never raises."""
    if not events:
        return events
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            return events
        settings = headsups.get_settings(db, username)
        if not settings.get("enabled"):
            return events
        now = now or datetime.now(timezone.utc)
        raw = headsups._read_events(username, tz_name, now, get_token, read_range)
        if not raw:
            return events
        cal_events = headsups.eligible_events(raw, now, tz_name, settings)
        return overlay(events, cal_events, tz_name, use_place=not settings.get("discreet"), denied=name_aliases.denied_for(username, db)) if cal_events else events
    except Exception:
        logger.warning("[events] could not lay the calendar over what is coming up.", exc_info=True)
        return events
