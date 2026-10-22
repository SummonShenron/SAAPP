"""What an upcoming thing may carry besides its day (a time, who it is with, where), and how Sonic says it, so a missing detail is KNOWN to be missing.

THE FAILURE (2026-10-20): Sonic held "a date tonight with Ashlyn" and "the Tidos bar" but no time. Nothing marked the time as missing, so it filled the gap ("nap, downtown by 7:30")
and one second later saved its own invention as if the person had said it. This module is the shape that makes the gap explicit (docs/memory-grounding-plan.md, step 3):

- A field exists only when it was SAID or CONFIRMED. No key means NOT KNOWN, and the reply is told so in a fixed sentence built here, which a model cannot paraphrase into a guess.
- Each field carries where it came from: "user" (their own words), "assistant_confirmed" (Sonic asked a yes/no question that named it and they said yes), "calendar" (step 4),
  "user_edit" (typed on the Memory page).
- A TIME is kept as it was said ("7"). It becomes a clock time only when that is certain: am/pm given, a 24-hour time, "noon", words that settle it ("tonight", "dinner"), or, by
  Jack's decision (2026-10-21), a SOCIAL plan with an hour of 1 to 11 and no am/pm, which is assumed to be PM and says so. Any other bare hour stays "AM or PM not said".
- Every value must appear in the person's own words (or the question they confirmed), checked mechanically with no model call; one that does not is dropped and the field stays unknown.
- The text is the person's own data and is shown back to them, but it is bounded and guarded: no links, nothing sensitive (a hospital is never a place), no street address.
- A later message FILLS a missing field, or CORRECTS a known one ("no, the date is at 7 silly"): the person's latest words win, whatever the earlier source was.
- Nothing here logs a value, and nothing here calls a model.
"""
import re
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.utils import memory_grounding, name_aliases

FIELD_KEYS = ("time", "with", "where")
SOURCE_USER, SOURCE_CONFIRMED, SOURCE_CALENDAR, SOURCE_EDIT = "user", "assistant_confirmed", "calendar", "user_edit"
SOURCES = (SOURCE_USER, SOURCE_CONFIRMED, SOURCE_CALENDAR, SOURCE_EDIT)
SOURCE_PHRASES = {SOURCE_USER: "you said", SOURCE_CONFIRMED: "you confirmed", SOURCE_CALENDAR: "your calendar", SOURCE_EDIT: "you wrote this"}
PERSON_SOURCES = (SOURCE_USER, SOURCE_CONFIRMED, SOURCE_EDIT)    # the person's own say-so: these may replace a known value

MAX_WITH_CHARS = 40
MAX_WHERE_CHARS = 60
MAX_SAID_CHARS = 12
MAX_EVENTS_IN_BLOCK = 3
BLOCK_DAYS_AHEAD = 1                 # an event today or tomorrow is put in front of the reply on its own
BLOCK_DAYS_WHEN_ASKED = 7            # and a week ahead when they ask what is coming up

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_LINK_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|io|co|dev|app)\b|@", re.IGNORECASE)
# A street address: a number, up to three words, then a street word ("1420 Maple Street", "55 Oak Ave"), or a unit. A venue or a neighbourhood is fine; where someone lives is not stored.
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){0,3}(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|court|ct|way|place|pl|terrace|highway|hwy|parkway|pkwy|circle|cir)\b\.?"
    r"|\b(?:apt|apartment|unit|suite|ste)\.?\s*#?\s*\w+\b",
    re.IGNORECASE,
)
_PRONOUNS = frozenset({"he", "she", "they", "him", "her", "them", "me", "you", "it", "someone", "somebody", "everyone", "anyone", "there", "here", "that", "this"})
_WORD_TOKEN_RE = re.compile(r"[a-z0-9']+")
_DESCRIPTION_STARTS = frozenset({"my", "the", "a", "an", "our", "his", "her", "their", "your", "some", "this", "that", "home", "work", "school", "campus", "downtown", "uptown"})


