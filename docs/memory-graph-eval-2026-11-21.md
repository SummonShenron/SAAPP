# Memory graph: would networkx over memory facts beat one-hop links? (2026-11-21)

Jack asked whether the networkx knowledge graphs can be pointed at Sonic's memory embeddings, and what entity nodes built from memory would cost. This is the eval (built, run offline, free) and the cost estimate (from real prices and 30 days of real usage).

## What was built

`backend/evals/memory_graph_eval.py` (25 tests in `backend/tests/test_memory_graph_eval.py`). A synthetic person with 40 facts in nine topics (an employer hub, a project chain, own projects, family, health, a trip, a dog, home, and facts about nothing else), 33 authored links of the kind `fact_links` should make, and 26 questions, each with the facts a good reply NEEDS, defensible extras that are not held against a method, and the facts a good embedding match starts from. The baseline is the app's real `fact_links.linked_extras` (one hop, cap 3), not a copy. The challengers are two-hop expansion and personalized PageRank (with and without a relevance floor), at 3 and 6 extras. The links are then damaged the ways real ones can be (30% of true links missing, wrong cross-topic links added at 25% of the link count, both), averaged over 30 random damages.

Run it: `python -m backend.evals.memory_graph_eval` (offline, free). `--embeddings real --cache <file>` uses the real embedding model for `--seed-source similarity` runs (about 800 tokens, a hundredth of a cent, a live call, never the default).

## Results (offline, starting facts authored, k = 3 extras)

| links | method | recall of needed facts | all needed facts present | extras / question | WRONG extras / question |
|---|---|---|---|---|---|
| perfect | none | 0.55 | 0.19 | 0 | 0 |
| perfect | **one-hop (today)** | 0.92 | 0.77 | 1.42 | 0.00 |
| perfect | two hops | 0.95 | 0.85 | 2.27 | 0.38 |
| perfect | PageRank | 1.00 | 1.00 | 2.38 | 0.46 |
| perfect | PageRank + floor | 0.98 | 0.92 | 1.81 | 0.04 |
| both damaged | none | 0.55 | 0.19 | 0 | 0 |
| both damaged | **one-hop (today)** | 0.81 | 0.58 | 1.45 | 0.42 |
| both damaged | two hops | 0.84 | 0.63 | 2.12 | 0.89 |
| both damaged | PageRank | 0.84 | 0.65 | 2.22 | 0.97 |
| both damaged | PageRank + floor | 0.83 | 0.62 | 1.80 | 0.65 |

By question kind (perfect links): one-hop already gets about 0.98 of the needed facts for questions that start next to their answer. The gains are in hub questions ("how is work going?": 0.67 to 1.00 with PageRank) and two-hop questions (0.62 to 1.00 with PageRank, 0.85 with the floor). Isolated facts and cross-topic questions were already fine.

## What it says

1. **Graph expansion helps on the questions where one hop falls short, and only there.** With true links, PageRank with a relevance floor adds 6 points of recall and 15 points of "everything needed is present" for almost no wrong context (0.04 a question).
2. **The bigger lever is the links, not the expansion.** Losing 30% of the true links costs one-hop 11 points of recall (0.92 to 0.81), more than any expansion method wins back (with 30% of links missing the best of them recovers 5 points, 0.81 to 0.86; with both damages, 3 points). The 30%-missing rows are in `eval_results/memory_graph_oracle.json`. Expansion cannot reach a fact whose link was never made.
3. **With damaged links every graph method brings more wrong context than today's one-hop** (0.42 to 0.65 to 0.97 wrong extras a question), which fact_links.py calls worse than a missing one. The floor variant is the safest of the three.
4. **What this cannot say.** The person, the links and the "needs" were authored by Claude, and 26 questions is small; the seeds are the authored direct matches (so the comparison is about expansion, not retrieval); real links are made by a model and are probably neither as complete nor as clean as the "perfect" row. The real-embedding run, and a run on a copy of real facts, would be the next check; neither has been done.

Two bugs in the harness were found and fixed while building it and are pinned by tests: PageRank left a few millionths of rank on unconnected facts, which made every isolated question come back with three wrong extras (the first PageRank table was wrong because of it), and one question was mislabelled "two hops" when everything it needs was one hop away.

## Entity nodes from memory: what they would cost

Measured over the last 30 days (read-only, counts only): one active person with 923 facts, 558 new facts in 30 days (18.6 a day), 86 characters a fact. The calls that already run on each saved fact (gemini-3.1-flash-lite, $0.25 in / $1.50 out per million tokens, from `LLM_PRICES`):

| existing call | calls in 30 days | avg tokens in / out | cost |
|---|---|---|---|
| `fact_links.judge_links_with_things` | 879 | 530 / 39 | about $0.17 |
| `fact_links.verify_pair` | 1,950 | 325 / 5 | about $0.17 |
| **fact linking in total** | | | **about $0.34 a month, 0.06 cents a new fact** |

