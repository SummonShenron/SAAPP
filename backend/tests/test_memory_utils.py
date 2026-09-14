import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.utils import memory_utils


def _fake_embed(text):
    """Deterministic stand-in for embedding_utils.embed_text: normalizes case/whitespace so
    near-identical text maps to the identical vector (similarity 1.0), and hashes into a large
    number of buckets so distinct phrases land in different buckets (similarity 0.0) with
    overwhelming probability for the handful of short strings used in these tests."""
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    bucket = hash(normalized) % 9973
    vector = [0.0] * 9973
    vector[bucket] = 1.0
    return vector


@pytest.fixture(autouse=True)
def isolated_memory_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir, forces get_db() to None so every test
    exercises the local-file fallback path deterministically, fakes embeddings so no network
    call is made, and defaults the fact-conflict LLM judge to a harmless response that only
    matters for tests that actually trigger it."""
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    monkeypatch.setattr(memory_utils, "embed_text", _fake_embed)
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "duplicate"})))
    )
    yield


def test_save_and_load_user_fact():
    saved = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == saved.id
    assert facts[0].fact == "Prefers dark mode UI."
    assert facts[0].category == "preference"
    assert facts[0].embedding is not None


def test_save_user_fact_updates_existing_fact_in_place():
    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI", category="preference")
    updated = memory_utils.save_user_fact("jack", "prefers dark mode ui", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id == updated.id


def test_save_user_fact_contradicting_statement_supersedes_instead_of_duplicating(monkeypatch):
    # Same normalized bucket as a stand-in for "semantically similar, but contradicting" —
    # the LLM judge (mocked to "duplicate" by the autouse fixture) is what actually decides
    # to update in place rather than append.
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "supersede"})))
    )
    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "prefers dark mode ui.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id
    assert facts[0].fact == "prefers dark mode ui."


def test_save_user_fact_unrelated_text_creates_new_entry():
    # Different phrases hash into different buckets -> similarity 0.0 -> below threshold ->
    # the LLM judge is never even consulted, both are kept side by side.
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Prefers concise answers.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 2


def test_save_user_fact_llm_judges_distinct_keeps_both(monkeypatch):
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: [1.0, 0.0])  # force a "similar" match
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "distinct"})))
    )
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Prefers large font sizes.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 2


def test_save_user_fact_falls_back_to_substring_match_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: None)
    llm_mock = Mock()
    monkeypatch.setattr(memory_utils.lite_llm, "invoke", llm_mock)

    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI", category="preference")
    updated = memory_utils.save_user_fact("jack", "prefers dark mode ui", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id == updated.id
    llm_mock.assert_not_called()  # no embedding means no similarity match, so no judge call either


def test_save_user_fact_invalid_category_defaults_to_preference():
    saved = memory_utils.save_user_fact("jack", "Likes concise answers.", category="not_a_real_category")
    assert saved.category == "preference"


def test_load_user_facts_filters_by_category():
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Name is Jack.", category="identity")

    identity_facts = memory_utils.load_user_facts("jack", category="identity")
    assert len(identity_facts) == 1
    assert identity_facts[0].fact == "Name is Jack."


def test_delete_user_fact():
    saved = memory_utils.save_user_fact("jack", "Likes expressive UI.", category="preference")

    assert memory_utils.delete_user_fact("jack", saved.id) is True
    assert memory_utils.load_user_facts("jack") == []
    assert memory_utils.delete_user_fact("jack", "not-a-real-id") is False


def test_delete_all_user_facts():
    memory_utils.save_user_fact("jack", "Fact one.", category="preference")
    memory_utils.save_user_fact("jack", "Fact two.", category="trait")

    memory_utils.delete_all_user_facts("jack")
    assert memory_utils.load_user_facts("jack") == []


def test_fetch_relevant_user_facts_returns_empty_when_no_facts():
    assert memory_utils.fetch_relevant_user_facts("jack", "what do you know about me?") == ""


def test_fetch_relevant_user_facts_includes_saved_fact():
    memory_utils.save_user_fact("jack", "Prefers expressive UI.", category="preference")

    context = memory_utils.fetch_relevant_user_facts("jack", "what should the UI look like?")
    assert "Prefers expressive UI." in context
    assert "KNOWN USER CONTEXT" in context


def test_fetch_relevant_user_facts_always_includes_identity_regardless_of_question(monkeypatch):
    # Give the question an embedding far away from everything, so relevance ranking alone
    # would never surface the identity fact — it must be force-included regardless.
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: {
        "Name is Jack Harper.": [1.0, 0.0, 0.0],
        "Prefers dark mode UI.": [0.0, 1.0, 0.0],
        "something totally unrelated": [0.0, 0.0, 1.0],
    }.get(text.strip(), [0.5, 0.5, 0.5]))

    memory_utils.save_user_fact("jack", "Name is Jack Harper.", category="identity")
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    context = memory_utils.fetch_relevant_user_facts("jack", "something totally unrelated", limit=1)
    assert "Name is Jack Harper." in context


def test_fetch_relevant_user_facts_ranks_by_relevance_to_question(monkeypatch):
    vectors = {
        "Loves discussing Python programming.": [1.0, 0.0],
        "Enjoys hiking on weekends.": [0.0, 1.0],
        "tell me about python best practices": [1.0, 0.0],
    }
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: vectors.get(text.strip(), [0.5, 0.5]))

    memory_utils.save_user_fact("jack", "Enjoys hiking on weekends.", category="trait")
    memory_utils.save_user_fact("jack", "Loves discussing Python programming.", category="trait")

    context = memory_utils.fetch_relevant_user_facts("jack", "tell me about python best practices", limit=1)
    assert "Loves discussing Python programming." in context
    assert "Enjoys hiking on weekends." not in context


def test_fetch_relevant_user_facts_falls_back_to_recency_when_question_empty():
    memory_utils.save_user_fact("jack", "Older fact.", category="trait")
    memory_utils.save_user_fact("jack", "Newer fact.", category="trait")

    context = memory_utils.fetch_relevant_user_facts("jack", "", limit=1)
    assert "Newer fact." in context
    assert "Older fact." not in context


def test_facts_are_scoped_per_username():
    memory_utils.save_user_fact("jack", "Jack's fact.", category="preference")
    memory_utils.save_user_fact("alice", "Alice's fact.", category="preference")

    assert [f.fact for f in memory_utils.load_user_facts("jack")] == ["Jack's fact."]
    assert [f.fact for f in memory_utils.load_user_facts("alice")] == ["Alice's fact."]
