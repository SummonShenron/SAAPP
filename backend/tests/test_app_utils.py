import re

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils
from backend.utils.app_utils import collect_kb_images, _serialize_messages, save_correction, fetch_relevant_corrections


def test_collect_kb_images_returns_empty_for_no_image_docs():
    docs = [Document(page_content="plain text", metadata={"source": "doc.pdf"})]
    assert collect_kb_images(docs) == []


def test_collect_kb_images_includes_standalone_image_doc():
    docs = [Document(page_content="A red square.", metadata={
        "doc_type": "standalone_image", "gridfs_id": "abc123", "source": "logo.png",
    })]
    assert collect_kb_images(docs) == [{"filename": "logo.png", "fileId": "abc123"}]


def test_collect_kb_images_includes_embedded_images_list():
    docs = [Document(page_content="Page text.", metadata={
        "embedded_images": [
            {"gridfs_id": "img1", "filename": "chart.png", "description": "A bar chart."},
            {"gridfs_id": "img2", "filename": "diagram.png", "description": "A flow diagram."},
        ],
    })]
    assert collect_kb_images(docs) == [
        {"filename": "chart.png", "fileId": "img1"},
        {"filename": "diagram.png", "fileId": "img2"},
    ]


def test_collect_kb_images_dedupes_across_chunks_from_same_page():
    shared_embedded = [{"gridfs_id": "img1", "filename": "chart.png"}]
    docs = [
        Document(page_content="chunk 1", metadata={"embedded_images": shared_embedded}),
        Document(page_content="chunk 2", metadata={"embedded_images": shared_embedded}),
    ]
    assert collect_kb_images(docs) == [{"filename": "chart.png", "fileId": "img1"}]


def test_collect_kb_images_ignores_embedded_entry_missing_gridfs_id():
    docs = [Document(page_content="text", metadata={
        "embedded_images": [{"filename": "broken.png", "description": "no id"}],
    })]
    assert collect_kb_images(docs) == []


def test_collect_kb_images_handles_missing_metadata_gracefully():
    doc = Document(page_content="text")
    doc.metadata = None
    assert collect_kb_images([doc]) == []


def test_serialize_messages_includes_kb_images_when_present():
    ai_msg = AIMessage(content="Here's the diagram.")
    ai_msg.additional_kwargs["kb_images"] = [{"filename": "chart.png", "fileId": "img1"}]

    serialized = _serialize_messages([HumanMessage(content="show me the chart"), ai_msg])

    assert serialized[0].get("kb_images") is None
    assert serialized[1]["kb_images"] == [{"filename": "chart.png", "fileId": "img1"}]


def test_serialize_messages_omits_kb_images_when_absent():
    serialized = _serialize_messages([AIMessage(content="plain reply")])
    assert "kb_images" not in serialized[0]


# ---------------------------------------------------------------------------
# save_correction / fetch_relevant_corrections
# ---------------------------------------------------------------------------

def _matches(value, query_value):
    if hasattr(query_value, "search"):  # compiled regex
        return bool(query_value.search(value or ""))
    if isinstance(query_value, dict) and "$ne" in query_value:
        return value != query_value["$ne"]
    return value == query_value


class _FakeCursor(list):
    def limit(self, n):
        return self[:n]


class _FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(doc)

    def find(self, query):
        return _FakeCursor(
            doc for doc in self.docs
            if all(_matches(doc.get(k), v) for k, v in query.items())
        )


class _FakeDB:
    def __init__(self):
        self.corrections = _FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


def test_save_correction_persists_rating_field(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    save_correction("jack", "What is the refund policy?", "We don't do refunds.", "Wrong, we do 30-day refunds.", "hallucination", rating="positive")

    assert len(fake_db.corrections.docs) == 1
    assert fake_db.corrections.docs[0]["rating"] == "positive"


def test_save_correction_defaults_to_negative_rating(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    save_correction("jack", "What is the refund policy?", "We don't do refunds.", "Wrong, we do 30-day refunds.", "hallucination")

    assert fake_db.corrections.docs[0]["rating"] == "negative"


def test_save_correction_noop_when_db_unavailable(monkeypatch):
    monkeypatch.setattr(app_utils, "get_db", lambda: None)
    # Should not raise even though there's nowhere to write.
    save_correction("jack", "question", "bad answer", "reason", "other")


def test_fetch_relevant_corrections_surfaces_positive_example(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    save_correction(
        "jack", "What is the refund policy for orders?",
        "We offer a 30-day no-questions-asked refund window.",
        "Matches the documented policy exactly.", "other", rating="positive",
    )

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert "PREFERRED EXAMPLES" in context
    assert "30-day no-questions-asked refund window" in context


def test_fetch_relevant_corrections_surfaces_negative_guardrail(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    save_correction(
        "jack", "What is the refund policy for orders?",
        "We never offer refunds under any circumstances.",
        "Contradicts the documented 30-day policy.", "hallucination", rating="negative",
    )

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert "CRITICAL GUARDRAILS" in context
    assert "PREFERRED EXAMPLES" not in context


def test_fetch_relevant_corrections_returns_empty_when_nothing_matches(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    assert fetch_relevant_corrections("jack", "Completely unrelated question about weather") == ""
