# local-function-app/function_app.py
import os
import glob
import shutil
import tempfile
import logging
from pymongo import MongoClient
from gridfs import GridFS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

# --- Standalone Self-Contained Configuration ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "your_database_name")
HOT_FOLDER_DIR = os.getenv("HOT_FOLDER_DIR", "./index-db")
DB_DIR = os.getenv("DB_DIR", "./chroma_db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger("StandaloneIngestion")
logger.setLevel(logging.INFO)

def get_db_client():
    """Independent DB connection with a short timeout to prevent locking up."""
    try:
        # 2-second timeout so the pipeline falls back quickly if Mongo is offline
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.server_info()  # Force a connection handshake test
        return client[DB_NAME]
    except Exception as e:
        logger.warning(f"[-] MongoDB connection failed: {e}. Falling back to Local Mode.")
        return None

def run_ingestion_pipeline():
    """Executes the multi-tenant document chunking and vector database indexing workflow."""
    logger.info("--- STARTING MULTI-TENANT INGESTION PIPELINE ---")
    
    db = get_db_client()
    fs = GridFS(db) if db is not None else None
    
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

    if os.path.exists(DB_DIR):
        vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)
    else:
        vector_store = None

    # =========================================================================
    # 1. CLOUD MODE: Process from GridFS (Database Containers)
    # =========================================================================
    if db is not None and fs is not None:
        logger.info("[+] MongoDB Connected. Executing GridFS Cloud Mode.")
        
        # Read files explicitly tagged as "raw"
        files_to_process = list(fs.find({"metadata.status": "raw"}))

        if not files_to_process:
            logger.info("Raw container empty. No new PDFs found to process in MongoDB.")
            return False
        
        for file_obj in files_to_process:
            filename = file_obj.filename
            affiliate = file_obj.metadata.get("affiliate", "Unknown") if file_obj.metadata else "Unknown"
            logger.info(f"-> Processing Raw Asset: {filename} [Mapped to: {affiliate}]")
            
            try:
                # Use tempfile module safely
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                    tmp.write(file_obj.read())
                    tmp.flush()
                    
                    loader = PyPDFLoader(tmp.name)
                    documents = loader.load()
                    
                    # Stamp chunks with metadata so we can delete them if something goes wrong
                    for doc in documents:
                        doc.metadata["affiliate"] = affiliate
                        doc.metadata["source"] = filename 
                        
                    chunks = text_splitter.split_documents(documents)
                    
                    if vector_store is None:
                        vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
                    else:
                        vector_store.add_documents(chunks)
                
                # MOVE TO PAGES CONTAINER (Success)
                # By updating status to "pages", it transitions out of raw and into your permanent archive
                db["fs.files"].update_one(
                    {"_id": file_obj._id}, 
                    {"$set": {"metadata.status": "pages"}}
                )
                logger.info(f"   [✓] Success: Indexed and moved {filename} to Pages container.")
                
            except Exception as e:
                # DELETE ON FAILURE (Rollback)
                logger.error(f"   [X] Error processing {filename}: {e}. Initiating rollback...")
                
                # Expel file entirely from GridFS
                fs.delete(file_obj._id)
                logger.info(f"       -> Deleted {filename} from GridFS storage.")
                
                # Scrub any orphaned vector chunks out of Chroma
                if vector_store is not None:
                    try:
                        vector_store._collection.delete(where={"source": filename})
                        logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                    except Exception as v_err:
                        logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")
                
                raise e

    # =========================================================================
    # 2. LEGACY MODE: Process from Local Filesystem (Local Folder Containers)
    # =========================================================================
    else:
        logger.info("[!] Entering Legacy Mode (Local FS).")
        pdf_pattern = os.path.join(HOT_FOLDER_DIR, "*", "*.pdf")
        pdf_files = glob.glob(pdf_pattern)

        if not pdf_files:
            logger.info("Hot folder empty. No new PDFs found to process.")
            return False

        for pdf_path in pdf_files:
            folder_name = os.path.basename(os.path.dirname(pdf_path))
            
            # Skip archiving folders
            if folder_name.endswith("_Pages"):
                continue
                
            filename = os.path.basename(pdf_path)
            logger.info(f"-> Processing Local Asset: {filename} [Mapped to: {folder_name}]")
            
            try:
                loader = PyPDFLoader(pdf_path)
                documents = loader.load()
                
                for doc in documents:
                    doc.metadata["affiliate"] = folder_name
                    doc.metadata["source"] = filename  # Stamped for identical rollback targeting
                    
                chunks = text_splitter.split_documents(documents)
                
                if vector_store is None:
                    vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
                else:
                    vector_store.add_documents(chunks)
                    
                # Move to permanent archive directory
                archive_dir = os.path.join(HOT_FOLDER_DIR, f"{folder_name}_Pages")
                os.makedirs(archive_dir, exist_ok=True)
                
                target_destination = os.path.join(archive_dir, filename)
                shutil.move(pdf_path, target_destination)
                logger.info(f"   [✓] Success: Indexed and moved {filename} to {folder_name}_Pages")
                    
            except Exception as e:
                logger.error(f"   [X] Error processing local {filename}: {e}. Initiating local rollback...")
                
                # Delete the corrupt physical file so it doesn't jam the pipeline
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                    logger.info(f"       -> Deleted local target file: {pdf_path}")
                
                # Scrub any partial chunks that made it to Chroma
                if vector_store is not None:
                    try:
                        vector_store._collection.delete(where={"source": filename})
                        logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                    except Exception as v_err:
                        logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")
                
                raise e
            
    logger.info("\nIngestion pipeline execution complete.")
    return True

if __name__ == "__main__":
    run_ingestion_pipeline()