"""Microsoft Graph operations for one person's own Outlook mail, Outlook calendar and OneDrive.

The counterpart of backend/services/google_gmail_service.py, google_calendar_service.py and google_drive_service.py, and it returns the same shapes
on purpose so the tool dispatcher and the approval cards treat both providers alike. Every function takes an already-resolved access token (see
backend/services/microsoft_oauth.py) and never touches Mongo or a username.

Plain HTTP with `requests`; Graph is a REST API and a client library would add a dependency for no gain.
"""
import base64
import datetime
import logging
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("SASS Logger")

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 20
# A file bigger than this is not read into a prompt; the answer says so instead of returning a wall of text.
MAX_READ_BYTES = 5_000_000
MAX_TEXT_CHARS = 60_000
_MAIL_SELECT = "id,subject,from,receivedDateTime,bodyPreview"


class GraphError(Exception):
    """A Graph call failed. The message is written for the person (or the model), not a stack trace."""


def _raise_for(response: requests.Response) -> None:
    if response.status_code < 400:
        return
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    code, message = str(error.get("code") or response.status_code), str(error.get("message") or "")
    if response.status_code in (401, 403):
        raise GraphError("Microsoft refused access. Reconnect Microsoft under Integrations to grant it.")
    if response.status_code == 404:
        raise GraphError("That item isn't there (it may have been moved or deleted).")
    if response.status_code == 429:
        raise GraphError("Microsoft is rate limiting requests right now. Try again in a moment.")
    raise GraphError(f"Microsoft Graph error {code}: {message[:200]}")


def _request(method: str, token: str, path: str, *, params: dict | None = None, json_body: dict | None = None, headers: dict | None = None) -> requests.Response:
    merged = {"Authorization": f"Bearer {token}", **(headers or {})}
    try:
        response = requests.request(method, f"{GRAPH}{path}", params=params, json=json_body, headers=merged, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise GraphError("Microsoft couldn't be reached. Try again in a moment.") from error
    _raise_for(response)
    return response


def _get_json(token: str, path: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
    return _request("GET", token, path, params=params, headers=headers).json()


def _address(entry: dict | None) -> str:
    address = ((entry or {}).get("emailAddress") or {})
    name, email = address.get("name") or "", address.get("address") or ""
    return f"{name} <{email}>" if name and email and name != email else (email or name)


# ----- Outlook mail -----

def search_messages(token: str, query: str, max_results: int = 10) -> list[dict]:
    """[{id, subject, from, date, snippet}] — the Gmail shape. `query` is Microsoft's search syntax (plain words, or e.g. from:sam subject:report),
    passed through quoted; an empty query lists the newest mail."""
    top = max(1, min(int(max_results or 10), 25))
    params = {"$top": top, "$select": _MAIL_SELECT}
    text = str(query or "").replace('"', " ").strip()
    if text:
        params["$search"] = f'"{text}"'
    else:
        params["$orderby"] = "receivedDateTime desc"
    data = _get_json(token, "/me/messages", params=params, headers={"ConsistencyLevel": "eventual"})
    return [
        {
            "id": m.get("id"),
            "subject": m.get("subject") or "(no subject)",
            "from": _address(m.get("from")),
            "date": m.get("receivedDateTime", ""),
            "snippet": " ".join(str(m.get("bodyPreview") or "").split()),
        }
        for m in data.get("value", [])
    ]


def get_message_detail(token: str, message_id: str) -> dict:
    """{subject, from, date, body_text, body_is_html, attachments}; attachments is [{filename, mime_type, attachment_id, size}]."""
    msg = _get_json(
        token, f"/me/messages/{quote(str(message_id), safe='')}",
        params={"$select": "subject,from,receivedDateTime,body,hasAttachments"},
        headers={"Prefer": 'outlook.body-content-type="text"'},
    )
    attachments = []
    if msg.get("hasAttachments"):
        listing = _get_json(token, f"/me/messages/{quote(str(message_id), safe='')}/attachments", params={"$select": "id,name,contentType,size,isInline"})
        for a in listing.get("value", []):
            if a.get("isInline"):
                continue
            attachments.append({"filename": a.get("name") or "attachment", "mime_type": a.get("contentType") or "", "attachment_id": a.get("id"), "size": a.get("size", 0)})
    body = msg.get("body") or {}
    return {
        "subject": msg.get("subject") or "(no subject)",
        "from": _address(msg.get("from")),
        "date": msg.get("receivedDateTime", ""),
        "body_text": body.get("content") or "",
        "body_is_html": str(body.get("contentType") or "").lower() == "html",
        "attachments": attachments,
    }


def _text_from_bytes(filename: str, raw: bytes) -> str:
    """Readable text from a file's bytes: PDF, Word, spreadsheets and plain text via the same extractor chat attachments use. Pictures are not
    sent to a vision model from here."""
    from backend.utils import attachment_utils  # imported here: it pulls in the chat model, which a Graph unit test should not need
    # The extractor writes a temp file named after the file, and an email attachment's name is whatever the sender chose.
    filename = re.sub(r"[^\w.\- ]", "_", str(filename).replace("\\", "/").rsplit("/", 1)[-1]).strip(". ") or "file"
    if attachment_utils.is_image_attachment(filename):
        return f"ERROR: '{filename}' is an image; it has no text to read."
    if len(raw) > MAX_READ_BYTES:
        return f"ERROR: '{filename}' is too large to read here ({len(raw) // 1_000_000} MB)."
    from backend.models.attachment import Attachment
    text = attachment_utils.extract_text_from_attachment(Attachment(filename=filename, content=base64.b64encode(raw).decode()))
    text = (text or "").strip()
    if not text:
        return f"ERROR: '{filename}' has no text I can read (it may be a scan, a picture or a binary file)."
    return text[:MAX_TEXT_CHARS] + ("\n[...truncated]" if len(text) > MAX_TEXT_CHARS else "")


def get_attachment_text(token: str, message_id: str, attachment_id: str) -> str:
    att = _get_json(token, f"/me/messages/{quote(str(message_id), safe='')}/attachments/{quote(str(attachment_id), safe='')}")
    if att.get("@odata.type") != "#microsoft.graph.fileAttachment" or not att.get("contentBytes"):
        return "ERROR: that attachment isn't a file I can read (it may be an item or a link)."
    return _text_from_bytes(att.get("name") or "attachment", base64.b64decode(att["contentBytes"]))


def send_message(token: str, to: str, subject: str, body: str) -> dict:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": addr.strip()}} for addr in str(to).split(",") if addr.strip()],
        },
        "saveToSentItems": True,
    }
    _request("POST", token, "/me/sendMail", json_body=payload)
    return {"status": "sent"}


