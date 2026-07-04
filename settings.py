import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIRECTORY_JSON_PATH = os.path.join(PROJECT_ROOT, "directory.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "chroma_db")
USER_DIRECTORY_FILE = os.path.join(BASE_DIR, "directory.json")
CHAT_HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
SAVED_CONVERSATIONS_FILE = os.path.join(BASE_DIR, "saved_conversations.json")