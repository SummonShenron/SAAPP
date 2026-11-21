"""Would a graph over Sonic's memory facts bring the right extra context, better than the one-hop links it uses now?  (2026-11-21)

THE QUESTION. Recall picks the facts closest to the question (embedding similarity), then `fact_links.linked_extras` adds up to three facts linked to them, one hop, in selection order. A networkx graph over the
same links could do more: reach two hops, rank by how well-connected a fact is to ALL the facts picked (personalized PageRank), and bring more or fewer extras. Does it help, and what does it cost in wrong context?

WHAT IS HELD FIXED. The links. `UserFact.related` is whatever the linking model decided (backend/utils/fact_links.py); here they are authored for a synthetic person, then damaged the ways the real ones can be: some true links
missing, some wrong cross-topic links added (a wrong link puts unrelated context into a reply, which fact_links.py says is worse than a missing one). The seeds are chosen the way recall chooses them: top-N facts by cosine
similarity to the question. The baseline is the REAL `fact_links.linked_extras`, not a copy of it.

WHAT IS SCORED, per question: `needs` are the facts a good reply needs in its context; `ok` are extras that are defensible and not held against a method; any other extra is WRONG context. Reported per method and per
link condition: how much of `needs` reached the reply (recall), how many questions got ALL of it (complete), extras per question and wrong extras per question.

EMBEDDINGS. `--embeddings offline` (the default) is a hashed bag of words: free, deterministic, no network; it only decides which facts are the seeds, which is not what is being compared. `--embeddings real` uses the same
model the app does through rag_eval.Embedder (cached on disk, so a rerun costs nothing): about 120 short texts, a fraction of a cent, but it IS a live call, so it is never the default and tests never use it.

    python -m backend.evals.memory_graph_eval                      # offline, free
    python -m backend.evals.memory_graph_eval --embeddings real --cache eval_results/memory_graph_embeddings.json
    python -m backend.evals.memory_graph_eval --seeds 3 --trials 20 --save eval_results/memory_graph.json
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx

from backend.utils import fact_links
from backend.utils.memory_utils import UserFact

# ---- the synthetic person ----------------------------------------------------------------------------------------------------------------------------

FACTS: Dict[str, str] = {
    # work: an employer hub, a project with a chain, people
    "w1": "Works at Principal as a software engineer.",
    "w2": "Is on the PAG UI team at Principal.",
    "w3": "Is leading the PAG UI redesign at Principal.",
    "w4": "The PAG UI redesign is built with React and TypeScript.",
    "w5": "The PAG UI redesign is stuck waiting on design tokens from the design team.",
    "w6": "Has a coworker named Dana who leads the design team at Principal.",
    "w7": "Writes a weekly report every Friday for his manager at Principal.",
    "w8": "His manager at Principal is Marcus.",
    "w9": "The Principal office is in Des Moines, Iowa.",
    # own projects
    "p1": "Is building a recipe app as a side project.",
    "p2": "The recipe app is written in Python with FastAPI.",
    "p3": "The recipe app stores its recipes in SQLite.",
    "p4": "Wants to launch the recipe app by March.",
    "p5": "Is building a bridge card game for friends in Rust.",
    "p6": "The bridge card game still needs an AI opponent.",
    # family
    "f1": "Has a sister named Priya who lives in Denver.",
    "f2": "Priya is getting married in June.",
    "f3": "Is giving a toast at Priya's wedding.",
    "f4": "Is flying to Denver for the wedding on June 12.",
    "f5": "His parents live in Omaha, Nebraska.",
    "f6": "Visits his parents in Omaha every Thanksgiving.",
    # health
    "h1": "Has a sore left knee.",
    "h2": "Sees a physical therapist named Dr. Okafor for the knee.",
    "h3": "Is training for a marathon in April.",
    "h4": "The marathon training plan has a long run every Saturday.",
    "h5": "Avoids running on pavement because of the knee.",
    # the trip
    "t1": "Is learning Spanish for a trip to Mexico City in March.",
    "t2": "Practices Spanish on Duolingo for twenty minutes every morning.",
    "t3": "The Mexico City trip is booked for March 8 to 15.",
    "t4": "Is staying in Roma Norte during the Mexico City trip.",
    # the dog
    "d1": "Has a dog named Max.",
    "d2": "Max is a golden retriever, four years old.",
    "d3": "Max sees the vet Dr. Lin on Tuesdays.",
    "d4": "Max is allergic to chicken.",
    # home
    "o1": "Lives in Des Moines, Iowa.",
    "o2": "Rents a two-bedroom apartment near the East Village.",
    "o3": "The apartment lease ends in August.",
    # facts about nothing else (no links)
    "k1": "Drinks oat milk lattes.",
    "k2": "Prefers dark mode in every app.",
    "k3": "Reads science fiction, mostly Becky Chambers.",
}

# What the linking model should have made: facts about the SAME real thing (employer, project, person, trip, health issue). Symmetric.
LINKS: List[Tuple[str, str]] = [
    ("w1", "w2"), ("w1", "w3"), ("w1", "w6"), ("w1", "w7"), ("w1", "w8"), ("w1", "w9"), ("w2", "w3"), ("w3", "w4"), ("w3", "w5"), ("w5", "w6"), ("w7", "w8"),
    ("p1", "p2"), ("p1", "p3"), ("p1", "p4"), ("p2", "p3"), ("p5", "p6"),
    ("f1", "f2"), ("f1", "f3"), ("f2", "f3"), ("f2", "f4"), ("f5", "f6"),
    ("h1", "h2"), ("h1", "h5"), ("h3", "h4"), ("h3", "h5"),
    ("t1", "t2"), ("t1", "t3"), ("t3", "t4"),
    ("d1", "d2"), ("d1", "d3"), ("d1", "d4"),
    ("o1", "o2"), ("o2", "o3"),
]


@dataclass
class Case:
    name: str
    question: str
    needs: Set[str]
    ok: Set[str] = field(default_factory=set)
    kind: str = "one_hop"          # one_hop | two_hop | hub | isolated | cross
    direct: Set[str] = field(default_factory=set)      # the facts a good embedding match picks for this question on its own: the seeds of `--seed-source oracle`


CASES: List[Case] = [
    Case("stack", "What is the redesign built with?", {"w4", "w3"}, {"w1", "w2"}, direct={"w4"}),
    Case("stuck", "Why is the redesign stuck?", {"w5", "w3", "w6"}, {"w1"}, direct={"w5"}),
    Case("dana", "What is Dana's team holding up for me?", {"w6", "w5", "w3"}, {"w1"}, "two_hop", direct={"w6"}),
    Case("manager", "Who is my manager and what do I send him?", {"w8", "w7", "w1"}, set(), direct={"w8", "w7"}),
    Case("work_going", "How is work going lately?", {"w1", "w3", "w7"}, {"w2", "w5", "w8", "w6"}, "hub", direct={"w1"}),
    Case("friday_report", "What should go in this Friday's report?", {"w7", "w3", "w8"}, {"w1", "w5", "w4"}, "two_hop", direct={"w7"}),
    Case("office", "Where is the Principal office?", {"w9", "w1"}, {"w2"}, direct={"w9"}),
    Case("recipe_lang", "What language is the recipe app in?", {"p2", "p1"}, {"p3"}, direct={"p2"}),
    Case("recipe_launch", "When do I want the recipe app live and where does it keep its recipes?", {"p4", "p3", "p1"}, {"p2"}, direct={"p4", "p3"}),
    Case("bridge", "What does the bridge card game still need?", {"p6", "p5"}, set(), direct={"p6"}),
    Case("toast", "What should I say in my toast at the wedding?", {"f3", "f2", "f1"}, {"f4"}, direct={"f3"}),
    Case("wedding_trip", "How do I get to Priya's wedding?", {"f4", "f2", "f1"}, {"f3"}, direct={"f4"}),
    Case("thanksgiving", "What are the plans for Thanksgiving in Omaha?", {"f6", "f5"}, set(), direct={"f6"}),
    Case("june", "Is anything planned for June?", {"f2", "f4"}, {"f1", "f3"}, direct={"f2", "f4"}),
    Case("long_run", "Can I do the Saturday long run?", {"h4", "h3", "h5", "h1"}, {"h2"}, "two_hop", direct={"h4"}),
    Case("knee_help", "Who is helping with my knee?", {"h2", "h1"}, {"h5"}, direct={"h2"}),
    Case("realistic", "Is the marathon plan realistic given my knee?", {"h3", "h1", "h5", "h4"}, {"h2"}, "one_hop", direct={"h3", "h1"}),
    Case("mexico_prep", "What should I do before the Mexico City trip?", {"t1", "t3", "t2"}, {"t4"}, direct={"t1"}),
    Case("roma", "Where am I staying in Mexico City?", {"t4", "t3"}, set(), direct={"t4"}),
    Case("dog_food", "What can Max eat?", {"d4", "d1"}, {"d2"}, direct={"d4"}),
    Case("dog_age", "How old is my dog?", {"d2", "d1"}, set(), direct={"d2"}),
    Case("lease", "When does my lease end and where do I live?", {"o3", "o2", "o1"}, set(), "two_hop", direct={"o3"}),
    Case("latte", "What do I drink?", {"k1"}, set(), "isolated", direct={"k1"}),
    Case("theme", "Which theme do I like?", {"k2"}, set(), "isolated", direct={"k2"}),
    Case("book", "What book should I read next?", {"k3"}, set(), "isolated", direct={"k3"}),
    Case("cross", "How does the redesign fit with my marathon schedule?", {"w3", "h3"}, {"w1", "w4", "w5", "h4", "h5"}, "cross", direct={"w3", "h3"}),
]

CLUSTER_OF = {fact_id: fact_id[0] for fact_id in FACTS}
STOPWORDS = frozenset("a an the of to in on at for and or is are be it its my me i do does did what which who when where how with by as from this that his her their was were has have had can should".split())


# ---- embeddings --------------------------------------------------------------------------------------------------------------------------------------

class OfflineEmbedder:
    """A hashed bag of words (512 dimensions): free and deterministic. It only decides which facts are the seeds; it is not what the eval compares."""

    DIM = 512

    def _vec(self, text: str) -> List[float]:
        vector = [0.0] * self.DIM
        for word in re.findall(r"[a-z0-9']+", text.lower()):
            word = word.rstrip("s") if len(word) > 3 else word
            if word in STOPWORDS or len(word) < 2:
                continue
            digest = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            vector[digest % self.DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector] if norm else vector

    def docs(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def query(self, text: str) -> List[float]:
        return self._vec(text)

    def save(self) -> None:
        pass


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---- the facts, links and the damage ----------------------------------------------------------------------------------------------------------------------

def build_facts(embedder: Any, links: Sequence[Tuple[str, str]]) -> List[UserFact]:
    """Real UserFact objects (what the app's own code works on), with embeddings and `related` set from `links` (symmetric, capped like the app's)."""
    now = datetime.now(timezone.utc).isoformat()
    ids = list(FACTS)
    vectors = embedder.docs([FACTS[i] for i in ids])
    facts = {i: UserFact(id=i, username="eval", category="context", fact=FACTS[i], created_at=now, updated_at=now, embedding=v) for i, v in zip(ids, vectors)}
    for a, b in links:
        if a in facts and b in facts and b not in facts[a].related and len(facts[a].related) < fact_links.MAX_LINKS_PER_FACT and len(facts[b].related) < fact_links.MAX_LINKS_PER_FACT:
            facts[a].related.append(b)
            facts[b].related.append(a)
    return list(facts.values())


def damage(links: Sequence[Tuple[str, str]], missing: float, noisy: float, rng: random.Random) -> List[Tuple[str, str]]:
    """The links as a real linking model could leave them: each true link lost with probability `missing`, and `noisy` x (the number of true links) WRONG links added between facts of different topics."""
    kept = [pair for pair in links if rng.random() >= missing]
    ids = list(FACTS)
    present = {frozenset(p) for p in kept}
    wrong = max(0, round(len(links) * noisy))
    attempts = 0
    while wrong and attempts < 2000:
        attempts += 1
        a, b = rng.sample(ids, 2)
        if CLUSTER_OF[a] == CLUSTER_OF[b] or frozenset((a, b)) in present:
            continue
        kept.append((a, b))
        present.add(frozenset((a, b)))
        wrong -= 1
    return kept


# ---- the methods ---------------------------------------------------------------------------------------------------------------------------------------

def seeds_for(question_vector: Sequence[float], facts: Sequence[UserFact], n: int, case: Optional[Case] = None, source: str = "oracle") -> List[Tuple[UserFact, float]]:
    """The facts recall would start from. `oracle`: the ones authored as direct matches for the question (so the comparison is about EXPANSION, not about how good an embedding model is). `similarity`: recall's own
    choice, the top n facts by cosine similarity to the question (confidence is 1 for all of them here)."""
    if source == "oracle" and case is not None:
        by_id = {f.id: f for f in facts}
        return [(by_id[i], 1.0) for i in sorted(case.direct) if i in by_id]
    scored = sorted(((cosine(question_vector, f.embedding), f) for f in facts if f.embedding), key=lambda pair: pair[0], reverse=True)
    return [(f, s) for s, f in scored[:n]]


def graph_of(facts: Sequence[UserFact]) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(f.id for f in facts)
    for fact in facts:
        for other in fact.related:
            graph.add_edge(fact.id, other)
    return graph


def m_none(seeds, facts, graph, k):
    return []


def m_onehop(seeds, facts, graph, k):
    """What the app does now: fact_links.linked_extras, not a copy of it."""
    return [f.id for f in fact_links.linked_extras([f for f, _ in seeds], facts, limit=k)]


def m_hops2(seeds, facts, graph, k):
    """Everything within two hops of any seed, nearest first, then the one reached from more seeds; at most k."""
    seed_ids = [f.id for f, _ in seeds]
    score: Dict[str, float] = {}
    for sid in seed_ids:
        if sid not in graph:
            continue
        for node, distance in nx.single_source_shortest_path_length(graph, sid, cutoff=2).items():
            if node not in seed_ids:
                score[node] = score.get(node, 0.0) + (1.0 if distance == 1 else 0.4)
    return [n for n, _ in sorted(score.items(), key=lambda item: (-item[1], item[0]))[:k]]


def _ppr_ranked(seeds, graph) -> List[Tuple[float, str]]:
    """Personalized PageRank from the seeds (weighted by their similarity): (rank, fact id) best first, for the facts in a seed's connected component only. The solver leaves a few millionths of rank on facts
    that are not connected to anything the seeds touch; only the component is ranked, so an isolated fact gets no extras however the numbers fall."""
    seed_ids = {f.id for f, _ in seeds}
    reachable: Set[str] = set()
    for sid in seed_ids:
        if sid in graph:
            reachable |= nx.node_connected_component(graph, sid)
    weights = {f.id: max(s, 1e-6) for f, s in seeds if f.id in graph}
    if not weights:
        return []
    rank = nx.pagerank(graph, alpha=0.85, personalization=weights, tol=1e-12, max_iter=500)
    return sorted(((r, n) for n, r in rank.items() if n in reachable and n not in seed_ids), key=lambda pair: (-pair[0], pair[1]))


def m_ppr(seeds, facts, graph, k):
    """The k best-connected facts to the whole set of seeds, however many hops away: always fills the quota when there is a component to take from."""
    return [n for _, n in _ppr_ranked(seeds, graph)[:k]]


PPR_FLOOR = 0.4      # ppr_rel keeps an extra only when its rank is at least this share of the best extra's


def m_ppr_rel(seeds, facts, graph, k):
    """Personalized PageRank with a relevance floor: an extra must be at least PPR_FLOOR of the best one's rank, so a question with little to add gets little (and an isolated fact gets nothing)."""
    ranked = _ppr_ranked(seeds, graph)
    if not ranked:
        return []
    best = ranked[0][0]
    return [n for r, n in ranked[:k] if r >= PPR_FLOOR * best]