# ----- Outlook calendar -----

def _day_bounds_utc(date_iso: str, tz: str) -> tuple[str, str]:
    zone = ZoneInfo(tz)
    start = datetime.datetime.fromisoformat(date_iso).replace(tzinfo=zone)
    end = start + datetime.timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(datetime.timezone.utc).strftime(fmt), end.astimezone(datetime.timezone.utc).strftime(fmt)


def _local_iso(graph_time: dict | None, tz: str) -> str | None:
    """Graph returns a bare local time in the zone asked for ("2026-10-19T14:00:00.0000000"); attach that zone so the string is a real timestamp."""
    value = (graph_time or {}).get("dateTime")
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value[:26].rstrip("Z"))
    except ValueError:
        return value
    return parsed.replace(tzinfo=ZoneInfo(tz)).isoformat()


def _event_row(event: dict, tz: str) -> dict:
    return {
        "id": event.get("id"),
        "summary": event.get("subject") or "Untitled event",
        "start": _local_iso(event.get("start"), tz),
        "end": _local_iso(event.get("end"), tz),
        "recurring_event_id": event.get("seriesMasterId"),
        "html_link": event.get("webLink"),
    }


def _calendar_view(token: str, start_utc: str, end_utc: str, tz: str, top: int = 100) -> list[dict]:
    data = _get_json(
        token, "/me/calendarView",
        params={"startDateTime": start_utc, "endDateTime": end_utc, "$orderby": "start/dateTime", "$top": top,
                "$select": "id,subject,start,end,seriesMasterId,webLink,isCancelled"},
        headers={"Prefer": f'outlook.timezone="{tz}"'},
    )
    return [e for e in data.get("value", []) if not e.get("isCancelled")]


