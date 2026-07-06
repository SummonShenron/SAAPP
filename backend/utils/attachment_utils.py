import os
import base64
from pypdf import PdfReader
from io import BytesIO
import logging
import pdfplumber
from docx import Document
from backend.components.constraints import ATTACHMENT_PROMPT
from backend.models.attachment import Attachment
from backend.models.models import llm
logger = logging.getLogger("SASS Logger")

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

