import random
from types import SimpleNamespace

import pytest

from backend.utils import example_questions as eq
from backend.utils.example_questions import ExampleInputs, build_example_questions, clean_doc_name


def build(inputs, seed=0, limit=6):
    return build_example_questions(inputs, limit=limit, rng=random.Random(seed))


def test_a_brand_new_user_with_an_empty_knowledge_base_still_gets_real_questions():
    qs = build(ExampleInputs(owns_scope=True))
    assert len(qs) == 6
    assert "How do I add a document to my knowledge base?" in qs
    assert "How do I connect my Google Calendar?" in qs
    assert all("sonic" not in q.lower() for q in qs)


def test_questions_about_their_own_documents_come_first():
    qs = build(ExampleInputs(owns_scope=True, doc_names=["Q3 planning notes.pdf", "resume_v2.docx"]))
    assert "What's in Q3 planning notes?" in qs or "What's in resume_v2?" in qs
    assert "How do I add a document to my knowledge base?" not in qs
    assert any(q.startswith("What's in ") for q in qs[:3])


def test_a_cross_document_question_needs_at_least_two_documents():
    assert "Which topics come up across my documents?" not in build(ExampleInputs(doc_names=["a.pdf"]), limit=20)
    assert "Which topics come up across my documents?" in build(ExampleInputs(doc_names=["a.pdf", "b.pdf"]), limit=20)


def test_calendar_questions_only_when_connected_and_a_setup_nudge_only_when_not():
    connected = build(ExampleInputs(calendar_connected=True), limit=20)
    assert "What's on my calendar today?" in connected and "How do I connect my Google Calendar?" not in connected
    missing = build(ExampleInputs(calendar_connected=False), limit=20)
    assert "How do I connect my Google Calendar?" in missing and "What's on my calendar today?" not in missing


def test_a_shared_identity_is_never_nudged_to_connect_a_calendar_it_cannot_have():
    qs = build(ExampleInputs(calendar_available=False), limit=20)
    assert "How do I connect my Google Calendar?" not in qs


def test_a_repo_question_names_the_repo_and_a_missing_repo_gets_a_nudge_instead():
    with_repo = build(ExampleInputs(target_repo="acme/widgets"), limit=20)
    assert "What changed in the last pull request on acme/widgets?" in with_repo
    assert not any("point you at" in q for q in with_repo)
    assert any("point you at" in q for q in build(ExampleInputs(), limit=20))


def test_the_memory_card_appears_only_when_there_is_something_remembered():
    assert "What do you remember about me?" in build(ExampleInputs(has_memories=True), limit=20)
    assert "What do you remember about me?" not in build(ExampleInputs(has_memories=False), limit=20)


def test_no_duplicates_and_the_limit_is_respected():
    inputs = ExampleInputs(owns_scope=True, doc_names=["a.pdf", "A.PDF", "b.pdf"], calendar_connected=True,
                           target_repo="x/y", has_memories=True)
    for seed in range(20):
        qs = build(inputs, seed=seed, limit=6)
        assert len(qs) == len(set(qs)) <= 6


def test_it_varies_between_visits_but_personal_questions_never_lose_to_generic_ones():
    inputs = ExampleInputs(owns_scope=True, doc_names=["notes.pdf"], calendar_connected=True, target_repo="x/y", has_memories=True)
    seen = {tuple(build(inputs, seed=s)) for s in range(30)}
    assert len(seen) > 1
    generic = set(eq._STARTERS)
    for s in range(30):
        qs = build(inputs, seed=s, limit=6)
        personal = [i for i, q in enumerate(qs) if q not in generic]
        starters = [i for i, q in enumerate(qs) if q in generic]
        assert not starters or not personal or max(personal) < min(starters)


@pytest.mark.parametrize("raw,expected", [
    ("Q3 planning notes.pdf", "Q3 planning notes"),
    ("folder/sub/resume_v2.docx", "resume_v2"),
    ("C:\\Users\\jack\\budget.xlsx", "budget"),
    ("  spaced   out  .txt ", "spaced out"),
    ("noextension", "noextension"),
    ("x" * 200 + ".pdf", "x" * 60),
    ("", ""),
])
def test_clean_doc_name(raw, expected):
    assert clean_doc_name(raw) == expected


