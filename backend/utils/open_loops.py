"""Open loops: things the user said are coming up ("a date tomorrow", "interview Friday"), so the next conversation
after the day has passed can ask how it went. Memory gives Sonic a past; this gives it a near future.

How it stays on the right side of the identity rules (backend/components/sonic_profile.py):

- It is anchored to something the user actually said, never to Sonic's own wish to hear from them. The follow-up
  block forbids "I was thinking about you / waiting to hear", and the identity check backstops the wording.
- It asks once. A loop is marked asked when it is offered and never offered again, and it expires a week after its day.
- Sensitive things (health, legal, money, loss, therapy) are never stored as loops, by the prompt and again by a
  keyword filter. If the user raises those themselves, ordinary memory handles it.
- It stays quiet when the emotional layer is holding the energy down, under any safety context, on a closing message,
  mid-conversation (only at the start of one or after a real pause), and on any route but plain conversation.
- It is the user's own data: listed on the Memory page, deletable one by one, and wiped by "Clear all".

Capture costs nothing on most turns: a cheap keyword gate looks for future-time language, and only a hit makes one
lite-model call, off the response path.

Storage is one document per loop in `user_open_loops` (not another array on the facts document), capped per user and
expired by a TTL index.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from backend.utils import event_fields, memory_grounding, name_aliases
from backend.utils.time_utils import gap_seconds, local_now

logger = logging.getLogger("SASS Logger")

COLLECTION = "user_open_loops"
MAX_OPEN_LOOPS = 12
MAX_TEXT_CHARS = 140
MAX_HORIZON_DAYS = 60
EXPIRE_DAYS_AFTER_DUE = 7
# Offered only at the start of a conversation, or when they are back after at least this long.
FOLLOW_UP_MIN_GAP_SECONDS = 2 * 3600
MAX_MESSAGE_CHARS = 2000

OPEN, ASKED, RESOLVED, CANCELLED = "open", "asked", "resolved", "cancelled"      # cancelled: they said it fell through; never asked about, gone with the rest on expiry
EVENTS_CACHE_SECONDS = 60       # the reply is told what is coming up on every conversational turn, so the read is kept this long (and dropped on any change we make)
# "sensitive" is something the model may answer with but is never stored.
ALLOWED_KINDS = ("social", "work", "travel", "errand", "event")

_index_ready = False


def open_loops_enabled() -> bool:
    """A kill switch (OPEN_LOOPS_ENABLED=false) for the whole feature."""
    return os.getenv("OPEN_LOOPS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------------------------
# The cheap gate in front of the model call
# ---------------------------------------------------------------------------------------------------------------

_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"  # full names only: "sat", "sun", "wed" are ordinary words
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ORDINAL_WORD = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|twenty[- ](?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)|thirtieth|thirty[- ]first)"
# The things people say are coming up, for the one cue that is too common on its own ("later", "in the morning").
_EVENT_NOUN = r"(?:interview|meeting|demo|date|exam|final|flight|presentation|appointment|game|match|wedding|party|recital|test|launch|deadline|trip|call|review|defense|pitch|dinner|show|concert|race|class|shift)"
_FUTURE_CUE_RE = re.compile(
    r"\b(?:"
    r"tomorrow|tonight|later today|later this week|coming up|upcoming"
    r"|this (?:morning|afternoon|evening|weekend|week)|(?:on|over) the weekend|in the (?:morning|evening|afternoon)"
    r"|next (?:week|month|weekend|few days)|(?:the )?(?:rest|end|start|beginning|middle) of (?:the |this |next )?(?:week|month)"
    r"|" + _WEEKDAY +  # "interview Friday", "on Friday", "next Friday": a weekday name alone is enough to ask the model
    r"|(?:in|within) (?:a|an|\d+|a couple of|a few|two|three|four|five|six|seven) (?:minutes?|hours?|days?|weeks?)"
    r"|(?:a|one|two|three|\d+) (?:days?|weeks?) from (?:today|now|" + _WEEKDAY + r")"
    r"|" + _MONTH + r"\.? \d{1,2}"
    r"|\d{1,2}/\d{1,2}"
    r"|on the (?:\d{1,2}(?:st|nd|rd|th)|" + _ORDINAL_WORD + r")"
    r"|after (?:work|school|class|lunch|dinner)"
    r"|at \d{1,2}(?::\d{2})? ?(?:am|pm)"
    r"|(?:leaves|leaving|departs|starts|begins|kicks off|is due|are due) at"
    r"|" + _EVENT_NOUN + r"s?\b[^.!?\n]{0,40}\blater"
    r")\b",
    re.IGNORECASE,
)
# A message that reports on something that already happened ("it went great", "got the job"): only worth a model call
# when there is a loop due that it might close.
_PAST_REPORT_RE = re.compile(
    r"\b(?:went|was|were|did|got|had|yesterday|last night|turned out|ended up|how it)\b", re.IGNORECASE
)


# A message that gives a detail of something already coming up ("the date is at 7 silly", "it's at Tidos"), or says it fell through. It carries no future-time word of its own, so
# the gate above misses it; with an event already noted for the next day or two it is worth a look.
_DETAIL_CUE_RE = re.compile(
    r"\b(?:at|around|by|@)\s*\d{1,2}(?::\d{2})?\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?[ap]\.?m\b|\b(?:noon|midnight)\b"
    r"|\b(?:cancel+ed|called off|fell through|not going|won't be going|isn't happening|no longer happening)\b"
    r"|\b(?:with|at)\s+(?-i:[A-Z][a-z]{2,})",      # "at Tidos", "with Ashlyn": a capitalised word, so "with the" and "at home" do not count
    re.IGNORECASE,
)


def should_extract(message: str, has_due_loop: bool = False, has_upcoming: bool = False, confirming: bool = False) -> bool:
    text = (message or "").strip()
    if not text or len(text) > MAX_MESSAGE_CHARS:
        return False
    if _FUTURE_CUE_RE.search(text):
        return True
    # A "yes" to Sonic's own question about a detail ("So the date is at 7 at Tidos, right?") carries no time word of its own, but confirms what the question named.
    if has_upcoming and (confirming or _DETAIL_CUE_RE.search(text)):
        return True
    return has_due_loop and bool(_PAST_REPORT_RE.search(text))


_SENSITIVE_RE = re.compile(
    r"\b(?:doctor|dr\.|surgery|surgeon|diagnos\w*|therap\w*|psychiatr\w*|psycholog\w*|counsel+ing|hospital|biopsy|chemo\w*|"
    r"oncolog\w*|pregnan\w*|scan results?|test results?|medication|prescription|rehab|detox|hospice|funeral|memorial|burial|"
    r"autopsy|passed away|court|lawyer|attorney|custody|divorce|lawsuit|sentenc\w*|bankrupt\w*|eviction|foreclos\w*|"
    r"immigration|abortion|miscarriage|cancer|overdose|suicid\w*)\b",
    re.IGNORECASE,
)


def is_sensitive(text: str) -> bool:
    """Whether text touches health, legal, money trouble, loss or therapy, which is never kept as an open loop."""
    return bool(_SENSITIVE_RE.search(text or ""))


# ---------------------------------------------------------------------------------------------------------------
# Validating what the model returned
# ---------------------------------------------------------------------------------------------------------------

# Words that give a DATE (not just a time of day): the day itself, a weekday, a month and day, a numeric date, "in 3 days", "next week", "the weekend". The person has to have said
# one for a date to be theirs.
_DATE_CUE_RE = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|last night|later today|this (?:morning|afternoon|evening|weekend|week)|(?:on|over) the weekend|next (?:week|month|weekend)|(?:the )?(?:rest|end) of (?:the |this |next )?(?:week|month)"
    r"|" + _WEEKDAY + r"|" + _MONTH + r"\.? \d{1,2}|\d{1,2}/\d{1,2}|on the (?:\d{1,2}(?:st|nd|rd|th)|" + _ORDINAL_WORD + r")"
    r"|(?:in|within) (?:a|an|\d+|a couple of|a few|two|three|four|five|six|seven) (?:days?|weeks?|months?)|(?:a|one|two|three|\d+) (?:days?|weeks?) from (?:today|now|" + _WEEKDAY + r"))\b",
    re.IGNORECASE,
)
_WEEKDAY_WORD_RE = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def due_date_issue(due: str, *, user_text: str, previous_reply: str = "", known_dates: Iterable[str] = ()) -> Optional[str]:
    """Why the date on a loop is not the person's own, or None when it is. The model is asked for a calendar date, and when the person gave no day it filled one in (an interview
    mentioned in passing was dated "today", and a reply then wished them luck with it). A date counts as the person's when their words (or the question they just said yes to) hold a
    day word, or when it is the date of something already noted (a later detail of the same event). When the only day they gave is a weekday, the date has to fall on it.
    Reasons are plain labels, never text. Never raises."""
    try:
        if due in set(known_dates):
            return None
        source = f"{user_text or ''}\n{memory_grounding.confirmed_text(user_text or '', previous_reply)}"
        if not _DATE_CUE_RE.search(source):
            return "no day given"
        said = {w.lower() for w in _WEEKDAY_WORD_RE.findall(source)}
        others = _DATE_CUE_RE.sub(lambda m: "" if _WEEKDAY_WORD_RE.fullmatch(m.group(0)) else "x", source)
        if said and "x" not in others and _WEEKDAYS[date.fromisoformat(due).weekday()] not in said:
            return "weekday does not match"
    except Exception:
        return None
    return None


def validate_loop(raw: Any, today: date) -> Optional[Dict[str, str]]:
    """A cleaned `{text, due_date, kind}` for one model-proposed loop, or None. The date must be real and today or
    later (up to about two months out), the text short and not sensitive, the kind a known one. The model is never
    trusted to have done the date arithmetic: a date that is wrong in an obvious way is dropped, not guessed."""
    if not isinstance(raw, dict):
        return None
    text = " ".join(str(raw.get("what") or "").split())
    kind = str(raw.get("kind") or "").strip().lower()
    if not text or len(text) > MAX_TEXT_CHARS or kind not in ALLOWED_KINDS or is_sensitive(text):
        return None
    try:
        due = date.fromisoformat(str(raw.get("due_date") or "").strip())
    except ValueError:
        return None
    if due < today or due > today + timedelta(days=MAX_HORIZON_DAYS):
        return None
    return {"text": text, "due_date": due.isoformat(), "kind": kind}


def _tokens(text: str) -> set:
    return {word for word in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(word) > 2}


def is_duplicate(loop: Dict[str, Any], others: Iterable[Dict[str, Any]]) -> bool:
    """The same thing on the same day, worded differently ("has a first date" / "has a date tomorrow night")."""
    mine = _tokens(loop.get("text", ""))
    for other in others:
        if other.get("due_date") != loop.get("due_date"):
            continue
        theirs = _tokens(other.get("text", ""))
        if not mine or not theirs:
            continue
        if len(mine & theirs) / len(mine | theirs) >= 0.4:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------
# Storage (one document per loop; blocking, so call off the event loop)
# ---------------------------------------------------------------------------------------------------------------

def _ensure_indexes(db) -> None:
    global _index_ready
    if _index_ready:
        return
    db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    db[COLLECTION].create_index("username")
    _index_ready = True


def list_loops(db, username: str, statuses: Iterable[str] = (OPEN,)) -> List[Dict[str, Any]]:
    wanted = set(statuses)
    docs = db[COLLECTION].find({"username": username}, {"_id": 0})
    return sorted((d for d in docs if d.get("status") in wanted), key=lambda d: d.get("due_date", ""))


_events_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _invalidate(username: str) -> None:
    _events_cache.pop(username, None)


def load_open_loops(username: str) -> List[Dict[str, Any]]:
    """The user's open loops, or [] with no database (blocking, so call off the event loop)."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return list_loops(db, username) if db is not None else []


