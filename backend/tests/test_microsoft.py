import asyncio
import base64
import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import microsoft_graph_service as graph
from backend.services import microsoft_oauth as oauth_mod
from backend.services import microsoft_tools as mt
from backend.services.microsoft_oauth import MicrosoftConnectionError, MicrosoftOAuth, MicrosoftTemporaryError
from backend.utils import microsoft_utils as mu

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MICROSOFT_REDIRECT_URI", "http://localhost:8000/api/microsoft/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", KEY)


def run(coro):
    return asyncio.run(coro)


class Resp:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content = status, body if body is not None else {}, content

    def json(self):
        return self._body


# ----- storage -----

class FakeConnections:
    def __init__(self, doc=None):
        self.doc = doc

    def find_one(self, query):
        return self.doc


@pytest.mark.parametrize("granted,asked,expected", [
    (["Mail.Read"], "Mail.Read", True),
    (["https://graph.microsoft.com/Mail.Read"], "Mail.Read", True),
    (["mail.read"], "Mail.Read", True),
    (["Mail.Read"], "Mail.Send", False),
    ([], "Mail.Read", False),
])
def test_has_granted_scope_accepts_either_spelling(monkeypatch, granted, asked, expected):
    monkeypatch.setattr(mu, "_connections_collection", lambda: FakeConnections({"scopes": granted}))
    assert mu.has_granted_scope("jack", asked) is expected


def test_no_connection_means_no_scope_and_not_connected(monkeypatch):
    monkeypatch.setattr(mu, "_connections_collection", lambda: FakeConnections(None))
    assert mu.has_granted_scope("jack", "Mail.Read") is False
    assert mu.get_connection_status("jack")["connected"] is False


def test_locked_guest_identities_never_hold_a_connection(monkeypatch):
    monkeypatch.setattr(mu, "_connections_collection", lambda: FakeConnections({"scopes": ["Mail.Read"], "account_email": "x@y.z"}))
    for guest in mu.MICROSOFT_LOCKED_USERS:
        assert mu.get_connection_status(guest)["connected"] is False
        assert mu.has_granted_scope(guest, "Mail.Read") is False
        assert mu.get_encrypted_tokens(guest) is None


def test_status_never_exposes_token_fields(monkeypatch):
    doc = {"scopes": ["Mail.Read"], "account_email": "a@b.c", "access_token_encrypted": "SECRET", "refresh_token_encrypted": "SECRET2", "connected_at": "t"}
    monkeypatch.setattr(mu, "_connections_collection", lambda: FakeConnections(doc))
    status = mu.get_connection_status("jack")
    assert status["connected"] is True and "SECRET" not in str(status) and "SECRET2" not in str(status)


# ----- oauth -----

def test_authorization_url_is_personal_accounts_with_pkce_and_offline_access():
    url, state, verifier = MicrosoftOAuth().authorization_url()
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert "/consumers/" in parsed.path
    assert query["client_id"] == ["cid"] and query["state"] == [state] and query["code_challenge_method"] == ["S256"]
    assert "offline_access" in query["scope"][0] and "Mail.Send" in query["scope"][0] and "Files.Read" in query["scope"][0]
    # the verifier stays server-side: only its hash goes in the URL
    assert verifier not in url and query["code_challenge"][0]


def test_authorization_url_requires_configuration(monkeypatch):
    monkeypatch.delenv("MICROSOFT_CLIENT_ID")
    with pytest.raises(ValueError):
        MicrosoftOAuth().authorization_url()


def test_build_connection_encrypts_tokens_and_reads_email(monkeypatch):
    posted = {}

    def fake_post(url, data=None, timeout=None):
        posted.update(data)
        return Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "Mail.Read Calendars.ReadWrite"})

    monkeypatch.setattr(oauth_mod.requests, "post", fake_post)
    monkeypatch.setattr(oauth_mod.requests, "get", lambda *a, **k: Resp(200, {"mail": "jack@outlook.com"}))
    result = MicrosoftOAuth().build_connection("the-code", "the-verifier")
    assert posted["code"] == "the-code" and posted["code_verifier"] == "the-verifier" and posted["grant_type"] == "authorization_code"
    assert result["account_email"] == "jack@outlook.com" and result["scopes"] == ["Mail.Read", "Calendars.ReadWrite"]
    fernet = Fernet(KEY.encode())
    assert fernet.decrypt(result["access_token_encrypted"].encode()).decode() == "AT"
    assert fernet.decrypt(result["refresh_token_encrypted"].encode()).decode() == "RT"
    assert "AT" != result["access_token_encrypted"]


