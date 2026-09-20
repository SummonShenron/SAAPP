import os
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

# Identities that must never opt into a heavier-than-default setting (open RAG scope, deep
# thinking's larger step budget) — guest_bty is the single shared identity every anonymous
# BTY Fitness embed visitor authenticates as, so honoring a stored override for it would leak
# across every visitor of that widget, and for deep thinking would also hand every anonymous
# visitor a much more expensive request by default.
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


def get_user_rag_mode(username: str) -> str:
    """Returns the user's stored RAG mode, defaulting to (and locking) 'strict'."""
    if username in TOGGLE_LOCKED_USERS:
        return RAG_MODE_STRICT

    db = get_db()
    if db is not None:
        doc = db["user_settings"].find_one({"username": username})
        if doc and doc.get("rag_mode") in VALID_RAG_MODES:
            return doc["rag_mode"]
        return RAG_MODE_STRICT

    data = _read_local_settings(username)
    if data.get("rag_mode") in VALID_RAG_MODES:
        return data["rag_mode"]
    return RAG_MODE_STRICT


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

    db = get_db()
    if db is not None:
        doc = db["user_settings"].find_one({"username": username})
        if doc and isinstance(doc.get("deep_thinking"), bool):
            return doc["deep_thinking"]
        return DEFAULT_DEEP_THINKING

    data = _read_local_settings(username)
    if isinstance(data.get("deep_thinking"), bool):
        return data["deep_thinking"]
    return DEFAULT_DEEP_THINKING


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
