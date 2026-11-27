"""A stronger voice for the few turns that matter most. OFF unless REPLY_TOP_TIER=on.

WHY (2026-11-27). Three quarters of turns are read as emotional stakes and every one is written by the cheapest voice. A stronger one costs about twenty times as much a reply, so it cannot
write them all; it can write the handful a day where the person is hurting most. Whether it writes those better is NOT yet measured (one three-question comparison exists), which is why this
ships switched off: turn it on for a comparison, not by default.

WHEN (`wanted`). The switch is on, the turn's stakes are in TOP_STAKES, the feeling in force is at least MIN_INTENSITY, the person is not a guest, and fewer than the daily cap of their
replies in the last 24 hours were written by it (read from the reply stamps saved on the conversation: backend/utils/reply_stamp.py). NEVER inside a safety context: those replies stay on
the standard voice the safety layer was built and tested on.

The person's own daily spend limit still applies, because the model's price is in LLM_PRICES (the caller only gets a voice when it is: backend/models/models.py get_top_stream_llm).

Pure apart from the environment and the clock; nothing is logged.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from backend.utils.time_utils import sent_at

TIER = "sonnet"
TOP_STAKES = ("emotional", "checkin")
MIN_INTENSITY = float(os.getenv("REPLY_TOP_TIER_MIN_INTENSITY", "0.6"))
DEFAULT_DAILY = 8
WINDOW_HOURS = 24


def enabled() -> bool:
    return os.getenv("REPLY_TOP_TIER", "off").strip().lower() in ("1", "true", "yes", "on")


def daily_cap() -> int:
    try:
        return max(int(os.getenv("REPLY_TOP_TIER_DAILY", str(DEFAULT_DAILY))), 0)
    except ValueError:
        return DEFAULT_DAILY


def used_today(messages: Iterable[Any], now: Optional[datetime] = None) -> int:
    """How many of Sonic's replies in the last WINDOW_HOURS were written by the top voice, by their saved stamps. A stamped reply with no time on it is counted (an unknown time is never
    guessed to be old). Pure apart from the clock."""
    moment = now or datetime.now(timezone.utc)
    count = 0
    for message in messages or []:
        stamp = (getattr(message, "additional_kwargs", None) or {}).get("voice")
        if getattr(message, "type", "") != "ai" or not isinstance(stamp, dict) or stamp.get("tier") != TIER:
            continue
        when = sent_at(message)
        if when is None or moment - when < timedelta(hours=WINDOW_HOURS):
            count += 1
    return count


def wanted(stakes: str, intensity: float, *, risk_active: bool, used: int, guest: bool, on: Optional[bool] = None, cap: Optional[int] = None) -> bool:
    """Whether the top voice should write this reply. Pure apart from the environment."""
    if not (enabled() if on is None else on) or risk_active or guest:
        return False
    if stakes not in TOP_STAKES or float(intensity or 0.0) < MIN_INTENSITY:
        return False
    return used < (daily_cap() if cap is None else cap)