def test_build_connection_rejects_a_failed_exchange(monkeypatch):
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *a, **k: Resp(400, {"error": "invalid_request"}))
    with pytest.raises(ValueError):
        MicrosoftOAuth().build_connection("c", "v")


def _stored(expires_in_seconds, refresh=True):
    fernet = Fernet(KEY.encode())
    return {
        "access_token_encrypted": fernet.encrypt(b"OLD").decode(),
        "refresh_token_encrypted": fernet.encrypt(b"RT").decode() if refresh else None,
        "expires_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(seconds=expires_in_seconds),
    }


def test_a_fresh_token_is_returned_without_a_refresh(monkeypatch):
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: _stored(3000))
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *a, **k: pytest.fail("should not refresh"))
    assert MicrosoftOAuth().get_valid_access_token("jack") == "OLD"


def test_an_expiring_token_is_refreshed_and_the_rotated_refresh_token_saved(monkeypatch):
    saved = {}
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: _stored(10))
    monkeypatch.setattr(mu, "update_tokens", lambda u, **kw: saved.update(kw))
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *a, **k: Resp(200, {"access_token": "NEW", "refresh_token": "RT2", "expires_in": 3600}))
    assert MicrosoftOAuth().get_valid_access_token("jack") == "NEW"
    fernet = Fernet(KEY.encode())
    assert fernet.decrypt(saved["access_token_encrypted"].encode()).decode() == "NEW"
    assert fernet.decrypt(saved["refresh_token_encrypted"].encode()).decode() == "RT2"


def test_a_revoked_grant_deletes_the_connection(monkeypatch):
    deleted = []
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: _stored(-10))
    monkeypatch.setattr(mu, "delete_connection", lambda u: deleted.append(u))
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *a, **k: Resp(400, {"error": "invalid_grant"}))
    with pytest.raises(MicrosoftConnectionError) as caught:
        MicrosoftOAuth().get_valid_access_token("jack")
    assert not isinstance(caught.value, MicrosoftTemporaryError) and deleted == ["jack"]


def test_a_transient_failure_keeps_the_connection(monkeypatch):
    deleted = []
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: _stored(-10))
    monkeypatch.setattr(mu, "delete_connection", lambda u: deleted.append(u))
    monkeypatch.setattr(oauth_mod.requests, "post", lambda *a, **k: Resp(503, {"error": "temporarily_unavailable"}))
    with pytest.raises(MicrosoftTemporaryError):
        MicrosoftOAuth().get_valid_access_token("jack")
    assert deleted == []


def test_no_connection_raises(monkeypatch):
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: None)
    with pytest.raises(MicrosoftConnectionError):
        MicrosoftOAuth().get_valid_access_token("jack")


# ----- graph -----

def capture(monkeypatch, response):
    calls = []

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params or {}, "json": json, "headers": headers or {}})
        return response if not callable(response) else response(method, url)

    monkeypatch.setattr(graph.requests, "request", fake_request)
    return calls


def test_search_messages_quotes_the_query_and_returns_the_gmail_shape(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"value": [{"id": "m1", "subject": "Report", "from": {"emailAddress": {"name": "Sam", "address": "sam@x.com"}}, "receivedDateTime": "2026-10-19T10:00:00Z", "bodyPreview": "hello\n there"}]}))
    results = graph.search_messages("tok", 'from:sam "evil', 5)
    assert calls[0]["params"]["$search"] == '"from:sam  evil"' and calls[0]["params"]["$top"] == 5
    assert calls[0]["headers"]["Authorization"] == "Bearer tok"
    assert results == [{"id": "m1", "subject": "Report", "from": "Sam <sam@x.com>", "date": "2026-10-19T10:00:00Z", "snippet": "hello there"}]


