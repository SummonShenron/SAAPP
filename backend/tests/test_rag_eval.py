"""The retrieval eval (backend/evals/rag_eval.py): its scoring is mechanical, its harness runs the app's own search code over an in-memory store, and none of it needs a model or the network here: a small
bag-of-words embedder stands in for the real one. (The real eval is run by hand: python -m backend.evals.rag_eval.)"""
import json
import re
import zlib
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from backend.evals import rag_eval as ev
from backend.services import search


class BagOfWords:
    """A deterministic stand-in for the embedding model: each word adds to one of 512 slots. Enough for "the passage with these words ranks first"."""

    DIM = 512

    def _vec(self, text):
        vector = [0.0] * self.DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % self.DIM] += 1.0
        return vector

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture()
def embedder(monkeypatch):
    monkeypatch.setattr(ev.Embedder, "_real", lambda self: BagOfWords())
    return ev.Embedder()


def doc(source, score=0.8, library="lib"):
    return Document(page_content="text", metadata={"source": source, "score": score, "affiliate": library})


# ---- the invented libraries and questions -------------------------------------------------------------------------------------------------------------

def test_the_corpus_is_consistent_so_a_wrong_label_cannot_hide_a_bad_number():
    names = [d.name for d in ev.DOCS]
    assert len(names) == len(set(names)), "file names are unique (the score reads them as ids)"
    ids = [c.id for c in ev.CASES]
    assert len(ids) == len(set(ids))
    known = set(names)
    for case in ev.CASES:
        assert set(case.expect) <= known and set(case.forbid) <= known, case.id
        assert not (set(case.expect) & set(case.forbid)), f"{case.id}: a file cannot be both expected and forbidden"
        if case.need_all:
            assert len(case.expect) > 1, case.id
    answerable_libraries = {d.library for d in ev.DOCS if d.name in {n for c in ev.CASES for n in c.expect}}
    assert answerable_libraries <= set(ev.SCOPE), "every answer lives in a library the person may read"
    assert ev.LIB_PRIVATE not in ev.SCOPE and any(d.library == ev.LIB_PRIVATE for d in ev.DOCS), "and someone else's library exists, outside their scope"
    assert {c.kind for c in ev.CASES} == {"fact", "term", "date", "latest", "paraphrase", "synthesis", "other_job", "career", "health", "unanswerable", "isolation"}
    assert sum(1 for c in ev.CASES if c.kind == "unanswerable") >= 3


def test_the_yesterday_failure_is_a_question_in_the_set():
    case = next(c for c in ev.CASES if c.id == "progress_nw")
    assert case.need_all and set(case.expect) == set(ev.NORTHWIND_NAMES)
    assert {"Cover Letter.pdf", "Beths Letter of Recommendation.pdf", "Resume.pdf"} <= set(case.forbid) and set(ev.HARBOR_NAMES) <= set(case.forbid)


def test_chunks_are_cut_the_way_ingestion_cuts_them_and_the_headers_variant_changes_only_what_is_embedded():
    baseline = ev.chunk_documents(ingest="baseline")
    headers = ev.chunk_documents(ingest="headers")
    assert len(baseline) == len(headers) > len(ev.DOCS) - 1
    assert all(len(c.text) <= ev.CHUNK_SIZE for c in baseline)
    assert all(c.embedded == c.text for c in baseline)
    for plain, headed in zip(baseline, headers):
        assert headed.text == plain.text, "what is stored and shown is unchanged"
        assert headed.embedded.startswith(f"File: {plain.source}\nLibrary: {plain.library}\n\n") and headed.embedded.endswith(plain.text)
    assert max(c.page for c in baseline if c.source == "week-2026-09-21.json") >= 0
    source = open(ev.__file__, encoding="utf-8").read()
    function_app = open(ev.__file__.replace("backend/evals/rag_eval.py", "local_function_app/function_app.py").replace("backend\\evals\\rag_eval.py", "local_function_app\\function_app.py"), encoding="utf-8").read()
    assert f"RecursiveCharacterTextSplitter(chunk_size={ev.CHUNK_SIZE}, chunk_overlap={ev.CHUNK_OVERLAP})" in function_app, "the eval cuts chunks the way the real ingestion does"
    assert "CHUNK_SIZE = 1000" in source


# ---- scoring ------------------------------------------------------------------------------------------------------------------------------------------

CASE = ev.Case("x", "fact", "q", expect=("a.json", "b.json"), forbid=("bad.pdf",))


