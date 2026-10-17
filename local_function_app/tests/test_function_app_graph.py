"""The knowledge-graph step of the ingestion function app: extraction, parsing, storage, and never failing an upload."""
from types import SimpleNamespace
from unittest.mock import Mock

from local_function_app import function_app as fa


class FakeCollection:
    def __init__(self):
        self.rows = []

    def delete_many(self, query):
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in query.items())]

    def insert_many(self, docs):
        self.rows.extend(docs)


class FakeDb(dict):
    def __init__(self):
        super().__init__({fa.GRAPH_EDGES_COLLECTION: FakeCollection()})


def fake_model(*replies):
    """A model whose successive invoke() calls return (or raise) the given replies."""
    model = Mock()
    model.invoke = Mock(side_effect=[r if isinstance(r, Exception) else SimpleNamespace(content=r) for r in replies])
    return model


def test_texts_are_grouped_into_blocks_of_about_the_limit():
    batches = fa._batch_texts(["a" * 400, "b" * 400, "c" * 400], 1000)
    assert len(batches) == 2
    assert batches[0].count("a") == 400 and batches[0].count("b") == 400
    assert fa._batch_texts(["", "   ", None], 1000) == []


def test_one_text_longer_than_the_limit_is_cut_not_dropped():
    batches = fa._batch_texts(["x" * 5000], 1000)
    assert len(batches) == 1 and len(batches[0]) == 1000


def test_a_reply_wrapped_in_a_code_fence_is_still_read():
    reply = '```json\n{"relationships": [{"s": "Tails", "t": "Sonic", "relationship": "friend of"}]}\n```'
    assert fa._parse_relationships(reply) == [{"s": "Tails", "t": "Sonic", "relationship": "friend of"}]


def test_a_reply_with_text_around_the_json_is_still_read():
    reply = 'Here you go: {"relationships": [{"s": "A1", "t": "B1", "relationship": "r"}]} Hope that helps.'
    assert len(fa._parse_relationships(reply)) == 1


def test_a_reply_that_is_not_json_gives_nothing():
    assert fa._parse_relationships("I could not find any relationships.") == []
    assert fa._parse_relationships('{"relationships": "none"}') == []
    assert fa._parse_relationships(None) == []


def test_cleaning_drops_bad_self_loops_long_names_and_duplicates():
    cleaned = fa._clean_relationships([
        {"s": "Tails", "t": "Sonic", "relationship": "friend of"},
        {"s": " tails ", "t": "SONIC", "relationship": "Friend Of"},      # the same edge again
        {"s": "Sonic", "t": "sonic", "relationship": "is"},                # a self-loop
        {"s": "", "t": "Sonic", "relationship": "x"},                      # an end is missing
        {"s": "A" * 200, "t": "Sonic", "relationship": "x"},               # a name that long is not an entity
        "not a dict",
    ])
    assert cleaned == [{"s": "Tails", "t": "Sonic", "relationship": "friend of"}]


def test_extraction_reads_every_block_and_merges_the_results(monkeypatch):
    model = fake_model('{"relationships": [{"s": "A1", "t": "B1", "relationship": "r1"}]}',
                       '{"relationships": [{"s": "C1", "t": "D1", "relationship": "r2"}]}')
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=model))
    monkeypatch.setattr(fa, "GRAPH_BATCH_CHARS", 500)
    edges = fa.extract_relationships(["x" * 400, "y" * 400])
    assert [e["s"] for e in edges] == ["A1", "C1"]
    assert model.invoke.call_count == 2


def test_one_block_failing_does_not_lose_the_others(monkeypatch):
    model = fake_model(RuntimeError("503 UNAVAILABLE"), '{"relationships": [{"s": "C1", "t": "D1", "relationship": "r2"}]}')
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=model))
    monkeypatch.setattr(fa, "GRAPH_BATCH_CHARS", 500)
    assert [e["s"] for e in fa.extract_relationships(["x" * 400, "y" * 400])] == ["C1"]


