import pytest

from backend.utils import memory_utils


@pytest.fixture(autouse=True)
def isolated_memory_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir and forces get_db() to None
    so every test exercises the local-file fallback path deterministically."""
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    yield


def test_save_and_load_user_fact():
    saved = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == saved.id
    assert facts[0].fact == "Prefers dark mode UI."
    assert facts[0].category == "preference"


def test_save_user_fact_updates_existing_fact_in_place():
    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI", category="preference")
    updated = memory_utils.save_user_fact("jack", "prefers dark mode ui", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id == updated.id


def test_save_user_fact_unrelated_text_creates_new_entry():
    # The dedupe check is a naive same-category substring match, not semantic —
    # unrelated facts in the same category are stored side by side, by design for v1.
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Prefers concise answers.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 2


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


def test_facts_are_scoped_per_username():
    memory_utils.save_user_fact("jack", "Jack's fact.", category="preference")
    memory_utils.save_user_fact("alice", "Alice's fact.", category="preference")

    assert [f.fact for f in memory_utils.load_user_facts("jack")] == ["Jack's fact."]
    assert [f.fact for f in memory_utils.load_user_facts("alice")] == ["Alice's fact."]
