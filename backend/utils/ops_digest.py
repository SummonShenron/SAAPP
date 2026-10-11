"""Sonic telling an admin about new production incidents, when the admin opens a conversation.

The second thing Sonic may write first (the first is the opener in initiation.py), and it follows the same shape on purpose:

- ON RETURN, NOT IN THE BACKGROUND. It is decided when an admin opens a conversation (POST /api/chat/opening), at that moment,
  so there is no scheduler and nothing runs while nobody is there. "I just checked errAgent" is literally true; it never says
  anything happened "overnight" or "while you were away".
- ADMIN ONLY AND OPT-IN. A separate toggle from Message First (so it can't surprise anyone who turned that on), settable only by
  a Global_Admin, and re-checked every time: a toggle left on by someone who is no longer an admin does nothing.
- ONLY WHEN THERE IS SOMETHING NEW. Incidents created since the last digest and not already announced, or suggestions
  (ops_suggestions.py) the admin hasn't been told about yet, each announced once. Nothing new means no message: there is no
  "all clear" note.
- RARE. At most one every MIN_HOURS_BETWEEN hours, never while a message of Sonic's own is still waiting, never in a thread the
  admin is in the middle of, and never in the week after a safety escalation (the same quiet period as the opener).
- NO MODEL IS INVOLVED. The message is a fixed template filled from the data, so there is no prompt to inject into and no tokens
  spent. And the text is deliberately thin: the incident's own message is written by whatever failed (it can include text a user
  typed), and this message is stored in the conversation, where the model reads it on every later turn. So only structured
  facts go in: when it happened, the error CLASS (a bare name like TypeError or an HTTP status, never free text), its status and
  its id. For the details, the admin asks, and the tool path shows them wrapped as untrusted data.
- FAILS SILENT. A slow or unreachable errAgent means no digest, never a delayed or broken conversation: a hard timeout, and a
  short pause before it will look again.
"""
import asyncio
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.services import ops_incidents
from backend.services.ops_incidents import error_kind
from backend.utils import initiation, ops_suggestions
from backend.utils.admin_utils import is_global_admin
from backend.utils.time_utils import stamp

logger = logging.getLogger("SASS Logger")

SETTINGS_COLLECTION = initiation.SETTINGS_COLLECTION
KIND = "ops_digest"

MIN_HOURS_BETWEEN = float(os.getenv("OPS_DIGEST_MIN_HOURS", "12") or 12)
FIRST_LOOKBACK_HOURS = 24
MAX_LOOKBACK_HOURS = 24 * 7
MAX_SHOWN = 3
MAX_SEEN_IDS = 100
READ_TIMEOUT_SECONDS = 3.0
SUGGESTIONS_TIMEOUT_SECONDS = 4.0
CHECK_COOLDOWN_SECONDS = 300
FETCH_LIMIT = 20

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")

# When errAgent was last asked for each admin, so a slow or down errAgent is not retried on every conversation open.
_last_checked: Dict[str, float] = {}


