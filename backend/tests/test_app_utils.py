import re

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils
from backend.utils.app_utils import collect_kb_images, _serialize_messages, save_correction, fetch_relevant_corrections


def _fake_embed(text):
    """Deterministic stand-in for embedding_utils.embed_text (same convention as
    test_memory_utils.py's own _fake_embed): near-identical text (normalized case/whitespace)
    maps to the identical vector; distinct phrases land in different buckets with overwhelming
    probability for the handful of short strings used in these tests."""
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    bucket = hash(normalized) % 9973
    vector = [0.0] * 9973
    vector[bucket] = 1.0
    return vector


def _similar_embed(_text):
    """A fixed, shared vector — stands in for two genuinely different phrasings that are
    nonetheless semantically similar (what _fake_embed's own hash-bucket scheme can't express,
    since it only maps near-identical text to the same vector)."""
    vector = [0.0] * 9973
    vector[42] = 1.0
    return vector


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
    monkeypatch.setattr(app_utils, "embed_text", _fake_embed)

    save_correction("jack", "What is the refund policy?", "We don't do refunds.", "Wrong, we do 30-day refunds.", "hallucination", rating="positive")

    assert len(fake_db.corrections.docs) == 1
    assert fake_db.corrections.docs[0]["rating"] == "positive"


def test_save_correction_defaults_to_negative_rating(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _fake_embed)

    save_correction("jack", "What is the refund policy?", "We don't do refunds.", "Wrong, we do 30-day refunds.", "hallucination")

    assert fake_db.corrections.docs[0]["rating"] == "negative"


def test_save_correction_noop_when_db_unavailable(monkeypatch):
    monkeypatch.setattr(app_utils, "get_db", lambda: None)
    # Should not raise even though there's nowhere to write.
    save_correction("jack", "question", "bad answer", "reason", "other")


def test_save_correction_stores_an_embedding(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _fake_embed)

    save_correction("jack", "What is the refund policy?", "We don't do refunds.", "Wrong.", "hallucination")

    assert fake_db.corrections.docs[0]["embedding"] == _fake_embed("What is the refund policy?")


def test_fetch_relevant_corrections_surfaces_positive_example_for_differently_worded_question(monkeypatch):
    """The actual bug this upgrade fixes: the old literal-30-char-prefix matcher could never
    surface this, since the two questions below share no common prefix at all despite asking the
    same thing. _similar_embed stands in for "semantically identical, worded completely
    differently" — real embeddings would score these as similar; the hash-bucket _fake_embed
    scheme used elsewhere in this file can't express that, only near-identical text can."""
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _similar_embed)

    save_correction(
        "jack", "Can I send back something I bought a month ago?",
        "We offer a 30-day no-questions-asked refund window.",
        "Matches the documented policy exactly.", "other", rating="positive",
    )

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert "PREFERRED EXAMPLES" in context
    assert "30-day no-questions-asked refund window" in context


def test_fetch_relevant_corrections_surfaces_negative_guardrail_for_differently_worded_question(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _similar_embed)

    save_correction(
        "jack", "Can I send back something I bought a month ago?",
        "We never offer refunds under any circumstances.",
        "Contradicts the documented 30-day policy.", "hallucination", rating="negative",
    )

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert "CRITICAL GUARDRAILS" in context
    assert "PREFERRED EXAMPLES" not in context


def test_fetch_relevant_corrections_ignores_dissimilar_corrections(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _fake_embed)

    save_correction(
        "jack", "What's the weather like today?",
        "It's sunny.", "Not relevant to refunds.", "other", rating="positive",
    )

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert context == ""


def test_fetch_relevant_corrections_skips_corrections_saved_before_the_embedding_upgrade(monkeypatch):
    """A correction saved before this change has no "embedding" field at all — it must be
    skipped cleanly during semantic matching, not crash the whole lookup."""
    fake_db = _FakeDB()
    fake_db.corrections.docs.append({
        "id": "legacy-1", "username": "jack", "user_prompt": "What is the refund policy for orders?",
        "bad_response": "We never offer refunds.", "reason": "Wrong.", "tag": "hallucination",
        "rating": "negative", "created_at": "2025-01-01T00:00:00+00:00",
        # No "embedding" key — simulates a pre-upgrade document.
    })
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _similar_embed)

    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert context == ""


def test_fetch_relevant_corrections_falls_back_to_substring_match_when_embeddings_unavailable(monkeypatch):
    """Mirrors save_user_fact's own embeddings-unavailable fallback (memory_utils.py) — if
    embed_text returns None, the original literal-prefix matching still runs rather than
    surfacing nothing at all."""
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", lambda text: None)

    save_correction(
        "jack", "What is the refund policy for orders?",
        "We offer a 30-day no-questions-asked refund window.",
        "Matches the documented policy exactly.", "other", rating="positive",
    )

    # Shares the same ~30-character prefix as the saved prompt — the substring fallback's own
    # matching contract, unchanged from before this upgrade.
    context = fetch_relevant_corrections("jack", "What is the refund policy for orders over $50?")

    assert "PREFERRED EXAMPLES" in context


def test_fetch_relevant_corrections_returns_empty_when_nothing_matches(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _fake_embed)

    assert fetch_relevant_corrections("jack", "Completely unrelated question about weather") == ""


# ---- a bare reply is not looked up, and a correction made on one is not offered (2026-11-09) ------------------------------------------------------------------------------------------------

def _same_vector(text):
    return [1.0, 0.0]          # every text means the same thing: only the rule about bare replies can keep a correction from matching


def test_a_bare_reply_is_not_matched_against_past_corrections_even_when_the_embeddings_agree(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _same_vector)
    save_correction("jack", "Can I send back something I bought a month ago?", "We never offer refunds.", "Contradicts the policy.", "hallucination", rating="negative")

    assert "CRITICAL GUARDRAILS" in fetch_relevant_corrections("jack", "What is the refund policy for orders?")          # the control: a real question matches
    for bare in ("yes", "yes please", "ok", "  go on  "):
        assert fetch_relevant_corrections("jack", bare) == "", bare


def test_a_correction_made_on_a_bare_reply_is_never_offered_for_a_real_question(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "embed_text", _same_vector)
    save_correction("jack", "yes", "Sure, here is some unrelated earlier reply.", "Not what I wanted.", "other", rating="negative")
    save_correction("jack", "ok sounds good", "Another earlier reply.", "", "other", rating="positive")

    context = fetch_relevant_corrections("jack", "Add the new counters to the Sonic Health page")
    assert "unrelated earlier reply" not in context                                      # the one-word prompt's correction is skipped
    assert "PREFERRED EXAMPLES" in context and "Another earlier reply" in context          # a three-word prompt is still a prompt


def test_the_word_floor_is_three_and_counts_words_not_characters():
    assert app_utils.MIN_WORDS_FOR_CORRECTIONS == 3
    assert app_utils._too_bare("yes") and app_utils._too_bare("go on") and app_utils._too_bare("") and app_utils._too_bare(None)
    assert not app_utils._too_bare("fix it please") and not app_utils._too_bare("a b c")
