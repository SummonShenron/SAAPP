import asyncio
import os
import re
import uuid
import datetime
import traceback
from typing import Dict, Any, Optional
from urllib import request as urllib_request, error as urllib_error
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
import logging
import json
from backend.state.graph_state import GraphState
from backend.models.models import llm
from settings import CHAT_HISTORY_FILE, SAVED_CONVERSATIONS_FILE
from backend.utils.db_utils import get_db, resolve_service_registry_repo
from fastapi import HTTPException
import subprocess
import sys

logger = logging.getLogger("SASS Logger")
DEFAULT_TARGET_REPO = os.getenv("DEFAULT_TARGET_REPO", "SummonShenron/SAAPP")
LEGACY_INGEST_SECRET = os.getenv("ERRAGENT_INGEST_SECRET", "")

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
    """Saves conversation messages to MongoDB and local JSON after serializing LangChain objects."""
    
    # 1. Convert LangChain message objects into plain dictionaries
    serialized_messages = []
    for msg in messages:
        if hasattr(msg, "content"):
            msg_type = "human"
            if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
                msg_type = "ai"
            elif isinstance(msg, SystemMessage) or getattr(msg, "type", "") == "system":
                msg_type = "system"
                
            serialized_messages.append({
                "type": msg_type,
                "content": msg.content
            })
        elif isinstance(msg, dict):
            serialized_messages.append(msg)

    new_entry = {
        "title": title.strip(),
        "timestamp": datetime.datetime.now().isoformat(),
        "messages": serialized_messages  # Now populated with real serialized data!
    }
    
    # 2. Update MongoDB
    db = get_db()
    if db is not None:
        db['saved_conversations'].update_one(
            {"username": username},
            {"$push": {"conversations": new_entry}},
            upsert=True
        )
        logger.info(f"Saved conversation '{title}' for {username} to MongoDB with {len(serialized_messages)} messages.")
    
    # 3. Update Local JSON safety net
    user_conversations = load_saved_conversations(username) 
    user_conversations.append(new_entry) 
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

def fetch_relevant_corrections(username: str, question: str) -> str:
    db = get_db()
    if db is None or not question:
        return ""

    try:
        # 1. Take a clean slice of the user prompt
        clean_prompt = question.strip()[:30]
        if not clean_prompt:
            return ""

        # 2. Use re.compile so PyMongo handles BSON regex encoding natively
        pattern = re.compile(re.escape(clean_prompt), re.IGNORECASE)

        # 3. Fetch BOTH from the single 'corrections' collection
        past_negatives = list(db["corrections"].find({
            "username": username,
            "rating": {"$ne": "positive"},
            "user_prompt": pattern
        }).limit(2))

        past_positives = list(db["corrections"].find({
            "username": username,
            "rating": "positive",
            "user_prompt": pattern
        }).limit(1)) # Limit 1 to avoid bloating the prompt

        # If nothing is found, return empty string
        if not past_negatives and not past_positives:
            return ""

        context_string = ""

        # 4. Inject Negative Guardrails
        if past_negatives:
            context_string += "\n\nCRITICAL GUARDRAILS (Avoid past errors for this prompt):\n"
            for idx, corr in enumerate(past_negatives, 1):
                tag = corr.get("tag", "general")
                reason = corr.get("reason", "Inaccurate output")
                bad_response = corr.get("bad_response", "")[:150]
                context_string += f"- Rule {idx} [{tag}]: Do NOT generate responses like: '{bad_response}'. Reason: {reason}\n"

        # 5. Inject Positive Examples (Golden Q&A)
        if past_positives:
            context_string += "\n\nPREFERRED EXAMPLES (Replicate this style/content):\n"
            for idx, corr in enumerate(past_positives, 1):
                # Note: The good text is stored under the 'bad_response' key based on the frontend payload
                good_response = corr.get("bad_response", "")[:250] 
                context_string += f"- Example {idx}: Aim for a response like: '{good_response}'\n"

        return context_string

    except Exception as e:
        # Gracefully log and fallback so a DB lookup error NEVER breaks chat streaming
        logger.warning(f"[GUARDRAIL WARNING] Could not fetch corrections: {e}")
        return ""

