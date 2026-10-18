"""The person rewrites a saved note on the Memory page (memory_utils.edit_user_fact, PUT /api/memory/{id})."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.utils import memory_utils

VECTORS = {
    "Works at Principal.": (1.0, 0.1, 0.0),
    "Works at Acme.": (0.9, 0.2, 0.1),
    "Is working on the PAG UI redesign at work.": (0.8, 0.6, 0.0),
    "Has a dog named Max.": (0.0, 0.2, 1.0),
    "Has a dog named Rex.": (0.0, 0.25, 1.0),
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    memory_utils.clear_facts_cache()
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: list(VECTORS[text.strip()]) if text and text.strip() in VECTORS else None)
    monkeypatch.setattr(memory_utils, "flag_stale_chunks_for_superseded_fact", Mock(return_value=0))

    def invoke(prompt):
        if "NOTE A:" in prompt:                             # the strict second look at a pair the first look picked
            return SimpleNamespace(content=json.dumps({"same": True}))
        if "SAVED NOTES" in prompt:                         # the link judgment: bind a note to the employer note
            lines = [line for line in prompt.splitlines() if line[:2] in ("1:", "2:", "3:")]
            picks = [int(line[0]) for line in lines if "Works at" in line]
            return SimpleNamespace(content=json.dumps({"links": [{"n": n, "thing": "the job"} for n in picks]}))
        return SimpleNamespace(content=json.dumps({"action": "distinct"}))

    monkeypatch.setattr(memory_utils.lite_llm, "invoke", Mock(side_effect=invoke))
    yield
    memory_utils.clear_facts_cache()


def _saved(text, category="career", source="inferred"):
    return memory_utils.save_user_fact("jack", text, category=category, source=source, confidence=0.4)


def test_the_new_wording_replaces_the_old_is_embedded_afresh_and_marked_as_theirs(store):
    dog = _saved("Has a dog named Max.", category="relationship")
    edited = memory_utils.edit_user_fact("jack", dog.id, "Has a dog named Rex.")
    assert edited.fact == "Has a dog named Rex." and edited.embedding == list(VECTORS["Has a dog named Rex."])
    assert edited.source == memory_utils.USER_EDIT_SOURCE and edited.confidence == 1.0
    memory_utils.clear_facts_cache()
    stored = memory_utils.load_user_facts("jack")
    assert [f.fact for f in stored] == ["Has a dog named Rex."] and stored[0].source == "user_edit"


def test_extra_spaces_are_tidied_and_an_empty_or_overlong_note_is_refused_with_a_message(store):
    dog = _saved("Has a dog named Max.", category="relationship")
    assert memory_utils.edit_user_fact("jack", dog.id, "  Has a   dog named Rex. ").fact == "Has a dog named Rex."
    with pytest.raises(memory_utils.FactEditError, match="can't be empty"):
        memory_utils.edit_user_fact("jack", dog.id, "   ")
    with pytest.raises(memory_utils.FactEditError, match="under"):
        memory_utils.edit_user_fact("jack", dog.id, "x" * (memory_utils.MAX_FACT_CHARS + 1))
    with pytest.raises(memory_utils.FactEditError, match="category"):
        memory_utils.edit_user_fact("jack", dog.id, "Has a dog named Max.", category="nonsense")
    assert [f.fact for f in memory_utils.load_user_facts("jack")] == ["Has a dog named Rex."]   # the refused edits changed nothing


def test_an_unknown_note_is_none_and_an_unchanged_note_costs_nothing(store):
    dog = _saved("Has a dog named Max.", category="relationship")
    assert memory_utils.edit_user_fact("jack", "no-such-id", "Has a dog named Rex.") is None
    calls = memory_utils.lite_llm.invoke.call_count
    same = memory_utils.edit_user_fact("jack", dog.id, "Has a dog named Max.")
    assert same.source == "inferred" and same.confidence == 0.4          # nothing was rewritten, so nothing was claimed
    assert memory_utils.lite_llm.invoke.call_count == calls


def test_text_that_cannot_be_embedded_is_not_saved_rather_than_kept_on_the_old_meaning(store):
    dog = _saved("Has a dog named Max.", category="relationship")
    with pytest.raises(memory_utils.FactEditError, match="try again"):
        memory_utils.edit_user_fact("jack", dog.id, "A sentence with no vector.")
    memory_utils.clear_facts_cache()
    assert memory_utils.load_user_facts("jack")[0].fact == "Has a dog named Max."


def test_links_are_cleared_both_ways_and_judged_again_for_the_new_wording(store):
    employer = _saved("Works at Principal.")
    project = _saved("Is working on the PAG UI redesign at work.", category="project")
    assert project.related == [employer.id]
    memory_utils.edit_user_fact("jack", employer.id, "Works at Acme.")
    facts = {f.id: f for f in memory_utils.load_user_facts("jack")}
    assert facts[employer.id].fact == "Works at Acme."
    assert facts[project.id].related == [] and facts[employer.id].related == []      # the project note is not about Acme, and the judge (here) names only job notes


def test_a_remembered_support_contact_stays_one_after_its_wording_is_fixed(store):
    contact = memory_utils.save_user_fact("jack", "Has a dog named Max.", category="relationship", source=memory_utils.SUPPORT_SOURCE)
    edited = memory_utils.edit_user_fact("jack", contact.id, "Has a dog named Rex.")
    assert edited.source == memory_utils.SUPPORT_SOURCE


def test_changing_the_category_alone_keeps_the_wording_and_the_links_and_makes_no_model_call(store):
    employer = _saved("Works at Principal.")
    project = _saved("Is working on the PAG UI redesign at work.", category="project")
    calls = memory_utils.lite_llm.invoke.call_count
    memory_utils.edit_user_fact("jack", project.id, "Is working on the PAG UI redesign at work.", category="goal")
    facts = {f.id: f for f in memory_utils.load_user_facts("jack")}
    assert facts[project.id].category == "goal" and facts[project.id].goal_status == "active"
    assert facts[project.id].related == [employer.id]
    assert memory_utils.lite_llm.invoke.call_count == calls


def test_a_model_restating_an_edited_note_does_not_replace_the_persons_wording(store, monkeypatch):
    dog = _saved("Has a dog named Max.", category="relationship")
    memory_utils.edit_user_fact("jack", dog.id, "Has a dog named Rex.")
    monkeypatch.setattr(memory_utils.lite_llm, "invoke", Mock(return_value=SimpleNamespace(content=json.dumps({"action": "duplicate"}))))
    memory_utils.save_user_fact("jack", "Has a dog named Rex.", category="trait", source="inferred")
    kept = memory_utils.load_user_facts("jack")[0]
    assert kept.fact == "Has a dog named Rex." and kept.category == "relationship" and kept.source == "user_edit"


def test_what_the_person_wrote_is_pruned_after_what_they_stated_and_what_a_model_inferred(store, monkeypatch):
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 2)
    monkeypatch.setattr(memory_utils, "_archive_dropped_facts", Mock())
    s = memory_utils.source_strength
    assert s("user_edit") > s("explicit") > s("pattern") > s("inferred")
    assert memory_utils.USER_EDIT_SOURCE not in memory_utils.PROTECTED_SOURCES        # many edits must never push the store past its cap


def test_the_edit_endpoint_needs_a_signed_in_user_returns_no_embedding_and_maps_the_errors():
    source = open("app.py", encoding="utf-8").read()
    start = source.index('@app.put("/api/memory/{fact_id}")')
    endpoint = source[start:source.index('@app.delete("/api/memory/{fact_id}/links/{other_id}")')]
    assert "Depends(get_current_user)" in endpoint and "edit_user_fact" in endpoint and "asyncio.to_thread" in endpoint
    assert "except FactEditError" in endpoint and "status_code=400" in endpoint and "status_code=404" in endpoint
    assert "fact_for_state(edited)" in endpoint and "linked_fact_summaries(edited, by_id)" in endpoint and "embedding" not in endpoint.replace("fact_for_state", "")
