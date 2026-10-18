import os
import base64
import logging
from typing import List, Dict, Optional
import pdfplumber
from docx import Document
from gridfs import GridFS
from langchain_core.messages import HumanMessage

# Import your database utility
from backend.utils.db_utils import get_db
from backend.utils import spreadsheet_utils

from backend.components.constraints import ATTACHMENT_PROMPT, IMAGE_DESCRIPTION_PROMPT
from backend.models.attachment import Attachment
from backend.models.models import llm

logger = logging.getLogger("SASS Logger")

# A document up to this many characters (a page or two) is given to the model as it is rather than summarized.
SHORT_DOCUMENT_CHARS = 6000
IMAGE_ATTACHMENT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
_IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def is_image_attachment(filename: str) -> bool:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return ext in IMAGE_ATTACHMENT_EXTENSIONS


def guess_image_mime_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return _IMAGE_MIME_TYPES.get(ext, "application/octet-stream")


def store_image_in_gridfs(db, username: str, session_id: str, attachment: Attachment) -> Optional[str]:
    """Persists an image attachment's raw bytes in GridFS (this app's direct equivalent of Azure
    Blob Storage) so it can be served back and rendered later — including from another device,
    where only the durable server-side copy is available — instead of keeping only its text
    description. Returns the GridFS file id as a string, or None if storage isn't available."""
    if db is None:
        return None
    try:
        raw_bytes = base64.b64decode(attachment.content)
        file_id = GridFS(db).put(
            raw_bytes,
            filename=attachment.filename,
            metadata={
                "username": username,
                "session_id": session_id,
                "content_type": guess_image_mime_type(attachment.filename),
                "kind": "chat_attachment",
            },
        )
        return str(file_id)
    except Exception:
        logger.exception(f"Failed to store image attachment {attachment.filename} in GridFS")
        return None


def attachment_image_visible_to(file_doc: Optional[Dict], username: Optional[str]) -> bool:
    """Whether this person may be served this stored picture. A photo someone attached in a chat (and a card made from it) belongs to them alone; a library
    image (no chat-attachment kind) is shared as before."""
    meta = (file_doc or {}).get("metadata") or {}
    if meta.get("kind") != "chat_attachment":
        return True
    return bool(username) and meta.get("username") == username


def describe_image_attachment(attachment: Attachment) -> str:
    """Derives a text description of an image attachment via the vision-capable chat LLM, so
    downstream handling (chat summarization, session storage, future KB embedding) can treat
    it exactly like extracted document text instead of needing image-aware code of its own."""
    mime_type = guess_image_mime_type(attachment.filename)

    message = HumanMessage(content=[
        {"type": "text", "text": IMAGE_DESCRIPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{attachment.content}"}},
    ])

    try:
        response = llm.invoke([message])
    except Exception as e:
        logger.error(f"Image description failed for {attachment.filename}: {e}")
        return ""

    description = response.content if hasattr(response, "content") else str(response)
    if isinstance(description, list):
        description = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in description])
    elif not isinstance(description, str):
        description = str(description)
    return description.strip()


def extract_text_from_attachment(attachment: Attachment) -> str:
    """Extract raw text from PDF or DOCX, or a vision-derived description for images, without
    indexing or chunking."""
    if is_image_attachment(attachment.filename):
        return describe_image_attachment(attachment)

    raw_bytes = base64.b64decode(attachment.content)

    # A format this app cannot read says so, so the person is told instead of getting a blank answer. A spreadsheet or CSV becomes a fixed picture of
    # the table (see spreadsheet_utils); a whole sheet is neither summarized nor pasted whole.
    note = spreadsheet_utils.unreadable_note(attachment.filename)
    if note:
        return note
    if spreadsheet_utils.is_table_file(attachment.filename):
        return spreadsheet_utils.table_text(attachment.filename, raw_bytes)

    # Create temp directory for processing
    temp_dir = "./temp"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, attachment.filename)
    
    with open(temp_path, "wb") as f:
        f.write(raw_bytes)
        
    # Process PDF
    if attachment.filename.lower().endswith(".pdf"):
        text = ""
        try:
            with pdfplumber.open(temp_path) as pdf:
                for page in pdf.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)  # Clean up temp file
        return text
        
    # Process DOCX
    if attachment.filename.lower().endswith(".docx"):
        try:
            doc = Document(temp_path)
            return "\n".join([p.text for p in doc.paragraphs])
        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            return ""
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)  # Clean up temp file
                
    # Fallback: treat as plain text
    try:
        text = raw_bytes.decode("utf-8")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return text
    except:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return ""


