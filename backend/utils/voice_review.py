"""A person's approvals of the voice examples and the situation facts, kept in the database on top of the registry's own settings, with how often each one has been used.

WHY (2026-11-13). An example or a fact is approved by adding its id to APPROVED_IDS in a Python file (backend/components/sonic_voice_examples.py, sonic_situation_facts.py): one place, but only someone with the code open can do it, and nothing shows
whether an approved one is ever matched. This is the dashboard's side of it: an admin approves a draft, removes an approved one, or goes back to the registry's setting, and sees the counts the counters already record (voice_example_used_*, situation_fact_used_*).

HOW. A document per item in COLLECTION: `{_id: "example:<id>", state: "approved" | "removed"}`. No document means the registry decides. The two selectors read the overrides through `overrides()` (cached for OVERRIDE_TTL_SECONDS, so the
hot path is not a database read on every turn; a failed read keeps the last good answer and, with none, the registry's settings) and apply them with `apply()` before they look at which entries are approved. A removed entry is never used;
an approved override makes a draft usable. The registry itself is never edited from here.

NOTHING PERSONAL. What is stored is an id, a state, who and when. The counts are the counters' own (a count per item, never a message).
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.components import sonic_situation_facts as fact_registry
from backend.components import sonic_voice_examples as example_registry
from backend.utils import self_counters

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_voice_review"
KINDS = ("example", "fact")
STATES = ("approved", "removed")
OVERRIDE_TTL_SECONDS = 60
USAGE_DAYS = 30
_USED_KEY = {"example": "voice_example_used_", "fact": "situation_fact_used_"}
_cache: Dict[str, Any] = {"at": 0.0, "map": {}}


def _entries(kind: str) -> List[Dict[str, Any]]:
    return list(example_registry.EXAMPLES if kind == "example" else fact_registry.FACTS)


def known(kind: str, entry_id: str) -> bool:
    """True when the registry of that kind has an entry with that id. Pure."""
    return kind in KINDS and any(e["id"] == entry_id for e in _entries(kind))


def forget_cached() -> None:
    _cache.update({"at": 0.0, "map": {}})


def overrides(db: Any, *, now: Optional[float] = None) -> Dict[Tuple[str, str], str]:
    """{(kind, id): "approved" | "removed"} for the items a person has decided on in the dashboard. Cached for OVERRIDE_TTL_SECONDS. {} without a database; a failed read keeps the last good answer. Never raises. Blocking."""
    if db is None:
        return {}
    moment = time.monotonic() if now is None else now
    if _cache["at"] and moment - _cache["at"] < OVERRIDE_TTL_SECONDS:
        return _cache["map"]
    try:
        found: Dict[Tuple[str, str], str] = {}
        for doc in db[COLLECTION].find({}):
            kind, _, entry_id = str(doc.get("_id", "")).partition(":")
            if kind in KINDS and doc.get("state") in STATES and entry_id:
                found[(kind, entry_id)] = doc["state"]
        _cache.update({"at": moment, "map": found})
    except Exception:
        logger.warning("[voice_review] could not read the approvals; keeping the last ones.", exc_info=True)
    return _cache["map"]


def effective_approved(kind: str, entry: Dict[str, Any], decided: Dict[Tuple[str, str], str]) -> bool:
    """Whether an entry may be used: a person's decision here wins, else the registry's own setting. Pure."""
    state = decided.get((kind, entry.get("id", "")))
    if state == "approved":
        return True
    if state == "removed":
        return False
    return entry.get("approved") is True


def apply(entries: List[Dict[str, Any]], kind: str, decided: Dict[Tuple[str, str], str]) -> List[Dict[str, Any]]:
    """The entries with `approved` set by effective_approved: copies, and the same objects when nothing was decided. Pure."""
    if not decided:
        return entries
    return [e if effective_approved(kind, e, decided) is (e.get("approved") is True) else {**e, "approved": effective_approved(kind, e, decided)} for e in entries]


def set_state(db: Any, kind: str, entry_id: str, state: str, by: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """A person approves or removes one item. False for an unknown item or state. Blocking."""
    if db is None or state not in STATES or not known(kind, entry_id):
        return False
    moment = (now or datetime.now(timezone.utc)).isoformat()
    db[COLLECTION].update_one({"_id": f"{kind}:{entry_id}"}, {"$set": {"state": state, "by": by, "at": moment}}, upsert=True)
    forget_cached()
    return True


def reset(db: Any, kind: str, entry_id: str) -> bool:
    """Goes back to the registry's own setting for one item. False for an unknown item. Blocking."""
    if db is None or not known(kind, entry_id):
        return False
    db[COLLECTION].delete_one({"_id": f"{kind}:{entry_id}"})
    forget_cached()
    return True


def _usage(db: Any, now: datetime) -> Dict[str, int]:
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (now - timedelta(days=USAGE_DAYS)).strftime("%Y-%m-%d")
    return self_counters._totals(self_counters.fetch_counter_docs(db, USAGE_DAYS, now), start, end)


def view(db: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the dashboard shows: each example and fact with what it says, whether it may be used now, the registry's own setting, whether a person has overridden it, and how often it was used in the last USAGE_DAYS days. Blocking."""
    now = now or datetime.now(timezone.utc)
    forget_cached()
    decided = overrides(db)
    try:
        used = _usage(db, now) if db is not None else {}
    except Exception:
        logger.warning("[voice_review] could not read the usage counts.", exc_info=True)
        used = {}
    items: List[Dict[str, Any]] = []
    for kind in KINDS:
        for entry in _entries(kind):
            item = {
                "kind": kind, "id": entry["id"], "situation": entry.get("situation", ""), "asks": list(entry.get("asks") or [])[:1],
                "approved": effective_approved(kind, entry, decided), "default": entry.get("approved") is True, "decision": decided.get((kind, entry["id"])),
                "used": int(used.get(f"{_USED_KEY[kind]}{entry['id']}", 0)),
            }
            if kind == "example":
                item.update({"tone": entry.get("tone", ""), "text": [entry.get("reply", "")]})
            else:
                item.update({"tone": "", "text": list(entry.get("facts") or [])})
            items.append(item)
    return {"items": items, "days": USAGE_DAYS}
