from unittest.mock import Mock

from langchain_core.documents import Document

from backend.services.memory_search import retrieve_relevant_memory_context


def _fake_store(hits):
    store = Mock()
    store.similarity_search_with_score = Mock(return_value=hits)
    return store


def test_returns_empty_when_no_vector_store():
    assert retrieve_relevant_memory_context(None, "jack", "do you remember my presentation?") == ""


def test_returns_empty_when_query_blank():
    store = _fake_store([])
    assert retrieve_relevant_memory_context(store, "jack", "   ") == ""
    store.similarity_search_with_score.assert_not_called()


def test_surfaces_chunks_that_clear_threshold():
    hits = [
        (Document(page_content="Jack felt discouraged after his Principal presentation."), 0.91),
        (Document(page_content="Unrelated chunk about lunch plans."), 0.20),
    ]
    store = _fake_store(hits)

    context = retrieve_relevant_memory_context(store, "jack", "do you remember that thing this morning?")

    assert "Jack felt discouraged after his Principal presentation." in context
    assert "Unrelated chunk about lunch plans." not in context
    assert "RELEVANT PAST CONTEXT" in context


def test_returns_empty_when_nothing_clears_threshold():
    hits = [(Document(page_content="Barely related."), 0.40)]
    store = _fake_store(hits)

    assert retrieve_relevant_memory_context(store, "jack", "something else entirely") == ""


def test_respects_custom_score_threshold():
    hits = [(Document(page_content="Loosely related chunk."), 0.60)]
    store = _fake_store(hits)

    assert retrieve_relevant_memory_context(store, "jack", "question", score_threshold=0.75) == ""
    context = retrieve_relevant_memory_context(store, "jack", "question", score_threshold=0.5)
    assert "Loosely related chunk." in context


def test_search_failure_returns_empty_instead_of_raising():
    store = Mock()
    store.similarity_search_with_score = Mock(side_effect=Exception("atlas down"))

    assert retrieve_relevant_memory_context(store, "jack", "question") == ""
