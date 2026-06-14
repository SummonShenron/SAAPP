import os
import shutil
import logging
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

logger = logging.getLogger("SASS Logger")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = os.path.join(BASE_DIR, "source_docs")
DB_DIR = os.path.join(BASE_DIR, "chroma_db")

def run_ingestion():
    # Make sure base structure paths exist out-of-the-box
    os.makedirs(os.path.join(SOURCE_DIR, "Affiliate_A"), exist_ok=True)
    os.makedirs(os.path.join(SOURCE_DIR, "Affiliate_B"), exist_ok=True)
    # 1. Clear out stale vector storage to avoid double-indexing collisions
    if os.path.exists(DB_DIR):
        logger.info("[*] Purging legacy database index...")
        shutil.rmtree(DB_DIR)

    raw_documents = []
    
    # 2. Sweep target data structures
    if not os.path.exists(SOURCE_DIR):
        logger.error(f"[-] Missing source directory framework at {SOURCE_DIR}")
        return

    # Look through subfolders inside source_docs
    for workspace in os.listdir(SOURCE_DIR):
        workspace_path = os.path.join(SOURCE_DIR, workspace)
        
        if os.path.isdir(workspace_path):
            logger.info(f"[*] Processing document pipeline for scope: {workspace}")
            
            for file_name in os.listdir(workspace_path):
                # Process text and markdown structures
                if file_name.endswith(('.txt', '.md')):
                    file_path = os.path.join(workspace_path, file_name)
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        
                        # Build standard Document wrapped with hard security tags
                        doc = Document(
                            page_content=content,
                            metadata={
                                "source": file_path,
                                "affiliate": workspace, # Tracks "Affiliate_A" or "Affiliate_B"
                                "page_label": "1" # Default placeholder fallback
                            }
                        )
                        raw_documents.append(doc)
                    except Exception as e:
                        logger.error(f"[-] Failed processing {file_name}: {str(e)}")

    if not raw_documents:
        logger.warning("[!] Framework warning: No text files detected in workspace scopes. DB remains empty.")
        return

    # 3. Structural Chunk Splitting
    logger.info(f"[*] Compiling {len(raw_documents)} data footprints. Splitting chunks...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
    split_chunks = text_splitter.split_documents(raw_documents)

    # 4. Process Local Core Embeddings & Save to Chroma
    logger.info("[*] Activating HuggingFace Vectorizer Engine (all-MiniLM-L6-v2)...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    
    logger.info(f"[*] Depositing {len(split_chunks)} secured segments into local index vault...")
    Chroma.from_documents(split_chunks, embeddings, persist_directory=DB_DIR)
    logger.info("[+] Data ingestion completely compiled and local vectors locked down!")

if __name__ == "__main__":
    run_ingestion()