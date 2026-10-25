# local-function-app/function_app.py
import os
import glob
import shutil
import tempfile
import logging
import re
import json
import time
import uuid
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

try:
    from . import json_chunks
    from . import text_files
    from . import spreadsheet_files
except ImportError:  # run as a script (the upload starts it that way), where this folder is on the path instead of being a package
    import json_chunks
    import text_files
    import spreadsheet_files

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
- If it's a letter, bill, statement, form, notice, or message: transcribe ALL of its text in order, and make sure every date, deadline, amount, \
account or reference number, phone number, web address, sender name, and signature is written out exactly as printed. Say so plainly where \
something is cut off, blurry, or unreadable instead of guessing it.

Be thorough and factual — this description will be used in place of the image itself."""

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger("StandaloneIngestion")
logger.setLevel(logging.INFO)

# --- Knowledge graph (GraphRAG) -----------------------------------------------------------------------------
# After a document's chunks are indexed, the key entities in them and the relationships between those entities are extracted by a small model and
# stored as edges in the `graph_edges` collection: one document per edge, {affiliate, source, s, t, relation}. Retrieval (backend/state/graph_db.py)
# reads only the edges of the knowledge bases a person may use, and the delete endpoint removes a file's edges with its chunks. The edge shape is the
# contract between the two sides. This is best effort by design: a failure here is logged and skipped, it never fails or rolls back an upload.
# Keep the prompt in sync with backend/components/constraints.py:RELATIONSHIP_PROMPT.
RELATIONSHIP_PROMPT = """
Read the text below and list the facts about how its key entities relate to each other. An entity is a person, character, place, organization,
product, technology, event or concept that the text names.

