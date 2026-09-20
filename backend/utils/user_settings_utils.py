import os
import re
import json
import logging
from typing import Optional

from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "settings")
os.makedirs(DATA_DIR, exist_ok=True)

RAG_MODE_STRICT = "strict"
RAG_MODE_OPEN = "open"
VALID_RAG_MODES = {RAG_MODE_STRICT, RAG_MODE_OPEN}

DEFAULT_DEEP_THINKING = False

_TARGET_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Identities that must never opt into a heavier-than-default setting (open RAG scope, deep
# thinking's larger step budget, a pinned target repo) — guest_bty is the single shared
# identity every anonymous BTY Fitness embed visitor authenticates as, so honoring a stored
# override for it would leak across every visitor of that widget, and for deep thinking would
# also hand every anonymous visitor a much more expensive request by default.
TOGGLE_LOCKED_USERS = {"guest_bty"}


def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")


def _read_local_settings(username: str) -> dict:
    path = _get_user_file(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to load local settings file for %s", username)
        return {}


def _write_local_setting(username: str, key: str, value) -> None:
    """Merges a single key into the user's local settings file instead of overwriting it —
    each setting (rag_mode, deep_thinking, ...) is saved independently, so setting one must
    never erase another that was already saved."""
    data = _read_local_settings(username)
    data[key] = value
    with open(_get_user_file(username), "w") as f:
        json.dump(data, f, indent=2)


def _fetch_settings_doc(username: str) -> dict:
    """One read of the user's whole settings document (Mongo) or file (local fallback) — every
    getter below reads from this instead of issuing its own query, since rag_mode,
    deep_thinking, and target_repo all live on the exact same per-user record."""
    db = get_db()
    if db is not None:
        return db["user_settings"].find_one({"username": username}) or {}
    return _read_local_settings(username)


def _resolve_rag_mode(doc: dict) -> str:
    mode = doc.get("rag_mode")
    return mode if mode in VALID_RAG_MODES else RAG_MODE_STRICT


def _resolve_deep_thinking(doc: dict) -> bool:
    enabled = doc.get("deep_thinking")
    return enabled if isinstance(enabled, bool) else DEFAULT_DEEP_THINKING


def _resolve_target_repo(doc: dict) -> Optional[str]:
    repo = doc.get("target_repo")
    return repo if repo and _TARGET_REPO_RE.match(repo) else None


def get_user_rag_mode(username: str) -> str:
    """Returns the user's stored RAG mode, defaulting to (and locking) 'strict'."""
    if username in TOGGLE_LOCKED_USERS:
        return RAG_MODE_STRICT
    return _resolve_rag_mode(_fetch_settings_doc(username))


def set_user_rag_mode(username: str, mode: str) -> str:
    """Validates and persists a user's RAG mode. Locked identities are always forced to strict."""
    if mode not in VALID_RAG_MODES:
        mode = RAG_MODE_STRICT
    if username in TOGGLE_LOCKED_USERS:
        mode = RAG_MODE_STRICT

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"rag_mode": mode}},
            upsert=True,
        )

    _write_local_setting(username, "rag_mode", mode)
    return mode


def get_user_deep_thinking_mode(username: str) -> bool:
    """Returns whether the user has deep thinking enabled, defaulting to (and locking) off."""
    if username in TOGGLE_LOCKED_USERS:
        return DEFAULT_DEEP_THINKING
    return _resolve_deep_thinking(_fetch_settings_doc(username))


def set_user_deep_thinking_mode(username: str, enabled: bool) -> bool:
    """Validates and persists a user's deep thinking setting. Locked identities are always forced off."""
    enabled = bool(enabled)
    if username in TOGGLE_LOCKED_USERS:
        enabled = DEFAULT_DEEP_THINKING

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"deep_thinking": enabled}},
            upsert=True,
        )

    _write_local_setting(username, "deep_thinking", enabled)
    return enabled


def get_user_target_repo(username: str) -> Optional[str]:
    """Returns the user's pinned "owner/repo", or None to keep the existing per-message
    auto-detection (resolve_recent_mention) as the fallback. Locked identities never get a
    pinned repo of their own."""
    if username in TOGGLE_LOCKED_USERS:
        return None
    return _resolve_target_repo(_fetch_settings_doc(username))


def get_user_settings_bundle(username: str) -> dict:
    """Fetches rag_mode, deep_thinking, and target_repo in a single DB round trip — for the hot
    chat-request path, which used to call the three getters above separately (3 network round
    trips per message for 3 fields on the exact same document) instead of once."""
    if username in TOGGLE_LOCKED_USERS:
        return {"rag_mode": RAG_MODE_STRICT, "deep_thinking": DEFAULT_DEEP_THINKING, "target_repo": None}
    doc = _fetch_settings_doc(username)
    return {
        "rag_mode": _resolve_rag_mode(doc),
        "deep_thinking": _resolve_deep_thinking(doc),
        "target_repo": _resolve_target_repo(doc),
    }


def set_user_target_repo(username: str, repo: Optional[str]) -> Optional[str]:
    """Validates and persists a user's pinned target repo. A falsy or malformed value clears
    the pin (back to per-message auto-detection) rather than being silently ignored — an
    invalid save should read as "not set", not as "still whatever it was before"."""
    repo = (repo or "").strip()
    repo = repo if _TARGET_REPO_RE.match(repo) else None
    if username in TOGGLE_LOCKED_USERS:
        repo = None

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"target_repo": repo}},
            upsert=True,
        )

    _write_local_setting(username, "target_repo", repo)
    return repo


def get_user_has_seen_help(username: str) -> bool:
    """Whether this user has already dismissed the onboarding/help overlay — a layout-level
    concern, not something the chat request path needs, so unlike rag_mode/deep_thinking/
    target_repo it deliberately isn't part of get_user_settings_bundle. Not subject to
    TOGGLE_LOCKED_USERS: seeing the help panel repeatedly carries no cost/scope concern the way
    an elevated resource setting would."""
    doc = _fetch_settings_doc(username)
    seen = doc.get("has_seen_help")
    return seen if isinstance(seen, bool) else False


def set_user_has_seen_help(username: str, seen: bool) -> bool:
    """Persists that this user has dismissed the onboarding/help overlay, so it doesn't
    auto-show again on their next sign-in (on any device)."""
    seen = bool(seen)

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"has_seen_help": seen}},
            upsert=True,
        )

    _write_local_setting(username, "has_seen_help", seen)
    return seen
