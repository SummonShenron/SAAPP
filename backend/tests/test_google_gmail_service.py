import base64
import json
from unittest.mock import MagicMock

from backend.services import google_gmail_service as ggs


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _fake_service(monkeypatch, users_mock):
    monkeypatch.setattr(ggs, "build", lambda *a, **k: MagicMock(users=lambda: users_mock))


def test_search_messages_fetches_metadata_per_candidate(monkeypatch):
    users_mock = MagicMock()
    users_mock.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }

    def fake_get(userId, id, format, metadataHeaders):
        headers = {
            "m1": [{"name": "Subject", "value": "Weekly Report"}, {"name": "From", "value": "a@x.com"}, {"name": "Date", "value": "Mon"}],
            "m2": [{"name": "Subject", "value": "Other"}, {"name": "From", "value": "b@x.com"}, {"name": "Date", "value": "Tue"}],
        }[id]
        return MagicMock(execute=lambda: {"payload": {"headers": headers}, "snippet": f"snippet-{id}"})

    users_mock.messages.return_value.get.side_effect = fake_get
    _fake_service(monkeypatch, users_mock)

    results = ggs.search_messages("token", "subject:report", max_results=10)

    assert len(results) == 2
    assert results[0] == {"id": "m1", "subject": "Weekly Report", "from": "a@x.com", "date": "Mon", "snippet": "snippet-m1"}


def test_search_messages_returns_empty_list_for_no_matches(monkeypatch):
    users_mock = MagicMock()
    users_mock.messages.return_value.list.return_value.execute.return_value = {}
    _fake_service(monkeypatch, users_mock)

    assert ggs.search_messages("token", "nothing matches this") == []


def test_get_message_detail_extracts_plain_text_and_attachments(monkeypatch):
    users_mock = MagicMock()
    payload = {
        "headers": [{"name": "Subject", "value": "Report"}, {"name": "From", "value": "a@x.com"}, {"name": "Date", "value": "Mon"}],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Here is the report.")}},
            {"filename": "export.json", "mimeType": "application/json", "body": {"attachmentId": "att1", "size": 123}},
        ],
    }
    users_mock.messages.return_value.get.return_value.execute.return_value = {"payload": payload}
    _fake_service(monkeypatch, users_mock)

    detail = ggs.get_message_detail("token", "m1")

    assert detail["subject"] == "Report"
    assert detail["body_text"] == "Here is the report."
    assert detail["body_is_html"] is False
    assert detail["attachments"] == [{"filename": "export.json", "mime_type": "application/json", "attachment_id": "att1", "size": 123}]


def test_get_message_detail_falls_back_to_html_when_no_plain_text(monkeypatch):
    users_mock = MagicMock()
    payload = {
        "headers": [],
        "parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>hi</p>")}}],
    }
    users_mock.messages.return_value.get.return_value.execute.return_value = {"payload": payload}
    _fake_service(monkeypatch, users_mock)

    detail = ggs.get_message_detail("token", "m1")

    assert detail["body_text"] == "<p>hi</p>"
    assert detail["body_is_html"] is True


def test_get_message_detail_handles_nested_multipart(monkeypatch):
    users_mock = MagicMock()
    payload = {
        "headers": [],
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("plain version")}},
                    {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
                ],
            }
        ],
    }
    users_mock.messages.return_value.get.return_value.execute.return_value = {"payload": payload}
    _fake_service(monkeypatch, users_mock)

    detail = ggs.get_message_detail("token", "m1")
    assert detail["body_text"] == "plain version"


def test_get_attachment_text_pretty_prints_valid_json(monkeypatch):
    users_mock = MagicMock()
    raw_json = json.dumps({"activity": "coding", "hours": 3})
    users_mock.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": _b64(raw_json)
    }
    _fake_service(monkeypatch, users_mock)

    result = ggs.get_attachment_text("token", "m1", "att1")
    assert json.loads(result) == {"activity": "coding", "hours": 3}
    assert "\n" in result  # pretty-printed, not minified


def test_get_attachment_text_falls_back_to_raw_text_for_non_json(monkeypatch):
    users_mock = MagicMock()
    users_mock.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": _b64("just plain text, not json")
    }
    _fake_service(monkeypatch, users_mock)

    assert ggs.get_attachment_text("token", "m1", "att1") == "just plain text, not json"


def test_get_attachment_text_reports_non_utf8_content(monkeypatch):
    users_mock = MagicMock()
    users_mock.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": base64.urlsafe_b64encode(b"\xff\xfe\x00\x01").decode().rstrip("=")
    }
    _fake_service(monkeypatch, users_mock)

    assert ggs.get_attachment_text("token", "m1", "att1") == "ERROR: attachment is not valid UTF-8 text"


def test_send_message_builds_correct_raw_payload(monkeypatch):
    users_mock = MagicMock()
    captured = {}

    def fake_send(userId, body):
        captured["body"] = body
        return MagicMock(execute=lambda: {"id": "sent1"})

    users_mock.messages.return_value.send.side_effect = fake_send
    _fake_service(monkeypatch, users_mock)

    result = ggs.send_message("token", "sam@example.com", "Hello", "Just checking in.")

    raw = captured["body"]["raw"]
    padded = raw + "=" * (-len(raw) % 4)
    decoded = base64.urlsafe_b64decode(padded).decode()
    assert "To: sam@example.com" in decoded
    assert "Subject: Hello" in decoded
    assert "Just checking in." in decoded
    assert result == {"id": "sent1"}
