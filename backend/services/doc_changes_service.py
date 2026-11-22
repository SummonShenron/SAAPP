"""Preparing and applying a Google Docs change: the part that talks to Google (the checking and building is backend/utils/doc_changes.py; the card that waits for Apply or Decline is backend/services/change_cards.py).

TWO STEPS, NEVER ONE, as for the calendar (backend/services/calendar_changes_service.py). `prepare` looks at the document as Google has it now, builds the exact requests and the card the person will see, and CHANGES NOTHING.
`apply` runs only those stored requests, after the person pressed Apply. A replace or an insert under a heading is made against the revision the card was drawn from, so Google refuses it if the document was edited in the
meantime; an append and a create do not depend on what the document looks like and carry no such guard.

Both are blocking (network calls): call them off the event loop. No document name, text or address is ever written to a log; failures are logged by kind only. A failure the person can act on is a DocChangeError whose message is
shown as it is; the raw Google error text is never passed on.
"""
import logging
import uuid
from typing import Any, Dict

from backend.services import google_docs_service as docs
from backend.services import google_drive_service as drive
from backend.services.google_calendar_oauth import GoogleCalendarConnectionError, GoogleCalendarOAuth, GoogleCalendarTemporaryError
from backend.utils import doc_changes as dc
from backend.utils.doc_changes import DocChangeError
from backend.utils.google_calendar_utils import CALENDAR_LOCKED_USERS, has_granted_scope
from backend.utils.user_settings_utils import get_user_target_doc_id

logger = logging.getLogger("SASS Logger")

DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
EDIT_LINK = "https://docs.google.com/document/d/{}/edit"


def _token(username: str) -> str:
    if username in CALENDAR_LOCKED_USERS:
        raise DocChangeError("Google Docs is not available for this account.")
    try:
        token = GoogleCalendarOAuth().get_valid_access_token(username)
    except GoogleCalendarTemporaryError as error:
        raise DocChangeError(str(error))
    except GoogleCalendarConnectionError:
        raise DocChangeError("No Google account is connected. Connect it under Integrations first.")
    if not has_granted_scope(username, DOCS_SCOPE):
        raise DocChangeError("Google Docs access was not granted to this connection. Reconnect Google under Integrations to enable it.")
    return token


def _status(error: Exception) -> int:
    return int(getattr(getattr(error, "resp", None), "status", 0) or 0)


def _explain(error: Exception, doing: str, guarded: bool = False) -> DocChangeError:
    """A sentence for the person from a Google failure, by its status and Google's reason code only (the raw text can carry anything)."""
    status = _status(error)
    reason = docs._error_details(error).get("reason") if status == 403 else None
    logger.warning("[doc change] Google refused or failed while %s (status %s%s, %s).", doing, status or "none", f", {reason}" if reason else "", type(error).__name__)
    if status in (404, 410):
        return DocChangeError("That document is no longer in your Drive. Look it up again to see what is there.")
    if status == 403:
        if reason in ("SERVICE_DISABLED", "accessNotConfigured"):
            return DocChangeError("The Google Docs API isn't enabled for SAAPP's Google Cloud project, so this can't work yet. That is a setup problem on SAAPP's side, not your document or your permissions.")
        if reason in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"):
            return DocChangeError("Google didn't grant Docs access to this connection. Reconnect Google under Integrations.")
        return DocChangeError("You don't have edit access to that document, or it isn't a Google Doc.")
    if status == 401:
        return DocChangeError("Google no longer accepts the connection. Reconnect Google under Integrations.")
    if status == 400 and guarded:
        return DocChangeError("That document was edited after I showed you the card, so I did not change it. Ask me again and I will show you the new version.")
    if status == 429 or status >= 500:
        return DocChangeError("Google Docs is busy right now. Nothing was changed; try again in a moment.")
    return DocChangeError("Google Docs did not accept that, so nothing was changed.")