def list_events_for_day(token: str, date_iso: str, tz: str) -> list[dict]:
    """[{id, summary, start, end, recurring_event_id}] for that day in the person's timezone, same shape as the Google version."""
    start_utc, end_utc = _day_bounds_utc(date_iso, tz)
    return [_event_row(e, tz) for e in _calendar_view(token, start_utc, end_utc, tz)]


def _event_body(summary: str | None, start_iso: str | None, duration_minutes: int, tz: str) -> dict:
    body: dict = {}
    if summary is not None:
        body["subject"] = summary
    if start_iso:
        start = datetime.datetime.fromisoformat(start_iso).replace(tzinfo=None)
        end = start + datetime.timedelta(minutes=int(duration_minutes))
        body["start"] = {"dateTime": start.isoformat(), "timeZone": tz}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": tz}
    return body


def create_event(token: str, summary: str, start_iso: str, duration_minutes: int, tz: str) -> dict:
    """Creates an event and returns {id, summary, html_link, start, end}."""
    created = _request(
        "POST", token, "/me/events", json_body=_event_body(summary, start_iso, duration_minutes, tz),
        headers={"Prefer": f'outlook.timezone="{tz}"'},
    ).json()
    row = _event_row(created, tz)
    return {"id": row["id"], "summary": row["summary"], "html_link": row["html_link"], "start": row["start"], "end": row["end"]}


def update_event(token: str, event_id: str, updates: dict, tz: str) -> dict:
    """Changes only the fields present in `updates` (summary, start_iso + duration_minutes)."""
    body = _event_body(updates.get("summary"), updates.get("start_iso"), updates.get("duration_minutes", 30), tz)
    updated = _request(
        "PATCH", token, f"/me/events/{quote(str(event_id), safe='')}", json_body=body,
        headers={"Prefer": f'outlook.timezone="{tz}"'},
    ).json()
    row = _event_row(updated, tz)
    return {"id": row["id"], "summary": row["summary"], "html_link": row["html_link"], "start": row["start"], "end": row["end"]}


def find_event_by_summary_on_day(token: str, search_summary: str, event_date_iso: str, tz: str) -> dict | None:
    """The first event that day whose title contains the words, or None. Returns the Graph event (it has `id`)."""
    start_utc, end_utc = _day_bounds_utc(event_date_iso, tz)
    needle = str(search_summary or "").strip().lower()
    for event in _calendar_view(token, start_utc, end_utc, tz):
        if needle and needle in str(event.get("subject") or "").lower():
            return event
    return None


# ----- OneDrive -----

def search_files(token: str, query: str, max_results: int = 10) -> list[dict]:
    """[{id, name, mimeType, modifiedTime}] — the Drive shape. `query` is plain search words (file names and contents)."""
    top = max(1, min(int(max_results or 10), 25))
    text = str(query or "").replace("'", "''").strip()
    data = _get_json(
        token, f"/me/drive/root/search(q='{quote(text, safe='')}')",
        params={"$top": top, "$select": "id,name,file,folder,lastModifiedDateTime,size"},
    )
    return [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "mimeType": (f.get("file") or {}).get("mimeType") or ("folder" if f.get("folder") is not None else ""),
            "modifiedTime": f.get("lastModifiedDateTime"),
        }
        for f in data.get("value", [])
    ]


def read_file(token: str, file_id: str) -> str:
    """A OneDrive file's content as text, or an `ERROR: ...` string the caller can show as it is."""
    meta = _get_json(token, f"/me/drive/items/{quote(str(file_id), safe='')}", params={"$select": "id,name,size,file,folder"})
    name = meta.get("name") or "file"
    if meta.get("folder") is not None:
        return f"ERROR: '{name}' is a folder, not a file."
    if int(meta.get("size") or 0) > MAX_READ_BYTES:
        return f"ERROR: '{name}' is too large to read here ({int(meta['size']) // 1_000_000} MB)."
    # /content answers with a redirect to a short-lived download URL; requests follows it.
    raw = _request("GET", token, f"/me/drive/items/{quote(str(file_id), safe='')}/content").content
    return _text_from_bytes(name, raw)