METHODS: Dict[str, Callable[..., List[str]]] = {
    "none": m_none,
    "onehop": m_onehop,
    "hops2": m_hops2,
    "ppr": m_ppr,
    "ppr_rel": m_ppr_rel,
}
CONDITIONS: Dict[str, Tuple[float, float]] = {          # (missing share, wrong links as a share of the true links)
    "perfect": (0.0, 0.0),
    "missing30": (0.3, 0.0),
    "noisy25": (0.0, 0.25),
    "both": (0.3, 0.25),
}


# ---- scoring ---------------------------------------------------------------------------------------------------------------------------------------------

@dataclass
class Outcome:
    recall: float
    complete: bool
    extras: int
    wrong: int
    seed_recall: float


def score_case(case: Case, seed_ids: Sequence[str], extra_ids: Sequence[str]) -> Outcome:
    context = set(seed_ids) | set(extra_ids)
    wrong = [e for e in extra_ids if e not in case.needs and e not in case.ok]
    return Outcome(
        recall=len(context & case.needs) / len(case.needs), complete=case.needs <= context, extras=len(extra_ids), wrong=len(wrong),
        seed_recall=len(set(seed_ids) & case.needs) / len(case.needs),
    )


def aggregate(outcomes: Sequence[Outcome]) -> Dict[str, float]:
    n = max(len(outcomes), 1)
    return {
        "recall": sum(o.recall for o in outcomes) / n, "complete": sum(o.complete for o in outcomes) / n, "extras": sum(o.extras for o in outcomes) / n,
        "wrong": sum(o.wrong for o in outcomes) / n, "seed_recall": sum(o.seed_recall for o in outcomes) / n,
    }


