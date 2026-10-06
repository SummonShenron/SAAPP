import asyncio
import os
import datetime
import json
import sys
import base64
import subprocess
import traceback
import time
import urllib.parse
import re
from gridfs import GridFSBucket
from bson import ObjectId, errors
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query, Form, Request, Depends, BackgroundTasks, status, Response
from typing import List, Dict, Any, Optional
import uuid
import traceback
import erragent
from gridfs import GridFS
from bson.objectid import ObjectId
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, RedirectResponse
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from backend.models.models import llm, get_stream_llm
from backend.models.attachment import Attachment
from backend.services.github_service import (
    process_pr_summary, verify_and_store_github_token, GitHubTokenRejected, GitHubUnreachable,
)
from backend.utils.secret_utils import SecretStorageNotConfigured
from backend.utils.webhook_utils import verify_github_signature
from backend.utils.github_audit import audit_github_token_use
# Modernized LangChain Imports
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_community.vectorstores import Chroma
from backend.components.constraints import (
    format_docs,
    build_voice_prompt,
    GROUNDING_BLOCKS,
    KB_STRICT_GROUNDING,
    get_affiliate_override,
    IMAGE_RENDERING_NOTE_TEMPLATE,
)
from backend.services.search import discover_workspace_documents
from local_function_app.function_app import run_ingestion_pipeline, HOT_FOLDER_DIR
from backend.state.graph_state import GraphState
from backend.services import steering
from backend.utils.app_utils import (
    save_conversation_turn,
    load_user_conversations,
    load_user_conversation,
    list_user_conversation_summaries,
    paginate_messages,
    load_session_messages,
    sync_session_messages,
    DEFAULT_CONVERSATION_PAGE_SIZE,
    MAX_CONVERSATION_PAGE_SIZE,
    delete_user_conversation,
    format_history_as_text,
    chat_sessions,
    fetch_relevant_corrections,
    save_correction,
    extract_target_repo,
    resolve_app_ingest_repo,
    validate_app_ingest_identity,
    pick_repo_from_metadata,
    resolve_target_repo,
    run_synthetic_read_only_question,
    index_conversation_turn,
    collect_kb_images
)
from backend.utils.attachment_utils import (
    process_user_attachment,
    ingest_doc_to_session,
    is_image_attachment,
    store_image_in_gridfs,
    guess_image_mime_type,
)
from backend.utils.memory_utils import (
    fetch_relevant_user_facts,
    fetch_goal_nudge_context,
    load_user_facts,
    delete_user_fact,
    delete_all_user_facts,
    effective_confidence,
)
from backend.services.memory_compaction import compact_user_memory, compact_meta_memory
from backend.services.memory_search import retrieve_relevant_memory_context
from backend.services.local_workspace import (
    apply_sync as apply_local_workspace_sync,
    clear_workspace as clear_local_workspace,
    is_guest_username,
    workspace_status as local_workspace_status,
)
from backend.utils.agent_utils import is_stale_capability_denial, scrub_stale_capability_denials
from backend.utils.embedding_utils import embed_text
from backend.utils.emotion_utils import build_emotional_context
from backend.utils.emotion_checks import emotional_reply_issue, build_emotional_revision_prompt
from backend.utils.user_settings_utils import (
    get_user_rag_mode, set_user_rag_mode, VALID_RAG_MODES,
    get_user_deep_thinking_mode, set_user_deep_thinking_mode,
    get_user_target_repo, set_user_target_repo,
    get_user_settings_bundle,
    get_user_has_seen_help, set_user_has_seen_help,
    get_user_timezone, set_user_timezone,
    get_user_target_doc_id, set_user_target_doc_id,
    get_github_token_status, find_github_token_for_repo,
    GitHubTokenInvalid, GitHubTokenNotAllowed,
)
from backend.services.google_drive_service import get_file_metadata as get_drive_file_metadata
from backend.utils.google_calendar_utils import (
    get_connection_status as get_calendar_connection_status,
    create_pending as create_calendar_pending,
    get_pending as get_calendar_pending,
    consume_pending as consume_calendar_pending,
    delete_connection as delete_calendar_connection,
    save_connection as save_calendar_connection,
    ensure_indexes as ensure_calendar_indexes,
    CALENDAR_LOCKED_USERS,
)
from backend.services.google_calendar_oauth import GoogleCalendarOAuth
from backend.utils.fallback_utils import rewrite_fallback
from backend.services.reward_evaluator import evaluate_response, build_correction_prompt, REWARD_EVAL_SOURCE_TYPES
from backend.logging.sass_logger import setup_logging
from backend.services.orchestrator import startup_services
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access, load_directory, make_personal_kb_id, new_personal_kb, personal_kb_groups, personal_kb_update, resolve_kb_display_names
from backend.utils.db_utils import get_db, save_error_event, test_connection
from backend.auth.isolation_auth import get_current_user, record_login_event
from backend.services.checkpoint_retention import run_checkpoint_retention_loop, prune_thread_checkpoints_async
from contextlib import asynccontextmanager
from settings import DB_DIR
import aiohttp
import aiohttp.resolver
import settings

DEFAULT_TARGET_REPO = os.getenv("DEFAULT_TARGET_REPO", "SummonShenron/SAAPP")
LEGACY_INGEST_SECRET = os.getenv("ERRAGENT_INGEST_SECRET", "")

def is_local_dev():
    return os.getenv("LOCAL_DEV", "false").lower() == "true"
aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
sys.path.append(os.path.join(os.path.dirname(__file__), "local_function_app"))

if sys.platform == "win32":
    # ProactorEventLoop's pipe transport teardown raises ConnectionResetError when the remote
    # side already reset the connection (WinError 10054) — a harmless artifact of closing an
    # already-dead socket, not a real failure, but it surfaces as an unhandled asyncio exception
    # and gets reported as one (e.g. by errAgent's asyncio exception hook). Swallow it at the
    # source instead of letting every exception-reporting layer downstream treat it as real.
    from asyncio.proactor_events import _ProactorBasePipeTransport

    _original_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

    def _call_connection_lost_quietly(self, exc):
        try:
            _original_call_connection_lost(self, exc)
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    _ProactorBasePipeTransport._call_connection_lost = _call_connection_lost_quietly

# 2. Define the startup/shutdown logic
@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_sessions
    # This runs once when the server starts. Chat transcripts are NOT warm-loaded here: each
    # conversation is read from the store the first time it's used (see secure_chat).
    retention_task = spawn_background_task(run_checkpoint_retention_loop())
    if get_db() is not None:
        ensure_calendar_indexes()
    yield
    # Cleanup tasks would go here
    retention_task.cancel()
    chat_sessions = {}
