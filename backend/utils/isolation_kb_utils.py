import os
import json
import logging
from typing import List
from settings import DIRECTORY_JSON_PATH

logger = logging.getLogger("SASS Logger")

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

def load_user_directory_groups(username: str) -> List[str]:
    """Reads directory.json dynamically to collect the security group claims array."""
    if not os.path.exists(DIRECTORY_JSON_PATH):
        print(f"Directory map file missing at: {DIRECTORY_JSON_PATH}")
        return []    
    try:
        with open(DIRECTORY_JSON_PATH, "r") as f:
            directory_data = json.load(f)       
        # Target user key inside the object
        user_record = directory_data.get(username)
        if user_record and "groups" in user_record:
            return user_record["groups"]          
    except Exception as e:
        print(f"System processing exception reading directory registry: {e}")
    return []

def verify_user_ingest_access(username: str, affiliate: str) -> bool:
    """Validates if the user's groups contain the designated administrative Ingesters role."""
    user_groups = load_user_directory_groups(username) 
    # Global Admins can bypass individual tenant restrictions
    if "Global_Admins" in user_groups:
        return True    
    required_ingester_group = f"{affiliate} Ingesters"
    return required_ingester_group in user_groups
