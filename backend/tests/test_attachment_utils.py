import base64
from types import SimpleNamespace
from unittest.mock import Mock

from backend.models.attachment import Attachment
from backend.utils import attachment_utils


def _image_attachment(filename="photo.png"):
    return Attachment(filename=filename, content=base64.b64encode(b"fake-image-bytes").decode("utf-8"))


def test_is_image_attachment_detects_common_extensions():
    for ext in ["png", "jpg", "jpeg", "gif", "webp", "bmp", "PNG", "JPG"]:
        assert attachment_utils.is_image_attachment(f"file.{ext}")


def test_is_image_attachment_false_for_documents():
    assert not attachment_utils.is_image_attachment("resume.pdf")
    assert not attachment_utils.is_image_attachment("notes.docx")
    assert not attachment_utils.is_image_attachment("no_extension")


def test_describe_image_attachment_sends_vision_message_and_returns_text(monkeypatch):
    invoke_mock = Mock(return_value=SimpleNamespace(content="A screenshot showing a login form."))
    monkeypatch.setattr(attachment_utils.llm, "invoke", invoke_mock)

    description = attachment_utils.describe_image_attachment(_image_attachment())

    assert description == "A screenshot showing a login form."
    messages = invoke_mock.call_args[0][0]
    content = messages[0].content
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_describe_image_attachment_handles_list_content(monkeypatch):
    monkeypatch.setattr(
        attachment_utils.llm, "invoke",
        Mock(return_value=SimpleNamespace(content=[{"type": "text", "text": "A diagram of a network."}]))
    )
    assert attachment_utils.describe_image_attachment(_image_attachment()) == "A diagram of a network."


def test_describe_image_attachment_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(attachment_utils.llm, "invoke", Mock(side_effect=Exception("vision API down")))
    assert attachment_utils.describe_image_attachment(_image_attachment()) == ""


def test_extract_text_from_attachment_routes_images_to_vision_description(monkeypatch):
    monkeypatch.setattr(attachment_utils, "describe_image_attachment", Mock(return_value="A red bicycle."))
    assert attachment_utils.extract_text_from_attachment(_image_attachment()) == "A red bicycle."


def test_process_user_attachment_returns_vision_description_directly_for_images(monkeypatch):
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value="A red bicycle."))
    llm_invoke = Mock()
    monkeypatch.setattr(attachment_utils.llm, "invoke", llm_invoke)

    result = attachment_utils.process_user_attachment(_image_attachment())

    assert result == "A red bicycle."
    llm_invoke.assert_not_called()  # no redundant ATTACHMENT_PROMPT summarization pass for images


def test_process_user_attachment_still_summarizes_long_documents(monkeypatch):
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value="Some PDF text. " * 600))
    monkeypatch.setattr(
        attachment_utils.llm, "invoke",
        Mock(return_value=SimpleNamespace(content="Structured summary."))
    )

    result = attachment_utils.process_user_attachment(Attachment(filename="resume.pdf", content="ZmFrZQ=="))

    assert result == "Structured summary."


def test_process_user_attachment_no_readable_text_short_circuits_before_image_check(monkeypatch):
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value=""))
    assert attachment_utils.process_user_attachment(_image_attachment()) == "Attachment contained no readable text."


def test_guess_image_mime_type_known_and_unknown_extensions():
    assert attachment_utils.guess_image_mime_type("photo.png") == "image/png"
    assert attachment_utils.guess_image_mime_type("photo.JPG") == "image/jpeg"
    assert attachment_utils.guess_image_mime_type("mystery.xyz") == "application/octet-stream"


def test_store_image_in_gridfs_returns_none_when_db_unavailable():
    assert attachment_utils.store_image_in_gridfs(None, "jack", "sess-1", _image_attachment()) is None


