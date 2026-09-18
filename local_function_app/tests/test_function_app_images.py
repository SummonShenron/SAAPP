import base64
from types import SimpleNamespace
from unittest.mock import Mock

from local_function_app import function_app as fa


def test_is_image_filename_detects_common_extensions():
    for ext in ["png", "jpg", "jpeg", "gif", "webp", "bmp", "PNG", "JPG"]:
        assert fa._is_image_filename(f"file.{ext}")


def test_is_image_filename_false_for_pdf_and_no_extension():
    assert not fa._is_image_filename("report.pdf")
    assert not fa._is_image_filename("no_extension")


def test_guess_image_mime_type_known_and_unknown_extensions():
    assert fa._guess_image_mime_type("photo.png") == "image/png"
    assert fa._guess_image_mime_type("photo.JPG") == "image/jpeg"
    assert fa._guess_image_mime_type("mystery.xyz") == "application/octet-stream"


def test_describe_image_bytes_returns_text_on_success(monkeypatch):
    fake_response = SimpleNamespace(content="A red square on a white background.")
    fake_llm = Mock()
    fake_llm.invoke = Mock(return_value=fake_response)
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=fake_llm))

    description = fa._describe_image_bytes(b"fake-bytes", "image/png")

    assert description == "A red square on a white background."
    messages = fake_llm.invoke.call_args[0][0]
    content = messages[0].content
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == b"fake-bytes"


def test_describe_image_bytes_handles_list_content(monkeypatch):
    fake_response = SimpleNamespace(content=[{"type": "text", "text": "A diagram."}])
    fake_llm = Mock()
    fake_llm.invoke = Mock(return_value=fake_response)
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=fake_llm))

    assert fa._describe_image_bytes(b"bytes", "image/png") == "A diagram."


def test_describe_image_bytes_returns_empty_on_failure(monkeypatch):
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(side_effect=Exception("vision API down")))
    assert fa._describe_image_bytes(b"bytes", "image/png") == ""


def _fake_image_file(name, data, size, fmt="PNG"):
    return SimpleNamespace(name=name, data=data, image=SimpleNamespace(size=size, format=fmt))


def _fake_reader(images_by_page):
    pages = []
    for page_images in images_by_page:
        pages.append(SimpleNamespace(images=page_images))
    return SimpleNamespace(pages=pages)


def test_extract_embedded_images_skips_below_minimum_dimension(monkeypatch):
    monkeypatch.setattr(fa, "_describe_image_bytes", Mock(return_value="description"))
    monkeypatch.setattr(fa, "MIN_EMBEDDED_IMAGE_DIMENSION_PX", 100)

    reader = _fake_reader([[_fake_image_file("Im0.png", b"tiny", (50, 50))]])
    fs = Mock()

    results = fa._extract_embedded_images(reader, 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=5)

    assert results == []
    fs.put.assert_not_called()


def test_extract_embedded_images_stores_and_describes_passing_image(monkeypatch):
    monkeypatch.setattr(fa, "_describe_image_bytes", Mock(return_value="A real description."))
    monkeypatch.setattr(fa, "MIN_EMBEDDED_IMAGE_DIMENSION_PX", 100)

    reader = _fake_reader([[], [], [_fake_image_file("Im0.png", b"real-bytes", (200, 200))]])
    fs = Mock()
    fs.put = Mock(return_value="fake-object-id")

    results = fa._extract_embedded_images(reader, 2, fs, "Affiliate_D", "doc.pdf", budget_remaining=5)

    assert len(results) == 1
    assert results[0] == {
        "gridfs_id": "fake-object-id",
        "content_type": "image/png",
        "filename": "Im0.png",
        "description": "A real description.",
    }
    fs.put.assert_called_once()
    call_args, call_kwargs = fs.put.call_args
    assert call_args[0] == b"real-bytes"
    assert call_kwargs["filename"] == "doc.pdf_p2_Im0.png"
    assert call_kwargs["metadata"] == {
        "affiliate": "Affiliate_D",
        "kind": "kb_embedded_image",
        "source_document": "doc.pdf",
        "page": 2,
        "content_type": "image/png",
    }


def test_extract_embedded_images_respects_budget_cap(monkeypatch):
    monkeypatch.setattr(fa, "_describe_image_bytes", Mock(return_value="desc"))
    monkeypatch.setattr(fa, "MIN_EMBEDDED_IMAGE_DIMENSION_PX", 100)

    images = [_fake_image_file(f"Im{i}.png", b"bytes", (200, 200)) for i in range(3)]
    reader = _fake_reader([images])
    fs = Mock()
    fs.put = Mock(side_effect=[f"id-{i}" for i in range(3)])

    results = fa._extract_embedded_images(reader, 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=2)

    assert len(results) == 2
    assert fs.put.call_count == 2


def test_extract_embedded_images_zero_budget_returns_immediately(monkeypatch):
    fs = Mock()
    reader = _fake_reader([[_fake_image_file("Im0.png", b"bytes", (200, 200))]])
    assert fa._extract_embedded_images(reader, 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=0) == []
    fs.put.assert_not_called()


def test_extract_embedded_images_swallows_unreadable_image_and_continues(monkeypatch):
    monkeypatch.setattr(fa, "_describe_image_bytes", Mock(return_value="desc"))
    monkeypatch.setattr(fa, "MIN_EMBEDDED_IMAGE_DIMENSION_PX", 100)

    class BrokenImageFile:
        name = "broken.png"

        @property
        def image(self):
            raise Exception("decode failed")

    broken = BrokenImageFile()
    good = _fake_image_file("good.png", b"bytes", (200, 200))

    reader = _fake_reader([[broken, good]])
    fs = Mock()
    fs.put = Mock(return_value="good-id")

    results = fa._extract_embedded_images(reader, 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=5)

    assert len(results) == 1
    assert results[0]["gridfs_id"] == "good-id"


def test_extract_embedded_images_none_pil_image_is_skipped(monkeypatch):
    monkeypatch.setattr(fa, "_describe_image_bytes", Mock(return_value="desc"))
    reader = _fake_reader([[SimpleNamespace(name="undecoded.png", data=b"bytes", image=None)]])
    fs = Mock()

    assert fa._extract_embedded_images(reader, 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=5) == []
    fs.put.assert_not_called()


def test_extract_embedded_images_page_read_failure_returns_empty():
    class BrokenReader:
        @property
        def pages(self):
            raise Exception("no pages")

    fs = Mock()
    assert fa._extract_embedded_images(BrokenReader(), 0, fs, "Affiliate_D", "doc.pdf", budget_remaining=5) == []