def test_an_empty_search_lists_newest_mail_instead_of_searching(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"value": []}))
    graph.search_messages("tok", "  ", 10)
    assert "$search" not in calls[0]["params"] and calls[0]["params"]["$orderby"] == "receivedDateTime desc"


def test_max_results_is_capped(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"value": []}))
    graph.search_messages("tok", "x", 500)
    assert calls[0]["params"]["$top"] == 25


def test_message_detail_skips_inline_attachments(monkeypatch):
    def respond(method, url):
        if url.endswith("/attachments"):
            return Resp(200, {"value": [{"id": "a1", "name": "cv.pdf", "contentType": "application/pdf", "size": 10}, {"id": "a2", "name": "logo.png", "isInline": True}]})
        return Resp(200, {"subject": "Hi", "from": {"emailAddress": {"address": "a@b.c"}}, "receivedDateTime": "d", "hasAttachments": True, "body": {"contentType": "text", "content": "body"}})

    capture(monkeypatch, respond)
    detail = graph.get_message_detail("tok", "m1")
    assert detail["body_text"] == "body" and detail["body_is_html"] is False
    assert [a["filename"] for a in detail["attachments"]] == ["cv.pdf"]


def test_a_non_file_attachment_is_refused(monkeypatch):
    capture(monkeypatch, Resp(200, {"@odata.type": "#microsoft.graph.itemAttachment"}))
    assert graph.get_attachment_text("tok", "m", "a").startswith("ERROR")


def test_attachment_text_uses_a_sanitized_filename(monkeypatch):
    seen = {}

    def fake_extract(attachment):
        seen["name"] = attachment.filename
        return "contents"

    from backend.utils import attachment_utils
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", fake_extract)
    capture(monkeypatch, Resp(200, {"@odata.type": "#microsoft.graph.fileAttachment", "name": "..\\..\\evil/../x.txt", "contentBytes": base64.b64encode(b"hi").decode()}))
    assert graph.get_attachment_text("tok", "m", "a") == "contents"
    assert "/" not in seen["name"] and "\\" not in seen["name"] and ".." not in seen["name"]


def test_a_picture_is_not_sent_to_a_vision_model(monkeypatch):
    capture(monkeypatch, Resp(200, {"@odata.type": "#microsoft.graph.fileAttachment", "name": "photo.png", "contentBytes": base64.b64encode(b"x").decode()}))
    assert "image" in graph.get_attachment_text("tok", "m", "a")


def test_send_message_builds_a_plain_text_send_to_each_recipient(monkeypatch):
    calls = capture(monkeypatch, Resp(202))
    graph.send_message("tok", "a@x.com, b@y.com", "Subj", "Body")
    payload = calls[0]["json"]
    assert calls[0]["method"] == "POST" and calls[0]["url"].endswith("/me/sendMail")
    assert [r["emailAddress"]["address"] for r in payload["message"]["toRecipients"]] == ["a@x.com", "b@y.com"]
    assert payload["message"]["body"] == {"contentType": "Text", "content": "Body"} and payload["saveToSentItems"] is True


def test_day_bounds_follow_the_persons_timezone_including_dst():
    start, end = graph._day_bounds_utc("2026-10-19", "America/Chicago")  # CDT, UTC-5
    assert (start, end) == ("2026-10-19T05:00:00Z", "2026-10-20T05:00:00Z")


def test_calendar_events_get_a_real_offset_and_skip_cancelled(monkeypatch):
    capture(monkeypatch, Resp(200, {"value": [
        {"id": "e1", "subject": "Standup", "start": {"dateTime": "2026-10-19T09:00:00.0000000"}, "end": {"dateTime": "2026-10-19T09:30:00.0000000"}, "seriesMasterId": "s1"},
        {"id": "e2", "subject": "Gone", "isCancelled": True, "start": {"dateTime": "2026-10-19T10:00:00.0000000"}, "end": {"dateTime": "2026-10-19T10:30:00.0000000"}},
    ]}))
    events = graph.list_events_for_day("tok", "2026-10-19", "America/Chicago")
    assert [e["summary"] for e in events] == ["Standup"]
    assert events[0]["start"] == "2026-10-19T09:00:00-05:00" and events[0]["recurring_event_id"] == "s1"


