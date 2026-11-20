from dotenv import load_dotenv
load_dotenv()

from unittest.mock import Mock

from backend.services import search


def test_mongo_vector_search_preserves_gridfs_and_embedded_image_fields(monkeypatch):
    """Regression guard: MongoDBAtlasVectorSearch flattens Document.metadata into top-level
    Mongo fields, so the $project stage must explicitly whitelist any new metadata field or it
    silently never reaches Document.metadata — this is exactly the bug that would make the KB
    image-rendering feature no-op at retrieval time even though ingestion worked fine."""
    fake_collection = Mock()
    fake_collection.aggregate = Mock(return_value=[
        {
            "page_content": "A red square on a white background.",
            "source": "logo.png",
            "page": 0,
            "affiliate": "Affiliate_D",
            "priority": True,
            "gridfs_id": "abc123",
            "content_type": "image/png",
            "doc_type": "standalone_image",
            "embedded_images": [],
            "score": 0.92,
        },
        {
            "page_content": "Page text with a chart on it.",
            "source": "report.pdf",
            "page": 3,
            "affiliate": "Affiliate_D",
            "priority": False,
            "embedded_images": [{"gridfs_id": "img1", "filename": "chart.png"}],
            "score": 0.81,
        },
    ])

    monkeypatch.setattr(search, "_get_mongo_collection", Mock(return_value=fake_collection))
    monkeypatch.setattr(search, "_embeddings", Mock(embed_query=Mock(return_value=[0.1, 0.2, 0.3])))

    docs = search._mongo_vector_search("what does the logo look like?", affiliate_scope=["Affiliate_D"])

    assert len(docs) == 2
    assert docs[0].metadata["doc_type"] == "standalone_image"
    assert docs[0].metadata["gridfs_id"] == "abc123"
    assert docs[0].metadata["content_type"] == "image/png"
    assert docs[1].metadata["embedded_images"] == [{"gridfs_id": "img1", "filename": "chart.png"}]


def test_mongo_vector_search_defaults_embedded_images_to_empty_list(monkeypatch):
    fake_collection = Mock()
    fake_collection.aggregate = Mock(return_value=[
        {"page_content": "plain text", "source": "doc.pdf", "page": 0, "score": 0.5}
    ])
    monkeypatch.setattr(search, "_get_mongo_collection", Mock(return_value=fake_collection))
    monkeypatch.setattr(search, "_embeddings", Mock(embed_query=Mock(return_value=[0.1, 0.2, 0.3])))

    docs = search._mongo_vector_search("a question", affiliate_scope=[])

    assert docs[0].metadata["embedded_images"] == []
    assert docs[0].metadata.get("gridfs_id") is None


# ---- which way a library question is searched (measured by backend/evals/rag_eval.py, 2026-11-19) -------------------------------------------------------

import pytest


@pytest.fixture()
def strategy_env(monkeypatch):
    monkeypatch.delenv("SEARCH_STRATEGY", raising=False)
    return monkeypatch


QUESTIONS = ["latest release date", "What did I update in the intake service?", "compare sonic vs shadow", "tell me about sonic", "when was it", "what is the capital of Australia",
             "Who is Sonic and how does his relationship with Shadow progress the narrative in Sonic Adventure 2?"]


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_question_is_searched_as_hybrid_by_default(strategy_env, question):
    assert search._detect_routing_strategy(question) == "hybrid"


@pytest.mark.parametrize("setting", ["vector", "lexical", "hybrid", " Vector ", "LEXICAL"])
def test_a_strategy_can_be_forced_whatever_the_question(strategy_env, setting):
    strategy_env.setenv("SEARCH_STRATEGY", setting)
    assert all(search._detect_routing_strategy(q) == setting.strip().lower() for q in QUESTIONS)


def test_a_setting_nobody_recognises_falls_back_to_the_default_rather_than_to_no_search(strategy_env):
    strategy_env.setenv("SEARCH_STRATEGY", "semantic-ish")
    assert search._detect_routing_strategy("anything") == search.DEFAULT_STRATEGY == "hybrid"
    strategy_env.setenv("SEARCH_STRATEGY", "")
    assert search._detect_routing_strategy("anything") == "hybrid"


def test_auto_brings_back_the_keyword_router_and_it_reads_whole_words_only(strategy_env):
    strategy_env.setenv("SEARCH_STRATEGY", "auto")
    route = search._detect_routing_strategy
    assert route("latest release date") == "lexical" and route("When did it ship?") == "lexical" and route("the current plan") == "lexical"
    assert route("compare sonic vs shadow") == "hybrid" and route("one two three four five six seven eight nine ten eleven twelve thirteen") == "hybrid"
    assert route("tell me about sonic") == "vector"
    # a word that merely CONTAINS a marker is not the marker: "update" and "validate" have "date" in them, "forecast" and "blast" have "last", "concurrent" has "current", "devs" and "TVs" have "vs"
    for question in ("What did I update?", "How did I validate the upgrade", "blast radius of the forecast", "concurrent jobs", "yearly goals", "whenever you like", "what did the devs say about the TVs"):
        assert route(question) == "vector", question


def test_the_retriever_searches_the_way_the_setting_says(strategy_env):
    calls = []
    for name in ("vector", "lexical", "hybrid"):
        strategy_env.setattr(search, f"_retrieve_{name}", lambda query, flt, k, name=name: calls.append(name) or [])
    search.get_secure_retriever(None, ["lib"], "latest release date", top_k=3).invoke("latest release date")
    assert calls == ["hybrid"], "no setting: hybrid, even for a question full of date words"
    strategy_env.setenv("SEARCH_STRATEGY", "vector")
    search.get_secure_retriever(None, ["lib"], "anything", top_k=3).invoke("anything")
    assert calls == ["hybrid", "vector"]
