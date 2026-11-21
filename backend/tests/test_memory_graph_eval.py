"""The memory graph eval (backend/evals/memory_graph_eval.py): does a graph over memory facts bring better extra context than the one-hop links recall uses now? These tests keep the harness honest: the authored
corpus is well formed, the damage to the links is what it says, the baseline IS the app's own fact_links.linked_extras, each method does what its name says, and nothing in a default run touches the network."""
import json
import random
from datetime import datetime, timezone

import networkx as nx
import pytest

from backend.evals import memory_graph_eval as ev
from backend.utils import fact_links
from backend.utils.memory_utils import UserFact


def toy_facts(edges, ids="abcdefgh"):
    now = datetime.now(timezone.utc).isoformat()
    names = sorted(set(ids) | {n for pair in edges for n in pair})
    facts = {i: UserFact(id=i, username="t", category="context", fact=f"fact {i}", created_at=now, updated_at=now, embedding=[1.0, 0.0]) for i in names}
    for a, b in edges:
        facts[a].related.append(b)
        facts[b].related.append(a)
    return list(facts.values())


def seed(facts, *ids):
    by_id = {f.id: f for f in facts}
    return [(by_id[i], 1.0) for i in ids]


# ---- the corpus --------------------------------------------------------------------------------------------------------------------------------------

def test_the_authored_links_are_between_real_facts_once_each_and_never_to_themselves():
    seen = set()
    for a, b in ev.LINKS:
        assert a in ev.FACTS and b in ev.FACTS and a != b
        assert frozenset((a, b)) not in seen, (a, b)
        seen.add(frozenset((a, b)))


def test_every_question_asks_for_facts_that_exist_and_names_where_it_starts():
    names = [c.name for c in ev.CASES]
    assert len(names) == len(set(names)) and len(ev.CASES) >= 20
    for case in ev.CASES:
        assert case.needs and case.direct, case.name
        assert case.needs <= set(ev.FACTS) and case.ok <= set(ev.FACTS), case.name
        assert case.direct <= case.needs, f"{case.name}: a question starts from facts it needs"
        assert not (case.needs & case.ok), f"{case.name}: a needed fact is not also an allowed extra"
        assert case.kind in {"one_hop", "two_hop", "hub", "isolated", "cross"}


def test_a_question_that_needs_more_than_it_starts_with_is_reachable_through_the_authored_links():
    """Otherwise no method could ever score it: every needed fact is in the same connected piece as a starting fact (except a cross-topic question, which starts from two pieces)."""
    graph = nx.Graph(ev.LINKS)
    graph.add_nodes_from(ev.FACTS)
    for case in ev.CASES:
        reachable = set()
        for start in case.direct:
            reachable |= nx.node_connected_component(graph, start)
        assert case.needs <= reachable, case.name


def test_the_two_hop_questions_really_need_two_hops_and_the_isolated_ones_have_nothing_to_reach():
    graph = nx.Graph(ev.LINKS)
    graph.add_nodes_from(ev.FACTS)
    for case in (c for c in ev.CASES if c.kind == "two_hop"):
        farthest = max(min(nx.shortest_path_length(graph, s, t) for s in case.direct) for t in case.needs)
        assert farthest >= 2, case.name
    for case in (c for c in ev.CASES if c.kind == "isolated"):
        assert all(graph.degree(f) == 0 for f in case.direct if f in graph) or all(f not in graph for f in case.direct), case.name


# ---- building and damaging ----------------------------------------------------------------------------------------------------------------------------

def test_facts_are_the_apps_own_objects_with_symmetric_capped_links():
    facts = ev.build_facts(ev.OfflineEmbedder(), ev.LINKS)
    assert all(isinstance(f, UserFact) and f.embedding for f in facts) and len(facts) == len(ev.FACTS)
    by_id = {f.id: f for f in facts}
    for fact in facts:
        assert len(fact.related) <= fact_links.MAX_LINKS_PER_FACT
        for other in fact.related:
            assert fact.id in by_id[other].related
    hub = [("hub", f"x{i}") for i in range(20)]
    capped = ev.build_facts(ev.OfflineEmbedder(), [("w1", t) for t in ("w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9")])
    assert len({f.id: f for f in capped}["w1"].related) == fact_links.MAX_LINKS_PER_FACT and hub


def test_no_damage_changes_nothing_and_the_same_seed_gives_the_same_damage():
    assert ev.damage(ev.LINKS, 0.0, 0.0, random.Random(1)) == list(ev.LINKS)
    assert ev.damage(ev.LINKS, 0.3, 0.25, random.Random(5)) == ev.damage(ev.LINKS, 0.3, 0.25, random.Random(5))
    assert ev.damage(ev.LINKS, 0.3, 0.25, random.Random(5)) != ev.damage(ev.LINKS, 0.3, 0.25, random.Random(6))


