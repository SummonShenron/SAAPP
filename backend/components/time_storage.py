import os
import json
from datetime import datetime
from typing import List
from pydantic import BaseModel
import uuid

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

# 2. TimeEntryCreate stays the same (this is what the AI/tool sends)
class TimeEntryCreate(BaseModel):
    username: str
    activity: str
    minutes: int
    date_iso: str
    notes: str | None = None
    hours: float | None = None

def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")

# 3. Safe loading prevents the 500 crash!
def load_user_time(username: str) -> List[TimeEntry]:
    path = _get_user_file(username)
    if not os.path.exists(path):
        return []
    
    with open(path, "r") as f:
        try:
            raw = json.load(f)
            entries = []
            for entry in raw:
                try:
                    entries.append(TimeEntry(**entry))
                except Exception as e:
                    print(f"Skipping malformed entry: {e}")
            return entries
        except Exception as e:
            print(f"Error reading JSON: {e}")
            return []

def save_user_time(username: str, entries: List[TimeEntry]):
    path = _get_user_file(username)
    with open(path, "w") as f:
        json.dump([entry.dict() for entry in entries], f, indent=2)

def add_time_entry(payload: TimeEntryCreate) -> TimeEntry:
    entries = load_user_time(payload.username)

    calculated_hours = payload.hours if payload.hours is not None else round(payload.minutes / 60, 2)

    entry = TimeEntry(
        id=str(uuid.uuid4()),
        username=payload.username,
        activity=payload.activity,
        duration_minutes=payload.minutes,  # Map to new frontend name
        duration_hours=calculated_hours,   # Map to new frontend name
        date=payload.date_iso,             # Map to new frontend name
        created_at=datetime.utcnow().isoformat(),
        notes=payload.notes
    )

    entries.append(entry)
    save_user_time(payload.username, entries)
    return entry

def clear_user_time(username: str):
    save_user_time(username, [])