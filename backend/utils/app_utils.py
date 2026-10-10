import os
import re
import uuid
import datetime
import traceback
from typing import Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
import logging
import json
from backend.state.graph_state import GraphState
from backend.models.models import llm, lite_llm
from backend.components.constraints import MEMORY_TURN_SUMMARY_PROMPT
from backend.services.memory_search import embed_and_store_memory_chunk
from backend.services.memory_compaction import maybe_trigger_compaction
from backend.utils.db_utils import get_db, resolve_service_registry_repo
from backend.utils.embedding_utils import embed_text, cosine_similarity
from backend.utils.time_utils import SENT_AT_KEY, history_gap_marker
from fastapi import HTTPException
import subprocess
import sys

logger = logging.getLogger("SASS Logger")
DEFAULT_TARGET_REPO = os.getenv("DEFAULT_TARGET_REPO", "SummonShenron/SAAPP")
LEGACY_INGEST_SECRET = os.getenv("ERRAGENT_INGEST_SECRET", "")
# Same "is this actually relevant, semantically" role as memory_search.py's
# DEFAULT_RECALL_SIMILARITY_THRESHOLD, independently tunable since this matches a whole past
# question against a new one rather than a fact/chunk against a question.
CORRECTION_SIMILARITY_THRESHOLD = float(os.getenv("CORRECTION_SIMILARITY_THRESHOLD", "0.75"))

# def sync_run_script(script_path):
#     """Synchronous function to run the script via subprocess."""
#     # We use subprocess.run, which handles the execution and waits for completion
#     subprocess.run([sys.executable, script_path], check=True)

chat_sessions = {}

CONVERSATIONS_DIR = os.path.join("saapp_data", "conversations")


def _get_conversations_file_path(username: str) -> str:
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
    return os.path.join(CONVERSATIONS_DIR, f"{username}.json")


def _serialize_messages(messages: list) -> list:
    """Converts LangChain message objects (or already-plain dicts) into plain {type, content} dicts."""
    serialized = []
    for msg in messages:
        if isinstance(msg, dict):
            serialized.append(msg)
        elif hasattr(msg, "content"):
            msg_type = "human"
            if isinstance(msg, AIMessage) or getattr(msg, "type", "") == "ai":
                msg_type = "ai"
            elif isinstance(msg, SystemMessage) or getattr(msg, "type", "") == "system":
                msg_type = "system"
            entry = {"type": msg_type, "content": msg.content}
            additional_kwargs = getattr(msg, "additional_kwargs", {})
            attachments = additional_kwargs.get("attachments")
            if attachments:
                entry["attachments"] = attachments
            kb_images = additional_kwargs.get("kb_images")
            if kb_images:
                entry["kb_images"] = kb_images
            if additional_kwargs.get(SENT_AT_KEY):
                entry[SENT_AT_KEY] = additional_kwargs[SENT_AT_KEY]
            if additional_kwargs.get("initiated"):
                # Sonic wrote this on its own when the conversation was opened (backend/utils/initiation.py).
                entry["initiated"] = True
                if additional_kwargs.get("initiated_kind"):
                    entry["initiated_kind"] = additional_kwargs["initiated_kind"]
            serialized.append(entry)
    return serialized


def collect_kb_images(docs) -> list:
    """Extracts renderable image references from retrieved KB documents (either a whole
    document that IS an image, or images embedded within a PDF page), deduped by gridfs id —
    used to surface a 'kb_images' SSE event so the frontend can render them alongside the
    answer, reusing the same GridFS-backed rendering path built for chat attachments."""
    seen_ids = set()
    images = []
    for d in docs:
        meta = d.metadata or {}
        gridfs_id = meta.get("gridfs_id")
        if meta.get("doc_type") == "standalone_image" and gridfs_id and gridfs_id not in seen_ids:
            seen_ids.add(gridfs_id)
            images.append({"filename": meta.get("source", "image"), "fileId": gridfs_id})
        for embedded in meta.get("embedded_images") or []:
            embedded_id = embedded.get("gridfs_id")
            if embedded_id and embedded_id not in seen_ids:
                seen_ids.add(embedded_id)
                images.append({"filename": embedded.get("filename", "image"), "fileId": embedded_id})
    return images


DEFAULT_CONVERSATION_PAGE_SIZE = 100
MAX_CONVERSATION_PAGE_SIZE = 500


def load_user_conversations(username: str) -> list:
    """Loads all of a user's conversation threads from MongoDB, falling back to JSON."""
    db = get_db()
    if db is not None:
        return list(db["conversations"].find({"username": username}, {"_id": 0}))

    path = _get_conversations_file_path(username)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return []


