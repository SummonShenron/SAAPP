"""Which day, and which month, the model calls are billed in.

Google bills Gemini usage by Pacific time: its usage days, its quota resets and the first of its billing month all turn over at midnight
Pacific, not UTC and not the user's own midnight. The usage meter (llm_usage.py), the per-turn counts (llm_turns.py) and the budget guard
(budget_guard.py) all key their days and months here so that "today" and "this month" in the app mean what they mean on Google's Spend page.

LLM_BILLING_TIMEZONE overrides the zone (an IANA name such as "America/Chicago"; "UTC" for the old behaviour). An unknown name falls back to
UTC rather than raising: a typo here must never take the meter or the guard down.

Rows written before this module existed were keyed by UTC date. They are not rewritten: for the few hours a day when the two dates differ,
a day's total is split between two keys, which only ever moves a few cents between neighbouring days, once.
"""
import os
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Optional

ZONE_ENV = "LLM_BILLING_TIMEZONE"
DEFAULT_ZONE = "America/Los_Angeles"


def zone() -> tzinfo:
    name = (os.getenv(ZONE_ENV) or DEFAULT_ZONE).strip()
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def label() -> str:
    """How to say the zone to a person."""
    name = (os.getenv(ZONE_ENV) or DEFAULT_ZONE).strip()
    return "Pacific time" if name == DEFAULT_ZONE else name


def local(now: Optional[datetime] = None) -> datetime:
    """`now` (default: the current moment) in the billing zone. A datetime with no zone is taken to be UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone())


def day(now: Optional[datetime] = None) -> str:
    """The billing day, "YYYY-MM-DD"."""
    return local(now).date().isoformat()


def month(now: Optional[datetime] = None) -> str:
    """The billing month, "YYYY-MM"."""
    return local(now).strftime("%Y-%m")


def month_start(now: Optional[datetime] = None) -> str:
    """The first day of the billing month, "YYYY-MM-DD"."""
    return local(now).date().replace(day=1).isoformat()


def days_back(now: Optional[datetime], days: int) -> str:
    """The first billing day of a period of `days` days that ends today (today included), "YYYY-MM-DD"."""
    return (local(now).date() - timedelta(days=max(days, 1) - 1)).isoformat()


def _midnight(on: date) -> datetime:
    return datetime.combine(on, time.min, tzinfo=zone())


def next_day_start(now: Optional[datetime] = None) -> datetime:
    """When the billing day next turns over (midnight in the billing zone, as an aware datetime)."""
    return _midnight(local(now).date() + timedelta(days=1))


def next_month_start(now: Optional[datetime] = None) -> datetime:
    """When the billing month next turns over."""
    first = local(now).date().replace(day=1)
    return _midnight((first + timedelta(days=32)).replace(day=1))
