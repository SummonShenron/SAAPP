import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
from settings import DIRECTORY_JSON_PATH
from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

def get_user_record(clerk_id: str):
    """
    Retrieves a user document from MongoDB by clerk_id.
    This replaces the legacy load_directory() logic.
    """
    db = get_db() #[cite: 1]
    if db is None:
        return None
        
    return db["users"].find_one({"clerk_id": clerk_id})

def load_directory() -> Dict[str, Any]:
    db = get_db()
    directory = {}

    try:
        cursor = db["directory"].find({})
        for user in cursor:
            keys = []
            for field in ("clerk_id", "email", "username"):
                value = user.get(field)
                if isinstance(value, str) and value.strip():
                    keys.append(value.strip())

            for key in keys:
                directory[key] = user
                directory[key.lower()] = user
                if "@" in key:
                    directory[key.split("@", 1)[0]] = user
                    directory[key.split("@", 1)[0].lower()] = user

            if not keys:
                logger.warning(f"Skipping directory entry with no ID or email: {user.get('_id')}")

        return directory
    except Exception as e:
        logger.error(f"Failed to fetch directory from MongoDB: {e}")
        return {}


def load_user_directory_groups(username: str) -> List[str]:
    """Now uses the centralized load_directory() function."""
    if not username:
        return []

    directory_data = load_directory()
    candidates = []
    value = str(username).strip()
    if value:
        candidates.extend([value, value.lower()])
        if "@" in value:
            candidates.extend([value.split("@", 1)[0], value.split("@", 1)[0].lower()])

    for candidate in candidates:
        user_record = directory_data.get(candidate)
        if user_record and "groups" in user_record:
            return user_record["groups"]
    return []

def get_accessible_affiliates(username: str, user_directory: dict) -> dict:
    # Now this function just does logic, it doesn't care about startup
    user_claims = user_directory.get(username, {})
    user_groups = user_claims.get("groups", [])
    accessible_affiliates = []
    if "Affiliate_A" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_A")
    if "Affiliate_B" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_B") 
    if "Affiliate_C" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_C")
    if "Affiliate_D" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_D")
    return {"accessible_affiliates": accessible_affiliates}

def verify_user_ingest_access(username: str, affiliate: str) -> bool:
    """Validates if the user's groups contain the designated administrative Ingesters role."""
    user_groups = load_user_directory_groups(username) 
    # Global Admins can bypass individual tenant restrictions
    if "Global_Admins" in user_groups:
        return True    
    required_ingester_group = f"{affiliate} Ingesters"
    return required_ingester_group in user_groups

def verify_paapp_access(username: str) -> bool:
    if not username:
        return False

    normalized = str(username).strip().lower()
    candidates = {normalized}
    if "@" in normalized:
        candidates.add(normalized.split("@", 1)[0])
    candidates.update({
        "guest_erragent",
        "guest-erragent",
        "guest_erragent@erragent.local",
        "guest-ops",
        "guest_ops",
    })

    for candidate in candidates:
        user_groups = load_user_directory_groups(candidate)
        if "Global_Admins" in user_groups:
            return True
        if "PAAPP_Admins" in user_groups:
            return True
        if candidate in {"guest_erragent", "guest-erragent", "guest_erragent@erragent.local"}:
            return True

    return False


def ensure_guest_user_record(username: str, email: str | None = None, groups: List[str] | None = None) -> Dict[str, Any]:
    """Ensures an embedded guest principal exists in MongoDB with the right access groups."""
    db = get_db()
    if db is None:
        return {}

    normalized_username = (username or "").strip()
    normalized_email = (email or "").strip()
    if not normalized_username:
        return {}

    directory = db["directory"]
    record = directory.find_one({"$or": [{"clerk_id": normalized_username}, {"email": normalized_email}, {"username": normalized_username}]})
    if record is not None:
        missing_groups = [group for group in (groups or []) if group not in record.get("groups", [])]
        if missing_groups:
            directory.update_one({"_id": record["_id"]}, {"$addToSet": {"groups": {"$each": missing_groups}}})
        return {**record, "groups": record.get("groups", []) + [g for g in missing_groups if g not in record.get("groups", [])]}

    default_groups = list(groups or ["PAAPP_Admins"])
    new_record = {
        "clerk_id": normalized_username,
        "email": normalized_email or f"{normalized_username}@erragent.local",
        "username": normalized_username,
        "groups": default_groups,
        "is_guest": True,
        "created_at": datetime.now(timezone.utc),
    }
    directory.insert_one(new_record)
    return new_record


def seed_guest_tasks(db, username: str):
    """
    Auto-populates the MongoDB tasks collection with interactive, 
    sandbox data for the guest recruiter.
    """
    mock_tasks = [
        {
            "username": username,
            "lane": "todo",
            "title": "Review Jack's Resume 📄",
            "description": "Download his resume from the Chat tab or ask the AI assistant about his qualifications."
        },
        {
            "username": username,
            "lane": "in_progress",
            "title": "Test RAG Engine 🤖",
            "description": "Go to the Chat page and ask: 'What technologies did Jack use to build this app?'"
        },
        {
            "username": username,
            "lane": "done",
            "title": "Schedule a Chat ☕",
            "description": "Reach out to Jack to set up a technical pairing session or virtual coffee."
        }
    ]
    
    # Batch insert the mock tasks into MongoDB
    db["tasks"].insert_many(mock_tasks)