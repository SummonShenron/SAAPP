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
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query, Form, Request, Depends, BackgroundTasks, status, Response, Body
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
from backend.models.models import llm, get_stream_llm, claude_available
from backend.models.attachment import Attachment
from backend.services.github_service import (
    process_pr_summary, verify_and_store_github_token, GitHubTokenRejected, GitHubUnreachable,
)
from backend.utils.secret_utils import SecretStorageNotConfigured
from backend.utils.webhook_utils import verify_github_signature
from backend.utils.github_audit import audit_github_token_use
# Modernized LangChain Imports
# SAAPP upgraded model coding capabilities active
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
    load_user_conversation_page,
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
from backend.utils.attachment_utils import delete_session_attachments, process_chat_attachments
from backend.utils import progress_notes as progress_notes_utils
from backend.utils.stored_file_access import can_read_stored_file, first_readable
from backend.utils.docx_writer import DOCX_MIME, render_docx, validate_document
from backend.utils.docx_edit import DocxEditError, apply_edits as apply_docx_edits
from backend.utils.memory_utils import (
    fetch_relevant_user_facts,
    fetch_goal_nudge_context,
    load_user_facts,
    delete_user_fact,
    effective_confidence,
    fact_for_state,
    linked_fact_summaries,
    unlink_user_facts,
    edit_user_fact,
    FactEditError,
)
from backend.services.memory_compaction import compact_user_memory, compact_meta_memory
from backend.services.memory_search import context_query_for, recall_with_second_look
from backend.services.local_workspace import (
    apply_sync as apply_local_workspace_sync,
    clear_workspace as clear_local_workspace,
    is_guest_username,
    workspace_status as local_workspace_status,
)
from backend.utils.agent_utils import is_stale_capability_denial, scrub_stale_capability_denials
from backend.utils.embedding_utils import embed_text
from backend.utils.emotion_utils import build_emotional_context, effective_intensity, energy_tier
from backend.utils.encouragement_utils import (
    ENCOURAGEMENT_MARK, build_encouragement_block, build_encouragement_revision_prompt, encouragement_reply_issue,
    select_encouragement,
)
from backend.utils.relatable_utils import RELATABLE_MARK, build_relatable_block, mentions_itself, select_relatable
from backend.utils.self_observations import asks_about_itself, build_self_knowledge_block, observations_for_turn
from backend.utils.emotion_checks import emotional_reply_issue, build_emotional_revision_prompt
from backend.utils.wrapup_utils import is_closing_message, CLOSING_DIRECTIVE, closing_reply_issue, build_closing_revision_prompt
from backend.utils.identity_checks import identity_reply_issue, build_identity_revision_prompt
from backend.utils.self_counters import new_turn_counts, record_turn_counts, revision_kind, tally
from backend.utils.safety_utils import (
    effective_risk, plan_safety_turn, build_safety_revision_prompt, remembered_support_people, CRISIS_RESOURCE_LINE, RISK_NONE,
    has_crisis_resource, record_safety_turn,
)
from backend.utils.outage_utils import handle_failed_turn, in_crisis
from backend.utils.safety_utils import KIND_OUTAGE_FALLBACK, log_safety_event
from backend.utils.safety_stats import fetch_recent_events, summarize_safety_events
from backend.utils.admin_utils import is_global_admin
from backend.utils.sonic_health import build_sonic_health
from backend.utils.self_observations import (
    approve as approve_observation_doc, list_observations, reflect as reflect_observations, retire as retire_observation_doc,
)
from backend.utils.example_questions import build_example_questions, build_welcome_cards, gather_example_inputs
from backend.components.sonic_profile import SONIC_PROFILE
from backend.utils.time_utils import build_time_context, local_today, sent_at, stamp
from backend.utils import open_loops as open_loops_utils
from backend.utils import clarifications as clarify_utils
from backend.utils import initiation as initiation_utils
from backend.utils import budget_guard
from backend.utils import llm_turns, llm_usage, loop_watchdog
from backend.utils import ops_digest as ops_digest_utils
from backend.utils import ops_suggestions as ops_suggestions_utils
from backend.utils import duration_utils
from backend.utils import curiosity as curiosity_utils
from backend.utils import paperwork_utils
from backend.utils import profile_utils
from backend.utils import reading_shelf as shelf_utils
from backend.services import shelf_refresh
from backend.utils.self_history_utils import SELF_HISTORY_MARK, build_self_history_block, select_self_history
from backend.utils import routines as routines_utils
from backend.utils import followups as followups_utils
from backend.services import routine_scheduler
from backend.services import user_task_scheduler
from backend.services import meeting_sweep
from backend.utils import user_tasks as user_tasks_utils
from backend.utils import meetings as meetings_utils
from backend.utils import calendar_headsups as headsups_utils
from backend.utils.shared_history import CALLBACK_MARK, build_callback_block, callback_allowed
from backend.utils.user_settings_utils import (
    get_user_rag_mode, set_user_rag_mode, VALID_RAG_MODES,
    get_user_deep_thinking_mode, set_user_deep_thinking_mode,
    tool_model_view, save_tool_model_choice,
    get_user_target_repo, set_user_target_repo,
    get_user_settings_bundle,
    get_user_profile,
    set_user_profile,
    get_user_has_seen_help, set_user_has_seen_help,
    get_user_timezone, set_user_timezone,
    get_user_target_doc_id, set_user_target_doc_id,
    get_github_token_status, find_github_token_for_repo, get_user_github_token,
    GitHubTokenInvalid, GitHubTokenNotAllowed,
    TOGGLE_LOCKED_USERS,
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
from backend.utils import microsoft_utils
from backend.services.microsoft_oauth import MicrosoftOAuth
from backend.services.reward_evaluator import evaluate_response, build_correction_prompt, REWARD_EVAL_SOURCE_TYPES
from backend.logging.sass_logger import setup_logging
from backend.services.orchestrator import startup_services
from backend.state import graph_db
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access, load_directory, make_personal_kb_id, new_personal_kb, personal_kb_groups, personal_kb_update, resolve_kb_display_names
from backend.utils.db_utils import get_db, save_error_event, test_connection
from backend.utils import memory_erase
from backend.auth.isolation_auth import get_current_user, record_login_event
from backend.services.checkpoint_retention import run_checkpoint_retention_loop, prune_thread_checkpoints_async
from backend.services.attachment_retention import run_attachment_retention_loop
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

async def _run_shelf_schedule():
    from backend.models.models import lite_llm as shelf_llm

    async def allowed() -> bool:
        return budget_guard.allows("shelf_refresh", await asyncio.to_thread(budget_guard.current_state))

    await shelf_refresh.run_scheduled_refresh_loop(get_db, shelf_llm.ainvoke, allowed)


async def _run_user_task_schedule():
    from backend.models.models import lite_llm as task_llm

    def allowed(owner):
        # The shared budget, then that person's own: a task is an optional extra, so it yields to low-power mode like the others.
        return (budget_guard.allows("user_tasks", budget_guard.current_state())
                and (owner is None or budget_guard.allows("user_tasks", budget_guard.current_state(username=owner))))

    await user_task_scheduler.run_user_task_schedule(get_db, task_llm.ainvoke, allowed)


async def _run_meeting_sweep():
    from backend.models.models import lite_llm as note_llm

    def allowed(owner):
        return budget_guard.allows("meeting_notes", budget_guard.current_state(None, None, owner))

    await meeting_sweep.run_meeting_sweep(get_db, note_llm.invoke, load_session_messages, allowed)


# 2. Define the startup/shutdown logic
@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_sessions
    # This runs once when the server starts. Chat transcripts are NOT warm-loaded here: each
    # conversation is read from the store the first time it's used (see secure_chat).
    retention_task = spawn_background_task(run_checkpoint_retention_loop())
    attachment_retention_task = spawn_background_task(run_attachment_retention_loop())
    # Sonic's own reading, about once a day whether or not anyone is chatting (backend/services/shelf_refresh.py). Paused with the other optional
    # extras when the cost guard is in low-power mode.
    shelf_task = spawn_background_task(_run_shelf_schedule())
    # Sonic's routines that have no loop of their own (today the health check), run on a schedule and written down (backend/services/routine_scheduler.py).
    routine_task = spawn_background_task(routine_scheduler.run_routine_schedule(get_db))
    # The research tasks people have set up for Sonic, run when due and within limits (backend/services/user_task_scheduler.py).
    user_task_task = spawn_background_task(_run_user_task_schedule())
    # A meeting the person forgot to end closes itself after a quiet spell, with its notes (backend/services/meeting_sweep.py).
    meeting_sweep_task = spawn_background_task(_run_meeting_sweep())
    watchdog_task = loop_watchdog.start()  # logs when the event loop is blocked, and what was waiting (backend/utils/loop_watchdog.py)
    if get_db() is not None:
        ensure_calendar_indexes()
        microsoft_utils.ensure_indexes()
    yield
    # Cleanup tasks would go here
    retention_task.cancel()
    attachment_retention_task.cancel()
    shelf_task.cancel()
    routine_task.cancel()
    user_task_task.cancel()
    meeting_sweep_task.cancel()
    watchdog_task.cancel()
    await asyncio.to_thread(llm_usage.flush)  # whatever the last calls used, before the process goes
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
    content: Optional[str] = None           # a text file
    content_base64: Optional[str] = None    # a .docx, of which the server keeps only a numbered text outline (backend/utils/docx_edit.py)

class LocalWorkspaceSync(BaseModel):
    name: str
    reset: bool = False
    files: list[LocalWorkspaceFile] = []
    deleted: list[str] = []

class RagModeUpdate(BaseModel):
    rag_mode: str

class DeepThinkingUpdate(BaseModel):
    deep_thinking: bool

class ToolModelUpdate(BaseModel):
    tool_model: str

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

class ProactiveOpeningUpdate(BaseModel):
    enabled: bool

class OpeningRequest(BaseModel):
    session_id: str

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
    in_flight = loop_watchdog.request_started(request.method, request.url.path)

    try:
        response = await call_next(request)
        loop_watchdog.request_finished(in_flight)
    except Exception:
        loop_watchdog.request_finished(in_flight)
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

    # A synchronous Mongo insert: on the event loop it froze every request for as long as the database took to answer.
    await asyncio.to_thread(
        record_login_event,
        user_id=sub,
        email=email,
        is_guest=is_guest,
        ip_address=client_ip,
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


@app.get("/api/sonic-profile")
async def get_sonic_profile(current_user = Depends(get_current_user)):
    """Sonic's character profile for the help panel's About card (see backend/components/sonic_profile.py)."""
    return SONIC_PROFILE


@app.get("/api/example-questions")
async def get_example_questions(affiliate: str = Query("All"), current_user = Depends(get_current_user)):
    """Welcome-screen questions about this person's own state (documents, integrations, repo), for one
    knowledge-base scope. Built without a model call; see backend/utils/example_questions.py."""
    username = current_user.get("sub")
    directory = load_directory()
    accessible = get_accessible_affiliates(username, directory)["accessible_affiliates"]
    if affiliate != "All" and affiliate not in accessible:
        raise HTTPException(status_code=403, detail="Unauthorized affiliate scope requested.")
    inputs = await asyncio.to_thread(gather_example_inputs, username, affiliate, directory)
    # `questions` is the older plain list, kept so a page loaded before this deploy still works; `cards` are the labelled ones.
    return {"questions": build_example_questions(inputs), "cards": build_welcome_cards(inputs)}


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
    effective_tool_model = _settings.get("tool_model")      # "claude" | "gemini" | None (never chose: the server's default)
    effective_target_repo = _settings["target_repo"]
    user_profile = _settings.get("profile") or profile_utils.stored_profile({})  # what they set on the Account page (backend/utils/profile_utils.py)
    # The usage budget guard (backend/utils/budget_guard.py). Off, and free, unless a budget is configured. In low-power mode deep
    # thinking is off whatever the setting says, and the optional extras further down are skipped.
    is_admin_user = is_global_admin(directory, username)
    # A person's own limits apply to them, admins included (so an admin's testing cannot use up the shared budget everyone else draws on).
    budget_state = await asyncio.to_thread(budget_guard.current_state, None, None, budget_guard.limit_username(username, is_admin_user))
    if not budget_guard.allows("deep_thinking", budget_state):
        effective_deep_thinking = False

    # ---------- Conversation Memory State Init ----------
    # The full transcript in chat_sessions[history_key] is kept durable (it's what gets
    # persisted per-conversation); only a bounded recent window is fed to the LLM/graph.
    if history_key not in chat_sessions:
        # Reads the whole saved conversation (megabytes for a long one): in a worker thread, never on the event loop.
        chat_sessions[history_key] = await asyncio.to_thread(load_session_messages, username, session_id)
    else:
        # Another backend instance sharing this database may have saved since we last looked.
        await asyncio.to_thread(sync_session_messages, username, session_id, chat_sessions[history_key])
    # When the previous message in this conversation was sent (None for a new conversation or one saved before
    # messages were stamped), read before this turn's own message goes in, so the reply can tell how long they were away.
    last_message_at = sent_at(chat_sessions[history_key][-1]) if chat_sessions[history_key] else None
    # Whether this message answers one Sonic opened the conversation with (backend/utils/initiation.py): counted, and the
    # run of unanswered openings starts over. Only checked when it is true, so ordinary turns cost nothing extra.
    answers_an_opening = bool(chat_sessions[history_key]) and initiation_utils.is_initiated(chat_sessions[history_key][-1])
    answered_kind = initiation_utils.opening_kind(chat_sessions[history_key][-1]) if answers_an_opening else None
    if answers_an_opening and not is_guest_username(username):
        await asyncio.to_thread(initiation_utils.answered, username)
    # Whether this message picks up the one open question Sonic asked at a lull (backend/utils/curiosity.py): counted only.
    previous_marks = (getattr(chat_sessions[history_key][-1], "additional_kwargs", None) or {}) if chat_sessions[history_key] else {}
    answers_curiosity = bool(previous_marks.get(curiosity_utils.CURIOSITY_MARK)) and curiosity_utils.taken(question)
    answers_shelf = bool(previous_marks.get(shelf_utils.SHELF_MARK)) and curiosity_utils.taken(question)
    clarify_reply_to = previous_marks.get(clarify_utils.CLARIFY_MARK)  # Sonic's last reply asked how a thing fits into their life: this message is the answer (or not)
    previous_reply = open_loops_utils.previous_reply(chat_sessions[history_key])  # so "Friday at 2" can be tied to what Sonic just asked about
    chat_sessions[history_key].append(stamp(HumanMessage(content=question)))

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
        "tool_model": effective_tool_model,
        "budget_level": budget_state.get("level"),
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
        # Vision-model calls and the image write, in a worker thread: on the event loop one photo froze every request.
        attachment_summaries, attachment_refs = await asyncio.to_thread(
            process_chat_attachments, username, session_id, request.attachments
        )

    if attachment_refs:
        chat_sessions[history_key][-1].additional_kwargs["attachments"] = attachment_refs

    initial_state["attachment_summaries"] = attachment_summaries
    initial_state["image_refs"] = attachment_refs      # the photos attached to THIS message, for putting words on one (backend/services/photo_cards.py)
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
        # Aggregate counts about how Sonic itself behaved this turn (no user, no text): backend/utils/self_counters.py.
        turn_counts = new_turn_counts()
        llm_turn = llm_turns.begin(username)  # counts this turn's model calls, and adds them to this person's own count (backend/utils/llm_turns.py)
        if answers_an_opening:
            tally(turn_counts, "initiation_answered")
            if answered_kind == "interest":
                tally(turn_counts, "initiation_interest_answered")
        if answers_curiosity:
            tally(turn_counts, "curiosity_taken")
        if answers_shelf:
            tally(turn_counts, "shelf_taken")

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

        budget_view = budget_guard.public_view(budget_state, is_admin_user)
        if budget_view["level"] in (budget_guard.NOTICE, budget_guard.THROTTLED, budget_guard.BLOCKED):
            yield f"data: {json.dumps({'event': 'budget', **budget_view})}\n\n"
        if budget_guard.blocked(budget_state):
            # No model call at all. A person who has signalled risk still gets the human support line, in code.
            recent_user_messages = [question, *(m.content for m in chat_sessions[history_key][-10:] if isinstance(m, HumanMessage))]
            crisis = in_crisis(recent_user_messages)
            if crisis:
                log_safety_event(username, None, "quota", KIND_OUTAGE_FALLBACK)
            tally(turn_counts, "turn_failed", "quota")
            await asyncio.to_thread(record_turn_counts, turn_counts)
            llm_turns.finish(llm_turn, "blocked")
            await asyncio.to_thread(llm_usage.flush)
            logger.info("[budget] chat paused by the usage budget (crisis fallback: %s).", crisis)
            yield f"data: {json.dumps({'event': 'final_generation', 'text': budget_guard.blocked_reply(budget_state.get('resets_at'), crisis, budget_state.get('scope'))})}\n\n"
            return

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
                        chat_sessions[history_key].append(stamp(AIMessage(content="_[Stopped by user before responding]_")))
                        await asyncio.to_thread(save_conversation_turn, username, session_id, chat_sessions[history_key])
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
                chat_sessions[history_key].append(stamp(AIMessage(content=card_text)))
                await asyncio.to_thread(save_conversation_turn, username, session_id, chat_sessions[history_key])
                t_first_token = time.perf_counter()
                log_timings(relevance_grade, "card")
                return

            # Single unified Voice Composer: one Sonic Assistant persona for every response
            # path, with only the grounding rules varying by source_type (assembled by
            # formatter_node into voice_payload — see backend/services/agent_workflow.py).
            payload = final_state.get("voice_payload") or {}
            source_type = payload.get("source_type", "kb_strict")
            # Crisis/safety level in force for this conversation (backend/utils/safety_utils.py). While one is
            # active the reply is always a conversational one, whatever route the message took: a person who
            # says they want to die must never get a knowledge-base refusal, a tool report or a data dump.
            safety_state = final_state.get("safety_state")
            risk_level = effective_risk(safety_state, datetime.now(timezone.utc))
            risk_fresh = bool(safety_state) and bool(safety_state.get("escalated")) and int(safety_state.get("turns_since", 1) or 0) == 0
            if risk_level != RISK_NONE and not is_guest_username(username):
                # Nothing proactive for a week after, and for as long as, someone is in a safety context.
                spawn_background_task(asyncio.to_thread(initiation_utils.risk_escalated, username))
            # What this turn's reply must do (work through the people in their life first; a human line only
            # when due), and the people Sonic remembers from earlier conversations to ask about by name.
            safety_plan = plan_safety_turn(
                risk_level, risk_fresh, safety_state, format_history_as_text(messages_state), final_state.get("original_question", question),
                people=await asyncio.to_thread(remembered_support_people, username) if risk_level != RISK_NONE else [],
                has_results=bool(payload.get("data") or documents),
            )
            drops_results = risk_level != RISK_NONE and not safety_plan.keeps_results     # a practical request below the top level keeps what the tools fetched
            if drops_results:
                source_type = "conversational"
            documents_sorted = sorted(documents, key=lambda d: d.metadata.get("priority", False), reverse=True)
            data = "" if drops_results else (payload.get("data") or format_docs(documents_sorted))
            kb_images = collect_kb_images(documents_sorted) if source_type in ("kb_strict", "kb_open") else []
            # Pictures Sonic made this turn (a card with the person's words on their photo) are shown the same way a library picture is.
            kb_images = [*kb_images, *(final_state.get("generated_images") or [])]

            emotional_context = safety_plan.context + build_emotional_context(
                final_state.get("emotional_state"), datetime.now(timezone.utc)
            )
            user_tz = await asyncio.to_thread(get_user_timezone, username)
            today_local = local_today(user_tz)
            # A meeting the person started in THIS conversation with the Start office hours button (backend/utils/meetings.py): its agenda, last notes and prep, for as long as it runs.
            # Never for a guest, and set aside under a safety context (the person comes first); one indexed read when no meeting is running.
            meeting_context = ""
            if not is_guest_username(username) and risk_level == RISK_NONE and source_type == "conversational":
                meeting_context = await asyncio.to_thread(meetings_utils.build_context_for_session, username, session_id, user_tz)
                if meeting_context:
                    tally(turn_counts, "meeting_turns")
            # Something they said was coming up, whose day has now passed, asked about once at the start of a
            # conversation (backend/utils/open_loops.py). The same gates as the relatable lines, plus: only at the
            # start of a conversation or after a real pause. It takes the turn's one proactive slot, so it also
            # suppresses a relatable line (and yields to encouragement, below).
            open_loop_entry = None
            if open_loops_utils.open_loops_enabled() and not is_guest_username(username):
                open_loop_entry = await asyncio.to_thread(
                    open_loops_utils.select_followup,
                    lambda: open_loops_utils.load_open_loops(username),
                    today=today_local, now=datetime.now(timezone.utc), last_message_at=last_message_at,
                    source_type=source_type, risk_active=risk_level != RISK_NONE,
                    energy_tier=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)),
                    closing=is_closing_message(question),
                )
            # Something coming up on their calendar (backend/utils/calendar_headsups.py): off until they turn it on, one short line, only at the start of a conversation or at a lull, and never
            # in a meeting, under a safety context, on a closing message or while the emotional layer is holding things down. An event starting within about two hours outranks an open loop; otherwise it yields to it.
            headsup_entry = None
            if (not meeting_context and not is_guest_username(username) and risk_level == RISK_NONE and source_type == "conversational" and not is_closing_message(question)
                    and energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)) == "open"
                    and (headsups_utils.is_return(last_message_at, datetime.now(timezone.utc)) or curiosity_utils.is_lull(question))):
                headsup_entry = await asyncio.to_thread(headsups_utils.headsup_for_turn, username, user_tz, allow_model=budget_guard.allows("calendar_headsups", budget_state))
            if headsup_entry and open_loop_entry:
                if headsup_entry["urgent"]:
                    open_loop_entry = None
                else:
                    headsup_entry = None
            # Occasionally Sonic may mention a habit of its own when what the user said genuinely overlaps with one
            # (backend/utils/relatable_utils.py); silent under a safety or held-down emotional context, on a closing
            # message, on grounded KB answers, and rate-limited.
            relatable_entry = None if (open_loop_entry or headsup_entry) else select_relatable(
                final_state.get("original_question", question), chat_sessions[history_key],
                source_type=source_type, risk_active=risk_level != RISK_NONE,
                energy_tier=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)),
                closing=is_closing_message(question),
            )
            if relatable_entry:
                tally(turn_counts, "relatable_offered")
            # Sonic's own measured track record (backend/utils/self_observations.py): only the approved observations
            # that apply to this turn, and only a few. Nothing under a safety context.
            self_knowledge_lines = await asyncio.to_thread(
                observations_for_turn, final_state.get("original_question", question),
                source_type=source_type, closing=is_closing_message(question), emotional_active=bool(emotional_context),
                risk_active=risk_level != RISK_NONE,
            )
            if self_knowledge_lines:
                tally(turn_counts, "observations_injected")
            # Encouragement for someone voicing self-doubt, only from evidence (what their own memory records as
            # achieved, or what they got through earlier in this conversation); never anything about Sonic's own past
            # (backend/utils/encouragement_utils.py). Silent under a safety context or acute distress, rate-limited,
            # and the memory read only happens once every cheap gate has passed.
            _emotion = final_state.get("emotional_state") or {}
            encouragement_entry = await asyncio.to_thread(
                select_encouragement, final_state.get("original_question", question), chat_sessions[history_key],
                lambda: load_user_facts(username),
                source_type=source_type, risk_active=risk_level != RISK_NONE,
                valence=str(_emotion.get("valence", "neutral")),
                intensity=effective_intensity(_emotion, datetime.now(timezone.utc)),
                closing=is_closing_message(question),
            )
            if encouragement_entry:
                tally(turn_counts, "encouragement_offered")
                open_loop_entry = None  # someone voicing self-doubt gets that, not a "how did it go"
                headsup_entry = None    # nor a calendar line
            if open_loop_entry:
                tally(turn_counts, "open_loop_offered")
            if headsup_entry:
                tally(turn_counts, "headsup_offered")
            # What has changed about Sonic itself (backend/components/sonic_changelog.py): said when asked what's new or
            # what it can do, and only rarely volunteered, never when another proactive item already took the turn.
            self_history = select_self_history(
                final_state.get("original_question", question), chat_sessions[history_key],
                source_type=source_type, risk_active=risk_level != RISK_NONE,
                energy_tier=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)),
                closing=is_closing_message(question),
                proactive_taken=bool(open_loop_entry or headsup_entry or relatable_entry or encouragement_entry),
            )
            if self_history:
                tally(turn_counts, "self_history_offered")
            # "What do you have planned later / what did you do today?": answered from the real schedule and the real run records, in the person's own
            # time (backend/utils/routines.py). Not under a safety context, and not on a closing message.
            routines_context = ""
            if (risk_level == RISK_NONE and source_type == "conversational" and not is_closing_message(question)
                    and routines_utils.asks_about_routines(final_state.get("original_question", question))):
                routines_context = await asyncio.to_thread(
                    lambda: routines_utils.build_routines_context(get_db(), user_tz, is_admin=is_admin_user)
                    + ("" if is_guest_username(username) else user_tasks_utils.build_tasks_context(get_db(), username, user_tz) + meetings_utils.build_schedule_context(username, user_tz))
                )
                tally(turn_counts, "routines_offered")
            # "What are you going to follow up on / do you have anything planned for me?", and the same when asked what it has planned (backend/utils/followups.py):
            # the person's own open loops, quiet goals and Message First setting, said only as what can come up when they next open a conversation. Never for a guest.
            followups_context = ""
            if (risk_level == RISK_NONE and source_type == "conversational" and not is_closing_message(question) and not is_guest_username(username)
                    and (followups_utils.asks_about_followups(final_state.get("original_question", question)) or bool(routines_context))):
                followups_context = await asyncio.to_thread(followups_utils.build_followups_context, username, user_tz)
                if followups_context:
                    tally(turn_counts, "followups_offered")
            # Results of the tasks this person set up (backend/utils/user_tasks.py): mentioned once when they are back, or told when asked. Never for a guest, under a
            # safety context, on a closing message, while the emotional layer is holding things down, or on a turn another proactive item already took.
            task_results_context, task_result_ids = "", []
            if (risk_level == RISK_NONE and source_type == "conversational" and not is_closing_message(question) and not is_guest_username(username) and not open_loop_entry and not meeting_context):
                _asked_results = user_tasks_utils.asks_about_task_results(final_state.get("original_question", question))
                _now = datetime.now(timezone.utc)
                if _asked_results or (user_tasks_utils.is_return(last_message_at, _now) and energy_tier(final_state.get("emotional_state"), _now) == "open"):
                    task_results_context, task_result_ids = await asyncio.to_thread(user_tasks_utils.build_results_context, username, asked=_asked_results)
                    if task_results_context:
                        tally(turn_counts, "task_results_offered")
            # "What did I log this week?": their own progress notes (backend/utils/progress_notes.py), only when asked. Never for a guest or under a safety context.
            progress_notes_context = ""
            if (risk_level == RISK_NONE and source_type == "conversational" and not is_closing_message(question) and not is_guest_username(username)
                    and progress_notes_utils.asks_about_notes(final_state.get("original_question", question))):
                def _read_progress_notes():
                    _db = get_db()
                    if _db is None:
                        return ""
                    _names = {m["id"]: m.get("name", "") for m in meetings_utils.list_meetings(_db, username)}
                    return progress_notes_utils.build_asked_context(
                        progress_notes_utils.list_notes(_db, username, 20)[::-1], progress_notes_utils.count_this_week(_db, username, user_tz), meeting_names=_names)
                progress_notes_context = await asyncio.to_thread(_read_progress_notes)
            # Only when asked "how long have we been talking?": the earliest saved conversation, as a plain fact with no
            # feeling or milestone attached (backend/utils/duration_utils.py). Never for guests, whose "we" is many people.
            duration_context = ""
            if (source_type == "conversational" and risk_level == RISK_NONE and not is_guest_username(username)
                    and duration_utils.asks_how_long(final_state.get("original_question", question))):
                duration_context = duration_utils.build_duration_context(
                    await asyncio.to_thread(duration_utils.conversation_facts, username), datetime.now(timezone.utc), user_tz,
                )
                tally(turn_counts, "duration_offered")
            callback_offered = False
            callback_at_lull = False
            lean_entry = None
            prompt = build_voice_prompt(
                grounding_block=GROUNDING_BLOCKS.get(source_type, KB_STRICT_GROUNDING),
                data=data,
                history=format_history_as_text(messages_state),
                question=final_state.get("original_question", question),
                affiliate_override=get_affiliate_override(requested_affiliate),
                insight=payload.get("insight") or "",
                emotional_context=emotional_context,
                relatable_context=build_relatable_block(relatable_entry),
                encouragement_context=build_encouragement_block(encouragement_entry),
                open_loop_context=open_loops_utils.build_open_loop_block(open_loop_entry, today_local),
                headsup_context=headsups_utils.build_headsup_block(headsup_entry, bool(headsup_entry and headsup_entry.get("discreet"))),
                self_history_context=build_self_history_block(self_history),
                routines_context=routines_context,
                followups_context=followups_context,
                task_results_context=task_results_context,
                progress_notes_context=progress_notes_context,
                meeting_context=meeting_context,
                duration_context=duration_context,
                self_knowledge_context=build_self_knowledge_block(
                    self_knowledge_lines, asks_about_itself(final_state.get("original_question", question))
                ),
                # With an emotional or safety context in force, the time block stays purely factual and defers to
                # it: the energy ceiling is decided there, not by how long they were away.
                time_context=build_time_context(
                    user_tz,
                    last_message_at=last_message_at,
                    defer_to_emotion=bool(emotional_context),
                ),
            )
            library_images = [img for img in kb_images if not img.get("generated")]       # a card Sonic made says what it is in its own facts, and must not be "described"
            if library_images:
                # The composer has no innate way to know an image will actually be rendered
                # alongside this response — without this, the model falls back on its default
                # "I'm text-only" assumption and apologizes for a limitation that no longer applies.
                image_names = ", ".join(img["filename"] for img in library_images)
                prompt = prompt + IMAGE_RENDERING_NOTE_TEMPLATE.format(image_names=image_names)

            if kb_images:
                yield f"data: {json.dumps({'event': 'kb_images', 'images': kb_images})}\n\n"

            # Announce LLM generation start
            yield f"data: {json.dumps({'event': 'node_progress', 'node': 'formatter_node', 'title': 'Formatting output structure...', 'detail': f'Synthesizing final answer for {question[:30]}...'})}\n\n"
            # Everything below that reads the database or the embeddings API is synchronous; run directly on the event loop, one slow read
            # froze the whole server (a 4 MB fact list took 41 s), so each goes to a worker thread. The fact list is read once here, which
            # also fills the in-memory copy the calls after it use (backend/utils/memory_utils.py).
            await asyncio.to_thread(load_user_facts, username)
            guardrail_context = await asyncio.to_thread(fetch_relevant_corrections, username, question)
            # Computed once and threaded through both fact + semantic memory recall below — they
            # embed the identical question string this turn, and re-embedding it a second time
            # (the semantic recall call, further down) would be a genuinely free duplicate
            # network round trip to the embeddings API for no behavior change.
            question_embedding = await asyncio.to_thread(embed_text, question) if question else None
            memory_context = await asyncio.to_thread(lambda: fetch_relevant_user_facts(username, question, precomputed_embedding=question_embedding))
            goal_nudge_context = "" if (risk_level != RISK_NONE or open_loop_entry) else await asyncio.to_thread(fetch_goal_nudge_context, username)

            if guardrail_context:
                tally(turn_counts, "guardrail_applied")
                prompt = prompt + guardrail_context
                # Emit trace event to frontend execution trace drawer!
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'self_correction_guardrail', 'title': 'Applying Lessons Learned Guardrail', 'detail': f'Injected past failure constraint into context prompt.'})}\n\n"

            profile_block = profile_utils.build_profile_block(user_profile)
            if profile_block:
                prompt = prompt + profile_block
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

            prompt = prompt + paperwork_utils.build_paperwork_block(question, bool(attachment_docs), [d.page_content for d in attachment_docs])

            if source_type == "conversational":
                semantic_memory_context = await recall_with_second_look(
                    services.get("user_memory_vector_store"), username, question,
                    precomputed_embedding=question_embedding,
                    context_query=context_query_for(chat_sessions[history_key], question),
                )
                if semantic_memory_context:
                    prompt = prompt + semantic_memory_context
                    # Invites one explicit connection to what was recalled (backend/utils/shared_history.py); quiet under
                    # the same gates as the other proactive items, and rate-limited.
                    # At a lull a callback is the conversation's one extender (backend/utils/curiosity.py): it follows the same once-per-
                    # conversation and per-person limits, and is framed as something to pick back up, not a link to what they just said.
                    callback_lull = curiosity_utils.is_lull(question)
                    callback_slot_ok = (not callback_lull) or (
                        profile_utils.offers_allowed(user_profile)
                        and curiosity_utils.slot_open(question, chat_sessions[history_key], source_type=source_type, risk_active=False,
                                                  energy_tier="open", proactive_taken=False)
                        and not curiosity_utils.last_reply_asked(chat_sessions[history_key])
                        and curiosity_utils.gap_clear(await asyncio.to_thread(curiosity_utils.last_offer_at, username), datetime.now(timezone.utc))
                    )
                    if callback_slot_ok and callback_allowed(
                        True, chat_sessions[history_key], source_type=source_type, risk_active=risk_level != RISK_NONE,
                        energy_tier=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)),
                        closing=is_closing_message(question),
                        proactive_taken=bool(open_loop_entry or headsup_entry or task_results_context or meeting_context or relatable_entry or encouragement_entry or self_history),
                    ):
                        prompt = prompt + build_callback_block(lull=callback_lull)
                        callback_offered = True
                        callback_at_lull = callback_lull
                        tally(turn_counts, "callback_offered")
                    yield f"data: {json.dumps({'event': 'node_progress', 'node': 'user_memory_recall', 'title': 'Recalling relevant memory', 'detail': 'Found semantically relevant past context.'})}\n\n"
            # At a real lull, or when they touch a topic Sonic has read about, offers ONE thing of its own: an item from its reading shelf
            # (backend/utils/reading_shelf.py) or, with nothing to offer, the plain open question (backend/utils/curiosity.py). Both use the
            # turn's one proactive slot, so they only get it when nothing else took it, and share the per-conversation and per-person limits.
            # Asking what it has been reading is answered from the shelf whatever else is going on.
            curiosity_invited = False
            shelf_offer = None
            clarify_entry = None
            # A question about its reading, or a short retry of one ("can you try now?"): see backend/utils/reading_shelf.py.
            reading_asked = shelf_utils.asks_what_reading(question) or shelf_utils.follows_reading_ask(question, shelf_utils.recent_user_text(chat_sessions[history_key]))
            if (source_type != "conversational" and not is_guest_username(username) and risk_level == RISK_NONE and shelf_utils.shelf_enabled()
                    and reading_asked and get_db() is not None):
                # Asked about its reading on a turn the router sent to a tool or search path: the shelf still answers, otherwise the model improvises from the
                # tools it does have ("check the admin endpoint", "the logs show nothing") and denies the reading it really did.
                shelf_offer = await asyncio.to_thread(shelf_utils.offer_for_turn, get_db(), username, question, listing=True, topic_ok=False, lull_ok=False)
                prompt = prompt + shelf_utils.build_listing_block(shelf_offer)
            if source_type == "conversational" and not is_guest_username(username) and risk_level == RISK_NONE:
                slot = dict(
                    source_type=source_type, risk_active=False,
                    energy_tier=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)),
                    proactive_taken=bool(open_loop_entry or headsup_entry or task_results_context or meeting_context or relatable_entry or encouragement_entry or self_history
                                         or callback_offered or goal_nudge_context),
                )
                # A getting-to-know-you question (backend/utils/clarifications.py): takes the turn's one slot, so nothing below stacks with it.
                clarify_level = clarify_utils.level_of(user_profile)
                if (clarify_utils.clarify_enabled() and clarify_level != clarify_utils.LESS and not slot["proactive_taken"] and slot["energy_tier"] == "open"
                        and not is_closing_message(question) and not curiosity_utils.last_reply_asked(chat_sessions[history_key]) and get_db() is not None
                        and not reading_asked):
                    clarify_entry = await asyncio.to_thread(clarify_utils.select_question, get_db(), username, clarify_level, datetime.now(timezone.utc))
                    if clarify_entry:
                        parent_text = await asyncio.to_thread(
                            lambda: clarify_utils.parent_text_for(load_user_facts(username), clarify_entry.get("parent_fact_id")))
                        prompt = prompt + clarify_utils.build_question_block(clarify_entry, parent_text)
                        slot["proactive_taken"] = True
                asked_listing = shelf_utils.shelf_enabled() and (reading_asked or shelf_utils.asks_about_day(question))
                offers = profile_utils.offers_allowed(user_profile)  # the person's own switch for Sonic bringing something up by itself
                topic_ok = offers and shelf_utils.shelf_enabled() and shelf_utils.topic_for(question) is not None and curiosity_utils.slot_open(
                    question, chat_sessions[history_key], min_turns=2, **slot)
                lull_ok = offers and curiosity_utils.curiosity_allowed(question, chat_sessions[history_key], **slot)
                # A softer lull (a short reaction like "huh, that makes sense now") may also bring up a shelf item, never the plain question, and only after its own
                # longer gap since the last shelf item this person was offered (backend/utils/reading_shelf.py soft_gap_clear).
                soft_ok = bool(not lull_ok and offers and shelf_utils.shelf_enabled() and shelf_utils.soft_lulls_enabled() and curiosity_utils.is_soft_lull(question)
                               and curiosity_utils.slot_open(question, chat_sessions[history_key], **slot)
                               and not curiosity_utils.last_reply_asked(chat_sessions[history_key]))
                db_for_shelf = get_db()
                if asked_listing and db_for_shelf is not None:
                    shelf_offer = await asyncio.to_thread(
                        shelf_utils.offer_for_turn, db_for_shelf, username, question, listing=True, topic_ok=False, lull_ok=False,
                        framing="" if reading_asked else "day")
                elif (topic_ok or lull_ok or soft_ok) and curiosity_utils.gap_clear(
                        await asyncio.to_thread(curiosity_utils.last_offer_at, username), datetime.now(timezone.utc)):
                    if soft_ok and not (topic_ok or lull_ok) and db_for_shelf is not None:
                        soft_ok = shelf_utils.soft_gap_clear(await asyncio.to_thread(shelf_utils.last_shelf_offer_at, db_for_shelf, username))
                    if db_for_shelf is not None and (topic_ok or lull_ok or soft_ok):
                        shelf_offer = await asyncio.to_thread(
                            shelf_utils.offer_for_turn, db_for_shelf, username, question, listing=False, topic_ok=bool(topic_ok), lull_ok=bool(lull_ok),
                            soft_ok=soft_ok, context=shelf_utils.recent_user_text(chat_sessions[history_key]),
                        )
                    if shelf_offer and shelf_offer["kind"] != "listing" and profile_utils.touches_avoided(
                            f"{shelf_offer['item'].get('title', '')} {shelf_offer['item'].get('summary', '')} {shelf_offer['item'].get('topic', '')}", user_profile):
                        shelf_offer = None  # the person asked not to have this subject brought up
                    if shelf_offer is None and lull_ok:
                        # Nothing on the shelf this person has not seen and no earlier conversation to pick back up: one of Sonic's own leanings
                        # (curated, said once to each person), and only with none left, the plain open question.
                        from backend.components.sonic_profile import INTERESTS as _LEANINGS

                        avoided_leanings = [e["id"] for e in _LEANINGS if profile_utils.touches_avoided(e["line"], user_profile)]
                        lean_entry = curiosity_utils.pick_lean(list(await asyncio.to_thread(curiosity_utils.lean_seen, username)) + avoided_leanings)
                        if lean_entry:
                            prompt = prompt + curiosity_utils.build_lean_block(lean_entry)
                        else:
                            prompt = prompt + curiosity_utils.build_curiosity_block()
                            curiosity_invited = True
                if shelf_offer:
                    prompt = prompt + (shelf_utils.build_listing_block(shelf_offer) if shelf_offer["kind"] == "listing" else shelf_utils.build_shelf_block(shelf_offer))
            # A clearly closing message ("thanks!", "goodnight") gets a directive to close the conversation
            # instead of fishing for more (backend/utils/wrapup_utils.py); the persona carries the general rule.
            closing_turn = source_type == "conversational" and risk_level == RISK_NONE and is_closing_message(question)
            if closing_turn:
                prompt = prompt + CLOSING_DIRECTIVE
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
                    chat_sessions[history_key].append(stamp(AIMessage(content=stopped_note)))
                    await asyncio.to_thread(save_conversation_turn, username, session_id, chat_sessions[history_key])
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

            # 4b. REPLY-FIT CHECKS — the reply was written with guidance (what this person seems to want from
            # it, or that a closing message should be closed rather than extended), but guidance is only
            # prose. These verify it was honored (backend/utils/emotion_checks.py and wrapup_utils.py) and
            # regenerate once if not. Conversational replies only; the data-grounded paths are judged by the
            # reward evaluator.
            tally(turn_counts, "route", source_type)
            if source_type == "conversational":
                tally(turn_counts, "reply_fit_checked")
                revision_prompt, revision_tag = None, None
                safety_revised, last_resort_line = False, False
                safety_issue = safety_plan.reply_issue(full_response)
                emotional_issue = None if safety_plan.active else emotional_reply_issue(final_state.get("emotional_state"), full_response)
                # Sonic's identity boundary (backend/utils/identity_checks.py): no claimed feelings, no stake in
                # whether the person stays or comes back. Not applied while a safety context is in force, which has
                # its own rules for what the reply must do.
                identity_issue = None if safety_plan.active else identity_reply_issue(full_response)
                if identity_issue is None and not safety_plan.active:
                    # An invented read, or a verdict on a piece on the reading shelf: also a matter of what Sonic is (backend/utils/reading_shelf.py).
                    identity_issue = shelf_utils.shelf_reply_issue(full_response, shelf_offer is not None)
                encouragement_issue = (
                    encouragement_reply_issue(full_response, encouragement_entry)
                    if encouragement_entry and not safety_plan.active else None
                )
                if safety_issue:
                    revision_prompt, _ = build_safety_revision_prompt(prompt, full_response, safety_issue)
                    revision_tag = "safety"
                    revision_title = "Making sure the reply is right for this moment"
                elif emotional_issue:
                    revision_prompt, _ = build_emotional_revision_prompt(prompt, full_response, emotional_issue)
                    revision_tag = emotional_issue
                    revision_title = "Adjusting the reply to fit what you need"
                elif identity_issue:
                    revision_prompt, _ = build_identity_revision_prompt(prompt, full_response, identity_issue)
                    revision_tag = "identity"
                    revision_title = "Keeping the reply honest about what I am"
                elif encouragement_issue:
                    revision_prompt, _ = build_encouragement_revision_prompt(prompt, full_response, encouragement_issue)
                    revision_tag = "encouragement"
                    revision_title = "Keeping the encouragement honest"
                elif closing_turn:
                    closing_issue = closing_reply_issue(full_response)
                    if closing_issue:
                        revision_prompt, _ = build_closing_revision_prompt(prompt, full_response, closing_issue)
                        revision_tag = "closing_engagement"
                        revision_title = "Wrapping up the reply"
                if revision_prompt:
                    logger.info("Reply-fit check failed (%s); regenerating once.", revision_tag)
                    tally(turn_counts, "revised", revision_kind(revision_tag))
                    yield f"data: {json.dumps({'event': 'node_progress', 'node': 'reply_fit_check', 'title': revision_title, 'detail': revision_tag})}\n\n"
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
                    logger.info("Reply-fit revision (%s) — rejected draft %d chars, revised %d chars.", revision_tag, len(rejected_draft), len(full_response))
                    safety_revised = revision_tag == "safety"
                # The one element that must not depend on the model getting it right: when a human line is due
                # (imminent danger, the people in their life exhausted, or they asked) the reply always names a
                # real one, added in code if the rewrite still left it out.
                if safety_plan.needs_resource_line(full_response):
                    full_response += CRISIS_RESOURCE_LINE
                    last_resort_line = True
                    yield f"data: {json.dumps({'event': 'token', 'text': CRISIS_RESOURCE_LINE})}\n\n"
                if risk_level != RISK_NONE:
                    # How the safety layer is doing, as counts only (no message text, no names): see safety_stats.py.
                    await asyncio.to_thread(
                        record_safety_turn, username, risk_level, safety_revised, has_crisis_resource(full_response), last_resort_line
                    )

            # 5. REWARD EVALUATOR & SELF-CORRECTION — judges the response itself (not the
            # documents) and regenerates once, with feedback, if it fails. Capped at one retry;
            # whatever comes out is what gets shown, and the failure is logged as a correction
            # either way so future similar questions get a guardrail via fetch_relevant_corrections.
            if source_type in REWARD_EVAL_SOURCE_TYPES and budget_guard.allows("answer_recheck", budget_state):
                verdict = await evaluate_response(prompt, full_response, source_type)
                tally(turn_counts, "reward_evaluated")
                if verdict["verdict"] == "fail":
                    tally(turn_counts, "reward_failed", verdict.get("tag"))
                    logger.info(f"Reward evaluator flagged response ({verdict['tag']}).")
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
                        source="evaluator",
                    )
                    logger.info(
                        "Reward evaluator correction applied — original %d chars, corrected %d chars.",
                        len(original_response), len(full_response),
                    )

            yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"
            ai_message = stamp(AIMessage(content=full_response))
            if relatable_entry:
                ai_message.additional_kwargs[RELATABLE_MARK] = True  # the rate limit reads this on later turns
            if encouragement_entry:
                ai_message.additional_kwargs[ENCOURAGEMENT_MARK] = True
            if callback_offered:
                ai_message.additional_kwargs[CALLBACK_MARK] = True
            # Counted and capped on what Sonic actually did, not on what it was invited to do.
            if curiosity_invited and curiosity_utils.asked_a_question(full_response):
                ai_message.additional_kwargs[curiosity_utils.CURIOSITY_MARK] = True
                tally(turn_counts, "curiosity_offered")
                await asyncio.to_thread(curiosity_utils.record_offer, username)
            if callback_at_lull and not is_guest_username(username):
                # A callback offered at a lull spent the one extender (it is counted as a callback above).
                ai_message.additional_kwargs[curiosity_utils.CURIOSITY_MARK] = True
                await asyncio.to_thread(curiosity_utils.record_offer, username)
            if lean_entry and curiosity_utils.lean_used(full_response, lean_entry):
                ai_message.additional_kwargs[curiosity_utils.CURIOSITY_MARK] = True
                tally(turn_counts, "lean_offered")
                await asyncio.to_thread(curiosity_utils.record_offer, username)
                await asyncio.to_thread(curiosity_utils.mark_lean, username, lean_entry["id"])
            if shelf_offer and shelf_offer["kind"] == "listing":
                tally(turn_counts, "shelf_listed")
            elif shelf_offer and shelf_utils.reply_used_item(full_response, shelf_offer["item"]):
                # Same: the shelf item is spent, for this conversation and for this person, only when the reply really brought it up.
                ai_message.additional_kwargs[curiosity_utils.CURIOSITY_MARK] = True
                ai_message.additional_kwargs[shelf_utils.SHELF_MARK] = True
                tally(turn_counts, "shelf_offered")
                await asyncio.to_thread(curiosity_utils.record_offer, username)
                await asyncio.to_thread(shelf_utils.mark_offered, get_db(), username, shelf_offer["item"]["_id"])
            if clarify_entry:
                # A getting-to-know-you question is recorded as asked only if the reply really asked something; otherwise it waits for one more try.
                if curiosity_utils.asked_a_question(full_response):
                    ai_message.additional_kwargs[clarify_utils.CLARIFY_MARK] = clarify_entry["id"]
                    tally(turn_counts, "clarify_asked")
                    await asyncio.to_thread(clarify_utils.set_status, get_db(), username, clarify_entry["id"], clarify_utils.ASKED, datetime.now(timezone.utc))
                else:
                    await asyncio.to_thread(clarify_utils.bump_tries, get_db(), username, clarify_entry["id"], datetime.now(timezone.utc))
            if self_history and self_history["mode"] == "volunteer":
                ai_message.additional_kwargs[SELF_HISTORY_MARK] = True  # only a volunteered mention is rate-limited
            if mentions_itself(full_response):
                tally(turn_counts, "self_mention")
            if kb_images:
                ai_message.additional_kwargs["kb_images"] = kb_images
            chat_sessions[history_key].append(ai_message)
            await asyncio.to_thread(save_conversation_turn, username, session_id, chat_sessions[history_key])
            # A false "I can't edit files" reply would otherwise be remembered and recalled into
            # later prompts as if it were a fact (a real memory chunk did exactly that).
            if budget_guard.allows("memory_summaries", budget_state) and not (folder_connected and is_stale_capability_denial(full_response)):
                spawn_background_task(index_conversation_turn(services.get("user_memory_vector_store"), username, question, full_response, session_id))
            # The reading shelf is topped up lazily, when a conversation starts: one search, a few page reads and two small model calls, only
            # when it is due (backend/services/shelf_refresh.py). After the reply is on its way, so it never slows one.
            if len(chat_sessions[history_key]) <= 2 and get_db() is not None and budget_guard.allows("shelf_refresh", budget_state):
                from backend.models.models import lite_llm as shelf_llm

                spawn_background_task(shelf_refresh.maybe_refresh(get_db(), shelf_llm.ainvoke))
            # Asked once: an offered loop is closed for good, and anything new they said is noted for later.
            if open_loop_entry:
                await asyncio.to_thread(open_loops_utils.mark_asked, username, open_loop_entry["id"])
            if headsup_entry and headsups_utils.was_mentioned(headsup_entry, full_response):    # recorded only if the reply really said it
                await asyncio.to_thread(headsups_utils.mark_mentioned, username, headsup_entry["event_id"], headsup_entry["phase"])
            if task_result_ids:
                await asyncio.to_thread(user_tasks_utils.mark_announced, username, task_result_ids)
            if open_loops_utils.open_loops_enabled() and not is_guest_username(username) and budget_guard.allows("open_loops", budget_state):
                spawn_background_task(open_loops_utils.capture_open_loops(
                    username, question, user_tz, risk_active=risk_level != RISK_NONE, previous_reply=previous_reply,
                ))
            # Getting-to-know-you questions (backend/utils/clarifications.py): what they said after being asked is the answer (saved as a fact and linked to what it
            # belongs under), and anything new about their own life is noted for one later question. Both off the response path, and only for someone who wants them.
            if clarify_utils.clarify_enabled() and not is_guest_username(username) and get_db() is not None:
                if clarify_reply_to:
                    from backend.models.models import lite_llm as clarify_llm

                    spawn_background_task(clarify_utils.apply_reply(
                        username, str(clarify_reply_to), question, risk_active=risk_level != RISK_NONE, invoke=clarify_llm.ainvoke,
                        memory_vector_store=services.get("user_memory_vector_store"),
                    ))
                elif clarify_utils.level_of(user_profile) != clarify_utils.LESS and budget_guard.allows("getting-to-know-you questions", budget_state):
                    spawn_background_task(clarify_utils.capture(
                        username, question, level=clarify_utils.level_of(user_profile), previous_reply=previous_reply, risk_active=risk_level != RISK_NONE,
                    ))
            # Bounds this conversation's checkpoint history right now rather than waiting for the daily pass.
            spawn_background_task(prune_thread_checkpoints_async(history_key))
            await asyncio.to_thread(record_turn_counts, turn_counts)
            llm_turns.finish(llm_turn)
            await asyncio.to_thread(llm_usage.flush)  # what this turn's model calls used (backend/utils/llm_usage.py)
            log_timings(relevance_grade, "ok")
            logger.info("--- End of token stream ---")

        except Exception as e:
            logger.error(f"[x] Error in token_streamer loop context: {e}", exc_info=True)
            log_timings(final_state.get("relevance_grade", "unknown"), "error")
            # A spent cap or an overloaded provider used to reach the user as a raw exception string in a trace
            # line and no reply at all. They now get a plain-language reply (never the exception text), and a
            # person who had signalled risk gets a human line even though the model is down.
            failed = handle_failed_turn(
                e, username,
                [question, *(m.content for m in chat_sessions[history_key][-10:] if isinstance(m, HumanMessage))],
                final_state.get("safety_state"),
            )
            tally(turn_counts, "turn_failed", failed.kind)
            await asyncio.to_thread(record_turn_counts, turn_counts)
            llm_turns.finish(llm_turn, "error")
            await asyncio.to_thread(llm_usage.flush)
            yield f"data: {json.dumps({'event': 'trace', 'title': 'Execution error', 'detail': 'The request failed on the server.', 'status': 'active'})}\n\n"
            if not full_response:
                yield f"data: {json.dumps({'event': 'final_generation', 'text': failed.reply})}\n\n"
        finally:
            llm_turns.finish(llm_turn)  # a turn the client walked away from still counts (only the first finish does)

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
        await asyncio.to_thread(save_conversation_turn, username, session_id, [])
        await _delete_checkpoint_thread(username, session_id)
        await asyncio.to_thread(delete_session_attachments, username, session_id)
        return {"status": "cleared", "count": 1}

    # No specific conversation given: wipe every conversation this user has.
    conversations = await asyncio.to_thread(list_user_conversation_summaries, username)  # ids only, not every message
    for convo in conversations:
        await asyncio.to_thread(delete_user_conversation, username, convo["session_id"])
        await _delete_checkpoint_thread(username, convo["session_id"])
        await asyncio.to_thread(delete_session_attachments, username, convo["session_id"])
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

    # 3. Flexible lookup against GridFS: every file that matches the name, newest first, and the first of them THIS person may read. The same name can exist in several
    # libraries, and a name alone must never be enough (backend/utils/stored_file_access.py); what they may not read is reported exactly like a file that is not there.
    candidates = list(db["fs.files"].find({
        "$or": [
            {"filename": decoded_filename},
            {"filename": clean_basename},
            {"filename": {"$regex": f"^{re.escape(clean_basename)}$", "$options": "i"}},
            {"metadata.filename": clean_basename}
        ]
    }).sort("uploadDate", -1).limit(25))
    file_doc = first_readable(candidates, current_user.get("sub"), load_directory())

    if not file_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{clean_basename}' not found in database repository."
        )

    # 4. Stream from GridFS bucket using matched document ID
    grid_out = gridfs_bucket.open_download_stream(file_doc["_id"])

    safe_filename = urllib.parse.quote(clean_basename)
    # A citation link opens the file itself: JSON is served as JSON, everything else keeps the PDF type it has always had.
    media_type = "application/json" if clean_basename.lower().endswith((".json", ".jsonl", ".ndjson")) else "application/pdf"
    return StreamingResponse(
        grid_out,
        media_type=media_type,
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
    # A photo someone attached, or a card made from one, is theirs alone: another signed-in person who somehow had its id gets the same answer as for a missing file.
    if not can_read_stored_file(file_doc, current_user.get("sub"), load_directory()):
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
        # The knowledge-graph edges that file produced go with it (backend/state/graph_db.py)
        for edge_source in {raw_filename, base_filename}:
            graph_db.delete_source_edges(db, edge_source, affiliate)
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
    return await asyncio.to_thread(list_user_conversation_summaries, username)

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
    page = await asyncio.to_thread(load_user_conversation_page, username, session_id, limit, before)
    if page is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return page

@app.delete("/api/conversations/{session_id}")
async def remove_conversation(session_id: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    chat_sessions.pop(f"{username}::{session_id}", None)
    if not await asyncio.to_thread(delete_user_conversation, username, session_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _delete_checkpoint_thread(username, session_id)
    await asyncio.to_thread(delete_session_attachments, username, session_id)
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
        rating=payload.rating or "negative",   # the page sends "like" or "dislike"; save_correction stores positive or negative
        source="user",
    )
    logger.info(f"[+] Correction stored for {username} (Tag: {payload.tag}).")
    return {"status": "success", "message": "Feedback indexed for dynamic self-correction."}


@app.get("/api/memory")
async def list_memory(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    facts = await asyncio.to_thread(load_user_facts, username)
    now = datetime.now(timezone.utc)
    by_id = {f.id: f for f in facts}
    # fact_for_state leaves out the embedding (about 15 KB a fact, never shown on the page); `related_facts` is what each note is linked with.
    return [
        {**fact_for_state(f), "effective_confidence": effective_confidence(f, now), "related_facts": linked_fact_summaries(f, by_id)}
        for f in facts
    ]


class DocxRender(BaseModel):
    document: Dict[str, Any]


@app.post("/api/local-edits/render-docx")
async def render_local_docx(payload: DocxRender, current_user = Depends(get_current_user)):
    """The real .docx for a document Sonic proposed, rendered only when the person presses Apply; the browser then writes it into their folder."""
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Sign in to create documents in your folder.")
    clean, problems = validate_document(payload.document)
    if clean is None:
        raise HTTPException(status_code=400, detail="; ".join(problems))
    data = await asyncio.to_thread(render_docx, clean)
    return Response(content=data, media_type=DOCX_MIME)


class DocxEditRequest(BaseModel):
    docx_base64: str
    word_edits: List[Dict[str, Any]]


@app.post("/api/local-edits/edit-docx")
async def edit_local_docx(payload: DocxEditRequest, current_user = Depends(get_current_user)):
    """An edited COPY of a Word document, from the document's current bytes (sent by the browser when the person presses Apply) and the edits Sonic proposed.
    The original is never written; every part but the named paragraphs is checked to be unchanged before anything is returned (backend/utils/docx_edit.py)."""
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Sign in to edit documents in your folder.")
    try:
        original = base64.b64decode(payload.docx_base64, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="That document could not be read.")
    try:
        data = await asyncio.to_thread(apply_docx_edits, original, payload.word_edits)
    except DocxEditError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return Response(content=data, media_type=DOCX_MIME)


class MemoryEdit(BaseModel):
    fact: str
    category: Optional[str] = None


@app.put("/api/memory/{fact_id}")
async def edit_memory_fact(fact_id: str, payload: MemoryEdit, current_user = Depends(get_current_user)):
    """The person rewrites a saved note; returns it as the list shows it (with its fresh links)."""
    username = current_user.get("sub")
    try:
        edited = await asyncio.to_thread(edit_user_fact, username, fact_id, payload.fact, payload.category, services.get("user_memory_vector_store"))
    except FactEditError as err:
        raise HTTPException(status_code=400, detail=str(err))
    if edited is None:
        raise HTTPException(status_code=404, detail="Memory fact not found")
    by_id = {f.id: f for f in await asyncio.to_thread(load_user_facts, username)}
    return {**fact_for_state(edited), "effective_confidence": effective_confidence(edited, datetime.now(timezone.utc)), "related_facts": linked_fact_summaries(edited, by_id)}


@app.delete("/api/memory/{fact_id}/links/{other_id}")
async def delete_memory_link(fact_id: str, other_id: str, current_user = Depends(get_current_user)):
    """The person says two notes do not belong together: the link is removed both ways."""
    username = current_user.get("sub")
    removed = await asyncio.to_thread(unlink_user_facts, username, fact_id, other_id)
    if not removed:
        raise HTTPException(status_code=404, detail="That link was not found")
    return {"status": "unlinked", "id": fact_id, "other_id": other_id}


@app.delete("/api/memory/{fact_id}")
async def delete_memory_fact(fact_id: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    deleted = delete_user_fact(username, fact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory fact not found")
    return {"status": "deleted", "id": fact_id}


@app.get("/api/memory/stored")
async def memory_stored(current_user = Depends(get_current_user)):
    """What Sonic keeps besides the facts listed above: how many notes from past conversations, and how many archived copies."""
    return await asyncio.to_thread(memory_erase.stored_counts, get_db(), current_user.get("sub"))


@app.delete("/api/memory")
async def clear_memory(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    result = await asyncio.to_thread(memory_erase.erase_user_memory, get_db(), username)
    await asyncio.to_thread(open_loops_utils.forget_all_loops, username)
    await asyncio.to_thread(user_tasks_utils.forget_all, username)
    await asyncio.to_thread(meetings_utils.forget_all, username)
    await asyncio.to_thread(progress_notes_utils.forget_all, username)
    await asyncio.to_thread(headsups_utils.forget_all, username)
    await asyncio.to_thread(clarify_utils.forget_all, username)
    if result["failed"]:
        # Never report a partial wipe as success: the page says it permanently deletes everything.
        raise HTTPException(status_code=500, detail="Some of your memory could not be deleted. Please try again.")
    return {"status": "cleared", "deleted": result["deleted"], "facts": result["facts"]}


@app.get("/api/open-loops")
async def list_open_loops(current_user = Depends(get_current_user)):
    """Things the user said are coming up, which Sonic may ask about once afterwards."""
    return await asyncio.to_thread(open_loops_utils.upcoming_for_page, current_user.get("sub"))


@app.delete("/api/open-loops/{loop_id}")
async def delete_open_loop(loop_id: str, current_user = Depends(get_current_user)):
    deleted = await asyncio.to_thread(open_loops_utils.forget_loop, current_user.get("sub"), loop_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="That upcoming item was not found")
    return {"status": "deleted", "id": loop_id}


def _tasks_db_or_503():
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Tasks are not available right now.")
    return db


def _tasks_user(current_user) -> str:
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Tasks are for signed-in accounts, so the results have someone to belong to.")
    return username


@app.get("/api/tasks")
async def list_user_tasks(current_user = Depends(get_current_user)):
    """The research tasks this person set up for Sonic, and the limits they work within."""
    username = _tasks_user(current_user)
    db = _tasks_db_or_503()
    now = datetime.now(timezone.utc)
    docs = await asyncio.to_thread(user_tasks_utils.list_tasks, db, username)
    used = await asyncio.to_thread(user_tasks_utils.runs_in_last_day, db, username, now)
    return {
        "tasks": [user_tasks_utils.public_task(d) for d in docs],
        "limits": {"max_tasks": user_tasks_utils.MAX_TASKS_PER_USER, "runs_per_day": user_tasks_utils.MAX_RUNS_PER_USER_PER_DAY, "runs_today": used,
                   "sources": user_tasks_utils.available_sources(), "source_labels": user_tasks_utils.SOURCE_LABELS,
                   # Repository tasks run on the person's OWN GitHub token (backend/services/repo_tasks.py), so the page needs to know whether they have stored one.
                   "kinds": list(user_tasks_utils.KINDS), "kind_labels": user_tasks_utils.KIND_LABELS,
                   "github_connected": bool(await asyncio.to_thread(get_user_github_token, username))},
    }


@app.post("/api/tasks")
async def create_user_task(payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _tasks_user(current_user)
    db = _tasks_db_or_503()
    tz = await asyncio.to_thread(get_user_timezone, username)
    spec, issue = user_tasks_utils.validate_spec({**payload, "tz": tz}, tz)
    if issue:
        raise HTTPException(status_code=400, detail=issue)
    if spec["kind"] in user_tasks_utils.REPO_KINDS and not await asyncio.to_thread(get_user_github_token, username):
        # A repository task reads only what its owner's own key can read, so there has to be one (Integrations). The server's shared token is never used for it.
        raise HTTPException(status_code=400, detail="Add your own GitHub token under Integrations first. These tasks read the repository with your key, so they can only see what you can.")
    task, issue = await asyncio.to_thread(user_tasks_utils.create_task, db, username, spec)
    if issue:
        raise HTTPException(status_code=400, detail=issue)
    return user_tasks_utils.public_task(task)


@app.patch("/api/tasks/{task_id}")
async def update_user_task(task_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _tasks_user(current_user)
    task, issue = await asyncio.to_thread(user_tasks_utils.update_task, _tasks_db_or_503(), username, task_id, payload)
    if issue:
        raise HTTPException(status_code=404 if "isn't there" in issue else 400, detail=issue)
    return user_tasks_utils.public_task(task)


@app.delete("/api/tasks/{task_id}")
async def delete_user_task(task_id: str, current_user = Depends(get_current_user)):
    username = _tasks_user(current_user)
    if not await asyncio.to_thread(user_tasks_utils.delete_task, _tasks_db_or_503(), username, task_id):
        raise HTTPException(status_code=404, detail="That task isn't there.")
    return {"status": "deleted", "id": task_id}


@app.get("/api/tasks/{task_id}/runs")
async def list_user_task_runs(task_id: str, current_user = Depends(get_current_user)):
    """What a task has found, newest first. Opening them is what clears the "new" mark."""
    username = _tasks_user(current_user)
    db = _tasks_db_or_503()
    if await asyncio.to_thread(user_tasks_utils.get_task, db, username, task_id) is None:
        raise HTTPException(status_code=404, detail="That task isn't there.")
    runs = await asyncio.to_thread(user_tasks_utils.runs_for_task, db, username, task_id)
    return {"runs": [user_tasks_utils.public_run(r) for r in runs]}


@app.post("/api/tasks/{task_id}/seen")
async def mark_user_task_seen(task_id: str, current_user = Depends(get_current_user)):
    username = _tasks_user(current_user)
    await asyncio.to_thread(user_tasks_utils.mark_seen, _tasks_db_or_503(), username, task_id)
    return {"status": "ok"}


@app.post("/api/tasks/{task_id}/run")
async def run_user_task_now(task_id: str, current_user = Depends(get_current_user)):
    """Runs one task now, in the background (it counts toward the day's runs). The page checks back for the result."""
    username = _tasks_user(current_user)
    db = _tasks_db_or_503()
    now = datetime.now(timezone.utc)
    if await asyncio.to_thread(user_tasks_utils.runs_in_last_day, db, username, now) >= user_tasks_utils.MAX_RUNS_PER_USER_PER_DAY:
        raise HTTPException(status_code=429, detail=f"You've used your {user_tasks_utils.MAX_RUNS_PER_USER_PER_DAY} task runs for today. Scheduled runs pick up again tomorrow.")
    state = await asyncio.to_thread(budget_guard.current_state, None, None, username)
    if not budget_guard.allows("user_tasks", state):
        raise HTTPException(status_code=503, detail="Sonic is in low-power mode right now, so tasks are paused for a while.")
    task = await asyncio.to_thread(user_tasks_utils.claim_task, db, username, task_id, now)
    if task is None:
        raise HTTPException(status_code=409, detail="That task is missing or already running.")
    from backend.models.models import lite_llm as task_llm

    async def _run():
        try:
            await user_task_scheduler.run_one(db, task, task_llm.ainvoke, "manual")
        except Exception:
            logger.warning("[tasks] a manual run failed.", exc_info=True)
            await asyncio.to_thread(user_tasks_utils.reschedule, db, task, datetime.now(timezone.utc))

    spawn_background_task(_run())
    return {"status": "started"}


def _meetings_db_or_503():
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Meetings are not available right now.")
    return db


def _meetings_user(current_user) -> str:
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Meetings are for signed-in accounts, so the notes have someone to belong to.")
    return username


@app.get("/api/meetings")
async def list_user_meetings(current_user = Depends(get_current_user)):
    """The meetings this person has with Sonic (backend/utils/meetings.py)."""
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    tz = await asyncio.to_thread(get_user_timezone, username)
    docs = await asyncio.to_thread(lambda: meetings_utils.decorate(db, username, meetings_utils.list_meetings(db, username), tz))
    return {"meetings": [meetings_utils.public_meeting(d) for d in docs], "limits": {"max_meetings": meetings_utils.MAX_MEETINGS}}


@app.get("/api/meetings/state")
async def meetings_state(session_id: str = Query(""), current_user = Depends(get_current_user)):
    """For the chat header: the person's meetings and the one running in this conversation (if any). Nothing here reads the calendar: a meeting can be started any time."""
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    tz = await asyncio.to_thread(get_user_timezone, username)
    docs = await asyncio.to_thread(lambda: meetings_utils.decorate(db, username, meetings_utils.list_meetings(db, username), tz))
    if not docs:
        return {"meetings": [], "active": None}
    run = await asyncio.to_thread(meetings_utils.active_run, db, username, session_id) if session_id else None
    active = None
    if run:
        meeting = next((d for d in docs if d["id"] == run["meeting_id"]), None)
        active = {"run_id": run["id"], "meeting_id": run["meeting_id"], "name": meeting.get("name", "")} if meeting else None
    return {"meetings": [{"id": d["id"], "name": d["name"], "due_today": d["due_today"], "schedule": d["schedule"]} for d in docs], "active": active}


@app.post("/api/meetings")
async def create_user_meeting(payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    spec, issue = meetings_utils.validate_meeting({**payload, "tz": await asyncio.to_thread(get_user_timezone, username)})
    if issue:
        raise HTTPException(status_code=400, detail=issue)
    meeting, issue = await asyncio.to_thread(meetings_utils.create_meeting, _meetings_db_or_503(), username, spec)
    if issue:
        raise HTTPException(status_code=400, detail=issue)
    return meetings_utils.public_meeting(meeting)


@app.patch("/api/meetings/notes/{note_id}")
async def update_user_meeting_note(note_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    note = await asyncio.to_thread(meetings_utils.update_note, _meetings_db_or_503(), username, note_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="That note isn't there.")
    return meetings_utils.public_note(note)


@app.delete("/api/meetings/notes/{note_id}")
async def delete_user_meeting_note(note_id: str, current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    if not await asyncio.to_thread(meetings_utils.delete_note, _meetings_db_or_503(), username, note_id):
        raise HTTPException(status_code=404, detail="That note isn't there.")
    return {"status": "deleted", "id": note_id}


@app.patch("/api/meetings/{meeting_id}")
async def update_user_meeting(meeting_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    meeting, issue = await asyncio.to_thread(meetings_utils.update_meeting, _meetings_db_or_503(), username, meeting_id, payload)
    if issue:
        raise HTTPException(status_code=404 if "isn't there" in issue else 400, detail=issue)
    return meetings_utils.public_meeting(meeting)


@app.delete("/api/meetings/{meeting_id}")
async def delete_user_meeting(meeting_id: str, current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    if not await asyncio.to_thread(meetings_utils.delete_meeting, _meetings_db_or_503(), username, meeting_id):
        raise HTTPException(status_code=404, detail="That meeting isn't there.")
    return {"status": "deleted", "id": meeting_id}


@app.get("/api/meetings/{meeting_id}/notes")
async def list_user_meeting_notes(meeting_id: str, current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    if await asyncio.to_thread(meetings_utils.get_meeting, db, username, meeting_id) is None:
        raise HTTPException(status_code=404, detail="That meeting isn't there.")
    notes = await asyncio.to_thread(meetings_utils.notes_for_meeting, db, username, meeting_id)
    return {"notes": [meetings_utils.public_note(n) for n in notes]}


@app.get("/api/meetings/{meeting_id}/progress-notes")
async def list_progress_notes(meeting_id: str, current_user = Depends(get_current_user)):
    """What this person logged to one meeting (backend/utils/progress_notes.py), newest first."""
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    if await asyncio.to_thread(meetings_utils.get_meeting, db, username, meeting_id) is None:
        raise HTTPException(status_code=404, detail="That meeting isn't there.")
    docs = await asyncio.to_thread(progress_notes_utils.list_notes, db, username, progress_notes_utils.LIST_LIMIT, meeting_id)
    return {"notes": [progress_notes_utils.public_note(d) for d in docs],
            "limits": {"max_text": progress_notes_utils.MAX_TEXT_CHARS, "max_per_day": progress_notes_utils.MAX_PER_DAY}}


@app.post("/api/meetings/{meeting_id}/progress-notes")
async def add_progress_note(meeting_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    if await asyncio.to_thread(meetings_utils.get_meeting, db, username, meeting_id) is None:
        raise HTTPException(status_code=404, detail="That meeting isn't there.")
    tz = await asyncio.to_thread(get_user_timezone, username)
    note, issue = await asyncio.to_thread(
        lambda: progress_notes_utils.add_note(db, username, payload.get("text"), str(payload.get("label") or "note"), "page", tz, meeting_id=meeting_id))
    if issue:
        raise HTTPException(status_code=400, detail=issue)
    return progress_notes_utils.public_note(note)


@app.patch("/api/progress-notes/{note_id}")
async def update_progress_note(note_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    note = await asyncio.to_thread(progress_notes_utils.update_note, _meetings_db_or_503(), username, note_id, payload)
    if note is None:
        raise HTTPException(status_code=404, detail="That note was not found, or the change was not valid.")
    return progress_notes_utils.public_note(note)


@app.delete("/api/progress-notes/{note_id}")
async def delete_progress_note(note_id: str, current_user = Depends(get_current_user)):
    username = _meetings_user(current_user)
    if not await asyncio.to_thread(progress_notes_utils.delete_note, _meetings_db_or_503(), username, note_id):
        raise HTTPException(status_code=404, detail="That note was not found.")
    return {"status": "deleted"}


@app.post("/api/meetings/{meeting_id}/start")
async def start_user_meeting(meeting_id: str, payload: dict = Body(...), current_user = Depends(get_current_user)):
    """Starts office hours in a conversation: only ever from the person pressing the button."""
    username = _meetings_user(current_user)
    run, issue = await asyncio.to_thread(meetings_utils.start_run, _meetings_db_or_503(), username, meeting_id, str(payload.get("session_id") or ""))
    if issue:
        raise HTTPException(status_code=404 if "isn't there" in issue else 400, detail=issue)
    return {"run_id": run["id"], "meeting_id": meeting_id}


@app.post("/api/meetings/runs/{run_id}/end")
async def end_user_meeting(run_id: str, current_user = Depends(get_current_user)):
    """Ends the meeting and saves its notes (a small model call, skipped in low-power mode)."""
    username = _meetings_user(current_user)
    db = _meetings_db_or_503()
    run = await asyncio.to_thread(meetings_utils.get_run, db, username, run_id)
    if run is None or run.get("status") != meetings_utils.ACTIVE:
        raise HTTPException(status_code=404, detail="That meeting isn't running.")
    meeting = await asyncio.to_thread(meetings_utils.get_meeting, db, username, run["meeting_id"])
    await asyncio.to_thread(meetings_utils.end_run, db, username, run_id)
    note, reason = None, None
    if meeting is not None:
        state = await asyncio.to_thread(budget_guard.current_state, None, None, username)
        if not budget_guard.allows("meeting_notes", state):
            reason = "low_power"
        else:
            session_id = run.get("session_id") or ""
            messages = chat_sessions.get(f"{username}::{session_id}")
            if messages is None:
                messages = await asyncio.to_thread(load_session_messages, username, session_id)
            from backend.models.models import lite_llm as note_llm

            note = await asyncio.to_thread(meetings_utils.write_note, db, username, run, meeting, list(messages), note_llm.invoke)
            reason = None if note else "nothing_to_note"
    return {"status": "ended", "note": meetings_utils.public_note(note) if note else None, "reason": reason}


def _headsups_user(current_user) -> str:
    username = current_user.get("sub")
    if is_guest_username(username):
        raise HTTPException(status_code=403, detail="Calendar heads-ups are for signed-in accounts.")
    return username


@app.get("/api/calendar-headsups")
async def get_calendar_headsups(current_user = Depends(get_current_user)):
    """The person's three switches, and whether a calendar is connected (the feature needs one)."""
    username = _headsups_user(current_user)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Calendar heads-ups are not available right now.")
    settings = await asyncio.to_thread(headsups_utils.get_settings, db, username)
    status = await asyncio.to_thread(get_calendar_connection_status, username)
    return {"settings": settings, "connected": bool(status.get("connected"))}


@app.put("/api/calendar-headsups")
async def put_calendar_headsups(payload: dict = Body(...), current_user = Depends(get_current_user)):
    username = _headsups_user(current_user)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Calendar heads-ups are not available right now.")
    return {"settings": await asyncio.to_thread(headsups_utils.set_settings, db, username, payload)}


@app.get("/api/calendar-headsups/preview")
async def preview_calendar_headsups(current_user = Depends(get_current_user)):
    """What Sonic would mention from today's and tomorrow's calendar, shown to the person on their own settings page. Nothing is recorded."""
    username = _headsups_user(current_user)
    tz = await asyncio.to_thread(get_user_timezone, username)
    state = await asyncio.to_thread(budget_guard.current_state, None, None, username)
    items = await asyncio.to_thread(lambda: headsups_utils.preview(username, tz, allow_model=budget_guard.allows("calendar_headsups", state)))
    return {"items": items}


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


@app.get("/api/settings/tool-model")
async def get_tool_model_setting(current_user = Depends(get_current_user)):
    """Which model does the tool agent's reasoning steps for this person: their own choice, else the server's default. `available` is whether Claude can be
    offered at all (a key is configured); guests and the embed are always Gemini."""
    username = current_user.get("sub")
    return tool_model_view(username, claude_available())


@app.put("/api/settings/tool-model")
async def update_tool_model_setting(payload: ToolModelUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    view, problem = save_tool_model_choice(username, payload.tool_model, claude_available())
    if problem:
        raise HTTPException(status_code=problem[0], detail=problem[1])
    return view


@app.get("/api/budget")
async def get_budget(current_user = Depends(get_current_user)):
    """Where the usage budget stands (backend/utils/budget_guard.py): everyone gets the level, what is paused and when it ends;
    only an admin gets amounts. `level` is "off" when no budget is configured."""
    username = current_user.get("sub")
    admin = is_global_admin(load_directory(), username)
    state = await asyncio.to_thread(budget_guard.current_state, None, None, budget_guard.limit_username(username, admin))
    return budget_guard.public_view(state, admin)


@app.post("/api/budget/extend")
async def extend_budget(current_user = Depends(get_current_user)):
    """A global admin raises the block line of whatever shared budget has paused chat by another 25%, until that budget resets
    (backend/utils/budget_guard.py). Each click adds another 25%; nothing happens if nothing is blocking. Each grant is saved with who and when."""
    username = current_user.get("sub")
    if not is_global_admin(load_directory(), username):
        raise HTTPException(status_code=403, detail="Admins only.")
    state = await asyncio.to_thread(budget_guard.current_state)
    if not budget_guard.extendable_scopes(state):
        raise HTTPException(status_code=409, detail="Nothing is blocking chat, so there is nothing to extend.")
    try:
        await asyncio.to_thread(budget_guard.grant_extension, get_db(), username, state)
    except Exception:
        logger.exception("[budget] could not save the extension")
        raise HTTPException(status_code=500, detail="The extension could not be saved.")
    logger.info("[budget] a global admin extended the blocked budget by %d%%.", round(budget_guard.EXTENSION_STEP * 100))
    fresh = await asyncio.to_thread(budget_guard.current_state)
    return budget_guard.public_view(fresh, True)


class ProfileUpdate(BaseModel):
    name: str = ""
    pronouns: str = ""
    work: str = ""
    interests: List[str] = []
    style: Dict[str, str] = {}
    avoid: List[str] = []
    offers: bool = True


@app.get("/api/settings/profile")
async def get_profile_setting(current_user = Depends(get_current_user)):
    """What the person set about themselves on the Account page (backend/utils/profile_utils.py)."""
    return await asyncio.to_thread(get_user_profile, current_user.get("sub"))


@app.put("/api/settings/profile")
async def update_profile_setting(payload: ProfileUpdate, current_user = Depends(get_current_user)):
    """Saves the whole profile. A value that cannot be saved is a 422 naming the field and the reason; nothing is written then."""
    username = current_user.get("sub")
    if is_guest_username(username) or username in TOGGLE_LOCKED_USERS:
        raise HTTPException(status_code=403, detail="Signing in is required to keep a profile.")
    try:
        return await asyncio.to_thread(set_user_profile, username, payload.model_dump())
    except profile_utils.ProfileError as error:
        raise HTTPException(status_code=422, detail={"field": error.field, "message": error.message})


@app.get("/api/settings/proactive-opening")
async def get_proactive_opening_setting(current_user = Depends(get_current_user)):
    """Whether Sonic may start a conversation (backend/utils/initiation.py). Off until the user turns it on."""
    return {"enabled": await asyncio.to_thread(initiation_utils.get_enabled, current_user.get("sub"))}


@app.put("/api/settings/proactive-opening")
async def update_proactive_opening_setting(payload: ProactiveOpeningUpdate, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    if is_guest_username(username) or username in TOGGLE_LOCKED_USERS:
        raise HTTPException(status_code=403, detail="Signing in is required to let Sonic message first.")
    return {"enabled": await asyncio.to_thread(initiation_utils.update_enabled, username, payload.enabled)}


@app.post("/api/chat/opening")
async def open_conversation(payload: OpeningRequest, current_user = Depends(get_current_user)):
    """Called when the user opens an existing conversation. Usually returns {"message": null}; rarely, Sonic has
    something anchored to say first, and it is saved into the thread and returned here (backend/utils/initiation.py)."""
    username = current_user.get("sub")
    message = await initiation_utils.open_for_request(
        username, payload.session_id, chat_sessions,
        load=load_session_messages, sync=sync_session_messages, get_timezone=get_user_timezone,
        get_llm=get_stream_llm, save=save_conversation_turn, is_guest=is_guest_username(username),
    )
    return {"message": message}


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


@app.post("/api/microsoft/connect/start")
async def start_microsoft_connection(return_to: str = Query(...), current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    if username in microsoft_utils.MICROSOFT_LOCKED_USERS:
        raise HTTPException(status_code=403, detail="Microsoft is not available for this account")
    if not _is_allowed_calendar_return_to(return_to):
        raise HTTPException(status_code=400, detail="return_to must be a trusted application URL")
    try:
        url, state, code_verifier = MicrosoftOAuth().authorization_url()
    except ValueError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    await asyncio.to_thread(microsoft_utils.create_pending, state, username, code_verifier, return_to)
    return {"authorization_url": url}


@app.get("/api/microsoft/callback")
async def microsoft_oauth_callback(request: Request, state: str = Query(...)):
    # Like the Google callback this is a bare browser redirect with no auth token: the pending record, looked up by state alone, says who started the flow.
    default_target = "https://www.sonicassistant.com/#/integrations"
    pending = await asyncio.to_thread(microsoft_utils.get_pending, state)
    if not pending:
        return RedirectResponse(f"{default_target}?microsoft_error=expired")

    return_to = pending.get("return_to") or default_target
    username = pending.get("username")
    if request.query_params.get("error"):
        await asyncio.to_thread(microsoft_utils.consume_pending, state)
        return RedirectResponse(f"{return_to}?microsoft_error=access_denied")

    code = request.query_params.get("code")
    if not code:
        await asyncio.to_thread(microsoft_utils.consume_pending, state)
        return RedirectResponse(f"{return_to}?microsoft_error=connection_failed")
    try:
        connection = await asyncio.to_thread(MicrosoftOAuth().build_connection, code, pending.get("code_verifier"))
        await asyncio.to_thread(lambda: microsoft_utils.save_connection(username, **connection))
        await asyncio.to_thread(microsoft_utils.consume_pending, state)
    except Exception:
        logger.exception("[microsoft_oauth_callback] Failed to complete the Microsoft connection for %s", username)
        return RedirectResponse(f"{return_to}?microsoft_error=connection_failed")

    return RedirectResponse(f"{return_to}?connected=microsoft")


@app.get("/api/microsoft/status")
async def get_microsoft_status(current_user = Depends(get_current_user)):
    return await asyncio.to_thread(microsoft_utils.get_connection_status, current_user.get("sub"))


@app.post("/api/microsoft/disconnect")
async def disconnect_microsoft(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    await asyncio.to_thread(MicrosoftOAuth().revoke, username)
    await asyncio.to_thread(microsoft_utils.delete_connection, username)
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

async def require_global_admin(current_user = Depends(get_current_user)):
    """Dependency for the admin endpoints: the signed-in subject must be in Global_Admins. Returns the claims."""
    if not is_global_admin(load_directory(), current_user.get("sub")):
        raise HTTPException(status_code=403, detail="Admins only.")
    return current_user


def _admin_db():
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="No database is configured.")
    return db


class ReflectRequest(BaseModel):
    days: int = Field(30, ge=7, le=180)


@app.get("/api/admin/ops-suggestions")
async def get_ops_suggestions(admin = Depends(require_global_admin)):
    """What is worth the admin's attention, worked out from errAgent's incidents and the pending self-observations
    (backend/utils/ops_suggestions.py). Fixed rules, no model; `incidents` says whether errAgent could be read. Global_Admins only."""
    return await ops_suggestions_utils.compute(_admin_db())


@app.post("/api/admin/ops-suggestions/{key}/{decision}")
async def decide_ops_suggestion(key: str, decision: str, admin = Depends(require_global_admin)):
    """Dismiss (hidden 30 days) or snooze (7 days) one suggestion. It comes back afterwards if the data still says so."""
    if decision not in ("dismiss", "snooze") or not ops_suggestions_utils.valid_key(key):
        raise HTTPException(status_code=400, detail="Unknown suggestion or decision.")
    decide = ops_suggestions_utils.dismiss if decision == "dismiss" else ops_suggestions_utils.snooze
    return {"hidden_until": await asyncio.to_thread(decide, _admin_db(), key, admin.get("sub"))}


@app.get("/api/settings/ops-digest")
async def get_ops_digest_setting(current_user = Depends(get_current_user)):
    """Whether Sonic tells an admin about new production incidents when a conversation is opened (backend/utils/ops_digest.py).
    `available` is false for anyone who isn't a Global_Admin, and on a deployment without errAgent reads, so the app can hide it."""
    username = current_user.get("sub")
    available = await asyncio.to_thread(ops_digest_utils.available_to, username)
    enabled = await asyncio.to_thread(ops_digest_utils.get_enabled, username) if available else False
    return {"enabled": enabled, "available": available}


@app.put("/api/settings/ops-digest")
async def update_ops_digest_setting(payload: ProactiveOpeningUpdate, admin = Depends(require_global_admin)):
    username = admin.get("sub")
    if not await asyncio.to_thread(ops_digest_utils.available_to, username):
        raise HTTPException(status_code=409, detail="errAgent reads are not configured here.")
    return {"enabled": await asyncio.to_thread(ops_digest_utils.update_enabled, username, payload.enabled), "available": True}


@app.get("/api/admin/safety-stats")
async def get_safety_stats(days: int = Query(7, ge=1, le=180), admin = Depends(require_global_admin)):
    """Counts of how the crisis safety layer has been behaving (see backend/utils/safety_stats.py): never message
    text, never names. Global_Admins only."""
    events = await asyncio.to_thread(fetch_recent_events, _admin_db(), days)
    return summarize_safety_events(events, days=days)


@app.get("/api/admin/sonic-health")
async def get_sonic_health(days: int = Query(7, ge=1, le=180), admin = Depends(require_global_admin)):
    """Everything the admin Sonic Health page shows: safety layer stats, Sonic's own behavior counters, and its
    self-observations (backend/utils/sonic_health.py). Aggregates only. Global_Admins only."""
    return await asyncio.to_thread(build_sonic_health, _admin_db(), days)


@app.post("/api/admin/observations/reflect")
async def run_observation_reflection(payload: ReflectRequest, admin = Depends(require_global_admin)):
    """Turns the counters into PROPOSED observations (nothing goes live until it is approved)."""
    db = _admin_db()
    result = await asyncio.to_thread(reflect_observations, db, payload.days)
    return {"result": result, "observations": await asyncio.to_thread(list_observations, db)}


@app.post("/api/admin/observations/{key}/approve")
async def approve_observation(key: str, admin = Depends(require_global_admin)):
    """Makes the proposed wording of one observation live, recording who approved it."""
    db = _admin_db()
    if not await asyncio.to_thread(approve_observation_doc, db, key, None, admin.get("sub")):
        raise HTTPException(status_code=404, detail="Nothing to approve for that observation.")
    return {"observations": await asyncio.to_thread(list_observations, db)}


@app.post("/api/admin/observations/{key}/retire")
async def retire_observation(key: str, admin = Depends(require_global_admin)):
    """Stops using an observation."""
    db = _admin_db()
    if not await asyncio.to_thread(retire_observation_doc, db, key, None, admin.get("sub")):
        raise HTTPException(status_code=404, detail="No such observation.")
    return {"observations": await asyncio.to_thread(list_observations, db)}

class ShelfRemoveRequest(BaseModel):
    block_source: bool = False


@app.get("/api/admin/reading-shelf")
async def get_reading_shelf(admin = Depends(require_global_admin)):
    """Everything on Sonic's reading shelf (backend/utils/reading_shelf.py), newest first, with the sources that are blocked. Global_Admins only."""
    return await asyncio.to_thread(shelf_utils.admin_view, _admin_db())


@app.post("/api/admin/reading-shelf/{key}/remove")
async def remove_reading_shelf_item(key: str, payload: ShelfRemoveRequest, admin = Depends(require_global_admin)):
    """Takes one item off the shelf for good (it stays as a tombstone so the page is never read again). With block_source, the site is blocked too."""
    db = _admin_db()
    if not await asyncio.to_thread(shelf_utils.remove_item, db, key, admin.get("sub"), payload.block_source):
        raise HTTPException(status_code=404, detail="No such item.")
    logger.info("[shelf] an admin removed an item%s.", " and blocked its source" if payload.block_source else "")
    return await asyncio.to_thread(shelf_utils.admin_view, db)


@app.delete("/api/admin/reading-shelf/blocked/{domain}")
async def unblock_reading_shelf_source(domain: str, admin = Depends(require_global_admin)):
    """Lets a blocked site be read again (items already removed stay removed)."""
    db = _admin_db()
    await asyncio.to_thread(shelf_utils.unblock_domain, db, domain)
    return await asyncio.to_thread(shelf_utils.admin_view, db)


@app.post("/api/admin/reading-shelf/refresh")
async def refresh_reading_shelf(admin = Depends(require_global_admin)):
    """Runs one search now instead of waiting for the interval: one search, up to a few page reads and two small model calls."""
    from backend.models.models import lite_llm as shelf_llm

    db = _admin_db()
    result = await shelf_refresh.refresh_shelf(db, shelf_llm.ainvoke, force=True)
    return {"result": result, **(await asyncio.to_thread(shelf_utils.admin_view, db))}


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