def extract_target_repo(payload: dict) -> str | None:
    repo_value = payload.get("repository") or payload.get("repo") or payload.get("target_repo") or payload.get("tag")
    if isinstance(repo_value, dict):
        return repo_value.get("full_name") or repo_value.get("name") or repo_value.get("repo")
    if repo_value:
        return str(repo_value).strip() or None
    return None


async def resolve_app_ingest_repo(db, payload: dict, app_id: str | None, app_default_repo: str | None) -> str:
    explicit_repo = extract_target_repo(payload)
    if explicit_repo:
        return explicit_repo

    service_name = (payload.get("service_name") or payload.get("service") or payload.get("source_service") or "").strip()
    if service_name:
        if app_id:
            scoped = await db["service_registry"].find_one({"service_name": service_name, "app_id": app_id})
            if scoped and scoped.get("repo"):
                return scoped["repo"]

        global_entry = await db["service_registry"].find_one({"service_name": service_name})
        if global_entry and global_entry.get("repo"):
            return global_entry["repo"]

    if app_default_repo:
        return app_default_repo

    return DEFAULT_TARGET_REPO


async def validate_app_ingest_identity(db, app_id: str | None, ingest_secret: str) -> dict:
    if app_id:
        client = await db["ingest_clients"].find_one({"app_id": app_id, "enabled": True})
        if not client:
            raise HTTPException(status_code=401, detail="Unknown or disabled app client.")
        if client.get("secret") != ingest_secret:
            raise HTTPException(status_code=401, detail="Invalid app secret.")
        return client

    if not LEGACY_INGEST_SECRET:
        raise HTTPException(status_code=401, detail="Legacy ingest secret is not configured.")

    if ingest_secret != LEGACY_INGEST_SECRET:
        raise HTTPException(status_code=401, detail="Invalid ingest secret.")

    return {}

DEFAULT_TARGET_REPO_FALLBACK = "summonshenron/SAAPP"
DEFAULT_ERRAGENT_INGEST_URL = "https://erragent.onrender.com/api/v1/webhooks/ingest"


def build_erragent_ingest_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized_payload = dict(payload)
    repository = normalized_payload.get("repository")
    if isinstance(repository, str) and repository.strip():
        normalized_payload["repository"] = repository.strip()
        return normalized_payload

    configured_repository = os.getenv("ERRAGENT_TARGET_REPO", "").strip()
    if configured_repository:
        normalized_payload["repository"] = configured_repository
        return normalized_payload

    default_repository = os.getenv("DEFAULT_TARGET_REPO", DEFAULT_TARGET_REPO_FALLBACK).strip()
    if default_repository:
        normalized_payload["repository"] = default_repository

    return normalized_payload