def test_create_event_sends_local_time_in_the_persons_zone(monkeypatch):
    calls = capture(monkeypatch, Resp(201, {"id": "e1", "subject": "Call", "start": {"dateTime": "2026-10-20T14:00:00.0000000"}, "end": {"dateTime": "2026-10-20T14:30:00.0000000"}, "webLink": "https://outlook/e1"}))
    created = graph.create_event("tok", "Call", "2026-10-20T14:00:00", 30, "America/Chicago")
    body = calls[0]["json"]
    assert body["subject"] == "Call" and body["start"] == {"dateTime": "2026-10-20T14:00:00", "timeZone": "America/Chicago"}
    assert body["end"]["dateTime"] == "2026-10-20T14:30:00"
    assert created["html_link"] == "https://outlook/e1" and created["start"].endswith("-05:00")


def test_update_event_patches_only_what_changed(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"id": "e1", "subject": "New", "start": {"dateTime": "2026-10-20T14:00:00.0000000"}, "end": {"dateTime": "2026-10-20T14:30:00.0000000"}}))
    graph.update_event("tok", "e1", {"summary": "New"}, "America/Chicago")
    assert calls[0]["method"] == "PATCH" and calls[0]["json"] == {"subject": "New"}


def test_find_event_matches_a_title_fragment_case_insensitively(monkeypatch):
    capture(monkeypatch, Resp(200, {"value": [
        {"id": "e1", "subject": "Lunch", "start": {"dateTime": "2026-10-19T12:00:00"}, "end": {"dateTime": "2026-10-19T13:00:00"}},
        {"id": "e2", "subject": "Call with Sam", "start": {"dateTime": "2026-10-19T14:00:00"}, "end": {"dateTime": "2026-10-19T14:30:00"}},
    ]}))
    assert graph.find_event_by_summary_on_day("tok", "SAM", "2026-10-19", "America/Chicago")["id"] == "e2"
    assert graph.find_event_by_summary_on_day("tok", "dentist", "2026-10-19", "America/Chicago") is None


def test_onedrive_search_escapes_quotes_and_marks_folders(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"value": [{"id": "f1", "name": "Plan.docx", "file": {"mimeType": "application/x"}, "lastModifiedDateTime": "t"}, {"id": "d1", "name": "Docs", "folder": {}}]}))
    files = graph.search_files("tok", "o'brien", 5)
    assert "o%27%27brien" in calls[0]["url"]
    assert files[0]["mimeType"] == "application/x" and files[1]["mimeType"] == "folder"


def test_reading_a_folder_or_a_huge_file_is_refused_before_downloading(monkeypatch):
    calls = capture(monkeypatch, Resp(200, {"name": "Docs", "folder": {}}))
    assert "folder" in graph.read_file("tok", "d1") and len(calls) == 1
    capture(monkeypatch, Resp(200, {"name": "big.pdf", "size": graph.MAX_READ_BYTES + 1, "file": {}}))
    assert "too large" in graph.read_file("tok", "f1")


@pytest.mark.parametrize("status,needle", [(401, "Reconnect"), (403, "Reconnect"), (404, "isn't there"), (429, "rate limiting")])
def test_graph_errors_are_written_for_a_person(monkeypatch, status, needle):
    capture(monkeypatch, Resp(status, {"error": {"code": "x", "message": "m"}}))
    with pytest.raises(graph.GraphError) as caught:
        graph.search_messages("tok", "x")
    assert needle in str(caught.value)


def test_a_network_failure_becomes_a_graph_error(monkeypatch):
    def boom(*a, **k):
        raise graph.requests.ConnectionError("down")

    monkeypatch.setattr(graph.requests, "request", boom)
    with pytest.raises(graph.GraphError):
        graph.search_messages("tok", "x")


