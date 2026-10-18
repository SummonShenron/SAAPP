"""Runs the tasks people have set up when they are due, one at a time, inside the limits in backend/utils/user_tasks.py.

Like the routines (routine_scheduler.py) it wakes on a timer, and "is it due" lives in the database: a claim on the task (a short lease) means a restart or a second
server cannot run the same task twice. A pass runs at most MAX_PER_PASS tasks. A due task is given back (rescheduled, not run) when the cost guard is in low-power
mode, when its owner has used their runs for the day, or when the owner's own limits are over; nothing queues up. A pass logs only how many ran and how many found something, never a topic.
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
MAX_CLAIMS_PER_PASS = 12           # tasks looked at (run or given back) in one pass, so a long skipped queue can't keep a pass going

Invoke = Callable[[str], Awaitable[Any]]
Allowed = Callable[[Optional[str]], bool]


async def run_one(db, task: Dict[str, Any], invoke: Invoke, trigger: str, now: Optional[datetime] = None, **runner_kwargs) -> Dict[str, Any]:
    """Runs a task that has already been claimed, and writes the run down. Returns the stored run. `now` is for tests; the schedule uses the real clock."""
    started = now or datetime.now(timezone.utc)
    result = await user_task_runner.run_task(db, task, invoke, **runner_kwargs)
    return await asyncio.to_thread(tasks.finish_run, db, task, result, trigger, started, now or datetime.now(timezone.utc))


async def run_due(db, invoke: Invoke, allowed: Allowed, now: Optional[datetime] = None, **runner_kwargs) -> Dict[str, int]:
    """One pass. `allowed(owner)` says whether the cost guard lets a model-using extra run for that person (None checks only the shared budget).
    Returns counts: {"ran", "found", "skipped"}."""
    counts = {"ran": 0, "found": 0, "skipped": 0}
    for _ in range(MAX_CLAIMS_PER_PASS):
        current = now or datetime.now(timezone.utc)
        task = await asyncio.to_thread(tasks.claim_due, db, current)
        if task is None:
            break
        owner = task["username"]
        over_cap = await asyncio.to_thread(tasks.runs_in_last_day, db, owner, current) >= tasks.MAX_RUNS_PER_USER_PER_DAY
        if over_cap or not await asyncio.to_thread(allowed, owner):
            await asyncio.to_thread(tasks.reschedule, db, task, current)
            counts["skipped"] += 1
            continue
        try:
            run = await run_one(db, task, invoke, "scheduled", now=now, **runner_kwargs)
        except Exception:
            logger.warning("[tasks] could not finish a scheduled run.", exc_info=True)
            await asyncio.to_thread(tasks.reschedule, db, task, current)
            continue
        counts["ran"] += 1
        counts["found"] += int(run.get("status") == tasks.OK)
        if counts["ran"] >= MAX_PER_PASS:
            break
    return counts


async def run_user_task_schedule(get_db: Callable[[], Any], invoke: Invoke, allowed: Allowed, *, check_every: float = CHECK_EVERY_SECONDS, first_delay: float = FIRST_DELAY_SECONDS) -> None:
    """Launched once from app.py's lifespan."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            db = get_db()
            if db is not None:
                counts = await run_due(db, invoke, allowed)
                if counts["ran"]:
                    logger.info("[tasks] ran %d task(s), %d found something.", counts["ran"], counts["found"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[tasks] a scheduling pass failed.", exc_info=True)
        await asyncio.sleep(check_every)
