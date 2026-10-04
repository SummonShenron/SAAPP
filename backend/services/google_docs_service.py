"""Google Docs API operations — appending to an existing document. No existing code anywhere in
this repo or the sibling workflow_builder project uses the real Docs API: workflow_builder's own
"append to a Doc" is actually a Drive-export-as-plain-text + string-concat + full-file-overwrite
trick using only the Drive scope, which drops native Doc formatting and is fragile. This module
does a real documents.batchUpdate/insertText instead, using the dedicated `documents` OAuth scope.
"""
import json
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("SASS Logger")


def _error_details(error: HttpError) -> dict:
    """Pulls Google's own reason out of an HttpError body. A 403 alone is ambiguous — it covers a
    document the user can't edit, a missing OAuth scope, AND the Docs API being switched off for the
    whole Cloud project — and reporting all three as "you don't have edit access" sent a user with a
    document they own chasing the wrong problem."""
    try:
        body = json.loads(error.content.decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        return {}
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return {}
    reason, activation_url = None, None
    for detail in err.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reason = detail["reason"]
            activation_url = (detail.get("metadata") or {}).get("activationUrl")
            break
    if reason is None:
        errors = err.get("errors") or []
        reason = errors[0].get("reason") if errors and isinstance(errors[0], dict) else None
    return {"reason": reason, "message": err.get("message"), "activation_url": activation_url}


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
            details = _error_details(error)
            reason = details.get("reason")
            if reason in ("SERVICE_DISABLED", "accessNotConfigured"):
                logger.error(
                    "[google_docs_service] The Google Docs API is not enabled for this Cloud project; "
                    "enable it at %s", details.get("activation_url") or "the Google Cloud console (APIs & Services)",
                )
                return (
                    "ERROR: the Google Docs API isn't enabled for SAAPP's Google Cloud project, so "
                    "appending can't work yet. This is a setup problem on SAAPP's side — not your "
                    "document or your permissions."
                )
            if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
                return "ERROR: Google didn't grant Docs access to this connection. Reconnect Google under Integrations."
            logger.warning("[google_docs_service] 403 appending to %s: %s", document_id, details)
            return "ERROR: you don't have edit access to that document, or it isn't a Google Doc."
        logger.exception("[google_docs_service] append_text failed for document %s", document_id)
        return f"ERROR: failed to update the document (HTTP {status})."


def get_document(access_token: str, document_id: str) -> dict:
    """Thin wrapper, used only to validate a user-configured document ID at settings-save time —
    the append path above never needs to read the document first."""
    service = _service(access_token)
    return service.documents().get(documentId=document_id).execute()
