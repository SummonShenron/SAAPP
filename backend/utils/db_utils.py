import os
from pymongo import MongoClient
from dotenv import load_dotenv
import logging
from datetime import datetime, timezone
from typing import Optional, Any
# Load your .env file
load_dotenv()
logger = logging.getLogger("SASS Logger")
# Global variable to hold the client so we don't reconnect every time
_client = None

def get_db():
    """
    Returns the database object if USE_DB is true, else returns None.
    """
    global _client
    
    if os.getenv("USE_DB") != "true":
        logger.info("Not Using MongoDB")
        return None
        
    if _client is None:
        uri = os.getenv("MONGO_URI")
        _client = MongoClient(uri)
    # logger.info(f"Connecting to MongoDB at: {os.environ.get('MONGO_URI', 'NOT SET')}")
    # 'saapp_database' will be the name of your DB in the cluster
    return _client['saapp_database']

def test_connection():
    """Run this once to see if it works!"""
    db = get_db()
    if db is None:
        print("USE_DB is not set to true.")
        return False
    try:
        # The 'ping' command
        db.command('ping')
        print("Successfully connected to MongoDB!")
        return True
    except Exception as e:
        print(f"Connection failed: {e}")
        return False

def resolve_service_registry_repo(service_name: str) -> Optional[str]:
    """
    Looks up service_name -> target_repo from service_registry.
    Expected doc shape:
      {
        "service_name": "payment-gateway",
        "target_repo": "owner/repo",
        "active": true
      }
    """
    db = get_db()
    if db is None:
        return None

    doc = db["service_registry"].find_one(
        {"service_name": service_name, "active": {"$ne": False}},
        {"target_repo": 1},
    )
    if not doc:
        return None

    target_repo = doc.get("target_repo")
    if isinstance(target_repo, str) and target_repo.strip():
        return target_repo.strip()

    return None


def save_error_event(event: dict[str, Any]) -> str:
    """
    Persists normalized ingest events to error_events.
    Returns inserted event ID as string.
    """
    db = get_db()
    if db is None:
        raise RuntimeError("Database is not enabled.")

    payload = dict(event)
    payload["created_at"] = datetime.now(timezone.utc)

    result = db["error_events"].insert_one(payload)
    return str(result.inserted_id)