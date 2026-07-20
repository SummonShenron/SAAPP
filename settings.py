import os
import toml

# --- Existing variables kept as-is to prevent breaking changes ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIRECTORY_JSON_PATH = os.path.join(PROJECT_ROOT, "directory.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "chroma_db")
USER_DIRECTORY_FILE = os.path.join(BASE_DIR, "directory.json")
CHAT_HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
SAVED_CONVERSATIONS_FILE = os.path.join(BASE_DIR, "saved_conversations.json")
PAAPP_BASE_URL = "https://paapp-u2l9.onrender.com"

# --- The TOML Bridge ---
config = {}
toml_path = os.path.join(PROJECT_ROOT, "settings.toml")
if os.path.exists(toml_path):
    config = toml.load(toml_path)

# Export LOCAL_DEV (defaults to False if file/key is missing)
LOCAL_DEV = config.get("development", {}).get("LOCAL_DEV", False)