def pick_repo_from_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(metadata, dict):
        return None

    for key in ("repository", "repo", "target_repo"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    tags = metadata.get("tags")
    if isinstance(tags, dict):
        for key in ("repository", "repo", "target_repo"):
            value = tags.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if isinstance(tags, list):
        for item in tags:
            if isinstance(item, str) and "/" in item:
                return item.strip()

    extra = metadata.get("extra")
    if isinstance(extra, dict):
        for key in ("repository", "repo", "target_repo"):
            value = extra.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def resolve_target_repo(service_name: str, payload_repo: Optional[str], metadata: Optional[Dict[str, Any]]) -> tuple[str, str]:
    if isinstance(payload_repo, str) and payload_repo.strip():
        return payload_repo.strip(), "payload"

    metadata_repo = pick_repo_from_metadata(metadata)
    if metadata_repo:
        return metadata_repo, "metadata"

    default_repo = os.getenv("DEFAULT_TARGET_REPO", DEFAULT_TARGET_REPO_FALLBACK).strip() or DEFAULT_TARGET_REPO_FALLBACK
    return default_repo, "default"


# --- HTTP DISPATCH ---
def post_erragent_ingest(payload: Dict[str, Any]) -> Dict[str, Any]:
    ingest_url = os.getenv("ERRAGENT_INGEST_URL", DEFAULT_ERRAGENT_INGEST_URL).strip()
    ingest_secret = os.getenv("ERRAGENT_INGEST_SECRET")

    if not ingest_secret:
        raise RuntimeError("ERRAGENT_INGEST_SECRET is not configured")

    normalized_payload = build_erragent_ingest_payload(payload)
    body = json.dumps(normalized_payload).encode("utf-8")
    req = urllib_request.Request(
        ingest_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Secret": ingest_secret,
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=60) as response:
            return {
                "status_code": response.getcode(),
                "body": response.read().decode("utf-8"),
            }
    except urllib_error.HTTPError as exc:
        return {
            "status_code": exc.code,
            "body": exc.read().decode("utf-8", errors="replace"),
        }


async def send_erragent_ingest(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(post_erragent_ingest, payload)


# --- NON-BLOCKING BACKGROUND DISPATCH & PAYLOAD BUILDER ---
async def safe_send_erragent_ingest(payload: Dict[str, Any]) -> None:
    """Executes send_erragent_ingest safely in the background."""
    try:
        result = await send_erragent_ingest(payload)
        logger.info(
            "--> [errAgent] Background dispatch status=%s body=%s",
            result.get("status_code"),
            result.get("body"),
        )
    except Exception as exc:
        logger.error("--> [errAgent] Background dispatch failed: %s", str(exc))


_background_tasks = set()

def dispatch_erragent_ingest(payload: Dict[str, Any]) -> None:
    """Fire-and-forget task scheduled on the running async event loop."""
    task = asyncio.create_task(safe_send_erragent_ingest(payload))
    
    # Store strong reference
    _background_tasks.add(task)
    
    # Remove from set once completed
    task.add_done_callback(_background_tasks.discard)


def build_error_payload(
    exc: Exception,
    service_default: str = "saapp",
    source: str = "unknown",
    method: str = "N/A",
) -> Dict[str, Any]:
    """Helper to consistently format exception payloads for errAgent."""
    stack_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return {
        "service_name": os.getenv("ERRAGENT_SERVICE_NAME", service_default),
        "error_message": str(exc),
        "stack_trace": stack_trace,
        "environment": os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "production")),
        "metadata": {
            "source": source,
            "method": method,
            "exception_type": exc.__class__.__name__,
        },
    }

async def run_synthetic_read_only_question(
    workflow,
    question: str,
    username: str,
) -> str:
    question = question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="Question is too long.")
    if workflow is None:
        raise HTTPException(status_code=503, detail="Synthetic workflow unavailable.")

    initial_state = {
        "messages": [HumanMessage(content=question)],
        "username": username,
        "target_scope": [],
        "documents": [],
        "relevance_grade": "conversational",
        "loop_count": 0,
        "original_question": question,
        "force_web_search": False,
        "workflowName": "synthetic_read_only",
        "requestId": uuid.uuid4().hex,
    }

    final_state = await workflow.ainvoke(initial_state)

    logger.info(
        "Synthetic workflow final_state keys: %s",
        sorted(final_state.keys()) if isinstance(final_state, dict) else type(final_state).__name__,
    )

    answer = (
        final_state.get("insight_answer")
        or final_state.get("generation")
        or final_state.get("content_to_format")
    )

    if not answer and isinstance(final_state, dict):
        messages = final_state.get("messages") or []
        if messages:
            last_message = messages[-1]
            if hasattr(last_message, "content"):
                embedded_content = getattr(last_message, "content")
                if isinstance(embedded_content, str) and embedded_content.strip():
                    answer = embedded_content.strip()
                elif isinstance(embedded_content, list):
                    answer = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in embedded_content
                    ).strip()
            elif isinstance(last_message, dict):
                content = last_message.get("content")
                if isinstance(content, str) and content.strip():
                    answer = content.strip()
                elif isinstance(content, list):
                    answer = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    ).strip()

    if not answer:
        raise HTTPException(
            status_code=502,
            detail="Synthetic workflow returned no answer.",
        )

    return str(answer)