def test_missing_links_are_dropped_from_the_true_ones_and_wrong_links_cross_topics_and_are_new():
    true = {frozenset(p) for p in ev.LINKS}
    dropped = ev.damage(ev.LINKS, 0.5, 0.0, random.Random(2))
    assert {frozenset(p) for p in dropped} < true and len(dropped) < len(ev.LINKS)
    noisy = ev.damage(ev.LINKS, 0.0, 0.25, random.Random(2))
    added = [p for p in noisy if frozenset(p) not in true]
    assert len(added) == round(len(ev.LINKS) * 0.25) and set(map(frozenset, ev.LINKS)) <= set(map(frozenset, noisy))
    assert all(ev.CLUSTER_OF[a] != ev.CLUSTER_OF[b] for a, b in added), "a wrong link joins facts about different things"
    assert len({frozenset(p) for p in noisy}) == len(noisy), "no link is added twice"


# ---- seeds ---------------------------------------------------------------------------------------------------------------------------------------------

def test_oracle_seeds_are_the_authored_direct_matches_and_similarity_seeds_are_the_closest_by_cosine():
    facts = ev.build_facts(ev.OfflineEmbedder(), ev.LINKS)
    case = next(c for c in ev.CASES if c.name == "manager")
    assert {f.id for f, _ in ev.seeds_for(None, facts, 3, case, "oracle")} == case.direct
    near = ev.OfflineEmbedder().query("Max is allergic to chicken")
    top = ev.seeds_for(near, facts, 2, None, "similarity")
    assert top[0][0].id == "d4" and len(top) == 2 and top[0][1] >= top[1][1]


# ---- the methods ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_baseline_is_the_apps_own_function_with_its_cap_and_not_a_copy():
    facts = toy_facts([("a", "b"), ("a", "c"), ("a", "d"), ("a", "e"), ("b", "f")])
    graph = ev.graph_of(facts)
    chosen = seed(facts, "a")
    assert ev.m_onehop(chosen, facts, graph, 3) == [f.id for f in fact_links.linked_extras([f for f, _ in chosen], facts, limit=3)] == ["b", "c", "d"]
    assert len(ev.m_onehop(chosen, facts, graph, 6)) == 4 and "f" not in ev.m_onehop(chosen, facts, graph, 6), "one hop only"
    assert ev.m_none(chosen, facts, graph, 3) == []


def test_two_hops_reach_what_one_hop_cannot_and_nearer_facts_come_first():
    facts = toy_facts([("a", "b"), ("b", "c"), ("c", "d")])
    graph = ev.graph_of(facts)
    assert ev.m_onehop(seed(facts, "a"), facts, graph, 3) == ["b"]
    assert ev.m_hops2(seed(facts, "a"), facts, graph, 3) == ["b", "c"], "two hops, never three"


def test_a_fact_linked_to_several_seeds_outranks_one_linked_to_a_single_seed():
    facts = toy_facts([("a", "x"), ("b", "x"), ("a", "y"), ("c", "y2")])
    graph = ev.graph_of(facts)
    assert ev.m_hops2(seed(facts, "a", "b"), facts, graph, 1) == ["x"]
    assert ev.m_ppr(seed(facts, "a", "b"), facts, graph, 1) == ["x"]


def test_pagerank_reaches_a_fact_two_hops_away_through_two_paths_that_one_hop_misses():
    facts = toy_facts([("a", "m1"), ("a", "m2"), ("m1", "z"), ("m2", "z"), ("a", "n")])
    graph = ev.graph_of(facts)
    assert "z" not in ev.m_onehop(seed(facts, "a"), facts, graph, 3)
    assert "z" in ev.m_ppr(seed(facts, "a"), facts, graph, 4)


def test_an_isolated_seed_gets_no_extras_from_either_pagerank_method_the_solvers_leftover_rank_is_not_a_link():
    """Regression: networkx leaves a few millionths of rank on unconnected facts, which once made every isolated question come back with three wrong extras."""
    facts = ev.build_facts(ev.OfflineEmbedder(), ev.LINKS)
    graph = ev.graph_of(facts)
    chosen = ev.seeds_for(None, facts, 3, next(c for c in ev.CASES if c.name == "latte"), "oracle")
    assert ev.m_ppr(chosen, facts, graph, 6) == [] and ev.m_ppr_rel(chosen, facts, graph, 6) == []
    assert ev.m_hops2(chosen, facts, graph, 6) == [] and ev.m_onehop(chosen, facts, graph, 6) == []


def test_seeds_are_never_returned_as_their_own_extras():
    facts = toy_facts([("a", "b"), ("b", "c"), ("a", "c")])
    graph = ev.graph_of(facts)
    for method in ev.METHODS.values():
        assert not ({"a", "b"} & set(method(seed(facts, "a", "b"), facts, graph, 6)))