# ----- tools -----

def test_tool_says_to_connect_when_there_is_no_connection(monkeypatch):
    monkeypatch.setattr(mu, "get_encrypted_tokens", lambda u: None)
    assert run(mt.run_tool("search_outlook_mail", "jack", {"query": "x"})) == mt.NOT_CONNECTED


def test_tool_refuses_guest_identities():
    assert "not available" in run(mt.run_tool("search_outlook_mail", "guest_bty", {"query": "x"}))


def test_tool_says_reconnect_when_the_permission_was_unticked(monkeypatch):
    monkeypatch.setattr(MicrosoftOAuth, "get_valid_access_token", lambda self, u: "tok")
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: False)
    message = run(mt.run_tool("read_onedrive_file", "jack", {"file_id": "f"}))
    assert message.startswith("ERROR") and "OneDrive" in message and "Reconnect" in message


def test_temporary_refresh_failure_is_not_reported_as_not_connected(monkeypatch):
    def fail(self, u):
        raise MicrosoftTemporaryError("try again in a moment")

    monkeypatch.setattr(MicrosoftOAuth, "get_valid_access_token", fail)
    message = run(mt.run_tool("search_outlook_mail", "jack", {"query": "x"}))
    assert "try again" in message and message != mt.NOT_CONNECTED


def _connected(monkeypatch):
    monkeypatch.setattr(MicrosoftOAuth, "get_valid_access_token", lambda self, u: "tok")
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(mt, "get_user_timezone", lambda u: "America/Chicago")


def test_search_tool_formats_results_and_empty(monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setattr(graph, "search_messages", lambda t, q, n: [{"id": "m1", "subject": "S", "from": "a", "date": "d", "snippet": "sn"}])
    assert run(mt.run_tool("search_outlook_mail", "jack", {"query": "x"})) == "- [m1] S — a (d): sn"
    monkeypatch.setattr(graph, "search_messages", lambda t, q, n: [])
    assert run(mt.run_tool("search_outlook_mail", "jack", {"query": "x"})) == "No matching emails found."


def test_read_tool_lists_attachments(monkeypatch):
    _connected(monkeypatch)
    monkeypatch.setattr(graph, "get_message_detail", lambda t, m: {"subject": "S", "from": "a", "date": "d", "body_text": "B", "attachments": [{"filename": "cv.pdf", "attachment_id": "a1", "mime_type": "application/pdf"}]})
    out = run(mt.run_tool("read_outlook_message", "jack", {"message_id": "m1"}))
    assert "cv.pdf (id: a1, application/pdf)" in out and out.endswith("B")


def test_calendar_tool_defaults_to_the_persons_today_and_reports_a_graph_error(monkeypatch):
    _connected(monkeypatch)
    seen = {}
    monkeypatch.setattr(mt, "local_today", lambda tz: "2026-10-19")
    monkeypatch.setattr(graph, "list_events_for_day", lambda t, d, tz: seen.update(day=d) or [{"start": "2026-10-19T09:00:00-05:00", "summary": "Standup"}])
    assert run(mt.run_tool("list_outlook_calendar_events", "jack", {})) == "- 2026-10-19T09:00:00-05:00: Standup" and seen["day"] == "2026-10-19"

    def boom(t, d, tz):
        raise graph.GraphError("nope")

    monkeypatch.setattr(graph, "list_events_for_day", boom)
    assert run(mt.run_tool("list_outlook_calendar_events", "jack", {})) == "ERROR: nope"


def test_a_bad_date_argument_is_an_error_string_not_an_exception(monkeypatch):
    _connected(monkeypatch)

    def bad(t, d, tz):
        raise ValueError("Invalid isoformat string")

    monkeypatch.setattr(graph, "list_events_for_day", bad)
    assert run(mt.run_tool("list_outlook_calendar_events", "jack", {"date": "tomorrow"})).startswith("ERROR")


# ----- provider choice -----

@pytest.mark.parametrize("message,google,microsoft,expected", [
    ("email sam", True, False, "google"),
    ("email sam", False, True, "microsoft"),
    ("email sam", False, False, None),
    ("email sam", True, True, "google"),
    ("email sam from my outlook", True, True, "microsoft"),
    ("add it to my Hotmail calendar", True, True, "microsoft"),
])
def test_pick_provider(message, google, microsoft, expected):
    assert mt.pick_provider(message, google, microsoft) == expected


def test_microsoft_is_only_looked_up_when_it_could_change_the_answer(monkeypatch):
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: pytest.fail("no lookup needed"))
    assert mt.microsoft_usable("jack", "Mail.Send", True, "email sam") is False
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: True)
    assert mt.microsoft_usable("jack", "Mail.Send", False, "email sam") is True
    assert mt.microsoft_usable("jack", "Mail.Send", True, "email sam via outlook") is True


