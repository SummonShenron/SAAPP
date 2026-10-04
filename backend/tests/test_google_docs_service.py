import json
from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from backend.services import google_docs_service as gds


def _fake_service(monkeypatch, documents_mock):
    monkeypatch.setattr(gds, "build", lambda *a, **k: MagicMock(documents=lambda: documents_mock))


def _http_error(status: int, body: dict | None = None) -> HttpError:
    resp = MagicMock()
    resp.status = status
    content = json.dumps(body).encode() if body is not None else b"error body"
    return HttpError(resp, content)


# The exact shape Google returned when the Docs API was switched off for the Cloud project (a real
# 403 captured while diagnosing "you don't have edit access" on a document the user owned).
SERVICE_DISABLED_BODY = {"error": {
    "code": 403, "status": "PERMISSION_DENIED",
    "message": "Google Docs API has not been used in project 123 before or it is disabled.",
    "details": [{
        "@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED",
        "domain": "googleapis.com",
        "metadata": {"activationUrl": "https://console.developers.google.com/apis/api/docs.googleapis.com/overview?project=123"},
    }],
}}


def test_append_text_uses_end_of_segment_location(monkeypatch):
    documents_mock = MagicMock()
    captured = {}

    def fake_batch_update(documentId, body):
        captured["documentId"] = documentId
        captured["body"] = body
        return MagicMock(execute=lambda: {})

    documents_mock.batchUpdate.side_effect = fake_batch_update
    _fake_service(monkeypatch, documents_mock)

    result = gds.append_text("token", "doc123", "Weekly summary text")

    assert captured["documentId"] == "doc123"
    request = captured["body"]["requests"][0]["insertText"]
    assert request["endOfSegmentLocation"] == {}
    assert "Weekly summary text" in request["text"]
    assert result == "Appended successfully."


def test_append_text_translates_404_to_friendly_message(monkeypatch):
    documents_mock = MagicMock()
    documents_mock.batchUpdate.return_value.execute.side_effect = _http_error(404)
    _fake_service(monkeypatch, documents_mock)

    result = gds.append_text("token", "doc123", "text")
    assert "no longer exists" in result


def test_append_text_translates_403_to_friendly_message(monkeypatch):
    documents_mock = MagicMock()
    documents_mock.batchUpdate.return_value.execute.side_effect = _http_error(403)
    _fake_service(monkeypatch, documents_mock)

    result = gds.append_text("token", "doc123", "text")
    assert "edit access" in result


def test_append_text_generic_failure_still_returns_error_string(monkeypatch):
    documents_mock = MagicMock()
    documents_mock.batchUpdate.return_value.execute.side_effect = _http_error(500)
    _fake_service(monkeypatch, documents_mock)

    result = gds.append_text("token", "doc123", "text")
    assert result.startswith("ERROR:")


def test_get_document_returns_raw_document(monkeypatch):
    documents_mock = MagicMock()
    documents_mock.get.return_value.execute.return_value = {"documentId": "doc123", "title": "My Doc"}
    _fake_service(monkeypatch, documents_mock)

    result = gds.get_document("token", "doc123")
    assert result["title"] == "My Doc"


def _append_with_error(monkeypatch, error):
    documents_mock = MagicMock()
    documents_mock.batchUpdate.return_value.execute.side_effect = error
    _fake_service(monkeypatch, documents_mock)
    return gds.append_text("token", "doc123", "text")


def test_disabled_docs_api_is_not_reported_as_a_permissions_problem(monkeypatch):
    result = _append_with_error(monkeypatch, _http_error(403, SERVICE_DISABLED_BODY))

    assert "isn't enabled" in result
    assert "edit access" not in result
    assert "not your document" in result


def test_disabled_docs_api_logs_the_activation_url_for_the_operator(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.ERROR, logger="SASS Logger"):
        _append_with_error(monkeypatch, _http_error(403, SERVICE_DISABLED_BODY))

    assert "docs.googleapis.com/overview?project=123" in caplog.text


def test_missing_scope_403_tells_the_user_to_reconnect(monkeypatch):
    body = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes.",
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}
    result = _append_with_error(monkeypatch, _http_error(403, body))

    assert "Reconnect Google" in result


def test_legacy_style_error_reason_is_recognised(monkeypatch):
    body = {"error": {"code": 403, "message": "Access Not Configured.", "errors": [{"reason": "accessNotConfigured"}]}}
    assert "isn't enabled" in _append_with_error(monkeypatch, _http_error(403, body))


def test_a_genuine_no_edit_access_403_still_says_so(monkeypatch):
    body = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "The caller does not have permission",
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "forbidden"}]}}
    assert "edit access" in _append_with_error(monkeypatch, _http_error(403, body))
    assert "edit access" in _append_with_error(monkeypatch, _http_error(403))  # unparseable body: same fallback
