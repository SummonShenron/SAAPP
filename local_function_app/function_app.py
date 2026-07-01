# local-function-app/function_app.py
import os
import glob
import shutil
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
import logging
# Core Pathing Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DB_DIR = os.path.join(PROJECT_ROOT, "chroma_db")
HOT_FOLDER_DIR = os.path.join(PROJECT_ROOT, "index-db")
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger("Function App Logger")
logger.setLevel(logging.INFO)
def run_ingestion_pipeline():
    """Executes the multi-tenant document chunking and vector database indexing workflow."""
    logger.info("--- STARTING MULTI-TENANT INGESTION PIPELINE ---")
    
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

    if os.path.exists(DB_DIR):
        vector_store = Chroma(persist_directory=DB_DIR, embedding_function=embeddings)
    else:
        vector_store = None

    pdf_pattern = os.path.join(HOT_FOLDER_DIR, "*", "*.pdf")
    pdf_files = glob.glob(pdf_pattern)

    if not pdf_files:
        logger.info("Hot folder empty. No new PDFs found to process.")
        return False

    for pdf_path in pdf_files:
        folder_name = os.path.basename(os.path.dirname(pdf_path))
        
        if folder_name.endswith("_Pages"):
            continue
            
        filename = os.path.basename(pdf_path)
        logger.info(f"-> Processing: {filename} [Mapped to: {folder_name}]")
        
        try:
            loader = PyPDFLoader(pdf_path)
            documents = loader.load()
            
            for doc in documents:
                doc.metadata["affiliate"] = folder_name
                
            chunks = text_splitter.split_documents(documents)
            
            if vector_store is None:
                vector_store = Chroma.from_documents(chunks, embeddings, persist_directory=DB_DIR)
            else:
                vector_store.add_documents(chunks)
                
            archive_dir = os.path.join(HOT_FOLDER_DIR, f"{folder_name}_Pages")
            os.makedirs(archive_dir, exist_ok=True)
            
            target_destination = os.path.join(archive_dir, filename)
            shutil.move(pdf_path, target_destination)
            logger.info(f"   Successfully indexed and archived to: {folder_name}_Pages\\{filename}")
                
        except Exception as e:
            logger.info(f"Error processing {pdf_path}: {e}")
            raise e
            
    logger.info("\nIngestion complete. Hot folder cleared of processed sources.")
    return True

# Allows script to still be executed directly via terminal/powershell
if __name__ == "__main__":
    run_ingestion_pipeline()