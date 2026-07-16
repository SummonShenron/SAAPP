import os
import json
from datetime import datetime
from typing import List
from pydantic import BaseModel
import uuid
from backend.utils.db_utils import get_db # Add this import

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
    username: str
    activity: str
    duration_hours: float  # Ensure these match what React sends
    duration_minutes: int
    date: str
    notes: str | None = None
    type: str = "log"

def add_time_entry(payload: TimeEntryCreate) -> TimeEntry:
    entries = load_user_time(payload.username)

    # Use the fields from the payload directly
    entry = TimeEntry(
        id=str(uuid.uuid4()),
        username=payload.username,
        activity=payload.activity,
        duration_minutes=payload.duration_minutes, # Corrected field name
        duration_hours=payload.duration_hours,     # Corrected field name
        date=payload.date,                         # Corrected field name
        created_at=datetime.utcnow().isoformat(),
        notes=payload.notes,
        type=payload.type
    )

    entries.append(entry)
    save_user_time(payload.username, entries)
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