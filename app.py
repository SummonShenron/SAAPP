import asyncio
import os
import datetime
import json
import sys
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from backend.models.models import llm
# Modernized LangChain Imports
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from backend.components.constraints import get_system_prompt, CONVERSATIONAL_PROMPT, format_docs
from backend.services.search import discover_workspace_documents
from local_function_app.function_app import run_ingestion_pipeline, HOT_FOLDER_DIR
from backend.state.graph_state import GraphState
from backend.utils.app_utils import save_conversation, list_saved_conversations, load_saved_conversations, load_saved_conversation, save_chat_history, format_history_as_text, chat_sessions
from backend.utils.fallback_utils import rewrite_fallback
from backend.logging.sass_logger import setup_logging
from backend.services.orchestrator import startup_services
from backend.utils.isolation_kb_utils import get_accessible_affiliates, load_user_directory_groups, verify_user_ingest_access
from settings import DB_DIR

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

class LoginRequest(BaseModel):
    username: str

class ChatRequest(BaseModel):
    username: str
    question: str
    affiliate: str 

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
async def get_affiliates(username: str):
    username = username.strip()
    return get_accessible_affiliates(username, services.get("user_directory"))

@app.get("/api/user/groups")
async def get_user_groups_endpoint(
    username: str, 
    x_user_id: str = Header(None)
):
    """Exposes directory profile groups to the frontend application layout layer."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing authorization context principal.")
    # Reuses the load_user_directory_groups helper function we wrote earlier!
    groups = load_user_directory_groups(username)
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
async def secure_chat(request: ChatRequest):
    username = request.username.strip()
    question = request.question.strip()
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
        final_state = services.get("compiled_workflow").invoke(initial_state)
    except Exception as e:
        logger.error(f"[x] Workflow failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent workflow failed.")
    # ---------- Build Prompt based on Decision Boundary ----------
    is_conversational = final_state.get("relevance_grade") == "conversational"
    if is_conversational:
        # Build conversational friendly response template
        history_transcript = format_history_as_text(chat_sessions[username])
        prompt = CONVERSATIONAL_PROMPT.format(
            username=username,
            question=question,
            history=history_transcript
        )
    else:
        # Build advanced RAG query template with final retrieved context
        final_question = final_state.get("original_question", question)
        documents = final_state.get("documents", [])
        accessible_affiliates_str = ", ".join(final_state.get("target_scope", target_scope))
        instructions = get_system_prompt(username, accessible_affiliates_str)
        formatted_docs = format_docs(documents)
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
    """
    Scans the archive folder for the given affiliate and returns a list 
    of indexed files to populate the Self-Service audit table.
    """
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing authorization principal context.")
    # target the archived path, e.g., local-rag/index-db/Affiliate_A_Pages/
    archive_target_zone = os.path.join(HOT_FOLDER_DIR, f"{affiliate}_Pages")
    manifest_records = []
    if not os.path.exists(archive_target_zone):
        return manifest_records # Return clean empty array if folder hasn't been generated yet
    try:
        # Loop through files in the archive directory to build our UI dashboard table rows
        for filename in os.listdir(archive_target_zone):
            file_path = os.path.join(archive_target_zone, filename) 
            if os.path.isfile(file_path) and filename.lower().endswith('.pdf'):
                file_stats = os.stat(file_path)    
                # Mock up an ID string and details matching your DocumentRecord interface
                manifest_records.append({
                    "id": f"id_{filename.replace('.', '_')}", 
                    "filename": filename,
                    "uploadDate": datetime.datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                    "fileSize": f"{round(file_stats.st_size / 1024, 1)} KB" if hasattr(file_stats, 'st_size') else f"{round(file_stats.st_size / 1024, 1)} KB"
                })
        return manifest_records
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to scan file system indexes: {str(e)}")

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
async def get_saved_conversations(username: str):
    conversations = load_saved_conversations(username)
    # conversations is a LIST, not a dict
    titles = [c["title"] for c in conversations]
    return {"titles": titles}

@app.get("/api/saved-conversations/{title}")
async def get_saved_conversation(username: str, title: str):
    conversations = load_saved_conversations(username)
    # find the conversation in the list
    for convo in conversations:
        if convo["title"] == title:
            return {
                "title": convo["title"],
                "messages": convo["messages"]
            }
    return {"error": "Conversation not found"}

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "database_connected": os.path.exists(DB_DIR)}