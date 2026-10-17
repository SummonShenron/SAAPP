"""The knowledge graph retrieval reads (backend/state/graph_db.py): per knowledge base, in MongoDB, never across tenants."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.state import graph_db


class FakeEdges:
    """Just enough of a MongoDB collection for the graph: find by a field, delete_many by fields."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.finds = 0

    def find(self, query, projection=None):
        self.finds += 1
        return [dict(r) for r in self.rows if all(r.get(k) == v for k, v in query.items())]

    def delete_many(self, query):
        keep = [r for r in self.rows if not all(r.get(k) == v for k, v in query.items())]
        deleted = len(self.rows) - len(keep)
        self.rows = keep
        return SimpleNamespace(deleted_count=deleted)


class FakeDb(dict):
    def __init__(self, edges):
        super().__init__({graph_db.GRAPH_EDGES_COLLECTION: edges})


def edge(affiliate, source, s, t, relation="related to"):
    return {"affiliate": affiliate, "source": source, "s": s, "t": t, "relation": relation}


@pytest.fixture(autouse=True)
def fresh_cache():
    graph_db.invalidate_scoped_graph()
    yield
    graph_db.invalidate_scoped_graph()


def test_a_question_gets_the_relationships_of_the_entities_it_names():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic", "best friend of"), edge("Affiliate_A", "a.pdf", "Tails", "Tornado", "pilots")]))
    facts = graph_db.scoped_context("Who is Tails?", ["Affiliate_A"], db=db)
    assert "Tails --(best friend of)--> Sonic" in facts
    assert "Tails --(pilots)--> Tornado" in facts


def test_one_knowledge_bases_relationships_never_reach_another():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "Goku", "Sonic", "met"), edge("Affiliate_B", "b.pdf", "Goku", "Vegeta", "rival of")]))
    only_b = graph_db.scoped_context("Tell me about Goku", ["Affiliate_B"], db=db)
    assert only_b == ["Goku --(rival of)--> Vegeta"]
    assert not any("Sonic" in fact for fact in only_b)
    assert graph_db.scoped_context("Tell me about Goku", ["Affiliate_D"], db=db) == []


def test_all_is_not_a_knowledge_base_and_an_empty_scope_gets_nothing():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic")]))
    assert graph_db.scoped_context("Tails", ["All"], db=db) == []
    assert graph_db.scoped_context("Tails", [], db=db) == []
    assert graph_db.scoped_context("Tails", None, db=db) == []


def test_two_allowed_knowledge_bases_are_searched_together():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic"), edge("Affiliate_C", "c.pdf", "Tails", "Jack", "mentioned by")]))
    facts = graph_db.scoped_context("what about Tails", ["Affiliate_A", "Affiliate_C"], db=db)
    assert len(facts) == 2


def test_very_short_names_do_not_match_inside_every_question():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "A", "B"), edge("Affiliate_A", "a.pdf", "It", "Thing")]))
    assert graph_db.scoped_context("what is a b it thing", ["Affiliate_A"], db=db) == []


def test_a_page_title_that_is_an_ordinary_word_does_not_match_every_question_that_uses_it():
    db = FakeDb(FakeEdges([edge("Affiliate_D", "site.pdf", "About", "Madison", "page about"), edge("Affiliate_D", "site.pdf", "Home", "Madison", "page of")]))
    assert graph_db.scoped_context("Tell me about Sonic, I want to go home", ["Affiliate_D"], db=db) == []


def test_a_name_matches_as_a_whole_word_not_inside_another_word():
    db = FakeDb(FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic", "friend of"), edge("Affiliate_A", "a.pdf", "Ann", "Bob", "knows")]))
    assert graph_db.scoped_context("Is it a fairy tale or a cannon?", ["Affiliate_A"], db=db) == []
    assert graph_db.scoped_context("who is tails?", ["Affiliate_A"], db=db) == ["Tails --(friend of)--> Sonic"]


def test_the_number_of_facts_added_to_an_answer_is_capped():
    rows = [edge("Affiliate_A", "a.pdf", "Hub", f"Spoke {i}", "links to") for i in range(40)]
    facts = graph_db.scoped_context("Hub", ["Affiliate_A"], db=FakeDb(FakeEdges(rows)))
    assert len(facts) == graph_db.MAX_GRAPH_FACTS


def test_the_graph_is_cached_for_a_while_so_a_question_is_not_a_database_read():
    edges = FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic")])
    db = FakeDb(edges)
    graph_db.scoped_context("Tails", ["Affiliate_A"], db=db)
    graph_db.scoped_context("Tails again", ["Affiliate_A"], db=db)
    assert edges.finds == 1


def test_deleting_a_document_removes_its_edges_and_the_next_question_sees_that():
    edges = FakeEdges([edge("Affiliate_A", "a.pdf", "Tails", "Sonic"), edge("Affiliate_A", "keep.pdf", "Tails", "Cream")])
    db = FakeDb(edges)
    assert len(graph_db.scoped_context("Tails", ["Affiliate_A"], db=db)) == 2
    assert graph_db.delete_source_edges(db, "a.pdf", "Affiliate_A") == 1
    assert graph_db.scoped_context("Tails", ["Affiliate_A"], db=db) == ["Tails --(related to)--> Cream"]


def test_deleting_by_name_in_one_knowledge_base_leaves_a_same_named_file_in_another():
    edges = FakeEdges([edge("Affiliate_A", "notes.pdf", "X1", "Y1"), edge("Affiliate_B", "notes.pdf", "X2", "Y2")])
    db = FakeDb(edges)
    graph_db.delete_source_edges(db, "notes.pdf", "Affiliate_A")
    assert [r["affiliate"] for r in edges.rows] == ["Affiliate_B"]


def test_retrieval_reads_the_graph_only_through_the_scoped_function():
    """A guard on the wiring: retrieve_node must not go back to the old global graph, which knows nothing about who may see what."""
    source = (Path(__file__).resolve().parents[1] / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "graph_db.scoped_context(question, target_scope" in source
    assert "graph_db.knowledge_graph.nodes" not in source


def test_deleting_a_document_in_the_app_also_deletes_its_edges():
    source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert re.search(r"graph_db\.delete_source_edges\(db, edge_source, affiliate\)", source)