Rules:
- Use short, canonical names for entities (a person's usual name, not "he" or "the author").
- Each relationship is one direct statement from the text: subject ('s'), target ('t'), and a short relation phrase of one to four words ('relationship').
- Only include relationships the text actually states. Do not guess or add outside knowledge.
- At most 25 relationships.

Return ONLY a JSON object with the key 'relationships', for example:
{{"relationships": [{{"s": "Tails", "t": "Sonic", "relationship": "best friend of"}}]}}

Text: {text}

JSON Output:
"""
GRAPH_EDGES_COLLECTION = "graph_edges"
GRAPH_BUILD_ON_INGEST = os.getenv("GRAPH_BUILD_ON_INGEST", "true").strip().lower() not in ("0", "false", "no", "off")
GRAPH_MODEL_NAME = os.getenv("GRAPH_MODEL_NAME", "gemini-3.1-flash-lite")
GRAPH_BATCH_CHARS = int(os.getenv("GRAPH_BATCH_CHARS", "6000"))                 # text sent to the model per call
GRAPH_MAX_BATCHES = int(os.getenv("GRAPH_MAX_BATCHES", "12"))                   # calls per file, however long it is
GRAPH_TIME_BUDGET_SECONDS = float(os.getenv("GRAPH_TIME_BUDGET_SECONDS", "45"))  # the upload request waits for this, so it stops at a budget
GRAPH_MAX_NAME_CHARS = 80
GRAPH_MAX_EDGES_PER_SOURCE = 300

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


def _batch_texts(texts, limit):
    """Consecutive texts grouped into blocks of about `limit` characters (a single text longer than the limit is its own block, cut to it)."""
    batches, current, size = [], [], 0
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        if current and size + len(text) > limit:
            batches.append("\n\n".join(current))
            current, size = [], 0
        current.append(text[:limit])
        size += min(len(text), limit)
    if current:
        batches.append("\n\n".join(current))
    return batches


def _parse_relationships(raw) -> list:
    """The relationships list out of a model reply (which may wrap its JSON in a code fence); [] when it is not usable JSON."""
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except Exception:
            return []
    items = data.get("relationships") if isinstance(data, dict) else None
    return items if isinstance(items, list) else []


def _clean_relationships(items) -> list:
    """Both ends and the relation are short non-empty strings, no self-loops, no duplicates, a cap per file."""
    kept, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        s_name, t_name, relation = (re.sub(r"\s+", " ", str(item.get(k) or "")).strip() for k in ("s", "t", "relationship"))
        if not s_name or not t_name or not relation:
            continue
        if max(len(s_name), len(t_name), len(relation)) > GRAPH_MAX_NAME_CHARS or s_name.lower() == t_name.lower():
            continue
        key = (s_name.lower(), t_name.lower(), relation.lower())
        if key in seen:
            continue
        seen.add(key)
        kept.append({"s": s_name, "t": t_name, "relationship": relation})
        if len(kept) >= GRAPH_MAX_EDGES_PER_SOURCE:
            break
    return kept


def extract_relationships(texts) -> list:
    """Relationships from a file's text, a few thousand characters per model call, within a call and time budget. Never raises."""
    batches = _batch_texts(texts, GRAPH_BATCH_CHARS)[:GRAPH_MAX_BATCHES]
    if not batches:
        return []
    started = time.monotonic()
    found = []
    try:
        graph_llm = ChatGoogleGenerativeAI(model=GRAPH_MODEL_NAME, api_key=os.getenv("GOOGLE_API_KEY"), temperature=0)
    except Exception as e:
        logger.error(f"Knowledge graph model could not be created: {e}")
        return []
    for batch in batches:
        if time.monotonic() - started > GRAPH_TIME_BUDGET_SECONDS:
            logger.info("   -> Knowledge graph: time budget reached, keeping what was found so far.")
            break
        try:
            response = graph_llm.invoke(RELATIONSHIP_PROMPT.format(text=batch))
            content = response.content if hasattr(response, "content") else str(response)
            if isinstance(content, list):
                content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
            found.extend(_parse_relationships(content))
        except Exception as e:
            logger.error(f"   -> Knowledge graph extraction failed for one block: {e}")
    return _clean_relationships(found)


def save_graph_edges(db, affiliate: str, source: str, relationships) -> int:
    """Replace the edges one file produced in one knowledge base. Returns how many were stored."""
    edges = _clean_relationships(relationships)
    collection = db[GRAPH_EDGES_COLLECTION]
    collection.delete_many({"affiliate": affiliate, "source": source})
    if edges:
        collection.insert_many([{"affiliate": affiliate, "source": source, "s": e["s"], "t": e["t"], "relation": e["relationship"]} for e in edges])
    return len(edges)


def index_graph_for_source(db, affiliate: str, source: str, texts) -> int:
    """Extract and store a file's relationships. Best effort: logs and returns 0 on any failure, so an upload never fails because of the graph."""
    if not GRAPH_BUILD_ON_INGEST or db is None:
        return 0
    try:
        edges = extract_relationships(texts)
        stored = save_graph_edges(db, affiliate, source, edges)
        logger.info(f"   -> Knowledge graph: {stored} relationships stored for {source}.")
        return stored
    except Exception as e:
        logger.error(f"   -> Knowledge graph step failed for {source}: {e}")
        return 0


def delete_graph_edges(db, affiliate: str, source: str) -> None:
    """Remove a file's edges (used when its ingestion is rolled back). Never raises."""
    try:
        if db is not None:
            db[GRAPH_EDGES_COLLECTION].delete_many({"affiliate": affiliate, "source": source})
    except Exception as e:
        logger.error(f"   -> Could not clear knowledge graph edges for {source}: {e}")


def _is_image_filename(filename: str) -> bool:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return ext in IMAGE_EXTENSIONS


def _is_json_filename(filename: str) -> bool:
    return filename.lower().endswith((".json", ".jsonl", ".ndjson"))


def _redacted_mongo_host(uri) -> str:
    """The host part of a MongoDB connection string, never the user name, password or options."""
    text = str(uri or "")
    if not text:
        return "(not set)"
    after_scheme = text.split("://", 1)[-1]
    return after_scheme.rsplit("@", 1)[-1].split("/", 1)[0].split("?", 1)[0] or "(unreadable)"


def _is_markdown_filename(filename: str) -> bool:
    return filename.lower().endswith((".md", ".markdown"))


def _is_text_filename(filename: str) -> bool:
    return filename.lower().endswith((".txt", ".log"))


def _is_docx_filename(filename: str) -> bool:
    return filename.lower().endswith(".docx")


SPREADSHEET_MIME_TYPES = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
}


def _is_spreadsheet_filename(filename: str) -> bool:
    return filename.lower().endswith((".csv", ".tsv", ".xlsx", ".xlsm"))