def test_the_relevance_floor_keeps_the_strong_extras_and_drops_the_weak_ones():
    facts = toy_facts([("a", "s1"), ("a", "s2"), ("s1", "w1"), ("w1", "w2"), ("w2", "w3")])
    graph = ev.graph_of(facts)
    plain = ev.m_ppr(seed(facts, "a"), facts, graph, 6)
    floored = ev.m_ppr_rel(seed(facts, "a"), facts, graph, 6)
    assert set(floored) < set(plain) and {"s1", "s2"} <= set(floored) and "w3" not in floored


def test_a_seed_that_is_not_in_the_graph_does_not_crash():
    facts = toy_facts([("a", "b")])
    graph = ev.graph_of(facts)
    ghost = seed(facts, "a")
    graph.remove_node("a")
    for method in ev.METHODS.values():
        assert method(ghost, facts, graph, 3) == [] or isinstance(method(ghost, facts, graph, 3), list)      # no exception is the point: a seed whose fact was deleted since the graph was built


# ---- scoring -------------------------------------------------------------------------------------------------------------------------------------------

def test_recall_complete_extras_and_wrong_are_counted_the_way_the_report_says():
    case = ev.Case("c", "q", needs={"a", "b", "c"}, ok={"d"}, direct={"a"})
    outcome = ev.score_case(case, ["a"], ["b", "d", "z", "y"])
    assert outcome.recall == pytest.approx(2 / 3) and outcome.complete is False and outcome.extras == 4 and outcome.wrong == 2 and outcome.seed_recall == pytest.approx(1 / 3)
    full = ev.score_case(case, ["a"], ["b", "c"])
    assert full.complete is True and full.recall == 1.0 and full.wrong == 0
    assert ev.score_case(case, [], []).recall == 0.0


def test_aggregate_averages_and_an_empty_list_is_zeros():
    rows = [ev.Outcome(1.0, True, 2, 0, 1.0), ev.Outcome(0.5, False, 4, 2, 0.0)]
    assert ev.aggregate(rows) == {"recall": 0.75, "complete": 0.5, "extras": 3.0, "wrong": 1.0, "seed_recall": 0.5}
    assert ev.aggregate([])["recall"] == 0.0


# ---- the whole run ------------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def results():
    return ev.run(ev.OfflineEmbedder(), trials=2)


def test_the_grid_has_every_condition_method_and_k(results):
    for condition in ev.CONDITIONS:
        assert f"{condition}|none|0" in results["summary"]
        for method in ev.METHODS:
            if method != "none":
                for k in (3, 6):
                    assert f"{condition}|{method}|{k}" in results["summary"]


def test_a_run_is_repeatable_and_perfect_links_are_exact():
    first, second = ev.run(ev.OfflineEmbedder(), trials=1, conditions=["perfect", "both"], methods=["onehop", "ppr"]), ev.run(ev.OfflineEmbedder(), trials=1, conditions=["perfect", "both"], methods=["onehop", "ppr"])
    assert first["summary"] == second["summary"]


def test_extras_help_and_never_hurt_recall_with_perfect_links_and_the_apps_baseline_adds_nothing_wrong(results):
    summary = results["summary"]
    none, onehop = summary["perfect|none|0"], summary["perfect|onehop|3"]
    assert onehop["recall"] > none["recall"] and onehop["complete"] > none["complete"]
    assert onehop["wrong"] == 0.0, "with true links, one hop brings nothing that is not defensible"
    for method in ("hops2", "ppr", "ppr_rel"):
        assert summary[f"perfect|{method}|3"]["recall"] >= onehop["recall"]


def test_damaged_links_cost_every_method_recall(results):
    summary = results["summary"]
    for method, k in (("onehop", 3), ("ppr", 3)):
        assert summary[f"missing30|{method}|{k}"]["recall"] < summary[f"perfect|{method}|{k}"]["recall"]
    assert summary["noisy25|onehop|3"]["wrong"] > summary["perfect|onehop|3"]["wrong"], "wrong links bring wrong context"


def test_the_report_names_every_condition_and_method_and_the_kinds(results):
    text = ev.format_report(results)
    for condition in ev.CONDITIONS:
        assert f"== links: {condition} ==" in text
    for method in ev.METHODS:
        assert method in text
    assert "recall by question kind" in text and "two_hop" in text and "authored direct matches" in text


# ---- the command line, and no network ----------------------------------------------------------------------------------------------------------------

def test_the_default_run_prints_a_report_saves_json_and_never_builds_the_real_embedder(monkeypatch, tmp_path, capsys):
    from backend.evals import rag_eval

    def forbidden(*a, **k):
        raise AssertionError("a default run must not build the real embedder")

    monkeypatch.setattr(rag_eval, "Embedder", forbidden)
    out = tmp_path / "numbers.json"
    assert ev.main(["--trials", "2", "--save", str(out)]) == 0
    assert "Memory graph eval" in capsys.readouterr().out
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in saved and saved["seed_source"] == "oracle"


