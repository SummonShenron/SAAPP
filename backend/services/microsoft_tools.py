"""The agent's Microsoft tools: the person's own Outlook mail, Outlook calendar and OneDrive (read-only here; sending mail and scheduling go through the approval
cards in agent_workflow's write registry, which call the helpers at the bottom).

Kept out of agent_workflow.py so that file does not grow by another provider's worth of branches: the dispatcher routes any tool named here to run_tool().
Each tool returns a string (what the model reads); a problem is an "ERROR: ..." string, never an exception.
"""
import asyncio
import logging

from backend.services import microsoft_graph_service as graph
from backend.services.microsoft_oauth import MicrosoftConnectionError, MicrosoftOAuth, MicrosoftTemporaryError
from backend.utils import microsoft_utils
from backend.utils.time_utils import local_today
from backend.utils.user_settings_utils import get_user_timezone

logger = logging.getLogger("SASS Logger")

# tool name -> (the scope it needs, what to call the product in a message)
TOOLS = {
    "search_outlook_mail": (microsoft_utils.SCOPE_MAIL_READ, "Outlook mail"),
    "read_outlook_message": (microsoft_utils.SCOPE_MAIL_READ, "Outlook mail"),
    "get_outlook_attachment": (microsoft_utils.SCOPE_MAIL_READ, "Outlook mail"),
    "list_outlook_calendar_events": (microsoft_utils.SCOPE_CALENDAR, "Outlook calendar"),
    "search_onedrive_files": (microsoft_utils.SCOPE_FILES_READ, "OneDrive"),
    "read_onedrive_file": (microsoft_utils.SCOPE_FILES_READ, "OneDrive"),
}

NOT_CONNECTED = "ERROR: No Microsoft account connected for this user. Connect it under Integrations first."


async def resolve_token(username: str, scope: str | None = None, product: str = "Microsoft") -> tuple[str | None, str | None]:
    """(token, None) or (None, "ERROR: ..."). Distinguishes no connection, a connection that lacks this permission (the person can untick them on the
    consent screen), and a refresh that failed for a reason that leaves the connection intact."""
    if username in microsoft_utils.MICROSOFT_LOCKED_USERS:
        return None, f"ERROR: {product} is not available for this account"
    try:
        token = await asyncio.to_thread(MicrosoftOAuth().get_valid_access_token, username)
    except MicrosoftTemporaryError as error:
        return None, f"ERROR: {error}"
    except MicrosoftConnectionError:
        return None, NOT_CONNECTED
    if scope and not await asyncio.to_thread(microsoft_utils.has_granted_scope, username, scope):
        return None, f"ERROR: {product} access was not granted. Reconnect Microsoft under Integrations and allow it."
    return token, None


async def run_tool(tool_action: str, username: str, args: dict) -> str:
    scope, product = TOOLS[tool_action]
    token, problem = await resolve_token(username, scope, product)
    if problem:
        return problem
    try:
        if tool_action == "search_outlook_mail":
            results = await asyncio.to_thread(graph.search_messages, token, args.get("query", ""), int(args.get("max_results") or 10))
            if not results:
                return "No matching emails found."
            return "\n".join(f"- [{r['id']}] {r['subject']} — {r['from']} ({r['date']}): {r['snippet']}" for r in results)
        if tool_action == "read_outlook_message":
            detail = await asyncio.to_thread(graph.get_message_detail, token, args.get("message_id"))
            attachments = (
                "; ".join(f"{a['filename']} (id: {a['attachment_id']}, {a['mime_type']})" for a in detail["attachments"])
                if detail["attachments"] else "none"
            )
            return f"Subject: {detail['subject']}\nFrom: {detail['from']}\nDate: {detail['date']}\nAttachments: {attachments}\n\nBody:\n{detail['body_text']}"
        if tool_action == "get_outlook_attachment":
            return await asyncio.to_thread(graph.get_attachment_text, token, args.get("message_id"), args.get("attachment_id"))
        if tool_action == "list_outlook_calendar_events":
            tz = get_user_timezone(username)
            date_arg = args.get("date") or local_today(tz)
            events = await asyncio.to_thread(graph.list_events_for_day, token, date_arg, tz)
            if not events:
                return f"No events found on the connected Outlook calendar for {date_arg}."
            return "\n".join(f"- {e['start']}: {e['summary']}" for e in events)
        if tool_action == "search_onedrive_files":
            files = await asyncio.to_thread(graph.search_files, token, args.get("query", ""), int(args.get("max_results") or 10))
            if not files:
                return "No matching files found."
            return "\n".join(f"- [{f['id']}] {f['name']} ({f['mimeType'] or 'file'}, modified {f.get('modifiedTime') or '?'})" for f in files)
        return await asyncio.to_thread(graph.read_file, token, args.get("file_id"))
    except graph.GraphError as error:
        return f"ERROR: {error}"
    except ValueError:
        return "ERROR: that request had an argument I couldn't use (check the date format YYYY-MM-DD)."