def load_open_loops_cached(username: str) -> List[Dict[str, Any]]:
    """The same list, kept for EVENTS_CACHE_SECONDS: the reply is told what is coming up on every conversational turn, and most turns should not read the store. Any save, status
    change or delete made here drops the person's entry. Blocking on a miss."""
    held = _events_cache.get(username)
    if held is not None and monotonic() - held[0] < EVENTS_CACHE_SECONDS:
        return held[1]
    loops = load_open_loops(username)
    if len(_events_cache) >= 2000:
        _events_cache.clear()                  # bounded: a few thousand people's lists at most, then it starts again
    _events_cache[username] = (monotonic(), loops)
    return loops


def upcoming_for_page(username: str) -> List[Dict[str, Any]]:
    """The Memory page's list, soonest first: what is coming up (open, still shown for the week after its day, greyed) and what was cancelled (so it can be restored)."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return [public_view(doc) for doc in list_loops(db, username, (OPEN, CANCELLED))] if db is not None else []


def edit_for_page(username: str, loop_id: str, changes: Dict[str, Any], today: date) -> Optional[Dict[str, Any]]:
    """edit_event for the endpoint (a database of its own). None for no such event or no database; raises EventEditError."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return edit_event(db, username, loop_id, changes, datetime.now(timezone.utc), today) if db is not None else None


