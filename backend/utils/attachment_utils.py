import os
import base64
import json
from pypdf import PdfReader
from io import BytesIO
import logging
import pdfplumber
from typing import List, Dict
from docx import Document
from langchain_community.vectorstores import Chroma
from backend.components.constraints import ATTACHMENT_PROMPT
from backend.models.attachment import Attachment
from backend.models.models import llm
logger = logging.getLogger("SASS Logger")

SESSION_ROOT = "local-rag/sessions"

def extract_text_from_attachment(attachment: Attachment) -> str:
    """Extract raw text from PDF or DOCX without indexing or chunking."""
    raw_bytes = base64.b64decode(attachment.content)
    # Write temp file
    temp_dir = "./temp"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, attachment.filename)
    with open(temp_path, "wb") as f:
        f.write(raw_bytes)
    # PDF
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
        return text
    # DOCX
    if attachment.filename.lower().endswith(".docx"):
        try:
            doc = Document(temp_path)
            return "\n".join([p.text for p in doc.paragraphs])
        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            return ""
    # Fallback: treat as plain text
    try:
        return raw_bytes.decode("utf-8")
    except:
        return ""


def summarize_attachment_text(text: str) -> str:
    """Summarize the attachment so it can be injected into context."""
    if not text.strip():
        return ""
    prompt = ATTACHMENT_PROMPT
    try:
        summary = llm.invoke(prompt)
        return summary
    except Exception as e:
        logger.error(f"Attachment summarization failed: {e}")
        return ""

def process_user_attachment(att):
    raw_text = extract_text_from_attachment(att)

    if not raw_text or not raw_text.strip():
        return "Attachment contained no readable text."

    prompt = ATTACHMENT_PROMPT.format(text=raw_text)

    response = llm.invoke(prompt)

    # Normalize LLM output
    if hasattr(response, "content"):
        summary_text = response.content
    else:
        summary_text = str(response)

    return summary_text.strip()

def get_or_create_session_store(username: str, session_id: str) -> str:
    """
    Creates (or loads) a session-specific vector store directory.
    Returns the path to the session directory.
    """
    session_path = os.path.join(SESSION_ROOT, username, session_id)

    # Create directory structure if missing
    os.makedirs(session_path, exist_ok=True)

    # Metadata file for this session
    meta_path = os.path.join(session_path, "docs.json")

    # Initialize metadata file if missing
    if not os.path.exists(meta_path):
        with open(meta_path, "w") as f:
            json.dump({"documents": []}, f, indent=2)

    return session_path


def ingest_doc_to_session(username: str, session_id: str, attachment: Attachment) -> dict:
    """
    Saves the extracted text + metadata for a document into the session store.
    (Embedding will be added later.)
    """
    session_path = get_or_create_session_store(username, session_id)
    meta_path = os.path.join(session_path, "docs.json")

    # Extract raw text
    raw_text = extract_text_from_attachment(attachment)
    if not raw_text.strip():
        return {"status": "error", "message": "Attachment contained no readable text."}

    # Save raw text file inside session
    text_filename = attachment.filename + ".txt"
    text_path = os.path.join(session_path, text_filename)

    with open(text_path, "w", encoding="utf-8") as f:
        f.write(raw_text)

    # Load existing metadata
    with open(meta_path, "r") as f:
        meta = json.load(f)

    # Add new document entry
    doc_entry = {
        "filename": attachment.filename,
        "text_file": text_filename,
        "size_bytes": len(attachment.content),
        "session_id": session_id,
        "username": username
    }

    meta["documents"].append(doc_entry)

    # Save updated metadata
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return {
        "status": "ok",
        "message": "Document ingested into session store.",
        "session_path": session_path,
        "document": doc_entry
    }




import math


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def retrieve_from_session(username: str, session_id: str, query: str, top_k: int = 5):
    session_path = get_or_create_session_store(username, session_id)

    session_vector_store = Chroma(
        persist_directory=session_path,
        embedding_function=None
    )

    # Add a metadata filter so it ONLY searches documents in this specific session
    results = session_vector_store.similarity_search(
        query, 
        k=top_k,
        filter={"session_id": session_id} # This forces the speedup
    )

    formatted = []
    for doc in results:
        formatted.append({
            "text": doc.page_content,
            "metadata": doc.metadata
        })

    return formatted