def test_store_image_in_gridfs_writes_decoded_bytes_with_metadata(monkeypatch):
    fake_gridfs_instance = Mock()
    fake_gridfs_instance.put = Mock(return_value="fake-object-id")
    fake_gridfs_class = Mock(return_value=fake_gridfs_instance)
    monkeypatch.setattr(attachment_utils, "GridFS", fake_gridfs_class)

    att = _image_attachment("banner.png")
    result = attachment_utils.store_image_in_gridfs(Mock(), "jack", "sess-1", att)

    assert result == "fake-object-id"
    fake_gridfs_instance.put.assert_called_once()
    call_args, call_kwargs = fake_gridfs_instance.put.call_args
    assert call_args[0] == base64.b64decode(att.content)
    assert call_kwargs["filename"] == "banner.png"
    assert call_kwargs["metadata"] == {
        "username": "jack",
        "session_id": "sess-1",
        "content_type": "image/png",
        "kind": "chat_attachment",
    }


def test_store_image_in_gridfs_returns_none_on_failure(monkeypatch):
    fake_gridfs_instance = Mock()
    fake_gridfs_instance.put = Mock(side_effect=Exception("mongo down"))
    monkeypatch.setattr(attachment_utils, "GridFS", Mock(return_value=fake_gridfs_instance))

    assert attachment_utils.store_image_in_gridfs(Mock(), "jack", "sess-1", _image_attachment()) is None


def test_an_image_is_read_by_the_vision_model_once_and_its_text_reused(monkeypatch):
    extract = Mock(return_value="A red bicycle.")
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", extract)
    monkeypatch.setattr(attachment_utils, "get_db", lambda: None)  # no database: nothing stored, nothing to wait on
    summarize = Mock()
    monkeypatch.setattr(attachment_utils.llm, "invoke", summarize)

    summaries, refs = attachment_utils.process_chat_attachments("jack", "s1", [_image_attachment()])

    assert extract.call_count == 1  # it used to be two vision calls for one photo: one to store the text, one to summarize
    assert summaries == ["A red bicycle."] and refs == []
    summarize.assert_not_called()  # an image's description is already its summary


def test_a_document_is_read_once_summarized_and_not_treated_as_an_image(monkeypatch):
    extract = Mock(return_value="Some PDF text. " * 600)
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", extract)
    monkeypatch.setattr(attachment_utils, "get_db", lambda: None)
    monkeypatch.setattr(attachment_utils.llm, "invoke", Mock(return_value=SimpleNamespace(content="A summary.")))
    stored = Mock()
    monkeypatch.setattr(attachment_utils, "store_image_in_gridfs", stored)

    summaries, refs = attachment_utils.process_chat_attachments("jack", "s1", [Attachment(filename="resume.pdf", content="ZmFrZQ==")])

    assert extract.call_count == 1 and summaries == ["A summary."] and refs == []
    stored.assert_not_called()


def test_a_stored_image_comes_back_as_a_reference_the_page_can_render(monkeypatch):
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value="A red bicycle."))
    monkeypatch.setattr(attachment_utils, "get_db", lambda: object())
    monkeypatch.setattr(attachment_utils, "ingest_doc_to_session", Mock())
    monkeypatch.setattr(attachment_utils, "store_image_in_gridfs", Mock(return_value="abc123"))

    _, refs = attachment_utils.process_chat_attachments("jack", "s1", [_image_attachment("photo.jpg")])

    assert refs == [{"filename": "photo.jpg", "gridfs_id": "abc123", "content_type": "image/jpeg"}]


def test_the_chat_endpoint_does_its_attachment_work_off_the_event_loop():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(\n            process_chat_attachments, username, session_id, request.attachments" in source
    assert "process_user_attachment(att)" not in source  # the old inline loop, on the event loop


def test_a_short_document_reaches_the_model_as_written_so_no_date_or_amount_is_lost(monkeypatch):
    letter = "Your premium changes from $1,180 to $1,396 on March 1. Dispute by February 14."
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value=letter))
    summarize = Mock()
    monkeypatch.setattr(attachment_utils.llm, "invoke", summarize)

    assert attachment_utils.process_user_attachment(Attachment(filename="notice.pdf", content="ZmFrZQ==")) == letter
    summarize.assert_not_called()                                     # and no model call is paid for


