import os
import json
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
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

def personal_kb_groups(kb_id: str) -> List[str]:
    """The two groups a personal KB is made of: read access (the KB id itself) and write access
    (the "{id} Ingesters" companion). Both are plain entries in the user's `groups`."""
    return [kb_id, f"{kb_id} Ingesters"]


def new_personal_kb(clerk_id: str, username: str) -> Dict[str, Any]:
    """The `personal_kb` record stored on a user's directory document."""
    return {
        "id": make_personal_kb_id(clerk_id),
        "display_name": f"{username}'s Knowledge Base",
        "created_at": datetime.utcnow(),
    }


# One identity shared by every visitor (the recruiter sandbox, the embedded BTY widget): a "personal"
# knowledge base on it would be shared by all of them, so it is never given one.
SHARED_IDENTITIES = {"guest", "guest-recruiter@example.com", "guest_bty"}


def personal_kb_update(user_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The Mongo update that gives an existing directory user the personal KB new users get at first
    login (see /api/me), or None when there is nothing to do or it can't safely be done. Safe to run
    any number of times: the id is derived from the Clerk id, so it is always the same, groups are only
    ever added to, and a user's other groups (roles, shared affiliates) are never touched.

    Not provisioned: shared guest identities (see SHARED_IDENTITIES), and documents with no clerk_id,
    since the id is derived from it and a placeholder would give every such user the SAME knowledge
    base; those are provisioned automatically at their next login, once /api/me has recorded one."""
    if user_doc.get("username") in SHARED_IDENTITIES or user_doc.get("clerk_id") in SHARED_IDENTITIES:
        return None
    clerk_id = user_doc.get("clerk_id")
    if not clerk_id:
        return None

    existing = user_doc.get("personal_kb") or {}
    if existing.get("id"):
        # Already has one; only make sure its access groups are really there (a half-finished earlier run).
        missing = [g for g in personal_kb_groups(existing["id"]) if g not in (user_doc.get("groups") or [])]
        if not missing:
            return None
        return {"filter": {"_id": user_doc["_id"]}, "update": {"$addToSet": {"groups": {"$each": missing}}}}

    email = user_doc.get("email") or ""
    username = user_doc.get("username") or (email.split("@")[0] if email else clerk_id)
    record = new_personal_kb(clerk_id, username)
    return {
        "filter": {"_id": user_doc["_id"]},
        "update": {"$set": {"personal_kb": record}, "$addToSet": {"groups": {"$each": personal_kb_groups(record["id"])}}},
    }


def provision_personal_kbs(db, apply: bool = False) -> Dict[str, Any]:
    """Gives every existing directory user a personal KB. With apply=False (the default) nothing is
    written; the report says what would change. Returns {"provisioned": [...], "already": [...],
    "skipped": [(username, reason), ...]}."""
    report: Dict[str, Any] = {"provisioned": [], "already": [], "skipped": []}
    users = db["directory"]
    for doc in list(users.find({})):
        label = doc.get("username") or doc.get("email") or str(doc.get("_id"))
        if doc.get("username") in SHARED_IDENTITIES or doc.get("clerk_id") in SHARED_IDENTITIES:
            report["skipped"].append((label, "shared guest identity"))
            continue
        if not doc.get("clerk_id"):
            report["skipped"].append((label, "no clerk_id yet; provisioned at their next login"))
            continue
        change = personal_kb_update(doc)
        if change is None:
            report["already"].append(label)
            continue
        if apply:
            users.update_one(change["filter"], change["update"])
        report["provisioned"].append(label)
    return report


# Role/administrative groups that live in the same flat `groups` list as KB-access groups but
# are never themselves a knowledge base to show as a query-scope option. PAAPP_Admins and
# Taskboard_Admins are retired roles, kept here only because existing user records still carry
# them — without this they'd show up as bogus knowledge bases in the scope picker.
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
