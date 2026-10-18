import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from gridfs import GridFS

from backend.utils.db_utils import get_db

from backend.utils import routines

logger = logging.getLogger("SASS Logger")

# Photos and files attached in a chat are kept with the conversation, which can be forever, and a photo of a letter or a bill is among the most
# personal things the app ever holds. This is the second way they go (the first is deleting the conversation, see delete_session_attachments in
# backend/utils/attachment_utils.py): after this many days the stored photo and the text read out of the file are removed, and the conversation
# itself stays. 0 or negative turns the clean-up off.
DEFAULT_MAX_AGE_DAYS = int(os.getenv("ATTACHMENT_RETENTION_DAYS", "30"))
DEFAULT_INTERVAL_SECONDS = float(os.getenv("ATTACHMENT_RETENTION_INTERVAL_SECONDS", str(24 * 60 * 60)))


def prune_expired_attachments(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Removes chat photos and the text read from attached files once they are older than `max_age_days`. Only chat attachments: knowledge-base files
    are never touched (the GridFS query is limited to kind "chat_attachment"). Returns what went, for the log. Safe to call repeatedly."""
    if not max_age_days or max_age_days <= 0:
        return {"skipped": True, "reason": "attachment retention is off"}
    db = get_db()
    if db is None:
        return {"skipped": True, "reason": "USE_DB not enabled"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    files = GridFS(db)
    expired = list(db["fs.files"].find({"metadata.kind": "chat_attachment", "uploadDate": {"$lt": cutoff}}, {"_id": 1}))
    for row in expired:
        files.delete(row["_id"])
    # session_documents rows carry no date of their own; a Mongo ObjectId starts with its creation time, so the cutoff is expressed as one.
    documents = db["session_documents"].delete_many({"_id": {"$lt": ObjectId.from_datetime(cutoff)}}).deleted_count

    summary = {"skipped": False, "deleted_photos": len(expired), "deleted_file_texts": documents, "max_age_days": max_age_days}
    logger.info("[attachment_retention] %s", summary)
    return summary


async def run_attachment_retention_loop(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> None:
    """Runs prune_expired_attachments on a fixed interval for the life of the process (launched once from app.py's lifespan). One failed pass is
    logged and the next gets another chance, the same as the checkpoint clean-up."""
    while True:
        started = datetime.now(timezone.utc)
        try:
            result = await asyncio.to_thread(prune_expired_attachments, max_age_days)
            deleted = int((result or {}).get("deleted_photos", 0)) + int((result or {}).get("deleted_file_texts", 0))
            await asyncio.to_thread(
                routines.record_run_safely, "attachment_cleanup",
                routines.STATUS_SKIPPED if (result or {}).get("skipped") else routines.STATUS_OK, {"deleted": deleted}, started,
            )
        except Exception:
            logger.exception("[attachment_retention] clean-up pass failed")
            await asyncio.to_thread(routines.record_run_safely, "attachment_cleanup", routines.STATUS_FAILED, {}, started)
        await asyncio.sleep(interval_seconds)
