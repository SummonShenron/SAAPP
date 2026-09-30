from unittest.mock import Mock

from langchain_core.documents import Document

from backend.services.memory_search import (
    retrieve_relevant_memory_context,
    retrieve_user_memory,
    flag_stale_chunks_for_superseded_fact,
)


def _fake_store(hits):
    store = Mock()
    store.similarity_search_with_score = Mock(return_value=hits)
    store._similarity_search_with_score = Mock(return_value=hits)
    return store


def _fake_stale_store(hits):
    """A store shaped for flag_stale_chunks_for_superseded_fact: _similarity_search_with_score
    returns (Document, score) pairs whose Document.id is set (as the real library's own
    _similarity_search_with_score does — see vectorstores.py:872), plus a raw .collection with
    a mockable update_many."""
    store = Mock()
    store._similarity_search_with_score = Mock(return_value=hits)
    store.collection = Mock()
    store.collection.update_many = Mock()
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


def test_precomputed_embedding_uses_vector_search_and_skips_reembedding():
    """A caller that already embedded this turn's question elsewhere (fetch_relevant_user_facts,
    given the same question string) can pass that vector straight through instead of this call
    re-embedding the identical text again via the vector store's own internal embeddings
    client — a real, avoidable second network round trip otherwise."""
    hits = [(Document(page_content="Jack felt discouraged after his Principal presentation."), 0.91)]
    store = _fake_store(hits)

    context = retrieve_relevant_memory_context(
        store, "jack", "do you remember that thing this morning?", precomputed_embedding=[0.1, 0.2, 0.3]
    )

    assert "Jack felt discouraged after his Principal presentation." in context
    store._similarity_search_with_score.assert_called_once_with(
        [0.1, 0.2, 0.3], k=3, pre_filter={"username": "jack", "stale": {"$ne": True}}
    )
    store.similarity_search_with_score.assert_not_called()


# ---------------------------------------------------------------------------
# retrieve_user_memory
# ---------------------------------------------------------------------------

def test_retrieve_user_memory_returns_empty_when_no_vector_store():
    assert retrieve_user_memory(None, "jack", "what do I like?") == []


def test_retrieve_user_memory_returns_empty_when_query_blank():
    store = Mock()
    assert retrieve_user_memory(store, "jack", "   ") == []
    store.similarity_search.assert_not_called()


def test_retrieve_user_memory_excludes_stale_chunks_via_prefilter():
    store = Mock()
    docs = [Document(page_content="Jack works at Acme now.")]
    store.similarity_search = Mock(return_value=docs)

    result = retrieve_user_memory(store, "jack", "where does Jack work?", top_k=4)

    assert result == docs
    store.similarity_search.assert_called_once_with(
        "where does Jack work?", k=4, pre_filter={"username": "jack", "stale": {"$ne": True}}
    )


def test_retrieve_user_memory_search_failure_returns_empty():
    store = Mock()
    store.similarity_search = Mock(side_effect=Exception("atlas down"))

    assert retrieve_user_memory(store, "jack", "question") == []


# ---------------------------------------------------------------------------
# flag_stale_chunks_for_superseded_fact
# ---------------------------------------------------------------------------

def test_flag_stale_chunks_returns_zero_without_vector_store():
    assert flag_stale_chunks_for_superseded_fact(None, "jack", [0.1, 0.2]) == 0


def test_flag_stale_chunks_returns_zero_without_embedding():
    store = _fake_stale_store([])
    assert flag_stale_chunks_for_superseded_fact(store, "jack", None) == 0
    store._similarity_search_with_score.assert_not_called()


def test_flag_stale_chunks_tags_matches_above_threshold():
    hits = [
        (Document(page_content="Jack works at Principal.", id="chunk-1"), 0.93),
        (Document(page_content="Jack likes hiking.", id="chunk-2"), 0.10),
    ]
    store = _fake_stale_store(hits)

    flagged = flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0])

    assert flagged == 1
    store.collection.update_many.assert_called_once()
    call_args = store.collection.update_many.call_args
    assert call_args[0][0] == {"_id": {"$in": ["chunk-1"]}}
    assert call_args[0][1]["$set"]["stale"] is True
    assert call_args[0][1]["$set"]["stale_reason"] == "fact_superseded"


def test_flag_stale_chunks_ignores_matches_below_threshold():
    hits = [(Document(page_content="Jack likes hiking.", id="chunk-2"), 0.40)]
    store = _fake_stale_store(hits)

    flagged = flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0])

    assert flagged == 0
    store.collection.update_many.assert_not_called()


def test_flag_stale_chunks_search_failure_returns_zero():
    store = Mock()
    store._similarity_search_with_score = Mock(side_effect=Exception("atlas down"))

    assert flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0]) == 0


def test_flag_stale_chunks_update_failure_returns_zero():
    hits = [(Document(page_content="Jack works at Principal.", id="chunk-1"), 0.93)]
    store = _fake_stale_store(hits)
    store.collection.update_many = Mock(side_effect=Exception("write failed"))

    assert flag_stale_chunks_for_superseded_fact(store, "jack", [1.0, 0.0]) == 0
