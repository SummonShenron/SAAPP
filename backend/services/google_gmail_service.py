"""Gmail API operations (search/read/send), separate from OAuth/connection management
(backend/services/google_calendar_oauth.py). Every function here takes an already-resolved
per-user access token and never touches Mongo or a username directly.

No existing code anywhere in this repo or the sibling workflow_builder project implements Gmail
search/read — this is genuinely new. workflow_builder does have a working Gmail send call
(backend/app/services/node_runners.py's run_gmail_send in that repo) which send_message below
mirrors for request shape, adapted to use googleapiclient.discovery.build for consistency with
google_calendar_service.py's own convention rather than raw HTTP.
"""
import base64
import json
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger("SASS Logger")

# Not a full RFC 2045 MIME parser — a deliberate scope limit. Handles the realistic nesting shapes
# Gmail actually produces (multipart/mixed wrapping multipart/alternative and/or multipart/related,
# plus attachment leaves at any depth), which covers ordinary sent/received mail. An unusual
# structure (e.g. an attachment nested inside a forwarded message/rfc822 part) may not be found.


def _service(access_token: str):
    return build("gmail", "v1", credentials=Credentials(token=access_token))


def _iter_parts(payload: dict):
    """Depth-first walk over a Gmail message payload's MIME tree, yielding every leaf part
    (a part with no further nested `parts`) — this is where both body text and attachments live."""
    parts = payload.get("parts")
    if not parts:
        yield payload
        return
    for part in parts:
        yield from _iter_parts(part)


def _decode_body_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def search_messages(access_token: str, query: str, max_results: int = 10) -> list[dict]:
    """Returns [{id, subject, from, date, snippet}, ...]. messages().list() alone only returns
    {id, threadId} — no subject/snippet — so this does one metadata fetch per candidate. Keep
    max_results modest (the tool_agent_node menu description defaults it to 10) since this is a
    real N+1 fan-out, not a single call."""
    service = _service(access_token)
    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    message_ids = [m["id"] for m in results.get("messages", [])]

    items = []
    for message_id in message_ids:
        msg = service.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        items.append({
            "id": message_id,
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
        })
    return items


def get_message_detail(access_token: str, message_id: str) -> dict:
    """Returns {subject, from, date, body_text, body_is_html, attachments}. attachments is
    [{filename, mime_type, attachment_id, size}] — the model should inspect this and call
    get_attachment_text next for whichever one looks relevant, rather than assuming content is
    inline in body_text."""
    service = _service(access_token)
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg.get("payload", {})
    headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

    body_text = None
    body_is_html = False
    html_fallback = None
    attachments = []

    for part in _iter_parts(payload):
        filename = part.get("filename")
        body = part.get("body", {})
        mime_type = part.get("mimeType", "")

        if filename and body.get("attachmentId"):
            attachments.append({
                "filename": filename,
                "mime_type": mime_type,
                "attachment_id": body["attachmentId"],
                "size": body.get("size", 0),
            })
            continue

        if body_text is None and mime_type == "text/plain" and body.get("data"):
            body_text = _decode_body_data(body["data"])
        elif html_fallback is None and mime_type == "text/html" and body.get("data"):
            html_fallback = _decode_body_data(body["data"])

    if body_text is None and html_fallback is not None:
        body_text = html_fallback
        body_is_html = True

    return {
        "subject": headers.get("Subject", "(no subject)"),
        "from": headers.get("From", ""),
        "date": headers.get("Date", ""),
        "body_text": body_text or "",
        "body_is_html": body_is_html,
        "attachments": attachments,
    }


def get_attachment_text(access_token: str, message_id: str, attachment_id: str) -> str:
    """Decodes an attachment's content as text, pretty-printing it if it's valid JSON. Gmail
    always returns attachment bytes as base64url regardless of the original message's
    Content-Transfer-Encoding (base64/quoted-printable/7bit) — it normalizes this server-side, so
    no manual transfer-encoding handling is needed here."""
    service = _service(access_token)
    attachment = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id,
    ).execute()

    padded = attachment["data"] + "=" * (-len(attachment["data"]) % 4)
    raw_bytes = base64.urlsafe_b64decode(padded)

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return "ERROR: attachment is not valid UTF-8 text"

    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except (json.JSONDecodeError, ValueError):
        return text


def send_message(access_token: str, to: str, subject: str, body: str) -> dict:
    service = _service(access_token)
    raw_message = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n{body}"
    encoded = base64.urlsafe_b64encode(raw_message.encode()).decode().rstrip("=")
    return service.users().messages().send(userId="me", body={"raw": encoded}).execute()
