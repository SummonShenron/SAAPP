import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

# LangGraph's MongoDB checkpointer writes a brand-new checkpoint on every single
# graph turn and never prunes old ones on its own. Left unattended, this is exactly
# what ate 96% of the Atlas cluster's 512MB free-tier quota and blocked all writes
# app-wide (documented in docs/coding-agent-roadmap.md). This module keeps only the
# most recent N checkpoints per thread going forward, matching the manual cleanup
# used to unblock the cluster.
CHECKPOINT_DB_NAME = "checkpointing_db"
CHECKPOINTS_COLLECTION = "checkpoints"
CHECKPOINT_WRITES_COLLECTION = "checkpoint_writes"

DEFAULT_KEEP_PER_THREAD = int(os.getenv("CHECKPOINT_RETENTION_KEEP_PER_THREAD", "3"))
DEFAULT_INTERVAL_SECONDS = float(os.getenv("CHECKPOINT_RETENTION_INTERVAL_SECONDS", str(24 * 60 * 60)))
# The per-thread floor above bounds any ONE thread's growth, but not the total NUMBER of
# threads — every conversation ever created keeps its floor of `keep_per_thread` checkpoints
# forever, so total storage still grows without limit as all-time thread count grows, even
# with the per-thread cap working exactly as designed. This second axis bounds that: nothing
# older than this survives, regardless of the per-thread floor. 0/negative disables it.
DEFAULT_MAX_AGE_DAYS = int(os.getenv("CHECKPOINT_RETENTION_MAX_AGE_DAYS", "90"))


def prune_old_checkpoints(
    keep_per_thread: int = DEFAULT_KEEP_PER_THREAD,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    """Keeps only the `keep_per_thread` most recent checkpoints for every thread_id in
    checkpointing_db.checkpoints (most recent = highest _id, since checkpoint _ids are
    monotonically increasing ObjectIds), deleting the rest along with their matching
    checkpoint_writes entries. Old checkpoints are only needed for LangGraph's
    time-travel/replay of past turns, not for continuing a conversation, so this never
    affects an in-flight or resumable thread. Returns a summary dict for logging. Safe
    to call repeatedly — a thread with <= keep_per_thread checkpoints is left untouched.

    Also unconditionally deletes anything older than `max_age_days`, even a thread's last
    surviving checkpoints under its own per-thread floor — a thread abandoned that long ago
    is never going to be resumed for replay, and the per-thread floor alone never stops total
    storage from growing with all-time thread count. Pass max_age_days<=0 to disable this axis.
    """
    db = get_db()
    if db is None:
        return {"skipped": True, "reason": "USE_DB not enabled"}

    ckpt_db = db.client[CHECKPOINT_DB_NAME]
    checkpoints = ckpt_db[CHECKPOINTS_COLLECTION]
    writes = ckpt_db[CHECKPOINT_WRITES_COLLECTION]

    # A single aggregation instead of distinct("thread_id") + one find() per thread. distinct()
    # returns every distinct value in ONE BSON reply, capped at ~16MB — fine for a handful of
    # threads, but a real risk once the number of distinct thread_ids (not checkpoints — every
    # conversation ever created, all-time) grows into the thousands, and a failure there would
    # previously be swallowed by the loop's blanket except-and-retry-tomorrow below. Grouping
    # server-side instead scopes each response document to one thread's own checkpoints, and
    # $push after a preceding $sort preserves that per-group order (a standard, documented
    # "top-N per group" idiom) — so "most recent first" survives the group with no extra sort.
    pipeline = [
        {"$sort": {"_id": -1}},
        {"$group": {"_id": "$thread_id", "docs": {"$push": {"_id": "$_id", "checkpoint_id": "$checkpoint_id"}}}},
    ]
    groups = list(checkpoints.aggregate(pipeline, allowDiskUse=True))

    to_delete_ids = []
    to_delete_checkpoint_ids = []
    threads_seen = len(groups)
    threads_pruned = 0

    for group in groups:
        drop = group["docs"][keep_per_thread:]
        if not drop:
            continue
        threads_pruned += 1
        to_delete_ids.extend(d["_id"] for d in drop)
        to_delete_checkpoint_ids.extend(d["checkpoint_id"] for d in drop)

    deleted_checkpoints = 0
    deleted_writes = 0
    if to_delete_ids:
        deleted_checkpoints = checkpoints.delete_many({"_id": {"$in": to_delete_ids}}).deleted_count
    if to_delete_checkpoint_ids:
        deleted_writes = writes.delete_many(
            {"checkpoint_id": {"$in": to_delete_checkpoint_ids}}
        ).deleted_count

    # Second axis: an unconditional age cutoff, on top of (not instead of) the per-thread floor
    # above. Checkpoint _ids are Mongo ObjectIds, so a cutoff derives straight from a timestamp
    # with no schema change. Runs over the WHOLE collection (including whatever the per-thread
    # prune just left behind), since a stale thread's last few checkpoints are exactly what this
    # axis exists to catch — the per-thread floor has no opinion on age, only on count.
    deleted_aged_checkpoints = 0
    deleted_aged_writes = 0
    if max_age_days and max_age_days > 0:
        cutoff_id = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=max_age_days))
        aged_checkpoint_ids = [
            d["checkpoint_id"]
            for d in checkpoints.find({"_id": {"$lt": cutoff_id}}, {"checkpoint_id": 1})
        ]
        deleted_aged_checkpoints = checkpoints.delete_many({"_id": {"$lt": cutoff_id}}).deleted_count
        if aged_checkpoint_ids:
            deleted_aged_writes = writes.delete_many(
                {"checkpoint_id": {"$in": aged_checkpoint_ids}}
            ).deleted_count

    summary = {
        "skipped": False,
        "threads_seen": threads_seen,
        "threads_pruned": threads_pruned,
        "deleted_checkpoints": deleted_checkpoints,
        "deleted_checkpoint_writes": deleted_writes,
        "deleted_aged_checkpoints": deleted_aged_checkpoints,
        "deleted_aged_checkpoint_writes": deleted_aged_writes,
    }
    logger.info("[checkpoint_retention] %s", summary)
    return summary


async def run_checkpoint_retention_loop(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    keep_per_thread: int = DEFAULT_KEEP_PER_THREAD,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> None:
    """Runs prune_old_checkpoints on a fixed interval for the lifetime of the process.
    Meant to be launched once via spawn_background_task() in app.py's lifespan. A single
    iteration's failure (e.g. a transient Mongo error) is logged and never kills the loop
    — the next scheduled run gets another chance, the same resilience the rest of this
    app's fire-and-forget background tasks already rely on."""
    while True:
        try:
            await asyncio.to_thread(prune_old_checkpoints, keep_per_thread, max_age_days)
        except Exception:
            logger.exception("[checkpoint_retention] pruning pass failed")
        await asyncio.sleep(interval_seconds)