# ---------------------------------------------------------------------------------------------------------------
# Gathering what the server knows
# ---------------------------------------------------------------------------------------------------------------

class _Files:
    def __init__(self, rows):
        self.rows, self.query = rows, None

    def find(self, query, projection):
        self.query = query
        return self

    def sort(self, *a):
        return self

    def limit(self, n):
        return iter(self.rows[:n])


class _DB(dict):
    pass


def _patch(monkeypatch, rows=(), calendar=False, repo=None, facts=()):
    files = _Files(list(rows))
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: _DB({"fs.files": files}))
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_connection_status", lambda u: {"connected": calendar})
    monkeypatch.setattr("backend.utils.user_settings_utils.get_user_settings_bundle", lambda u: {"target_repo": repo})
    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", lambda u: list(facts))
    return files


DIRECTORY = {"user_1": {"username": "jack", "personal_kb": {"id": "kb_abc"}, "groups": ["Affiliate_A", "kb_abc"]}}


def test_all_scope_looks_only_at_their_own_knowledge_base_for_documents(monkeypatch):
    files = _patch(monkeypatch, rows=[{"filename": "mine.pdf"}])
    out = eq.gather_example_inputs("user_1", "All", DIRECTORY)
    assert files.query["metadata.affiliate"] == {"$in": ["kb_abc"]}
    assert out.doc_names == ["mine.pdf"] and out.owns_scope


def test_a_specific_shared_scope_lists_that_scopes_documents_and_is_not_theirs(monkeypatch):
    files = _patch(monkeypatch, rows=[{"filename": "lore.pdf"}])
    out = eq.gather_example_inputs("user_1", "Affiliate_A", DIRECTORY)
    assert files.query["metadata.affiliate"] == {"$in": ["Affiliate_A"]}
    assert not out.owns_scope


def test_their_personal_kb_selected_is_their_own_scope(monkeypatch):
    _patch(monkeypatch)
    assert eq.gather_example_inputs("user_1", "kb_abc", DIRECTORY).owns_scope


def test_integrations_and_repo_are_read_from_the_users_own_settings(monkeypatch):
    _patch(monkeypatch, calendar=True, repo="acme/widgets")
    out = eq.gather_example_inputs("user_1", "All", DIRECTORY)
    assert out.calendar_connected and out.target_repo == "acme/widgets"


def test_what_the_safety_layer_wrote_never_makes_the_memory_card_appear(monkeypatch):
    only_safety = [SimpleNamespace(active=True, source="safety_support")]
    _patch(monkeypatch, facts=only_safety)
    assert not eq.gather_example_inputs("user_1", "All", DIRECTORY).has_memories
    _patch(monkeypatch, facts=only_safety + [SimpleNamespace(active=True, source="explicit")])
    assert eq.gather_example_inputs("user_1", "All", DIRECTORY).has_memories
    _patch(monkeypatch, facts=[SimpleNamespace(active=False, source="explicit")])
    assert not eq.gather_example_inputs("user_1", "All", DIRECTORY).has_memories


def test_a_shared_identity_gets_no_calendar_memory_or_repo_questions(monkeypatch):
    _patch(monkeypatch, calendar=True, repo="acme/widgets", facts=[SimpleNamespace(active=True, source="explicit")])
    directory = {"guest_bty": {"username": "guest_bty", "groups": ["Affiliate_A"]}}
    out = eq.gather_example_inputs("guest_bty", "All", directory)
    assert not out.calendar_available and not out.calendar_connected and out.target_repo is None and not out.has_memories
    assert not any("calendar" in q.lower() for q in build(out, limit=20))


def test_a_failing_source_means_fewer_personal_questions_never_an_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_connection_status", boom)
    monkeypatch.setattr("backend.utils.user_settings_utils.get_user_settings_bundle", boom)
    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", boom)
    out = eq.gather_example_inputs("user_1", "All", DIRECTORY)
    assert out.doc_names == [] and not out.calendar_connected and out.target_repo is None and not out.has_memories
    assert len(build(out)) == 6  # the screen is never empty


def test_a_user_with_no_database_still_gets_questions(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    assert eq.gather_example_inputs("user_1", "All", DIRECTORY).doc_names == []
