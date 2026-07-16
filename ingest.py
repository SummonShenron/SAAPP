# local-function-app/text_ingestion.py
import os
import shutil
import logging
from pymongo import MongoClient
from gridfs import GridFS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

# --- Standalone Configuration ---
logger = logging.getLogger("SASS Text Ingestion")
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(BASE_DIR, "source_docs")
DB_DIR = os.path.join(BASE_DIR, "chroma_db")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "sass_db")

def get_db_client():
    """Independent DB connection with a short timeout to prevent locking up."""
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.server_info()  # Force a connection handshake test
        return client[DB_NAME]
    except Exception as e:
        logger.warning(f"[-] MongoDB connection failed: {e}. Falling back to Local Mode.")
        return None

def run_ingestion():
    # Make sure base structure paths exist out-of-the-box[cite: 4]
    os.makedirs(os.path.join(SOURCE_DIR, "Affiliate_A"), exist_ok=True)
    os.makedirs(os.path.join(SOURCE_DIR, "Affiliate_B"), exist_ok=True)

    db = get_db_client()
    fs = GridFS(db) if db is not None else None

    # Structural Chunk Splitting configuration[cite: 4]
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
    
    logger.info("Activating HuggingFace Vectorizer Engine (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # Load existing Chroma DB incrementally instead of deleting it
    if os.path.exists(DB_DIR):
        vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)
    else:
        vector_store = None

    # =========================================================================
    # 1. CLOUD MODE: Process from GridFS (Database Containers)
    # =========================================================================
    if db is not None and fs is not None:
        logger.info("[+] MongoDB Connected. Executing GridFS Cloud Mode.")
        
        # Pull explicitly tagged 'raw' documents
        files_to_process = list(fs.find({"metadata.status": "raw"}))
        
        # Filter strictly for text/markdown in this pipeline
        text_files = [f for f in files_to_process if f.filename.endswith(('.txt', '.md'))]

        if not text_files:
            logger.info("Raw container empty. No new text/markdown files found.")
            return

        for file_obj in text_files:
            filename = file_obj.filename
            affiliate = file_obj.metadata.get("affiliate", "Unknown") if file_obj.metadata else "Unknown"
            logger.info(f"-> Processing Raw Text Asset: {filename} [Scope: {affiliate}]")
            
            try:
                # Text files can be read directly into memory (no tempfile needed)
                content = file_obj.read().decode("utf-8")
                
                # Build standard Document wrapped with hard security tags[cite: 4]
                doc = Document(
                    page_content=content,
                    metadata={
                        "source": filename,
                        "affiliate": affiliate,
                        "page_label": "1" # Default placeholder fallback[cite: 4]
                    }
                )
                
                chunks = text_splitter.split_documents([doc])
                
                if vector_store is None:
                    vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
                else:
                    vector_store.add_documents(chunks)
                
                # MOVE TO PAGES CONTAINER (Success)
                db["fs.files"].update_one(
                    {"_id": file_obj._id}, 
                    {"$set": {"metadata.status": "pages"}}
                )
                logger.info(f"   [✓] Success: Indexed and moved {filename} to Pages container.")
                
            except Exception as e:
                # DELETE ON FAILURE (Rollback)
                logger.error(f"   [X] Error processing {filename}: {e}. Initiating rollback...")
                fs.delete(file_obj._id)
                if vector_store is not None:
                    try:
                        vector_store._collection.delete(where={"source": filename})
                    except Exception as v_err:
                        pass
                raise e

    # =========================================================================
    # 2. LEGACY MODE: Process from Local Filesystem
    # =========================================================================
    else:
        logger.info("[!] Entering Legacy Mode (Local FS).")
        if not os.path.exists(SOURCE_DIR):
            logger.error(f"Missing source directory framework at {SOURCE_DIR}")
            return

        for workspace in os.listdir(SOURCE_DIR):
            workspace_path = os.path.join(SOURCE_DIR, workspace)
            
            # Skip archive folders if they exist
            if not os.path.isdir(workspace_path) or workspace.endswith("_Pages"):
                continue

            logger.info(f"Processing document pipeline for scope: {workspace}")
            
            for file_name in os.listdir(workspace_path):
                if file_name.endswith(('.txt', '.md')):
                    file_path = os.path.join(workspace_path, file_name)
                    logger.info(f"-> Processing Local Text Asset: {file_name}")
                    
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        doc = Document(
                            page_content=content,
                            metadata={
                                "source": file_name,
                                "affiliate": workspace,
                                "page_label": "1"
                            }
                        )
                        
                        chunks = text_splitter.split_documents([doc])
                        
                        if vector_store is None:
                            vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
                        else:
                            vector_store.add_documents(chunks)
                            
                        # Move to permanent archive directory to prevent reprocessing
                        archive_dir = os.path.join(SOURCE_DIR, f"{workspace}_Pages")
                        os.makedirs(archive_dir, exist_ok=True)
                        shutil.move(file_path, os.path.join(archive_dir, file_name))
                        logger.info(f"   [✓] Success: Indexed and archived {file_name}")
                        
                    except Exception as e:
                        logger.error(f"   [X] Failed processing {file_name}: {str(e)}. Rolling back...")
                        if os.path.exists(file_path):
                            os.remove(file_path) # Expunge corrupt file
                        if vector_store is not None:
                            try:
                                vector_store._collection.delete(where={"source": file_name})
                            except:
                                pass
                        raise e
                        
    logger.info("Data ingestion completely compiled and vectors locked down!")

if __name__ == "__main__":
    run_ingestion()