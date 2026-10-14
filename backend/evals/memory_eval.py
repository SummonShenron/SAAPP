"""Does Sonic's long-term memory actually bring back what it should, and nothing else? Measured with the real embeddings and model.

    python -m backend.evals.memory_eval                       # every part
    python -m backend.evals.memory_eval --part recall         # only "is the right memory found?"
    python -m backend.evals.memory_eval --part write --runs 5 # only "does the saved note keep the detail?"
    python -m backend.evals.memory_eval --query-policy latest # the app today; "context" adds the previous user message
    python -m backend.evals.memory_eval --list
    python -m backend.evals.memory_eval --save eval_results/memory_before.json

FOUR PARTS, each one a place memory can quietly go wrong
- recall      A made-up person's memories (plus unrelated and near-topic filler, so the right one has to win) and questions about
              them: asked directly, asked in different words, asked as a short follow-up that only makes sense with the turn
              before it, asked when nothing relevant is stored, and asked when ANOTHER user stored the answer. Run through the
              app's own retrieval (memory_search.retrieve_relevant_memory_context, same top-k and threshold), so a change to either
              shows up here. The report also sweeps the similarity threshold, from the same hits, to show where the right memory
              stops being separable from the noise.
- write       Scripted turns through the app's own summarising step (app_utils.summarize_turn): does the saved one-sentence note
              keep the name, date or number the user gave, does small talk save nothing, and can the note be found again?
- compaction  Related notes spread over months through the real consolidation prompt: do the specifics, and the order they
              happened in, survive, and is nothing invented?
- dedup       Pairs of notes that are, or are not, the same thing in different words, with their similarity next to the two
              thresholds compaction merges at. If true repeats sit below the bar, compaction never merges them.

NOTHING REAL IS TOUCHED. The memories live in an in-memory store that stands in for Atlas Vector Search (cosine, with Atlas's
score scale, (1 + cosine) / 2, per its documentation), and no user, fact or chunk in the database is read or written. Only the
embedding and the small model are called, for a few cents in all. The person is invented; none of it is a real user's data.

SCORING IS MECHANICAL and tested without any model (backend/tests/test_memory_eval.py). It is not part of the test suite: run it
before changing how memory is written, searched or compacted, and again after.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

from backend.services import memory_search as ms
from backend.utils.embedding_utils import cosine_similarity

Embed = Callable[[str], Optional[List[float]]]

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
PERSONA = "eval_persona"
STRANGER = "eval_stranger"
PARTS = ("recall", "write", "compaction", "dedup")
POLICIES = ("latest", "context")
SWEEP_THRESHOLDS = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
TIER1_SIMILARITY = 0.85  # memory_compaction.DEFAULT_SIMILARITY_THRESHOLD
TIER2_SIMILARITY = 0.85  # memory_compaction.DEFAULT_META_SIMILARITY_THRESHOLD


# ---------------------------------------------------------------------------------------------------------------------
# The invented person's memories, in the form the app writes them (one third-person sentence about the user).
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Memory:
    key: str
    text: str
    days_ago: int
    owner: str = PERSONA


MEMORIES: Tuple[Memory, ...] = (
    Memory("dog", "Adopted a rescue greyhound named Biscuit in March and is learning how to settle a nervous dog.", 200),
    Memory("wedding", "Said their sister Anika is getting married in Lisbon on June 14th and that they are giving the toast.", 120),
    Memory("allergy", "Is allergic to shellfish and avoids sushi places that fry shrimp tempura in shared oil.", 300),
    Memory("race", "Is training for a half marathon on October 25th and has been struggling with shin splints.", 21),
    Memory("cover_letter", "Asked for help writing a cover letter for a data analyst role at Brightline Health.", 45),
    Memory("rent", "Mentioned that their landlord is raising the rent from $1,850 to $2,100 starting in January.", 30),
    Memory("book", "Finished reading The Overstory and found the ending bleak but loved the tree-ring chapter.", 90),
    Memory("trip", "Is planning a trip to Kyoto in April and wants to avoid the crowded tourist spots.", 14),
    Memory("orchestra", "Plays cello in a community orchestra that rehearses on Wednesday evenings.", 180),
    Memory("move", "Moved from Chicago to Denver last summer for a job.", 250),
    Memory("manager", "Switched to the payments team after a reorg and now reports to a manager named Dev.", 60),
    Memory("roommate", "Had a stressful argument with their roommate Sam about shared dishes.", 7),
    Memory("style", "Prefers answers with short code examples over long explanations.", 150),
    Memory("stranger_cat", "Adopted a rescue cat named Pixel and is worried about introducing it to the other pets.", 40, owner=STRANGER),
)

# Unrelated things a person talks about, so the right memory has to beat a crowd.
FILLER: Tuple[str, ...] = (
    "Asked how to convert a Python list of dictionaries into a pandas DataFrame.",
    "Wanted a recipe for a quick weeknight chickpea curry.",
    "Asked what the difference is between a mutex and a semaphore.",
    "Asked for ideas for a birthday gift for a coworker who likes gardening.",
    "Wanted to know how compound interest works on a savings account.",
    "Asked how to remove a stripped screw from a cabinet hinge.",
    "Asked for a summary of how the French Revolution started.",
    "Wanted help choosing between two standing desks.",
    "Asked how to fix a git merge conflict in a rebase.",
    "Asked why sourdough starter sometimes smells like nail polish remover.",
    "Asked for a good stretching routine for a stiff lower back.",
    "Wanted to understand how TLS certificates are verified.",
    "Asked for suggestions for a podcast about urban design.",
    "Asked how to write a polite email declining a meeting invitation.",
    "Asked how to descale an espresso machine.",
    "Wanted an explanation of the difference between TCP and UDP.",
    "Asked for tips on keeping basil alive on a windowsill.",
    "Asked how to calculate a tip when splitting a bill four ways.",
    "Asked what a good beginner board game for four players would be.",
    "Wanted to know how long cooked rice is safe to keep in the fridge.",
    "Asked how to set up a cron job on a Linux server.",
    "Asked for a short history of the printing press.",
    "Wanted to compare two noise-cancelling headphones for flights.",
    "Asked how to explain recursion to a ten-year-old.",
    "Asked how to patch a small hole in drywall.",
    "Asked what causes the northern lights.",
    "Asked for a template for a weekly team status update.",
    "Wanted to know how to read a nutrition label for added sugar.",
    "Asked how to center a div with CSS grid.",
    "Asked for advice on negotiating a freelance rate.",
    "Wanted a playlist idea for a long drive.",
    "Asked how vaccines train the immune system.",
    "Asked how to get a red wine stain out of a rug.",
    "Wanted to learn the basic rules of chess openings.",
    "Asked what a reasonable emergency fund looks like.",
    "Asked how to write a unit test for a function that calls an API.",
    "Asked for tips on brewing better cold brew coffee.",
    "Wanted to understand why the sky is blue at noon and red at sunset.",
    "Asked how to politely ask a neighbor to turn the music down.",
    "Asked how a bill becomes law in the United States.",
    "Wanted ideas for a low-effort vegetable garden bed.",
    "Asked how to speed up a slow SQL query with an index.",
    "Asked for a reading list on the history of mathematics.",
    "Asked how to travel with a bike on a train.",
    "Asked how to make a sourdough discard pancake.",
    "Wanted to know what makes a good thesis statement.",
    "Asked about the difference between a Roth and a traditional IRA.",
    "Asked how to remove limescale from a showerhead.",
    "Asked for a way to remember the order of the planets.",
    "Asked how to keep a laptop from overheating on a desk.",
    "Wanted to know how to say thank you in Japanese and Portuguese.",
    "Asked how tides work.",
    "Asked for a packing list for a three-day hiking trip.",
    "Wanted to understand how a hash map handles collisions.",
    "Asked how to rewrite a sentence to sound less passive.",
    "Asked what to look for when buying a used bicycle.",
    "Asked how to stop a ceiling fan from wobbling.",
    "Wanted a mnemonic for the cranial nerves.",
    "Asked how to write a good commit message.",
)

# Near the person's topics but not about them, which is what actually crowds a real search.
NEAR_TOPIC: Tuple[str, ...] = (
    "Asked for tips on training a puppy not to pull on the leash.",
    "Asked how to plan a budget for a destination wedding.",
    "Asked how to avoid shin splints when running on pavement.",
    "Asked for a template cover letter for a software engineering role.",
    "Asked what to see in Tokyo in winter.",
    "Asked how rent control rules work in Colorado.",
    "Asked for a good cello warm-up routine.",
    "Asked how to cook a shrimp stir fry for guests.",
    "Asked for a review of a popular novel about climate change.",
    "Asked how to give a good toast at a family event.",
)


# ---------------------------------------------------------------------------------------------------------------------
# Recall probes
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    id: str
    category: str  # direct | paraphrase | follow_up | negative | isolation
    query: str
    want: Tuple[str, ...] = ()  # memory keys that must come back
    forbid: Tuple[str, ...] = ()  # memory keys that must not
    history: Tuple[str, ...] = ()  # the user's earlier messages in this conversation, oldest first
    notes: str = ""


PROBES: Tuple[Probe, ...] = (
    Probe("dog_direct", "direct", "What was my greyhound's name again?", ("dog",)),
    Probe("wedding_direct", "direct", "When is my sister's wedding?", ("wedding",)),
    Probe("rent_direct", "direct", "How much is my rent going up in January?", ("rent",)),
    Probe("book_direct", "direct", "What was that book with the tree-ring chapter?", ("book",)),
    Probe("trip_direct", "direct", "Where am I going in April?", ("trip",)),
    Probe("orchestra_direct", "direct", "What night is my orchestra rehearsal?", ("orchestra",)),
    Probe("dog_paraphrase", "paraphrase", "What's my dog called?", ("dog",), notes="dog for greyhound"),
    Probe("allergy_paraphrase", "paraphrase", "Is it safe for me to order the tempura at that sushi place?", ("allergy",)),
    Probe("race_paraphrase", "paraphrase", "How's my training for the October race going?", ("race",)),
    Probe("job_paraphrase", "paraphrase", "Remind me which company I was writing that application for.", ("cover_letter",)),
    Probe("move_paraphrase", "paraphrase", "Why did I end up living in Colorado?", ("move",)),
    Probe("boss_paraphrase", "paraphrase", "Who is my boss these days?", ("manager",)),
    Probe("style_paraphrase", "paraphrase", "How do I like my coding answers written?", ("style",)),
    Probe("race_follow_up", "follow_up", "Do you think my leg will hold up?", ("race",),
          history=("I'm so nervous about the race this month.",), notes="'my leg' only means the shin splints given the race turn"),
    Probe("trip_follow_up", "follow_up", "Somewhere quieter than the usual spots, please.", ("trip",),
          history=("Can you help me plan my spring trip?",), notes="needs the previous turn to know it is about the April trip"),
    Probe("toast_follow_up", "follow_up", "Make it funnier.", ("wedding",),
          history=("Help me draft the toast I have to give.",), notes="'it' is the toast, whose occasion is in memory"),
    Probe("roommate_follow_up", "follow_up", "Anyway, he did it again this morning.", ("roommate",),
          history=("Sam left the dishes in the sink for the third day.",)),
    Probe("capital_negative", "negative", "What's the capital of Australia?", notes="nothing stored is relevant"),
    Probe("math_negative", "negative", "What's seventeen times twenty-three?", notes="nothing stored is relevant"),
    Probe("poem_negative", "negative", "Write me a four-line poem about autumn rain.", notes="nothing stored is relevant"),
    Probe("pet_isolation", "isolation", "What's my pet's name?", ("dog",), ("stranger_cat",), notes="another user stored a cat; it must never appear"),
    Probe("cat_isolation", "isolation", "Remind me about my cat.", (), ("stranger_cat",), notes="this person has no cat; the other user's must not appear"),
)


# ---------------------------------------------------------------------------------------------------------------------
# The store: Atlas Vector Search stood in for by memory
# ---------------------------------------------------------------------------------------------------------------------


def atlas_score(cosine: float) -> float:
    """Atlas Vector Search reports a cosine index's score as (1 + cosine) / 2, so 0.75 means a cosine of 0.5."""
    return (1.0 + cosine) / 2.0


