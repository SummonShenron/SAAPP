import json
import os
import logging
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langgraph.checkpoint.mongodb import MongoDBSaver

from backend.services.agent_workflow import create_workflow
from backend.services.insights_workflow import create_insight_workflow
from backend.services.memory_search import get_user_memory_vector_store
from backend.utils.db_utils import get_db
from fastapi import HTTPException
from langchain_mongodb import MongoDBAtlasVectorSearch

logger = logging.getLogger("SASS Logger")

def startup_services():
    # 1. CONNECT TO DATABASE
    db = get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection unavailable")
    
    # 2. POPULATE DIRECTORY (Shim)
    user_directory_cursor = db["directory"].find({})
    user_directory = {user["username"]: user for user in user_directory_cursor}
    logger.info(f"Loaded {len(user_directory)} profiles from MongoDB.")

    # 3. INITIALIZE EMBEDDINGS & VECTOR STORE
    logger.info("Initializing embedding engine...")
    # embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    embeddings = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-001",
        output_dimensionality=768
    )
    logger.info("Connecting to MongoDB Atlas Vector Search...")
    vector_store = MongoDBAtlasVectorSearch(
        collection=db["documents"],
        embedding=embeddings,
        index_name="vector_index"
    )

    # 3B. INITIALIZE PERSONAL MEMORY VECTOR STORE (isolated from the shared KB above)
    logger.info("Connecting to personal memory vector search (user_memory_chunks)...")
    user_memory_vector_store = get_user_memory_vector_store(db, embeddings)

    # Verification Log
    logger.info("="*30)
    logger.info(f"VECTOR ENGINE INITIALIZED: {type(vector_store).__name__}")
    if "MongoDB" in str(type(vector_store)):
        logger.info("SUCCESS: App is connected to MongoDB Atlas Vector Search.")
    else:
        logger.error("WARNING: App is NOT using MongoDB!")
    logger.info("="*30)

    # 3C. CHECKPOINTER — persists graph state across turns, keyed by thread_id (see
    # create_workflow's reset_transient_state for how this is kept safe: only fields explicitly
    # meant to survive across turns, like paused_clarification, are left out of the per-turn
    # reset). db is guaranteed non-None here (the guard above already raised otherwise), so no
    # fallback-to-None-checkpointer path is needed for that reason — only for the checkpointer's
    # own setup possibly failing, mirroring how a failed workflow compile below degrades to None
    # rather than crashing startup entirely.
    try:
        checkpointer = MongoDBSaver(client=db.client)
        logger.info("LangGraph checkpointer (MongoDBSaver) initialized.")
    except Exception as e:
        logger.critical(f"Failed to initialize LangGraph checkpointer: {e}")
        checkpointer = None

    # 4. COMPILE WORKFLOWS
    logger.info("Importing and compiling LangGraph workflow execution engine...")
    try:
        compiled_workflow = create_workflow(vector_store, user_memory_vector_store, checkpointer=checkpointer)
        logger.info("Compiled LangGraph Workflow successfully loaded.")
    except Exception as e:
        logger.critical(f"Failed to compile LangGraph workflow: {e}")
        compiled_workflow = None

    try:
        insight_workflow = create_insight_workflow()
        logger.info("Compiled Insight Workflow successfully")
    except Exception as e:
        logger.critical(f"Failed to compile Insight workflow: {e}")
        insight_workflow = None
        
    return {
        "user_directory": user_directory,
        "vector_store": vector_store,
        "user_memory_vector_store": user_memory_vector_store,
        "compiled_workflow": compiled_workflow,
        "insight_workflow": insight_workflow,
        "checkpointer": checkpointer,
    }