# ----- helpers for the approval-card writes in agent_workflow -----

_MICROSOFT_WORDS = ("outlook", "microsoft", "hotmail", "office 365", "o365", "m365", "onedrive")


def mentions_microsoft(message: str) -> bool:
    text = str(message or "").lower()
    return any(word in text for word in _MICROSOFT_WORDS)


def pick_provider(message: str, google_ok: bool, microsoft_ok: bool) -> str | None:
    """Which account a write goes to: "google", "microsoft", or None when neither is usable. With both usable, the person's own words decide (they said
    Outlook / Microsoft / hotmail), and Google is the default so nobody's existing behavior changes."""
    if microsoft_ok and not google_ok:
        return "microsoft"
    if google_ok and not microsoft_ok:
        return "google"
    if not google_ok and not microsoft_ok:
        return None
    return "microsoft" if mentions_microsoft(message) else "google"


def microsoft_usable(username: str, scope: str, google_ok: bool, message: str) -> bool:
    """Whether Microsoft can take this write. Only asked (a database read) when it could change the answer: Google is not usable, or the person named Microsoft."""
    if google_ok and not mentions_microsoft(message):
        return False
    return microsoft_utils.has_granted_scope(username, scope)


ACCOUNT_LINE = "- **Account:** Outlook (Microsoft)"
ACCOUNT_LINE_RE = r"-\s*\*\*Account:\*\*\s*Outlook"


def provider_from_card(content: str) -> str | None:
    """"microsoft" when an approval card names the Outlook account, so approving it later (a new turn, state rebuilt from the card text) still goes there."""
    import re
    return "microsoft" if re.search(ACCOUNT_LINE_RE, content or "") else None


def _token_or_error(username: str, scope: str, failure: str) -> tuple[str | None, str | None]:
    try:
        token = MicrosoftOAuth().get_valid_access_token(username)
    except MicrosoftConnectionError as error:
        return None, f"**{failure}:** {error}"
    if not microsoft_utils.has_granted_scope(username, scope):
        return None, f"**{failure}:** that permission was not granted. Reconnect Microsoft under Integrations and allow it."
    return token, None


def execute_create_event(username: str, details: dict) -> str:
    token, problem = _token_or_error(username, microsoft_utils.SCOPE_CALENDAR, "Failed to schedule event")
    if problem:
        return problem
    try:
        created = graph.create_event(token, details["summary"], details["start_iso"], details.get("duration_minutes", 30), details.get("timezone") or get_user_timezone(username))
    except graph.GraphError as error:
        logger.warning("[microsoft_tools] Outlook event create failed: %s", error)
        return f"**Failed to schedule event:** {error}"
    link = f" [View in Outlook]({created['html_link']})" if created.get("html_link") else ""
    return f"**Event scheduled:** '{created['summary']}' at {created['start']}.{link}"


def execute_update_event(username: str, details: dict) -> str:
    token, problem = _token_or_error(username, microsoft_utils.SCOPE_CALENDAR, "Failed to update event")
    if problem:
        return problem
    tz = details.get("timezone") or get_user_timezone(username)
    try:
        event = graph.find_event_by_summary_on_day(token, details["search_summary"], details["event_date_iso"], tz)
        if not event:
            return f"**Failed to update event:** couldn't find an event matching \"{details['search_summary']}\" on {details['event_date_iso']}."
        updates = {}
        if details.get("new_summary"):
            updates["summary"] = details["new_summary"]
        if details.get("start_iso"):
            updates["start_iso"] = details["start_iso"]
            updates["duration_minutes"] = details.get("duration_minutes", 30)
        updated = graph.update_event(token, event["id"], updates, tz)
    except graph.GraphError as error:
        logger.warning("[microsoft_tools] Outlook event update failed: %s", error)
        return f"**Failed to update event:** {error}"
    link = f" [View in Outlook]({updated['html_link']})" if updated.get("html_link") else ""
    return f"**Event updated:** '{updated['summary']}' now at {updated['start']}.{link}"


def execute_send_email(username: str, details: dict) -> str:
    token, problem = _token_or_error(username, microsoft_utils.SCOPE_MAIL_SEND, "Failed to send email")
    if problem:
        return problem
    try:
        graph.send_message(token, details["to"], details["subject"], details["body"])
    except graph.GraphError as error:
        logger.warning("[microsoft_tools] Outlook send failed: %s", error)
        return f"**Failed to send email:** {error}"
    return f"**Email sent** to {details['to']} from your Outlook account."