def test_a_long_file_is_read_in_a_limited_number_of_blocks(monkeypatch):
    model = fake_model(*(['{"relationships": []}'] * 3))
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=model))
    monkeypatch.setattr(fa, "GRAPH_BATCH_CHARS", 100)
    monkeypatch.setattr(fa, "GRAPH_MAX_BATCHES", 3)
    fa.extract_relationships(["z" * 90] * 20)
    assert model.invoke.call_count == 3


def test_the_time_budget_stops_the_extraction_but_keeps_what_was_found(monkeypatch):
    model = fake_model('{"relationships": [{"s": "A1", "t": "B1", "relationship": "r1"}]}', '{"relationships": []}')
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=model))
    monkeypatch.setattr(fa, "GRAPH_BATCH_CHARS", 500)
    clock = iter([0.0, 0.0, 99.0, 99.0])
    monkeypatch.setattr(fa.time, "monotonic", lambda: next(clock))
    edges = fa.extract_relationships(["x" * 400, "y" * 400])
    assert [e["s"] for e in edges] == ["A1"]
    assert model.invoke.call_count == 1


def test_a_model_that_cannot_be_created_gives_nothing_instead_of_an_error(monkeypatch):
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(side_effect=RuntimeError("no key")))
    assert fa.extract_relationships(["some text"]) == []


def test_storing_replaces_the_files_earlier_edges_and_tags_them_with_the_knowledge_base():
    db = FakeDb()
    fa.save_graph_edges(db, "Affiliate_A", "a.pdf", [{"s": "A1", "t": "B1", "relationship": "old"}])
    fa.save_graph_edges(db, "Affiliate_B", "a.pdf", [{"s": "X1", "t": "Y1", "relationship": "other kb"}])
    stored = fa.save_graph_edges(db, "Affiliate_A", "a.pdf", [{"s": "C1", "t": "D1", "relationship": "new"}])
    rows = db[fa.GRAPH_EDGES_COLLECTION].rows
    assert stored == 1
    assert {(r["affiliate"], r["s"], r["relation"]) for r in rows} == {("Affiliate_A", "C1", "new"), ("Affiliate_B", "X1", "other kb")}


def test_indexing_stores_the_edges(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=fake_model('{"relationships": [{"s": "A1", "t": "B1", "relationship": "r"}]}')))
    assert fa.index_graph_for_source(db, "Affiliate_A", "a.pdf", ["some text"]) == 1
    assert db[fa.GRAPH_EDGES_COLLECTION].rows[0]["source"] == "a.pdf"


def test_a_graph_failure_never_fails_the_upload(monkeypatch):
    class BrokenDb(dict):
        def __getitem__(self, name):
            raise RuntimeError("database went away")

    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", Mock(return_value=fake_model('{"relationships": [{"s": "A1", "t": "B1", "relationship": "r"}]}')))
    assert fa.index_graph_for_source(BrokenDb(), "Affiliate_A", "a.pdf", ["some text"]) == 0
    fa.delete_graph_edges(BrokenDb(), "Affiliate_A", "a.pdf")  # also swallowed


def test_the_graph_step_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(fa, "GRAPH_BUILD_ON_INGEST", False)
    model = Mock()
    monkeypatch.setattr(fa, "ChatGoogleGenerativeAI", model)
    assert fa.index_graph_for_source(FakeDb(), "Affiliate_A", "a.pdf", ["some text"]) == 0
    model.assert_not_called()


def test_rolling_back_an_ingestion_removes_the_files_edges():
    db = FakeDb()
    fa.save_graph_edges(db, "Affiliate_A", "a.pdf", [{"s": "A1", "t": "B1", "relationship": "r"}])
    fa.delete_graph_edges(db, "Affiliate_A", "a.pdf")
    assert db[fa.GRAPH_EDGES_COLLECTION].rows == []
