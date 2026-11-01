"""The numbers behind the session column's memory compaction meter (local/src/components/CompactionMeter.tsx).

Sonic keeps a short note of what each exchange was about. Once more of them than a threshold have piled up without being folded together, memory compaction folds them into summaries
(backend/services/memory_compaction.py: `maybe_trigger_compaction` checks after every new note and compacts when `count_uncompacted_chunks` is over `DEFAULT_COMPACTION_THRESHOLD`, 100 unless
MEMORY_COMPACTION_THRESHOLD says otherwise). The meter shows that count against the threshold: it fills as notes are added and falls back to the start when a compaction folds them in.

It replaces the context ring (how full the model's window was), which never came close to filling: a reply is given only the last ten messages (app.py), so the window cannot grow.

A COUNT and a limit, nothing else: no note text, nothing stored, nothing logged but a warning when the count cannot be read.
"""
import logging
from typing import Any, Dict, Optional

from backend.services.local_workspace import is_guest_username
from backend.services.memory_compaction import DEFAULT_COMPACTION_THRESHOLD, count_uncompacted_chunks

logger = logging.getLogger("SASS Logger")


def view(db: Any, username: Optional[str]) -> Dict[str, int]:
    """{"uncompacted": notes not yet folded together, "threshold": the number at which they are}. Zero notes for a guest, a missing person or no datastore, or when the count cannot be read: the
    meter then shows an empty track instead of an error. Blocking (a read), so call it off the event loop. Never raises."""
    threshold = int(DEFAULT_COMPACTION_THRESHOLD)
    if db is None or not username or is_guest_username(username):
        return {"uncompacted": 0, "threshold": threshold}
    try:
        return {"uncompacted": max(int(count_uncompacted_chunks(db, username)), 0), "threshold": threshold}
    except Exception:
        logger.warning("[compaction meter] could not count the uncompacted notes.", exc_info=True)
        return {"uncompacted": 0, "threshold": threshold}
