"""Google Drive API operations — file search/read, plus metadata lookup for validating a
user-configured target document. The full `drive` scope is already granted as part of this
session's Google OAuth expansion regardless of whether these read functions exist, so building
real search/read here (rather than just the minimal metadata check) doesn't cost anything extra —
skipping it would leave already-paid-for capability unused.
"""
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

logger = logging.getLogger("SASS Logger")

_EXPORT_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}

# Native Google types with no reasonable plain-text export (binary/visual formats).
_UNREADABLE_NATIVE_TYPES = {
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.drawing",
    "application/vnd.google-apps.form",
}


def _service(access_token: str):
    return build("drive", "v3", credentials=Credentials(token=access_token))


def get_file_metadata(access_token: str, file_id: str) -> dict:
    service = _service(access_token)
    return service.files().get(fileId=file_id, fields="id,name,mimeType").execute()


def search_files(access_token: str, query: str, max_results: int = 10) -> list[dict]:
    """`query` is a real Drive API query string, passed through directly — e.g.
    "name contains 'Report'", "fullText contains 'budget'",
    "mimeType='application/vnd.google-apps.document'". Same choice as Gmail's search_messages:
    expose the real query syntax rather than a simplified wrapper, since the menu description can
    teach the model real examples."""
    service = _service(access_token)
    results = service.files().list(
        q=query, pageSize=max_results, fields="files(id,name,mimeType,modifiedTime)",
    ).execute()
    return results.get("files", [])


def read_file(access_token: str, file_id: str) -> str:
    """Reads a file's content as text. Native Google Docs/Sheets are exported as plain
    text/CSV (they have no raw byte content of their own); other native Google types (Slides,
    Drawings, Forms) have no reasonable text export and return an ERROR string; anything else
    (regular files) is downloaded directly and decoded as UTF-8, same fallback convention as
    get_attachment_text in the Gmail service."""
    service = _service(access_token)
    metadata = service.files().get(fileId=file_id, fields="id,name,mimeType").execute()
    mime_type = metadata.get("mimeType", "")

    if mime_type in _UNREADABLE_NATIVE_TYPES:
        return f"ERROR: '{metadata.get('name')}' is a {mime_type.split('.')[-1]} file — not readable as text."

    if mime_type in _EXPORT_MIME_TYPES:
        content = service.files().export(fileId=file_id, mimeType=_EXPORT_MIME_TYPES[mime_type]).execute()
        return content.decode("utf-8") if isinstance(content, bytes) else str(content)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()

    try:
        return buffer.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        return f"ERROR: '{metadata.get('name')}' is not valid UTF-8 text (likely a binary file)."
