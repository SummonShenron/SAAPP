"""Things worth an admin's attention, worked out from real data and waiting for the admin to act on.

Shown on the Sonic Health page, and counted in the incident digest (ops_digest.py). It follows the pattern of the self-observations
(self_observations.py): fixed rules, no model, nothing happens until a person decides, and a decision is remembered.

Three kinds, each from a fixed rule:

- RECURRING  the same error class (or HTTP status) at least RECURRING_MIN times in the last 7 days. One incident already reaches
             the admin through the digest; a repeat is the thing worth stopping to look at.
- STALE      an incident that has stayed open (open, analyzing or a fix proposed) for STALE_DAYS or more.
- REVIEW     self-observations waiting for approval, or approved ones with a changed wording waiting. The one place something
             depends on the admin remembering to look.

What is deliberately NOT here: anything Sonic wants for itself, anything a model wrote, and anything that acts. "Investigate" only
puts a ready-made question in the admin's chat box; the admin presses send, and the existing read-only tools and approval cards
take it from there.

How it stays honest and simple:

- NOTHING IS STORED BUT DECISIONS. Suggestions are computed fresh from the incidents and observations every time, so they can't go
  stale or drift from the data. The only stored state is which ones the admin dismissed (hidden 30 days) or snoozed (7 days).
- THE TEXT IS STRUCTURED. An incident's own message is written by whatever failed and can contain a user's words, so only its error
  CLASS (ops_incidents.error_kind) is ever used. Titles, details and the question for the chat box are built from a class name, a
  count, a status, an id and a date, nothing else.
- IT LOOKS WHEN ASKED, NOT IN THE BACKGROUND. errAgent is read when the page is opened or a digest is being considered, with a
  short timeout and a five-minute cache, and an unreachable errAgent is reported as such rather than as "nothing to suggest".
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.services import ops_incidents
from backend.services.ops_incidents import error_kind

logger = logging.getLogger("SASS Logger")

COLLECTION = "ops_suggestion_state"

RECURRING_MIN = 3
WINDOW_DAYS = 7
STALE_DAYS = 3
STALE_STATUSES = frozenset({"open", "analyzing", "fix_proposed"})
MAX_STALE = 5
DISMISS_DAYS = 30
SNOOZE_DAYS = 7
FETCH_LIMIT = 50
READ_TIMEOUT_SECONDS = 3.0
CACHE_SECONDS = 300

REVIEW_KEY = "review:observations"
UNKNOWN_KIND = "an error"

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_KEY_RE = re.compile(r"^(?:recurring|stale|review):[A-Za-z0-9_.\-]{1,80}$")
_SAFE_NAME_RE = re.compile(r"^[a-z_]{1,40}$")

# The last successful read of the incidents, so opening the page twice doesn't ask errAgent twice.
_cache: Dict[str, Any] = {"at": None, "rows": []}


def _reset_cache() -> None:
    _cache["at"] = None
    _cache["rows"] = []


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def valid_key(key: Any) -> bool:
    return isinstance(key, str) and bool(_KEY_RE.match(key))


def _slug(kind: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", kind)[:80]


def _label(kind: str) -> str:
    return kind[:1].upper() + kind[1:] if kind == UNKNOWN_KIND else kind


def _when(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%d %b %H:%M UTC")


# ---------------------------------------------------------------------------------------------------------------
# The rules (pure)
# ---------------------------------------------------------------------------------------------------------------

def _usable(rows: Any) -> List[Dict[str, Any]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        incident_id = str(row.get("id") or "")
        created = _parse(row.get("created_at"))
        if not _ID_RE.match(incident_id) or created is None:
            continue
        out.append({"id": incident_id, "created": created, "status": str(row.get("status") or "open").strip().lower()[:24],
                    "kind": error_kind(row.get("message"))})
    return out


def incident_suggestions(rows: Any, now: datetime) -> List[Dict[str, Any]]:
    """Recurring classes first (most frequent first), then the oldest stale incidents. Pure."""
    usable = _usable(rows)
    window_start = now - timedelta(days=WINDOW_DAYS)
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for row in usable:
        if row["kind"] != UNKNOWN_KIND and row["created"] >= window_start:
            by_kind.setdefault(row["kind"], []).append(row)

    suggestions: List[Dict[str, Any]] = []
    recurring_kinds = set()
    for kind, group in sorted(by_kind.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(group) < RECURRING_MIN:
            continue
        recurring_kinds.add(kind)
        latest = max(group, key=lambda row: row["created"])
        still_open = sum(1 for row in group if row["status"] in STALE_STATUSES)
        suggestions.append({
            "key": f"recurring:{_slug(kind)}",
            "kind": "recurring",
            "title": f"{kind} has happened {len(group)} times in the last {WINDOW_DAYS} days",
            "detail": f"{still_open} still open. Most recent: {_when(latest['created'])} ({latest['status']}), id {latest['id']}.",
            "prompt": f"Look into the recurring {kind}: read incident {latest['id']} and any others like it, and tell me the likely cause.",
            "evidence": {"count": len(group), "open": still_open, "latest_id": latest["id"]},
        })

    cutoff = now - timedelta(days=STALE_DAYS)
    stale = sorted(
        (row for row in usable if row["status"] in STALE_STATUSES and row["created"] <= cutoff and row["kind"] not in recurring_kinds),
        key=lambda row: row["created"],
    )
    for row in stale[:MAX_STALE]:
        days = max((now - row["created"]).days, STALE_DAYS)
        suggestions.append({
            "key": f"stale:{row['id']}",
            "kind": "stale",
            "title": f"{_label(row['kind'])} has been {row['status'].replace('_', ' ')} for {days} days",
            "detail": f"Created {_when(row['created'])}, id {row['id']}.",
            "prompt": f"Read incident {row['id']} and tell me where it stands and what is blocking it.",
            "evidence": {"days": days, "id": row["id"]},
        })
    return suggestions


def review_suggestion(observations: Any) -> Optional[Dict[str, Any]]:
    """One entry for however many self-observations are waiting on the admin: pending ones, and live ones with a changed wording."""
    waiting = []
    for view in observations if isinstance(observations, list) else []:
        if not isinstance(view, dict):
            continue
        name = str(view.get("key") or "")
        if not _SAFE_NAME_RE.match(name):
            continue
        if view.get("state") == "pending" or (view.get("state") == "live" and view.get("proposed_text")):
            waiting.append(name)
    if not waiting:
        return None
    noun = "self-observation" if len(waiting) == 1 else "self-observations"
    shown = ", ".join(name.replace("_", " ") for name in waiting[:3])
    return {
        "key": REVIEW_KEY,
        "kind": "review",
        "title": f"{len(waiting)} {noun} waiting for your review",
        "detail": shown + (f" and {len(waiting) - 3} more" if len(waiting) > 3 else "") + ". Approve or retire them below.",
        "prompt": None,  # nothing to ask Sonic: the review happens on this page
        "evidence": {"count": len(waiting)},
    }


# ---------------------------------------------------------------------------------------------------------------
# What the admin has decided (the only stored state)
# ---------------------------------------------------------------------------------------------------------------

def load_hidden(db, now: datetime) -> set:
    try:
        docs = db[COLLECTION].find({"hidden_until": {"$gt": now.isoformat()}})
        return {str(doc.get("key")) for doc in docs}
    except Exception:
        logger.warning("[ops_suggestions] could not read what was dismissed.", exc_info=True)
        return set()


def hide(db, key: str, days: int, status: str, by: Optional[str], now: datetime) -> str:
    """Hides one suggestion for `days` ("dismissed" or "snoozed"). It comes back afterwards if the data still says so."""
    until = (now + timedelta(days=days)).isoformat()
    db[COLLECTION].update_one(
        {"key": key},
        {"$set": {"key": key, "status": status, "hidden_until": until, "decided_by": by, "decided_at": now.isoformat()}},
        upsert=True,
    )
    return until


def dismiss(db, key: str, by: Optional[str] = None, now: Optional[datetime] = None) -> str:
    return hide(db, key, DISMISS_DAYS, "dismissed", by, now or datetime.now(timezone.utc))


def snooze(db, key: str, by: Optional[str] = None, now: Optional[datetime] = None) -> str:
    return hide(db, key, SNOOZE_DAYS, "snoozed", by, now or datetime.now(timezone.utc))


# ---------------------------------------------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------------------------------------------

async def _incident_rows(reader: Any) -> Tuple[List[Dict[str, Any]], str]:
    """`(rows, status)`: status is "ok", "not_configured" or "unavailable", so an errAgent that can't be reached is never
    mistaken for "nothing to suggest"."""
    if reader is None:
        reader = ops_incidents._sdk() if ops_incidents.ops_available() else None
    if reader is None:
        return [], "not_configured"
    cached_at = _cache["at"]
    if cached_at is not None and time.monotonic() - cached_at < CACHE_SECONDS:
        return list(_cache["rows"]), "ok"
    try:
        rows = await asyncio.wait_for(reader.list_incidents(since=f"{WINDOW_DAYS}d", limit=FETCH_LIMIT), READ_TIMEOUT_SECONDS)
    except Exception:
        logger.info("[ops_suggestions] could not read incidents from errAgent.")
        return [], "unavailable"
    rows = rows if isinstance(rows, list) else []
    _cache["at"], _cache["rows"] = time.monotonic(), rows
    return list(rows), "ok"


def _observation_views(db, now: datetime) -> List[Dict[str, Any]]:
    try:
        from backend.utils.self_observations import list_observations

        return list_observations(db, now)
    except Exception:
        logger.warning("[ops_suggestions] could not read the self-observations.", exc_info=True)
        return []


async def compute(db, now: Optional[datetime] = None, *, reader: Any = None) -> Dict[str, Any]:
    """`{"suggestions": [...], "incidents": "ok" | "not_configured" | "unavailable", "generated_at": ...}`. Never raises."""
    moment = now or datetime.now(timezone.utc)
    try:
        rows, status = await _incident_rows(reader)
        views = await asyncio.to_thread(_observation_views, db, moment)
        hidden = await asyncio.to_thread(load_hidden, db, moment)
        found = incident_suggestions(rows, moment)
        review = review_suggestion(views)
        if review:
            found.append(review)
        return {"suggestions": [s for s in found if s["key"] not in hidden], "incidents": status, "generated_at": moment.isoformat()}
    except Exception:
        logger.warning("[ops_suggestions] could not work out the suggestions.", exc_info=True)
        return {"suggestions": [], "incidents": "unavailable", "generated_at": moment.isoformat()}