def _info(token: str, doc_id: str) -> Dict[str, Any]:
    try:
        info = drive.get_file_info(token, doc_id)
    except Exception as error:
        raise _explain(error, "looking the document up")
    if info.get("mimeType") != dc.DOC_MIME:
        raise DocChangeError("That is not a Google Doc (it may be a spreadsheet, a slide deck or a file), so I can't edit it here.")
    if info.get("trashed"):
        raise DocChangeError("That document is in the trash.")
    return info


def prepare(username: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """The proposal for these arguments: {proposal_id, username, action, title, card, request, link, subject, apply}; `apply(proposal)` is what runs after Apply (backend/services/change_cards.py). Raises DocChangeError. Changes nothing."""
    change = dc.clean_args(args)
    token = _token(username)
    action = change["action"]
    if action == "create":
        built = dc.build_request(change, None)
        card = dc.card_data(change, change["title"], None)
        request = {"kind": "create", "doc_id": None, "title": change["title"], **built}
        link = None
    else:
        doc_id = change["doc_id"] or get_user_target_doc_id(username)
        if not doc_id:
            raise DocChangeError("Tell me which document. Give me part of its title and I will look it up, or set a default document under Integrations.")
        info = _info(token, doc_id)
        doc, matches = None, 0
        if action in ("replace", "insert_after_heading"):
            try:
                doc = docs.get_document(token, doc_id)
            except Exception as error:
                raise _explain(error, "reading the document")
            if action == "replace":
                matches = dc.count_matches(doc, change["find"])
                if matches == 0:
                    raise DocChangeError(f"I can't find that exact phrase in \"{info.get('name')}\". Read the document and give me the exact words, with the same capitals and spacing.")
                if matches > dc.MAX_MATCHES:
                    raise DocChangeError(f"That phrase appears in {matches} places, which is more than I change at once (the limit is {dc.MAX_MATCHES}). Give me a longer, more specific phrase.")
        built = dc.build_request(change, doc)
        card = dc.card_data(change, info.get("name") or "Untitled document", info.get("shared"), matches)
        request = {"kind": action, "doc_id": doc_id, "title": card["title"], "text_length": len(change.get("text") or ""), "heading": change.get("heading"), **built}
        link = info.get("webViewLink") or EDIT_LINK.format(doc_id)
    return {"proposal_id": uuid.uuid4().hex, "username": username, "action": action, "title": card["title"], "card": card, "request": request, "link": link, "subject": "document", "apply": apply}


def apply(proposal: Dict[str, Any]) -> str:
    """Runs the stored requests and returns one plain sentence saying what happened (raises DocChangeError with the reason when it could not). Only called after Apply."""
    token = _token(proposal["username"])
    request = proposal["request"]
    kind, guarded = request["kind"], request.get("required_revision") is not None
    try:
        if kind == "create":
            created = docs.create_document(token, request["title"])
            doc_id = created["documentId"]
            link = EDIT_LINK.format(doc_id)
            if request["requests"]:
                try:
                    docs.batch_update(token, doc_id, request["requests"])
                except Exception as error:
                    _explain(error, "putting the starting text in the new document")
                    raise DocChangeError(f"I created \"{request['title']}\" but could not put the text in it. Open it to check: {link}")
            return f"Created \"{request['title']}\". Link: {link}"
        reply = docs.batch_update(token, request["doc_id"], request["requests"], request.get("required_revision"))
        link = EDIT_LINK.format(request["doc_id"])
        if kind == "append":
            return f"Added the text ({request['text_length']} characters) to the end of \"{request['title']}\". Link: {link}"
        if kind == "insert_after_heading":
            return f"Added the text under the heading \"{request['heading']}\" in \"{request['title']}\". Link: {link}"
        changed = int((((reply or {}).get("replies") or [{}])[0].get("replaceAllText") or {}).get("occurrencesChanged") or 0)
        if changed == 0:
            return f"Nothing was replaced in \"{request['title']}\": the phrase was no longer there."
        return f"Replaced the phrase in {changed} place{'s' if changed != 1 else ''} in \"{request['title']}\". Link: {link}"
    except DocChangeError:
        raise
    except Exception as error:
        raise _explain(error, {"create": "creating the document", "append": "adding to the document", "replace": "replacing text", "insert_after_heading": "adding under the heading"}[kind], guarded)