def forget_loop(username: str, loop_id: str) -> bool:
    from backend.utils.db_utils import get_db

    db = get_db()
    return delete_loop(db, username, loop_id) if db is not None else False


def forget_all_loops(username: str) -> int:
    """Part of "forget everything": loops are the user's data too."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return delete_all_loops(db, username) if db is not None else 0


def mark_asked(username: str, loop_id: str, now: Optional[datetime] = None) -> bool:
    """Records that a loop was offered, so it is never offered again. Never raises."""
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is None:
            return False
        return set_status(db, username, loop_id, ASKED, now or datetime.now(timezone.utc))
    except Exception:
        logger.warning("[open loops] could not mark a loop as asked.", exc_info=True)
        return False


def _denied_names(db, username: str) -> List[str]:
    """The name pairs this person said are different people; [] when they cannot be read (two near-identical spellings may then be merged, which they can fix). Blocking."""
    try:
        return name_aliases.get_denied(db, username)
    except Exception:
        logger.warning("[open loops] could not read the names marked as different.", exc_info=True)
        return []


def _same_event(loop: Dict[str, Any], fields: Dict[str, Any], other: Dict[str, Any], denied: Iterable[str] = ()) -> bool:
    """Whether a newly mentioned thing is one already noted: the same day and kind, and either worded alike (the old rule) or sharing the same person or place. Two things on the
    same day with different people ("the date with Ashlyn", "dinner with Sam") are two events; "Ashlynn" and "Ashlyn" are one person unless they said otherwise."""
    if other.get("due_date") != loop.get("due_date") or other.get("kind") != loop.get("kind"):
        return False
    theirs = other.get("fields") or {}
    if not event_fields.compatible(theirs, fields, denied):
        return False
    return is_duplicate(loop, [other]) or event_fields.shares_a_value(theirs, fields, denied)


def save_events(db, username: str, items: List[Dict[str, Any]], now: datetime, denied: Iterable[str] = ()) -> Dict[str, Any]:
    """Stores what was noted: `items` is [{"loop": {text, due_date, kind}, "fields": {...}}]. A thing already noted is not added twice: a detail it was missing FILLS it, a different
    one CORRECTS it (the person's latest words win), and the rest is left alone. New ones are capped per person. Returns {"saved", "filled", "corrected", "events"}, where `events` are
    the changed or created events (for the one line shown under the reply). Blocking."""
    _ensure_indexes(db)
    existing = list_loops(db, username, (OPEN, ASKED))
    open_count = sum(1 for d in existing if d.get("status") == OPEN)
    result: Dict[str, Any] = {"saved": 0, "filled": 0, "corrected": 0, "events": []}
    for item in items:
        loop, fields = item["loop"], item.get("fields") or {}
        match = next((d for d in existing if _same_event(loop, fields, d, denied)), None)
        if match is not None:
            merged, changes = event_fields.merge_fields(match.get("fields"), fields)
            if changes:
                update: Dict[str, Any] = {"fields": merged}
                if any(kind == "corrected" for kind, _ in changes):
                    update["corrected_at"] = now.isoformat()
                if any(merged[key].get("source") == event_fields.SOURCE_CONFIRMED for _, key in changes):
                    update["confirmed_at"] = now.isoformat()
                db[COLLECTION].update_one({"username": username, "id": match["id"]}, {"$set": update})
                match.update(update)
                result["filled"] += sum(1 for kind, _ in changes if kind == "filled")
                result["corrected"] += sum(1 for kind, _ in changes if kind == "corrected")
                result["events"].append({"event": dict(match), "updated": True})
            continue
        if open_count + result["saved"] >= MAX_OPEN_LOOPS:
            continue
        due = date.fromisoformat(loop["due_date"])
        doc = {
            "id": uuid.uuid4().hex,
            "username": username,
            "text": loop["text"],
            "due_date": loop["due_date"],
            "kind": loop["kind"],
            "fields": fields,
            "status": OPEN,
            "created_at": now.isoformat(),
            "asked_at": None,
            "corrected_at": None,
            "confirmed_at": now.isoformat() if any(f.get("source") == event_fields.SOURCE_CONFIRMED for f in fields.values()) else None,
            # Gone from the database a day after the week-long window closes.
            "expires_at": datetime.combine(due + timedelta(days=EXPIRE_DAYS_AFTER_DUE + 1), time.min, tzinfo=timezone.utc),
        }
        db[COLLECTION].insert_one(doc)
        existing.append(doc)
        result["saved"] += 1
        result["events"].append({"event": dict(doc), "updated": False})
    if result["saved"] or result["filled"] or result["corrected"]:
        _invalidate(username)
    return result


def save_loops(db, username: str, loops: List[Dict[str, str]], now: datetime) -> int:
    """Stores new loops with no fields, skipping duplicates and anything past the per-user cap. Returns how many were saved."""
    return save_events(db, username, [{"loop": loop, "fields": {}} for loop in loops], now)["saved"]


def set_status(db, username: str, loop_id: str, status: str, now: datetime) -> bool:
    fields: Dict[str, Any] = {"status": status}
    if status == ASKED:
        fields["asked_at"] = now.isoformat()
    result = db[COLLECTION].update_one({"username": username, "id": loop_id}, {"$set": fields})
    _invalidate(username)
    return getattr(result, "matched_count", 0) > 0


def delete_loop(db, username: str, loop_id: str) -> bool:
    _invalidate(username)
    return getattr(db[COLLECTION].delete_one({"username": username, "id": loop_id}), "deleted_count", 0) > 0


def delete_all_loops(db, username: str) -> int:
    _invalidate(username)
    return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0) or 0)


class EventEditError(ValueError):
    """The edit cannot be made; the message is safe to show the person."""


def _expiry(due: date) -> datetime:
    return datetime.combine(due + timedelta(days=EXPIRE_DAYS_AFTER_DUE + 1), time.min, tzinfo=timezone.utc)


def edit_event(db, username: str, loop_id: str, changes: Dict[str, Any], now: datetime, today: date) -> Optional[Dict[str, Any]]:
    """The person corrects something coming up on the Memory page. `changes` may hold: "with" and "where" (text; empty clears it back to NOT KNOWN), "time" (24-hour "19:00" from
    the page, or "7 PM" typed; empty clears it; a bare "7" is refused because the page is the one place nothing is assumed), "due_date" (a day from today to MAX_HORIZON_DAYS ahead),
    "status" ("cancelled", or "open" to restore a cancelled one) and "confirm" (true: they checked it and it is right). What they write is marked "you wrote this". Returns the
    event as the page shows it, None when there is no such event, and raises EventEditError (with a message for the person) when a value cannot be kept. Blocking."""
    docs = list(db[COLLECTION].find({"username": username, "id": loop_id}, {"_id": 0}))
    if not docs:
        return None
    doc = docs[0]
    fields = {key: dict(value) for key, value in (doc.get("fields") or {}).items() if isinstance(value, dict)}
    update: Dict[str, Any] = {}
    replaced = False
    for key, limit in (("with", event_fields.MAX_WITH_CHARS), ("where", event_fields.MAX_WHERE_CHARS)):
        if key not in changes:
            continue
        raw = changes[key]
        if raw is None or not str(raw).strip():
            fields.pop(key, None)
            continue
        problem = event_fields.value_problem(raw, limit)
        if problem:
            raise EventEditError(problem)
        value = event_fields.clean_value(raw, limit)
        replaced = replaced or (key in fields and str(fields[key].get("value", "")).casefold() != value.casefold())
        fields[key] = {"value": value, "source": event_fields.SOURCE_EDIT}
    if "time" in changes:
        raw = changes["time"]
        if raw is None or not str(raw).strip():
            fields.pop("time", None)
        else:
            parsed = event_fields.parse_time(str(raw).strip(), doc.get("kind", ""), "")
            if parsed is None:
                raise EventEditError("That isn't a time. Use something like 7 PM or 19:00.")
            if parsed["ambiguous"] or parsed["assumed"]:
                raise EventEditError("Say AM or PM (for example 7 PM), or use a 24-hour time like 19:00.")
            replaced = replaced or ("time" in fields and fields["time"].get("value") != parsed["value"])
            fields["time"] = {"value": parsed["value"], "said": event_fields.format_clock(parsed["value"]), "ambiguous": False, "assumed": None, "source": event_fields.SOURCE_EDIT}
    if "due_date" in changes:
        try:
            due = date.fromisoformat(str(changes["due_date"]).strip())
        except ValueError:
            raise EventEditError("That isn't a date.")
        if due < today or due > today + timedelta(days=MAX_HORIZON_DAYS):
            raise EventEditError(f"Pick a day from today to {MAX_HORIZON_DAYS} days ahead.")
        if due.isoformat() != doc.get("due_date"):
            update["due_date"] = due.isoformat()
            update["expires_at"] = _expiry(due)
    if "status" in changes:
        wanted = changes["status"]
        if wanted not in (OPEN, CANCELLED):
            raise EventEditError("An event is either coming up or cancelled.")
        if doc.get("status") not in (OPEN, CANCELLED) and wanted != doc.get("status"):
            raise EventEditError("Sonic has already asked how that went, so it can't be changed now.")
        if wanted != doc.get("status"):
            update["status"] = wanted
    if changes.get("confirm") is True:
        update["confirmed_at"] = now.isoformat()
    if fields != (doc.get("fields") or {}):
        update["fields"] = fields
        if replaced:
            update["corrected_at"] = now.isoformat()
    if update:
        db[COLLECTION].update_one({"username": username, "id": loop_id}, {"$set": update})
        _invalidate(username)
    return public_view({**doc, **update})


def public_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What the Memory page is shown for a loop: its own words, its day, and what is known about it (each field with where it came from; a field that was never said is simply
    absent, which the page shows as not known). Nothing internal."""
    view = {key: doc.get(key) for key in ("id", "text", "due_date", "kind", "status", "corrected_at", "confirmed_at")}
    fields = doc.get("fields") or {}
    view["fields"] = {
        key: {**fields[key], "display": event_fields.describe_time(fields[key]) if key == "time" else fields[key].get("value", "")}
        for key in event_fields.FIELD_KEYS if isinstance(fields.get(key), dict)
    }
    return view


# ---------------------------------------------------------------------------------------------------------------
# Choosing a follow-up for this turn
# ---------------------------------------------------------------------------------------------------------------

def _due_for_follow_up(loop: Dict[str, Any], today: str) -> bool:
    """Open, its day has passed, and still inside the week it is worth asking in."""
    if loop.get("status") != OPEN:
        return False
    try:
        due = date.fromisoformat(str(loop.get("due_date")))
        today_date = date.fromisoformat(today)
    except ValueError:
        return False
    return due < today_date <= due + timedelta(days=EXPIRE_DAYS_AFTER_DUE)


def select_followup(
    load_loops: Callable[[], List[Dict[str, Any]]],
    *,
    today: str,
    now: datetime,
    last_message_at: Optional[datetime],
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
) -> Optional[Dict[str, Any]]:
    """The one loop to ask about this turn, or None. Every cheap gate is checked before `load_loops` (a database
    read) is called, so most turns never touch the store."""
    if risk_active or closing or energy_tier != "open" or source_type != "conversational":
        return None
    seconds = gap_seconds(last_message_at, now)
    if last_message_at is not None and (seconds is None or seconds < FOLLOW_UP_MIN_GAP_SECONDS):
        return None  # mid-conversation: only the start of one, or a real pause, is the moment to ask
    try:
        loops = load_loops() or []
    except Exception:
        logger.warning("[open loops] could not read the loops.", exc_info=True)
        return None
    due = [loop for loop in loops if _due_for_follow_up(loop, today)]
    if not due:
        return None
    return max(due, key=lambda loop: loop.get("due_date", ""))


def describe_when(due_date: str, today: str) -> str:
    try:
        due = date.fromisoformat(due_date)
        today_date = date.fromisoformat(today)
    except ValueError:
        return "recently"
    if due == today_date - timedelta(days=1):
        return "yesterday"
    return f"on {due.strftime('%A, %B')} {due.day}"


def build_open_loop_block(loop: Optional[Dict[str, Any]], today: str) -> str:
    """The prompt section for an offered loop ("" when none). The model may use it or skip it."""
    if not loop:
        return ""
    fields = loop.get("fields") or {}
    known = [f"with {fields['with']['value']}" if fields.get("with") else "", f"at {fields['where']['value']}" if fields.get("where") else ""]
    detail = f" ({', '.join(k for k in known if k)}; you may use these names, they told you them)" if any(known) else ""
    return (
        "\nOPEN LOOP (optional, once): "
        f"{describe_when(loop.get('due_date', ''), today)} the user mentioned something coming up: "
        f"\"{loop.get('text', '')}\"{detail}. If, and only if, it fits naturally, ask once how it went, as one short, casual "
        "clause. Answer what they actually asked first, and don't make it the point of the reply. If they've already "
        "told you how it went, don't ask: respond to what they said. Never say you were thinking about them, waiting, "
        "wondering, or looking forward to hearing; never mention that you keep track of things or remembered it on "
        "purpose; it's just something they told you that you recall. If the moment isn't right, skip it entirely.\n"
    )


# ---------------------------------------------------------------------------------------------------------------
# Capture (runs after the reply, off the response path)
# ---------------------------------------------------------------------------------------------------------------

def _response_text(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def parse_extraction(raw_text: str) -> Dict[str, Any]:
    """The model's JSON answer as a dict ({} if it isn't usable)."""
    cleaned = (raw_text or "").replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


MAX_PREVIOUS_REPLY_CHARS = 400


def previous_reply(history: List[Any]) -> str:
    """What Sonic said last, if the conversation so far ends on a reply of its own, clipped; "" otherwise. Context only, so a bare
    answer ("Friday at 2") to Sonic's question can be tied to what it was about."""
    if not history or getattr(history[-1], "type", "") != "ai":
        return ""
    text = " ".join(_response_text(history[-1]).split())
    return text[-MAX_PREVIOUS_REPLY_CHARS:]


def _existing_lines(loops: List[Dict[str, Any]]) -> str:
    """What is already noted, for the model to tell a new thing from a detail or a correction of one it has: each line carries the fields that are known."""
    if not loops:
        return "(none)"
    lines = []
    for d in loops:
        fields = d.get("fields") or {}
        known = []
        if fields.get("time"):
            known.append(f"time {event_fields.describe_time(fields['time'])}")
        if fields.get("with"):
            known.append(f"with {fields['with']['value']}")
        if fields.get("where"):
            known.append(f"at {fields['where']['value']}")
        extra = f"; {'; '.join(known)}" if known else ""
        lines.append(f"{d.get('id')}: {d.get('text')} ({d.get('kind')}, due {d.get('due_date')}{extra})")
    return "\n".join(lines)


async def capture_open_loops(
    username: str,
    message: str,
    tz_name: Optional[str],
    *,
    risk_active: bool = False,
    previous_reply: str = "",
    now: Optional[datetime] = None,
    invoke: Optional[Callable[[str], Awaitable[Any]]] = None,
    get_db_fn: Optional[Callable[[], Any]] = None,
) -> Dict[str, int]:
    """Notes any upcoming thing in this message and closes any loop it reports on. Never raises (it runs as a
    background task); returns `{"captured": n, "resolved": m}` for tests and logging."""
    result = {"captured": 0, "resolved": 0}
    try:
        if not open_loops_enabled() or risk_active or is_sensitive(message):
            return result
        # Most messages mention nothing about time at all, so the store isn't even read for them.
        confirming = bool(memory_grounding.confirmed_text(message or "", previous_reply))      # a short yes to a question Sonic just asked
        if not (_FUTURE_CUE_RE.search(message or "") or _PAST_REPORT_RE.search(message or "") or _DETAIL_CUE_RE.search(message or "") or confirming):
            return result
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        db = get_db_fn()
        if db is None:
            return result
        moment = now or datetime.now(timezone.utc)
        local = local_now(tz_name, moment)
        today = local.date()
        existing = await asyncio.to_thread(list_loops, db, username, (OPEN,))
        # A loop whose day has arrived or passed might be what a past-tense message is reporting on.
        has_due = any(d.get("due_date", "") <= today.isoformat() for d in existing)
        # Something already noted for today or tomorrow: a message that gives it a time, a person or a place, or says it fell through, is about it.
        has_upcoming = any(today.isoformat() <= d.get("due_date", "") <= (today + timedelta(days=1)).isoformat() for d in existing)
        if not should_extract(message, has_due, has_upcoming, confirming):
            return result

        from backend.components.constraints import OPEN_LOOP_EXTRACTION_PROMPT

        prompt = OPEN_LOOP_EXTRACTION_PROMPT.format(
            today=today.isoformat(), weekday=local.strftime("%A"), clock=local.strftime("%I:%M %p").lstrip("0"),
            existing=_existing_lines(existing), message=message.strip()[:MAX_MESSAGE_CHARS],
            previous=(previous_reply or "").strip()[-MAX_PREVIOUS_REPLY_CHARS:] or "(none)",
        )
        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.ainvoke
        parsed = parse_extraction(_response_text(await invoke(prompt)))

        items: List[Dict[str, Any]] = []
        for raw in (parsed.get("loops") or [])[:3]:
            loop = validate_loop(raw, today)
            # Honest writes (backend/utils/memory_grounding.py): a loop whose wording carries a time, number, weekday or month the person did not give is not saved.
            if not loop or memory_grounding.ground_note(loop["text"], user_text=message, previous_reply=previous_reply).dropped:
                continue
            # Its DATE has to be the person's too: with no day in their words the model had to guess one, and a guess saved as an event gets said back as fact.
            issue = due_date_issue(loop["due_date"], user_text=message, previous_reply=previous_reply, known_dates=[d.get("due_date", "") for d in existing])
            if issue:
                logger.info("[open loops] a loop whose date was not the person's own was not saved (%s).", issue)
                continue
            # A role the person never said ("reviewed by a colleague" for a message that named none) is an invented reading: the line is not saved.
            if event_fields.invented_role(loop["text"], f"{message}\n{memory_grounding.confirmed_text(message, previous_reply)}"):
                logger.info("[open loops] a line with a role the person did not say was not saved.")
                continue
            # Its time, person and place are kept only if they are the person's own words (or the question they just said yes to); each one that is not is left NOT KNOWN.
            fields = event_fields.grounded_fields(raw, kind=loop["kind"], user_text=message, previous_reply=previous_reply)
            items.append({"loop": loop, "fields": fields})
        known_ids = {d.get("id") for d in existing}
        resolved_ids = [i for i in (parsed.get("resolved_ids") or []) if isinstance(i, str) and i in known_ids]
        cancelled_ids = [i for i in (parsed.get("cancelled_ids") or []) if isinstance(i, str) and i in known_ids and i not in resolved_ids]
        noted: List[str] = []

        if items:
            # The names this person said are different people are never merged, however alike (backend/utils/name_aliases.py).
            denied = await asyncio.to_thread(_denied_names, db, username) if any(i["fields"].get("with") for i in items) else []
            saved = await asyncio.to_thread(save_events, db, username, items, moment, denied)
            result["captured"] = saved["saved"]
            noted += [event_fields.noted_line(entry["event"], today, updated=entry["updated"]) for entry in saved["events"]]
        for loop_id in resolved_ids:
            if await asyncio.to_thread(set_status, db, username, loop_id, RESOLVED, moment):
                result["resolved"] += 1
        for loop_id in cancelled_ids:
            if await asyncio.to_thread(set_status, db, username, loop_id, CANCELLED, moment):
                gone = next((d for d in existing if d.get("id") == loop_id), {})
                noted.append(f"Noted: cancelled, {event_fields.second_person(gone.get('text', ''))}.")
        if noted:
            result["noted"] = noted[:2]                    # one or two short lines under the reply, never a list

        counts = {"open_loop_captured": result["captured"], "open_loop_resolved": result["resolved"]}
        if any(counts.values()):
            from backend.utils.self_counters import record_turn_counts

            await asyncio.to_thread(record_turn_counts, counts)
    except Exception:
        logger.warning("[open loops] capture failed for this turn.", exc_info=True)
    return result