@dataclass
class Row:
    text: str
    username: str
    embedding: List[float]
    created_at: str
    key: str
    stale: bool = False


class EvalStore:
    """The slice of MongoDBAtlasVectorSearch that memory_search uses, over rows held in memory."""

    def __init__(self, embed: Embed):
        self.embed = embed
        self.rows: List[Row] = []

    def add(self, text: str, username: str, key: str, days_ago: int = 0, stale: bool = False) -> None:
        embedding = self.embed(text)
        if embedding is None:
            raise RuntimeError(f"could not embed: {text[:60]!r}")
        created = (NOW - timedelta(days=days_ago)).isoformat()
        self.rows.append(Row(text, username, embedding, created, key, stale))

    @staticmethod
    def _allowed(row: Row, pre_filter: Optional[Dict[str, Any]]) -> bool:
        pre_filter = pre_filter or {}
        if "username" in pre_filter and row.username != pre_filter["username"]:
            return False
        stale_rule = pre_filter.get("stale")
        if isinstance(stale_rule, dict) and "$ne" in stale_rule and row.stale == stale_rule["$ne"]:
            return False
        return True

    def _similarity_search_with_score(self, embedding, k: int = 4, pre_filter=None, **_: Any):
        scored = [
            (Document(page_content=row.text, metadata={"username": row.username, "created_at": row.created_at, "key": row.key}),
             atlas_score(cosine_similarity(embedding, row.embedding)))
            for row in self.rows if self._allowed(row, pre_filter)
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def similarity_search_with_score(self, query: str, k: int = 4, pre_filter=None, **_: Any):
        embedding = self.embed(query)
        return self._similarity_search_with_score(embedding, k=k, pre_filter=pre_filter) if embedding is not None else []


def cached(embed: Embed) -> Embed:
    memo: Dict[str, Optional[List[float]]] = {}

    def wrapped(text: str) -> Optional[List[float]]:
        if text not in memo:
            memo[text] = embed(text)
        return memo[text]

    return wrapped


def warm(embed: Embed, texts: Sequence[str], workers: int = 8) -> None:
    """Embeds many texts at once into a cached embedder (a real run makes a hundred or so single calls)."""
    unique = list(dict.fromkeys(texts))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(embed, unique))


