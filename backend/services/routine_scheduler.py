"""Runs the routines that have no loop of their own (today: the health check), and writes down every run.

The reading shelf and the two clean-ups already have their own loops (shelf_refresh.py, checkpoint_retention.py, attachment_retention.py); those write their
own run records and are not run from here. A routine runs from here only when it has a runner in RUNNERS.

Like the shelf's, the "is it due" test lives in the database: the last run's time, plus a claim (one document per routine, with a short expiry) so that a
restart or a second server cannot start the same routine twice at once. One failed pass is logged and the next check tries again.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from backend.services import health_check
from backend.utils import routines

logger = logging.getLogger("SASS Logger")

CHECK_EVERY_SECONDS = 600.0
FIRST_DELAY_SECONDS = 120.0         # let a starting server settle before it looks itself over
CLAIM_MINUTES = 15

Runner = Callable[[Any], Awaitable[Tuple[str, Dict[str, int]]]]
RUNNERS: Dict[str, Runner] = {"health_check": health_check.run_health_check}


def claim(db, routine_id: str, now: datetime) -> bool:
    """Takes the right to run one routine now, atomically. False when another process holds it."""
    from pymongo.errors import DuplicateKeyError
    routines._ensure_indexes(db)        # the unique index on `routine` is what makes a held claim refuse a second one
    try:
        db[routines.CLAIMS_COLLECTION].update_one(
            {"routine": routine_id, "$or": [{"claimed_until": {"$exists": False}}, {"claimed_until": {"$lte": now}}]},
            {"$set": {"claimed_until": now + timedelta(minutes=CLAIM_MINUTES)}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False


def is_due(routine: routines.Routine, last: Optional[Dict[str, Any]], now: datetime) -> bool:
    return last is None or now - last["started_at"] >= timedelta(hours=routine.every_hours)


async def run_due(db, now: Optional[datetime] = None, runners: Optional[Dict[str, Runner]] = None) -> int:
    """Runs every routine that is due and has a runner. Returns how many ran. `now` is for tests; the schedule uses the real clock."""
    runners = RUNNERS if runners is None else runners
    ran = 0
    for routine in routines.registry():
        runner = runners.get(routine.id)
        if runner is None:
            continue
        current = now or datetime.now(timezone.utc)
        last = (await asyncio.to_thread(routines.latest_runs, db, current)).get(routine.id)
        if not is_due(routine, last, current):
            continue
        if not await asyncio.to_thread(claim, db, routine.id, current):
            continue
        started = datetime.now(timezone.utc)
        try:
            status, counts = await runner(db)
        except Exception:
            logger.warning("[routines] %s failed.", routine.id, exc_info=True)
            status, counts = routines.STATUS_FAILED, {}
        await asyncio.to_thread(routines.record_run, db, routine.id, status, counts, started)
        logger.info("[routines] %s: %s.", routine.id, status)
        ran += 1
    return ran


async def run_routine_schedule(get_db: Callable[[], Any], *, check_every: float = CHECK_EVERY_SECONDS, first_delay: float = FIRST_DELAY_SECONDS) -> None:
    """Launched once from app.py's lifespan. Wakes every `check_every` seconds and runs whatever is due."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            db = get_db()
            if db is not None:
                await run_due(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[routines] a scheduling pass failed.", exc_info=True)
        await asyncio.sleep(check_every)
