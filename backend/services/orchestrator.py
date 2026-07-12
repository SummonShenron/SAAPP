import json
import os
import logging
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from backend.services.agent_workflow import create_workflow
from settings import USER_DIRECTORY_FILE, DB_DIR
from backend.models.attachment import Attachment
logger = logging.getLogger("SASS Logger")

def startup_services():
    # 1. LOAD USER DIRECTORY (Simulated Entra ID)
    if not os.path.exists(USER_DIRECTORY_FILE):
        raise RuntimeError(f"Could not find {USER_DIRECTORY_FILE}")
    with open(USER_DIRECTORY_FILE, "r") as f:
        user_directory = json.load(f)

    logger.info("Available Simulated Users: jack_admin, sonic_user, dragon_ball_user")
    # 2. CONNECT TO DATABASE AND LLM ONCE ON STARTUP
    logger.info("Initializing local HuggingFace embedding engine and connecting to Chroma...")
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)
    # llm = Ollama(model="llama3")

    # 3. COMPILE THE LANGGRAPH WORKFLOW ENGINE
    logger.info("Importing and compiling LangGraph workflow execution engine...")
    try:
        compiled_workflow = create_workflow(vector_store)
        logger.info("Compiled LangGraph Workflow successfully loaded.")
    except ImportError:
        try:
            compiled_workflow = create_workflow(vector_store)
            logger.info("Compiled LangGraph Workflow successfully loaded.")
        except Exception as e:
            logger.critical(f"Failed to compile LangGraph workflow: {e}")
            compiled_workflow = None
    return {
        "user_directory": user_directory,
        "vector_store": vector_store,
        "compiled_workflow": compiled_workflow,
    }        