def build_store(embed: Embed, memories: Sequence[Memory] = MEMORIES, fillers: Sequence[str] = FILLER, near: Sequence[str] = NEAR_TOPIC) -> EvalStore:
    store = EvalStore(embed)
    for memory in memories:
        store.add(memory.text, memory.owner, memory.key, memory.days_ago)
    for i, text in enumerate(fillers):
        store.add(text, PERSONA, "filler", days_ago=2 + i * 3)
    for i, text in enumerate(near):
        store.add(text, PERSONA, "near", days_ago=5 + i * 11)
    return store


# ---------------------------------------------------------------------------------------------------------------------
# Recall: run, score, sweep
# ---------------------------------------------------------------------------------------------------------------------

_AGE_PREFIX = re.compile(r"^\([^)]*\)\s*")


def build_query(policy: str, message: str, history: Sequence[str] = ()) -> str:
    """What gets searched. "latest" is what the app does today: the message alone. "context" adds the user's previous message."""
    if policy == "context" and history:
        return f"{history[-1]}\n{message}"
    return message


def parse_block(block: str) -> List[str]:
    """The recalled lines of a RELEVANT PAST CONTEXT block, without the "- " and without the "(about 3 weeks ago) " prefix."""
    items = []
    for line in (block or "").splitlines():
        if line.startswith("- "):
            items.append(_AGE_PREFIX.sub("", line[2:]).strip())
    return items


