import os
import json
import hashlib
import logging
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
            # Use clerk_id, fallback to email if clerk_id is missing
            key = user.get("clerk_id") or user.get("email")
            
            if key:
                directory[key] = user
            else:
                logger.warning(f"Skipping directory entry with no ID or email: {user.get('_id')}")
        
        return directory
    except Exception as e:
        logger.error(f"Failed to fetch directory from MongoDB: {e}")
        return {}
    
def load_user_directory_groups(username: str) -> List[str]:
    """Now uses the centralized load_directory() function."""
    directory_data = load_directory() # Centralized call
    user_record = directory_data.get(username)
    if user_record and "groups" in user_record:
        return user_record["groups"]
    return []

def make_personal_kb_id(clerk_id: str) -> str:
    """Deterministic, collision-safe id for a user's personal knowledge base — derived from the
    Clerk subject (already the guaranteed-unique key used throughout this file), never from a
    username or display name, which two different users could share."""
    digest = hashlib.sha256((clerk_id or "").encode("utf-8")).hexdigest()[:10]
    return f"kb_{digest}"

# Role/administrative groups that live in the same flat `groups` list as KB-access groups but
# are never themselves a knowledge base to show as a query-scope option.
_NON_KB_ROLE_GROUPS = {"Global_Admins", "PAAPP_Admins", "Taskboard_Admins"}

def get_accessible_affiliates(username: str, user_directory: dict) -> dict:
    """Derives accessible knowledge bases purely from the caller's own `groups` — no fixed
    enum, no Global_Admins bypass. A KB is any group that isn't a known role name and isn't an
    ingest-companion group (the "{affiliate} Ingesters" suffix marks write, not read, access).
    This is what makes a dynamically-created personal KB (see make_personal_kb_id) show up here
    automatically, with zero changes to this function, the moment it's added to a user's groups."""
    user_claims = user_directory.get(username, {})
    user_groups = user_claims.get("groups", [])
    accessible_affiliates = [
        g for g in user_groups
        if g not in _NON_KB_ROLE_GROUPS and not g.endswith(" Ingesters")
    ]
    return {"accessible_affiliates": accessible_affiliates}

def resolve_kb_display_names(directory: dict) -> dict:
    """Builds an {id: display_name} lookup for personal KBs by scanning the directory dict
    load_directory() already fetched in full — no extra Mongo round trip. A user doc with no
    personal_kb field (e.g. an older account, or one of the shared Affiliate_A/B/C/D KBs, which
    have no owning user) is simply skipped."""
    display_names = {}
    for user_doc in directory.values():
        personal_kb = user_doc.get("personal_kb")
        if personal_kb and personal_kb.get("id") and personal_kb.get("display_name"):
            display_names[personal_kb["id"]] = personal_kb["display_name"]
    return display_names

def verify_user_ingest_access(username: str, affiliate: str) -> bool:
    """Validates if the user's groups contain the designated administrative Ingesters role."""
    user_groups = load_user_directory_groups(username) 
    # Global Admins can bypass individual tenant restrictions
    if "Global_Admins" in user_groups:
        return True    
    required_ingester_group = f"{affiliate} Ingesters"
    return required_ingester_group in user_groups

def verify_paapp_access(username: str) -> bool:
    user_groups = load_user_directory_groups(username)
    # Global Admins always have access
    if "Global_Admins" in user_groups:
        return True
    # PAAPP-specific admin group
    return "PAAPP_Admins" in user_groups

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