def test_a_hit_is_any_expected_file_in_the_top_k_and_the_rank_of_the_first_one_is_the_reciprocal():
    score = ev.score_case(CASE, [doc("other.json"), doc("a.json"), doc("a.json"), doc("b.json")], 3)
    assert score.hit and score.rr == 0.5 and score.sources == ["other.json", "a.json", "a.json"], "k cuts the list: the fourth passage is not returned"
    assert score.coverage == 0.5, "one of the two expected files was found"
    assert abs(score.precision - 2 / 3) < 1e-9
    assert not score.wrong
    miss = ev.score_case(CASE, [doc("other.json"), doc("more.json")], 3)
    assert not miss.hit and miss.rr == 0 and miss.coverage == 0 and miss.precision == 0


def test_a_forbidden_file_anywhere_in_the_top_k_is_wrong_even_when_the_right_one_is_first():
    assert ev.score_case(CASE, [doc("a.json"), doc("bad.pdf")], 3).wrong
    assert not ev.score_case(CASE, [doc("a.json"), doc("bad.pdf")], 1).wrong, "past k it was never returned"


def test_nothing_returned_scores_zero_without_dividing_by_it_and_an_unanswerable_question_is_not_answerable():
    empty = ev.score_case(CASE, [], 3)
    assert (empty.hit, empty.rr, empty.coverage, empty.precision, empty.wrong, empty.top_score) == (False, 0, 0, 0, False, 0.0)
    none = ev.score_case(ev.Case("u", "unanswerable", "q"), [doc("a.json", 0.77)], 3)
    assert not none.answerable and none.top_score == 0.77 and not none.hit


def test_aggregate_averages_over_the_answerable_questions_and_counts_wrong_over_those_with_something_to_avoid():
    cases = (ev.Case("one", "fact", "q", ("a.json",), ("bad.pdf",)), ev.Case("two", "fact", "q", ("b.json",)), ev.Case("all", "synthesis", "q", ("a.json", "b.json"), ("bad.pdf",), need_all=True),
             ev.Case("none", "unanswerable", "q"))
    scores = [
        ev.score_case(cases[0], [doc("a.json", 0.9)], 3),
        ev.score_case(cases[1], [doc("zzz.json", 0.8)], 3),
        ev.score_case(cases[2], [doc("a.json", 0.85), doc("bad.pdf", 0.8)], 3),
        ev.score_case(cases[3], [doc("zzz.json", 0.78)], 3),
    ]
    a = ev.aggregate(scores, cases)
    assert a["n"] == 3 and abs(a["hit"] - 2 / 3) < 1e-9
    assert a["complete"] == 0.5, "the synthesis question found one of its two files"
    assert a["wrong"] == 0.5, "one of the two questions that name a file to avoid got it"
    assert abs(a["top_score_unanswerable"] - 0.78) < 1e-9 and abs(a["top_score_answerable"] - (0.9 + 0.8 + 0.85) / 3) < 1e-9
    assert a["by_kind"]["fact"]["n"] == 2 and a["by_kind"]["fact"]["hit"] == 0.5


def test_the_cutoff_sweep_drops_low_passages_and_says_what_it_cost_and_what_it_cleaned():
    cases = (ev.Case("right", "fact", "q", ("a.json",), ("bad.pdf",)), ev.Case("none", "unanswerable", "q"))
    scores = [ev.score_case(cases[0], [doc("a.json", 0.82), doc("bad.pdf", 0.74)], 3), ev.score_case(cases[1], [doc("x.json", 0.76), doc("y.json", 0.72)], 3)]
    rows = {row["cutoff"]: row for row in ev.sweep(scores, cases, cutoffs=(0.70, 0.75, 0.80, 0.85))}
    assert rows[0.70] == {"cutoff": 0.70, "hit": 1.0, "wrong": 1.0, "clean": 0.0, "junk": 2.0}
    assert rows[0.75]["wrong"] == 0.0 and rows[0.75]["clean"] == 0.0 and rows[0.75]["junk"] == 1.0
    assert rows[0.80]["hit"] == 1.0 and rows[0.80]["clean"] == 1.0, "0.80 keeps the right passage and returns nothing for the unanswerable one"
    assert rows[0.85]["hit"] == 0.0 and rows[0.85]["clean"] == 1.0, "0.85 loses the right passage"


def test_the_score_scale_is_atlas_cosine_scale_and_cosine_is_cosine():
    assert (ev.atlas_score(1.0), ev.atlas_score(0.0), ev.atlas_score(-1.0)) == (1.0, 0.5, 0.0)
    assert abs(ev.cosine([1, 0], [1, 0]) - 1) < 1e-9 and abs(ev.cosine([1, 0], [0, 1])) < 1e-9 and abs(ev.cosine([1, 1], [-1, -1]) + 1) < 1e-9
    assert ev.cosine([0, 0], [1, 1]) == 0.0