@dataclass
class ProbeResult:
    probe: Probe
    policy: str
    passed: bool
    why: str
    returned: List[str]  # the keys of what came back, in order
    noise: int  # recalled lines that were not a wanted memory
    hits: List[Tuple[str, float]] = field(default_factory=list)  # the ten best (key, score), before any threshold


def score_recall(probe: Probe, returned_keys: Sequence[str]) -> Tuple[bool, str]:
    missing = [key for key in probe.want if key not in returned_keys]
    leaked = [key for key in probe.forbid if key in returned_keys]
    if leaked:
        return False, f"brought back another user's memory: {', '.join(leaked)}"
    if missing:
        return False, f"missing {', '.join(missing)} (got {', '.join(returned_keys) or 'nothing'})"
    if probe.category == "negative" and returned_keys:
        return False, f"nothing relevant is stored but it brought back {', '.join(returned_keys)}"
    return True, "ok"


def run_probe(store: EvalStore, probe: Probe, policy: str, embed: Embed, text_to_key: Dict[str, str]) -> ProbeResult:
    query = build_query(policy, probe.query, probe.history)
    embedding = embed(query)
    block = ms.retrieve_relevant_memory_context(store, PERSONA, query, precomputed_embedding=embedding) if embedding is not None else ""
    returned = [text_to_key.get(item, "unknown") for item in parse_block(block)]
    passed, why = score_recall(probe, returned)
    hits = [(text_to_key.get(doc.page_content, "unknown"), round(score, 3))
            for doc, score in store._similarity_search_with_score(embedding, k=10, pre_filter={"username": PERSONA, "stale": {"$ne": True}})] if embedding is not None else []
    noise = len([key for key in returned if key not in probe.want])
    return ProbeResult(probe, policy, passed, why, returned, noise, hits)


def run_recall(embed: Embed, policy: str, store: Optional[EvalStore] = None) -> List[ProbeResult]:
    store = store or build_store(embed)
    text_to_key = {row.text: row.key for row in store.rows}
    return [run_probe(store, probe, policy, embed, text_to_key) for probe in PROBES]


def sweep(results: Sequence[ProbeResult], thresholds: Sequence[float] = SWEEP_THRESHOLDS, top_k: int = 3) -> List[Dict[str, float]]:
    """What the recall numbers would have been at other thresholds, from the hits already recorded (no more model calls).
    At the app's own threshold and k this matches the real run."""
    rows = []
    for threshold in thresholds:
        wanted = [r for r in results if r.probe.want]
        found = 0
        noise_total = 0
        negatives = [r for r in results if r.probe.category == "negative"]
        clean = 0
        for r in results:
            returned = [key for key, score in r.hits if score >= threshold][:top_k]
            if r.probe.want:
                noise_total += len([key for key in returned if key not in r.probe.want])
                if all(key in returned for key in r.probe.want) and not any(key in returned for key in r.probe.forbid):
                    found += 1
            elif r.probe.category == "negative" and not returned:
                clean += 1
        rows.append({
            "threshold": threshold,
            "recall": found / len(wanted) if wanted else 0.0,
            "noise_per_probe": noise_total / len(wanted) if wanted else 0.0,
            "negatives_clean": clean / len(negatives) if negatives else 1.0,
        })
    return rows