def test_the_summary_prompt_for_long_documents_keeps_specifics_and_is_not_about_resumes():
    from backend.components.constraints import ATTACHMENT_PROMPT

    for needle in ("dates", "amounts", "exactly as written", "action required"):
        assert needle in ATTACHMENT_PROMPT
    assert "Skills, experience" not in ATTACHMENT_PROMPT


def test_a_document_just_over_the_limit_is_summarized_and_one_at_the_limit_is_not(monkeypatch):
    summarize = Mock(return_value=SimpleNamespace(content="A summary."))
    monkeypatch.setattr(attachment_utils.llm, "invoke", summarize)
    at_limit = "x" * attachment_utils.SHORT_DOCUMENT_CHARS
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value=at_limit))
    assert attachment_utils.process_user_attachment(Attachment(filename="a.pdf", content="ZmFrZQ==")) == at_limit
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value=at_limit + "y"))
    assert attachment_utils.process_user_attachment(Attachment(filename="a.pdf", content="ZmFrZQ==")) == "A summary."


# ---- what a deleted conversation leaves behind ---------------------------------------------------------------------------------------------

class _Coll:
    def __init__(self, rows=(), deleted=0):
        self.rows, self.deleted, self.find_filter, self.delete_filter = list(rows), deleted, None, None

    def delete_many(self, filt):
        self.delete_filter = filt
        return SimpleNamespace(deleted_count=self.deleted)

    def find(self, filt, projection=None):
        self.find_filter = filt
        return iter(self.rows)


class _Db(dict):
    pass


def test_deleting_a_conversation_removes_its_extracted_text_and_its_stored_photos_and_nothing_else(monkeypatch):
    documents, files = _Coll(deleted=2), _Coll(rows=[{"_id": "a"}, {"_id": "b"}])
    monkeypatch.setattr(attachment_utils, "get_db", lambda: _Db({"session_documents": documents, "fs.files": files}))
    gridfs = Mock()
    monkeypatch.setattr(attachment_utils, "GridFS", Mock(return_value=gridfs))

    out = attachment_utils.delete_session_attachments("jack", "s1")

    assert out == {"documents": 2, "files": 2}
    assert documents.delete_filter == {"username": "jack", "session_id": "s1"}
    assert files.find_filter == {"metadata.username": "jack", "metadata.session_id": "s1", "metadata.kind": "chat_attachment"}   # never a knowledge-base file
    assert [c.args[0] for c in gridfs.delete.call_args_list] == ["a", "b"]


def test_cleaning_up_attachments_never_raises_and_does_nothing_without_a_database(monkeypatch):
    monkeypatch.setattr(attachment_utils, "get_db", lambda: None)
    assert attachment_utils.delete_session_attachments("jack", "s1") == {"documents": 0, "files": 0}

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(attachment_utils, "get_db", lambda: Broken())
    assert attachment_utils.delete_session_attachments("jack", "s1") == {"documents": 0, "files": 0}


def test_every_way_of_deleting_or_clearing_a_conversation_also_removes_its_attachments():
    source = open("app.py", encoding="utf-8").read()
    call = "await asyncio.to_thread(delete_session_attachments, username, session_id)"
    remove = source[source.index('@app.delete("/api/conversations/{session_id}")'):source.index('@app.post("/api/chat/feedback")')]
    assert call in remove
    clear = source[source.index('@app.post("/api/chat/clear")'):source.index('@app.post("/api/upload-attachment")')]
    assert clear.count("delete_session_attachments") == 2          # the one conversation emptied in place, and the wipe of every conversation
