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
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query, Form, Request, Depends, BackgroundTasks, status
from typing import List, Dict, Any, Optional
import uuid
import traceback
from gridfs import GridFS
from bson.objectid import ObjectId
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from backend.components import taskboard
from backend.utils.taskboard_utils import require_taskboard_admin, is_taskboard_admin_for_user
from backend.models.models import llm, get_stream_llm
from backend.models.attachment import Attachment
from backend.services.github_service import process_pr_summary
# Modernized LangChain Imports
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_community.vectorstores import Chroma
from backend.components.time_storage import TimeEntry
from backend.components.constraints import get_system_prompt, CONVERSATIONAL_PROMPT, WEB_SEARCH_PROMPT, CODE_INTERPRETER_PROMPT, GITHUB_FORMAT_PROMPT, PR_FORMAT_PROMPT, format_docs
from backend.services.search import discover_workspace_documents
from local_function_app.function_app import run_ingestion_pipeline, HOT_FOLDER_DIR
from backend.state.graph_state import GraphState
from backend.services.insights_workflow import create_insight_workflow
from backend.utils.app_utils import ( 
    save_conversation, 
    list_saved_conversations, 
    load_saved_conversations, 
    load_saved_conversation, 
    save_chat_history, 
    format_history_as_text, 
    chat_sessions, 
    get_db_dependency, 
    serialize_doc, 
    load_chat_history, 
    fetch_relevant_corrections, 
    extract_target_repo, 
    resolve_app_ingest_repo, 
    validate_app_ingest_identity,
    build_erragent_ingest_payload,
    pick_repo_from_metadata,
    send_erragent_ingest,
    dispatch_erragent_ingest,
    build_error_payload,
    resolve_target_repo,
)
from backend.utils.attachment_utils import process_user_attachment, ingest_doc_to_session
from backend.utils.fallback_utils import rewrite_fallback
from backend.logging.sass_logger import setup_logging
from backend.logging.erragent_handler import install_erragent_logging
from backend.services.orchestrator import startup_services
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access, verify_paapp_access, load_directory, seed_guest_tasks
from backend.utils.db_utils import get_db, save_error_event, test_connection
from backend.auth.isolation_auth import get_current_user, record_login_event
from contextlib import asynccontextmanager
from settings import DB_DIR
from backend.components.time_storage import TimeEntryCreate, add_time_entry, load_user_time, clear_user_time, TimeEntry, save_user_time
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

# 2. Define the startup/shutdown logic
@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_sessions
    # This runs once when the server starts
    try:
        logger.info("Loading chat history from database...")
        chat_sessions = load_chat_history()
    except Exception as e:
        print(f"Error loading chat history: {e}")
    yield
    # Cleanup tasks would go here
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
        "https://paapp-u2l9.onrender.com",
        "https://sonicassistant.com",
        "https://www.sonicassistant.com/"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = setup_logging()  # Initialize the logger from backend/logging/sass_logger.py
install_erragent_logging(logger)
logger.info("--- BOOTING SECURE KNOWLEDGE ASSISTANT ---")
services = startup_services()
insight_workflow = services["insight_workflow"]
chat_sessions = {}


class LoginRequest(BaseModel):
    username: str

class ChatRequest(BaseModel):
    question: str
    affiliate: str 
    attachments: list[Attachment] | None = None
    session_id: str | None = None