def _tokens(text: str) -> set:
    return set(_WORD_TOKEN_RE.findall((text or "").lower()))


# ---------------------------------------------------------------------------------------------------------------
# Cleaning a person or a place
# ---------------------------------------------------------------------------------------------------------------

# Words that are not who someone is with. "i'm going to have work claude review the changes" once came back as with: "work", which passed because the word was in the message.
_GENERIC_WITH = frozenset({"work", "job", "office", "school", "home", "someone", "somebody", "everyone", "them", "it", "ai", "assistant", "bot", "chatbot", "tool"})

# Roles the saved wording may carry only if the person said one of them: the model once wrote "reviewed by a colleague" for a message that named no colleague. Words in one group
# stand for each other ("mom" for "mother"), so a plain rewording is not an invention.
_ROLE_GROUPS = (
    {"colleague", "coworker", "co-worker", "workmate"}, {"boss", "manager", "supervisor"}, {"teammate", "team-mate"}, {"client", "customer"}, {"friend", "buddy"},
    {"sister", "sis"}, {"brother", "bro"}, {"mom", "mother", "mum", "mama"}, {"dad", "father", "papa"}, {"parent", "parents"},
    {"wife", "husband", "spouse", "partner", "girlfriend", "boyfriend", "fiance", "fiancee"}, {"son", "daughter", "kid", "child"},
    {"cousin"}, {"aunt", "uncle"}, {"grandma", "grandmother", "grandpa", "grandfather"}, {"neighbor", "neighbour"}, {"landlord"}, {"roommate"},
    {"mentor"}, {"recruiter", "interviewer"}, {"professor", "teacher", "advisor", "adviser"},
)


def invented_role(text: str, user_text: str) -> Optional[str]:
    """The first role word in `text` (a saved line about something coming up) that the person never said, or None. Used to refuse a line that casts someone the person did not mention."""
    said = (user_text or "").lower()
    for group in _ROLE_GROUPS:
        for role in group:
            if re.search(rf"\b{re.escape(role)}s?\b", (text or "").lower()) and not any(re.search(rf"\b{re.escape(other)}s?\b", said) for other in group):
                return role
    return None


def value_problem(value: Any, limit: int) -> Optional[str]:
    """Why this cannot be kept as a person or a place, in words for the person, or None when it can: too long, a link, a street address, a bare pronoun, or anything about health,
    law, money trouble, loss or therapy (the same rule open loops have always had)."""
    from backend.utils.open_loops import is_sensitive

    text = " ".join(_CONTROL_RE.sub(" ", str(value or "")).split()).strip(" .,;:")
    if not text:
        return "Say who or where, or clear it to leave it as not known."
    if len(text) > limit:
        return f"Keep it under {limit} characters."
    if _LINK_RE.search(text):
        return "Leave links and email addresses out."
    if _ADDRESS_RE.search(text):
        return "Sonic doesn't keep street addresses. A venue or a neighbourhood is fine."
    if is_sensitive(text):
        return "Sonic doesn't keep plans about health, legal or money trouble, loss or therapy."
    if text.lower() in _PRONOUNS:
        return "Use a name, or a description like \"my sister\"."
    return None


def clean_value(value: Any, limit: int) -> Optional[str]:
    """A person's name or a place, tidied, or None when it cannot be kept (see `value_problem`)."""
    if value_problem(value, limit):
        return None
    text = " ".join(_CONTROL_RE.sub(" ", str(value or "")).split()).strip(" .,;:")
    # Typed in lower case ("ashlyn", "tidos bar") it is shown as a name; "my mom" or "the bar" is a description and stays as written.
    if text == text.lower() and not text.split()[0] in _DESCRIPTION_STARTS:
        text = text.title()
    return text


# ---------------------------------------------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?(?:m\.?)?$", re.IGNORECASE)
_PM_CONTEXT_RE = re.compile(r"\b(?:tonight|evening|dinner|supper|night|after work|afternoon|happy hour|drinks)\b", re.IGNORECASE)
_AM_CONTEXT_RE = re.compile(r"\b(?:morning|breakfast|brunch|sunrise|first thing)\b", re.IGNORECASE)


