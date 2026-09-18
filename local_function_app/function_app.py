# local-function-app/function_app.py
import os
import glob
import shutil
import tempfile
import logging
import time
import base64
from pymongo import MongoClient
from gridfs import GridFS
from pypdf import PdfReader
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_google_genai import GoogleGenerativeAIEmbeddings, ChatGoogleGenerativeAI
from langchain_mongodb import MongoDBAtlasVectorSearch

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))
# --- Standalone Self-Contained Configuration ---
# This module is deliberately kept free of `backend.*` imports (own Mongo client, own env
# loading) so it stays a fully independent subprocess — a small amount of image-handling logic
# below is duplicated from backend/utils/attachment_utils.py rather than imported, to preserve
# that boundary.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "saapp_database")
HOT_FOLDER_DIR = os.getenv("HOT_FOLDER_DIR", "./index-db")
DB_DIR = os.getenv("DB_DIR", "./chroma_db")

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}
MAX_EMBEDDED_IMAGES_PER_PDF = int(os.getenv("MAX_EMBEDDED_IMAGES_PER_PDF", "5"))
MIN_EMBEDDED_IMAGE_DIMENSION_PX = int(os.getenv("MIN_EMBEDDED_IMAGE_DIMENSION_PX", "100"))
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "gemini-3.5-flash")

# Keep in sync with backend/components/constraints.py:IMAGE_DESCRIPTION_PROMPT
IMAGE_DESCRIPTION_PROMPT = """You are a vision-to-text assistant. Describe this image in detail so a \
future reader who cannot see it can fully understand its content and purpose.

Include:
- What the image depicts (people, objects, scenes, diagrams, screenshots, etc.)
- Any visible text, labels, numbers, or data (transcribe it verbatim)
- Layout or structure if it's a chart, table, diagram, or UI screenshot
- Overall context or apparent purpose of the image

Be thorough and factual — this description will be used in place of the image itself."""

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


def _is_image_filename(filename: str) -> bool:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return ext in IMAGE_EXTENSIONS


def _guess_image_mime_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return IMAGE_MIME_TYPES.get(ext, "application/octet-stream")


