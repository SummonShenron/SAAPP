import os
import base64
import logging
from typing import List, Dict
import pdfplumber
from docx import Document

# Import your database utility
from backend.utils.db_utils import get_db

from backend.components.constraints import ATTACHMENT_PROMPT
from backend.models.attachment import Attachment
from backend.models.models import llm

logger = logging.getLogger("SASS Logger")

def extract_text_from_attachment(attachment: Attachment) -> str:
    """Extract raw text from PDF or DOCX without indexing or chunking."""
    raw_bytes = base64.b64decode(attachment.content)
    
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


def process_user_attachment(att):
    raw_text = extract_text_from_attachment(att)

    if not raw_text or not raw_text.strip():
        return "Attachment contained no readable text."

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


def ingest_doc_to_session(username: str, session_id: str, attachment: Attachment) -> dict:
    """
    Extracts attachment text and stores it directly into MongoDB.
    """
    db = get_db()
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