def parse_time(said: Any, kind: str, context: str = "") -> Optional[Dict[str, Any]]:
    """{"value": "19:00" | None, "said": "7", "ambiguous": bool, "assumed": "pm" | "am" | None} for a time as the person wrote it, or None when it is not a time.
    `context` is the person's own words, which can settle am/pm ("tonight"). A bare hour of 1 to 11 for a SOCIAL plan is assumed PM and marked as assumed."""
    raw = " ".join(str(said or "").split())[:MAX_SAID_CHARS]
    lowered = raw.lower()
    if lowered in ("noon", "midday"):
        return {"value": "12:00", "said": raw, "ambiguous": False, "assumed": None}
    if lowered == "midnight":
        return {"value": "00:00", "said": raw, "ambiguous": False, "assumed": None}
    match = _TIME_RE.match(lowered)
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if minute > 59 or hour > 23:
        return None
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
        return {"value": f"{hour:02d}:{minute:02d}", "said": raw, "ambiguous": False, "assumed": None}
    if hour == 0 or hour >= 13 or (match.group(2) and hour >= 13):
        return {"value": f"{hour:02d}:{minute:02d}", "said": raw, "ambiguous": False, "assumed": None}    # a 24-hour time, said plainly
    if 1 <= hour <= 11:
        if _AM_CONTEXT_RE.search(context or ""):
            return {"value": f"{hour:02d}:{minute:02d}", "said": raw, "ambiguous": False, "assumed": None}
        if _PM_CONTEXT_RE.search(context or ""):
            return {"value": f"{hour + 12:02d}:{minute:02d}", "said": raw, "ambiguous": False, "assumed": None}
        if kind == "social":
            return {"value": f"{hour + 12:02d}:{minute:02d}", "said": raw, "ambiguous": False, "assumed": "pm"}
    return {"value": None, "said": raw, "ambiguous": True, "assumed": None}      # "7" or "12" with nothing to settle it: kept as said, never turned into a clock time


def format_clock(value: Optional[str]) -> str:
    """"19:00" as "7 PM", "19:30" as "7:30 PM"."""
    try:
        hour, minute = (int(part) for part in str(value).split(":"))
    except (TypeError, ValueError):
        return ""
    clock = f"{hour % 12 or 12}{f':{minute:02d}' if minute else ''}"
    return f"{clock} {'AM' if hour < 12 else 'PM'}"


def describe_time(field: Optional[Dict[str, Any]]) -> str:
    """The time as the reply and the page say it: what was said, what it was taken to mean, and what was assumed."""
    if not field:
        return "NOT KNOWN"
    if field.get("ambiguous") or not field.get("value"):
        return f"{field.get('said') or '?'} (they did not say AM or PM)"
    clock = format_clock(field["value"])
    if field.get("assumed"):
        return f"{clock} (they said {field.get('said')}; {field['assumed'].upper()} assumed for a social plan)"
    return clock


# ---------------------------------------------------------------------------------------------------------------
# Fields from what the model proposed, checked against what the person said
# ---------------------------------------------------------------------------------------------------------------

def _source_for(value_tokens: set, user_tokens: set, confirmed_tokens: set) -> Optional[str]:
    if value_tokens and value_tokens <= user_tokens:
        return SOURCE_USER
    if value_tokens and value_tokens <= (user_tokens | confirmed_tokens):
        return SOURCE_CONFIRMED
    return None


