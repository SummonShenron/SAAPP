import json
import os
import logging
from langchain_huggingface import HuggingFaceEmbeddings
from backend.services.agent_workflow import create_workflow
from backend.services.insights_workflow import create_insight_workflow
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
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    
    logger.info("Connecting to MongoDB Atlas Vector Search...")
    vector_store = MongoDBAtlasVectorSearch(
        collection=db["documents"],
        embedding=embeddings,
        index_name="saapp_index"
    )
    
    # Verification Log
    logger.info("="*30)
    logger.info(f"VECTOR ENGINE INITIALIZED: {type(vector_store).__name__}")
    if "MongoDB" in str(type(vector_store)):
        logger.info("SUCCESS: App is connected to MongoDB Atlas Vector Search.")
    else:
        logger.error("WARNING: App is NOT using MongoDB!")
    logger.info("="*30)

    # 4. COMPILE WORKFLOWS
    logger.info("Importing and compiling LangGraph workflow execution engine...")
    try:
        compiled_workflow = create_workflow(vector_store)
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
        "compiled_workflow": compiled_workflow,
        "insight_workflow": insight_workflow
    }