def recall_summary(results: Sequence[ProbeResult]) -> Dict[str, Any]:
    by_category: Dict[str, List[bool]] = {}
    for r in results:
        by_category.setdefault(r.probe.category, []).append(r.passed)
    wanted = [r for r in results if r.probe.want]
    return {
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "by_category": {c: (sum(v), len(v)) for c, v in by_category.items()},
        "noise_per_probe": (sum(r.noise for r in wanted) / len(wanted)) if wanted else 0.0,
    }


# ---------------------------------------------------------------------------------------------------------------------
# Write: does the saved note keep what the user said?
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    id: str
    question: str
    answer: str
    details: Tuple[str, ...] = ()  # strings the saved note must keep
    keep: bool = True  # False: small talk, which must save nothing
    probe: str = ""  # a later question the note should answer


TURNS: Tuple[Turn, ...] = (
    Turn("dog", "I adopted a greyhound named Biscuit last March. Any tips for a first-time greyhound owner?",
         "Congratulations! Greyhounds are gentle but need a quiet space to decompress. Give Biscuit a predictable routine, keep walks on a "
         "secure leash because of their prey drive, and let them settle for a couple of weeks before introducing new people.",
         ("Biscuit", "greyhound"), probe="What's my dog called?"),
    Turn("wedding", "My sister Anika's wedding is in Lisbon on June 14 and I have to give the toast. Can you help me draft it?",
         "Of course. A good toast is short: open with a warm line about Anika, tell one specific story, then turn to the couple and raise a "
         "glass. Here is a draft you can tune: ...",
         ("Anika", "Lisbon", "June 14"), probe="When is my sister's wedding and where?"),
    Turn("rent", "My rent is going from $1,850 to $2,100 in January. How do I negotiate it down?",
         "Start by checking comparable listings, then write to your landlord with a polite offer: a longer lease in exchange for a smaller "
         "increase. Here is a short script you can adapt: ...",
         ("2,100",), probe="How much is my rent going up to?"),
    Turn("allergy", "I'm allergic to shellfish. Can I eat tom kha soup?",
         "Tom kha is often made with shrimp paste or fish sauce, so ask whether any shellfish is in the base. If you cannot confirm, "
         "choose a different dish, and carry your medication.",
         ("shellfish",), probe="Is it safe for me to order the shrimp tempura?"),
    Turn("race", "My shin splints are flaring up and my half marathon is on October 25th. Should I rest?",
         "Some rest is smart. Cut back on pavement, ice after runs, and see a physio if the pain is sharp or persists. Cross-train on a bike "
         "to keep your fitness while the shin heals.",
         ("shin splints", "October 25"), probe="How's my race training going?"),
    Turn("manager", "I moved to the payments team after the reorg and my new manager Dev wants weekly one-on-ones. What should I bring?",
         "Bring a short list: what you shipped, what is blocked, one question about priorities, and one thing you want feedback on.",
         ("Dev", "payments"), probe="Who's my boss now?"),
    Turn("thanks", "thanks!", "You're welcome! Let me know if there's anything else.", keep=False),
    Turn("math", "what's 15% of 80?", "15% of 80 is 12.", keep=False),
    Turn("hello", "hey", "Hey! What's on your mind today?", keep=False),
)


def normalize(text: str) -> str:
    return re.sub(r"[\s]+", " ", (text or "").lower().replace("$", "").replace(",", "")).strip()


def score_summary(summary: Optional[str], turn: Turn) -> Tuple[bool, str]:
    if not turn.keep:
        return (summary is None), ("ok" if summary is None else f"saved small talk: {summary!r}")
    if summary is None:
        return False, "judged not worth remembering"
    if re.match(r"^\W*(summary|note)\W*:", summary, re.IGNORECASE):
        return False, f"saved with a label in front: {summary[:40]!r}"
    text = normalize(summary)
    missing = [detail for detail in turn.details if normalize(detail) not in text]
    return (not missing), ("ok" if not missing else f"lost {', '.join(missing)}: {summary!r}")


@dataclass
class WriteResult:
    turn: Turn
    runs: int
    kept_ok: int
    found_ok: int
    samples: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


async def run_write(embed: Embed, runs: int, summarize: Callable[[str, str], Any]) -> List[WriteResult]:
    results = []
    for turn in TURNS:
        result = WriteResult(turn, runs, 0, 0)
        for _ in range(runs):
            try:
                summary = await summarize(turn.question, turn.answer)
            except Exception as exc:  # a failed call counts against the turn, not the run
                result.failures.append(f"call failed: {exc}")
                continue
            ok, why = score_summary(summary, turn)
            result.kept_ok += int(ok)
            if not ok:
                result.failures.append(why)
            elif summary:
                result.samples.append(summary)
            if turn.keep and summary and turn.probe:
                store = build_store(embed, memories=(), fillers=FILLER, near=NEAR_TOPIC)
                store.add(summary, PERSONA, f"turn:{turn.id}", days_ago=1)
                text_to_key = {row.text: row.key for row in store.rows}
                probe = Probe(f"write_{turn.id}", "write", turn.probe, (f"turn:{turn.id}",))
                found = run_probe(store, probe, "latest", embed, text_to_key)
                result.found_ok += int(found.passed)
        results.append(result)
    return results