# ---- the harness: the app's own search code over rows in memory ----------------------------------------------------------------------------

def run_engine(embedder, engine, query, scope=ev.SCOPE, k=3, ingest="baseline"):
    with ev.Harness(ev.chunk_documents(ingest=ingest), embedder) as harness:
        return harness.retrieve(engine, query, scope, k)


@pytest.mark.parametrize("engine", ev.ENGINES)
def test_every_engine_runs_the_apps_code_and_returns_scored_passages_with_their_file_and_library(embedder, engine):
    docs = run_engine(embedder, engine, "What was PLAT-2291 about?")
    assert 1 <= len(docs) <= 3
    assert all(d.metadata["source"] and d.metadata["affiliate"] in ev.SCOPE and 0.0 <= d.metadata["score"] <= 1.0 for d in docs)


def test_a_vector_search_for_an_exact_ticket_finds_the_week_it_is_in(embedder):
    docs = run_engine(embedder, "vector", "What was PLAT-2291 about?", k=3)
    assert docs[0].metadata["source"] in {"week-2026-09-21.json", "week-2026-09-28.json"}
    scores = [d.metadata["score"] for d in docs]
    assert scores == sorted(scores, reverse=True), "best first, on Atlas's score scale"


def test_someone_elses_library_is_never_returned_unless_it_is_in_the_scope_and_the_test_can_tell(embedder):
    question = "What is the wifi password for the cabin?"
    assert ev.LIB_PRIVATE not in {d.metadata["affiliate"] for d in run_engine(embedder, "vector", question, k=8)}
    leaked = run_engine(embedder, "vector", question, scope=ev.SCOPE + (ev.LIB_PRIVATE,), k=3)
    assert leaked[0].metadata["source"] == "cabin_notes.txt", "with the library in scope the answer IS found, so its absence above is the scope filter's doing"


def test_the_app_engine_goes_through_the_apps_routing_and_a_forced_engine_does_not(embedder, monkeypatch):
    seen = []
    real = search._detect_routing_strategy
    monkeypatch.setattr(search, "_detect_routing_strategy", lambda q: seen.append(q) or real(q))
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        harness.retrieve("vector", "latest release", ev.SCOPE, 3)
        assert seen == []
        harness.retrieve("app", "latest release", ev.SCOPE, 3)
        assert seen == ["latest release"]


def test_the_harness_puts_the_search_module_back_when_it_closes(embedder):
    before = (search._get_mongo_collection, search._embeddings)
    with ev.Harness(ev.chunk_documents(), embedder):
        assert search._get_mongo_collection is not before[0] and search._embeddings is not before[1]
    assert (search._get_mongo_collection, search._embeddings) == before


def test_each_passage_and_question_is_embedded_once(embedder):
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        passages = embedder.calls
        for _ in range(3):
            harness.retrieve("vector", "a question", ev.SCOPE, 3)
    assert embedder.calls == passages + 1, "the same question three times costs one embedding"


def test_embedding_the_same_passages_twice_is_free_in_memory_and_across_runs_through_the_disk_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(ev.Embedder, "_real", lambda self: BagOfWords())
    path = str(tmp_path / "cache.json")
    first = ev.Embedder(path)
    vectors = first.docs(["alpha beta", "gamma"])
    assert first.calls == 2
    assert first.docs(["alpha beta", "gamma"]) == vectors and first.calls == 2, "the second time costs nothing"
    first.query("alpha")
    assert first.calls == 3
    first.save()
    second = ev.Embedder(path)
    assert second.docs(["alpha beta", "gamma"]) == vectors and second.query("alpha") == first.query("alpha")
    assert second.calls == 0, "a new run, same texts: everything comes from the file"
    second.docs(["something new"])
    assert second.calls == 1
    assert ev.Embedder("").calls == 0 and ev.Embedder(str(tmp_path / "missing.json")).docs(["x"]) is not None


# ---- the tagging experiment ----------------------------------------------------------------------------------------------------------------------------

def test_the_oracle_labels_cover_every_file_with_valid_values_and_the_weeks_come_from_the_file_names():
    tags = ev.oracle_tags()
    assert set(tags) == {d.name for d in ev.DOCS}
    for name, tag in tags.items():
        assert tag["employer"] in ev.EMPLOYERS and tag["doc_type"] in ev.TYPES, name
    assert tags["week-2026-09-07.json"] == {"doc_type": "weekly_report", "employer": "northwind", "period": "2026-09-07"}
    assert tags["week-2025-03-10.json"]["employer"] == "harbor" and tags["Resume.pdf"]["doc_type"] == "resume" and tags["Cover Letter.pdf"]["employer"] == "northwind"
    assert tags["cabin_notes.txt"]["doc_type"] == "notes"


