import asyncio
import logging
import os

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


def prune_old_checkpoints(keep_per_thread: int = DEFAULT_KEEP_PER_THREAD) -> dict:
    """Keeps only the `keep_per_thread` most recent checkpoints for every thread_id in
    checkpointing_db.checkpoints (most recent = highest _id, since checkpoint _ids are
    monotonically increasing ObjectIds), deleting the rest along with their matching
    checkpoint_writes entries. Old checkpoints are only needed for LangGraph's
    time-travel/replay of past turns, not for continuing a conversation, so this never
    affects an in-flight or resumable thread. Returns a summary dict for logging. Safe
    to call repeatedly — a thread with <= keep_per_thread checkpoints is left untouched.
    """
    db = get_db()
    if db is None:
        return {"skipped": True, "reason": "USE_DB not enabled"}

    ckpt_db = db.client[CHECKPOINT_DB_NAME]
    checkpoints = ckpt_db[CHECKPOINTS_COLLECTION]
    writes = ckpt_db[CHECKPOINT_WRITES_COLLECTION]

    to_delete_ids = []
    to_delete_checkpoint_ids = []
    threads_seen = 0
    threads_pruned = 0

    for thread_id in checkpoints.distinct("thread_id"):
        threads_seen += 1
        docs = list(
            checkpoints.find({"thread_id": thread_id}, {"_id": 1, "checkpoint_id": 1})
            .sort("_id", -1)
        )
        drop = docs[keep_per_thread:]
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

    summary = {
        "skipped": False,
        "threads_seen": threads_seen,
        "threads_pruned": threads_pruned,
        "deleted_checkpoints": deleted_checkpoints,
        "deleted_checkpoint_writes": deleted_writes,
    }
    logger.info("[checkpoint_retention] %s", summary)
    return summary


async def run_checkpoint_retention_loop(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    keep_per_thread: int = DEFAULT_KEEP_PER_THREAD,
) -> None:
    """Runs prune_old_checkpoints on a fixed interval for the lifetime of the process.
    Meant to be launched once via spawn_background_task() in app.py's lifespan. A single
    iteration's failure (e.g. a transient Mongo error) is logged and never kills the loop
    — the next scheduled run gets another chance, the same resilience the rest of this
    app's fire-and-forget background tasks already rely on."""
    while True:
        try:
            await asyncio.to_thread(prune_old_checkpoints, keep_per_thread)
        except Exception:
            logger.exception("[checkpoint_retention] pruning pass failed")
        await asyncio.sleep(interval_seconds)
