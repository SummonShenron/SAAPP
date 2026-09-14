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

# Identities that must never be allowed to run in open RAG mode. guest_bty is the single
# shared identity every anonymous BTY Fitness embed visitor authenticates as, so honoring a
# stored "open" setting for it would leak across every visitor of that widget.
RAG_TOGGLE_LOCKED_USERS = {"guest_bty"}


def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")


def get_user_rag_mode(username: str) -> str:
    """Returns the user's stored RAG mode, defaulting to (and locking) 'strict'."""
    if username in RAG_TOGGLE_LOCKED_USERS:
        return RAG_MODE_STRICT

    db = get_db()
    if db is not None:
        doc = db["user_settings"].find_one({"username": username})
        if doc and doc.get("rag_mode") in VALID_RAG_MODES:
            return doc["rag_mode"]
        return RAG_MODE_STRICT

    path = _get_user_file(username)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if data.get("rag_mode") in VALID_RAG_MODES:
                return data["rag_mode"]
        except Exception:
            logger.exception("Failed to load rag_mode setting for %s", username)

    return RAG_MODE_STRICT


def set_user_rag_mode(username: str, mode: str) -> str:
    """Validates and persists a user's RAG mode. Locked identities are always forced to strict."""
    if mode not in VALID_RAG_MODES:
        mode = RAG_MODE_STRICT
    if username in RAG_TOGGLE_LOCKED_USERS:
        mode = RAG_MODE_STRICT

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"rag_mode": mode}},
            upsert=True,
        )

    with open(_get_user_file(username), "w") as f:
        json.dump({"rag_mode": mode}, f, indent=2)

    return mode
