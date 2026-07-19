import asyncio
import os
import datetime
import json
import sys
import base64
import subprocess
import traceback
from bson import ObjectId, errors
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query, Form, Request, Depends
from typing import List
import uuid
import traceback
from gridfs import GridFS
from bson.objectid import ObjectId
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
from backend.utils.app_utils import save_conversation, list_saved_conversations, load_saved_conversations, load_saved_conversation, save_chat_history, format_history_as_text, chat_sessions, get_db_dependency, serialize_doc, load_chat_history
from backend.utils.attachment_utils import process_user_attachment, ingest_doc_to_session
from backend.utils.fallback_utils import rewrite_fallback
from backend.logging.sass_logger import setup_logging
from backend.services.orchestrator import startup_services
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access, verify_paapp_access, load_directory, seed_guest_tasks
from backend.utils.db_utils import get_db
from backend.auth.isolation_auth import get_current_user
from contextlib import asynccontextmanager
from settings import DB_DIR
from backend.components.time_storage import TimeEntryCreate, add_time_entry, load_user_time, clear_user_time, TimeEntry, save_user_time
import aiohttp
import aiohttp.resolver

aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
sys.path.append(os.path.join(os.path.dirname(__file__), "local_function_app"))

# 2. Define the startup/shutdown logic
@asynccontextmanager
async def lifespan(app: FastAPI):
    global chat_sessions
    # This runs once when the server starts
    try:
        print("Loading chat history from database...")
        chat_sessions = load_chat_history()
    except Exception as e:
        print(f"Error loading chat history: {e}")
    yield
    # Cleanup tasks would go here
    chat_sessions = {}

# 3. Pass the lifespan to the app
app = FastAPI(lifespan=lifespan)
app = FastAPI(title="Secure RAG Engine API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://saapp-delta.vercel.app", "https://saapp-9w9p265cy-jackharper0517-6113s-projects.vercel.app" ], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger = setup_logging()  # Initialize the logger from backend/logging/sass_logger.py
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
                "groups": ["Affiliate_A", "Affiliate_B", "PAAPP_Admins", "Taskboard_Admins"],
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
    # raw = await fastapi_request.json()
    # logger.info(f"RAW REQUEST BODY: {raw}")
    username = current_user.get("sub")
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
    directory = load_directory()
    user_claims = directory.get(username, {})
    user_groups = user_claims.get("groups", [])
    logger.info(f"--- BEGINNING CHAT STREAM ---")
    logger.info(f"User Verified: {user_claims['email']}")
    logger.debug(f"User Group Claims: {user_groups}")
    # ---------- Auth Authorization Boundary ----------
    if username not in directory:
        raise HTTPException(status_code=401, detail="Unauthorized: User not found.")
    accessible_affiliates = get_accessible_affiliates(username, directory)
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
            # Execute primary stream
            async for chunk in llm.astream(prompt):
                # 1. Extract content from the chunk
                content = getattr(chunk, "content", "")
                
                # 2. Handle cases where content is a list (multimodal parts)
                if isinstance(content, list):
                    # Join all text parts into a single string
                    token = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in content])
                else:
                    token = str(content) if content else ""
                
                if not token:
                    continue
                
                full_response += token
                yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
                await asyncio.sleep(0)

            # Check for grounding failure requiring a rewrite
            if full_response and "I cannot find the answer in the provided knowledge base." in full_response.strip():
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

            # Stream completion and history saving
            yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"
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
async def clear_chat_history(current_user = Depends(get_current_user)):
    # We no longer need the 'request: dict' since the JWT has the identity
    username = current_user.get("sub")
    
    if username in chat_sessions:
        chat_sessions[username] = []
        save_chat_history()
    return {"status": "cleared"}

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
    affiliate: str,
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
        filename = file_obj.filename
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

    # Vector Deletion
    logger.info(f"Sweeping MongoDB Atlas 'documents' for filename: {filename}")
    
    try:
        vector_collection = db["documents"]
        # Regex query for the full path
        query = {"metadata.source": {"$regex": f".*{filename}$"}}
        result = vector_collection.delete_many(query)
        
        logger.info(f"Successfully cleared {result.deleted_count} vector fragments.")
        
        # Finally, remove from GridFS
        fs.delete(ObjectId(doc_id))
        return {"status": "success", "detail": f"Expelled {filename}"}

    except Exception as e:
        logger.error(f"Deletion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Update these two endpoints in app.py
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
    clear_user_time(current_user.sub)
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

@app.get("/api/health")
async def health_check():
    logger.info("Checking health")
    return {"status": "healthy", "database_connected": os.path.exists(DB_DIR)}