def test_the_real_embedder_is_used_only_when_asked_for_and_its_cache_is_saved(monkeypatch, tmp_path):
    from backend.evals import rag_eval

    calls = {}

    class Fake(ev.OfflineEmbedder):
        def __init__(self, cache):
            calls["cache"] = cache

        def save(self):
            calls["saved"] = True

    monkeypatch.setattr(rag_eval, "Embedder", Fake)
    assert ev.main(["--embeddings", "real", "--cache", str(tmp_path / "c.json"), "--trials", "1", "--seed-source", "similarity"]) == 0
    assert calls == {"cache": str(tmp_path / "c.json"), "saved": True}


# ---- PageRank without SciPy (CI failed on `nx.pagerank`: "No module named 'scipy'", PR #185) ------------------------------------------------------------

def test_the_pagerank_gives_the_hand_worked_answer_on_two_connected_facts():
    graph = nx.Graph([("a", "b")])
    rank = ev.personalized_pagerank(graph, {"a": 1.0})
    # x_a = 0.85 * x_b + 0.15, x_b = 0.85 * x_a  =>  x_a = 0.15 / (1 - 0.7225)
    assert rank["a"] == pytest.approx(0.15 / (1 - 0.85 ** 2), abs=1e-9)
    assert rank["b"] == pytest.approx(0.85 * rank["a"], abs=1e-9)
    assert sum(rank.values()) == pytest.approx(1.0, abs=1e-9)


def test_the_pagerank_keeps_all_of_an_unconnected_seeds_rank_on_it_and_none_leaks():
    graph = nx.Graph([("a", "b")])
    graph.add_node("c")
    rank = ev.personalized_pagerank(graph, {"c": 1.0})
    assert rank["c"] == pytest.approx(1.0, abs=1e-9) and rank["a"] < 1e-9 and rank["b"] < 1e-9


def test_the_pagerank_is_even_across_a_symmetric_graph_with_even_seeds_and_follows_the_seed_weights():
    triangle = nx.Graph([("a", "b"), ("b", "c"), ("a", "c")])
    even = ev.personalized_pagerank(triangle, {"a": 1.0, "b": 1.0, "c": 1.0})
    assert all(value == pytest.approx(1 / 3, abs=1e-9) for value in even.values())
    tilted = ev.personalized_pagerank(triangle, {"a": 3.0, "b": 1.0})
    assert tilted["a"] > tilted["b"] > tilted["c"]


def test_the_pagerank_refuses_a_personalization_with_no_weight_and_copes_with_an_empty_graph():
    with pytest.raises(ValueError):
        ev.personalized_pagerank(nx.Graph([("a", "b")]), {})
    with pytest.raises(ValueError):
        ev.personalized_pagerank(nx.Graph([("a", "b")]), {"a": 0.0, "b": -1.0})
    assert ev.personalized_pagerank(nx.Graph(), {"a": 1.0}) == {}


def test_it_agrees_with_networkx_where_networkx_can_run(monkeypatch):
    pytest.importorskip("scipy")            # networkx's own version needs SciPy: only a machine that has it can compare
    facts = ev.build_facts(ev.OfflineEmbedder(), ev.LINKS)
    graph = ev.graph_of(facts)
    for case in ev.CASES:
        weights = {fact.id: 1.0 for fact, _ in ev.seeds_for(None, facts, 3, case, "oracle")}
        ours = ev.personalized_pagerank(graph, weights)
        theirs = nx.pagerank(graph, alpha=0.85, personalization=weights, tol=1e-12, max_iter=500)
        assert max(abs(ours[n] - theirs[n]) for n in graph) < 1e-8, case.name


def test_the_whole_eval_runs_with_scipy_missing_exactly_as_it_does_in_ci(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "scipy", None)            # `import scipy` now raises, as on a machine that installed only requirements.txt
    results = ev.run(ev.OfflineEmbedder(), trials=1, conditions=["perfect", "both"], methods=["onehop", "hops2", "ppr", "ppr_rel"])
    assert results["summary"]["perfect|ppr|3"]["recall"] > results["summary"]["perfect|onehop|3"]["recall"]


def test_requirements_do_not_have_to_grow_for_this_eval():
    """The eval needs networkx (graph structure) and nothing that networkx only uses for PageRank."""
    source = open("backend/evals/memory_graph_eval.py", encoding="utf-8").read()
    assert "nx.pagerank(" not in source, "a call to networkx's PageRank (which needs SciPy) is back"
    requirements = open("requirements.txt", encoding="utf-8").read().lower()
    assert "networkx" in requirements and "scipy" not in requirements
