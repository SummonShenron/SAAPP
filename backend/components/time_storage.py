import os
import json
from datetime import datetime
from typing import List
from pydantic import BaseModel
import uuid
from backend.utils.db_utils import get_db # Add this import
import logging
from typing import Optional

logger = logging.getLogger("SASS Logger")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "time")
os.makedirs(DATA_DIR, exist_ok=True)

# 1. Update TimeEntry to EXACTLY MATCH the React frontend
class TimeEntry(BaseModel):
    id: str
    username: str
    activity: str
    duration_hours: float    # Matches React
    duration_minutes: int    # Matches React
    date: str                # Matches React
    created_at: str
    notes: str | None = None
    type: str = "log"

# 2. TimeEntryCreate stays the same (this is what the AI/tool sends)
# In backend/components/time_storage.py

class TimeEntryCreate(BaseModel):
    # Add "= None" or "= 0" to make them optional
    username: str = "default_user" 
    activity: str
    date: str
    type: str = "event"
    notes: Optional[str] = ""
    duration_minutes: Optional[int] = 0
    duration_hours: Optional[float] = 0

import requests # You already use this for your headless-chat, so it's likely available

def add_time_entry(payload: TimeEntryCreate) -> TimeEntry:
    entries = load_user_time(payload.username)

    entry = TimeEntry(
        id=str(uuid.uuid4()),
        username=payload.username,
        activity=payload.activity,
        duration_minutes=payload.duration_minutes,
        duration_hours=payload.duration_hours,
        date=payload.date,
        created_at=datetime.utcnow().isoformat(),
        notes=payload.notes,
        type=payload.type
    )

    entries.append(entry)
    save_user_time(payload.username, entries)
    
    # --- RESTORED SYNC TRIGGER ---
    if entry.type == "event":
        try:
            # Re-trigger the headless API to handle the Google sync 
            # This mimics the "Zero-Import" behavior by delegating the work
            requests.post(
                f"{os.getenv('PAAPP_BASE_URL', 'http://localhost:8000')}/api/sync-google",
                json={"activity": entry.activity, "date": entry.date, "duration": entry.duration_minutes},
                headers={"x-saapp": "true"}
            )
            logger.info(f"Sync triggered for event: {entry.activity}")
        except Exception as e:
            logger.error(f"Sync trigger failed: {e}")

    return entry

def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")

# 3. Safe loading prevents the 500 crash!
def load_user_time(username: str) -> List[TimeEntry]:
    """Loads entries from MongoDB, falling back to JSON file."""
    db = get_db()
    
    # 1. Try MongoDB
    if db is not None:
        doc = db['time_entries'].find_one({"username": username})
        if doc and "entries" in doc:
            # Convert stored dicts back to TimeEntry objects
            return [TimeEntry(**item) for item in doc["entries"]]
            
    # 2. Fallback to Local JSON
    path = _get_user_file(username)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        try:
            raw = json.load(f)
            return [TimeEntry(**item) for item in raw]
        except Exception:
            return []

def save_user_time(username: str, entries: List[TimeEntry]):
    """Saves entries to MongoDB and syncs to local JSON file."""
    # Convert objects to dicts for storage
    entries_dicts = [entry.dict() for entry in entries]
    
    # 1. Save to MongoDB
    db = get_db()
    if db is not None:
        db['time_entries'].update_one(
            {"username": username},
            {"$set": {"entries": entries_dicts}},
            upsert=True
        )
        
    # 2. Sync to Local JSON (The Safety Net)
    path = _get_user_file(username)
    with open(path, "w") as f:
        json.dump(entries_dicts, f, indent=2)

def clear_user_time(username: str):
    save_user_time(username, [])