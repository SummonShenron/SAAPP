from unittest.mock import MagicMock

from backend.services import google_drive_service as gdrs


def _fake_service(monkeypatch, files_mock):
    monkeypatch.setattr(gdrs, "build", lambda *a, **k: MagicMock(files=lambda: files_mock))


def test_get_file_metadata(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "Report", "mimeType": "application/vnd.google-apps.document"}
    _fake_service(monkeypatch, files_mock)

    result = gdrs.get_file_metadata("token", "f1")
    assert result["name"] == "Report"


def test_search_files_passes_query_through_directly(monkeypatch):
    files_mock = MagicMock()
    captured = {}

    def fake_list(q, pageSize, fields):
        captured["q"] = q
        return MagicMock(execute=lambda: {"files": [{"id": "f1", "name": "Budget", "mimeType": "x", "modifiedTime": "t"}]})

    files_mock.list.side_effect = fake_list
    _fake_service(monkeypatch, files_mock)

    results = gdrs.search_files("token", "fullText contains 'budget'", max_results=5)

    assert captured["q"] == "fullText contains 'budget'"
    assert results == [{"id": "f1", "name": "Budget", "mimeType": "x", "modifiedTime": "t"}]


def test_search_files_returns_empty_list_for_no_matches(monkeypatch):
    files_mock = MagicMock()
    files_mock.list.return_value.execute.return_value = {}
    _fake_service(monkeypatch, files_mock)

    assert gdrs.search_files("token", "nothing") == []


def test_read_file_exports_native_google_doc_as_plain_text(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "Report", "mimeType": "application/vnd.google-apps.document"}
    files_mock.export.return_value.execute.return_value = b"Doc content as plain text"
    _fake_service(monkeypatch, files_mock)

    result = gdrs.read_file("token", "f1")
    assert result == "Doc content as plain text"
    files_mock.export.assert_called_once_with(fileId="f1", mimeType="text/plain")


def test_read_file_exports_native_sheet_as_csv(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet"}
    files_mock.export.return_value.execute.return_value = b"a,b,c\n1,2,3"
    _fake_service(monkeypatch, files_mock)

    result = gdrs.read_file("token", "f1")
    assert result == "a,b,c\n1,2,3"
    files_mock.export.assert_called_once_with(fileId="f1", mimeType="text/csv")


def test_read_file_reports_unreadable_native_type(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "Deck", "mimeType": "application/vnd.google-apps.presentation"}
    _fake_service(monkeypatch, files_mock)

    result = gdrs.read_file("token", "f1")
    assert result.startswith("ERROR:")
    assert "not readable as text" in result


def test_read_file_downloads_and_decodes_regular_text_file(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "notes.txt", "mimeType": "text/plain"}
    _fake_service(monkeypatch, files_mock)

    class _FakeDownloader:
        def __init__(self, buffer, request):
            self._buffer = buffer

        def next_chunk(self):
            self._buffer.write(b"plain file content")
            return None, True

    monkeypatch.setattr(gdrs, "MediaIoBaseDownload", _FakeDownloader)

    result = gdrs.read_file("token", "f1")
    assert result == "plain file content"


def test_read_file_reports_non_utf8_regular_file(monkeypatch):
    files_mock = MagicMock()
    files_mock.get.return_value.execute.return_value = {"id": "f1", "name": "image.png", "mimeType": "image/png"}
    _fake_service(monkeypatch, files_mock)

    class _FakeDownloader:
        def __init__(self, buffer, request):
            self._buffer = buffer

        def next_chunk(self):
            self._buffer.write(b"\xff\xfe\x00\x01")
            return None, True

    monkeypatch.setattr(gdrs, "MediaIoBaseDownload", _FakeDownloader)

    result = gdrs.read_file("token", "f1")
    assert result.startswith("ERROR:")
    assert "binary" in result
