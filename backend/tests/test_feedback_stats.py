"""Feedback as the model is shown it, and as the Sonic Health page counts it (backend/utils/correction_kinds.py, feedback_stats.py)."""
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import app_utils, correction_kinds as ck, feedback_stats as fs, self_counters
from backend.utils import sonic_health as sh

NOW = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
APP_SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------ what kind a record is ------------------

@pytest.mark.parametrize("record, kind", [
    ({"rating": "like", "tag": "positive"}, ck.POSITIVE),            # what the page sends for a thumbs-up
    ({"rating": "positive"}, ck.POSITIVE),
    ({"tag": "positive"}, ck.POSITIVE),                              # older records: no rating, the tag says it
    ({"tag": "thumbs_up", "reason": "User marked response as helpful"}, ck.POSITIVE),
    ({"rating": None, "tag": "thumbs_up"}, ck.POSITIVE),
    ({"rating": "dislike", "tag": "hallucination"}, ck.NEGATIVE),
    ({"rating": "negative", "tag": "hallucination"}, ck.NEGATIVE),   # the answer checker
    ({"tag": "formatting"}, ck.NEGATIVE),                            # older thumbs-down: no rating, a reason tag
    ({}, ck.NEGATIVE),
    ({"rating": "LIKE"}, ck.POSITIVE),
])
def test_every_shape_of_stored_feedback_reads_as_the_right_kind(record, kind):
    assert ck.polarity(record) == kind


def test_the_rating_to_store_is_positive_or_negative():
    assert ck.normalize_rating("like") == ck.POSITIVE
    assert ck.normalize_rating("positive") == ck.POSITIVE
    assert ck.normalize_rating("dislike") == ck.NEGATIVE
    assert ck.normalize_rating(None) == ck.NEGATIVE
    assert ck.normalize_rating("whatever") == ck.NEGATIVE


def test_who_gave_it_comes_from_the_source_or_from_the_rating_the_two_paths_wrote():
    assert ck.source({"source": "evaluator", "rating": "positive"}) == ck.EVALUATOR
    assert ck.source({"source": "user"}) == ck.USER
    assert ck.source({"rating": "negative"}) == ck.EVALUATOR     # older: only the answer checker wrote exactly "negative"
    assert ck.source({"rating": "dislike"}) == ck.USER
    assert ck.source({"rating": "like"}) == ck.USER
    assert ck.source({"tag": "formatting"}) == ck.USER


# ------------------------------------------------------------------------ what the model is shown -----------------

class FakeCorrections:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query=None, *a, **k):
        query = query or {}
        out = [r for r in self.rows if r.get("username") == query.get("username", r.get("username"))]
        pattern = query.get("user_prompt")
        if pattern is not None and hasattr(pattern, "search"):
            out = [r for r in out if pattern.search(r.get("user_prompt", ""))]
        return list(out)

    def insert_one(self, doc):
        self.rows.append(doc)


class FakeDb(dict):
    def __init__(self, rows):
        super().__init__({"corrections": FakeCorrections(rows)})


def correction(rating, tag, text="the liked or disliked answer", embedding=(1.0, 0.0), **extra):
    return {"username": "u1", "user_prompt": "how do I do X", "bad_response": text, "reason": "because", "tag": tag, "rating": rating,
            "embedding": list(embedding) if embedding is not None else None, **extra}


@pytest.fixture
def ask(monkeypatch):
    """fetch_relevant_corrections for a question whose embedding matches the stored ones."""
    def run(rows):
        monkeypatch.setattr(app_utils, "get_db", lambda: FakeDb(rows))
        monkeypatch.setattr(app_utils, "embed_text", lambda text: [1.0, 0.0])
        return app_utils.fetch_relevant_corrections("u1", "how do I do X")
    return run


def test_a_thumbs_up_stored_as_like_is_shown_as_a_preferred_example_not_a_thing_to_avoid(ask):
    shown = ask([correction("like", "positive", "A GOOD ANSWER")])
    assert "PREFERRED EXAMPLES" in shown and "A GOOD ANSWER" in shown
    assert "CRITICAL GUARDRAILS" not in shown


def test_a_thumbs_up_with_no_rating_at_all_is_also_a_preferred_example(ask):
    shown = ask([correction(None, "thumbs_up", "ANOTHER GOOD ONE")])
    assert "PREFERRED EXAMPLES" in shown and "CRITICAL GUARDRAILS" not in shown


