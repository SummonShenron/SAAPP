import asyncio
import os
import datetime
import json
import sys
import base64
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query, Form, Request, Depends
from typing import List
import uuid
import traceback
from datetime import datetime, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from backend.components import taskboard
from backend.utils.taskboard_utils import require_taskboard_admin, is_taskboard_admin_for_user
from backend.models.models import llm
from backend.models.attachment import Attachment
# Modernized LangChain Imports
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_community.vectorstores import Chroma
from backend.components.time_storage import TimeEntry
from backend.components.constraints import get_system_prompt, CONVERSATIONAL_PROMPT, format_docs
from backend.services.search import discover_workspace_documents
from local_function_app.function_app import run_ingestion_pipeline, HOT_FOLDER_DIR
from backend.state.graph_state import GraphState
from backend.services.insights_workflow import create_insight_workflow
from backend.utils.app_utils import save_conversation, list_saved_conversations, load_saved_conversations, load_saved_conversation, save_chat_history, format_history_as_text, chat_sessions
from backend.utils.attachment_utils import process_user_attachment, ingest_doc_to_session
from backend.utils.fallback_utils import rewrite_fallback
from backend.logging.sass_logger import setup_logging
from backend.services.orchestrator import startup_services
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access, verify_paapp_access, load_directory
from settings import DB_DIR
from backend.components.time_storage import TimeEntryCreate, add_time_entry, load_user_time, clear_user_time, TimeEntry, save_user_time