def _replace_earlier_version(db, fs, new_file_id, affiliate: str, filename: str, ingest_id: str) -> None:
    """Called only once the new JSON file's chunks are safely stored: removes what an earlier upload of the same file name left in this knowledge base (its chunks, which
    carry another ingest_id, and its stored copy), so re-exporting a file replaces it instead of piling up duplicates. A failed re-upload never gets here, so the earlier
    version survives it."""
    db["documents"].delete_many({"source": filename, "affiliate": affiliate, "ingest_id": {"$ne": ingest_id}})
    for earlier in list(fs.find({"filename": filename, "metadata.affiliate": affiliate, "metadata.status": "pages"})):
        if earlier._id != new_file_id:
            fs.delete(earlier._id)
            logger.info(f"       -> Replaced an earlier upload of {filename}.")



def _index_text_upload(db, fs, embeddings, text_splitter, file_obj, filename: str, affiliate: str, kind: str) -> None:
    """Indexes one .txt/.log, .md/.markdown or .docx upload: read it (text_files.py), chunk it, embed it, replace any earlier upload of the same name, add it to the graph, mark it done.

    A FILE THAT FAILS LEAVES NOTHING BEHIND. Whatever THIS upload created is removed: its chunks (by its own ingest_id), its stored copy, and its graph edges when it got as far as touching
    the graph or had already replaced the earlier version (whose edges are then gone with it). An earlier version that is still in place is left exactly as it was, edges included, so a
    failed re-upload never damages the file it was meant to replace.
      - A file that simply cannot be used (empty, binary, too big, not a Word document: text_files.TextIngestError) is removed completely and the other uploads carry on, as for a JSON file
        that is not JSON.
      - Any other failure is a real fault: the same complete removal, then the error is raised to stop the run and keep the original error reporting, as for a PDF."""
    ingest_id = uuid.uuid4().hex
    replaced_earlier = graph_touched = False
    try:
        raw = file_obj.read()
        if kind == "docx":
            document = text_files.build_docx_document(raw, filename, affiliate, Document, ingest_id)
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif kind == "spreadsheet":
            text = spreadsheet_files.spreadsheet_text(raw, filename)
            document = text_files._document(Document, text, filename, affiliate, ingest_id, "spreadsheet")
            content_type = SPREADSHEET_MIME_TYPES.get(filename.lower().rsplit(".", 1)[-1], "application/octet-stream")
        else:
            doc_type = "markdown" if kind == "markdown" else "text"
            document = text_files.build_text_document(raw, filename, affiliate, Document, ingest_id, doc_type)
            content_type = "text/markdown" if kind == "markdown" else "text/plain"
        chunks = text_splitter.split_documents([document])
        if not chunks:
            raise text_files.TextIngestError("The file has no readable text in it.")
        if len(chunks) > text_files.MAX_CHUNKS:
            raise text_files.TextIngestError(f"The file would make {len(chunks)} chunks, over the {text_files.MAX_CHUNKS} limit for one file.")

        MongoDBAtlasVectorSearch.from_documents(
            chunks,
            embeddings,
            collection=db["documents"],
            index_name="vector_index"
        )
        replaced_earlier = True
        _replace_earlier_version(db, fs, file_obj._id, affiliate, filename, ingest_id)
        graph_touched = True
        index_graph_for_source(db, affiliate, filename, [c.page_content for c in chunks])

        db["fs.files"].update_one(
            {"_id": file_obj._id},
            {"$set": {"metadata.status": "pages", "metadata.content_type": content_type}}
        )
        logger.info(f"   [✓] Success: Indexed {kind} file {filename} as {len(chunks)} chunks.")

    except Exception as e:
        logger.error(f"   [X] Error processing {kind} file {filename}: {e}. Initiating rollback...")
        try:
            db["documents"].delete_many({"source": filename, "affiliate": affiliate, "ingest_id": ingest_id})
        except Exception as clean_err:
            logger.error(f"       -> Could not remove partial chunks for {filename}: {clean_err}")
        if replaced_earlier or graph_touched:
            try:
                delete_graph_edges(db, affiliate, filename)
            except Exception as clean_err:
                logger.error(f"       -> Could not remove graph edges for {filename}: {clean_err}")
        fs.delete(file_obj._id)
        logger.info(f"       -> Deleted {filename} from GridFS storage.")
        if not isinstance(e, text_files.TextIngestError):
            raise e      # a real fault stops the run with its original error; a file that simply cannot be used must not stop the other uploads

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
    # Which database this run talks to, WITHOUT the credentials: the connection string holds the user name and password, and stdout ends up in logs and test output.
    print(f"DEBUG: MONGO_URI host is: {_redacted_mongo_host(os.getenv('MONGO_URI'))}")
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

                    index_graph_for_source(db, affiliate, filename, [c.page_content for c in chunks])

                    db["fs.files"].update_one(
                        {"_id": file_obj._id},
                        {"$set": {"metadata.status": "pages", "metadata.content_type": mime_type}}
                    )
                    logger.info(f"   [✓] Success: Indexed standalone image {filename} as a KB chunk.")

                except Exception as e:
                    logger.error(f"   [X] Error processing image {filename}: {e}. Initiating rollback...")
                    fs.delete(file_obj._id)
                    logger.info(f"       -> Deleted {filename} from GridFS storage.")
                    delete_graph_edges(db, affiliate, filename)
                    if vector_store is not None:
                        try:
                            vector_store.delete(where={"source": filename})
                            logger.info(f"       -> Scrubbed orphaned vectors for {filename} from Chroma.")
                        except Exception as v_err:
                            logger.error(f"       -> Failed to scrub Chroma collection: {v_err}")
                    raise e

                continue  # skip the PDF-loading branch entirely for this file

            if _is_json_filename(filename):
                # --- JSON BRANCH: any JSON file, read by its shape (see json_chunks.py), then the same embed / graph / mark-as-done steps as every other file. ---
                ingest_id = uuid.uuid4().hex
                try:
                    documents = json_chunks.build_documents(file_obj.read(), filename, affiliate, Document, ingest_id)
                    chunks = text_splitter.split_documents(documents)

                    vector_store = MongoDBAtlasVectorSearch.from_documents(
                        chunks,
                        embeddings,
                        collection=db["documents"],
                        index_name="vector_index"
                    )
                    _replace_earlier_version(db, fs, file_obj._id, affiliate, filename, ingest_id)
                    index_graph_for_source(db, affiliate, filename, [c.page_content for c in chunks])

                    db["fs.files"].update_one(
                        {"_id": file_obj._id},
                        {"$set": {"metadata.status": "pages", "metadata.content_type": "application/json"}}
                    )
                    logger.info(f"   [✓] Success: Indexed JSON file {filename} as {len(chunks)} chunks.")

                except Exception as e:
                    # Roll back only what THIS upload made: its own chunks (by ingest_id) and its stored copy. An earlier version of the same file is left as it was,
                    # and so are its graph edges (the graph step replaces them itself on success).
                    logger.error(f"   [X] Error processing JSON {filename}: {e}. Initiating rollback...")
                    try:
                        db["documents"].delete_many({"source": filename, "affiliate": affiliate, "ingest_id": ingest_id})
                    except Exception as clean_err:
                        logger.error(f"       -> Could not remove partial chunks for {filename}: {clean_err}")
                    fs.delete(file_obj._id)
                    logger.info(f"       -> Deleted {filename} from GridFS storage.")
                    if not isinstance(e, json_chunks.JsonIngestError):
                        raise e      # a real fault keeps the original error reporting; a file that simply is not usable JSON must not stop the other uploads

                continue

            if _is_text_filename(filename) or _is_markdown_filename(filename) or _is_docx_filename(filename) or _is_spreadsheet_filename(filename):
                # --- TEXT, MARKDOWN, WORD AND SPREADSHEET BRANCH: one shared path (see _index_text_upload), which leaves nothing behind if a file fails. ---
                kind = "docx" if _is_docx_filename(filename) else ("spreadsheet" if _is_spreadsheet_filename(filename) else ("markdown" if _is_markdown_filename(filename) else "text"))
                _index_text_upload(db, fs, embeddings, text_splitter, file_obj, filename, affiliate, kind)
                continue

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

                # 3. Knowledge graph (best effort, never fails the upload), then success (move to pages container)
                index_graph_for_source(db, affiliate, filename, [c.page_content for c in chunks])
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
                delete_graph_edges(db, affiliate, filename)

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