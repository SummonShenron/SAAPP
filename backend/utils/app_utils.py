import os
import datetime
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
import logging
import json
from backend.state.graph_state import GraphState
from backend.models.models import llm
from settings import CHAT_HISTORY_FILE, SAVED_CONVERSATIONS_FILE
   
logger = logging.getLogger("SASS Logger")

def get_user_file_path(username: str):
    # Ensure this directory exists
    os.makedirs("saapp_data/saved_conversations", exist_ok=True)
    return os.path.join("saapp_data/saved_conversations", f"{username}.json")

def load_saved_conversations(username: str):
    user_file = get_user_file_path(username)
    if not os.path.exists(user_file):
        return []
    with open(user_file, "r") as f:
        return json.load(f)
    
def save_conversation(username: str, title: str):
    # 1. Load the list specifically for this user
    user_conversations = load_saved_conversations(username)
    
    # 2. Serialize messages
    msg_list = []
    for msg in chat_sessions.get(username, []):
        if isinstance(msg, HumanMessage):
            msg_type = "human"
        elif isinstance(msg, AIMessage):
            msg_type = "ai"
        elif isinstance(msg, SystemMessage):
            msg_type = "system"
        else:
            continue
        msg_list.append({"type": msg_type, "content": msg.content})
    
    # 3. Create the new entry
    new_entry = {
        "title": title.strip(),
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": msg_list
    }
    
    # 4. Append to the user's list
    user_conversations.append(new_entry)
    
    # 5. Save to the user-specific file
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
    """Serializes LangChain message objects to raw JSON dicts and writes to disk."""
    serialized = {}
    for user, messages in chat_sessions.items():
        msg_list = []
        for msg in messages:
            # Map Python classes to clean string labels
            if isinstance(msg, HumanMessage):
                msg_type = "human"
            elif isinstance(msg, AIMessage):
                msg_type = "ai"
            elif isinstance(msg, SystemMessage):
                msg_type = "system"
            else:
                continue
            msg_list.append({"type": msg_type, "content": msg.content})
        serialized[user] = msg_list
    try:
        with open(CHAT_HISTORY_FILE, "w") as f:
            json.dump(serialized, f, indent=4)
        logger.debug("Stateful chat history backed up to local memory.")
    except Exception as e:
        logger.error(f"Failed to write chat history backup: {e}")

def load_chat_history() -> dict:
    """Reads local JSON history and reconstructs live LangChain message class objects."""
    if not os.path.exists(CHAT_HISTORY_FILE):
        return {}
    if os.path.getsize(CHAT_HISTORY_FILE) == 0:
        logger.warning(f"{CHAT_HISTORY_FILE} was empty. Creating clean sessions dictionary.")
        return {}  
    try:
        with open(CHAT_HISTORY_FILE, "r") as f:
            raw_data = json.load(f)
        sessions = {}
        for user, msg_list in raw_data.items():
            messages = []
            for msg in msg_list:
                m_type = msg.get("type")
                content = msg.get("content", "")
                # Reconstruct classes on backend load
                if m_type == "human":
                    messages.append(HumanMessage(content=content))
                elif m_type == "ai":
                    messages.append(AIMessage(content=content))
                elif m_type == "system":
                    messages.append(SystemMessage(content=content))
            sessions[user] = messages
        logger.info(f"Restored stateful sessions for {len(sessions)} profiles from disk.")
        return sessions
    except Exception as e:
        logger.error(f"Failed to restore session history: {e}")
        return {}
    
def format_history_as_text(messages) -> str:
    """Formats the LangChain history array into a clean text transcript block for the prompt."""
    formatted = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            formatted.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            formatted.append(f"Assistant: {msg.content}")
    return "\n".join(formatted)

chat_sessions = load_chat_history()
