import asyncio
import os
import datetime
import json
import logging
import sys
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Query
from typing import List
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Modernized LangChain Imports
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.llms import Ollama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

import constraints
from backend.search import search_service
sys.path.append(os.path.join(os.path.dirname(__file__), "local-function-app"))
from local_function_app.function_app import run_ingestion_pipeline, HOT_FOLDER_DIR

# This automatically finds the folder relative to where app.py is living
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIRECTORY_JSON_PATH = os.path.join(PROJECT_ROOT, "directory.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "chroma_db")
USER_DIRECTORY_FILE = os.path.join(BASE_DIR, "directory.json")
CHAT_HISTORY_FILE = os.path.join(BASE_DIR, "chat_history.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = FastAPI(title="Secure RAG Engine API")
logger = logging.getLogger("SASS Logger")
logger.setLevel(logging.DEBUG)
logger.info("--- BOOTING SECURE KNOWLEDGE ASSISTANT ---")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. LOAD USER DIRECTORY (Simulated Entra ID)
if not os.path.exists(USER_DIRECTORY_FILE):
    raise RuntimeError(f"Could not find {USER_DIRECTORY_FILE}")
with open(USER_DIRECTORY_FILE, "r") as f:
    user_directory = json.load(f)

logger.info("\nAvailable Simulated Users: jack_admin, sonic_user, dragon_ball_user")
# 2. CONNECT TO DATABASE AND LLM ONCE ON STARTUP
logger.info("[*] Initializing local HuggingFace embedding engine and connecting to Chroma...")
embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)
llm = Ollama(model="llama3")
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
        logger.info("[✓] Stateful chat history backed up to local memory.")
    except Exception as e:
        logger.error(f"[-] Failed to write chat history backup: {e}")

def load_chat_history() -> dict:
    """Reads local JSON history and reconstructs live LangChain message class objects."""
    if not os.path.exists(CHAT_HISTORY_FILE):
        return {}
    
    if os.path.getsize(CHAT_HISTORY_FILE) == 0:
        logger.warning(f"[!] {CHAT_HISTORY_FILE} was empty. Creating clean sessions dictionary.")
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
        
        logger.info(f"[✓] Restored stateful sessions for {len(sessions)} profiles from disk.")
        return sessions
    except Exception as e:
        logger.error(f"[-] Failed to restore session history: {e}")
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

    
# In-Memory Session Storage for Chat History
chat_sessions = load_chat_history()
class LoginRequest(BaseModel):
    username: str

@app.post("/api/login")
async def verify_identity_profile(payload: LoginRequest):
    """
    Secure single-point identity verification gatekeeper.
    Validates identity claims against the internal server directory file.
    """
    username = payload.username.strip()
    
    if username not in user_directory:
        logger.warning(f"[-] Security Audit: Unauthorized identity profile attempt: '{username}'")
        raise HTTPException(status_code=401, detail="Unauthorized: Profile missing from security directory.")
        
    logger.info(f"[+] Security Audit: Validated profile session for '{username}'")
    return {"status": "authenticated", "principal": username}
    
class ChatRequest(BaseModel):
    username: str
    question: str
    affiliate: str 

@app.get("/api/affiliates")
async def get_affiliates(username: str):
    username = username.strip()
    if username not in user_directory:
        raise HTTPException(status_code=401, detail="Unauthorized: User profile missing.")
    
    user_claims = user_directory[username]
    user_groups = user_claims.get("groups", [])
    
    accessible_affiliates = []
    if "Affiliate_A" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_A")
    if "Affiliate_B" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_B")
        
    return {"accessible_affiliates": accessible_affiliates}

@app.get("/api/discover-docs")
async def discover_documents(affiliate: str = "All"):
    """
    Simulates an Azure AI Search broad discovery sweep. 
    It requests all unique filenames within the user's active security clearance scope.
    """
    try:
        # Calls the dynamic metadata extraction layer inside search.py
        files = search_service.discover_workspace_documents(affiliate)
        return {"accessible_documents": files}
    except Exception as e:
        logger.error(f"[-] Catalog discovery anomaly: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def secure_chat(request: ChatRequest):
    username = request.username.strip()
    question = request.question.strip()
    requested_affiliate = request.affiliate.strip()

    if username not in user_directory:
        raise HTTPException(status_code=401, detail="Unauthorized: User not found.")

    user_claims = user_directory[username]
    user_groups = user_claims["groups"]
    logger.info(f"[+] Successfully acquired user: {user_claims['email']}")
    logger.info(f"[+] Token Groups Retrieved: {user_groups}")

    # Calculate authorization limits
    accessible_affiliates = []
    if "Affiliate_A" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_A")
    if "Affiliate_B" in user_groups or "Global_Admins" in user_groups:
        accessible_affiliates.append("Affiliate_B")

    # Anti-Spoofing Check
    if requested_affiliate != "All" and requested_affiliate not in accessible_affiliates:
        raise HTTPException(status_code=403, detail="Security Breach: Unauthorized affiliate scope requested.")

    # Apply data trimming logic
    target_scope = accessible_affiliates if requested_affiliate == "All" else [requested_affiliate]

    retriever = search_service.get_secure_retriever(
        target_scope=target_scope, 
        query_text=question, 
        top_k=3
    )

    # Warm-initialize state list cleanly to prevent KeyError failures on new sessions
    if username not in chat_sessions:
        chat_sessions[username] = []

    # Get conversational history formatted for the prompt layout
    history_transcript = format_history_as_text(chat_sessions[username])

    # === DYNAMIC INTENT DETECTION & COGNITIVE BYPASS ===
    conversational_triggers = ["save", "history", "remember", "clear", "hello", "hi", "hey", "who are you", "thank you", "thanks"]
    is_conversational_query = any(trigger in question.lower() for trigger in conversational_triggers)

    if is_conversational_query:
        # Soften system instructions for historical inquiries or conversational greetings
        system_instructions = (
            "You are a helpful and adaptive conversational assistant. "
            "You have full access to current conversation history logs. "
            "Answer the user's conversational query directly, warmly, and concisely."
        )
    else:
        # Enforce strict RAG rules for database document queries
        system_instructions = constraints.get_system_prompt(username=username, affiliate=requested_affiliate)

    # Build the strict structural prompt layout with past dialogue awareness
    template = f"""{system_instructions}

CONVERSATION HISTORY LOG:
{{history}}

CONTEXT FROM CURRENT WORKSPACE DOCUMENTS:
{{context}}

CURRENT QUESTION: 
{{question}}
"""
    prompt = ChatPromptTemplate.from_template(template)

    # 🔗 SECURE PIPELINE LINKING:
    rag_chain = (
        {
            "context": retriever | constraints.format_docs, 
            "history": lambda x: history_transcript,
            "question": RunnablePassthrough()
        }
        | prompt
        | llm
        | StrOutputParser()
    )

    # Keep memory buffer lean
    if len(chat_sessions[username]) > 10:
        chat_sessions[username] = chat_sessions[username][-10:]

    # Define an asynchronous generator to feed the stream
    async def token_streamer():
        full_response = ""
        try:
            async for chunk in rag_chain.astream(question):
                full_response += chunk
                yield chunk
            
            # --- OUTSIDE STREAM LOOP ---
            # Append conversation state and write to disk ONLY after stream has fully finished!
            chat_sessions[username].append(HumanMessage(content=question))
            chat_sessions[username].append(AIMessage(content=full_response))
            save_chat_history()
            
        except Exception as e:
            logger.error(f"[-] Stream disruption: {str(e)}", exc_info=True)
            yield f"\n[Stream Error: {str(e)}]"

    logger.info(f"[*] Initializing secured token stream for {username}")
    return StreamingResponse(token_streamer(), media_type="text/plain")

def load_user_directory_groups(username: str) -> List[str]:
    """Reads directory.json dynamically to collect the security group claims array."""
    if not os.path.exists(DIRECTORY_JSON_PATH):
        print(f"[!] Directory map file missing at: {DIRECTORY_JSON_PATH}")
        return []
        
    try:
        with open(DIRECTORY_JSON_PATH, "r") as f:
            directory_data = json.load(f)
            
        # Target user key inside the object
        user_record = directory_data.get(username)
        if user_record and "groups" in user_record:
            return user_record["groups"]
            
    except Exception as e:
        print(f"[!] System processing exception reading directory registry: {e}")
       
    return []

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

def verify_user_ingest_access(username: str, affiliate: str) -> bool:
    """Validates if the user's groups contain the designated administrative Ingesters role."""
    user_groups = load_user_directory_groups(username)
    
    # Global Admins can bypass individual tenant restrictions
    if "Global_Admins" in user_groups:
        return True
        
    required_ingester_group = f"{affiliate} Ingesters"
    return required_ingester_group in user_groups

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
        logger.info(f"[*] Executing absolute database eviction sequence for: {doc_id}")
        
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

        logger.info(f"[*] Sweeping Chroma for Hot Path: {hot_folder_source}")
        logger.info(f"[*] Sweeping Chroma for Archive Path: {archive_folder_source}")

        # 3. DIRECT CHROMACLIENT EVICTION (Bypasses LangChain limitations)
        try:
            # Clear chunks stamped with the ingestion hot folder path
            vector_store._collection.delete(where={"source": hot_folder_source})
            
            # Clear chunks stamped with the archive pages folder path
            vector_store._collection.delete(where={"source": archive_folder_source})
            
            # Clear chunks that might only have the raw filename stamp
            vector_store._collection.delete(where={"source": filename})
            
            logger.info("[✓] Associated vector matrix fragments thoroughly cleared from Chroma collection.")
        except Exception as v_err:
            logger.error(f"[!] Direct collection level eviction encountered an issue: {v_err}")

        # 4. PHYSICAL STORAGE CLEANUP
        if os.path.exists(archive_folder_source):
            os.remove(archive_folder_source)
            logger.info(f"[✓] Physical asset file erased from disk layout: {archive_folder_source}")
        else:
            logger.warning(f"[!] Physical asset was not present on disk canvas: {archive_folder_source}")

        return {"status": "success", "detail": f"Successfully expelled asset: {filename}"}

    except Exception as e:
        logger.error(f"[CRITICAL] Deletion engine sequence failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Core index expulsion failure: {str(e)}")
    
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "database_connected": os.path.exists(DB_DIR)}