def grounded_fields(raw: Dict[str, Any], *, kind: str, user_text: str, previous_reply: str = "") -> Dict[str, Dict[str, Any]]:
    """The fields (time, with, where) the model proposed that are really the person's: each value must be in their own words, or in the question they just said yes to. One that is
    not is left out, so that field stays NOT KNOWN; the others are kept."""
    confirmed = memory_grounding.confirmed_text(user_text, previous_reply)
    user_tokens, confirmed_tokens = _tokens(user_text), _tokens(confirmed)
    source_text = f"{user_text or ''}\n{confirmed}"
    fields: Dict[str, Dict[str, Any]] = {}

    parsed = parse_time(raw.get("time_said"), kind, user_text)
    if parsed:
        said = parsed["said"]
        if said.lower() in ("noon", "midday", "midnight"):
            grounded = said.lower() in _tokens(source_text)
        else:
            grounded = not memory_grounding.ungrounded_kinds(said, user_text=source_text)
        if grounded:
            by_person = not memory_grounding.ungrounded_kinds(said, user_text=user_text)
            fields["time"] = {**parsed, "source": SOURCE_USER if by_person else SOURCE_CONFIRMED}

    for key, limit in (("with", MAX_WITH_CHARS), ("where", MAX_WHERE_CHARS)):
        value = clean_value(raw.get(key), limit)
        if not value or (key == "with" and value.casefold() in _GENERIC_WITH):
            continue
        source = _source_for(_tokens(value), user_tokens, confirmed_tokens)
        if source:
            fields[key] = {"value": value, "source": source}
    return fields


# ---------------------------------------------------------------------------------------------------------------
# Filling and correcting
# ---------------------------------------------------------------------------------------------------------------

def _same(key: str, old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    if key == "time":
        return (old.get("value"), str(old.get("said", "")).lower()) == (new.get("value"), str(new.get("said", "")).lower())
    return str(old.get("value", "")).casefold() == str(new.get("value", "")).casefold()


def merge_fields(existing: Optional[Dict[str, Any]], incoming: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Tuple[str, str]]]:
    """(the fields after the merge, [("filled" | "corrected", key), ...]). A field that was unknown is filled. A known one with a different value is CORRECTED: the person's
    latest words win over whatever was there (a calendar value, a confirmed one, an earlier message). The same value changes nothing. Neither argument is modified."""
    merged = {key: dict(value) for key, value in (existing or {}).items() if key in FIELD_KEYS and isinstance(value, dict)}
    changes: List[Tuple[str, str]] = []
    for key in FIELD_KEYS:
        new = incoming.get(key)
        if not new:
            continue
        old = merged.get(key)
        if old is None:
            merged[key] = dict(new)
            changes.append(("filled", key))
        elif not _same(key, old, new) and new.get("source") in PERSON_SOURCES:
            merged[key] = dict(new)
            changes.append(("corrected", key))
    return merged, changes


def _same_who(old: Dict[str, Any], new: Dict[str, Any], denied: Iterable[str]) -> bool:
    """The same person: the same name, or a near-identical spelling of it ("Ashlyn" / "Ashlynn"), unless they said those two are different people (backend/utils/name_aliases.py)."""
    return name_aliases.same_person(str(old.get("value", "")), str(new.get("value", "")), denied)


def compatible(existing: Optional[Dict[str, Any]], incoming: Dict[str, Dict[str, Any]], denied: Iterable[str] = ()) -> bool:
    """Whether an incoming event with these fields could be the same event as one that has `existing`: nothing that both know disagrees on WHO (a date with Ashlyn is not the
    dinner with Sam on the same day, but a date with "Ashlynn" is the date with "Ashlyn"). Where and time are not compared: those are the ones a correction changes."""
    old, new = (existing or {}).get("with"), incoming.get("with")
    if old and new:
        return _same_who(old, new, denied)
    return True


def shares_a_value(existing: Optional[Dict[str, Any]], incoming: Dict[str, Dict[str, Any]], denied: Iterable[str] = ()) -> bool:
    """The same person or place is in both: enough, with the same day and kind, to call two differently worded phrases one event."""
    for key in ("with", "where"):
        old, new = (existing or {}).get(key), incoming.get(key)
        if old and new and (_same_who(old, new, denied) if key == "with" else _same(key, old, new)):
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------
# Saying it
# ---------------------------------------------------------------------------------------------------------------