def digest_enabled_globally() -> bool:
    """A server-side kill switch (OPS_DIGEST_ENABLED=false), whatever anyone has chosen."""
    return os.getenv("OPS_DIGEST_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------------------------
# Per-admin state (on the same per-user settings document as the other toggles)
# ---------------------------------------------------------------------------------------------------------------

def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_state(db, username: str) -> Dict[str, Any]:
    doc = db[SETTINGS_COLLECTION].find_one(
        {"username": username}, {"_id": 0, "ops_digest": 1, "ops_digest_last_at": 1, "ops_digest_seen_ids": 1}
    ) or {}
    seen = doc.get("ops_digest_seen_ids")
    return {
        "enabled": doc.get("ops_digest") is True,
        "last_at": _parse(doc.get("ops_digest_last_at")),
        "seen": [str(i) for i in seen] if isinstance(seen, list) else [],
    }


def set_enabled(db, username: str, enabled: bool) -> bool:
    enabled = bool(enabled)
    db[SETTINGS_COLLECTION].update_one({"username": username}, {"$set": {"ops_digest": enabled}}, upsert=True)
    return enabled


def record_digest(db, username: str, now: datetime, incident_ids: List[str]) -> None:
    db[SETTINGS_COLLECTION].update_one(
        {"username": username},
        {
            "$set": {"ops_digest_last_at": now.isoformat()},
            "$push": {"ops_digest_seen_ids": {"$each": list(incident_ids), "$slice": -MAX_SEEN_IDS}},
        },
        upsert=True,
    )


def _with_db(fn: Callable, *args, default=None):
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        return fn(db, *args) if db is not None else default
    except Exception:
        logger.warning("[ops_digest] state operation failed.", exc_info=True)
        return default


def get_enabled(username: str) -> bool:
    return bool(_with_db(lambda db, u: load_state(db, u)["enabled"], username, default=False))


def update_enabled(username: str, enabled: bool) -> bool:
    return bool(_with_db(set_enabled, username, enabled, default=False))


def _is_admin(username: str) -> bool:
    try:
        from backend.utils.isolation_kb_utils import load_directory

        return is_global_admin(load_directory(), username)
    except Exception:
        return False


def available_to(username: str) -> bool:
    """Whether this person can use the digest at all: an admin, on a deployment where errAgent reads are configured."""
    return digest_enabled_globally() and ops_incidents.ops_available() and _is_admin(username)


# ---------------------------------------------------------------------------------------------------------------
# What is new, and what to say about it
# ---------------------------------------------------------------------------------------------------------------

def since_argument(last_at: Optional[datetime], now: datetime) -> str:
    """The look-back to ask errAgent for, in its own "24h" form: since the last digest, rounded up, within a week."""
    if last_at is None:
        return f"{FIRST_LOOKBACK_HOURS}h"
    hours = math.ceil(max((now - last_at).total_seconds(), 0) / 3600)
    return f"{max(1, min(hours, MAX_LOOKBACK_HOURS))}h"


def new_incidents(rows: Any, last_at: Optional[datetime], seen: List[str], now: datetime) -> List[Dict[str, Any]]:
    """The incidents worth announcing, newest first: a valid id, not announced before, and created after the last digest (or, for
    the first digest, within the first look-back)."""
    floor = last_at or (now - timedelta(hours=FIRST_LOOKBACK_HOURS))
    fresh = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        incident_id = str(row.get("id") or "")
        created = _parse(row.get("created_at"))
        if not _ID_RE.match(incident_id) or incident_id in seen or created is None or created <= floor:
            continue
        fresh.append({**row, "id": incident_id, "_created": created})
    return sorted(fresh, key=lambda row: row["_created"], reverse=True)


def build_suggestions_only(suggestions: List[Dict[str, Any]]) -> str:
    """When nothing new came in but something is waiting on the admin: the titles are built from class names, counts and ids only."""
    count = len(suggestions)
    noun = "suggestion" if count == 1 else "suggestions"
    lines = [f"I just checked: {count} {noun} waiting for you on the Sonic Health page."]
    lines.extend(f"- {item['title']}" for item in suggestions[:MAX_SHOWN])
    if count > MAX_SHOWN:
        lines.append(f"(and {count - MAX_SHOWN} more)")
    return "\n".join(lines)


def build_digest(fresh: List[Dict[str, Any]], *, first: bool, suggestions: int = 0) -> str:
    count = len(fresh)
    noun = "incident" if count == 1 else "incidents"
    when = "in the last day" if first else "since the last time I flagged any here"
    lines = [f"I just checked errAgent: {count} new production {noun} {when}."]
    for row in fresh[:MAX_SHOWN]:
        status = ops_incidents.clean(row.get("status") or "open", 24)
        lines.append(
            f"- {row['_created'].astimezone(timezone.utc).strftime('%d %b %H:%M')} UTC, {error_kind(row.get('message'))} ({status}), id {row['id']}"
        )
    if count > MAX_SHOWN:
        lines.append(f"(and {count - MAX_SHOWN} more)")
    if suggestions:
        lines.append(f"Also, {suggestions} {'suggestion is' if suggestions == 1 else 'suggestions are'} waiting on the Sonic Health page.")
    lines.append("Ask me about any of them and I'll pull up the details.")
    return "\n".join(lines)


async def _unseen_suggestions(db, moment: datetime, reader: Any, seen: List[str]) -> List[Dict[str, Any]]:
    """Suggestions (ops_suggestions.py) the admin hasn't been told about yet. Each is announced once; a failure just means none."""
    try:
        found = await asyncio.wait_for(ops_suggestions.compute(db, moment, reader=reader), SUGGESTIONS_TIMEOUT_SECONDS)
        return [item for item in found["suggestions"] if f"sugg:{item['key']}" not in seen]
    except Exception:
        logger.info("[ops_digest] could not check the suggestions; leaving them out.")
        return []


# ---------------------------------------------------------------------------------------------------------------
# The whole decision, run when a conversation is opened
# ---------------------------------------------------------------------------------------------------------------

def _reset_checks() -> None:
    _last_checked.clear()


async def maybe_post_digest(
    username: str,
    session_id: str,
    transcript: List[Any],
    *,
    save: Callable[[str, str, list], Any],
    now: Optional[datetime] = None,
    is_guest: bool = False,
    db=None,
    is_admin: Optional[Callable[[str], bool]] = None,
    sdk=None,
) -> Optional[Dict[str, Any]]:
    """Appends and saves one digest message to `transcript` and returns it as `{type, content, sent_at, initiated}`, or returns
    None (the usual case) when any gate says no or there is nothing new. Never raises: a failure here must not stop a
    conversation from opening."""
    try:
        if is_guest or not digest_enabled_globally() or not ops_incidents.ops_available():
            return None
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            return None
        moment = now or datetime.now(timezone.utc)
        state = await asyncio.to_thread(load_state, db, username)
        if not state["enabled"]:
            return None
        if not await asyncio.to_thread(is_admin or _is_admin, username):
            return None  # re-checked every time: a toggle left on by someone who is no longer an admin does nothing
        if state["last_at"] and (moment - state["last_at"]) < timedelta(hours=MIN_HOURS_BETWEEN):
            return None
        quiet_until = (await asyncio.to_thread(initiation.load_state, db, username))["quiet_until"]
        if quiet_until and moment < quiet_until:
            return None  # the week after a safety escalation, same as the opener
        refusal = initiation.thread_refusal(transcript, moment)
        if refusal:
            logger.debug("[ops_digest] not posting for %s::%s: %s", username, session_id, refusal)
            return None
        if time.monotonic() - _last_checked.get(username, -CHECK_COOLDOWN_SECONDS) < CHECK_COOLDOWN_SECONDS:
            return None
        _last_checked[username] = time.monotonic()  # set before the call, so an errAgent that is down is not retried every open

        reader = sdk if sdk is not None else ops_incidents._sdk()
        if reader is None:
            return None
        rows = await asyncio.wait_for(
            reader.list_incidents(since=since_argument(state["last_at"], moment), limit=FETCH_LIMIT), READ_TIMEOUT_SECONDS
        )
        fresh = new_incidents(rows, state["last_at"], state["seen"], moment)
        waiting = await _unseen_suggestions(db, moment, reader, state["seen"])
        if not fresh and not waiting:
            return None

        from langchain_core.messages import AIMessage

        if fresh:
            text = build_digest(fresh, first=state["last_at"] is None, suggestions=len(waiting))
        else:
            text = build_suggestions_only(waiting)
        message = stamp(AIMessage(content=text, additional_kwargs={initiation.INITIATED_KEY: True, initiation.KIND_KEY: KIND}), moment)
        transcript.append(message)
        try:
            await asyncio.to_thread(save, username, session_id, transcript)
        except Exception:
            transcript.remove(message)  # not saved, so not in the conversation, and not recorded as announced
            raise
        announced = [row["id"] for row in fresh] + [f"sugg:{item['key']}" for item in waiting]
        await asyncio.to_thread(record_digest, db, username, moment, announced)
        logger.info("[ops_digest] posted a digest of %d incident(s) and %d suggestion(s) for %s::%s.", len(fresh), len(waiting), username, session_id)
        return {"type": "ai", "content": text, "sent_at": message.additional_kwargs.get("sent_at"), initiation.INITIATED_KEY: True}
    except asyncio.TimeoutError:
        logger.info("[ops_digest] errAgent did not answer in time; no digest this time.")
        return None
    except Exception:
        logger.warning("[ops_digest] could not post a digest.", exc_info=True)
        return None