def test_the_card_account_line_round_trips():
    assert mt.provider_from_card(f"Ready to send this email:\n{mt.ACCOUNT_LINE}\n- **To:** a@b.c") == "microsoft"
    assert mt.provider_from_card("Ready to send this email:\n- **To:** a@b.c") is None


# ----- the write actions in the workflow -----

@pytest.fixture
def aw(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy")
    from backend.services import agent_workflow
    return agent_workflow


def _llm(content):
    return lambda username: SimpleNamespace(invoke=lambda prompt: SimpleNamespace(content=content))


def test_send_email_drafts_for_outlook_when_only_microsoft_is_connected(aw, monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda u, s: False)
    monkeypatch.setattr(aw.microsoft_utils, "has_granted_scope", lambda u, s: s == "Mail.Send")
    monkeypatch.setattr(aw, "get_chat_llm", _llm('{"to": "sam@x.com", "subject": "Hi", "body": "Hello"}'))
    details, card = aw._draft_send_email({"username": "jack", "messages": [HumanMessage(content="email sam@x.com hello")]})
    assert details["provider"] == "microsoft" and aw.microsoft_tools.ACCOUNT_LINE in card
    recovered = aw._recover_send_email([AIMessage(content="**Approval Required**\n\n" + card + "\n\n*Please Approve, Modify parameters, or Reject this action.*")], "")
    assert recovered == {"to": "sam@x.com", "subject": "Hi", "body": "Hello", "provider": "microsoft"}


def test_send_email_stays_on_google_by_default_and_has_no_account_line(aw, monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(aw.microsoft_utils, "has_granted_scope", lambda u, s: pytest.fail("Google is usable; Microsoft shouldn't be consulted"))
    monkeypatch.setattr(aw, "get_chat_llm", _llm('{"to": "sam@x.com", "subject": "Hi", "body": "Hello"}'))
    details, card = aw._draft_send_email({"username": "jack", "messages": [HumanMessage(content="email sam@x.com hello")]})
    assert "provider" not in details and "Account" not in card


def test_send_email_with_both_connected_follows_the_persons_words(aw, monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(aw.microsoft_utils, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(aw, "get_chat_llm", _llm('{"to": "sam@x.com", "subject": "Hi", "body": "Hello"}'))
    details, _ = aw._draft_send_email({"username": "jack", "messages": [HumanMessage(content="send an email from my outlook to sam@x.com")]})
    assert details["provider"] == "microsoft"


def test_send_email_with_neither_connected_keeps_the_existing_message(aw, monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda u, s: False)
    monkeypatch.setattr(aw.microsoft_utils, "has_granted_scope", lambda u, s: False)
    details, message = aw._draft_send_email({"username": "jack", "messages": [HumanMessage(content="email sam@x.com hello")]})
    assert details is None and "haven't connected Gmail send access" in message


def test_executing_an_outlook_send_never_touches_google(aw, monkeypatch):
    sent = {}
    monkeypatch.setattr(aw, "GoogleCalendarOAuth", lambda: pytest.fail("must not use Google"))
    monkeypatch.setattr(mt, "MicrosoftOAuth", lambda: SimpleNamespace(get_valid_access_token=lambda u: "tok"))
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(graph, "send_message", lambda t, to, subject, body: sent.update(to=to, subject=subject))
    result = aw._execute_send_email("jack", {"to": "sam@x.com", "subject": "Hi", "body": "B", "provider": "microsoft"})
    assert "Email sent" in result and sent == {"to": "sam@x.com", "subject": "Hi"}


def test_an_outlook_send_without_the_send_permission_says_so(aw, monkeypatch):
    monkeypatch.setattr(mt, "MicrosoftOAuth", lambda: SimpleNamespace(get_valid_access_token=lambda u: "tok"))
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: False)
    result = aw._execute_send_email("jack", {"to": "a@b.c", "subject": "s", "body": "b", "provider": "microsoft"})
    assert result.startswith("**Failed to send email:**") and "Reconnect" in result


def test_calendar_event_draft_execute_and_recover_for_outlook(aw, monkeypatch):
    monkeypatch.setattr(aw, "get_calendar_connection_status", lambda u: {"connected": False})
    monkeypatch.setattr(aw.microsoft_utils, "has_granted_scope", lambda u, s: s == "Calendars.ReadWrite")
    monkeypatch.setattr(aw, "get_user_timezone", lambda u: "America/Chicago")
    monkeypatch.setattr(aw, "get_chat_llm", _llm('{"summary": "Call with Sam", "start_iso": "2026-10-20T14:00:00", "duration_minutes": 30}'))
    details, card = aw._draft_create_calendar_event({"username": "jack", "messages": [HumanMessage(content="schedule a call with Sam tomorrow at 2pm")]})
    assert details["provider"] == "microsoft"
    assert aw._recover_create_calendar_event([AIMessage(content=card)], "")["provider"] == "microsoft"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", lambda: pytest.fail("must not use Google"))
    monkeypatch.setattr(mt, "MicrosoftOAuth", lambda: SimpleNamespace(get_valid_access_token=lambda u: "tok"))
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(mt, "get_user_timezone", lambda u: "America/Chicago")
    monkeypatch.setattr(graph, "create_event", lambda t, s, st, d, tz: {"id": "e", "summary": s, "start": st, "html_link": "https://outlook/e"})
    out = aw._execute_create_calendar_event("jack", details)
    assert "Event scheduled" in out and "View in Outlook" in out


def test_calendar_event_update_for_outlook_finds_then_patches(aw, monkeypatch):
    monkeypatch.setattr(aw, "GoogleCalendarOAuth", lambda: pytest.fail("must not use Google"))
    monkeypatch.setattr(mt, "MicrosoftOAuth", lambda: SimpleNamespace(get_valid_access_token=lambda u: "tok"))
    monkeypatch.setattr(mu, "has_granted_scope", lambda u, s: True)
    monkeypatch.setattr(mt, "get_user_timezone", lambda u: "America/Chicago")
    patched = {}
    monkeypatch.setattr(graph, "find_event_by_summary_on_day", lambda t, s, d, tz: {"id": "e1"})
    monkeypatch.setattr(graph, "update_event", lambda t, eid, updates, tz: patched.update(id=eid, updates=updates) or {"id": eid, "summary": "New", "start": "2026-10-20T15:00:00-05:00"})
    out = aw._execute_update_calendar_event("jack", {"search_summary": "sam", "event_date_iso": "2026-10-20", "new_summary": "New", "provider": "microsoft"})
    assert "Event updated" in out and patched == {"id": "e1", "updates": {"summary": "New"}}

    monkeypatch.setattr(graph, "find_event_by_summary_on_day", lambda t, s, d, tz: None)
    assert "couldn't find" in aw._execute_update_calendar_event("jack", {"search_summary": "sam", "event_date_iso": "2026-10-20", "provider": "microsoft"})


def test_the_dispatcher_offers_and_routes_the_outlook_tools(aw):
    from backend.services import native_tools
    for name in mt.TOOLS:
        assert name in native_tools.REGISTRY
    assert aw.classify_intent("what's on my outlook calendar today") == "tool_agent"
    assert aw.classify_intent("any new mail in outlook?") == "tool_agent"
    assert aw.classify_intent("search my onedrive for the resume") == "tool_agent"