def test_a_thumbs_down_and_a_checker_redo_are_still_guardrails(ask):
    shown = ask([correction("dislike", "hallucination", "BAD ONE"), correction("negative", "formatting", "REDONE ONE")])
    assert "CRITICAL GUARDRAILS" in shown and "BAD ONE" in shown and "REDONE ONE" in shown
    assert "PREFERRED EXAMPLES" not in shown


def test_the_word_for_word_fallback_reads_old_records_the_same_way(monkeypatch):
    rows = [correction("like", "positive", "LIKED"), correction("dislike", "hallucination", "DISLIKED")]
    monkeypatch.setattr(app_utils, "get_db", lambda: FakeDb(rows))
    monkeypatch.setattr(app_utils, "embed_text", lambda text: None)       # no embeddings: the old substring route
    shown = app_utils.fetch_relevant_corrections("u1", "how do I do X")
    assert "PREFERRED EXAMPLES" in shown and "LIKED" in shown
    assert "CRITICAL GUARDRAILS" in shown and "DISLIKED" in shown


def test_saving_feedback_stores_a_clean_rating_and_who_gave_it(monkeypatch):
    rows = []
    monkeypatch.setattr(app_utils, "get_db", lambda: FakeDb(rows))
    monkeypatch.setattr(app_utils, "embed_text", lambda text: [0.1, 0.2])
    app_utils.save_correction("u1", "q", "a", "why", "positive", rating="like")
    app_utils.save_correction("u1", "q", "a", "why", "hallucination", rating="negative", source="evaluator")
    app_utils.save_correction("u1", "q", "a", "why", "formatting", rating="dislike", source="nonsense")
    assert [(r["rating"], r["source"]) for r in rows] == [("positive", "user"), ("negative", "evaluator"), ("negative", "user")]


def test_the_app_marks_who_gave_each_kind_of_feedback_and_counts_the_turns_it_was_shown_on():
    assert 'source="evaluator"' in APP_SOURCE and 'source="user"' in APP_SOURCE
    assert 'tally(turn_counts, "guardrail_applied")' in APP_SOURCE
    assert "guardrail_applied" in self_counters.KEYS


# ------------------------------------------------------------------------ what the page counts -------------------

def row(rating, tag, days_ago=1, user="u1", embedding=True, **extra):
    return {"username": user, "rating": rating, "tag": tag, "created_at": (NOW - timedelta(days=days_ago)).isoformat(), "has_embedding": embedding, **extra}


def test_the_period_counts_split_by_kind_and_by_who_gave_it():
    rows = [row("like", "positive"), row("like", "positive", user="u2"), row("dislike", "formatting"), row("negative", "hallucination"), row("negative", "hallucination"),
            row("like", "positive", days_ago=40)]                       # outside a 7-day period
    out = fs.summarize_feedback(rows, now=NOW, days=7)
    assert out["period"] == {"positive": 2, "negative": 3, "user_positive": 2, "user_negative": 1, "evaluator_negative": 2,
                             "people": 2, "negative_share": 0.6}
    assert out["by_tag"] == [{"tag": "hallucination", "count": 2}, {"tag": "formatting", "count": 1}]


def test_what_is_stored_counts_all_time_and_flags_the_old_rule_misreading_thumbs_ups():
    rows = [row("like", "positive"), row(None, "thumbs_up", embedding=False), row("positive", "positive"), row("dislike", "formatting"), row("negative", "other", days_ago=60)]
    stored = fs.summarize_feedback(rows, now=NOW, days=7)["stored"]
    assert stored["total"] == 5 and stored["positive"] == 3 and stored["negative"] == 2
    assert stored["matchable"] == 4 and stored["unmatchable"] == 1
    assert stored["positives_old_rule_misread"] == 2                     # "like" and the unrated thumbs-up; "positive" was always read right


def test_unknown_tags_are_counted_as_other_so_free_text_never_reaches_the_page():
    out = fs.summarize_feedback([row("dislike", "my own free text about Jane")], now=NOW, days=7)
    assert out["by_tag"] == [{"tag": "other", "count": 1}]