def test_what_a_question_asks_for_only_uses_labels_that_exist_and_names_no_question_that_is_not_in_the_set():
    ids = {c.id for c in ev.CASES}
    assert set(ev.WANTED) <= ids
    for case_id, want in ev.WANTED.items():
        assert set(want) <= {"employer", "doc_type", "order"}, case_id
        assert want.get("employer", "northwind") in ev.EMPLOYERS and want.get("doc_type", "guide") in ev.TYPES and want.get("order", "recent") == "recent", case_id
    assert "ticket_id" not in ev.WANTED and "capital" not in ev.WANTED, "a question that names neither an employer nor a kind asks for nothing"


def test_mislabelling_changes_exactly_that_share_of_the_files_in_both_labels_the_same_way_for_a_seed_and_differently_for_another():
    tags = ev.oracle_tags()
    assert ev.noisy_tags(tags, 0.0, 0) == tags
    noisy = ev.noisy_tags(tags, 0.30, 0)
    changed = [n for n in tags if noisy[n] != tags[n]]
    assert len(changed) == round(0.30 * len(tags))
    for n in changed:
        assert noisy[n]["employer"] != tags[n]["employer"] and noisy[n]["doc_type"] != tags[n]["doc_type"] and noisy[n]["period"] == tags[n]["period"]
    assert ev.noisy_tags(tags, 0.30, 0) == noisy, "the same seed gives the same mistakes"
    assert ev.noisy_tags(tags, 0.30, 1) != noisy
    assert tags == ev.oracle_tags(), "and the perfect labels were not touched"


def case_by_id(case_id):
    return next(c for c in ev.CASES if c.id == case_id)


def test_with_nothing_asked_for_or_labels_ignored_the_search_is_the_plain_one(embedder):
    tags = ev.oracle_tags()
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        plain = [d.page_content for d in harness.retrieve("app", case_by_id("ticket_id").query, ev.SCOPE, 3)]
        for mode in ("none", "hard", "soft"):
            assert [d.page_content for d in ev.retrieve_tagged(harness, mode, case_by_id("ticket_id"), tags, ev.SCOPE, 3)] == plain, mode
        asked = case_by_id("progress_nw")
        assert [d.page_content for d in ev.retrieve_tagged(harness, "none", asked, tags, ev.SCOPE, 3)] == [d.page_content for d in harness.retrieve("app", asked.query, ev.SCOPE, 3)]


def test_a_hard_filter_returns_only_files_with_the_labels_the_question_asked_for_and_the_store_is_whole_again_after(embedder):
    tags = ev.oracle_tags()
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        whole = len(harness.collection.rows)
        docs = ev.retrieve_tagged(harness, "hard", case_by_id("progress_nw"), tags, ev.SCOPE, 8)
        assert docs and all(tags[d.metadata["source"]]["employer"] == "northwind" and tags[d.metadata["source"]]["doc_type"] == "weekly_report" for d in docs)
        harbor = ev.retrieve_tagged(harness, "hard", case_by_id("harbor_work"), tags, ev.SCOPE, 8)
        assert harbor and all(tags[d.metadata["source"]]["employer"] == "harbor" for d in harbor), "the recommendation letter is about Harbor Mutual too"
        assert not {d.metadata["source"] for d in harbor} & set(ev.NORTHWIND_NAMES)
        assert len(harness.collection.rows) == whole


def test_a_hard_filter_on_the_most_recent_keeps_only_the_newest_week_and_a_wrong_label_can_hide_the_right_file(embedder):
    tags = ev.oracle_tags()
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        docs = ev.retrieve_tagged(harness, "hard", case_by_id("latest"), tags, ev.SCOPE, 5)
        assert docs and {d.metadata["source"] for d in docs} == {"week-2026-10-26.json"}
        broken = {**tags, "week-2026-10-26.json": {**tags["week-2026-10-26.json"], "doc_type": "guide"}}
        hidden = ev.retrieve_tagged(harness, "hard", case_by_id("latest"), broken, ev.SCOPE, 5)
        assert "week-2026-10-26.json" not in {d.metadata["source"] for d in hidden}, "a mislabelled file is gone from a hard filter: that is its risk"
        everything_gone = {name: {**tag, "doc_type": "guide"} for name, tag in tags.items()}
        assert ev.retrieve_tagged(harness, "hard", case_by_id("progress_nw"), everything_gone, ev.SCOPE, 3) == []