An entity extraction call per new fact (the existing `RELATIONSHIP_PROMPT` pattern: a short instruction, the fact, JSON out), estimated from those token sizes, not measured:

| design | tokens in / out | per new fact | per month at 18.6 facts a day | one-off backfill of 923 facts |
|---|---|---|---|---|
| minimal prompt | 300 / 60 | $0.00017 | $0.09 | $0.15 |
| plus a shortlist of existing entities chosen by embedding | 400 / 80 | $0.00022 | $0.12 | $0.20 |
| plus the whole entity list in the prompt | 1,200 / 80 | $0.00042 | $0.23 | $0.39 |
| plus a verify call per new edge (like `verify_pair`, about 3 an edge set) | +975 / +15 | +$0.00027 | +$0.15 | +$0.25 |

So entity nodes would add roughly **$0.10 to $0.40 a month** at today's one heavy user, against $0.34 a month for the linking that exists now: about 30 to 110% on top of the per-save memory cost, and well under a dollar for the one-off backfill. Reading is free: the graph is built in memory from stored edges (a few hundred nodes, milliseconds), with no model call per turn. Embedding the entity names is a rounding error (a few hundred three-word strings, about $0.0001).

Things that scale or change the number:
- **Per fact, not per person:** at 1,000 people each adding 5 facts a day it is about 5,000 facts a day, so about $0.85 to $2.10 a day ($25 to $63 a month) for the extraction alone, against the app's $5 a day cap. Linking is already about 0.06 cents a fact on top.
- **The model's price moves:** `gemini-3.1-flash-lite` has a shutdown date of 2027-05-07; its replacement `gemini-3.5-flash-lite` lists at $0.30 in / $2.50 out, which makes output-heavy calls about 67% dearer.
- **The cost that is not dollars:** every extra model call per save is another thing that can be wrong. A wrong entity edge puts unrelated context into a reply, and memory is personal, so the graph has to stay per person and an erased fact must take its edges with it (the library graph already deletes by source file; memory would need the same, and `memory_erase.py` would have to know about it).

## Real embeddings (run 2026-11-21, the app's own embedding model, starting facts chosen by similarity)

The starting facts are now what recall would really pick (top N by cosine), not authored. The same 40 facts and 26 questions, perfect links, k = 3 extras; "found" is recall with no extras at all.

| similarity seeds | found by retrieval alone (recall / all present) | one-hop today (recall / all present / wrong per question) | best graph method (recall / all present / wrong) |
|---|---|---|---|
| 1 | 0.44 / 0.12 | 0.88 / 0.65 / 0.00 | PageRank 0.97 / 0.92 / 0.46; with floor 0.91 / 0.73 / 0.04 |
| 2 | 0.69 / 0.35 | 0.96 / 0.88 / 0.38 | with floor 0.98 / 0.96 / 0.54 |
| 3 | 0.78 / 0.42 | 1.00 / 1.00 / 0.88 | two hops or PageRank 0.99 / 0.96 / 1.19 (no gain) |
| 5 (recall's own limit) | 0.87 / 0.65 | 1.00 / 1.00 / 1.92 | 0.97 to 0.99 / 0.92 to 0.96 / 2.0 to 2.2 (no gain) |

With links damaged (30% missing and wrong links added) the picture is the same or worse for the graph: at 1 to 2 starting facts the best method gains 0 to 4 points of recall for roughly double the wrong context, and at 3 or 5 starting facts it loses to one-hop.

What this says: **graph expansion only matters when retrieval finds one or two of the facts a question needs, and it never helps once retrieval finds three or more.** At recall's real limit of five starting facts, today's one-hop is already complete on this data and the graph methods add only wrong context. Caveat that cuts the other way: this pool is 40 facts, so top-5 covers a large share of it; in a real list of 900 facts, retrieval finds fewer of the needed facts and the one- and two-seed rows are probably closer to the truth. That cannot be settled with a made-up person; it needs a copy of Jack's real fact list and real questions with the needed facts marked.

Cost of this run: about 800 tokens of embeddings (a fraction of a cent). The vectors are cached in `eval_results/memory_graph_embeddings.json` (700 KB; it should be gitignored), so reruns are free. Numbers: `eval_results/memory_graph_real_s{1,2,3,5}.json`.

## Recommendation

Do not build entity nodes, and do not build graph expansion yet either. Two evals now agree: link quality matters more than the expansion method, and with real embeddings at recall's limit of five starting facts, one-hop is already as good as anything tried and brings less wrong context. The only case for a graph is a real fact list where retrieval often finds just one or two of what a question needs, and that has not been measured. The next step, if wanted, is to score Jack's real facts: take a copy of the fact list, write 15 to 20 real questions, mark the facts each needs, and run this same harness on them (the harness takes facts and cases as data, so that is a small change). Entity nodes only if a real failure shows the same entity under different names going unlinked.