sys.path.append(os.path.join(os.path.dirname(__file__), "local-function-app"))
app = FastAPI(title="Secure RAG Engine API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = setup_logging()  # Initialize the logger from backend/logging/sass_logger.py
logger.info("--- BOOTING SECURE KNOWLEDGE ASSISTANT ---")
services = startup_services()
insight_workflow = services["insight_workflow"]

class LoginRequest(BaseModel):
    username: str

class ChatRequest(BaseModel):
    username: str
    question: str
    affiliate: str 
    attachments: list[Attachment] | None = None
    session_id: str | None = None

@app.get("/api/me")
def get_me(x_user_id: str | None = Header(None, alias="x-user-id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Unauthenticated")
    directory = load_directory()
    entry = directory.get(x_user_id)
    logger.info("GET /api/me for %s -> %s", x_user_id, bool(entry))
    return {"username": x_user_id, "email": entry.get("email") if entry else None, "groups": entry.get("groups", []) if entry else []}

@app.post("/api/login")
async def verify_identity_profile(payload: LoginRequest):
    """
    Secure single-point identity verification gatekeeper.
    Validates identity claims against the internal server directory file.
    """
    username = payload.username.strip()
    if username not in services.get("user_directory"):
        logger.warning(f"[-] Security Audit: Unauthorized identity profile attempt: '{username}'")
        raise HTTPException(status_code=401, detail="Unauthorized: Profile missing from security directory.")
    logger.info(f"[+] Security Audit: Validated profile session for '{username}'")
    return {"status": "authenticated", "principal": username}
    
@app.get("/api/affiliates")
async def get_affiliates(x_user_id: str = Header(None)):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing authorization context.")
    return get_accessible_affiliates(x_user_id, services.get("user_directory"))


@app.get("/api/user/groups")
def get_user_groups(username: str | None = Query(None), x_user_id: str | None = Header(None, alias="x-user-id")):
    # Accept either ?username= or x-user-id header for dev flexibility
    user = username or x_user_id
    if not user:
        raise HTTPException(status_code=400, detail="Missing username")
    directory = load_directory()
    entry = directory.get(user)
    groups = entry.get("groups", []) if entry else []
    logger.info("Fetching groups for: %s -> %s", user, groups)
    return groups


@app.get("/api/discover-docs")
async def discover_documents(affiliate: str = "All"):
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
async def secure_chat(request: ChatRequest, fastapi_request: Request):
    # raw = await fastapi_request.json()
    # logger.info(f"RAW REQUEST BODY: {raw}")
    username = request.username.strip()
    question = request.question.strip()
    session_id = request.session_id.strip() if request.session_id else f"{username}_session"

    user_docs = []
    if not verify_paapp_access(username):
        return {"message": "Access denied: You are not authorized to use PAAPP integrations."}
    # logger.debug(f"ChatRequest fields: {request.model_dump().keys()}")
    # logger.info(f"attachments value: {request.attachments}")
    async def stream_simple_message(text: str):
        async def generator():
            yield f"data: {json.dumps({'event': 'token', 'text': text})}\n\n"
            yield f"data: {json.dumps({'event': 'final_generation', 'text': text})}\n\n"
        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    async def streamThinkingThen(text: str):
        async def generator():
            # tiny "thinking" animation
            yield f"data: {json.dumps({'event': 'token', 'text': '…'})}\n\n"
            await asyncio.sleep(0.15)
            # final streamed message
            yield f"data: {json.dumps({'event': 'token', 'text': text})}\n\n"
            yield f"data: {json.dumps({'event': 'final_generation', 'text': text})}\n\n"
        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    if question.lower().startswith("save conversation"):
        # extract title
        parts = question.split("save conversation", 1)
        title = parts[1].strip() or f"Conversation_{datetime.datetime.now().isoformat()}"
        save_conversation(username, title)
        return await streamThinkingThen(f"Conversation '{title}' saved successfully.")
    if question.lower().startswith("load conversation"):
        title = question.split("load conversation", 1)[1].strip()
        conversation = load_saved_conversation(username, title)
        if not conversation:
            return await streamThinkingThen("Conversation not found.")
        # reconstruct LangChain messages
        reconstructed = []
        for msg in conversation["messages"]:
            if msg["type"] == "human":
                reconstructed.append(HumanMessage(content=msg["content"]))
            elif msg["type"] == "ai":
                reconstructed.append(AIMessage(content=msg["content"]))
            elif msg["type"] == "system":
                reconstructed.append(SystemMessage(content=msg["content"]))
        chat_sessions[username] = reconstructed
        chat_sessions[username].insert(0, SystemMessage(content="Loaded conversation context."))
        save_chat_history()
        return await streamThinkingThen(f"Conversation '{title}' loaded successfully.")
    if question.lower().startswith("list conversations"):
        titles = list_saved_conversations(username)
        if not titles:
            return await streamThinkingThen("You have no saved conversations.")
        # Build a nice readable list
        formatted = "\n".join(f"• {t}" for t in titles)
        return await streamThinkingThen(f"Saved conversations:\n{formatted}")
    
    requested_affiliate = request.affiliate.strip()
    user_claims = services.get("user_directory").get(username, {})
    user_groups = user_claims.get("groups", [])
    logger.info(f"--- BEGINNING CHAT STREAM ---")
    logger.info(f"User Verified: {user_claims['email']}")
    logger.debug(f"User Group Claims: {user_groups}")
    # ---------- Auth Authorization Boundary ----------
    if username not in services.get("user_directory"):
        raise HTTPException(status_code=401, detail="Unauthorized: User not found.")
    accessible_affiliates = get_accessible_affiliates(username, services.get("user_directory"))
    if requested_affiliate != "All" and requested_affiliate not in accessible_affiliates["accessible_affiliates"]:
        raise HTTPException(status_code=403, detail="Security Breach: Unauthorized affiliate scope requested.")
    target_scope = accessible_affiliates["accessible_affiliates"] if requested_affiliate == "All" else [requested_affiliate]
    # ---------- Conversation Memory State Init ----------
    if username not in chat_sessions:
        chat_sessions[username] = []
    if len(chat_sessions[username]) > 10:
        chat_sessions[username] = chat_sessions[username][-10:]
    messages_state = chat_sessions[username]
    messages_state.append(HumanMessage(content=question))
    initial_state: GraphState = {
        "messages": messages_state,
        "username": username,
        "target_scope": target_scope,
        "documents": [],
        "relevance_grade": "",
        "loop_count": 0,
        "original_question": question,
    }
    # ---------- Run LangGraph ONCE (no streaming, logic-only) ----------
    try:
        attachment_summaries = []

        if request.attachments:
            logger.info(f"Processing {len(request.attachments)} attachments for {username}")

            for att in request.attachments:
                # 1. Extract + save raw text into session store
                ingest_result = ingest_doc_to_session(username, session_id, att)
                # 2. Summarize for immediate context injection
                summary = process_user_attachment(att)
                if summary:
                    attachment_summaries.append(summary)



        # Inject summaries into graph state BEFORE workflow runs
        initial_state["attachment_summaries"] = attachment_summaries
        if attachment_summaries:
            docs = []
            for summary in attachment_summaries:
                docs.append(Document(
                    page_content=summary,
                    metadata={
                        "source": "user_attachment_summary",
                        "priority": True,
                        "page": "N/A"
                    }
                ))
            initial_state["documents"] = docs

        # Run workflow normally
        final_state = services.get("compiled_workflow").invoke(initial_state)

    except Exception as e:
        logger.error(f"[x] Workflow failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent workflow failed.")

    # ---------- Build Prompt based on Decision Boundary ----------
    insight_answer = final_state.get("insight_answer")
    is_conversational = final_state.get("relevance_grade") == "conversational"

    if insight_answer:
        history_transcript = format_history_as_text(chat_sessions[username])
        prompt = CONVERSATIONAL_PROMPT.format(
            username=username,
            question=question,
            history=history_transcript,
            insight=insight_answer
        )

    elif is_conversational:
        history_transcript = format_history_as_text(chat_sessions[username])
        prompt = CONVERSATIONAL_PROMPT.format(
            username=username,
            question=question,
            history=history_transcript,
            insight=final_state.get("insight_answer")
        )

    else:
        # RAG branch
        final_question = final_state.get("original_question", question)
        documents = final_state.get("documents", [])
        accessible_affiliates_str = ", ".join(final_state.get("target_scope", target_scope))
        instructions = get_system_prompt(username, accessible_affiliates_str)
        documents_sorted = sorted(
            documents,
            key=lambda d: d.metadata.get("priority", False),
            reverse=True
        )

        formatted_docs = format_docs(documents_sorted)
        history_transcript = format_history_as_text(chat_sessions[username])
        prompt = instructions.format(
            context=formatted_docs,
            history=history_transcript,
            question=final_question,
        )

    logger.debug(f"--- FINAL PROMPT SENT TO LLM: ---\n{prompt}")
    logger.debug("--- END OF PROMPT ---")
    # ---------- Real token streaming from LLM ----------
    async def token_streamer():
        full_response = ""
        try:
            async for chunk in llm.astream(prompt):
                # Safely parse text or content chunk representations 
                token = chunk if isinstance(chunk, str) else getattr(chunk, "content", None) or str(chunk)
                if not token:
                    continue
                full_response += token
                yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
                await asyncio.sleep(0)  # Cooperatively yield back event loop control
            if full_response:
                if "I cannot find the answer in the provided knowledge base." in full_response.strip():
                    logger.info("Grounding failure detected — triggering rewrite fallback...")
                    fallback_state = {
                        **initial_state,
                        "target_scope": final_state.get("target_scope", initial_state["target_scope"]),
                        "documents": final_state.get("documents", []),
                        "original_question": final_state.get("original_question", initial_state["original_question"]),
                    }
                    async for fallback_chunk in rewrite_fallback(services.get("vector_store"), fallback_state, username, messages_state, chat_sessions, save_chat_history):
                        yield fallback_chunk
                    return
                yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"
                # Update memory session history only after a fully successful stream
                chat_sessions[username].append(HumanMessage(content=question))
                chat_sessions[username].append(AIMessage(content=full_response))
                save_chat_history()
                logger.info("--- End of token stream ---")
        except Exception as e:
            logger.error(f"[x] Error in token_streamer loop context: {e}", exc_info=True)
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"
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
async def clear_chat_history(request: dict):
    username = request.get("username")
    if username in chat_sessions:
        chat_sessions[username] = []
        save_chat_history()
    return {"status": "cleared"}

@app.get("/api/documents")
async def get_ingested_documents_endpoint(
    affiliate: str,
    x_user_id: str = Header(None)
):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing authorization principal context.")

    if not verify_user_ingest_access(x_user_id, affiliate):
        raise HTTPException(status_code=403, detail="Unauthorized")

    # Ensure we are using the correct path format
    # Ensure HOT_FOLDER_DIR is defined and imported correctly in your app.py
    archive_target_zone = os.path.join(HOT_FOLDER_DIR, f"{affiliate}_Pages")
    
    # ADD THIS: Create the directory if it doesn't exist, or return empty
    if not os.path.exists(archive_target_zone):
        logger.info(f"Directory not found: {archive_target_zone}, returning empty manifest.")
        return []

    try:
        manifest_records = []
        for filename in os.listdir(archive_target_zone):
            file_path = os.path.join(archive_target_zone, filename) 
            if os.path.isfile(file_path) and filename.lower().endswith('.pdf'):
                file_stats = os.stat(file_path)    
                manifest_records.append({
                    "id": f"id_{filename.replace('.', '_')}", 
                    "filename": filename,
                    "uploadDate": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                    "fileSize": f"{round(file_stats.st_size / 1024, 1)} KB"
                })
        return manifest_records
    except Exception as e:
        logger.error(f"Failed to scan file system indexes: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to scan file system indexes: {str(e)}")

@app.post("/api/upload-attachment")
async def upload_attachment(
    username: str = Form(...),
    session_id: str = Form(...),
    file: UploadFile = File(...)
):
    raw_bytes = await file.read()

    # Run your ingestion pipeline
    raw_bytes = await file.read()
    
    encoded = base64.b64encode(raw_bytes).decode("utf-8")

    attachment = Attachment(filename=file.filename, content=encoded)

    return {"status": "ok", "filename": file.filename}


# --- ELEVATED ENDPOINT: SECURE MULTI-PART FILE UPLOAD ---
@app.post("/api/upload")
async def upload_and_ingest_documents(
    affiliate: str,
    files: List[UploadFile] = File(...),
    x_user_id: str = Header(None)
):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing required security context header.")  
    # CHECK CLAIMS DATABASE: Validate Ingesters group
    if not verify_user_ingest_access(x_user_id, affiliate):
        raise HTTPException(
            status_code=403, 
            detail=f"Access Denied: Account lacks required '{affiliate} Ingesters' claims configuration."
        )
    target_landing_zone = os.path.join(HOT_FOLDER_DIR, affiliate)
    os.makedirs(target_landing_zone, exist_ok=True)
    saved_paths = []
    try:
        for file in files:
            if not file.filename.lower().endswith('.pdf'):
                raise HTTPException(status_code=400, detail="Invalid extension format. Only PDFs accepted.")    
            destination_path = os.path.join(target_landing_zone, file.filename)
            with open(destination_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)
            saved_paths.append(destination_path)
        # Execute programmatic module pipeline dynamically
        pipeline_executed = run_ingestion_pipeline() 
        return {
            "status": "success",
            "message": f"Successfully ingested {len(saved_paths)} assets into {affiliate.replace('_', ' ')}.",
            "pipeline_triggered": pipeline_executed
        }
    except Exception as e:
        # Prevent dirty state file lingering if execution breaks halfway
        for path in saved_paths:
            if os.path.exists(path):
                os.remove(path)
        raise HTTPException(status_code=500, detail=f"Pipeline Processing Fault: {str(e)}")
    
# --- ELEVATED ENDPOINT: PURGE FROM VECTOR INDEX ---
@app.get("/api/documents")
async def list_documents(affiliate: str = Query(...), x_user_id: str = Header(None)):
    # 1. Permission check
    if not verify_user_ingest_access(x_user_id, affiliate):
        raise HTTPException(status_code=403, detail="Unauthorized")

    # 2. Target folder (your ingestion pipeline stores pages here)
    folder = os.path.join(HOT_FOLDER_DIR, f"{affiliate}_Pages")
    os.makedirs(folder, exist_ok=True)

    # 3. Build manifest
    manifest = []
    for filename in os.listdir(folder):
        full_path = os.path.join(folder, filename)
        if os.path.isfile(full_path):
            stat = os.stat(full_path)
            manifest.append({
                "id": filename,
                "filename": filename,
                "uploadDate": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "fileSize": stat.st_size
            })

    return manifest


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, affiliate: str = Query(...)):
    try:
        logger.info(f"Executing absolute database eviction sequence for: {doc_id}") 
        # 1. Reconstruct clean filename tracking variables
        filename = doc_id
        if filename.startswith("id_"):
            filename = filename[3:]
        if filename.endswith("_pdf"):
            filename = filename[:-4] + ".pdf"
        # 2. Build BOTH potential path tracking string variations
        # Variation A: The inbound hot folder path where Chroma originally read it
        hot_folder_source = os.path.join(HOT_FOLDER_DIR, affiliate, filename)
        # Variation B: The permanent pages archive folder location on disk
        archive_folder_source = os.path.join(HOT_FOLDER_DIR, f"{affiliate}_Pages", filename)
        logger.info(f"Sweeping Chroma for Hot Path: {hot_folder_source}")
        logger.info(f"Sweeping Chroma for Archive Path: {archive_folder_source}")
        # 3. DIRECT CHROMACLIENT EVICTION (Bypasses LangChain limitations)
        try:
            # Clear chunks stamped with the ingestion hot folder path
            services.get("vector_store")._collection.delete(where={"source": hot_folder_source})
            # Clear chunks stamped with the archive pages folder path
            services.get("vector_store")._collection.delete(where={"source": archive_folder_source})
            # Clear chunks that might only have the raw filename stamp
            services.get("vector_store")._collection.delete(where={"source": filename})
            logger.info("Associated vector matrix fragments thoroughly cleared from Chroma collection.")
        except Exception as v_err:
            logger.error(f"Direct collection level eviction encountered an issue: {v_err}")
        # 4. PHYSICAL STORAGE CLEANUP
        if os.path.exists(archive_folder_source):
            os.remove(archive_folder_source)
            logger.info(f"Physical asset file erased from disk layout: {archive_folder_source}")
        else:
            logger.warning(f"Physical asset was not present on disk canvas: {archive_folder_source}")
        return {"status": "success", "detail": f"Successfully expelled asset: {filename}"}
    except Exception as e:
        logger.error(f"[CRITICAL] Deletion engine sequence failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Core index expulsion failure: {str(e)}")

@app.get("/api/saved-conversations")
async def get_saved_conversations(
    username: str, 
    x_user_id: str = Header(None)
):
    # Enforce identity authorization
    if not x_user_id or username != x_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view these conversations.")
        
    conversations = load_saved_conversations(username)
    # conversations is a LIST, not a dict
    titles = [c["title"] for c in conversations]
    return {"titles": titles}

@app.get("/api/saved-conversations/{title}")
async def get_saved_conversation(
    username: str, 
    title: str, 
    x_user_id: str = Header(None)
):
    # Enforce identity authorization
    if not x_user_id or username != x_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view this conversation.")
        
    conversations = load_saved_conversations(username)
    # find the conversation in the list
    for convo in conversations:
        if convo["title"] == title:
            return {
                "title": convo["title"],
                "messages": convo["messages"]
            }
    return {"error": "Conversation not found"}

@app.get("/api/is-paapp-admin")
def is_paapp_admin(username: str | None = Query(None), x_user_id: str | None = Header(None, alias="x-user-id")):
    user = username or x_user_id
    if not user:
        return {"allowed": False}
    directory = load_directory()
    entry = directory.get(user, {})
    groups = entry.get("groups", [])
    allowed = "PAAPP_Admins" in groups or "Global_Admins" in groups
    logger.info("is-paapp-admin for %s -> %s", user, allowed)
    return {"allowed": allowed}

TIME_ENTRIES: dict[str, list[TimeEntry]] = {}  # key: username, value: list of entries
@app.get("/api/time/list")
def saapp_list_time(
    username: str, 
    x_user_id: str = Header(None)
):
    # Enforce identity authorization
    if not x_user_id or username != x_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view time entries.")
        
    return load_user_time(username)


@app.delete("/api/time/clear")
def saapp_clear_time(username: str):
    clear_user_time(username)
    return {"status": "cleared"}

@app.post("/api/time/log")
async def log_time(entry: TimeEntryCreate):
    try:
        new_entry = TimeEntry(
            id=str(uuid.uuid4()),
            username=entry.username,
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
        # This will print the actual error (e.g., ValidationError, KeyError) to your terminal
        traceback.print_exc() 
        raise HTTPException(status_code=500, detail="Check terminal for traceback")


@app.delete("/api/time/delete")
async def delete_time_entry(username: str, id: str): # Add username parameter
    entries = load_user_time(username) # Use your utility function
    new_data = [entry for entry in entries if entry.id != id]
    save_user_time(username, new_data) # Use your utility function
    return {"status": "ok", "deleted": id}

@app.delete("/api/events/delete")
async def delete_event(username: str, id: str):
    base_dir = os.path.join("saapp_data", "events")
    os.makedirs(base_dir, exist_ok=True)
    event_file = os.path.join(base_dir, f"{username}_events.json")
    

    # Load existing events
    try:
        with open(event_file, "r") as f:
            events = json.load(f)
    except FileNotFoundError:
        return {"status": "error", "message": "No events file found"}

    # Filter out the event to delete
    updated_events = [e for e in events if str(e["id"]) != str(id)]

    # Save updated list
    with open(event_file, "w") as f:
        json.dump(updated_events, f, indent=2)

    return {"status": "deleted", "id": id}

@app.post("/api/events/create")
async def create_event(payload: dict):
    try:
        username = payload.get("username")
        if not username:
            raise HTTPException(status_code=400, detail="Username missing")

        # 1. Ensure the directory exists
        base_dir = os.path.join("saapp_data", "events")
        os.makedirs(base_dir, exist_ok=True)
        
        event_file = os.path.join(base_dir, f"{username}_events.json")
        logger.info(base_dir)

        # 2. Safely load existing data
        events = []
        if os.path.exists(event_file):
            with open(event_file, "r") as f:
                try:
                    events = json.load(f)
                except json.JSONDecodeError:
                    events = []

        # 3. Create the event object
        new_event = {
            "id": str(len(events) + 1),
            "activity": payload.get("activity"),
            "start_time": payload.get("start_time"),
            "date": payload.get("date"),
            "notes": payload.get("notes", ""),
            "type": "event",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        events.append(new_event)

        # 4. Save
        with open(event_file, "w") as f:
            json.dump(events, f, indent=4)

        return {"status": "ok", "event": new_event}
        
    except Exception as e:
        logger.error(f"Event creation failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/events/log")
async def saapp_log_event(entry: TimeEntryCreate):
    saapp_time_dir = r"C:\Users\jackh\local-rag\saapp_data\events"
    path = os.path.join(saapp_time_dir, f"{entry.username}_events.json")

    # Load existing events
    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
    else:
        data = []

    # Build event entry
    new_entry = {
        "id": str(uuid.uuid4()),
        "username": entry.username,
        "activity": entry.activity,
        "duration_hours": entry.duration_hours,
        "duration_minutes": entry.duration_minutes,
        "date": entry.date,
        "notes": entry.notes,
        "type": "event",
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Save event
    data.append(new_entry)
    with open(path, "w") as f:
        json.dump(data, f)

    return {"status": "ok"}

# Add this in app.py
@app.get("/api/events/list")
def saapp_list_events(
    username: str, 
    x_user_id: str = Header(None)
):
    # Enforce identity authorization
    if not x_user_id or username != x_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view events.")

    # Point this to the same _events.json path used by the agent
    saapp_time_dir = r"C:\Users\jackh\local-rag\saapp_data\events"
    path = os.path.join(saapp_time_dir, f"{username}_events.json")
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)

@app.post("/api/tasks")
def create_task(payload: dict, username: str = Depends(require_taskboard_admin)):
    """
    Protected: only Taskboard_Admins can create tasks.
    """
    store = taskboard.read_store()
    tasks = store.get("tasks", [])
    
    # Append the new task data sent from the frontend
    tasks.append(payload)
    
    # Save it back to the store
    store["tasks"] = tasks
    taskboard.write_store(store)
    
    return {"status": "ok", "task": payload}

@app.get("/api/tasks")
def get_tasks():
    store = taskboard.read_store()
    return store.get("tasks", [])

@app.put("/api/tasks/{task_id}")
def update_task_lane(task_id: str, payload: dict, username: str = Depends(require_taskboard_admin)):
    """
    Updates an existing task's lane position or metadata on the server.
    """
    store = taskboard.read_store()
    tasks = store.get("tasks", [])
    
    # Find the task and update its properties
    task_found = False
    for t in tasks:
        if str(t.get("id")) == str(task_id):
            if "lane" in payload:
                t["lane"] = payload["lane"]
            if "title" in payload:
                t["title"] = payload["title"]
            if "description" in payload:
                t["description"] = payload["description"]
            task_found = True
            break
            
    if not task_found:
        raise HTTPException(status_code=404, detail="Task not found")
        
    store["tasks"] = tasks
    taskboard.write_store(store)
    return {"status": "ok"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, username: str = Depends(require_taskboard_admin)):
    """
    Protected: only Taskboard_Admins can delete tasks.
    """
    store = taskboard.read_store()
    tasks = store.get("tasks", [])
    filtered = [t for t in tasks if str(t.get("id")) != str(task_id)]
    store["tasks"] = filtered
    taskboard.write_store(store)
    return {"status": "deleted", "id": task_id}

@app.get("/api/insights")
def get_insights(current_user: str = "jack_admin"): 
    # 1. Ensure we pass the active user ('jack_admin'), not 'default_user'
    # (If you have a dependency injection for auth here like Depends(get_current_user), use that)
    state = {
        "messages": [], 
        "username": current_user
    }
    logger.info(f"Triggering insight workflow for user: {state['username']}")
    result = insight_workflow.invoke(state)
    
    logger.info(f"Final graph result dictionary:{result}")    
    # 2. Extract what the frontend actually needs.
    # If your graph saves the final product to a key named 'insights', return that.
    # If it formats it into a message content string, return result["messages"][-1].content
    return result.get("insights", [])
@app.get("/api/health")
async def health_check():
    logger.info("Checking health")
    return {"status": "healthy", "database_connected": os.path.exists(DB_DIR)}