class StubHarness:
    """Hands back a fixed pool, best first, whatever it is asked."""

    def __init__(self, pool):
        self.pool = pool
        self.asked = []

    def retrieve(self, engine, query, scope, k):
        self.asked.append(k)
        return self.pool[:k]


def test_a_soft_boost_lets_matching_files_rank_higher_without_removing_anything_and_asks_for_a_wider_pool():
    tags = {"a.json": {"employer": "harbor", "doc_type": "resume", "period": ""}, "b.json": {"employer": "northwind", "doc_type": "weekly_report", "period": "2026-10-26"},
            "c.json": {"employer": "northwind", "doc_type": "letter", "period": ""}}
    pool = [doc("a.json", 0.84), doc("c.json", 0.82), doc("b.json", 0.81)]
    stub = StubHarness(pool)
    out = ev.retrieve_tagged(stub, "soft", ev.Case("progress_nw", "synthesis", "q"), tags, ev.SCOPE, 3)
    assert [d.metadata["source"] for d in out] == ["b.json", "c.json", "a.json"], "both labels match b (+0.10), one matches c (+0.05), none a"
    assert stub.asked == [12], "four times as many as were wanted, so a boost has something to promote"
    assert [d.metadata["source"] for d in ev.retrieve_tagged(StubHarness(pool), "soft", ev.Case("progress_nw", "synthesis", "q"), tags, ev.SCOPE, 1)] == ["b.json"]
    newest = ev.retrieve_tagged(StubHarness(pool), "soft", ev.Case("latest", "latest", "q"), tags, ev.SCOPE, 3)
    assert newest[0].metadata["source"] == "b.json", "the newest week is boosted when the question asks for the most recent"


def test_a_whole_tagging_run_has_the_modes_rates_and_ks_averages_over_draws_and_a_readable_report(embedder, capsys, tmp_path):
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        results = ev.run_tagging(harness, ks=(3,), error_rates=(0.0, 0.3), seeds=(0, 1))
    assert set(results) == {"none", "hard", "soft"} and set(results["none"]) == {0.0} and set(results["hard"]) == {0.0, 0.3}
    cell = results["hard"][0.3][3]
    assert {"hit", "mrr", "precision", "complete", "wrong", "kinds"} <= set(cell) and set(cell["kinds"]) == {"latest", "synthesis", "other_job", "career"}
    report = ev.format_tagging(results)
    for needle in ("TAGGING EXPERIMENT", "mislabelled", "hard", "soft", "none", "latest"):
        assert needle in report
    out = tmp_path / "t.json"
    assert ev.main(["--tagging", "--k", "3", "--save", str(out), "--cache", str(tmp_path / "v.json")]) == 0
    assert "TAGGING EXPERIMENT" in capsys.readouterr().out and "hard" in json.loads(out.read_text())


# ---- the run, the report and the command ---------------------------------------------------------------------------------------------------------

def test_a_whole_run_scores_every_question_and_the_report_and_record_are_complete(embedder):
    with ev.Harness(ev.chunk_documents(), embedder) as harness:
        results = {"baseline": ev.run(harness, ("vector", "app"), (3, 5))}
    assert set(results["baseline"]) == {"vector", "app"} and set(results["baseline"]["vector"]) == {3, 5}
    assert [s.id for s in results["baseline"]["vector"][3]] == [c.id for c in ev.CASES]
    report = ev.format_report(results)
    for needle in ("INGEST: baseline", "engine", "complete", "MRR", "wrong", "relevance cutoff sweep", "WHAT CAME BACK", "progress_nw"):
        assert needle in report, needle
    record = json.loads(json.dumps(ev.to_record(results)))
    assert record["baseline"]["vector"]["3"]["summary"]["n"] == sum(1 for c in ev.CASES if c.expect)


def test_the_command_lists_runs_and_refuses_an_unknown_engine(embedder, capsys, tmp_path):
    assert ev.main(["--list"]) == 0
    assert "LIBRARIES" in capsys.readouterr().out
    out = tmp_path / "saved" / "rag.json"
    assert ev.main(["--engines", "vector", "--k", "3", "--ingest", "baseline", "--save", str(out), "--cache", str(tmp_path / "vectors.json")]) == 0
    assert (tmp_path / "vectors.json").exists(), "the run wrote its vectors where it was told, not into the project's own cache"
    assert "INGEST: baseline" in capsys.readouterr().out and json.loads(out.read_text())["baseline"]["vector"]["3"]
    with pytest.raises(SystemExit):
        ev.main(["--engines", "nonsense"])
