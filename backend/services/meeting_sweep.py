"""Closes meetings the person forgot to end, and keeps their notes.

A meeting runs until the person presses "End and save notes". One that was left open (the tab was closed, the person walked away) would keep the agenda in that conversation forever, so this
sweep ends it: after MEETING_IDLE_HOURS (3) with no message in its conversation, or MEETING_MAX_HOURS (12) after it started. Closing it does what the button does: the notes are written from what was
said, and marked as written automatically so the person can tell.

It ends a run with the same atomic update the button uses, so a second server (or the button, pressed at the same moment) cannot also write the notes. In low-power mode the notes call is paused like
the other extras, so a closing is put off and tried again on the next pass; once a meeting has been open DEFER_LIMIT_HOURS it closes without notes rather than stay open. Nothing here logs what was said.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from backend.utils import meetings as meetings_utils

logger = logging.getLogger("SASS Logger")

CHECK_EVERY_SECONDS = 900.0
FIRST_DELAY_SECONDS = 300.0
MAX_PER_PASS = 5

Invoke = Callable[[str], Any]
LoadMessages = Callable[[str, str], Any]
Allowed = Callable[[Optional[str]], bool]


async def close_stale(db, invoke: Invoke, load_messages: LoadMessages, allowed: Allowed, now: Optional[datetime] = None) -> Dict[str, int]:
    """One pass. Returns counts: {"closed", "with_notes", "deferred"}. Never raises."""
    counts = {"closed": 0, "with_notes": 0, "deferred": 0}
    now = now or datetime.now(timezone.utc)
    try:
        stale = await asyncio.to_thread(meetings_utils.stale_runs, db, now)
    except Exception:
        logger.warning("[meetings] could not look for meetings left open.", exc_info=True)
        return counts
    for run, _reason in stale[:MAX_PER_PASS]:
        username = run["username"]
        try:
            started = meetings_utils._utc(run.get("started_at")) or now
            can_write = await asyncio.to_thread(allowed, username)
            if not can_write and now - started < timedelta(hours=meetings_utils.DEFER_LIMIT_HOURS):
                counts["deferred"] += 1
                continue
            if not await asyncio.to_thread(meetings_utils.end_run, db, username, run["id"], now, True):
                continue                                          # the person (or another server) ended it first
            counts["closed"] += 1
            meeting = await asyncio.to_thread(meetings_utils.get_meeting, db, username, run["meeting_id"])
            if meeting is None or not can_write:
                continue
            messages = await asyncio.to_thread(load_messages, username, run.get("session_id") or "")
            note = await asyncio.to_thread(meetings_utils.write_note, db, username, run, meeting, list(messages), invoke, now, True)
            counts["with_notes"] += int(note is not None)
        except Exception:
            logger.warning("[meetings] could not close a meeting left open.", exc_info=True)
    return counts


async def run_meeting_sweep(get_db: Callable[[], Any], invoke: Invoke, load_messages: LoadMessages, allowed: Allowed, *, check_every: float = CHECK_EVERY_SECONDS, first_delay: float = FIRST_DELAY_SECONDS) -> None:
    """Launched once from app.py's lifespan."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            db = get_db()
            if db is not None:
                counts = await close_stale(db, invoke, load_messages, allowed)
                if counts["closed"] or counts["deferred"]:
                    logger.info("[meetings] closed %d meeting(s) left open (%d with notes), %d put off.", counts["closed"], counts["with_notes"], counts["deferred"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[meetings] a sweep for meetings left open failed.", exc_info=True)
        await asyncio.sleep(check_every)