def run(embedder: Any, seeds: int = 3, trials: int = 8, ks: Sequence[int] = (3, 6), conditions: Optional[Sequence[str]] = None, methods: Optional[Sequence[str]] = None, seed_source: str = "oracle") -> Dict[str, Any]:
    """The whole grid: condition x method x k. A damaged condition is averaged over `trials` random damages (the same damages for every method), the perfect one is exact."""
    conditions = list(conditions or CONDITIONS)
    methods = list(methods or METHODS)
    query_vectors = {c.name: embedder.query(c.question) for c in CASES}
    results: Dict[str, Any] = {"seeds": seeds, "seed_source": seed_source, "trials": trials, "ks": list(ks), "grid": {}, "by_kind": {}}
    for condition in conditions:
        missing, noisy = CONDITIONS[condition]
        runs = 1 if (missing == 0 and noisy == 0) else trials
        for trial in range(runs):
            rng = random.Random(1000 + trial)
            facts = build_facts(embedder, damage(LINKS, missing, noisy, rng))
            graph = graph_of(facts)
            for case in CASES:
                chosen = seeds_for(query_vectors[case.name], facts, seeds, case, seed_source)
                seed_ids = [f.id for f, _ in chosen]
                for method in methods:
                    for k in (ks if method != "none" else (0,)):
                        extra_ids = METHODS[method](chosen, facts, graph, k)
                        key = f"{condition}|{method}|{k}"
                        results["grid"].setdefault(key, []).append(score_case(case, seed_ids, extra_ids))
                        results["by_kind"].setdefault(f"{key}|{case.kind}", []).append(score_case(case, seed_ids, extra_ids))
    results["summary"] = {key: aggregate(rows) for key, rows in results["grid"].items()}
    results["kind_summary"] = {key: aggregate(rows) for key, rows in results["by_kind"].items()}
    del results["grid"], results["by_kind"]
    return results


