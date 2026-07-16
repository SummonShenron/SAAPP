import json
import os
from tempfile import NamedTemporaryFile
from backend.utils.db_utils import get_db # Import your DB utility

STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "saapp_data", "taskboard.json")
os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)

def read_store():
    # 1. Try MongoDB First
    db = get_db()
    if db is not None:
        doc = db['taskboard_data'].find_one({"_id": "main_taskboard"})
        if doc:
            return {"tasks": doc.get("tasks", [])}

    # 2. Fallback to Local JSON
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"tasks": []}

def write_store(data):
    # 1. Write to MongoDB
    db = get_db()
    if db is not None:
        # We use replace_one to update the "main_taskboard" document
        db['taskboard_data'].replace_one(
            {"_id": "main_taskboard"},
            {"_id": "main_taskboard", "tasks": data.get("tasks", [])},
            upsert=True
        )

    # 2. Atomic Write to Local JSON (The Safety Net)
    with NamedTemporaryFile("w", delete=False, dir=os.path.dirname(STORE_PATH), encoding="utf-8") as tf:
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        tmpname = tf.name
    os.replace(tmpname, STORE_PATH)