def load_user_conversation(username: str, session_id: str) -> dict | None:
    """One thread (with its messages). Unlike load_user_conversations, Mongo only ships this one
    document instead of every thread the user has ever had."""
    db = get_db()
    if db is not None:
        return db["conversations"].find_one({"username": username, "session_id": session_id}, {"_id": 0})
    return next((c for c in load_user_conversations(username) if c.get("session_id") == session_id), None)


def list_user_conversation_summaries(username: str) -> list:
    """Title/timestamp rows for the conversations panel, newest first. Mongo's projection keeps the
    message arrays — by far the bulk of each document — out of the response entirely."""
    db = get_db()
    if db is not None:
        rows = list(db["conversations"].find(
            {"username": username}, {"_id": 0, "session_id": 1, "title": 1, "updated_at": 1}
        ))
    else:
        rows = load_user_conversations(username)
    summaries = [
        {"session_id": c["session_id"], "title": c.get("title", ""), "updated_at": c.get("updated_at", "")}
        for c in rows
    ]
    summaries.sort(key=lambda c: c["updated_at"], reverse=True)
    return summaries


def paginate_messages(messages: list, limit: int | None = None, before: int | None = None) -> dict:
    """Newest-first paging over a transcript: the `limit` messages ending just before index `before`
    (default: the end), returned in chronological order with `start`/`total` so a client can ask for
    the previous page by passing `before=start`."""
    total = len(messages)
    limit = min(max(limit or DEFAULT_CONVERSATION_PAGE_SIZE, 1), MAX_CONVERSATION_PAGE_SIZE)
    end = total if before is None else min(max(before, 0), total)
    start = max(end - limit, 0)
    return {"messages": messages[start:end], "start": start, "total": total}


def _deserialize_messages(raw_messages: list) -> list:
    messages = []
    for msg in raw_messages or []:
        m_type = msg.get("type")
        content = msg.get("content", "")
        extra = {SENT_AT_KEY: msg[SENT_AT_KEY]} if msg.get(SENT_AT_KEY) else {}
        if msg.get("initiated"):
            extra["initiated"] = True
            if msg.get("initiated_kind"):
                extra["initiated_kind"] = msg["initiated_kind"]
        if m_type == "human": messages.append(HumanMessage(content=content, additional_kwargs=extra))
        elif m_type == "ai": messages.append(AIMessage(content=content, additional_kwargs=extra))
        elif m_type == "system": messages.append(SystemMessage(content=content, additional_kwargs=extra))
    return messages


class TranscriptMessages(list):
    """A process's in-memory copy of one saved conversation, remembering how many of its leading
    messages are already stored. save_conversation_turn appends only what comes after that point,
    so a copy that has fallen behind the database (another backend instance sharing the same Mongo
    saved in the meantime) can never overwrite what the other instance wrote — the old
    replace-the-whole-transcript save did exactly that and silently deleted a morning's messages."""
    persisted: int = 0


def load_session_messages(username: str, session_id: str) -> "TranscriptMessages":
    """The saved transcript of one conversation as LangChain messages (empty if it's brand new).
    Loaded on first use per conversation instead of warm-starting every user's every thread at boot."""
    convo = load_user_conversation(username, session_id)
    transcript = TranscriptMessages(_deserialize_messages(convo.get("messages", [])) if convo else [])
    transcript.persisted = len(transcript)
    return transcript


def sync_session_messages(username: str, session_id: str, transcript: "TranscriptMessages") -> None:
    """Brings an already-loaded in-memory transcript up to date with the database before a turn, so
    messages saved by another backend instance are neither missing from the model's context nor at
    risk of being overwritten. One tiny aggregate per turn (a message count, no message data); the
    missing tail is fetched only when the counts differ. Local, unsaved messages stay at the end."""
    db = get_db()
    if db is None or not isinstance(transcript, TranscriptMessages):
        return
    match = {"username": username, "session_id": session_id}
    sizes = list(db["conversations"].aggregate([
        {"$match": match},
        {"$project": {"_id": 0, "n": {"$size": {"$ifNull": ["$messages", []]}}}},
    ]))
    remote = sizes[0]["n"] if sizes else 0
    if remote == transcript.persisted:
        return
    unsaved = list(transcript[transcript.persisted:])
    if remote > transcript.persisted:
        doc = db["conversations"].find_one(
            match, {"_id": 0, "messages": {"$slice": [transcript.persisted, remote - transcript.persisted]}}
        ) or {}
        transcript[:] = list(transcript[:transcript.persisted]) + _deserialize_messages(doc.get("messages", [])) + unsaved
        transcript.persisted = remote
        logger.info("Synced %s::%s: picked up messages saved elsewhere (now %d stored).", username, session_id, remote)
        return
    # Fewer stored than we believed: the conversation was cleared or deleted elsewhere. The database wins.
    fresh = load_session_messages(username, session_id)
    transcript[:] = list(fresh) + unsaved
    transcript.persisted = fresh.persisted