def format_report(results: Dict[str, Any]) -> str:
    lines = [f"Memory graph eval: {len(CASES)} questions, {len(FACTS)} facts, seeds = {"the authored direct matches" if results.get("seed_source") == "oracle" else "top " + str(results["seeds"]) + " by similarity"}, {results['trials']} damage trials per damaged condition.",
             "recall = share of the needed facts that reached the reply; complete = questions with ALL of them; wrong = extras that are neither needed nor defensible.", ""]
    for condition in dict.fromkeys(key.split("|")[0] for key in results["summary"]):
        lines.append(f"== links: {condition} ==")
        lines.append(f"{'method':<10}{'k':>3}{'recall':>9}{'complete':>10}{'extras/q':>10}{'wrong/q':>9}")
        for key, row in results["summary"].items():
            c, method, k = key.split("|")
            if c == condition:
                lines.append(f"{method:<10}{k:>3}{row['recall']:>9.2f}{row['complete']:>10.2f}{row['extras']:>10.2f}{row['wrong']:>9.2f}")
        lines.append("")
    kinds = sorted({key.split("|")[3] for key in results["kind_summary"]})
    lines.append("== recall by question kind, links: perfect, k=3 ==")
    lines.append(f"{'method':<10}" + "".join(f"{kind:>10}" for kind in kinds))
    for method in METHODS:
        kk = 0 if method == "none" else 3
        row = [results["kind_summary"].get(f"perfect|{method}|{kk}|{kind}") for kind in kinds]
        if all(r is not None for r in row):
            lines.append(f"{method:<10}" + "".join(f"{r['recall']:>10.2f}" for r in row))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare one-hop linked facts with graph expansion over memory facts.")
    parser.add_argument("--embeddings", choices=("offline", "real"), default="offline", help="offline = hashed words (free); real = the app's embedding model (a live call, cached)")
    parser.add_argument("--cache", default="", help="on-disk cache of real embeddings")
    parser.add_argument("--seeds", type=int, default=3, help="with --seed-source similarity: how many top facts count as the seeds (recall's own limit is 5)")
    parser.add_argument("--seed-source", choices=("oracle", "similarity"), default="oracle", help="oracle = the authored direct matches (isolates expansion); similarity = recall's own choice by embedding")
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument("--save", default="", help="write the numbers as JSON")
    args = parser.parse_args(argv)
    if args.embeddings == "real":
        from backend.evals.rag_eval import Embedder

        embedder: Any = Embedder(args.cache)
    else:
        embedder = OfflineEmbedder()
    results = run(embedder, seeds=args.seeds, trials=args.trials, seed_source=args.seed_source)
    if args.embeddings == "real":
        embedder.save()
    print(format_report(results))
    if args.save:
        os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=1)
    return 0


if __name__ == "__main__":
    from backend.utils import llm_usage

    llm_usage.mark_as_eval()      # a real-embedding run is counted as eval spend, never toward the budget that can pause chat
    sys.exit(main())