# ---------------------------------------------------------------------------------------------------------------------
# Compaction: does consolidating keep the specifics and their order?
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cluster:
    id: str
    notes: Tuple[Tuple[str, int], ...]  # (text, days_ago), oldest first
    details: Tuple[str, ...]  # strings the summary must keep
    forbid: Tuple[str, ...] = ()  # strings that would be wrong or invented
    want_facts: bool = False  # a durable fact should be pulled out
    fact_forbid: Tuple[str, ...] = ()


CLUSTERS: Tuple[Cluster, ...] = (
    Cluster("job_change", (
        ("Started a new job at Acme in January as a backend engineer.", 280),
        ("Said work at Acme has been stressful, with long on-call weeks.", 150),
        ("Was laid off from Acme in July and has started looking for work.", 90),
    ), ("Acme", "January", "July"), forbid=("currently works at Acme", "still works at Acme", "still employed"),
        fact_forbid=("works at Acme", "is employed at Acme", "currently employed at Acme", "is a backend engineer at Acme")),
    Cluster("running", (
        ("Ran their first 10k in 58 minutes in March.", 200),
        ("Improved their 10k time to 52 minutes in June.", 120),
        ("Signed up for a half marathon on October 25th.", 30),
    ), ("58", "52", "half marathon"), forbid=("marathon in 58", "first half marathon in March")),
    Cluster("code_style", (
        ("Asked for a short code example instead of a long explanation of decorators.", 100),
        ("Said they prefer concise code snippets over lengthy prose when learning a new library.", 60),
        ("Asked again for just the code with minimal commentary for a pandas question.", 20),
    ), ("code",), want_facts=True, fact_forbid=("senior", "expert", "years of experience")),
)


def score_cluster(summary: str, facts: Sequence[Any], notes_text: str, cluster: Cluster) -> Tuple[bool, str]:
    if normalize(summary) == normalize(notes_text):
        return False, "the model call failed (the summary is just the notes pasted together)"
    text = normalize(summary)
    missing = [d for d in cluster.details if normalize(d) not in text]
    wrong = [f for f in cluster.forbid if normalize(f) in text]
    fact_texts = [normalize(f.get("fact", "")) for f in facts if isinstance(f, dict)]
    bad_facts = [t for t in fact_texts if any(normalize(f) in t for f in cluster.fact_forbid)]
    problems = []
    if missing:
        problems.append(f"lost {', '.join(missing)}")
    if wrong:
        problems.append(f"said {', '.join(wrong)}")
    if bad_facts:
        problems.append(f"promoted a wrong fact: {'; '.join(bad_facts)}")
    if cluster.want_facts and not fact_texts:
        problems.append("pulled out no durable fact")
    return (not problems), ("ok" if not problems else "; ".join(problems) + f": {summary!r}")


@dataclass
class ClusterResult:
    cluster: Cluster
    runs: int
    ok: int
    failures: List[str] = field(default_factory=list)
    ratio: float = 0.0  # summary length over notes length


async def run_compaction(runs: int, summarize_cluster: Callable[[List[dict]], Any]) -> List[ClusterResult]:
    results = []
    for cluster in CLUSTERS:
        chunks = [{"text": text} for text, _ in cluster.notes]
        notes_text = "\n".join(f"- {c['text']}" for c in chunks)
        result = ClusterResult(cluster, runs, 0)
        ratios = []
        for _ in range(runs):
            try:
                out = await summarize_cluster(chunks)
            except Exception as exc:
                result.failures.append(f"call failed: {exc}")
                continue
            summary = out.get("summary", "") if isinstance(out, dict) else ""
            ok, why = score_cluster(summary, out.get("facts", []) if isinstance(out, dict) else [], notes_text, cluster)
            result.ok += int(ok)
            if not ok:
                result.failures.append(why)
            ratios.append(len(summary) / max(len(notes_text), 1))
        result.ratio = sum(ratios) / len(ratios) if ratios else 0.0
        results.append(result)
    return results


# ---------------------------------------------------------------------------------------------------------------------
# Dedup: would compaction merge notes that are really the same?
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    same: bool
    label: str


