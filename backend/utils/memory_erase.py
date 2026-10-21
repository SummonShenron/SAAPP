"""Forgetting everything Sonic remembers about a user: what "Clear All" on the Memory page does.

The page promises that clearing "permanently deletes every saved memory". Sonic keeps memory in more places than the page lists, so
this is the one place that knows all of them, and a test pins the list:

- the saved facts (and the inactive ones the page hides)
- the notes from past conversations (`user_memory_chunks`), which recall searches on every message, including the merged summaries
- the archived copies compaction keeps so a merge can be undone (`user_memory_chunk_archive`), and the copies of facts pruned to stay
  under the cap (`user_memory_fact_archive`); an archive nothing ever purged would make "forget" a lie
- the compaction log (`memory_compaction_events`: counts and timestamps for this user)
- the backups of facts that sorting them by part of life (memory_domains.py) kept so it can be undone (`user_memory_domain_backups`)

Upcoming-item reminders (open loops) are forgotten by their own module, called next to this by the endpoint. Not touched, on purpose: the
user's conversations (deleted from the chat list) and the feedback they rated.

Every step is attempted even if one fails, and the failures are returned, so a partial wipe is reported as one instead of as success."""
import logging
from typing import Any, Dict, List

from backend.services.memory_search import CHUNK_ARCHIVE_COLLECTION, USER_MEMORY_COLLECTION
from backend.utils.memory_domains import BACKUP_COLLECTION as DOMAIN_BACKUP_COLLECTION
from backend.utils.memory_utils import FACT_ARCHIVE_COLLECTION, delete_all_user_facts, load_user_facts

logger = logging.getLogger("SASS Logger")

COMPACTION_EVENTS_COLLECTION = "memory_compaction_events"
# Every collection erased by username, in the order they are emptied: the live notes first, then the copies kept of them.
ERASED_COLLECTIONS = (USER_MEMORY_COLLECTION, CHUNK_ARCHIVE_COLLECTION, FACT_ARCHIVE_COLLECTION, COMPACTION_EVENTS_COLLECTION, DOMAIN_BACKUP_COLLECTION)


def stored_counts(db: Any, username: str) -> Dict[str, int]:
    """What exists besides the facts the page lists: {"notes": notes from past conversations, "archived": archived copies of notes and
    facts}. Zeros when there is no database. Never raises."""
    if db is None:
        return {"notes": 0, "archived": 0}
    try:
        notes = db[USER_MEMORY_COLLECTION].count_documents({"username": username})
        archived = sum(db[name].count_documents({"username": username}) for name in (CHUNK_ARCHIVE_COLLECTION, FACT_ARCHIVE_COLLECTION, DOMAIN_BACKUP_COLLECTION))
        return {"notes": int(notes), "archived": int(archived)}
    except Exception:
        logger.exception("[MemoryErase] Could not count stored memory for %s", username)
        return {"notes": 0, "archived": 0}


def erase_user_memory(db: Any, username: str) -> Dict[str, Any]:
    """Deletes everything listed in the module docstring for `username` and nobody else. Returns {"deleted": {collection: count},
    "facts": how many facts were saved, "failed": [steps that did not complete]}; "failed" empty means everything is gone."""
    result: Dict[str, Any] = {"deleted": {}, "facts": 0, "failed": []}
    try:
        result["facts"] = len(load_user_facts(username))
        delete_all_user_facts(username)
    except Exception:
        logger.exception("[MemoryErase] Could not clear the saved facts for %s", username)
        result["failed"].append("facts")
    if db is None:
        return result
    for name in ERASED_COLLECTIONS:
        try:
            result["deleted"][name] = int(db[name].delete_many({"username": username}).deleted_count)
        except Exception:
            logger.exception("[MemoryErase] Could not clear %s for %s", name, username)
            result["failed"].append(name)
    return result