class EventCreate(BaseModel):
    activity: str
    start_time: str
    date: str
    notes: str = ""
    type: str = "event"   

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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Re-raise standard FastAPI HTTP exceptions so they return their intended status code (e.g. 401, 404)
    if isinstance(exc, HTTPException):
        raise exc

    logger.error("--> [SAAPP] Caught unhandled exception on %s [%s]: %s", request.url.path, request.method, str(exc))

    # 1. Build standardized error payload
    payload = build_error_payload(
        exc=exc,
        service_default="btyapp",
        source=request.url.path,
        method=request.method,
    )

    # 2. Fire-and-forget in background
    dispatch_erragent_ingest(payload)

    # 3. Return clean 500
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    clerk_id = current_user.get("sub")
    email = current_user.get("email")
    
    db = get_db() #[cite: 1]
    
    if db is not None:
        users_col = db["directory"] 
        
        # 1. Look for the user by clerk_id
        user_doc = users_col.find_one({"clerk_id": clerk_id})
        
        # 2. LAZY MIGRATION: If not found, look for them by email
        if not user_doc and email:
            user_doc = users_col.find_one({"email": email})
            if user_doc:
                logger.info(f"Lazy migrating user record for: {email}")
                # Add the missing clerk_id to the existing record
                users_col.update_one(
                    {"_id": user_doc["_id"]}, 
                    {"$set": {"clerk_id": clerk_id}}
                )
                # Refresh user_doc with the new id
                user_doc["clerk_id"] = clerk_id
        
        # 3. If still not found, provision new user
        if not user_doc:
            logger.info(f"[+] Provisioning new database user: {email or clerk_id}")
            new_user = {
                "clerk_id": clerk_id,
                "email": email,
                "username": email.split("@")[0] if email else "new_user",
                "groups": ["Affiliate_A", "Affiliate_B", "Affiliate_C", "PAAPP_Admins", "Taskboard_Admins"],
                "created_at": datetime.utcnow()
            }
            users_col.insert_one(new_user)
            user_doc = new_user
            
        return {
            "username": user_doc.get("username"),
            "email": user_doc.get("email"),
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
    
    return get_accessible_affiliates(clerk_id, directory)


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
        files = discover_workspace_documents(affiliate)
        return {"accessible_documents": files}
    except Exception as e:
        logger.error(f"[-] Catalog discovery anomaly: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def secure_chat(request: ChatRequest, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    response_llm = get_stream_llm(username)
    question = request.question.strip()
    session_id = request.session_id.strip() if request.session_id else f"{username}_session"
    history_key = f"{username}::{session_id}"
    t_auth_start = time.perf_counter()
    
    if not verify_paapp_access(username):
        return {"message": "Access denied: You are not authorized to use PAAPP integrations."}

    async def stream_simple_message(text: str):
        async def generator():
            yield f"data: {json.dumps({'event': 'token', 'text': text})}\n\n"
            yield f"data: {json.dumps({'event': 'final_generation', 'text': text})}\n\n"
        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    async def streamThinkingThen(text: str):
        async def generator():
            yield f"data: {json.dumps({'event': 'token', 'text': '…'})}\n\n"
            await asyncio.sleep(0.2)
            yield f"data: {json.dumps({'event': 'token', 'text': text})}\n\n"
            yield f"data: {json.dumps({'event': 'final_generation', 'text': text})}\n\n"
        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    # ---------- Command Overrides ----------
    if question.lower().startswith("save conversation"):
        parts = question.split("save conversation", 1)
        title = parts[1].strip() or f"Conversation_{datetime.now().isoformat()}"
        save_conversation(username, title, chat_sessions.get(history_key, []))
        return await streamThinkingThen(f"Conversation '{title}' saved successfully.")

    if question.lower().startswith("load conversation"):
        title = question.split("load conversation", 1)[1].strip()
        conversation = load_saved_conversation(username, title)
        if not conversation:
            return await streamThinkingThen("Conversation not found.")
        
        reconstructed = []
        for msg in conversation["messages"]:
            if msg["type"] == "human": reconstructed.append(HumanMessage(content=msg["content"]))
            elif msg["type"] == "ai": reconstructed.append(AIMessage(content=msg["content"]))
            elif msg["type"] == "system": reconstructed.append(SystemMessage(content=msg["content"]))
        chat_sessions[history_key] = reconstructed
        chat_sessions[history_key].insert(0, SystemMessage(content="Loaded conversation context."))
        save_chat_history()
        return await streamThinkingThen(f"Conversation '{title}' loaded successfully.")

    if question.lower().startswith("list conversations"):
        titles = list_saved_conversations(username)
        if not titles:
            return await streamThinkingThen("You have no saved conversations.")
        formatted = "\n".join(f"• {t}" for t in titles)
        return await streamThinkingThen(f"Saved conversations:\n{formatted}")

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

    # ---------- Conversation Memory State Init ----------
    if history_key not in chat_sessions:
        chat_sessions[history_key] = []
    if len(chat_sessions[history_key]) > 10:
        chat_sessions[history_key] = chat_sessions[history_key][-10:]

    messages_state = chat_sessions[history_key]
    messages_state.append(HumanMessage(content=question))

    initial_state: GraphState = {
        "messages": messages_state,
        "username": username,
        "target_scope": target_scope,
        "documents": [],
        "relevance_grade": "web_search" if force_web_search else "", 
        "loop_count": 0,
        "original_question": question,
        "force_web_search": force_web_search
    }

    # ---------- Early Attachments Processing ----------
    attachment_summaries = []
    if request.attachments:
        logger.info(f"Processing {len(request.attachments)} attachments for {username}")
        for att in request.attachments:
            ingest_doc_to_session(username, session_id, att)
            summary = process_user_attachment(att)
            if summary:
                attachment_summaries.append(summary)
                
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
        t_prompt_ready = None
        t_first_token = None
        final_state = {}

        # Only needs username+question, so run it alongside the graph instead of after it.
        corrections_task = asyncio.create_task(
            asyncio.to_thread(fetch_relevant_corrections, username, question)
        )

        def log_timings(grade: str, outcome: str):
            now = time.perf_counter()
            logger.info(
                "[TIMING] user=%s affiliate=%s grade=%s outcome=%s docs=%d "
                "preflight=%.2fs graph=%.2fs prompt=%.2fs llm_ttft=%.2fs ttft=%.2fs total=%.2fs",
                username,
                requested_affiliate,
                grade,
                outcome,
                len(final_state.get("documents", []) or []),
                t_stream_start - t_auth_start,
                (t_graph_end - t_stream_start) if t_graph_end else -1,
                (t_prompt_ready - t_graph_end) if (t_prompt_ready and t_graph_end) else -1,
                (t_first_token - t_prompt_ready) if (t_first_token and t_prompt_ready) else -1,
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
                    "original_question": question
                }
            else:
                # 1. LIVE GRAPH EXECUTION & EVENT STREAMING
                logger.info("--- STARTING LIVE GRAPH EXECUTION ---")
                workflow = services.get("compiled_workflow")
                
                async for event in workflow.astream_events(initial_state, version="v2"):
                    kind = event["event"]

                    # Catch Custom Thoughts emitted by your nodes via adispatch_custom_event
                    if kind == "on_custom_event" and event.get("name") == "trace_detail":
                        data = event.get("data", {})
                        yield f"data: {json.dumps({'event': 'node_progress', 'node': data.get('node', 'system'), 'title': data.get('title', 'Processing...'), 'detail': data.get('detail', '')})}\n\n"
                        await asyncio.sleep(0.01)

                    # Catch Final State when the graph finishes (Look for the final dictionary output)
                    if kind == "on_chain_end":
                        output = event.get("data", {}).get("output")
                        # Ensure we grab the actual state dict and not a sub-node return
                        if output and isinstance(output, dict) and "relevance_grade" in output:
                            final_state = output

            # Fallback just in case event streaming missed the final state dict
            if not final_state:
                final_state = await workflow.ainvoke(initial_state)

            t_graph_end = time.perf_counter()

            # 2. EVALUATE FINAL STATE & BUILD PROMPT
            relevance_grade = final_state.get("relevance_grade")
            insight_answer = final_state.get("insight_answer")
            documents = final_state.get("documents", [])

            if relevance_grade in ["hitl_approval_required", "action_complete"]:
                card_text = final_state.get("generation") or final_state.get("content_to_format") or (final_state.get("messages")[-1].content if final_state.get("messages") else "Action complete.")
                yield f"data: {json.dumps({'event': 'token', 'text': card_text})}\n\n"
                yield f"data: {json.dumps({'event': 'final_generation', 'text': card_text})}\n\n"
                corrections_task.cancel()
                t_first_token = time.perf_counter()
                log_timings(relevance_grade, "card")
                return

            if relevance_grade == "web_search":
                prompt = WEB_SEARCH_PROMPT.format(context=format_docs(documents), question=question)
            elif relevance_grade == "code_interpreter":
                prompt = CODE_INTERPRETER_PROMPT.format(content=final_state.get("content_to_format", ""), question=question)
            elif relevance_grade == "github_search":
                prompt = GITHUB_FORMAT_PROMPT.format(content=final_state.get("content_to_format", ""), question=question)
            elif relevance_grade == "pr_summary":
                prompt = PR_FORMAT_PROMPT.format(content=final_state.get("content_to_format", ""), question=question)
            elif insight_answer:
                prompt = CONVERSATIONAL_PROMPT.format(username=username, question=question, history=format_history_as_text(chat_sessions[history_key]), insight=insight_answer)
            elif relevance_grade == "conversational":
                prompt = CONVERSATIONAL_PROMPT.format(username=username, question=question, history=format_history_as_text(chat_sessions[history_key]), insight="")
            else:
                final_question = final_state.get("original_question", question)
                accessible_affiliates_str = ", ".join(final_state.get("target_scope", target_scope))
                instructions = get_system_prompt(username, accessible_affiliates_str)
                documents_sorted = sorted(documents, key=lambda d: d.metadata.get("priority", False), reverse=True)
                prompt = instructions.format(context=format_docs(documents_sorted), history=format_history_as_text(chat_sessions[history_key]), question=final_question)

            # Announce LLM generation start
            yield f"data: {json.dumps({'event': 'node_progress', 'node': 'formatter_node', 'title': 'Formatting output structure...', 'detail': f'Synthesizing final answer for {question[:30]}...'})}\n\n"
            guardrail_context = await corrections_task

            if guardrail_context:
                prompt = prompt + guardrail_context
                # Emit trace event to frontend execution trace drawer!
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'self_correction_guardrail', 'title': 'Applying Lessons Learned Guardrail', 'detail': f'Injected past failure constraint into context prompt.'})}\n\n"

            t_prompt_ready = time.perf_counter()
            yield f"data: {json.dumps({'event': 'node_progress', 'node': 'formatter_node', 'title': 'Thinking through the answer...', 'detail': 'Gemini is composing the final response.'})}\n\n"
            # 3. STREAM RESPONSE TOKENS FROM LLM
            async for chunk in response_llm.astream(prompt):
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

            # 4. GROUNDING CHECK & FALLBACK
            if full_response and "I cannot find the answer in the provided knowledge base." in full_response.strip():
                logger.info("Grounding failure detected — triggering rewrite fallback...")
                yield f"data: {json.dumps({'event': 'node_progress', 'node': 'rewrite_query_node', 'title': 'Refining search parameters...', 'detail': f'Expanding query parameters...'})}\n\n"

                fallback_state = {
                    **initial_state,
                    "target_scope": final_state.get("target_scope", initial_state["target_scope"]),
                    "documents": final_state.get("documents", []),
                    "original_question": final_state.get("original_question", initial_state["original_question"]),
                }
                async for fallback_chunk in rewrite_fallback(services.get("vector_store"), fallback_state, username, messages_state, chat_sessions, save_chat_history):
                    yield fallback_chunk
                log_timings(relevance_grade, "rewrite_fallback")
                return

            yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"
            chat_sessions[history_key].append(AIMessage(content=full_response))
            save_chat_history()
            log_timings(relevance_grade, "ok")
            logger.info("--- End of token stream ---")

        except Exception as e:
            logger.error(f"[x] Error in token_streamer loop context: {e}", exc_info=True)
            log_timings(final_state.get("relevance_grade", "unknown"), "error")
            yield f"data: {json.dumps({'event': 'trace', 'title': 'Execution error', 'detail': str(e), 'status': 'active'})}\n\n"

    logger.info(f"Initializing secured token stream for {username}")
    return StreamingResponse(
        token_streamer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.post("/api/chat/clear")
async def clear_chat(request: Request, user = Depends(get_current_user)):
    db = get_db()
    data = await request.json()
    session_id = data.get("session_id")
    username = data.get("username") or user.get("sub") or user.get("email")

    filters = [{"username": username}] if username else []
    if session_id:
        filters.append({"session_id": session_id})

    if user.get("sub"):
        filters.append({"user_id": user.get("sub")})

    if not filters:
        return {"status": "cleared", "count": 0}

    # Remove live in-memory chat context used by the runtime session store.
    keys_to_remove = []
    if username and session_id:
        keys_to_remove.append(f"{username}::{session_id}")
    elif username:
        keys_to_remove.extend([k for k in list(chat_sessions.keys()) if k == username or k.startswith(f"{username}::")])

    for key in keys_to_remove:
        chat_sessions.pop(key, None)

    # Wipe by session_id OR user id/username (covers all bases)
    result = db.chat_history.delete_many({
        "$or": filters
    })
    return {"status": "cleared", "count": result.deleted_count}

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
    
    return StreamingResponse(
        grid_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{clean_basename}"'
        }
    )

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
        logger.error(f"Failed to spawn ingestion process: {str(e)}")
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
        logger.error(f"Deletion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/saved-conversations")
