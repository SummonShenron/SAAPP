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


def test_process_user_attachment_still_summarizes_documents(monkeypatch):
    monkeypatch.setattr(attachment_utils, "extract_text_from_attachment", Mock(return_value="Some PDF text."))
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
