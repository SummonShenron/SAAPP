"""Runs the tasks people have set up when they are due, one at a time, inside the limits in backend/utils/user_tasks.py.

Like the routines (routine_scheduler.py) it wakes on a timer, and "is it due" lives in the database: a claim on the task (a short lease) means a restart or a second
server cannot run the same task twice. A pass runs at most MAX_PER_PASS tasks, and at most one for any one person (so one person with three tasks due cannot take every
slot). A due task is given back (rescheduled, not run) when the cost guard is in low-power mode, when its owner has used their runs for the day, or when the owner's
own limits are over; nothing queues up. A pass logs only how many ran and how many found something, never a topic.

Windows (docs/schedule-windows-plan.md): a task with a window is "due" from when the window opens, but runs only at a quiet moment inside it: its owner has been away
QUIET_MINUTES and the shared budget has headroom. Until then it waits (looked at again a few minutes later, never past the window's end); when the window closes it runs
anyway, unless low-power mode gives it back. Each run stores why it landed when it did. There is no model in any of this: the checks are code, so the reason is a fact.
A task with no window is exact, as it always was, except that it too gives way when scheduled work has used its share of the shared budget.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.services import user_task_runner
from backend.utils import user_tasks as tasks

logger = logging.getLogger("SASS Logger")

CHECK_EVERY_SECONDS = 300.0
FIRST_DELAY_SECONDS = 240.0
MAX_PER_PASS = 3                   # tasks run in one pass
MAX_CLAIMS_PER_PASS = 12           # tasks looked at (run, waited or given back) in one pass, so a long skipped queue can't keep a pass going

Invoke = Callable[[str], Awaitable[Any]]
Allowed = Callable[[Optional[str]], bool]
Headroom = Callable[[], bool]                                  # True while scheduled work may still use the shared budget
IdleMinutes = Callable[[str, datetime], Optional[float]]       # minutes since this person was last active, None when nothing says


async def run_one(db, task: Dict[str, Any], invoke: Invoke, trigger: str, now: Optional[datetime] = None, *, reason: str = "", **runner_kwargs) -> Dict[str, Any]:
    """Runs a task that has already been claimed, and writes the run down. Returns the stored run. `now` is for tests; the schedule uses the real clock."""
    started = now or datetime.now(timezone.utc)
    result = await user_task_runner.run_task(db, task, invoke, **runner_kwargs)
    return await asyncio.to_thread(
        lambda: tasks.finish_run(db, task, result, trigger, started, now or datetime.now(timezone.utc), reason=reason or tasks.run_reason(task, trigger, None, False))
    )


async def run_due(
    db, invoke: Invoke, allowed: Allowed, now: Optional[datetime] = None, *, headroom: Optional[Headroom] = None, idle: Optional[IdleMinutes] = None, **runner_kwargs,
) -> Dict[str, int]:
    """One pass. `allowed(owner)` says whether the cost guard lets a model-using extra run for that person (None checks only the shared budget); `headroom()` whether
    scheduled work may still use the shared budget; `idle(owner, now)` how long that person has been away. Leave `headroom` or `idle` out and that check is not made.
    Returns counts: {"ran", "found", "skipped", "waited"}: a task that waited for a quiet moment or its turn is "waited", not "skipped"."""
    counts = {"ran": 0, "found": 0, "skipped": 0, "waited": 0}
    ran_for: set = set()
    for _ in range(MAX_CLAIMS_PER_PASS):
        current = now or datetime.now(timezone.utc)
        task = await asyncio.to_thread(tasks.claim_due, db, current)
        if task is None:
            break
        owner = task["username"]
        windowed = tasks.is_windowed(task)
        deadline = tasks.window_deadline(task)
        at_deadline = windowed and deadline is not None and current >= deadline
        if await asyncio.to_thread(tasks.runs_in_last_day, db, owner, current) >= tasks.MAX_RUNS_PER_USER_PER_DAY:
            await asyncio.to_thread(tasks.reschedule, db, task, current)
            counts["skipped"] += 1
            continue
        if not await asyncio.to_thread(allowed, owner):
            if windowed and not at_deadline:
                await asyncio.to_thread(tasks.defer, db, task, current, "headroom")        # low-power now may be over before the window closes
                counts["waited"] += 1
            else:
                await asyncio.to_thread(tasks.reschedule, db, task, current)
                counts["skipped"] += 1
            continue
        if owner in ran_for:
            await asyncio.to_thread(tasks.defer, db, task, current, None)                  # one task per person per pass; the rest take the next one
            counts["waited"] += 1
            continue
        if headroom is not None and not await asyncio.to_thread(headroom):
            if windowed and not at_deadline:
                await asyncio.to_thread(tasks.defer, db, task, current, "headroom")
                counts["waited"] += 1
                continue
            if not windowed:
                await asyncio.to_thread(tasks.reschedule, db, task, current)               # an exact task gives way, as it does in low-power mode
                counts["skipped"] += 1
                continue
            # a window that has closed runs anyway: only low-power mode (above) gives it back
        idle_for: Optional[float] = None
        if windowed and not at_deadline and idle is not None:
            idle_for = await asyncio.to_thread(idle, owner, current)
            if idle_for is not None and idle_for < tasks.QUIET_MINUTES:
                await asyncio.to_thread(tasks.defer, db, task, current, "quiet")
                counts["waited"] += 1
                continue
        try:
            run = await run_one(db, task, invoke, "scheduled", now=now, reason=tasks.run_reason(task, "scheduled", idle_for, at_deadline), **runner_kwargs)
        except Exception:
            logger.warning("[tasks] could not finish a scheduled run.", exc_info=True)
            await asyncio.to_thread(tasks.reschedule, db, task, current)
            continue
        ran_for.add(owner)
        counts["ran"] += 1
        counts["found"] += int(run.get("status") == tasks.OK)
        if counts["ran"] >= MAX_PER_PASS:
            break
    return counts


async def run_user_task_schedule(
    get_db: Callable[[], Any], invoke: Invoke, allowed: Allowed, *, headroom: Optional[Headroom] = None, idle: Optional[IdleMinutes] = None,
    check_every: float = CHECK_EVERY_SECONDS, first_delay: float = FIRST_DELAY_SECONDS,
) -> None:
    """Launched once from app.py's lifespan."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            db = get_db()
            if db is not None:
                counts = await run_due(db, invoke, allowed, headroom=headroom, idle=idle)
                if counts["ran"]:
                    logger.info("[tasks] ran %d task(s), %d found something, %d waiting.", counts["ran"], counts["found"], counts["waited"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[tasks] a scheduling pass failed.", exc_info=True)
        await asyncio.sleep(check_every)
