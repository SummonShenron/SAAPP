from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from backend.services import google_docs_service as gds


def _fake_service(monkeypatch, documents_mock):
    monkeypatch.setattr(gds, "build", lambda *a, **k: MagicMock(documents=lambda: documents_mock))


def _http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, b"error body")


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
