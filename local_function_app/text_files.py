"""Plain text, Markdown and Word files for the ingestion pipeline (function_app.py): turning the bytes into one clean text, or refusing the file in words.

Like json_chunks.py, this is deliberately free of `backend.*` imports so the pipeline stays an independent subprocess.

WHAT A FILE MUST SURVIVE
- ENCODING. Text files come from everywhere: UTF-8 (with or without a byte-order mark), UTF-16 (Notepad's "Unicode"), and Windows ANSI (cp1252). Each is read as what it is; nothing is allowed to
  fail with a decode error, and a file that is really binary (a renamed image, a corrupt log) is refused instead of being embedded as noise.
- SIZE. A text file over TEXT_INGEST_MAX_BYTES (5 MB), a Word file over DOCX_INGEST_MAX_BYTES (20 MB) or one that unzips to more than 100 MB (a zip bomb), and a file that would make more than
  TEXT_INGEST_MAX_CHUNKS (1,500) chunks is refused with the reason. A big log would otherwise mean thousands of embeddings for one upload.
- EMPTY. A file with nothing readable in it is refused, never sent on to be embedded as an empty list.
- WORD ORDER. A .docx is read in document order: each paragraph, and each table as rows of cells separated by " | ", at the place it sits in the document. A cell merged across columns is
  read once, not once per column. Headers, footers, footnotes and text boxes are not read.

Everything that is the FILE's fault raises TextIngestError, which the pipeline handles the way it handles a JSON file that is not JSON: the upload is removed and the other uploads carry on. The
messages are fixed words, never the file's content.
"""
import codecs
import io
import os
import zipfile
from typing import Any

MAX_BYTES = int(os.getenv("TEXT_INGEST_MAX_BYTES", str(5 * 1024 * 1024)))
MAX_DOCX_BYTES = int(os.getenv("DOCX_INGEST_MAX_BYTES", str(20 * 1024 * 1024)))
MAX_DOCX_UNZIPPED_BYTES = 100 * 1024 * 1024
MAX_CHUNKS = int(os.getenv("TEXT_INGEST_MAX_CHUNKS", "1500"))
BINARY_SHARE = 0.10             # more than this share of control or replacement characters means it is not text


class TextIngestError(Exception):
    """The file cannot be used (too big, empty, binary, not a Word document). Its message is for the person and holds none of the file."""


def decode_text(raw: bytes) -> str:
    """The text in `raw`: UTF-8 with or without a BOM, UTF-16 when it has a BOM, otherwise Windows-1252. Newlines are made \\n and NUL characters dropped. Never raises on bytes."""
    if raw.startswith(codecs.BOM_UTF8):
        text = raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = raw.decode("utf-16", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _looks_binary(text: str) -> bool:
    if not text:
        return False
    odd = sum(1 for ch in text if ch == "�" or (ord(ch) < 32 and ch not in "\t\n"))
    return odd / len(text) > BINARY_SHARE


def _document(Document: Any, text: str, filename: str, affiliate: str, ingest_id: str, doc_type: str) -> Any:
    return Document(page_content=text, metadata={"affiliate": affiliate, "source": filename, "doc_type": doc_type, "ingest_id": ingest_id})


def build_text_document(raw: bytes, filename: str, affiliate: str, Document: Any, ingest_id: str, doc_type: str = "text") -> Any:
    """One Document out of a .txt, .log, .md or .markdown file, or TextIngestError."""
    if len(raw) > MAX_BYTES:
        raise TextIngestError(f"The file is {len(raw) // (1024 * 1024)} MB, which is over the {MAX_BYTES // (1024 * 1024)} MB limit for text files.")
    text = decode_text(raw)
    if _looks_binary(text):
        raise TextIngestError("The file looks like binary data, not text.")
    if not text.strip():
        raise TextIngestError("The file has no readable text in it.")
    return _document(Document, text, filename, affiliate, ingest_id, doc_type)


def docx_text(raw: bytes) -> str:
    """The text of a Word document in document order, or TextIngestError."""
    if len(raw) > MAX_DOCX_BYTES:
        raise TextIngestError(f"The file is {len(raw) // (1024 * 1024)} MB, which is over the {MAX_DOCX_BYTES // (1024 * 1024)} MB limit for Word files.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNZIPPED_BYTES:
                raise TextIngestError("The Word file expands to far more than its size suggests, so it was not read.")
        from docx import Document as DocxDocument
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = DocxDocument(io.BytesIO(raw))
        parts = []
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                text = Paragraph(child, document).text.strip()
                if text:
                    parts.append(text)
            elif tag == "tbl":
                rows = []
                for row in Table(child, document).rows:
                    cells, seen = [], []
                    for cell in row.cells:
                        if any(cell._tc is earlier for earlier in seen):
                            continue                  # a cell merged across columns is the same cell each time round
                        seen.append(cell._tc)
                        cells.append(" ".join(cell.text.split()))
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append("\n".join(rows))
        return "\n\n".join(parts)
    except TextIngestError:
        raise
    except Exception:
        raise TextIngestError("The file could not be read as a Word document.") from None


def build_docx_document(raw: bytes, filename: str, affiliate: str, Document: Any, ingest_id: str) -> Any:
    """One Document out of a .docx file, or TextIngestError."""
    text = docx_text(raw)
    if not text.strip():
        raise TextIngestError("The file has no readable text in it.")
    return _document(Document, text, filename, affiliate, ingest_id, "docx")