# 3. Pass the lifespan to the app
app = FastAPI(title="Secure RAG Engine API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://(saapp|saapp-[\w-]+)\.vercel\.app",
    allow_origins=[
        "http://127.0.0.1:8080", 
        "http://localhost:8080",
        "http://localhost:5173",
        "https://sonicassistant.com",
        "https://www.sonicassistant.com/"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_CALENDAR_RETURN_TO_ALLOWED_HOSTS = {
    "127.0.0.1", "localhost", "sonicassistant.com", "www.sonicassistant.com",
}
_CALENDAR_RETURN_TO_HOST_RE = re.compile(r"^(saapp|saapp-[\w-]+)\.vercel\.app$")


def _is_allowed_calendar_return_to(url: str) -> bool:
    """Mirrors the CORSMiddleware config above so this allow-list can't silently drift from it —
    a return_to outside these hosts would let /api/calendar/connect/start be used as an open
    redirect after a real Google OAuth consent screen, so this is checked before the OAuth flow
    ever starts."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    return parsed.hostname in _CALENDAR_RETURN_TO_ALLOWED_HOSTS or bool(_CALENDAR_RETURN_TO_HOST_RE.match(parsed.hostname))


logger = setup_logging()  # Initialize the logger from backend/logging/sass_logger.py
erragent.install(logger)
logger.info("--- BOOTING SECURE KNOWLEDGE ASSISTANT ---")
services = startup_services()
chat_sessions = {}

# Holds strong references to fire-and-forget background tasks (e.g. index_conversation_turn).
# asyncio only weakly references tasks internally; an unreferenced task can be garbage-collected
# mid-execution with no error, so every asyncio.create_task(...) call must be added here.
_background_tasks: set = set()


def spawn_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


class LoginRequest(BaseModel):
    username: str

class ChatRequest(BaseModel):
    question: str
    affiliate: str 
    attachments: list[Attachment] | None = None
    session_id: str | None = None

class SteerRequest(BaseModel):
    session_id: str
    message: str

class LocalWorkspaceFile(BaseModel):
    path: str
    content: str

class LocalWorkspaceSync(BaseModel):
    name: str
    reset: bool = False
    files: list[LocalWorkspaceFile] = []
    deleted: list[str] = []

class RagModeUpdate(BaseModel):
    rag_mode: str

class DeepThinkingUpdate(BaseModel):
    deep_thinking: bool

class TargetRepoUpdate(BaseModel):
    target_repo: Optional[str] = None

class TargetDocUpdate(BaseModel):
    doc_url_or_id: Optional[str] = None

class GitHubTokenUpdate(BaseModel):
    token: Optional[str] = None

class HasSeenHelpUpdate(BaseModel):
    has_seen_help: bool

class TimezoneUpdate(BaseModel):
    timezone: str

class SaveConversationRequest(BaseModel):
    title: str
    messages: List[Dict[str, Any]] 

class FeedbackPayload(BaseModel):
    user_prompt: str
    bad_response: str
    reason: str
    tag: str  # e.g., "hallucination", "incorrect_filter", "formatting"
    rating: Optional[str] = "negative" # "positive" or "negative"

class IngestPayload(BaseModel):
    service_name: str
    error_message: str
    stack_trace: str
    environment: Optional[str] = None
    repository: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class StatusUpdate(BaseModel):
    status: str


class SyntheticAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)

@app.middleware("http")
async def capture_unhandled_errors(request: Request, call_next) -> Response:
    started_at = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled request failure",
            extra={
                "erragent_context": {
                    "method": request.method,
                    "path": request.url.path,
                    "environment": os.getenv("ENVIRONMENT", "production"),
                }
            },
        )
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000)

    if response.status_code >= 500:
        logger.error(
            "Request returned server error",
            extra={
                "erragent_context": {
                    "method": request.method,
                    "path": request.url.path,
                    "statusCode": response.status_code,
                    "durationMs": duration_ms,
                    "environment": os.getenv("ENVIRONMENT", "production"),
                }
            },
        )

    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Re-raise standard FastAPI HTTP exceptions so they return their intended status code (e.g. 401, 404)
    if isinstance(exc, HTTPException):
        raise exc

    # Reporting already happened in capture_unhandled_errors's `except` block via
    # logger.exception(...), which — unlike a plain logger.error(...) here — carries a real
    # traceback (exc_info=True), so both the cloud and local-dev pipelines can actually resolve
    # which file the bug is in. Reporting it again here duplicated every unhandled exception into
    # 2-3 incidents with different message text (so errAgent's fingerprint-based dedup couldn't
    # recognize them as the same event), one of which had no real traceback and produced a
    # useless remediation guess against the wrong file.

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )

@app.get("/api/me")
def get_me(request: Request, current_user: dict = Depends(get_current_user)):
    clerk_id = current_user.get("sub")
    email = current_user.get("email") or (request.headers.get("x-principal") or request.headers.get("X-Principal") or request.headers.get("x-user-id") or "").strip()

    if email and "@" not in email:
        email = None
    
    db = get_db()
    
    if db is not None:
        users_col = db["directory"] 
        
        user_doc = None
        if clerk_id:
            user_doc = users_col.find_one({"clerk_id": clerk_id})
        if not user_doc and email:
            user_doc = users_col.find_one({"email": email})
            if user_doc:
                logger.info(f"Lazy migrating user record for: {email}")
                users_col.update_one(
                    {"_id": user_doc["_id"]},
                    {"$set": {"clerk_id": clerk_id}}
                )
                user_doc["clerk_id"] = clerk_id
        
        if not user_doc:
            if not email:
                logger.warning("No email claim was available for this user; preserving the Clerk subject as the username instead of creating a synthetic new_user identity.")
                safe_username = clerk_id or "new_user"
                safe_email = None
            else:
                safe_username = email.split("@")[0]
                safe_email = email

            logger.info(f"[+] Provisioning user record for: {safe_email or safe_username}")
            personal_kb = new_personal_kb(clerk_id, safe_username)
            new_user = {
                "clerk_id": clerk_id,
                "email": safe_email,
                "username": safe_username,
                "groups": ["Affiliate_A", "Affiliate_B", "Affiliate_C", *personal_kb_groups(personal_kb["id"])],
                "personal_kb": personal_kb,
                "created_at": datetime.utcnow()
            }
            users_col.insert_one(new_user)
            user_doc = new_user
        else:
            # An account from before personal KBs existed: it gets the same one a new user would, the
            # first time it shows up (personal_kb_update decides whether that applies to this account).
            provisioning = personal_kb_update(user_doc)
            if provisioning:
                logger.info(f"[+] Provisioning a personal knowledge base for existing user: {user_doc.get('email') or user_doc.get('username')}")
                users_col.update_one(provisioning["filter"], provisioning["update"])
                user_doc = users_col.find_one({"_id": user_doc["_id"]}) or user_doc
            
        return {
            "username": user_doc.get("username") or (email.split("@")[0] if email else clerk_id),
            "email": user_doc.get("email") or email,
            "groups": user_doc.get("groups", [])
        }
        
    # --- FALLBACK LOCAL JSON FLOW ---
    else:
        logger.warning("Database disabled. Falling back to local directory.")
        directory = load_directory()
        
        # Attempt to map them based on email, or fallback to the clerk_id 
        # (This will fail for new users unless manually added to your JSON)
        directory_key = email if email in directory else clerk_id
        entry = directory.get(directory_key)
        
        if not entry:
            raise HTTPException(status_code=403, detail="User not found in local directory.")
            
        return {
            "username": directory_key,
            "email": entry.get("email"),
            "groups": entry.get("groups", [])
        }
@app.post("/api/login")
async def verify_identity_profile(payload: LoginRequest):
    # Just check if the user exists in your MongoDB "users" collection
    db = get_db()
    user_exists = db["users"].find_one({"clerk_id": payload.username})
    
    if not user_exists:
        # If they aren't in the DB, create them or handle registration
        return {"status": "needs_registration"}
        
    return {"status": "authenticated", "principal": payload.username}

@app.post("/api/log-login")
async def log_user_login(request: Request, current_user: dict = Depends(get_current_user)):
    client_ip = request.client.host if request.client else "unknown"
    
    sub = current_user.get("sub", "")
    email = current_user.get("email") or sub
    
    # Detect if the current principal is a guest session
    is_guest = sub in ("guest-recruiter@example.com", "guest_bty") or request.headers.get("Authorization", "") in ("Bearer guest-sandbox-token", "Bearer guest-bty-token")

    record_login_event(
        user_id=sub,
        email=email,
        is_guest=is_guest,
        ip_address=client_ip
    )
    return {"status": "success"}

@app.get("/api/affiliates")
async def get_affiliates(current_user = Depends(get_current_user)):
    clerk_id = current_user.get("sub")
    directory = load_directory()

    # DEBUG: See if we can find the user with the new ID
    user_data = directory.get(clerk_id)
    logger.debug(f"Lookup result for {clerk_id}: {user_data}")

    accessible = get_accessible_affiliates(clerk_id, directory)["accessible_affiliates"]
    display_names = resolve_kb_display_names(directory)
    return {
        "accessible_affiliates": [
            {"id": aff, "display_name": display_names.get(aff, aff.replace("_", " "))}
            for aff in accessible
        ]
    }


@app.get("/api/user/groups")
def get_user_groups(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    
    directory = load_directory()
    entry = directory.get(username)
    groups = entry.get("groups", []) if entry else []
    
    logger.info("Fetching groups for: %s -> %s", username, groups)
    return groups


@app.get("/api/discover-docs")
async def discover_documents(affiliate: str = "All", current_user = Depends(get_current_user)):
    """
    Simulates an Azure AI Search broad discovery sweep. 
    It requests all unique filenames within the user's active security clearance scope.
    """
    try:
        # Calls the dynamic metadata extraction layer inside search.py
        files = await discover_workspace_documents(affiliate)
        return {"accessible_documents": files}
    except Exception as e:
        logger.exception(f"[-] Catalog discovery anomaly: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def secure_chat(request: ChatRequest, http_request: Request, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    response_llm = get_stream_llm(username)
    question = request.question.strip()
    session_id = request.session_id.strip() if request.session_id else f"{username}_session"
    history_key = f"{username}::{session_id}"
    # history_key already uniquely identifies one conversation — reused directly as the
    # checkpointer's thread_id, no separate ID scheme needed. Defined once, up front, so both
    # graph invocation sites below use the exact same config regardless of which branch runs.
    # Passing this is required (not just useful) once the graph is compiled with a checkpointer:
    # LangGraph raises if a checkpointer is attached and config has no "configurable" key at
    # all — harmless to always pass it even when checkpointer setup failed and
    # compiled_workflow has none attached.
    graph_config = {"configurable": {"thread_id": history_key}}
    t_auth_start = time.perf_counter()
    
    # ---------- Auth Authorization Boundary ----------
    web_triggers = ["search the web", "search online", "search google", "web search", "look up online"]
    force_web_search = any(kw in question.lower() for kw in web_triggers)
    requested_affiliate = request.affiliate.strip()
    directory = load_directory()
    user_claims = directory.get(username, {})
    
    logger.info("--- BEGINNING CHAT STREAM ---")
    
    if username not in directory:
        raise HTTPException(status_code=401, detail="Unauthorized: User not found.")
    
    accessible_affiliates = get_accessible_affiliates(username, directory)
    
    if requested_affiliate != "All" and requested_affiliate not in accessible_affiliates["accessible_affiliates"]:
        raise HTTPException(status_code=403, detail="Security Breach: Unauthorized affiliate scope requested.")

    target_scope = accessible_affiliates["accessible_affiliates"] if requested_affiliate == "All" else [requested_affiliate]
    # One DB round trip for all three settings instead of three (they live on the same
    # per-user document) — locked identities like guest_bty get the safe defaults from here too.
    _settings = get_user_settings_bundle(username)
    effective_rag_mode = _settings["rag_mode"]
    effective_deep_thinking = _settings["deep_thinking"]
    effective_target_repo = _settings["target_repo"]

    # ---------- Conversation Memory State Init ----------
    # The full transcript in chat_sessions[history_key] is kept durable (it's what gets
    # persisted per-conversation); only a bounded recent window is fed to the LLM/graph.
    if history_key not in chat_sessions:
        chat_sessions[history_key] = load_session_messages(username, session_id)
    else:
        # Another backend instance sharing this database may have saved since we last looked.
        sync_session_messages(username, session_id, chat_sessions[history_key])
    chat_sessions[history_key].append(HumanMessage(content=question))

    messages_state = chat_sessions[history_key][-10:]
    # With a local folder connected, earlier "I can't write files" replies are false — and a thread
    # full of them teaches the model to keep saying it. Only what the model sees is changed; the
    # saved transcript is untouched.
    folder_connected = bool(local_workspace_status(username).get("connected"))
    if folder_connected:
        messages_state = scrub_stale_capability_denials(messages_state)

    request_id = uuid.uuid4().hex
    initial_state: GraphState = {
        "messages": messages_state,
        "username": username,
        "session_id": session_id,
        "target_scope": target_scope,
        "rag_mode": effective_rag_mode,
        "deep_thinking": effective_deep_thinking,
        "repo": effective_target_repo,
        "documents": [],
        "relevance_grade": "web_search" if force_web_search else "",
        "loop_count": 0,
        "original_question": question,
        "force_web_search": force_web_search,
        "workflowName": "sonic_assistant",
        "requestId": request_id,
    }

    # ---------- Early Attachments Processing ----------
    attachment_summaries = []
    attachment_refs = []
    if request.attachments:
        logger.info(f"Processing {len(request.attachments)} attachments for {username}")
        db = get_db()
        for att in request.attachments:
            ingest_doc_to_session(username, session_id, att)
            summary = process_user_attachment(att)
            if summary:
                attachment_summaries.append(summary)
            if is_image_attachment(att.filename):
                gridfs_id = store_image_in_gridfs(db, username, session_id, att)
                if gridfs_id:
                    attachment_refs.append({
                        "filename": att.filename,
                        "gridfs_id": gridfs_id,
                        "content_type": guess_image_mime_type(att.filename),
                    })

    if attachment_refs:
        chat_sessions[history_key][-1].additional_kwargs["attachments"] = attachment_refs

    initial_state["attachment_summaries"] = attachment_summaries
    if attachment_summaries:
        initial_state["documents"] = [Document(
            page_content=s, metadata={"source": "user_attachment_summary", "priority": True, "page": "N/A"}
        ) for s in attachment_summaries]

    # =====================================================================
    # THE MEGA-STREAMER (Graph Execution + Prompting + LLM Generation)
    # =====================================================================
    async def token_streamer():
        full_response = ""
        first_token = True
        t_stream_start = time.perf_counter()
        t_graph_end = None
        t_first_token = None
        final_state = {}

        def log_timings(grade: str, outcome: str):
            now = time.perf_counter()
            logger.info(
                "[TIMING] user=%s affiliate=%s grade=%s outcome=%s docs=%d "
                "preflight=%.2fs graph=%.2fs ttft=%.2fs total=%.2fs",
                username,
                requested_affiliate,
                grade,
                outcome,
                len(final_state.get("documents", []) or []),
                t_stream_start - t_auth_start,
                (t_graph_end - t_stream_start) if t_graph_end else -1,
                (t_first_token - t_auth_start) if t_first_token else -1,
                now - t_auth_start,
            )

        try:
            if settings.LOCAL_DEV:
                logger.info("--- [LOCAL DEV MODE] Bypassing Graph Workflow ---")
                final_state = {
                    "insight_answer": "Local dev mode active: Graph API bypassed.",
                    "relevance_grade": "conversational",
                    "target_scope": [request.affiliate],
                    "documents": [],
                    "messages": initial_state["messages"],
                    "original_question": question,
                    "workflowName": initial_state["workflowName"],
                    "requestId": initial_state["requestId"],
                }
            else:
                # 1. LIVE GRAPH EXECUTION & EVENT STREAMING
                logger.info("--- STARTING LIVE GRAPH EXECUTION ---")
                workflow = services.get("compiled_workflow")

                event_stream = workflow.astream_events(initial_state, version="v2", config=graph_config)
                async for event in event_stream:
                    # A user-initiated Stop (see docs/coding-agent-roadmap.md) aborts the
                    # client's fetch — without this check the graph (including slow admin
                    # tools like run_repo_tests/run_snippet) would keep running to completion
                    # server-side anyway, burning real LLM/tool cost for a response nobody is
                    # waiting for. aclose() sends the generator a real cancellation signal
                    # rather than just abandoning it half-consumed.
                    if await http_request.is_disconnected():
                        logger.info(f"[secure_chat] Client disconnected mid-investigation for {history_key}; stopping graph execution.")
                        await event_stream.aclose()
                        chat_sessions[history_key].append(AIMessage(content="_[Stopped by user before responding]_"))
                        save_conversation_turn(username, session_id, chat_sessions[history_key])
                        return
                    kind = event["event"]

                    # Catch Custom Thoughts emitted by your nodes via adispatch_custom_event
                    if kind == "on_custom_event" and event.get("name") == "trace_detail":
                        data = event.get("data", {})
                        node_progress_payload = {
                            "event": "node_progress",
                            "node": data.get("node", "system"),
                            "title": data.get("title", "Processing..."),
                            "detail": data.get("detail", ""),
                        }
                        # Only present when tool_agent_node's batching (run_react_loop's
                        # "queries" support) actually ran this action concurrently alongside
                        # others in the same step — lets the frontend trace panel show that
                        # directly instead of the only way to confirm it being to read the
                        # backend log for repeated step numbers.
                        if data.get("batch_size"):
                            node_progress_payload["batch_index"] = data.get("batch_index")
                            node_progress_payload["batch_size"] = data.get("batch_size")
                        yield f"data: {json.dumps(node_progress_payload)}\n\n"
                        await asyncio.sleep(0.01)

                    # The tool agent folded one or more mid-run steering messages into its work
                    # (backend/services/steering.py) — lets the browser mark them as taken into account.
                    if kind == "on_custom_event" and event.get("name") == "steer_applied":
                        data = event.get("data", {})
                        yield f"data: {json.dumps({'event': 'steer_applied', 'count': data.get('count', 0), 'step': data.get('step')})}\n\n"
                        await asyncio.sleep(0.01)

                    # The reasoner just read this message's emotional state: tell the browser which energy
                    # ceiling is in force so the mascot's mood follows it (backend/utils/emotion_utils.py).
                    if kind == "on_custom_event" and event.get("name") == "emotion":
                        data = event.get("data", {})
                        yield f"data: {json.dumps({'event': 'emotion', 'tier': data.get('tier', 'open')})}\n\n"
                        await asyncio.sleep(0.01)

                    # A view-only browserless.io LiveURL, emitted once tool_agent_node's browser_*
                    # actions actually open a session — lets the frontend embed a real-time watch
                    # link in the in-progress chat bubble (see backend/services/browser_tool.py).
                    if kind == "on_custom_event" and event.get("name") == "browser_live_view":
                        data = event.get("data", {})
                        yield f"data: {json.dumps({'event': 'browser_live_view', 'url': data.get('url', '')})}\n\n"
                        await asyncio.sleep(0.01)

                    # A validated set of edits Sonic proposes to the user's connected local folder
                    # (backend/services/local_edits.py). The browser renders it as a diff card and
                    # applies it only when the user presses Apply.
                    if kind == "on_custom_event" and event.get("name") == "local_edit_proposal":
                        yield f"data: {json.dumps({'event': 'local_edit_proposal', **event.get('data', {})})}\n\n"
                        await asyncio.sleep(0.01)

                    # Catch Final State when the graph finishes (Look for the final dictionary output)
                    if kind == "on_chain_end":
                        output = event.get("data", {}).get("output")
                        # Ensure we grab the actual state dict and not a sub-node return
                        if output and isinstance(output, dict) and "relevance_grade" in output:
                            final_state = output

            # Fallback just in case event streaming missed the final state dict
            if not final_state:
                final_state = await workflow.ainvoke(initial_state, config=graph_config)

            t_graph_end = time.perf_counter()

            # 2. EVALUATE FINAL STATE & BUILD PROMPT
            relevance_grade = final_state.get("relevance_grade")
            insight_answer = final_state.get("insight_answer")
            documents = final_state.get("documents", [])

            if relevance_grade in ["hitl_approval_required", "action_complete", "needs_clarification"]:
                card_text = final_state.get("generation") or final_state.get("content_to_format") or (final_state.get("messages")[-1].content if final_state.get("messages") else "Action complete.")
                yield f"data: {json.dumps({'event': 'token', 'text': card_text})}\n\n"
                yield f"data: {json.dumps({'event': 'final_generation', 'text': card_text})}\n\n"
                # This branch returns early, bypassing the normal-path append/save below (line ~715-719) —
                # without persisting here, the card text (which classify_intent's card_marker detection
                # depends on finding in the PREVIOUS assistant message) never reaches chat_sessions, so a
                # later "approve" can never find it and always falls through to a fresh proposal instead
                # of executing.
                chat_sessions[history_key].append(AIMessage(content=card_text))
                save_conversation_turn(username, session_id, chat_sessions[history_key])
                t_first_token = time.perf_counter()
                log_timings(relevance_grade, "card")
                return

            # Single unified Voice Composer: one Sonic Assistant persona for every response
            # path, with only the grounding rules varying by source_type (assembled by
            # formatter_node into voice_payload — see backend/services/agent_workflow.py).
            payload = final_state.get("voice_payload") or {}
            source_type = payload.get("source_type", "kb_strict")
            documents_sorted = sorted(documents, key=lambda d: d.metadata.get("priority", False), reverse=True)
            data = payload.get("data") or format_docs(documents_sorted)
            kb_images = collect_kb_images(documents_sorted) if source_type in ("kb_strict", "kb_open") else []

            prompt = build_voice_prompt(
                grounding_block=GROUNDING_BLOCKS.get(source_type, KB_STRICT_GROUNDING),
                data=data,
                history=format_history_as_text(messages_state),
                question=final_state.get("original_question", question),
                affiliate_override=get_affiliate_override(requested_affiliate),
                insight=payload.get("insight") or "",
                emotional_context=build_emotional_context(
                    final_state.get("emotional_state"), datetime.now(timezone.utc)
                ),
            )
            if kb_images:
                # The composer has no innate way to know an image will actually be rendered
                # alongside this response — without this, the model falls back on its default
                # "I'm text-only" assumption and apologizes for a limitation that no longer applies.
                image_names = ", ".join(img["filename"] for img in kb_images)
                prompt = prompt + IMAGE_RENDERING_NOTE_TEMPLATE.format(image_names=image_names)

            if kb_images:
                yield f"data: {json.dumps({'event': 'kb_images', 'images': kb_images})}\n\n"

            # Announce LLM generation start
            yield f"data: {json.dumps({'event': 'node_progress', 'node': 'formatter_node', 'title': 'Formatting output structure...', 'detail': f'Synthesizing final answer for {question[:30]}...'})}\n\n"
            guardrail_context = fetch_relevant_corrections(username, question)
            # Computed once and threaded through both fact + semantic memory recall below — they
            # embed the identical question string this turn, and re-embedding it a second time
            # (the semantic recall call, further down) would be a genuinely free duplicate
            # network round trip to the embeddings API for no behavior change.
            question_embedding = embed_text(question) if question else None
            memory_context = fetch_relevant_user_facts(username, question, precomputed_embedding=question_embedding)
            goal_nudge_context = fetch_goal_nudge_context(username)

            if guardrail_context:
                prompt = prompt + guardrail_context
                # Emit trace event to frontend execution trace drawer!
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'self_correction_guardrail', 'title': 'Applying Lessons Learned Guardrail', 'detail': f'Injected past failure constraint into context prompt.'})}\n\n"

            if memory_context:
                prompt = prompt + memory_context
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'user_memory', 'title': 'Applying known user context', 'detail': 'Injected saved preferences/facts into context prompt.'})}\n\n"

            if goal_nudge_context:
                prompt = prompt + goal_nudge_context
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'goal_nudge_checkin', 'title': 'Checking in on a stale goal', 'detail': 'Injected a stale goal/project check-in into context prompt.'})}\n\n"

            # The unified voice prompt's {data} slot is populated from voice_payload/documents
            # above, but attachment summaries are appended separately here (unchanged) since
            # they're always relevant to this specific turn regardless of source_type.
            attachment_docs = [d for d in documents if d.metadata.get("source") == "user_attachment_summary"]
            if attachment_docs:
                attachment_context = (
                    "\n\nATTACHED FILE CONTENT (from this turn — you have already seen and "
                    "processed this, never claim you cannot see it):\n"
                    + "\n\n".join(d.page_content for d in attachment_docs) + "\n"
                )
                prompt = prompt + attachment_context
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'attachment_context', 'title': 'Applying attached file content', 'detail': 'Injected description of the attached file into context prompt.'})}\n\n"

            if source_type == "conversational":
                semantic_memory_context = retrieve_relevant_memory_context(
                    services.get("user_memory_vector_store"), username, question,
                    precomputed_embedding=question_embedding,
                )
                if semantic_memory_context:
                    prompt = prompt + semantic_memory_context
                    yield f"data: {json.dumps({'event': 'node_progress', 'node': 'user_memory_recall', 'title': 'Recalling relevant memory', 'detail': 'Found semantically relevant past context.'})}\n\n"
            # 3. STREAM RESPONSE TOKENS FROM LLM
            token_stream = response_llm.astream(prompt)
            async for chunk in token_stream:
                # Same Stop check as the graph phase above — a user cutting off a response
                # that's clearly going the wrong way shouldn't leave the LLM generating the
                # rest of it into the void.
                if await http_request.is_disconnected():
                    logger.info(f"[secure_chat] Client disconnected mid-response for {history_key}; stopping token stream.")
                    await token_stream.aclose()
                    stopped_note = f"{full_response}\n\n_[Stopped by user]_" if full_response else "_[Stopped by user before responding]_"
                    chat_sessions[history_key].append(AIMessage(content=stopped_note))
                    save_conversation_turn(username, session_id, chat_sessions[history_key])
                    return

                if first_token:
                    first_token = False
                    t_first_token = time.perf_counter()

                content = getattr(chunk, "content", "")
                if isinstance(content, list):
                    token = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                else:
                    token = str(content) if content else ""

                if not token:
                    continue

                full_response += token
                yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
                await asyncio.sleep(0)

            # 4. GROUNDING CHECK & FALLBACK — catches "no data to answer with" (a retrieval
            # problem), fixed by re-retrieving. Distinct from the reward evaluator below, which
            # catches a bad answer despite having good data (a generation problem).
            if full_response and "I cannot find the answer in the provided knowledge base." in full_response.strip():
                logger.info("Grounding failure detected — triggering rewrite fallback...")
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'rewrite_query_node', 'title': 'Refining search parameters...', 'detail': f'Expanding query parameters...'})}\n\n"
                yield f"data: {json.dumps({'event': 'regenerate_reset'})}\n\n"

                fallback_state = {
                    **initial_state,
                    "target_scope": final_state.get("target_scope", initial_state["target_scope"]),
                    "documents": final_state.get("documents", []),
                    "original_question": final_state.get("original_question", initial_state["original_question"]),
                }
                async for fallback_chunk in rewrite_fallback(services.get("vector_store"), fallback_state, username, session_id, chat_sessions, save_conversation_turn):
                    yield fallback_chunk
                log_timings(relevance_grade, "rewrite_fallback")
                return

            # 4b. EMOTIONAL-NEED CHECK — the reply was written with guidance for what this person
            # seems to want (to be heard, or to have good news explored with them), but guidance is
            # only prose. This verifies it was honored (no advice for a venting message, a real
            # question for shared good news) and regenerates once if not (backend/utils/emotion_checks.py).
            # Conversational replies only; the data-grounded paths are judged by the reward evaluator.
            if source_type == "conversational":
                emotional_issue = emotional_reply_issue(final_state.get("emotional_state"), full_response)
                if emotional_issue:
                    revision_prompt, revision_reason = build_emotional_revision_prompt(prompt, full_response, emotional_issue)
                    logger.info("Emotional-need check failed (%s); regenerating once.", emotional_issue)
                    yield f"data: {json.dumps({'event': 'node_progress', 'node': 'emotional_check', 'title': 'Adjusting the reply to fit what you need', 'detail': emotional_issue})}\n\n"
                    yield f"data: {json.dumps({'event': 'regenerate_reset'})}\n\n"
                    rejected_draft = full_response
                    full_response = ""
                    async for chunk in response_llm.astream(revision_prompt):
                        content = getattr(chunk, "content", "")
                        if isinstance(content, list):
                            token = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                        else:
                            token = str(content) if content else ""
                        if not token:
                            continue
                        full_response += token
                        yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
                        await asyncio.sleep(0)
                    logger.info("Emotional-need revision — rejected: %r | revised: %r", rejected_draft[:200], full_response[:200])

            # 5. REWARD EVALUATOR & SELF-CORRECTION — judges the response itself (not the
            # documents) and regenerates once, with feedback, if it fails. Capped at one retry;
            # whatever comes out is what gets shown, and the failure is logged as a correction
            # either way so future similar questions get a guardrail via fetch_relevant_corrections.
            if source_type in REWARD_EVAL_SOURCE_TYPES:
                verdict = await evaluate_response(prompt, full_response, source_type)
                if verdict["verdict"] == "fail":
                    logger.info(f"Reward evaluator flagged response ({verdict['tag']}): {verdict['reason']}")
                    yield f"data: {json.dumps({'event': 'node_progress', 'node': 'reward_evaluator', 'title': 'Reconsidering that answer...', 'detail': verdict['reason']})}\n\n"
                    yield f"data: {json.dumps({'event': 'regenerate_reset'})}\n\n"

                    original_response = full_response
                    corrected_prompt = build_correction_prompt(prompt, verdict["tag"], verdict["reason"])
                    full_response = ""
                    async for chunk in response_llm.astream(corrected_prompt):
                        content = getattr(chunk, "content", "")
                        if isinstance(content, list):
                            token = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                        else:
                            token = str(content) if content else ""
                        if not token:
                            continue
                        full_response += token
                        yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
                        await asyncio.sleep(0)

                    save_correction(
                        username,
                        user_prompt=question,
                        bad_response=original_response,
                        reason=verdict["reason"],
                        tag=verdict["tag"],
                        rating="negative",
                    )
                    logger.info(
                        "Reward evaluator correction applied — original: %r | corrected: %r",
                        original_response[:200], full_response[:200],
                    )

            yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"
            ai_message = AIMessage(content=full_response)
            if kb_images:
                ai_message.additional_kwargs["kb_images"] = kb_images
            chat_sessions[history_key].append(ai_message)
            save_conversation_turn(username, session_id, chat_sessions[history_key])
            # A false "I can't edit files" reply would otherwise be remembered and recalled into
            # later prompts as if it were a fact (a real memory chunk did exactly that).
            if not (folder_connected and is_stale_capability_denial(full_response)):
                spawn_background_task(index_conversation_turn(services.get("user_memory_vector_store"), username, question, full_response, session_id))
            # Bounds this conversation's checkpoint history right now rather than waiting for the daily pass.
            spawn_background_task(prune_thread_checkpoints_async(history_key))
            log_timings(relevance_grade, "ok")
            logger.info("--- End of token stream ---")

        except Exception as e:
            logger.error(f"[x] Error in token_streamer loop context: {e}", exc_info=True)
            log_timings(final_state.get("relevance_grade", "unknown"), "error")
            yield f"data: {json.dumps({'event': 'trace', 'title': 'Execution error', 'detail': str(e), 'status': 'active'})}\n\n"

    async def steerable_stream():
        # Registered for the whole turn so POST /api/chat/steer has somewhere to deliver messages.
        run = steering.open_run(history_key, chat_sessions[history_key])
        try:
            async for chunk in token_streamer():
                yield chunk
        finally:
            leftover = steering.close_run(history_key, run)
            if leftover:
                logger.info("[steering] %d steer(s) were never consumed for %s; the browser re-sends them.", len(leftover), history_key)

    logger.info(f"Initializing secured token stream for {username}")
    return StreamingResponse(
        steerable_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.post("/api/chat/steer")
async def steer_chat(request: SteerRequest, current_user = Depends(get_current_user)):
    """Queues a message into the turn that is currently running for this conversation, to be applied
    at the agent's next step. Anything but "queued" means it could not be applied; the browser then
    sends it as a normal follow-up message."""
    username = current_user.get("sub")
    session_id = request.session_id.strip()
    status = steering.submit(f"{username}::{session_id}", request.message)
    return {"status": status}


async def _delete_checkpoint_thread(username: str, session_id: str) -> None:
    """Deletes a conversation's checkpointed graph state (if a checkpointer is configured) —
    without this, a deleted/cleared conversation's checkpoint document would live in Mongo
    forever, the same class of unbounded-growth problem already fixed for the fact store.
    Best-effort: a checkpoint cleanup failure must never block the conversation deletion itself."""
    checkpointer = services.get("checkpointer")
    if checkpointer is None:
        return
    try:
        await checkpointer.adelete_thread(f"{username}::{session_id}")
    except Exception:
        logger.exception("Failed to delete checkpoint thread for %s::%s", username, session_id)


@app.post("/api/chat/clear")
async def clear_chat(request: Request, user = Depends(get_current_user)):
    data = await request.json()
    session_id = data.get("session_id")
    username = data.get("username") or user.get("sub") or user.get("email")

    if not username:
        return {"status": "cleared", "count": 0}

    # Remove live in-memory chat context used by the runtime session store.
    keys_to_remove = []
    if session_id:
        keys_to_remove.append(f"{username}::{session_id}")
    else:
        keys_to_remove.extend([k for k in list(chat_sessions.keys()) if k == username or k.startswith(f"{username}::")])

    for key in keys_to_remove:
        chat_sessions.pop(key, None)

    if session_id:
        # Empty this one conversation in place — keeps its id/title/slot in the conversation
        # list, but the same thread_id will be reused going forward, so its checkpoint (any
        # pending action, paused clarification, etc.) must be cleared too or stale state from
        # before the clear could resurface on the next message.
        save_conversation_turn(username, session_id, [])
        await _delete_checkpoint_thread(username, session_id)
        return {"status": "cleared", "count": 1}

    # No specific conversation given: wipe every conversation this user has.
    conversations = load_user_conversations(username)
    for convo in conversations:
        delete_user_conversation(username, convo["session_id"])
        await _delete_checkpoint_thread(username, convo["session_id"])
    return {"status": "cleared", "count": len(conversations)}

@app.post("/api/upload-attachment")
async def upload_attachment(
    session_id: str = Form(...), 
    file: UploadFile = File(...),
    current_user = Depends(get_current_user) # Replaced username: str = Form(...)
):
    username = current_user.get("sub") # Currently unused in this block, but ready if needed
    raw_bytes = await file.read()
    encoded = base64.b64encode(raw_bytes).decode("utf-8")
    attachment = Attachment(filename=file.filename, content=encoded)

    return {"status": "ok", "filename": file.filename}

@app.get("/api/documents/download/{filename:path}")
def download_document(
    filename: str, 
    current_user: dict = Depends(get_current_user)  # Locks down endpoint to valid logged-in users
):
    # Fetch DB instance from your helper
    db = get_db()
    if db is None:
        raise HTTPException(
            status_code=500, 
            detail="Database connection is disabled (USE_DB is not set to true)."
        )

    # Initialize GridFS bucket with the synchronous PyMongo db handle
    gridfs_bucket = GridFSBucket(db)

    # 1. Decode URL encoded spaces/characters (%20 -> " ")
    decoded_filename = urllib.parse.unquote(filename)
    
    # 2. Extract bare filename in case full path was supplied
    clean_basename = decoded_filename.split("/")[-1].split("\\")[-1]

    # 3. Flexible lookup against GridFS
    file_doc = db["fs.files"].find_one({
        "$or": [
            {"filename": decoded_filename},
            {"filename": clean_basename},
            {"filename": {"$regex": f"^{re.escape(clean_basename)}$", "$options": "i"}},
            {"metadata.filename": clean_basename}
        ]
    })

    if not file_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{clean_basename}' not found in database repository."
        )

    # 4. Stream from GridFS bucket using matched document ID
    grid_out = gridfs_bucket.open_download_stream(file_doc["_id"])

    safe_filename = urllib.parse.quote(clean_basename)
    return StreamingResponse(
        grid_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=utf-8''{safe_filename}"
        }
    )

@app.get("/api/attachments/image/{file_id}")
def get_attachment_image(
    file_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Serves a chat-attached image's raw bytes back from GridFS by id — the durable,
    auth-gated equivalent of the Knowledge Assistant's SAS-URL image rendering, without needing
    time-limited signed URLs or a regeneration job since access is already gated by login."""
    db = get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail="Database connection is disabled (USE_DB is not set to true)."
        )

    try:
        object_id = ObjectId(file_id)
    except (errors.InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid attachment id.")

    file_doc = db["fs.files"].find_one({"_id": object_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="Attachment image not found.")

    content_type = file_doc.get("metadata", {}).get("content_type", "application/octet-stream")
    grid_out = GridFSBucket(db).open_download_stream(object_id)

    return StreamingResponse(grid_out, media_type=content_type)

# --- ELEVATED ENDPOINT: SECURE MULTI-PART FILE UPLOAD (MongoDB GridFS) ---
def sync_run_script(script_path):
    process = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    stdout, stderr = process.communicate()

    logger.info(f"[INGEST STDOUT]\n{stdout}")
    logger.error(f"[INGEST STDERR]\n{stderr}")
    logger.info(f"[INGEST EXIT CODE] {process.returncode}")

@app.post("/api/upload")
async def upload_and_ingest_documents(
    affiliate: str = Query(...),
    files: List[UploadFile] = File(...),
    current_user = Depends(get_current_user)
):
    if not verify_user_ingest_access(current_user.get("sub"), affiliate):
        raise HTTPException(status_code=403, detail="Unauthorized")

    # 1. Access/Upload Logic
    db = get_db()
    fs = GridFS(db)
    for file in files:
        content = await file.read()
        fs.put(content, filename=file.filename, metadata={"affiliate": affiliate, "status": "raw", "processed": False})
    await asyncio.sleep(3)
    # 2. Diagnostics & Trigger
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(base_dir, "local_function_app", "function_app.py")
        
        # This sends the function to a background thread, preventing the main app crash
        await asyncio.to_thread(sync_run_script, script_path)
        
        logger.info(f"Ingestion pipeline triggered in thread: {script_path}")
        return {"status": "success", "message": "Uploaded and started ingestion."}
        
    except Exception as e:
        logger.exception(f"Failed to spawn ingestion process: {str(e)}")
        raise HTTPException(status_code=500, detail="Trigger failed.")

# --- ELEVATED ENDPOINT: FETCH INDEXED MANIFEST (MongoDB GridFS) ---
@app.get("/api/documents")
async def list_documents(affiliate: str = Query(...), current_user = Depends(get_current_user)):
    # 1. Permission check
    if not verify_user_ingest_access(current_user.get("sub"), affiliate):
        raise HTTPException(status_code=403, detail="Unauthorized")

    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
        
    fs = GridFS(db)
    
    # 2. Fetch from the "Pages Container" (documents that succeeded ingestion)
    archived_files = fs.find({
        "metadata.affiliate": affiliate,
        "metadata.status": "pages"
    })

    # 3. Build manifest matching React frontend expectations
    manifest = []
    for file_obj in archived_files:
        manifest.append({
            "id": str(file_obj._id),
            "filename": file_obj.filename,
            "uploadDate": file_obj.upload_date.isoformat(),
            "fileSize": f"{round(file_obj.length / 1024, 1)} KB"
        })

    return manifest

# --- ELEVATED ENDPOINT: PURGE FROM VECTOR INDEX (MongoDB GridFS) ---
import re  # Ensure 're' is imported at top of app.py

# --- ELEVATED ENDPOINT: PURGE FROM VECTOR INDEX (MongoDB GridFS) ---
@app.delete("/api/documents/{doc_id}")
async def delete_document(
    doc_id: str, 
    affiliate: str = Query(...),
    current_user = Depends(get_current_user)
):
    # Security Check
    user_id = current_user.get("sub")
    if not verify_user_ingest_access(user_id, affiliate):
        raise HTTPException(status_code=403, detail="Unauthorized.")

    # Initialize DB in scope
    db = get_db() 
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    
    # GridFS Logic
    fs = GridFS(db)
    try:
        file_obj = fs.get(ObjectId(doc_id))
        raw_filename = file_obj.filename
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

    # 1. Normalize filename: Strip browser copy suffixes like " (1)", " (2)", " - Copy"
    # e.g., "jack facts (1).pdf" -> "jack facts.pdf"
    base_filename = re.sub(r'[\s\-_]*\(\d+\)|[\s\-_]*copy', '', raw_filename, flags=re.IGNORECASE)

    # 2. Escape special characters for regex matching
    safe_raw = re.escape(raw_filename)
    safe_base = re.escape(base_filename)

    logger.info(f"Sweeping MongoDB Atlas 'documents' for: '{raw_filename}' and base target: '{base_filename}'")
    
    try:
        vector_collection = db["documents"]
        
        # Sweep both root-level 'source' AND nested 'metadata.source'
        query = {
            "$or": [
                # 1. Root-level field checks (Matches your chunk payload)
                {"source": raw_filename},
                {"source": base_filename},
                {"source": {"$regex": f".*{safe_raw}$", "$options": "i"}},
                {"source": {"$regex": f".*{safe_base}$", "$options": "i"}},
                
                # 2. Nested field fallback (if other ingestors use metadata)
                {"metadata.source": raw_filename},
                {"metadata.source": base_filename},
                {"metadata.source": {"$regex": f".*{safe_raw}$", "$options": "i"}},
                {"metadata.source": {"$regex": f".*{safe_base}$", "$options": "i"}}
            ]
        }
        
        result = vector_collection.delete_many(query)
        logger.info(f"Successfully cleared {result.deleted_count} vector fragments.")
        # 4. Remove target file from GridFS
        fs.delete(ObjectId(doc_id))

        # 5. Optional: Clean up older orphaned GridFS file objects matching base name
        orphan_files = db["fs.files"].find({"filename": base_filename})
        for orphan in orphan_files:
            fs.delete(orphan["_id"])
            logger.info(f"Cleaned orphaned GridFS file record for: {base_filename}")

        return {"status": "success", "detail": f"Expelled {raw_filename} and cleared {result.deleted_count} vector fragments."}

    except Exception as e:
        logger.exception(f"Deletion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/conversations")
async def list_conversations(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return list_user_conversation_summaries(username)

@app.get("/api/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    limit: int = Query(DEFAULT_CONVERSATION_PAGE_SIZE, ge=1, le=MAX_CONVERSATION_PAGE_SIZE),
    before: int | None = Query(None, ge=0),
    current_user = Depends(get_current_user),
):
    """One page of a conversation, newest messages by default. `start`/`total` in the response let
    the client request the previous page with `before=start`."""
    username = current_user.get("sub")
    convo = load_user_conversation(username, session_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    page = paginate_messages(convo.get("messages", []), limit=limit, before=before)
    return {"session_id": convo["session_id"], "title": convo.get("title", ""), **page}

@app.delete("/api/conversations/{session_id}")
async def remove_conversation(session_id: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    chat_sessions.pop(f"{username}::{session_id}", None)
    if not delete_user_conversation(username, session_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _delete_checkpoint_thread(username, session_id)
    return {"status": "deleted", "session_id": session_id}

@app.post("/api/chat/feedback")
async def store_feedback(
    payload: FeedbackPayload, 
    current_user = Depends(get_current_user)
):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    username = current_user.get("sub")

    save_correction(
        username,
        user_prompt=payload.user_prompt,
        bad_response=payload.bad_response,
        reason=payload.reason,
        tag=payload.tag,
        rating=payload.rating or "negative",
    )
    logger.info(f"[+] Correction stored for {username} (Tag: {payload.tag}): {payload.reason}")
    return {"status": "success", "message": "Feedback indexed for dynamic self-correction."}


@app.get("/api/memory")
async def list_memory(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    facts = load_user_facts(username)
    now = datetime.now(timezone.utc)
    return [{**f.dict(), "effective_confidence": effective_confidence(f, now)} for f in facts]


@app.delete("/api/memory/{fact_id}")
async def delete_memory_fact(fact_id: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    deleted = delete_user_fact(username, fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory fact not found")
    return {"status": "deleted", "id": fact_id}


@app.delete("/api/memory")
async def clear_memory(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    delete_all_user_facts(username)
    return {"status": "cleared"}


@app.get("/api/local-workspace")
async def get_local_workspace(current_user = Depends(get_current_user)):
    return await asyncio.to_thread(local_workspace_status, current_user.get("sub"))


@app.post("/api/local-workspace/sync")
async def sync_local_workspace(payload: LocalWorkspaceSync, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Connecting a local folder requires signing in.")
    return await asyncio.to_thread(
        apply_local_workspace_sync,
        username,
        payload.name,
        [f.dict() for f in payload.files],
        payload.deleted,
        payload.reset,
    )


@app.delete("/api/local-workspace")
async def disconnect_local_workspace(current_user = Depends(get_current_user)):
    await asyncio.to_thread(clear_local_workspace, current_user.get("sub"))
    return {"status": "disconnected"}


@app.post("/api/memory/compact")
async def compact_memory(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    result = await compact_user_memory(get_db(), services.get("user_memory_vector_store"), username)
    return result


@app.post("/api/memory/compact-meta")
async def compact_meta_memory_endpoint(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    result = await compact_meta_memory(get_db(), services.get("user_memory_vector_store"), username)
    return result


@app.get("/api/settings/rag-mode")
async def get_rag_mode_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"rag_mode": get_user_rag_mode(username)}


@app.put("/api/settings/rag-mode")
async def update_rag_mode_setting(payload: RagModeUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    if payload.rag_mode not in VALID_RAG_MODES:
        raise HTTPException(status_code=400, detail="rag_mode must be 'strict' or 'open'")
    saved = set_user_rag_mode(username, payload.rag_mode)
    return {"rag_mode": saved}


@app.get("/api/settings/deep-thinking")
async def get_deep_thinking_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"deep_thinking": get_user_deep_thinking_mode(username)}


@app.put("/api/settings/deep-thinking")
async def update_deep_thinking_setting(payload: DeepThinkingUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    saved = set_user_deep_thinking_mode(username, payload.deep_thinking)
    return {"deep_thinking": saved}


@app.get("/api/settings/target-repo")
async def get_target_repo_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"target_repo": get_user_target_repo(username)}


@app.put("/api/settings/target-repo")
async def update_target_repo_setting(payload: TargetRepoUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    saved = set_user_target_repo(username, payload.target_repo)
    return {"target_repo": saved}


@app.get("/api/settings/target-doc")
async def get_target_doc_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"target_doc_id": get_user_target_doc_id(username)}


@app.put("/api/settings/target-doc")
async def update_target_doc_setting(payload: TargetDocUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    saved = set_user_target_doc_id(username, payload.doc_url_or_id)
    warning = None
    if saved:
        # Best-effort validation using the user's own token, if connected — never blocks the
        # save itself, since the connection or Docs scope might not exist yet.
        try:
            token = GoogleCalendarOAuth().get_valid_access_token(username)
            metadata = get_drive_file_metadata(token, saved)
            if metadata.get("mimeType") != "application/vnd.google-apps.document":
                warning = "That ID doesn't look like a Google Doc."
        except Exception:
            warning = "Could not verify access to this document yet — check that it's shared with your connected Google account."
    return {"target_doc_id": saved, "warning": warning}


@app.get("/api/settings/github-token")
async def get_github_token_setting(current_user = Depends(get_current_user)):
    """Whether this user has their own GitHub token set, and its last four characters. The token
    itself is never sent back to the browser."""
    username = current_user.get("sub")
    return get_github_token_status(username)


@app.put("/api/settings/github-token")
async def update_github_token_setting(payload: GitHubTokenUpdate, current_user = Depends(get_current_user)):
    """Saves (or, with an empty token, removes) the user's own GitHub token. The token is checked with
    GitHub first and only stored, encrypted, if GitHub accepts it, so a typo is never saved as if it worked."""
    username = current_user.get("sub")
    try:
        return await asyncio.to_thread(verify_and_store_github_token, username, payload.token)
    except GitHubTokenNotAllowed as error:
        raise HTTPException(status_code=403, detail=str(error))
    except (GitHubTokenInvalid, GitHubTokenRejected) as error:
        raise HTTPException(status_code=400, detail=str(error))
    except GitHubUnreachable as error:
        raise HTTPException(status_code=503, detail=str(error))
    except SecretStorageNotConfigured:
        logger.error("A GitHub token was submitted but TOKEN_ENCRYPTION_KEY is not usable; nothing was stored.")
        raise HTTPException(status_code=503, detail="Token storage isn't configured on this server.")


@app.get("/api/settings/has-seen-help")
async def get_has_seen_help_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"has_seen_help": get_user_has_seen_help(username)}


@app.put("/api/settings/has-seen-help")
async def update_has_seen_help_setting(payload: HasSeenHelpUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    saved = set_user_has_seen_help(username, payload.has_seen_help)
    return {"has_seen_help": saved}


@app.get("/api/settings/timezone")
async def get_timezone_setting(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return {"timezone": get_user_timezone(username)}


@app.put("/api/settings/timezone")
async def update_timezone_setting(payload: TimezoneUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    saved = set_user_timezone(username, payload.timezone)
    return {"timezone": saved}


@app.post("/api/calendar/connect/start")
async def start_calendar_connection(return_to: str = Query(...), current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    if username in CALENDAR_LOCKED_USERS:
        raise HTTPException(status_code=403, detail="Google Calendar is not available for this account")
    if not _is_allowed_calendar_return_to(return_to):
        raise HTTPException(status_code=400, detail="return_to must be a trusted application URL")

    try:
        url, state, code_verifier = GoogleCalendarOAuth().authorization_url()
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    create_calendar_pending(state, username, code_verifier, return_to)
    return {"authorization_url": url}


@app.get("/api/calendar/callback")
async def calendar_oauth_callback(request: Request, state: str = Query(...)):
    # No auth dependency — Google's redirect is a bare browser navigation carrying no Bearer
    # token. The pending record (looked up by state alone) is what recovers which user started
    # this flow; see google_calendar_utils.create_pending/get_pending.
    # HashRouter (see local/src/main.tsx) — the route lives after the '#', not as a real path.
    default_target = "https://www.sonicassistant.com/#/integrations"
    pending = get_calendar_pending(state)
    if not pending:
        return RedirectResponse(f"{default_target}?calendar_error=expired")

    return_to = pending.get("return_to") or default_target
    username = pending.get("username")
    oauth_error = request.query_params.get("error")
    if oauth_error:
        consume_calendar_pending(state)
        return RedirectResponse(f"{return_to}?calendar_error=access_denied")

    try:
        connection = GoogleCalendarOAuth().build_connection(str(request.url), state, pending.get("code_verifier"))
        save_calendar_connection(username, **connection)
        consume_calendar_pending(state)
    except Exception:
        logger.exception("[calendar_oauth_callback] Failed to complete Google Calendar connection for %s", username)
        return RedirectResponse(f"{return_to}?calendar_error=connection_failed")

    return RedirectResponse(f"{return_to}?connected=google")


@app.get("/api/calendar/status")
async def get_calendar_status(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return get_calendar_connection_status(username)


@app.post("/api/calendar/disconnect")
async def disconnect_calendar(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    GoogleCalendarOAuth().revoke(username)
    delete_calendar_connection(username)
    return {"connected": False}


@app.post("/api/v1/webhooks/ingest", status_code=status.HTTP_200_OK)
async def ingest_error_webhook(
    payload: IngestPayload,
    x_ingest_secret: Optional[str] = Header(default=None, alias="X-Ingest-Secret"),
):
    configured_secret = os.getenv("INGEST_WEBHOOK_SECRET")
    if not configured_secret:
        logger.error("INGEST_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="Ingest is not configured")

    if x_ingest_secret != configured_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"accepted": False, "error": "invalid_ingest_secret"},
        )

    resolved_repo, resolved_via = resolve_target_repo(
        service_name=payload.service_name,
        payload_repo=payload.repository,
        metadata=payload.metadata,
    )

    event_doc = {
        "service_name": payload.service_name.strip(),
        "error_message": payload.error_message,
        "stack_trace": payload.stack_trace,
        "environment": payload.environment or "unknown",
        "repository": resolved_repo,
        "resolved_via": resolved_via,
        "metadata": payload.metadata or {},
        "source": "direct_ingest",
    }

    try:
        event_id = save_error_event(event_doc)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to persist ingest event: %s", str(exc))
        raise HTTPException(status_code=500, detail="Failed to store ingest event") from exc

    return {
        "accepted": True,
        "status": "stored",
        "event_id": event_id,
        "service_name": payload.service_name,
        "resolved_repository": resolved_repo,
        "resolved_via": resolved_via,
    }

# -------------------------------------------------------------
# 5. TEST ROUTES
# -------------------------------------------------------------
@app.get("/api/erragent-debug")
async def trigger_error():
    logger.info("--> /api/erragent-debug endpoint hit!")
    # Intentionally trigger zero division; caught and handled via HTTPException to prevent unhandled crash
    try:
        return 1 / 0
    except ZeroDivisionError as e:
        raise HTTPException(status_code=500, detail="ZeroDivisionError: division by zero")
    
@app.post("/webhooks/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Listens for GitHub webhook events and triggers automated PR reviews.
    """
    event = request.headers.get("X-GitHub-Event")

    # The raw body is needed to check GitHub's signature, so it is read once and parsed from here.
    raw_body = await request.body()
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if webhook_secret:
        if not verify_github_signature(webhook_secret, raw_body, request.headers.get("X-Hub-Signature-256")):
            logger.warning("Rejected a GitHub webhook with a missing or invalid signature.")
            raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    else:
        logger.warning(
            "GITHUB_WEBHOOK_SECRET is not set, so /webhooks/github accepts unsigned requests. "
            "Set it (and the same secret on the GitHub webhook) to reject forged events."
        )

    try:
        payload = json.loads(raw_body or b"{}")
    except Exception:
        payload = {}

    if event == "pull_request" and payload.get("action") in ["opened", "synchronize", "reopened"]:
        pr_number = payload.get("number")
        repo = payload.get("repository", {}).get("full_name")

        if pr_number and repo:
            # A user who pinned this repo AND supplied their own token reviews it as themselves
            # (their token, their audit entry); otherwise the shared server token is used, as before.
            token_owner, user_token = find_github_token_for_repo(repo)
            if not user_token and not os.getenv("GITHUB_TOKEN"):
                # Previously this queued a job that failed silently in the background and still told
                # GitHub "event_queued", so a repo with no usable token looked like it was working.
                logger.error(
                    "No GitHub token can act on %s: no user who owns or pinned it has saved a token, and the shared "
                    "GITHUB_TOKEN is not set. Save a token under Integrations on the account that owns the repo.", repo,
                )
                return {"status": "no_token_for_repo", "pr_number": pr_number}
            audit_github_token_use(token_owner, "user" if user_token else "shared", "webhook_pr_summary", repo)

            background_tasks.add_task(process_pr_summary, repo, pr_number, user_token)
            logger.info(f"Queued background PR summary job for {repo} #{pr_number}")
            return {"status": "event_queued", "pr_number": pr_number}

    return {"status": "ignored_event"}

@app.get("/api/health", tags=["Health"])
def saapp_health_check():
    """
    Lightweight health endpoint for SAAPP.
    Used by errAgent to monitor uptime and latency.
    """
    db_status = "connected" if test_connection() else "disabled_or_failed"

    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "service": "SAAPP Widget",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/api/synthetic/ask")
async def synthetic_ask(
    request: SyntheticAskRequest,
    current_user=Depends(get_current_user),
):
    workflow = services.get("compiled_workflow")

    answer = await run_synthetic_read_only_question(
        workflow=workflow,
        question=request.question,
        username=current_user.get("sub", "synthetic-check"),
    )

    return {
        "status": "ok",
        "answer": answer,
        "request_id": uuid.uuid4().hex,
    }