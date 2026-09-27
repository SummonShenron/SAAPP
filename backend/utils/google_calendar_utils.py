import logging
from datetime import datetime, timezone
from typing import Optional

from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

# Both hardcoded guest sentinels from backend/auth/isolation_auth.py — every anonymous visitor of
# either guest path authenticates as the exact same shared identity, so honoring a stored Google
# Calendar connection for either would leak one real account across every anonymous session that
# hits that path. Unlike user_settings_utils.TOGGLE_LOCKED_USERS (which only needs guest_bty, since
# guest-recruiter@example.com never touches settings), a real third-party OAuth grant is sensitive
# enough that both sentinels are excluded here.
CALENDAR_LOCKED_USERS = {"guest-recruiter@example.com", "guest_bty"}

# Abandoned OAuth attempts (user starts the flow, never completes it) self-expire via a Mongo TTL
# index rather than a batched retention job — see ensure_indexes().
_PENDING_TTL_SECONDS = 600


def _connections_collection():
    db = get_db()
    return None if db is None else db["google_calendar_connections"]


def _pending_collection():
    db = get_db()
    return None if db is None else db["google_calendar_oauth_pending"]


def ensure_indexes() -> None:
    """Idempotent — safe to call on every startup. Unique index on each collection's natural key,
    plus a TTL index so a pending OAuth attempt the user never finishes cleans itself up instead of
    accumulating forever."""
    connections = _connections_collection()
    if connections is not None:
        connections.create_index("username", unique=True)

    pending = _pending_collection()
    if pending is not None:
        pending.create_index("state", unique=True)
        pending.create_index("created_at", expireAfterSeconds=_PENDING_TTL_SECONDS)


def _fetch_connection_doc(username: str) -> Optional[dict]:
    """One read of the user's connection document — every getter below reads from this instead of
    issuing its own query, mirroring user_settings_utils._fetch_settings_doc."""
    connections = _connections_collection()
    if connections is None:
        return None
    return connections.find_one({"username": username})


def get_connection_status(username: str) -> dict:
    """Returns a frontend-safe summary — never the encrypted token fields themselves."""
    if username in CALENDAR_LOCKED_USERS:
        return {"connected": False, "account_email": None, "scopes": [], "connected_at": None}

    doc = _fetch_connection_doc(username)
    if not doc:
        return {"connected": False, "account_email": None, "scopes": [], "connected_at": None}

    return {
        "connected": True,
        "account_email": doc.get("account_email"),
        "scopes": doc.get("scopes", []),
        "connected_at": doc.get("connected_at"),
    }


def has_granted_scope(username: str, scope: str) -> bool:
    """Distinguishes "no connection at all" from "connected, but the connection predates this
    scope being added" — a real gap opened up once Drive/Docs/Gmail scopes were added after
    Calendar was already live: get_valid_access_token() still returns a token for an
    already-connected user, but calling a Gmail/Drive/Docs endpoint with it 403s
    (insufficient_permission) rather than the invalid_grant that the self-heal-on-refresh path
    already handles. Every action outside Calendar's own three functions must check this before
    calling its API, so the failure surfaces as "reconnect to grant access" instead of a raw 403."""
    if username in CALENDAR_LOCKED_USERS:
        return False
    doc = _fetch_connection_doc(username)
    if not doc:
        return False
    return scope in (doc.get("scopes") or [])


def save_connection(
    username: str,
    *,
    account_email: Optional[str],
    scopes: list,
    expires_at,
    access_token_encrypted: str,
    refresh_token_encrypted: Optional[str],
) -> None:
    """Upserts a user's Google Calendar connection. Refuses (no-op) for locked identities — same
    guard pattern as user_settings_utils's setters, checked here rather than trusting every caller
    to check first."""
    if username in CALENDAR_LOCKED_USERS:
        logger.warning("Refusing to save a Google Calendar connection for a locked identity: %s", username)
        return

    connections = _connections_collection()
    if connections is None:
        return

    now = datetime.now(timezone.utc)
    connections.update_one(
        {"username": username},
        {
            "$set": {
                "account_email": account_email,
                "scopes": scopes,
                "expires_at": expires_at,
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "updated_at": now,
            },
            "$setOnInsert": {"connected_at": now},
        },
        upsert=True,
    )


def update_tokens(
    username: str,
    *,
    access_token_encrypted: str,
    expires_at,
    refresh_token_encrypted: Optional[str] = None,
) -> None:
    """Partial update after a refresh — only overwrites refresh_token_encrypted if Google actually
    rotated it (Google doesn't always return a new refresh token on a plain access-token refresh)."""
    connections = _connections_collection()
    if connections is None:
        return

    fields = {
        "access_token_encrypted": access_token_encrypted,
        "expires_at": expires_at,
        "updated_at": datetime.now(timezone.utc),
    }
    if refresh_token_encrypted:
        fields["refresh_token_encrypted"] = refresh_token_encrypted

    connections.update_one({"username": username}, {"$set": fields})


def get_encrypted_tokens(username: str) -> Optional[dict]:
    """Raw doc (still-encrypted fields) for google_calendar_oauth.py to decrypt — nothing else
    should read access_token_encrypted/refresh_token_encrypted directly."""
    if username in CALENDAR_LOCKED_USERS:
        return None
    return _fetch_connection_doc(username)


def delete_connection(username: str) -> bool:
    connections = _connections_collection()
    if connections is None:
        return False
    result = connections.delete_one({"username": username})
    return result.deleted_count > 0


def create_pending(state: str, username: str, code_verifier: str, return_to: Optional[str]) -> None:
    pending = _pending_collection()
    if pending is None:
        return
    pending.update_one(
        {"state": state},
        {
            "$set": {
                "username": username,
                "code_verifier": code_verifier,
                "return_to": return_to,
                "created_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )


def get_pending(state: str) -> Optional[dict]:
    pending = _pending_collection()
    if pending is None:
        return None
    return pending.find_one({"state": state})


def consume_pending(state: str) -> None:
    """Single-use — deletes on read so a replayed/reused OAuth callback can't complete twice."""
    pending = _pending_collection()
    if pending is None:
        return
    pending.delete_one({"state": state})