def _describe_image_bytes(image_bytes: bytes, mime_type: str) -> str:
    """Vision-LLM description of raw image bytes. Never raises — a failed description must not
    abort the surrounding page-text/document ingestion."""
    message = HumanMessage(content=[
        {"type": "text", "text": IMAGE_DESCRIPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"}},
    ])
    try:
        vision_llm = ChatGoogleGenerativeAI(model=VISION_MODEL_NAME, api_key=os.getenv("GOOGLE_API_KEY"))
        response = vision_llm.invoke([message])
        content = response.content if hasattr(response, "content") else str(response)
        if isinstance(content, list):
            content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        return str(content).strip()
    except Exception as e:
        logger.error(f"Embedded image description failed: {e}")
        return ""


def _extract_embedded_images(reader, page_index, fs, affiliate, source_filename, budget_remaining):
    """Extracts, filters, stores, and describes embedded raster images on one PDF page.
    Returns a list of {gridfs_id, content_type, filename, description} dicts."""
    results = []
    if budget_remaining <= 0:
        return results

    try:
        page = reader.pages[page_index]
        images = list(page.images)
    except Exception as e:
        logger.warning(f"   -> Could not read embedded images on page {page_index}: {e}")
        return results

    for img_file in images:
        if budget_remaining <= 0:
            logger.info(f"   -> Embedded image cap ({MAX_EMBEDDED_IMAGES_PER_PDF}) reached; skipping remainder on page {page_index}.")
            break
        try:
            pil_img = img_file.image
            if pil_img is None:
                continue
            width, height = pil_img.size
            if width < MIN_EMBEDDED_IMAGE_DIMENSION_PX or height < MIN_EMBEDDED_IMAGE_DIMENSION_PX:
                continue  # skip decorative/spacer/icon images

            mime_type = f"image/{(pil_img.format or 'png').lower()}"
            new_id = fs.put(
                img_file.data,
                filename=f"{source_filename}_p{page_index}_{img_file.name}",
                metadata={
                    "affiliate": affiliate,
                    "kind": "kb_embedded_image",
                    "source_document": source_filename,
                    "page": page_index,
                    "content_type": mime_type,
                },
            )
            description = _describe_image_bytes(img_file.data, mime_type)
            results.append({
                "gridfs_id": str(new_id),
                "content_type": mime_type,
                "filename": img_file.name,
                "description": description,
            })
            budget_remaining -= 1
        except Exception as e:
            logger.warning(f"   -> Skipping unreadable embedded image on page {page_index}: {e}")
            continue

    return results

def run_ingestion_pipeline():
    """Executes the multi-tenant document chunking and vector database indexing workflow."""
    print(f"DEBUG: MONGO_URI is currently: {os.getenv('MONGO_URI')}")
    print(f"DEBUG: DB_NAME is currently: {os.getenv('DB_NAME')}")
    logger.info("--- STARTING MULTI-TENANT INGESTION PIPELINE ---")
    
    db = get_db_client()
    fs = GridFS(db) if db is not None else None
    
    # CRITICAL FIX: Swapped HuggingFace for Google Gemini with exact dimensionality matching
    embeddings = GoogleGenerativeAIEmbeddings(
        model="gemini-embedding-001",
        output_dimensionality=768
    )
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
        
        # --- NEW POLLING LOGIC ---
        files_to_process = []
        max_retries = 5
        for i in range(max_retries):
            files_to_process = list(fs.find({"metadata.status": "raw"}))
            if files_to_process:
                break
            logger.info(f"Container empty. Waiting for file (Attempt {i+1}/{max_retries})...")
            time.sleep(2) # Wait 2 seconds before checking again
        # -------------------------

        if not files_to_process:
            logger.info("Raw container empty after retries. No new PDFs found.")
            return False
        
        for file_obj in files_to_process:
            filename = file_obj.filename
            affiliate = file_obj.metadata.get("affiliate", "Unknown") if file_obj.metadata else "Unknown"
            logger.info(f"-> Processing Raw Asset: {filename} [Mapped to: {affiliate}]")

            if _is_image_filename(filename):
                # --- STANDALONE IMAGE BRANCH: the raw upload IS already a GridFS file, so we
                # reuse its own _id as the gridfs_id reference instead of re-storing it. ---
                try:
                    image_bytes = file_obj.read()
                    mime_type = _guess_image_mime_type(filename)
                    description = _describe_image_bytes(image_bytes, mime_type)
                    if not description:
                        description = f"Image file: {filename} (automatic description unavailable)."

                    doc = Document(
                        page_content=description,
                        metadata={
                            "affiliate": affiliate,
                            "source": filename,
                            "page": 0,
                            "doc_type": "standalone_image",
                            "gridfs_id": str(file_obj._id),
                            "content_type": mime_type,
                        },
                    )
                    chunks = text_splitter.split_documents([doc])

                    collection = db["documents"]
                    vector_store = MongoDBAtlasVectorSearch.from_documents(
                        chunks,
                        embeddings,
                        collection=collection,
                        index_name="vector_index"
                    )

                    db["fs.files"].update_one(
                        {"_id": file_obj._id},
                        {"$set": {"metadata.status": "pages", "metadata.content_type": mime_type}}
                    )
                    logger.info(f"   [✓] Success: Indexed standalone image {filename} as a KB chunk.")

                except Exception as e:
                    logger.error(f"   [X] Error processing image {filename}: {e}. Initiating rollback...")
                    fs.delete(file_obj._id)
                    logger.info(f"       -> Deleted {filename} from GridFS storage.")
                    if vector_store is not None:
                        try:
                            vector_store.delete(where={"source": filename})
                            logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                        except Exception as v_err:
                            logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")
                    raise e

                continue  # skip the PDF-loading branch entirely for this file

            # Create file with delete=False to avoid Windows file-locking permission errors
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp_path = tmp.name

            try:
                # 1. Prepare File
                tmp.write(file_obj.read())
                tmp.close()

                # 2. Process
                loader = PyPDFLoader(tmp_path)
                documents = loader.load()

                try:
                    reader = PdfReader(tmp_path)
                except Exception as e:
                    logger.warning(f"   -> Could not open {filename} for embedded-image extraction: {e}")
                    reader = None

                images_used = 0
                for doc in documents:
                    doc.metadata["affiliate"] = affiliate
                    doc.metadata["source"] = filename
                    page_index = doc.metadata.get("page", 0)
                    page_images = []
                    if reader is not None:
                        page_images = _extract_embedded_images(
                            reader, page_index, fs, affiliate, filename,
                            budget_remaining=MAX_EMBEDDED_IMAGES_PER_PDF - images_used,
                        )
                        images_used += len(page_images)
                    doc.metadata["embedded_images"] = page_images
                    if page_images:
                        # Fold descriptions into the searchable text itself — this both makes the
                        # image's content genuinely retrievable by semantic search, and ensures a
                        # page with an embedded image but no surrounding text still produces a
                        # non-empty chunk (an empty page_content gets silently dropped by the text
                        # splitter, which would otherwise lose the embedded_images metadata entirely).
                        image_descriptions = "\n\n".join(
                            f"[Embedded Image: {img['description']}]" for img in page_images if img.get("description")
                        )
                        if image_descriptions:
                            doc.page_content = f"{doc.page_content}\n\n{image_descriptions}".strip()

                chunks = text_splitter.split_documents(documents)

                collection = db["documents"] # Ensure this matches your collection name
                vector_store = MongoDBAtlasVectorSearch.from_documents(
                    chunks,
                    embeddings,
                    collection=collection,
                    index_name="vector_index"
                )

                # 3. Success (Move to pages container)
                db["fs.files"].update_one(
                    {"_id": file_obj._id},
                    {"$set": {"metadata.status": "pages"}}
                )
                logger.info(f"   [✓] Success: Indexed and moved {filename} to Pages container.")

            except Exception as e:
                # DELETE ON FAILURE (Rollback) - THIS IS YOUR ORIGINAL BLOCK
                logger.error(f"   [X] Error processing {filename}: {e}. Initiating rollback...")

                fs.delete(file_obj._id)
                logger.info(f"       -> Deleted {filename} from GridFS storage.")

                if vector_store is not None:
                    try:
                        vector_store.delete(where={"source": filename})
                        logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                    except Exception as v_err:
                        logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")

                raise e # This keeps your original error reporting active

            finally:
                # Cleanup: This runs even if the 'except' block was triggered
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                    logger.info(f"       -> Cleaned up temporary file: {tmp_path}")

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
                        vector_store.delete(where={"source": filename})
                        logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                    except Exception as v_err:
                        logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")
                
                raise e
            
    logger.info("\nIngestion pipeline execution complete.")
    return True

if __name__ == "__main__":
    run_ingestion_pipeline()