def process_user_attachment(att, raw_text: Optional[str] = None):
    """`raw_text` is the already-extracted text when the caller has it: for an image that is a vision-model call, never worth paying twice."""
    if raw_text is None:
        raw_text = extract_text_from_attachment(att)

    if not raw_text or not raw_text.strip():
        return "Attachment contained no readable text."

    if is_image_attachment(att.filename):
        # extract_text_from_attachment already ran the image through the vision model —
        # that description IS the summary, no need to re-run it through ATTACHMENT_PROMPT
        # (which is phrased for extracting structure out of document text).
        return raw_text.strip()

    if len(raw_text) <= SHORT_DOCUMENT_CHARS:
        # A letter, a bill or a one-page form fits in the prompt as it is. A summary of it could drop the very date or amount the person is asking
        # about, and skipping the model call is cheaper too.
        return raw_text.strip()

    prompt = ATTACHMENT_PROMPT.format(text=raw_text)
    response = llm.invoke(prompt)

    if hasattr(response, "content"):
        summary_text = response.content
    else:
        summary_text = str(response)

    if isinstance(summary_text, list):
        summary_text = "".join([c.get("text", "") if isinstance(c, dict) else str(c) for c in summary_text])
    elif not isinstance(summary_text, str):
        summary_text = str(summary_text)

    return summary_text.strip()


def ingest_doc_to_session(username: str, session_id: str, attachment: Attachment, raw_text: Optional[str] = None) -> dict:
    """
    Extracts attachment text and stores it directly into MongoDB.
    """
    db = get_db()
    if raw_text is None:
        raw_text = extract_text_from_attachment(attachment)

    if not raw_text.strip():
        return {"status": "error", "message": "Attachment contained no readable text."}

    doc_entry = {
        "username": username,
        "session_id": session_id,
        "filename": attachment.filename,
        "text": raw_text,
        "size_bytes": len(attachment.content)
    }

    # If DB usage is active, write to the 'session_documents' collection
    if db is not None:
        try:
            collection = db["session_documents"]
            # Upsert document based on unique session, user, and filename
            collection.update_one(
                {
                    "username": username, 
                    "session_id": session_id, 
                    "filename": attachment.filename
                },
                {"$set": doc_entry},
                upsert=True
            )
            logger.info(f"Successfully saved {attachment.filename} to MongoDB.")
        except Exception as e:
            logger.error(f"Failed to write attachment to MongoDB: {e}")
            return {"status": "error", "message": f"Database write failed: {e}"}
    else:
        logger.warning("MongoDB is disabled (USE_DB != true). Document processed but not saved.")

    return {
        "status": "ok",
        "message": "Document ingested into MongoDB session store.",
        "document": {k: v for k, v in doc_entry.items() if k != "text"} # Exclude big text from returning in status dict
    }


def process_chat_attachments(username: str, session_id: str, attachments) -> tuple:
    """Everything a chat turn does with its attachments: read each one once (an image is a vision-model call), keep its text for the
    session, summarize it, and for an image store the bytes. Returns (summaries, image refs). Blocking by nature (model calls, the
    database), so the endpoint runs it in a worker thread, never on the event loop."""
    summaries, refs = [], []
    db = get_db()
    for att in attachments:
        raw_text = extract_text_from_attachment(att)
        ingest_doc_to_session(username, session_id, att, raw_text=raw_text)
        summary = process_user_attachment(att, raw_text=raw_text)
        if summary:
            summaries.append(summary)
        if is_image_attachment(att.filename):
            gridfs_id = store_image_in_gridfs(db, username, session_id, att)
            if gridfs_id:
                refs.append({
                    "filename": att.filename,
                    "gridfs_id": gridfs_id,
                    "content_type": guess_image_mime_type(att.filename),
                })
    return summaries, refs


def delete_session_attachments(username: str, session_id: str) -> Dict[str, int]:
    """Removes what a conversation's attachments left behind: the text read out of each file (session_documents) and each stored photo (GridFS).
    Called when a conversation is deleted or cleared, so a photographed letter or bill does not outlive the conversation the person deleted. Only this
    person's chat attachments for this conversation: knowledge-base files are never touched. Never raises (a cleanup failure must not block the
    deletion itself); the counts say what went."""
    removed = {"documents": 0, "files": 0}
    db = get_db()
    if db is None:
        return removed
    try:
        removed["documents"] = db["session_documents"].delete_many({"username": username, "session_id": session_id}).deleted_count
        files = GridFS(db)
        stored = db["fs.files"].find(
            {"metadata.username": username, "metadata.session_id": session_id, "metadata.kind": "chat_attachment"}, {"_id": 1}
        )
        for row in list(stored):
            files.delete(row["_id"])
            removed["files"] += 1
    except Exception:
        logger.exception("Failed to delete the attachments of a deleted conversation")
    return removed


def retrieve_from_session(username: str, session_id: str, query: str, top_k: int = 5) -> List[Dict]:
    """
    Queries MongoDB for attachments matching this specific session.
    """
    db = get_db()
    if db is None:
        logger.warning("Attempted session retrieval but MongoDB is disabled.")
        return []

    try:
        collection = db["session_documents"]
        
        # Pull text contexts matching user session filter
        cursor = collection.find(
            {"username": username, "session_id": session_id},
            {"_id": 0, "filename": 1, "text": 1}
        ).limit(top_k)

        formatted = []
        for doc in cursor:
            # Structuring return format to cleanly map back to standard LangChain layout
            formatted.append({
                "text": doc.get("text", ""),
                "metadata": {
                    "source": "mongodb_session",
                    "filename": doc.get("filename", "unknown")
                }
            })
            
        return formatted
    except Exception as e:
        logger.error(f"MongoDB session context fetch failed: {e}")
        return []