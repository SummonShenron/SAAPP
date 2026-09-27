"""Google Docs API operations — appending to an existing document. No existing code anywhere in
this repo or the sibling workflow_builder project uses the real Docs API: workflow_builder's own
"append to a Doc" is actually a Drive-export-as-plain-text + string-concat + full-file-overwrite
trick using only the Drive scope, which drops native Doc formatting and is fragile. This module
does a real documents.batchUpdate/insertText instead, using the dedicated `documents` OAuth scope.
"""
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("SASS Logger")


def _service(access_token: str):
    return build("docs", "v1", credentials=Credentials(token=access_token))


def append_text(access_token: str, document_id: str, text: str) -> str:
    """Appends `text` to the true end of the document body and returns a plain success/failure
    string (this repo's established convention for a final execute-step result).

    Uses insertText with endOfSegmentLocation (an empty location object, which Google's API
    resolves to the end of the body segment) rather than documents.get() + manual end-index
    arithmetic. This is Google's own documented "append" pattern: it's one API call instead of
    two, and it's robust to what the doc's last element happens to be (a table or list at the end
    would make manual end-index math fragile; endOfSegmentLocation isn't affected by that)."""
    service = _service(access_token)
    formatted_text = f"\n{text}\n"
    try:
        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": formatted_text}}]},
        ).execute()
        return "Appended successfully."
    except HttpError as error:
        status = getattr(error.resp, "status", None)
        if status == 404:
            return "ERROR: that document no longer exists or was deleted."
        if status == 403:
            return "ERROR: you don't have edit access to that document, or it isn't a Google Doc."
        logger.exception("[google_docs_service] append_text failed for document %s", document_id)
        return f"ERROR: failed to update the document (HTTP {status})."


def get_document(access_token: str, document_id: str) -> dict:
    """Thin wrapper, used only to validate a user-configured document ID at settings-save time —
    the append path above never needs to read the document first."""
    service = _service(access_token)
    return service.documents().get(documentId=document_id).execute()
