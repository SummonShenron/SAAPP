from backend.components.taskboard import read_store
# Adjust this import to wherever your get_db function actually lives
from backend.utils.db_utils import get_db 

def migrate():
    db = get_db()
    if db is None:
        print("Failed to connect to MongoDB.")
        return

    # Read from the old JSON file
    old_store = read_store()
    tasks = old_store.get("tasks", [])

    if not tasks:
        print("No tasks found in the old JSON store.")
        return

    # Clean up the old string IDs so Mongo can generate proper ObjectIds
    for task in tasks:
        task.pop("id", None)

    # Bulk insert into MongoDB
    result = db["tasks"].insert_many(tasks)
    print(f"Success! Migrated {len(result.inserted_ids)} tasks to MongoDB.")

if __name__ == "__main__":
    migrate()