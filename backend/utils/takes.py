"""Which of Sonic's takes may be used now (backend/components/sonic_takes.py): approved, and their piece still live on the reading shelf.

A take says "I read a piece on ... recently", so it may be said only while that is true: its `source_url` must be a live, unexpired shelf item (reading_shelf.live_items, which already leaves out
stale ones). Live takes join the relatable pool (backend/utils/relatable_utils.py) and go through its match, gates, rate limit and code placement. The live list is cached for CACHE_SECONDS
(one small read). Counts only, no logging of any take or piece.
"""
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.components.sonic_takes import APPROVED_IDS, TAKES

logger = logging.getLogger("SASS Logger")

CACHE_SECONDS = 600
ID_PREFIX = "take_"
_ID_RE = re.compile(r"^take_[a-z_]{3,60}$")
_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "takes": []}


def approved(entries: Optional[List[Dict[str, Any]]] = None, approved_ids: Optional[tuple] = None) -> List[Dict[str, Any]]:
    ids = set(APPROVED_IDS if approved_ids is None else approved_ids)
    return [entry for entry in (TAKES if entries is None else entries) if entry.get("id") in ids]


def live_from(entries: List[Dict[str, Any]], shelf_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The entries whose source is one of these shelf items (urls compared normalized). Pure."""
    from backend.utils.reading_shelf import normalize_url

    live = {normalize_url(str(item.get("url") or "")) for item in shelf_items or [] if item.get("url")}
    return [entry for entry in entries if normalize_url(str(entry.get("source_url") or "")) in live]


def live_takes(db: Any, now: Optional[datetime] = None, *, use_cache: bool = True) -> List[Dict[str, Any]]:
    """Approved takes whose piece is live on the shelf. Blocking (one read when the cache is stale). [] without a database or on any failure. Never raises."""
    if db is None or not approved():
        return []
    with _lock:
        if use_cache and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return list(_cache["takes"])
    try:
        from backend.utils import reading_shelf

        found = live_from(approved(), reading_shelf.live_items(db, now))
    except Exception:
        logger.warning("[takes] could not read the shelf for the takes.", exc_info=True)
        return []
    with _lock:
        _cache.update(at=time.monotonic(), takes=found)
    return list(found)


def is_take(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and str(entry.get("id") or "").startswith(ID_PREFIX)


def check_registry(entries: Optional[List[Dict[str, Any]]] = None, approved_ids: Optional[tuple] = None) -> List[str]:
    """Problems with the registry: a bad or repeated id, a missing field, an approved id with no entry, or a line that does not say it was read. Pure."""
    entries = TAKES if entries is None else entries
    problems: List[str] = []
    seen = set()
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not _ID_RE.match(entry_id):
            problems.append(f"bad id: {entry_id!r}")
        if entry_id in seen:
            problems.append(f"repeated id: {entry_id}")
        seen.add(entry_id)
        for field in ("topic", "source_url", "line", "triggers"):
            if not entry.get(field):
                problems.append(f"{entry_id}: missing {field}")
        if not re.search(r"\bI(?:'ve)? read\b", str(entry.get("line") or "")):
            problems.append(f"{entry_id}: the line must say it was read")
    for approved_id in (APPROVED_IDS if approved_ids is None else approved_ids):
        if approved_id not in seen:
            problems.append(f"approved id has no take: {approved_id}")
    return problems
