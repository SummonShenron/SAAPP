"""Per-user Microsoft (Outlook mail, Outlook calendar, OneDrive) connection storage.

Mirrors backend/utils/google_calendar_utils.py on purpose, in its own collections: a person may connect Google, Microsoft, or both, and
disconnecting one must never touch the other. Only personal Microsoft accounts are supported (outlook.com, hotmail, live), so no admin consent is involved.
Tokens are stored encrypted; backend/services/microsoft_oauth.py is the only code that decrypts them.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.utils.db_utils import get_db
from backend.utils.google_calendar_utils import CALENDAR_LOCKED_USERS

logger = logging.getLogger("SASS Logger")

# The shared guest identities must never hold a real third-party grant, for the same reason as Google.
MICROSOFT_LOCKED_USERS = CALENDAR_LOCKED_USERS

_PENDING_TTL_SECONDS = 600

SCOPE_MAIL_READ = "Mail.Read"
SCOPE_MAIL_SEND = "Mail.Send"
SCOPE_CALENDAR = "Calendars.ReadWrite"
SCOPE_FILES_READ = "Files.Read"


def _connections_collection():
    db = get_db()
    return None if db is None else db["microsoft_connections"]


def _pending_collection():
    db = get_db()
    return None if db is None else db["microsoft_oauth_pending"]


def ensure_indexes() -> None:
    connections = _connections_collection()
    if connections is not None:
        connections.create_index("username", unique=True)
    pending = _pending_collection()
    if pending is not None:
        pending.create_index("state", unique=True)
        pending.create_index("created_at", expireAfterSeconds=_PENDING_TTL_SECONDS)


def _fetch_connection_doc(username: str) -> Optional[dict]:
    connections = _connections_collection()
    if connections is None:
        return None
    return connections.find_one({"username": username})


def get_connection_status(username: str) -> dict:
    """A frontend-safe summary; never the encrypted token fields."""
    empty = {"connected": False, "account_email": None, "scopes": [], "connected_at": None}
    if username in MICROSOFT_LOCKED_USERS:
        return empty
    doc = _fetch_connection_doc(username)
    if not doc:
        return empty
    return {
        "connected": True,
        "account_email": doc.get("account_email"),
        "scopes": doc.get("scopes", []),
        "connected_at": doc.get("connected_at"),
    }


def is_connected(username: str) -> bool:
    return bool(get_connection_status(username).get("connected"))


def has_granted_scope(username: str, scope: str) -> bool:
    """Microsoft reports granted scopes bare ("Mail.Read") or as full URLs, so both spellings count. A person can untick individual
    permissions on the consent screen, so a connection does not guarantee every scope."""
    if username in MICROSOFT_LOCKED_USERS:
        return False
    doc = _fetch_connection_doc(username)
    if not doc:
        return False
    granted = {str(s).rsplit("/", 1)[-1].lower() for s in (doc.get("scopes") or [])}
    return scope.lower() in granted


def save_connection(
    username: str,
    *,
    account_email: Optional[str],
    scopes: list,
    expires_at,
    access_token_encrypted: str,
    refresh_token_encrypted: Optional[str],
) -> None:
    if username in MICROSOFT_LOCKED_USERS:
        logger.warning("Refusing to save a Microsoft connection for a locked identity: %s", username)
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
    """Partial update after a refresh. Microsoft rotates the refresh token on every refresh, so the new one is normally stored."""
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
    if username in MICROSOFT_LOCKED_USERS:
        return None
    return _fetch_connection_doc(username)


def delete_connection(username: str) -> bool:
    connections = _connections_collection()
    if connections is None:
        return False
    return connections.delete_one({"username": username}).deleted_count > 0


def create_pending(state: str, username: str, code_verifier: str, return_to: Optional[str]) -> None:
    pending = _pending_collection()
    if pending is None:
        return
    pending.update_one(
        {"state": state},
        {"$set": {"username": username, "code_verifier": code_verifier, "return_to": return_to, "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def get_pending(state: str) -> Optional[dict]:
    pending = _pending_collection()
    if pending is None:
        return None
    return pending.find_one({"state": state})


def consume_pending(state: str) -> None:
    """Single use, so a replayed callback can't complete twice."""
    pending = _pending_collection()
    if pending is None:
        return
    pending.delete_one({"state": state})
