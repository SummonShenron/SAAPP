import os
import json
import logging
from typing import List, Dict, Any
from settings import DIRECTORY_JSON_PATH
from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

def load_directory() -> Dict[str, Any]:
    """Primary entry point for all directory data."""
    # 1. Try MongoDB First
    db = get_db()
    if db is not None:
        doc = db['config'].find_one({"_id": "user_directory"})
        if doc:
            return doc.get("data", {})
            
    # 2. Fallback to Local JSON file
    try:
        if not os.path.exists(DIRECTORY_JSON_PATH):
            logger.warning("directory.json missing at %s", DIRECTORY_JSON_PATH)
            return {}
        with open(DIRECTORY_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Failed to load directory.json: %s", e)
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