def test_the_daily_series_has_one_entry_per_day_oldest_first():
    out = fs.summarize_feedback([row("like", "positive", days_ago=0), row("dislike", "formatting", days_ago=2)], now=NOW, days=3)
    assert [d["day"] for d in out["daily"]] == sorted(d["day"] for d in out["daily"]) and len(out["daily"]) == 3
    assert sum(d["positive"] for d in out["daily"]) == 1 and sum(d["negative"] for d in out["daily"]) == 1


def test_how_often_feedback_was_shown_to_the_model_is_reported_as_turns_and_share():
    out = fs.summarize_feedback([], applied_turns=3, turns_total=40, now=NOW, days=7)
    assert out["applied"] == {"turns": 3, "share": 0.075}
    assert fs.summarize_feedback([], now=NOW, days=7)["applied"]["share"] is None


def test_nothing_in_the_summary_carries_a_prompt_a_response_or_a_name():
    out = fs.summarize_feedback([row("dislike", "formatting", user="jane@example.com", reason="private words")], now=NOW, days=7)
    assert "jane" not in repr(out) and "private" not in repr(out)


# ------------------------------------------------------------------------ the knowledge graph --------------------

class FakeAggregate:
    def __init__(self, edges, documents):
        self.data = {"graph_edges": edges, "documents": documents}

    def __getitem__(self, name):
        data = self.data[name]
        return type("Coll", (), {"aggregate": lambda self_, pipeline: iter(data)})()


def grouped(rows, key):
    return [{"_id": {"affiliate": a, "source": s}, key: n} for a, s, n in rows]


def test_each_knowledge_base_shows_its_edges_and_its_documents_that_have_none():
    db = FakeAggregate(
        edges=grouped([("Affiliate_A", "a.pdf", 12), ("Affiliate_A", "b.pdf", 3)], "edges"),
        documents=grouped([("Affiliate_A", "a.pdf", 5), ("Affiliate_A", "b.pdf", 2), ("Affiliate_A", "c.pdf", 4), ("Affiliate_D", "d.pdf", 9)], "chunks"),
    )
    out = fs.summarize_graph(db)
    assert out["edges_total"] == 15 and out["documents_total"] == 4 and out["documents_without_edges"] == 2
    a, d = out["by_affiliate"]
    assert a["affiliate"] == "Affiliate_A" and a["edges"] == 15 and a["documents"] == 3 and a["missing"] == ["c.pdf"]
    assert d["affiliate"] == "Affiliate_D" and d["edges"] == 0 and d["missing"] == ["d.pdf"]


def test_an_empty_graph_is_reported_as_every_document_missing_not_as_nothing():
    out = fs.summarize_graph(FakeAggregate(edges=[], documents=grouped([("Affiliate_A", "a.pdf", 5)], "chunks")))
    assert out["edges_total"] == 0 and out["documents_without_edges"] == 1


def test_personal_knowledge_bases_are_one_row_of_counts_with_no_ids_and_no_file_names():
    db = FakeAggregate(
        edges=grouped([("Affiliate_A", "a.pdf", 4), ("kb_1a2b3c4d5e", "my private tax return.pdf", 9)], "edges"),
        documents=grouped([("Affiliate_A", "a.pdf", 5), ("kb_1a2b3c4d5e", "my private tax return.pdf", 3), ("kb_1a2b3c4d5e", "diary.pdf", 2),
                           ("kb_ffeeddccbb", "someone else's file.pdf", 6)], "chunks"),
    )
    out = fs.summarize_graph(db)
    assert [r["affiliate"] for r in out["by_affiliate"]] == ["Affiliate_A", "personal"]
    personal = out["by_affiliate"][1]
    assert personal["documents"] == 3 and personal["chunks"] == 11 and personal["edges"] == 9
    assert personal["documents_without_edges"] == 2 and personal["missing"] == []
    text = repr(out)
    for private in ("tax return", "diary", "someone else", "kb_1a2b3c4d5e", "kb_ffeeddccbb"):
        assert private not in text
    assert out["documents_without_edges"] == 2          # they still count toward the total, so a gap there still raises the warning


def test_a_section_that_cannot_be_built_is_left_out_without_taking_the_page_down():
    assert sh._section("boom", lambda: (_ for _ in ()).throw(RuntimeError("down"))) is None
    assert sh._section("fine", lambda: {"ok": 1}) == {"ok": 1}
