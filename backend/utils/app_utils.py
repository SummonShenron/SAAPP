import os
import datetime
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
import logging
import json
from backend.state.graph_state import GraphState
from backend.models.models import llm
from settings import CHAT_HISTORY_FILE, SAVED_CONVERSATIONS_FILE
from backend.utils.db_utils import get_db # Ensure this is imported
from fastapi import HTTPException
import subprocess
import sys
logger = logging.getLogger("SASS Logger")

# def sync_run_script(script_path):
#     """Synchronous function to run the script via subprocess."""
#     # We use subprocess.run, which handles the execution and waits for completion
#     subprocess.run([sys.executable, script_path], check=True)

chat_sessions = {}
def serialize_doc(doc):
    if doc and "_id" in doc:
        doc["id"] = str(doc["_id"])
        del doc["_id"]
    return doc

# Add this function to your app.py
def get_db_dependency():
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    return db

def get_user_file_path(username: str):
    # Ensure this directory exists
    os.makedirs("saapp_data/saved_conversations", exist_ok=True)
    return os.path.join("saapp_data/saved_conversations", f"{username}.json")

def load_saved_conversations(username: str) -> list:
    """Loads saved conversations from MongoDB, falling back to JSON file."""
    db = get_db()
    
    # 1. Try MongoDB
    if db is not None:
        doc = db['saved_conversations'].find_one({"username": username})
        if doc and "conversations" in doc:
            return doc["conversations"]
            
    # 2. Fallback to Local JSON
    user_file = get_user_file_path(username)
    if not os.path.exists(user_file):
        return []
    with open(user_file, "r") as f:
        return json.load(f)
    
def save_conversation(username: str, title: str, messages: list):
    """Saves the explicitly provided messages to MongoDB."""
    
    # We no longer need the for-loop reading from chat_sessions!
    # The frontend is going to pass the formatted messages directly to us.
    new_entry = {
        "title": title.strip(),
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": messages # Take it straight from the argument
    }
    
    # 1. Update MongoDB
    db = get_db()
    if db is not None:
        db['saved_conversations'].update_one(
            {"username": username},
            {"$push": {"conversations": new_entry}},
            upsert=True
        )
        logger.info(f"Saved conversation '{title}' for {username} to MongoDB.")
    
    # 2. Update Local JSON (Always keep this as your safety net)
    user_conversations = load_saved_conversations(username) # This will pull from Mongo if available
    user_conversations.append(new_entry) # Add to the list
    with open(get_user_file_path(username), "w") as f:
        json.dump(user_conversations, f, indent=4)

def list_saved_conversations(username: str):
    # Load the specific file for this user
    conversations = load_saved_conversations(username)
    # Extract titles from the list
    return [c["title"] for c in conversations]

def load_saved_conversation(username: str, title: str):
    # Load the specific file for this user
    conversations = load_saved_conversations(username)
    # Search the list
    for conversation in conversations:
        if conversation["title"].lower() == title.lower():
            return conversation
    return None   


def save_chat_history():
    """Saves to MongoDB first, falls back to JSON."""
    db = get_db()
    # Serialize data first
    serialized = {}
    for user, messages in chat_sessions.items():
        serialized[user] = [
            {"type": "human" if isinstance(msg, HumanMessage) else "ai" if isinstance(msg, AIMessage) else "system", 
             "content": msg.content} 
            for msg in messages
        ]

    if db is not None:
        # Save each user session to MongoDB
        for username, messages in serialized.items():
            db['chat_history'].update_one(
                {"username": username},
                {"$set": {"messages": messages}},
                upsert=True
            )
        logger.debug("Chat history saved to MongoDB.")
    else:
        # Fallback to local file
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump(serialized, f, indent=4)
        logger.debug("Chat history saved to local JSON.")

def load_chat_history() -> dict:
    """Loads from MongoDB first, falls back to JSON."""
    db = get_db()
    raw_data = {}

    if db is not None:
        # Load from MongoDB
        cursor = db['chat_history'].find({}, {'_id': 0})
        raw_data = {doc['username']: doc['messages'] for doc in cursor}
        logger.info("Restored chat sessions from MongoDB.")
    else:
        # Fallback to local file
        if os.path.exists(CHAT_HISTORY_FILE):
            with open(CHAT_HISTORY_FILE, "r") as f:
                raw_data = json.load(f)
            logger.info("Restored chat sessions from local JSON.")

    # Reconstruct LangChain objects (this part remains largely the same)
    sessions = {}
    for user, msg_list in raw_data.items():
        messages = []
        for msg in msg_list:
            m_type = msg.get("type")
            content = msg.get("content", "")
            if m_type == "human": messages.append(HumanMessage(content=content))
            elif m_type == "ai": messages.append(AIMessage(content=content))
            elif m_type == "system": messages.append(SystemMessage(content=content))
        sessions[user] = messages
    return sessions
    
def format_history_as_text(messages) -> str:
    """Formats the LangChain history array into a clean text transcript block for the prompt."""
    formatted = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            formatted.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            formatted.append(f"Assistant: {msg.content}")
    return "\n".join(formatted)