from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Chicago"


def local_now(tz_name: Optional[str], now: Optional[datetime] = None) -> datetime:
    """The current moment in the user's own timezone. The server's clock is usually UTC, so reading it directly
    puts an evening user on tomorrow's date; an unknown timezone name falls back to the default rather than
    raising."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(tz_name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return moment.astimezone(zone)


def local_today(tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """Today's date as YYYY-MM-DD in the user's timezone."""
    return local_now(tz_name, now).strftime("%Y-%m-%d")


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    if 21 <= hour or hour < 1:
        return "night"
    return "late night"  # 1am-5am


def time_phrase(tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """e.g. "9:14 PM on Tuesday, October 6, 2026 (night, America/Chicago)": one line a model can read without
    doing any timezone or weekday arithmetic itself."""
    local = local_now(tz_name, now)
    clock = local.strftime("%I:%M %p").lstrip("0")
    zone_name = getattr(local.tzinfo, "key", None) or (tz_name or DEFAULT_TIMEZONE)
    return f"{clock} on {local.strftime('%A, %B')} {local.day}, {local.year} ({part_of_day(local.hour)}, {zone_name})"


# Short pauses are ordinary conversation, so only a real gap is ever mentioned to the model: 20 minutes before
# the current message, and an hour between two earlier messages in the history it is shown.
GAP_NOTE_MIN_SECONDS = 20 * 60
HISTORY_GAP_MIN_SECONDS = 60 * 60
SENT_AT_KEY = "sent_at"


def stamp(message, now: Optional[datetime] = None):
    """Records when a message was sent (UTC, ISO) on the message itself, so it is saved with the conversation
    and the time between messages can be known later. Leaves an existing stamp alone; returns the message."""
    kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(kwargs, dict) and not kwargs.get(SENT_AT_KEY):
        kwargs[SENT_AT_KEY] = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return message


def sent_at(message) -> Optional[datetime]:
    """When a message was sent, or None for one saved before messages were stamped (an unknown time is never
    guessed)."""
    raw = (getattr(message, "additional_kwargs", None) or {}).get(SENT_AT_KEY)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def describe_gap(seconds: float) -> str:
    """A rounded, human way to say how long: "about 20 minutes", "about 3 hours", "about 2 days"."""
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {max(minutes, 1)} minute{'s' if minutes != 1 else ''}"
    hours = round(seconds / 3600)
    if hours < 24:
        return f"about {hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    if days < 14:
        return f"about {days} day{'s' if days != 1 else ''}"
    weeks = round(days / 7)
    if days < 60:
        return f"about {weeks} week{'s' if weeks != 1 else ''}"
    months = round(days / 30)
    return f"about {months} month{'s' if months != 1 else ''}"


def gap_seconds(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[float]:
    if earlier is None or later is None:
        return None
    seconds = (later - earlier).total_seconds()
    return seconds if seconds >= 0 else None


def history_gap_marker(earlier, later) -> str:
    """A one-line marker to put between two messages in the history text when a long pause separated them
    ("" otherwise, and "" if either message has no stamp)."""
    seconds = gap_seconds(sent_at(earlier), sent_at(later))
    if seconds is None or seconds < HISTORY_GAP_MIN_SECONDS:
        return ""
    return f"[{describe_gap(seconds)} later]"


def build_time_context(
    tz_name: Optional[str],
    now: Optional[datetime] = None,
    last_message_at: Optional[datetime] = None,
    defer_to_emotion: bool = False,
) -> str:
    """The block for the final-answer prompt: the user's real local time, with a rule against announcing it or
    greeting by the wrong part of the day. When there was a real pause since the previous message in this
    conversation, it also says how long, so the reply can pick back up rather than carry on as if no time passed.

    It is a fact about timing and nothing more. How the user feels, and so how much energy the reply may carry,
    belongs entirely to the emotional context (backend/utils/emotion_utils.py), which already decays with elapsed
    time and has its own rules for a returning user. So this block never says anything about feelings, and when an
    emotional context is in force (`defer_to_emotion`) it drops the "acknowledge the pause" suggestion and says the
    emotional section alone sets the tone, so the two can never pull a reply in opposite directions."""
    current = now or datetime.now(timezone.utc)
    block = (
        f"\nCURRENT TIME (the user's own clock): it is {time_phrase(tz_name, current)} for them. Use it quietly and "
        "naturally: don't announce it, don't greet with the wrong part of the day, and don't assume time has "
        "passed since earlier in the conversation unless it clearly has."
    )
    seconds = gap_seconds(last_message_at, current)
    if seconds is not None and seconds >= GAP_NOTE_MIN_SECONDS:
        block += f" Their previous message in this conversation was {describe_gap(seconds)} ago."
        if defer_to_emotion:
            block += (
                " That is a fact about timing only: the emotional and safety guidance in this prompt sets your tone "
                "and energy, and the length of the pause changes nothing about it. Don't remark on the gap."
            )
        else:
            block += (
                " This is them coming back after a pause, not an unbroken flow. Acknowledge the gap lightly only if "
                "it fits, don't dwell on it, and don't assume what happened in between."
            )
    return block + "\n"


def build_agent_time_line(tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """The line for the tool agent's prompt, so "today", "tomorrow" and "this week" in searches and calendar
    reads resolve against the user's real date."""
    return f"CURRENT DATE/TIME (the user's timezone): {time_phrase(tz_name, now)}. Resolve \"today\", \"tomorrow\" and \"this week\" against this."