PAIRS: Tuple[Pair, ...] = (
    Pair("Adopted a rescue greyhound named Biscuit in March.", "Said their dog Biscuit, a rescued greyhound, is settling in well.", True, "same dog, reworded"),
    Pair("Is training for a half marathon on October 25th.", "Is running a half marathon at the end of October and has shin splints.", True, "same race, reworded"),
    Pair("Asked for help writing a cover letter for a data analyst role.", "Wanted feedback on their cover letter for the Brightline Health analyst job.", True, "same application, reworded"),
    Pair("Is allergic to shellfish.", "Avoids shrimp and crab because of an allergy.", True, "same allergy, reworded"),
    Pair("Prefers short code examples over long explanations.", "Likes concise snippets of code rather than lengthy prose.", True, "same preference, reworded"),
    Pair("Adopted a rescue greyhound named Biscuit in March.", "Is planning a trip to Kyoto in April.", False, "unrelated"),
    Pair("Is training for a half marathon on October 25th.", "Is learning Rust by rewriting a small command-line tool.", False, "unrelated"),
    Pair("Plays cello in a community orchestra on Wednesdays.", "Finished reading The Overstory and loved one chapter.", False, "unrelated"),
    Pair("Said their sister is getting married in Lisbon on June 14th.", "Mentioned the rent is going up to $2,100 in January.", False, "unrelated"),
    Pair("Moved from Chicago to Denver last summer for a job.", "Switched to the payments team after a reorg.", False, "both about work, different things"),
    Pair("Is training for a half marathon on October 25th.", "Asked how to avoid shin splints when running on pavement.", False, "same area, different notes"),
)


def judge_pair(cosine: float, same: bool) -> Dict[str, Any]:
    return {
        "cosine": round(cosine, 3),
        "merges_tier1": cosine >= TIER1_SIMILARITY,
        "merges_tier2": cosine >= TIER2_SIMILARITY,
        "right_at_tier1": (cosine >= TIER1_SIMILARITY) == same,
        "right_at_tier2": (cosine >= TIER2_SIMILARITY) == same,
    }


def run_dedup(embed: Embed) -> List[Tuple[Pair, Dict[str, Any]]]:
    out = []
    for pair in PAIRS:
        a, b = embed(pair.a), embed(pair.b)
        if a is None or b is None:
            raise RuntimeError(f"could not embed pair: {pair.label}")
        out.append((pair, judge_pair(cosine_similarity(a, b), pair.same)))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------------------------------------------------


def format_recall(by_policy: Dict[str, List[ProbeResult]]) -> str:
    lines = ["RECALL  (the app's own retrieval: top 3, score >= %.2f)" % ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD]
    for policy, results in by_policy.items():
        summary = recall_summary(results)
        cats = "  ".join(f"{c} {p}/{t}" for c, (p, t) in summary["by_category"].items())
        lines.append(f"  query = {policy:<8} {summary['passed']}/{summary['total']} passed   {cats}   noise lines per question: {summary['noise_per_probe']:.2f}")
    for policy, results in by_policy.items():
        failed = [r for r in results if not r.passed]
        if failed:
            lines.append(f"  failures with query = {policy}:")
            for r in failed:
                best = next((score for key, score in r.hits if key in r.probe.want), None)
                near = f"   (wanted memory scored {best:.2f})" if best is not None else ""
                lines.append(f"    {r.probe.id:<20}{r.probe.category:<11}{r.why}{near}")
    first = next(iter(by_policy.values()))
    lines.append("  threshold sweep (query = %s, top 3): what a different cutoff would give" % next(iter(by_policy)))
    lines.append("    cutoff   found   noise/question   'nothing stored' questions left clean")
    for row in sweep(first):
        mark = "  <- today" if abs(row["threshold"] - ms.DEFAULT_RECALL_SIMILARITY_THRESHOLD) < 1e-9 else ""
        lines.append(f"    {row['threshold']:.2f}     {row['recall']:>4.0%}    {row['noise_per_probe']:>6.2f}           {row['negatives_clean']:>4.0%}{mark}")
    return "\n".join(lines)


def format_write(results: Sequence[WriteResult]) -> str:
    lines = ["WRITE  (the note the app saves for a turn)", "  turn        note kept what it should   found again later"]
    for r in results:
        found = f"{r.found_ok}/{r.runs}" if r.turn.keep and r.turn.probe else "-"
        lines.append(f"  {r.turn.id:<12}{r.kept_ok}/{r.runs:<24}{found}")
        for failure in dict.fromkeys(r.failures):
            lines.append(f"      {failure}")
    lines.append("  example notes: " + " | ".join(r.samples[0] for r in results if r.samples)[:600])
    return "\n".join(lines)


def format_compaction(results: Sequence[ClusterResult]) -> str:
    lines = ["COMPACTION  (the consolidation prompt over notes from different months)", "  cluster       kept the specifics   summary length vs notes"]
    for r in results:
        lines.append(f"  {r.cluster.id:<14}{r.ok}/{r.runs:<19}{r.ratio:>5.0%}")
        for failure in dict.fromkeys(r.failures):
            lines.append(f"      {failure}")
    return "\n".join(lines)