def save_user_conversations(username: str, conversations: list) -> None:
    """Mirrors a user's full conversation list to the local JSON fallback."""
    with open(_get_conversations_file_path(username), "w") as f:
        json.dump(conversations, f, indent=2)


def save_conversation_turn(username: str, session_id: str, messages: list) -> dict:
    """
    Upserts ONE conversation thread, auto-titling it from the first human message.

    With Mongo as the store and a TranscriptMessages list, only the messages not yet stored are
    appended ($push), never the whole list rewritten: a stale in-memory copy then can't delete what
    another process saved. A plain list (or one shorter than what is stored, i.e. an intentional
    clear) still replaces the whole transcript, as before."""
    serialized_messages = _serialize_messages(messages)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    db = get_db()
    # With Mongo as the store, only this thread's title/created_at are needed — not every thread's
    # full message list — and the local JSON mirror (rewritten in full on every turn) is skipped.
    # The JSON file is only the store when there is no database.
    if db is not None:
        all_conversations = None
        existing = db["conversations"].find_one(
            {"username": username, "session_id": session_id}, {"_id": 0, "title": 1, "created_at": 1}
        )
    else:
        all_conversations = load_user_conversations(username)
        existing = next((c for c in all_conversations if c.get("session_id") == session_id), None)

    title = existing.get("title") if existing else None
    if not title:
        first_human = next((m for m in serialized_messages if m.get("type") == "human"), None)
        title = (first_human["content"].strip()[:40] if first_human else "") or "New conversation"

    doc = {
        "username": username,
        "session_id": session_id,
        "title": title,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "messages": serialized_messages,
    }

    if db is not None:
        match = {"username": username, "session_id": session_id}
        append_only = isinstance(messages, TranscriptMessages) and messages.persisted <= len(serialized_messages)
        if append_only:
            delta = serialized_messages[messages.persisted:]
            update = {
                "$set": {"updated_at": now},
                "$setOnInsert": {"title": title, "created_at": doc["created_at"]},
            }
            if delta:
                update["$push"] = {"messages": {"$each": delta}}
            else:
                update["$setOnInsert"]["messages"] = []
            db["conversations"].update_one(match, update, upsert=True)
        else:
            db["conversations"].update_one(match, {"$set": doc}, upsert=True)
        if isinstance(messages, TranscriptMessages):
            messages.persisted = len(serialized_messages)
        return doc

    remaining = [c for c in all_conversations if c.get("session_id") != session_id]
    remaining.append(doc)
    save_user_conversations(username, remaining)
    if isinstance(messages, TranscriptMessages):
        messages.persisted = len(serialized_messages)
    return doc


def delete_user_conversation(username: str, session_id: str) -> bool:
    # Determine whether the conversation exists BEFORE deleting it — if we delete from Mongo
    # first, the very next load_user_conversations() call re-reads Mongo and would never see
    # the (now-gone) row, making this always report "not found" even on a successful delete.
    all_conversations = load_user_conversations(username)
    remaining = [c for c in all_conversations if c.get("session_id") != session_id]
    existed = len(remaining) != len(all_conversations)

    db = get_db()
    if db is not None:
        db["conversations"].delete_one({"username": username, "session_id": session_id})

    if not existed:
        return False

    save_user_conversations(username, remaining)
    return True


def format_history_as_text(messages) -> str:
    """Formats the LangChain history array into a clean text transcript block for the prompt."""
    formatted = []
    previous = None
    for msg in messages:
        if isinstance(msg, (HumanMessage, AIMessage)):
            # A long pause between two messages is part of what happened, and the text alone hides it.
            marker = history_gap_marker(previous, msg) if previous is not None else ""
            if marker:
                formatted.append(marker)
            previous = msg
        if isinstance(msg, HumanMessage):
            formatted.append(f"User: {msg.content}")
        elif isinstance(msg, AIMessage):
            formatted.append(f"Assistant: {msg.content}")
    return "\n".join(formatted)

def save_correction(username: str, user_prompt: str, bad_response: str, reason: str, tag: str, rating: str = "negative") -> None:
    """Writes a correction/feedback record, shared by the manual feedback endpoint and the
    reward evaluator's self-correction path. `rating` must actually be persisted for
    fetch_relevant_corrections' "positive"/"negative" split to work."""
    db = get_db()
    if db is None:
        return
    db["corrections"].insert_one({
        "id": str(uuid.uuid4()),
        "username": username,
        "user_prompt": user_prompt,
        "bad_response": bad_response[:400],
        "reason": reason,
        "tag": tag,
        "rating": rating,
        "embedding": embed_text(user_prompt),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })


def _fetch_relevant_corrections_by_substring(db, username: str, question: str) -> tuple:
    """The original matching mechanism — a literal substring match on the first 30 characters of
    the question. Kept only as a fallback for when embedding generation is unavailable (mirrors
    save_user_fact's own embeddings-unavailable fallback in memory_utils.py), since it only ever
    surfaces a past correction when a future question is worded almost identically."""
    clean_prompt = question.strip()[:30]
    if not clean_prompt:
        return [], []

    pattern = re.compile(re.escape(clean_prompt), re.IGNORECASE)

    past_negatives = list(db["corrections"].find({
        "username": username,
        "rating": {"$ne": "positive"},
        "user_prompt": pattern
    }).limit(2))

    past_positives = list(db["corrections"].find({
        "username": username,
        "rating": "positive",
        "user_prompt": pattern
    }).limit(1))

    return past_negatives, past_positives


def _fetch_relevant_corrections_by_embedding(db, username: str, question_embedding) -> tuple:
    """Semantic matching — the same embed_text + cosine_similarity pattern
    fetch_relevant_user_facts (memory_utils.py) already uses, so a differently-phrased but
    semantically identical question actually surfaces a past mistake instead of needing
    near-identical wording. Negatives and positives are ranked independently (not a single
    overall ranking then split), matching the original substring version's own independence
    between the two categories. Corrections saved before this change have no stored embedding
    and are simply skipped, the same way memory_utils._find_best_embedding_match skips facts
    with no embedding — nothing to migrate, they just age out as newer corrections accumulate."""
    all_corrections = list(db["corrections"].find({"username": username}))
    if not all_corrections:
        return [], []

    def _ranked(rating_filter) -> list:
        scored = []
        for corr in all_corrections:
            if not rating_filter(corr.get("rating")):
                continue
            embedding = corr.get("embedding")
            if not embedding:
                continue
            sim = cosine_similarity(question_embedding, embedding)
            if sim >= CORRECTION_SIMILARITY_THRESHOLD:
                scored.append((sim, corr))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [corr for _, corr in scored]

    past_negatives = _ranked(lambda r: r != "positive")[:2]
    past_positives = _ranked(lambda r: r == "positive")[:1]
    return past_negatives, past_positives


def fetch_relevant_corrections(username: str, question: str) -> str:
    db = get_db()
    if db is None or not question:
        return ""

    try:
        question_embedding = embed_text(question.strip())
        if question_embedding is not None:
            past_negatives, past_positives = _fetch_relevant_corrections_by_embedding(db, username, question_embedding)
        else:
            # Embeddings unavailable for some reason — fall back to the old substring check
            # rather than surfacing nothing at all.
            past_negatives, past_positives = _fetch_relevant_corrections_by_substring(db, username, question)

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

async def index_conversation_turn(vector_store, username: str, question: str, answer: str, session_id: Optional[str] = None):
    """Fire-and-forget: summarizes a completed turn and embeds it into the user's semantic memory.
    Meant to be called via asyncio.create_task off the response-streaming critical path;
    failures are logged, never raised, so they can never break chat streaming."""
    if vector_store is None or not answer:
        logger.debug("[MemoryIndex] Skipping turn index for %s: no vector store or empty answer.", username)
        return
    try:
        summary_resp = await lite_llm.ainvoke(
            MEMORY_TURN_SUMMARY_PROMPT.format(question=question[:500], answer=answer[:1000])
        )
        raw_content = summary_resp.content if hasattr(summary_resp, "content") else str(summary_resp)
        if isinstance(raw_content, list):
            summary_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            summary_text = str(raw_content)
        summary_text = summary_text.strip()
        if not summary_text or summary_text.upper() == "NONE":
            logger.info("[MemoryIndex] Turn for %s judged not worth remembering; no chunk stored.", username)
            return
        embed_and_store_memory_chunk(vector_store, username, summary_text, source_type="conversation_turn", source_ref=session_id)
        logger.info("[MemoryIndex] Embedded memory chunk for %s (%d chars).", username, len(summary_text))
        await maybe_trigger_compaction(get_db(), vector_store, username)
    except Exception:
        logger.exception("[MemoryIndex] Failed to index conversation turn for %s", username)

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

    # A fresh, never-reused thread_id per call — this endpoint is a stateless health check, not
    # a real conversation, so it must never persist or resurrect anything across calls. Passing
    # SOME "configurable" key is mandatory once the graph is compiled with a checkpointer
    # (LangGraph raises otherwise); a random id here guarantees no checkpoint ever accumulates
    # or gets reused for it.
    graph_config = {"configurable": {"thread_id": f"synthetic::{uuid.uuid4().hex}"}}
    final_state = await workflow.ainvoke(initial_state, config=graph_config)

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