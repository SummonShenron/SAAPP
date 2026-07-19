import os
import json
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