def format_dedup(rows: Sequence[Tuple[Pair, Dict[str, Any]]]) -> str:
    lines = ["DEDUP  (cosine between two notes; compaction merges at %.2f in tier 1 and %.2f in tier 2)" % (TIER1_SIMILARITY, TIER2_SIMILARITY),
             "  cosine  tier1  tier2  should merge?  pair"]
    for pair, judged in rows:
        lines.append(f"  {judged['cosine']:.3f}   {'yes' if judged['merges_tier1'] else 'no ':<5}  {'yes' if judged['merges_tier2'] else 'no ':<5}  {'yes' if pair.same else 'no ':<13}  {pair.label}")
    same = [j for p, j in rows if p.same]
    distinct = [j for p, j in rows if not p.same]
    lines.append(f"  true repeats merged: tier 1 {sum(j['merges_tier1'] for j in same)}/{len(same)}, tier 2 {sum(j['merges_tier2'] for j in same)}/{len(same)}"
                 f"    distinct notes wrongly merged: tier 1 {sum(j['merges_tier1'] for j in distinct)}/{len(distinct)}, tier 2 {sum(j['merges_tier2'] for j in distinct)}/{len(distinct)}")
    if same and distinct:
        lines.append(f"  lowest true repeat {min(j['cosine'] for j in same):.3f}, highest distinct pair {max(j['cosine'] for j in distinct):.3f}")
    return "\n".join(lines)


def to_record(parts: Dict[str, Any]) -> Dict[str, Any]:
    record: Dict[str, Any] = {"at": NOW.isoformat()}
    if "recall" in parts:
        record["recall"] = {
            policy: {"summary": recall_summary(results), "sweep": sweep(results),
                     "results": [{"id": r.probe.id, "category": r.probe.category, "passed": r.passed, "why": r.why, "returned": r.returned, "hits": r.hits} for r in results]}
            for policy, results in parts["recall"].items()
        }
    if "write" in parts:
        record["write"] = [{"turn": r.turn.id, "runs": r.runs, "kept_ok": r.kept_ok, "found_ok": r.found_ok, "failures": r.failures} for r in parts["write"]]
    if "compaction" in parts:
        record["compaction"] = [{"cluster": r.cluster.id, "runs": r.runs, "ok": r.ok, "ratio": r.ratio, "failures": r.failures} for r in parts["compaction"]]
    if "dedup" in parts:
        record["dedup"] = [{"label": p.label, "same": p.same, **j} for p, j in parts["dedup"]]
    return record


def every_text() -> List[str]:
    texts = [m.text for m in MEMORIES] + list(FILLER) + list(NEAR_TOPIC)
    for probe in PROBES:
        for policy in POLICIES:
            texts.append(build_query(policy, probe.query, probe.history))
    for pair in PAIRS:
        texts.extend([pair.a, pair.b])
    for turn in TURNS:
        texts.append(turn.probe)
    return [t for t in texts if t]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score Sonic's long-term memory against the real embeddings and model.")
    parser.add_argument("--part", default="all", choices=("all",) + PARTS)
    parser.add_argument("--runs", type=int, default=3, help="model runs per turn / cluster in the write and compaction parts")
    parser.add_argument("--query-policy", default="both", choices=("both",) + POLICIES, help="what the recall part searches on")
    parser.add_argument("--list", action="store_true", help="list the probes, turns and clusters and exit")
    parser.add_argument("--save", default="", help="write the results to this JSON file")
    args = parser.parse_args(argv)

    if args.list:
        for probe in PROBES:
            print(f"recall      {probe.id:<22}{probe.category:<11}{probe.query}")
        for turn in TURNS:
            print(f"write       {turn.id:<22}{'keep' if turn.keep else 'skip':<11}{turn.question[:70]}")
        for cluster in CLUSTERS:
            print(f"compaction  {cluster.id:<22}{len(cluster.notes)} notes")
        print(f"dedup       {len(PAIRS)} pairs")
        return 0

    from dotenv import load_dotenv

    load_dotenv()
    from backend.utils.embedding_utils import embed_text

    chosen = PARTS if args.part == "all" else (args.part,)
    embed = cached(embed_text)
    warm(embed, every_text())
    out: Dict[str, Any] = {}
    sections = []

    if "recall" in chosen:
        policies = POLICIES if args.query_policy == "both" else (args.query_policy,)
        store = build_store(embed)
        out["recall"] = {policy: run_recall(embed, policy, store) for policy in policies}
        sections.append(format_recall(out["recall"]))
    if "write" in chosen:
        from backend.utils.app_utils import summarize_turn

        out["write"] = asyncio.run(run_write(embed, args.runs, summarize_turn))
        sections.append(format_write(out["write"]))
    if "compaction" in chosen:
        from backend.services import memory_compaction

        out["compaction"] = asyncio.run(run_compaction(args.runs, memory_compaction._summarize_cluster))
        sections.append(format_compaction(out["compaction"]))
    if "dedup" in chosen:
        out["dedup"] = run_dedup(embed)
        sections.append(format_dedup(out["dedup"]))

    print("\n\n".join(sections))
    if args.save:
        os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(to_record(out), handle, indent=2)
        print(f"\nsaved to {args.save}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
