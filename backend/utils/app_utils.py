import os
import datetime
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
import logging
import json
from backend.state.graph_state import GraphState
from backend.models.models import llm
from settings import CHAT_HISTORY_FILE, SAVED_CONVERSATIONS_FILE
   
logger = logging.getLogger("SASS Logger")

def save_conversation(username: str, title: str):
    saved = load_all_saved_conversations()
    if username not in saved:
        saved[username] = []
    # serialize messages
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
    saved[username].append({
        "title": title.strip(),
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": msg_list
    })
    with open(SAVED_CONVERSATIONS_FILE, "w") as f:
        json.dump(saved, f, indent=4)

def list_saved_conversations(username: str):
    saved = load_all_saved_conversations()
    if username not in saved:
        return []
    return [c["title"] for c in saved[username]]    

def load_all_saved_conversations():
    if not os.path.exists(SAVED_CONVERSATIONS_FILE):
        return {}
    try:
        with open(SAVED_CONVERSATIONS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def load_saved_conversations(username: str):
    saved = load_all_saved_conversations()
    return saved.get(username, [])

def load_saved_conversation(username: str, title: str):
    saved = load_all_saved_conversations()
    if username not in saved:
        return None
    for conversation in saved[username]:
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
