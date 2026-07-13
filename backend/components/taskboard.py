import json
import os
from tempfile import NamedTemporaryFile

STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "saapp_data", "taskboard.json")
os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)

def read_store():
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"tasks": []}

def write_store(data):
    # atomic write
    with NamedTemporaryFile("w", delete=False, dir=os.path.dirname(STORE_PATH), encoding="utf-8") as tf:
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        tmpname = tf.name
    os.replace(tmpname, STORE_PATH)
