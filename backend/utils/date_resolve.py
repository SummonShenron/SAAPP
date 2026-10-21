"""Relative days become real dates at the moment something is saved, so a note does not go on saying "tonight" or "Thursday" after the day has gone.

The failure this fixes (observed 2026-10-20): a saved note said "a date tonight" and "Thursday"; days later the words were still there, and Sonic reasoned from them as if
they were today ("mom tonight, since it's Tuesday"). The fix is mechanical and has no model in it: the day words in the text about to be saved are given the date they meant on
the day they were said, in the person's own timezone, right after the words, so the person's wording stays and the date sits beside it:

    "Has a date with Ashlyn tonight."          ->  "Has a date with Ashlyn tonight (Wednesday 2026-10-21)."
    "Has an interview Friday."                 ->  "Has an interview Friday (2026-10-23)."
    "Has a call tomorrow morning."             ->  "Has a call tomorrow (Thursday 2026-10-22) morning."

It annotates only what has one clear answer. Said on a Wednesday, "Wednesday" could be today or next week, and "next Friday" means different days to different people, so those
are left exactly as written rather than guessed (an unknown stays unknown). "this weekend", "next week" and "soon" are left alone for the same reason. Running it twice changes
nothing. It adds a date, never a time, a place or a name.
"""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from backend.utils.time_utils import local_now

MAX_ANNOTATIONS = 4        # a note that names more days than this is mostly about something else; the first few are enough

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY = "|".join(_WEEKDAYS)
_ABBREV = {name[:3]: name for name in _WEEKDAYS}

# One alternation, longest phrases first. Group "day" is the phrase that gets the date after it; a leading "on" / "this" is part of the match and the date goes after the weekday.
_PHRASE = re.compile(
    r"(?<![\w-])(?:"
    r"(?P<last_night>last night)"
    r"|(?P<today>today|tonight|this (?:morning|afternoon|evening)|later today)"
    r"|(?P<tomorrow>tomorrow)"
    r"|(?P<yesterday>yesterday)"
    r"|(?P<prefix>next|last|this|on|every)\s+(?P<pday>" + _DAY + r")"
    r"|(?P<day>" + _DAY + r")"
    r")(?![\w'’-])",
    re.IGNORECASE,
)
_DATE_AFTER = re.compile(r"\s*\((?:(?:" + _DAY + r"),?\s+)?\d{4}-\d{2}-\d{2}\)", re.IGNORECASE)


def _next_weekday(today: date, index: int) -> Optional[date]:
    """The next day (strictly after today) with this weekday, or None when it IS that weekday today: "Wednesday" said on a Wednesday could mean today or a week on."""
    ahead = (index - today.weekday()) % 7
    return None if ahead == 0 else today + timedelta(days=ahead)


def _previous_weekday(today: date, index: int) -> Optional[date]:
    behind = (today.weekday() - index) % 7
    return None if behind == 0 else today - timedelta(days=behind)


def _resolve(match: "re.Match", today: date) -> Optional[tuple]:
    """(the date the phrase meant, whether the weekday is already in the words) or None when there is no single clear answer."""
    if match.group("last_night") or match.group("yesterday"):
        return today - timedelta(days=1), False
    if match.group("today"):
        return today, False
    if match.group("tomorrow"):
        return today + timedelta(days=1), False
    word = (match.group("pday") or match.group("day") or "").lower()
    prefix = (match.group("prefix") or "").lower()
    if word not in _WEEKDAYS:
        return None
    index = _WEEKDAYS.index(word)
    if prefix == "last":
        found = _previous_weekday(today, index)
    elif prefix == "next" or prefix == "every":
        return None                          # "next Friday" is two different days to two different people; "every Monday" is not one day
    else:
        found = _next_weekday(today, index)  # "Friday", "on Friday", "this Friday": the coming one
    return (found, True) if found else None


def annotate_dates(text: str, tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """`text` with each clear relative day followed by its real date, in the person's own timezone. Unclear ones, and anything already dated, are left as they are."""
    if not text:
        return text
    today = local_now(tz_name, now or datetime.now(timezone.utc)).date()
    out, last, count, seen = [], 0, 0, set()
    for match in _PHRASE.finditer(text):
        if count >= MAX_ANNOTATIONS:
            break
        if _DATE_AFTER.match(text, match.end()):
            continue                          # already dated: running this twice changes nothing
        resolved = _resolve(match, today)
        if resolved is None:
            continue
        when, weekday_in_words = resolved
        key = (match.group(0).lower(), when)
        if key in seen:
            continue                          # the same words twice in one note get one date
        seen.add(key)
        label = when.isoformat() if weekday_in_words else f"{when.strftime('%A')} {when.isoformat()}"
        out.append(text[last:match.end()])
        out.append(f" ({label})")
        last = match.end()
        count += 1
    out.append(text[last:])
    return "".join(out)