def _label(due: date, today: date) -> str:
    stamp = f"{due.strftime('%A')} {due.isoformat()}"
    if due == today:
        return f"Today, {stamp}"
    if due == today + timedelta(days=1):
        return f"Tomorrow, {stamp}"
    return stamp


def _parse_day(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def describe_field(key: str, field: Optional[Dict[str, Any]]) -> str:
    if not field:
        return "NOT KNOWN"
    body = describe_time(field) if key == "time" else str(field.get("value", ""))
    return f"{body} ({SOURCE_PHRASES.get(field.get('source'), 'you said')})"


def event_line(event: Dict[str, Any], today: date) -> str:
    due = _parse_day(event.get("due_date"))
    fields = event.get("fields") or {}
    parts = [f"{_label(due, today) if due else 'Soon'}: the user {event.get('text', 'has something coming up')}."]
    for key, name in (("with", "With"), ("where", "Where"), ("time", "Time")):
        parts.append(f"{name}: {describe_field(key, fields.get(key))}.")
    # Their calendar disagrees with what they said: they win, and the reply says there is a difference (event_calendar.py).
    for key, cal in (event.get("calendar_differs") or {}).items():
        shown = describe_time(cal) if key == "time" else str(cal.get("value", ""))
        parts.append(f"Their calendar has the {key} as {shown} instead: they told you otherwise, so use what they said, and mention the difference once if it comes up.")
    return " ".join(parts)


_PLANS_RE = re.compile(
    r"\bwhat(?:'s| is| do i have| have i got| am i doing)?\s+(?:on|coming up|planned|happening|going on|my (?:plans|schedule))\b"
    r"|\b(?:what|anything)\b[^.?!]{0,25}\b(?:do i have|have i got|am i doing)\b[^.?!]{0,25}\b(?:today|tonight|tomorrow|this week|this weekend|coming up|planned)\b"
    r"|\bmy (?:plans|schedule|week|day|evening)\b|\bwhat(?:'s| is) (?:today|tonight|tomorrow)\b|\bdo i have (?:anything|plans)\b|\bwhat time (?:is|was|am)\b",
    re.IGNORECASE,
)


def asks_about_plans(message: str) -> bool:
    """A question about their own plans or schedule, which widens the events shown from today and tomorrow to the week."""
    return bool(_PLANS_RE.search(message or ""))


def events_for_reply(events: List[Dict[str, Any]], today: date, *, wide: bool = False) -> List[Dict[str, Any]]:
    """The events the reply is told about: open ones from today to tomorrow (or a week when asked), soonest first, at most MAX_EVENTS_IN_BLOCK."""
    reach = today + timedelta(days=BLOCK_DAYS_WHEN_ASKED if wide else BLOCK_DAYS_AHEAD)
    keep = []
    for event in events:
        due = _parse_day(event.get("due_date"))
        if due and today <= due <= reach and event.get("status") == "open":
            keep.append((due, event))
    return [event for _, event in sorted(keep, key=lambda pair: pair[0])][:MAX_EVENTS_IN_BLOCK]


def build_events_block(events: List[Dict[str, Any]], today: date, *, wide: bool = False) -> str:
    """The prompt section for what is coming up, with every unknown spelled out ("" when there is nothing). Fixed wording built here, never left to the model."""
    shown = events_for_reply(events, today, wide=wide)
    if not shown:
        return ""
    lines = "\n".join(f"- {event_line(event, today)}" for event in shown)
    from_calendar = any(f.get("source") == SOURCE_CALENDAR for event in shown for f in (event.get("fields") or {}).values()) or any(event.get("calendar_differs") for event in shown)
    calendar_note = "A detail marked (your calendar) was read from their calendar, not told to you: say \"your calendar has...\" when you use it. " if from_calendar else ""
    return (
        "\n\nCOMING UP (only what they told you, nothing else. NOT KNOWN means they never said it: do not state it, guess it or plan around it):\n"
        f"{lines}\n"
        "If they ask about one of these, say what is known and that the rest is not known; if the missing detail matters to what they asked, ask for it. Never name a time, "
        "place or person that is not listed, and never suggest a schedule (when to leave, when to start) that depends on something NOT KNOWN. If a time is marked as assumed, "
        f"say it as an assumption. {calendar_note}\n"
    )


_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?[ap]\.?m\b|\bat\s+\d{1,2}\b|\b(?:noon|midnight)\b", re.IGNORECASE)
_HOUR_MINUTE_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _clock_key(text: str) -> Tuple[int, int]:
    """A clock time as (hour on a 12-hour dial, minute), so "7", "7pm", "7:00 PM" and "19:00" are the same time here (the check is about a time being named at all)."""
    if text.lower() in ("noon", "midnight"):
        return (0, 0)
    found = _HOUR_MINUTE_RE.search(text)
    return (int(found.group(1)) % 12, int(found.group(2) or 0)) if found else (-1, 0)


def _clock_keys(text: str) -> set:
    return {_clock_key(m.group(0)) for m in _CLOCK_RE.finditer(text or "")}


def reply_issue(reply: str, events: List[Dict[str, Any]], user_message: str = "") -> Optional[str]:
    """What the reply does wrong about a time, or None: it STATES a clock time (not asks about one) while an event it was told has no known time, and the time is not one the
    person just said or one the block gave for another event. Only checked when the block was shown, so a time about something else the person raised does not count."""
    if not reply or not any(not (event.get("fields") or {}).get("time") for event in events):
        return None
    allowed = _clock_keys(user_message)
    for event in events:
        for known in ((event.get("fields") or {}).get("time") or {}, (event.get("calendar_differs") or {}).get("time") or {}):
            for part in (known.get("value"), known.get("said")):
                if part:
                    allowed |= {_clock_key(str(part))}
    for sentence in _SENTENCE_RE.split(reply):
        if "?" in sentence:
            continue                                  # asking "is it at 7?" is how a missing time gets filled
        if any(key not in allowed for key in _clock_keys(sentence)):
            return "stated a time for something it was never told the time of"
    return None


_STARTS = (("has ", "you have "), ("is ", "you're "), ("are ", "you're "), ("will ", "you'll "), ("plans to ", "you plan to "), ("needs to ", "you need to "),
           ("wants to ", "you want to "), ("had ", "you had "), ("was ", "you were "))


def second_person(text: str) -> str:
    lowered = (text or "").strip()
    for start, replacement in _STARTS:
        if lowered.lower().startswith(start):
            return re.sub(r"\btheir\b", "your", replacement + lowered[len(start):], flags=re.IGNORECASE)
    return f'"{lowered}"' if lowered else "something coming up"


def noted_line(event: Dict[str, Any], today: date, *, updated: bool = False) -> str:
    """One short line shown under the reply when an event was saved or changed, so the person can see what was kept and correct it on the spot. Facts only: what is known, and
    that the time is not (the one gap that matters most)."""
    due = _parse_day(event.get("due_date"))
    when = ""
    if due:
        when = " today" if due == today else " tomorrow" if due == today + timedelta(days=1) else f" on {due.strftime('%A')}"
    fields = event.get("fields") or {}
    phrase = event.get("text", "")
    bits = [f"{second_person(phrase)}{when}"]
    # The short phrase may already say who it is with ("has dinner with their mother"); the line does not say it twice.
    if fields.get("with") and " with " not in f" {phrase.lower()} ":
        bits.append(f"with {fields['with']['value']}")
    if fields.get("where") and " at " not in f" {phrase.lower()} ":
        bits.append(f"at {fields['where']['value']}")
    line = " ".join(bits)
    line += f", {_short_time(fields['time'])}" if fields.get("time") else ", time not known"
    return f"{'Updated' if updated else 'Noted'}: {line}."


def _short_time(field: Dict[str, Any]) -> str:
    if field.get("ambiguous") or not field.get("value"):
        return f"{field.get('said') or '?'} (AM or PM not said)"
    clock = format_clock(field["value"])
    return f"{clock} (assumed)" if field.get("assumed") else clock