async def get_saved_conversations(current_user = Depends(get_current_user)):
    # Use the 'sub' claim from the verified JWT
    username = current_user.get("sub") 
    conversations = load_saved_conversations(username)
    return {"titles": [c["title"] for c in conversations]}

@app.get("/api/saved-conversations/{title}")
async def get_saved_conversation(title: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    conversations = load_saved_conversations(username)
    for convo in conversations:
        if convo["title"] == title:
            return {"title": convo["title"], "messages": convo["messages"]}
    raise HTTPException(status_code=404, detail="Conversation not found")

@app.get("/admin/paapp")
def access_paapp_data(current_user = Depends(get_current_user)):
    # 1. Identity is handled by get_current_user (Clerk JWT)
    username = current_user.get("sub")
    
    # 2. Use your existing logic from isolation_kb_utils.py
    allowed = verify_paapp_access(username)
    
    logger.info("is-paapp-admin for %s -> %s", username, allowed)
    
    return {"allowed": allowed}

TIME_ENTRIES: dict[str, list[TimeEntry]] = {}  # key: username, value: list of entries
@app.get("/api/time/list")
def saapp_list_time(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    return load_user_time(username)

@app.delete("/api/time/clear")
def saapp_clear_time(current_user = Depends(get_current_user)):
    clear_user_time(current_user.get("sub"))
    return {"status": "cleared"}

@app.post("/api/time/log")
async def log_time(
    entry: TimeEntryCreate, 
    current_user = Depends(get_current_user)
):
    try:
        new_entry = TimeEntry(
            id=str(uuid.uuid4()),
            username=current_user.get("sub"),  # Overriding the payload with verified ID
            activity=entry.activity,
            duration_hours=entry.duration_hours,
            duration_minutes=entry.duration_minutes,
            date=entry.date,
            created_at=datetime.now(timezone.utc).isoformat(),
            notes=entry.notes,
            type=entry.type
        )
        add_time_entry(new_entry)
        return {"status": "ok"}
    except Exception:
        traceback.print_exc() 
        raise HTTPException(status_code=500, detail="Check terminal for traceback")

@app.delete("/api/time/delete")
async def delete_time_entry(id: str, current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    entries = load_user_time(username)
    new_data = [entry for entry in entries if entry.id != id]
    save_user_time(username, new_data) 
    return {"status": "ok", "deleted": id}

@app.delete("/api/events/delete")
async def delete_event(id: str, current_user = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
        
    # The database query strictly limits deletion to the active user's documents
    result = db["events"].delete_one({"_id": ObjectId(id), "username": current_user.get("sub")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "deleted", "id": id}

@app.post("/api/events/create")
async def create_event(
    event: EventCreate, 
    current_user = Depends(get_current_user)
):
    username = current_user.get("sub")
    db = get_db()
    
    # Convert Pydantic model to a dictionary
    event_dict = event.dict() 
    event_dict["username"] = username # Attach the user from the token
    
    # Insert into database
    result = db["events"].insert_one(event_dict)
    
    # Fetch it back to return the full object with ID
    inserted_doc = db["events"].find_one({"_id": result.inserted_id})
    
    return serialize_doc(inserted_doc)

@app.post("/api/events/log")
async def saapp_log_event(
    entry: TimeEntryCreate, 
    db = Depends(get_db_dependency),
    current_user = Depends(get_current_user)
):
    new_entry = {
        "id": str(uuid.uuid4()), 
        "username": current_user.sub, # Override with verified session
        "activity": entry.activity,
        "duration_hours": entry.duration_hours,
        "duration_minutes": entry.duration_minutes,
        "date": entry.date,
        "notes": entry.notes,
        "type": "event",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    db["events"].insert_one(new_entry)
    return {"status": "ok"}

@app.get("/api/events/list")
def saapp_list_events(current_user = Depends(get_current_user)):
    username = current_user.get("sub")
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    
    events = list(db["events"].find({"username": username}))
    
    for e in events:
        e["id"] = str(e["_id"]) 
        e.pop("_id", None)      
        
    return events

@app.get("/api/tasks")
def get_tasks(current_user = Depends(get_current_user)):
    db = get_db()
    
    # Query ONLY tasks owned by the active logged-in user
    tasks = list(db["tasks"].find({"username": current_user.get("sub")}))
    
    # If a guest logs in and has no tasks yet, seed 3 mock ones for them!
    if not tasks and current_user.get("sub") == "guest-recruiter@example.com":
        seed_guest_tasks(db, current_user.get("sub"))
        tasks = list(db["tasks"].find({"username": current_user.get("sub")}))
        
    for t in tasks:
        t["id"] = str(t["_id"])
        t.pop("_id", None)
    return tasks

# @app.get("/api/tasks")
# def get_tasks(current_user = Depends(get_current_user)): # SECURED: Added auth check
#     db = get_db()
#     if db is None:
#         raise HTTPException(status_code=500, detail="Database connection unavailable")
        
#     tasks = list(db.get("tasks").find({}))
#     for t in tasks:
#         # Add the string version
#         t["id"] = str(t["_id"])
        
#         # CRITICAL: Strip out the raw ObjectId
#         t.pop("_id", None)
        
#     return tasks

@app.post("/api/tasks")
def create_task(task_data: dict, current_user = Depends(get_current_user)):
    db = get_db()
    # Add the username to the new task to ensure data isolation
    task_data["username"] = current_user.get("sub")
    
    result = db["tasks"].insert_one(task_data)
    task_data["id"] = str(result.inserted_id)
    task_data.pop("_id", None)
    
    return task_data

@app.put("/api/tasks/{task_id}")
def update_task_lane(task_id: str, payload: dict, current_user = Depends(get_current_user)):
    db = get_db() # Get the database connection
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    update_data = {k: v for k, v in payload.items() if k in ["lane", "title", "description"]}
    result = db["tasks"].update_one({"_id": ObjectId(task_id)}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "ok"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    username = current_user.get("sub")
    
    # 1. Attempt to handle both ObjectId and string IDs
    query_id = task_id
    if len(task_id) == 24:
        try:
            query_id = ObjectId(task_id)
        except errors.InvalidId:
            pass # Keep as string if it's not a valid ObjectId

    # 2. Add debug logging to see exactly what you are querying
    print(f"DEBUG: Deleting task with ID: {query_id} (Type: {type(query_id)}) for user: {username}")
    
    result = db["tasks"].delete_one({
        "_id": query_id,
        "username": username
    })
    
    if result.deleted_count == 0:
        # 3. Log what happened if nothing was found
        print(f"DEBUG: No task found with ID {query_id} for user {username}")
        raise HTTPException(status_code=404, detail="Task not found or unauthorized")
        
    return {"status": "deleted", "id": task_id}

@app.get("/api/insights")
def get_insights(current_user = Depends(get_current_user)): 
    username = current_user.get("sub")
    
    state = {
        "messages": [], 
        "username": username
    }
    logger.info(f"Triggering insight workflow for user: {state['username']}")
    result = insight_workflow.invoke(state)
    
    logger.info(f"Final graph result dictionary:{result}")    
    return result.get("insights", [])

@app.post("/api/chat/feedback")
async def store_feedback(
    payload: FeedbackPayload, 
    current_user = Depends(get_current_user)
):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")

    username = current_user.get("sub")

    correction_doc = {
        "id": str(uuid.uuid4()),
        "username": username,
        "user_prompt": payload.user_prompt,
        "bad_response": payload.bad_response[:400],
        "reason": payload.reason,
        "tag": payload.tag,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    db["corrections"].insert_one(correction_doc)
    logger.info(f"[+] Correction stored for {username} (Tag: {payload.tag}): {payload.reason}")
    return {"status": "success", "message": "Feedback indexed for dynamic self-correction."}


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
    
    # Safely parse JSON payload without throwing a 500 error on empty bodies
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if event == "pull_request" and payload.get("action") in ["opened", "synchronize", "reopened"]:
        pr_number = payload.get("number")
        repo = payload.get("repository", {}).get("full_name")

        if pr_number and repo:
            background_tasks.add_task(process_pr_summary, repo, pr_number)
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
