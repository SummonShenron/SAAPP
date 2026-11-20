"""Does library retrieval bring back the right passages, and nothing else? Measured with the real embeddings and the app's own search code.

    python -m backend.evals.rag_eval                        # every engine, every k, both ways of ingesting
    python -m backend.evals.rag_eval --engines app,vector   # only these engines (app = the app's routing, as in production)
    python -m backend.evals.rag_eval --ingest headers       # only chunks that carry their file name and library in their text
    python -m backend.evals.rag_eval --list                 # the questions, and what the invented libraries hold
    python -m backend.evals.rag_eval --save eval_results/rag_baseline.json

WHY IT EXISTS. Nothing measured retrieval before (2026-11-18: a question about one job came back mixed with a cover letter, a recommendation letter and another employer's work, and there was no number to say how
often that happens). This is the number. Run it before changing how libraries are chunked, embedded or searched, and again after.

WHAT IT RUNS. An invented person, Riley Chen, with libraries shaped like a real one: eight weekly reports from the current job, four from the previous one (another library, vocabulary that overlaps), a cover letter, a
recommendation letter and a résumé that all talk about growth in general words, health and recipe guides that say "progress" and "weekly plan", and a library that belongs to someone else. The questions are the kinds a person
asks: one fact, an exact ticket id, a date word, a paraphrase, a whole stretch of weeks, the other job, the career documents, things nobody stored, and something only the other person's library knows.

THE APP'S OWN CODE IS WHAT IS MEASURED: `backend.services.search` (routing, vector, lexical, hybrid, the scope filter, the score scale) runs unchanged over rows held in memory that stand in for Atlas Vector Search
(cosine, with Atlas's score scale, (1 + cosine) / 2). The chunks are cut the way ingestion cuts them (RecursiveCharacterTextSplitter, 1000 characters, 200 overlap). Nothing in the database is read or written, and none
of it is a real person's data. Only the embedding model is called: about a hundred passages and thirty questions, a fraction of a cent. No chat model is used.

WHAT IS REPORTED, for each engine and each k (how many passages come back)
- hit         the share of answerable questions where a passage from an expected file is among the k.
- complete    for a question that needs several files ("how has my work progressed"), the share of the expected files that were found.
- MRR         how high the first right passage ranks.
- precision   the share of returned passages that come from an expected file.
- wrong       the share of questions where a file that must NOT be used (the other job, a letter about growth, someone else's library) came back.
- unanswerable  for questions nothing stored can answer: the top score returned, next to the answerable ones', and a sweep of relevance cutoffs showing where the right passage stops being separable from the noise.

THE TAGGING EXPERIMENT (--tagging, 2026-11-19) asks a different question: if every document were labelled when it was ingested (what kind it is, which employer it is about, which week it covers), and the question's own
words were read for the same labels, how much better would retrieval be? It is an ORACLE: the labels are written by hand and are perfect, so the answer is the most tagging could ever give. Then a share of the labels is
corrupted, to show how much a real tagger's mistakes cost. Two ways of using a label are compared: a hard filter (only passages whose file has the labels the question asked for) and a soft boost (matching files rank
higher, nothing is removed). The search underneath is the app's own, unchanged.

SCORING IS MECHANICAL and tested without any model (backend/tests/test_rag_eval.py). It is not part of the test suite.
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

ENGINES = ("app", "vector", "lexical", "hybrid")
KS = (3, 5, 8)
INGESTS = ("baseline", "headers")
CHUNK_SIZE = 1000          # function_app/function_app.py: RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
CHUNK_OVERLAP = 200
CUTOFFS = (0.70, 0.72, 0.74, 0.76, 0.78, 0.80, 0.82, 0.84, 0.86)

LIB_WEEKLY = "kb_weekly"               # the person's own library: the current job's weekly reports
LIB_PREVIOUS = "lib_harbor"            # the previous job's weekly reports
LIB_CAREER = "lib_career"              # letters and a résumé
LIB_HEALTH = "lib_health"              # unrelated guides that happen to use the same words
LIB_PRIVATE = "lib_someone_else"       # not theirs
SCOPE: Tuple[str, ...] = (LIB_WEEKLY, LIB_PREVIOUS, LIB_CAREER, LIB_HEALTH)    # what the invented person may read


# ---------------------------------------------------------------------------------------------------------------------
# The invented libraries
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    name: str
    library: str
    text: str


def _week(start: str, highlights: Sequence[str], tickets: Sequence[Tuple[str, str, str]], notes: str, org: str = "") -> str:
    record: Dict[str, Any] = {"week_start": start}
    if org:
        record["org"] = org
    record["highlights"] = list(highlights)
    record["tickets"] = [{"id": ticket, "title": title, "status": status} for ticket, title, status in tickets]
    record["notes"] = notes
    return json.dumps(record, indent=2)


NORTHWIND_WEEKS: Tuple[Doc, ...] = (
    Doc("week-2026-09-07.json", LIB_WEEKLY, _week(
        "2026-09-07",
        ["First week at Northwind Insurance: laptop, accounts and repo access set up.", "Met my manager, Dana Whitfield, and the claims platform team.", "Read the claims-intake architecture doc end to end."],
        [("PLAT-2104", "Fix the typo in the claim PDF footer", "done")],
        "Mostly onboarding. Dana wants me on a small ticket first to learn the release process.", org="Northwind Insurance")),
    Doc("week-2026-09-14.json", LIB_WEEKLY, _week(
        "2026-09-14",
        ["Shipped PLAT-2104 and PLAT-2118 to production through the normal release train.", "Paired with Priya Natarajan on the claims-intake service for the first time.", "Started reading about the planned Postgres 11 to 15 upgrade."],
        [("PLAT-2104", "Fix the typo in the claim PDF footer", "shipped"), ("PLAT-2118", "Add a null guard to the policy lookup", "shipped")],
        "Two small fixes out. Priya showed me how the intake queue is wired and where the retries live.")),
    Doc("week-2026-09-21.json", LIB_WEEKLY, _week(
        "2026-09-21",
        ["Wrote the design doc for rewriting claims intake as an event-driven service with a queue.", "Design review with Priya and Dana; they asked for a rollout plan with a canary.", "Investigated PLAT-2291, the flaky webhook delivery: a retry storm after timeouts."],
        [("PLAT-2291", "Webhook delivery retries pile up after a timeout", "investigating"), ("PLAT-2302", "Document the intake queue message format", "in review")],
        "The rewrite is mine to drive. Root cause of PLAT-2291 found, fix planned for next week.")),
    Doc("week-2026-09-28.json", LIB_WEEKLY, _week(
        "2026-09-28",
        ["Implemented the queue consumer for the new claims intake service.", "Fixed PLAT-2291 with exponential backoff and jitter on webhook retries.", "Added 23 tests around the retry path.", "Shadowed my first on-call shift with Priya."],
        [("PLAT-2291", "Webhook delivery retries pile up after a timeout", "fixed"), ("PLAT-2310", "Queue consumer for claims intake", "in review")],
        "Retry storm is gone in staging. On-call shadowing was quiet but I learned the paging runbook.")),
    Doc("week-2026-10-05.json", LIB_WEEKLY, _week(
        "2026-10-05",
        ["Rehearsed the Postgres 11 to 15 migration in staging and wrote the runbook.", "Found index bloat on the claims table that would have slowed the cutover.", "Took my first primary on-call shift; it was quiet."],
        [("PLAT-2344", "Postgres 15 upgrade rehearsal and runbook", "done"), ("PLAT-2351", "Rebuild bloated claims indexes", "in progress")],
        "The database upgrade is on track for the weekend of the 24th. Rollback steps are written and tested.")),
    Doc("week-2026-10-12.json", LIB_WEEKLY, _week(
        "2026-10-12",
        ["The new claims API went to production on Thursday the 8th with a canary at 5 percent, 50 percent and then 100 percent. No rollbacks.", "Demoed the new intake flow to the claims operations team.", "Closed out the design doc follow-ups."],
        [("PLAT-2310", "Queue consumer for claims intake", "shipped"), ("PLAT-2362", "Claims ops demo feedback", "open")],
        "Biggest release so far and it went cleanly. Claims ops asked for two small changes to the status page.")),
    Doc("week-2026-10-19.json", LIB_WEEKLY, _week(
        "2026-10-19",
        ["Handled a P2 incident as primary on-call: a batch job stuck on a poison message.", "Led the incident review and wrote three follow-up tickets.", "Mentored our intern Jordan Okafor on writing integration tests."],
        [("INC-884", "Stuck batch job on a poison message", "resolved"), ("PLAT-2378", "Dead-letter handling for the intake queue", "open")],
        "First real incident. Dana said the review was a good template for the team.")),
    Doc("week-2026-10-26.json", LIB_WEEKLY, _week(
        "2026-10-26",
        ["The Postgres 15 cutover happened over the weekend of the 24th with no data issues.", "Cut CI flake rate from 9 percent to 3 percent by quarantining three unreliable tests.", "Proposed a test-stability dashboard and presented it at the Northwind engineering all-hands."],
        [("PLAT-2344", "Postgres 15 upgrade", "done"), ("PLAT-2390", "Test-stability dashboard", "proposed")],
        "Feels like I own the intake service now. Next: dead-letter handling and the dashboard.", org="Northwind Insurance")),
)

HARBOR_WEEKS: Tuple[Doc, ...] = (
    Doc("week-2025-03-03.json", LIB_PREVIOUS, _week(
        "2025-03-03",
        ["Triaged the support ticket queue and closed 14 maintenance tickets.", "Applied the monthly security patches to the claims portal."],
        [("HM-410", "Portal login page spacing", "done"), ("HM-415", "Update the PDF library", "done")],
        "Standard maintenance week. Waiting on the architects for the portal migration plan.")),
    Doc("week-2025-03-10.json", LIB_PREVIOUS, _week(
        "2025-03-10",
        ["Took the on-call phone for the first time; two low-priority pages.", "Fixed a release script that skipped the smoke tests.", "Harbor Mutual quarterly planning: asked to look at the batch scheduler next."],
        [("HM-422", "Release script skips smoke tests", "done"), ("HM-425", "Batch scheduler retries", "open")],
        "Ticket flow is steady. The batch scheduler is a mess nobody owns.", org="Harbor Mutual")),
    Doc("week-2025-03-17.json", LIB_PREVIOUS, _week(
        "2025-03-17",
        ["Started untangling the batch scheduler retries and wrote down how it works.", "Migration of the portal to the new hosting went out on Friday."],
        [("HM-425", "Batch scheduler retries", "in progress"), ("HM-431", "Portal hosting migration", "shipped")],
        "Nobody else knows the scheduler, so I am becoming the person who does.")),
    Doc("week-2025-03-24.json", LIB_PREVIOUS, _week(
        "2025-03-24",
        ["Fixed the scheduler retry bug and added tests.", "Wrote a release checklist after a rough deploy on Tuesday."],
        [("HM-425", "Batch scheduler retries", "fixed"), ("HM-440", "Release checklist", "done")],
        "Small wins. Beth said the checklist was long overdue.")),
)

CAREER: Tuple[Doc, ...] = (
    Doc("Cover Letter.pdf", LIB_CAREER, (
        "Dear Northwind Insurance hiring team, I am applying for the software engineer role on your claims platform. Over the last two years at Harbor Mutual my work progressed from maintenance and ticket flow "
        "into taking ownership of systems nobody else understood. When team structures shifted I did not just cover the gaps; I stepped into those responsibilities, learned the details and became the person to ask. "
        "I care about stability, automated tests and clear separation of concerns, and I want to keep growing as an engineer who can step into ambiguous, high-need areas and deliver dependable work. "
        "I would be glad to bring that growth to Northwind.")),
    Doc("Beths Letter of Recommendation.pdf", LIB_CAREER, (
        "To whom it may concern, I managed Riley Chen at Harbor Mutual for two years. Riley's progress in that time was remarkable. They began on routine maintenance and steadily took on ownership of the batch "
        "scheduler, a system nobody else wanted. Riley learns quickly, communicates clearly, documents what they build and treats every ticket as a chance to improve stability. I recommend Riley without reservation "
        "for any role that rewards initiative and growth. Sincerely, Beth Alvarez, Engineering Manager, Harbor Mutual.")),
    Doc("Resume.pdf", LIB_CAREER, (
        "Riley Chen. Software Engineer. Skills: Python, TypeScript, PostgreSQL, message queues, CI pipelines, incident response, mentoring. "
        "Experience: Northwind Insurance, Software Engineer, September 2026 to present: claims platform, event-driven claims intake service, Postgres 15 upgrade. "
        "Harbor Mutual, Software Engineer, 2024 to August 2026: maintenance, batch scheduler ownership, release tooling, on-call. "
        "Education: B.S. Computer Science, State University, 2024.")),
)

HEALTH: Tuple[Doc, ...] = (
    Doc("Workout_Recovery_and_Nutrition.pdf", LIB_HEALTH, (
        "Track your progress each week. Progressive overload means adding a little weight or a few reps from week to week. Recovery matters as much as training: sleep seven to nine hours, eat protein after a session "
        "and take one full rest day in your weekly plan. If your progress stalls for three weeks, change one variable at a time.")),
    Doc("Meal_Prep_Guide.pdf", LIB_HEALTH, (
        "Plan your meals for the whole week on Sunday. A weekly plan with five lunches and four dinners keeps work days simple. Cook grains in bulk, roast two trays of vegetables and keep sauces separate. "
        "Review what you actually ate at the end of the week and adjust the plan for next week.")),
    Doc("Sleep_Basics.pdf", LIB_HEALTH, (
        "Consistent sleep timing improves mood and focus. Keep the room cool, avoid screens in the last hour and write down worries before bed. Keep a weekly sleep log to notice patterns and track progress over time.")),
)

PRIVATE: Tuple[Doc, ...] = (
    Doc("cabin_notes.txt", LIB_PRIVATE, "The cabin wifi network is PineRidge-Guest and the password is 'lakeview-4471'. The key is under the green flowerpot."),
)

DOCS: Tuple[Doc, ...] = NORTHWIND_WEEKS + HARBOR_WEEKS + CAREER + HEALTH + PRIVATE
NORTHWIND_NAMES: Tuple[str, ...] = tuple(doc.name for doc in NORTHWIND_WEEKS)
HARBOR_NAMES: Tuple[str, ...] = tuple(doc.name for doc in HARBOR_WEEKS)
CAREER_NAMES: Tuple[str, ...] = tuple(doc.name for doc in CAREER)
HEALTH_NAMES: Tuple[str, ...] = tuple(doc.name for doc in HEALTH)
PRIVATE_NAMES: Tuple[str, ...] = tuple(doc.name for doc in PRIVATE)


# ---------------------------------------------------------------------------------------------------------------------
# The questions
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: str
    kind: str                       # fact | term | date | latest | paraphrase | synthesis | other_job | career | health | unanswerable | isolation
    query: str
    expect: Tuple[str, ...] = ()    # files that answer it: finding ANY of them is a hit (a synthesis question needs them all: see `need_all`)
    forbid: Tuple[str, ...] = ()    # files that must not come back for it
    need_all: bool = False


def _not(*groups: Sequence[str]) -> Tuple[str, ...]:
    return tuple(name for group in groups for name in group)


CASES: Tuple[Case, ...] = (
    Case("ship_week", "fact", "What did I ship in the week of September 14?", ("week-2026-09-14.json",), _not(HARBOR_NAMES)),
    Case("manager", "fact", "Who is my manager at Northwind?", ("week-2026-09-07.json", "week-2026-09-21.json", "week-2026-10-19.json", "Resume.pdf"), _not(HARBOR_NAMES)),
    Case("pair_priya", "fact", "Who did I first pair with on the claims intake service?", ("week-2026-09-14.json",), _not(HARBOR_NAMES)),
    Case("ticket_id", "term", "What was PLAT-2291 about?", ("week-2026-09-21.json", "week-2026-09-28.json"), _not(HARBOR_NAMES)),
    Case("incident_id", "term", "What happened in INC-884?", ("week-2026-10-19.json",), _not(HARBOR_NAMES)),
    Case("prod_date", "date", "When did the claims API go to production?", ("week-2026-10-12.json",), _not(HARBOR_NAMES)),
    Case("upgrade_date", "date", "What date was the Postgres 15 cutover?", ("week-2026-10-26.json", "week-2026-10-05.json"), _not(HARBOR_NAMES)),
    Case("latest", "latest", "What is the most recent thing I worked on?", ("week-2026-10-26.json",), _not(HARBOR_NAMES)),
    Case("database", "paraphrase", "how did the database upgrade go", ("week-2026-10-05.json", "week-2026-10-26.json"), _not(HARBOR_NAMES)),
    # words that CONTAIN a routing marker without being one ("update" has "date" in it, "validate" too): the router used to read them as questions about dates
    Case("update_trap", "paraphrase", "What did I update in the claims intake service?", ("week-2026-09-21.json", "week-2026-09-28.json", "week-2026-10-12.json"), _not(HARBOR_NAMES)),
    Case("validate_trap", "paraphrase", "How did I validate the Postgres upgrade?", ("week-2026-10-05.json", "week-2026-10-26.json"), _not(HARBOR_NAMES)),
    Case("incident", "paraphrase", "Tell me about the production incident I handled", ("week-2026-10-19.json",), _not(HARBOR_NAMES)),
    Case("mentor", "paraphrase", "Who did I mentor?", ("week-2026-10-19.json",), _not(HARBOR_NAMES)),
    Case("flaky", "paraphrase", "What did I do about flaky tests?", ("week-2026-10-26.json",), _not(HARBOR_NAMES)),
    Case("progress_nw", "synthesis", "how do you think my work has progressed since ive been at Northwind?", NORTHWIND_NAMES, _not(CAREER_NAMES, HARBOR_NAMES, HEALTH_NAMES), need_all=True),
    Case("seven_weeks", "synthesis", "Review my last seven weeks at Northwind", NORTHWIND_NAMES[1:], _not(CAREER_NAMES, HARBOR_NAMES, HEALTH_NAMES), need_all=True),
    Case("growth", "synthesis", "How have I grown as an engineer over my time at Northwind?", NORTHWIND_NAMES, _not(CAREER_NAMES, HARBOR_NAMES, HEALTH_NAMES), need_all=True),
    Case("harbor_work", "other_job", "What did I work on at Harbor Mutual?", HARBOR_NAMES + ("Resume.pdf",), _not(NORTHWIND_NAMES)),
    Case("harbor_oncall", "other_job", "Did I do on-call at Harbor Mutual?", ("week-2025-03-10.json",), _not(NORTHWIND_NAMES)),
    Case("harbor_scheduler", "other_job", "What happened with the batch scheduler?", ("week-2025-03-10.json", "week-2025-03-17.json", "week-2025-03-24.json"), _not(NORTHWIND_NAMES)),
    Case("letter_ownership", "career", "What does my recommendation letter say about how I handle ownership?", ("Beths Letter of Recommendation.pdf",), ()),
    Case("resume_skills", "career", "What skills are on my resume?", ("Resume.pdf",), ()),
    Case("why_northwind", "career", "Why did I say I wanted to join Northwind?", ("Cover Letter.pdf",), ()),
    Case("recovery", "health", "What is my weekly workout recovery plan?", ("Workout_Recovery_and_Nutrition.pdf",), _not(NORTHWIND_NAMES, HARBOR_NAMES)),
    Case("meal_prep", "health", "How should I plan my meals for the week?", ("Meal_Prep_Guide.pdf",), _not(NORTHWIND_NAMES, HARBOR_NAMES)),
    Case("deductible", "unanswerable", "What is my health insurance deductible?"),
    Case("capital", "unanswerable", "What is the capital of Australia?"),
    Case("tax", "unanswerable", "Summarize my tax return for last year"),
    Case("chess", "unanswerable", "What was my score in the chess tournament?"),
    Case("cabin_wifi", "isolation", "What is the wifi password for the cabin?", (), _not(PRIVATE_NAMES)),
)


# ---------------------------------------------------------------------------------------------------------------------
# Chunks, embeddings, and the stand-in for Atlas
# ---------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    source: str
    library: str
    page: int
    text: str          # what is stored and shown
    embedded: str      # what is embedded: the text, or the text with its file and library in front of it


def chunk_documents(docs: Sequence[Doc] = DOCS, ingest: str = "baseline", size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[Chunk]:
    """The documents cut the way ingestion cuts them. `headers` puts the file name and library in front of the text that is EMBEDDED (what is stored and shown is unchanged)."""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    chunks: List[Chunk] = []
    for doc in docs:
        for page, text in enumerate(splitter.split_text(doc.text)):
            header = f"File: {doc.name}\nLibrary: {doc.library}\n\n" if ingest == "headers" else ""
            chunks.append(Chunk(doc.name, doc.library, page, text, header + text))
    return chunks


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def atlas_score(cos: float) -> float:
    """Atlas Vector Search's score for cosine similarity: (1 + cosine) / 2."""
    return (1.0 + cos) / 2.0


class Embedder:
    """The two things the app does with an embedding model: embed passages (ingestion) and embed a question (search). Cached, so nothing is paid for twice."""

    def __init__(self, cache_path: str = "") -> None:
        self._client: Any = None
        self._docs: Dict[str, List[float]] = {}
        self._queries: Dict[str, List[float]] = {}
        self.calls = 0
        self.cache_path = cache_path
        self._load()

    # An on-disk copy of what the model returned, so a rerun after changing the SEARCH code (not the texts) costs nothing and gives the same vectors. Keyed by the model and a hash of the text.
    def _key(self, kind: str, text: str) -> str:
        import hashlib

        return f"{kind}:{os.getenv('EMBEDDING_MODEL', 'gemini-embedding-001')}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

    def _load(self) -> None:
        self._disk: Dict[str, List[float]] = {}
        if self.cache_path and os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, encoding="utf-8") as handle:
                    self._disk = json.load(handle)
            except (OSError, ValueError):
                self._disk = {}

    def save(self) -> None:
        if self.cache_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_path)), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as handle:
                json.dump(self._disk, handle)

    def _real(self) -> Any:
        if self._client is None:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            self._client = GoogleGenerativeAIEmbeddings(model=os.getenv("EMBEDDING_MODEL", "gemini-embedding-001"), output_dimensionality=int(os.getenv("EMBEDDING_DIM", "768")))
        return self._client

    def docs(self, texts: Sequence[str]) -> List[List[float]]:
        for text in dict.fromkeys(texts):
            if text not in self._docs and self._key("doc", text) in self._disk:
                self._docs[text] = self._disk[self._key("doc", text)]
        missing = [t for t in dict.fromkeys(texts) if t not in self._docs]
        if missing:
            self.calls += len(missing)
            for text, vector in zip(missing, self._real().embed_documents(missing)):
                self._docs[text] = vector
                self._disk[self._key("doc", text)] = vector
        return [self._docs[t] for t in texts]

    def query(self, text: str) -> List[float]:
        if text not in self._queries and self._key("query", text) in self._disk:
            self._queries[text] = self._disk[self._key("query", text)]
        if text not in self._queries:
            self.calls += 1
            self._queries[text] = self._real().embed_query(text)
            self._disk[self._key("query", text)] = self._queries[text]
        return self._queries[text]


class EvalCollection:
    """The slice of a Mongo collection that backend.services.search uses ($vectorSearch with an affiliate filter, then $project), over chunks held in memory."""

    def __init__(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]):
        self.rows = list(zip(chunks, vectors))

    def aggregate(self, pipeline: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        stage = pipeline[0]["$vectorSearch"]
        allowed = (stage.get("filter") or {}).get("affiliate", {}).get("$in")
        scored = [(atlas_score(cosine(stage["queryVector"], vector)), chunk) for chunk, vector in self.rows if allowed is None or chunk.library in allowed]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"page_content": chunk.text, "text": chunk.text, "source": chunk.source, "filename": chunk.source, "page": chunk.page, "affiliate": chunk.library, "score": score}
            for score, chunk in scored[: int(stage["limit"])]
        ]


class Harness:
    """Points the app's search module at the in-memory store and the cached embedder for as long as it is open, and puts everything back after."""

    def __init__(self, chunks: Sequence[Chunk], embedder: Embedder):
        self.embedder = embedder
        self.collection = EvalCollection(chunks, embedder.docs([chunk.embedded for chunk in chunks]))
        self._saved: List[Tuple[str, Any]] = []

    def __enter__(self) -> "Harness":
        from backend.services import search

        class _Query:
            @staticmethod
            def embed_query(text: str) -> List[float]:
                return self.embedder.query(text)

        self._saved = [("_get_mongo_collection", search._get_mongo_collection), ("_embeddings", search._embeddings)]
        search._get_mongo_collection = lambda name: self.collection
        search._embeddings = _Query()
        return self

    def __exit__(self, *exc: Any) -> None:
        from backend.services import search

        for name, value in self._saved:
            setattr(search, name, value)

    def narrowed(self, keep: Callable[[Chunk], bool]) -> "_Narrowed":
        """For a `with`: the same store, but only the chunks `keep` says yes to, for the searches made inside it."""
        return _Narrowed(self, keep)

    def retrieve(self, engine: str, query: str, scope: Sequence[str], k: int) -> List[Document]:
        """What the app would hand the model for `query`, with `k` passages: through its own routing (`app`) or forced to one strategy."""
        from backend.services import search

        if engine == "app":
            return list(search.get_secure_retriever(None, list(scope), query, top_k=k).invoke(query))
        search_filter = {"metadata.affiliate": {"$in": list(scope)}}
        strategy = {"vector": search._retrieve_vector, "lexical": search._retrieve_lexical, "hybrid": search._retrieve_hybrid}[engine]
        return list(strategy(query, search_filter, k))


class _Narrowed:
    def __init__(self, harness: Harness, keep: Callable[[Chunk], bool]):
        self.harness = harness
        self.keep = keep
        self._whole: Optional[EvalCollection] = None

    def __enter__(self) -> Harness:
        self._whole = self.harness.collection
        self.harness.collection = EvalCollection([c for c, _ in self._whole.rows if self.keep(c)], [v for c, v in self._whole.rows if self.keep(c)])
        return self.harness

    def __exit__(self, *exc: Any) -> None:
        assert self._whole is not None
        self.harness.collection = self._whole


# ---------------------------------------------------------------------------------------------------------------------
# Scoring (mechanical: no model)
# ---------------------------------------------------------------------------------------------------------------------


@dataclass
class CaseScore:
    id: str
    kind: str
    answerable: bool
    sources: List[str]              # the file of each returned passage, in order
    scores: List[float]             # and each passage's relevance score
    hit: bool
    coverage: float                 # share of the expected files found (what a synthesis question needs)
    rr: float                       # 1 / rank of the first passage from an expected file, 0 if none
    precision: float                # share of the returned passages that come from an expected file
    wrong: bool                     # a file that must not be used came back
    top_score: float                # the best passage's score (0 when nothing came back)


def score_case(case: Case, docs: Sequence[Document], k: int) -> CaseScore:
    docs = list(docs)[:k]
    sources = [str((doc.metadata or {}).get("source") or "") for doc in docs]
    scores = [float((doc.metadata or {}).get("score") or 0.0) for doc in docs]
    expected = set(case.expect)
    found = {s for s in sources if s in expected}
    rr = next((1.0 / (rank + 1) for rank, s in enumerate(sources) if s in expected), 0.0)
    return CaseScore(
        id=case.id, kind=case.kind, answerable=bool(case.expect), sources=sources, scores=scores,
        hit=bool(found), coverage=(len(found) / len(expected)) if expected else 0.0, rr=rr,
        precision=(sum(1 for s in sources if s in expected) / len(sources)) if sources and expected else 0.0,
        wrong=any(s in set(case.forbid) for s in sources), top_score=max(scores) if scores else 0.0,
    )


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def aggregate(scores: Sequence[CaseScore], cases: Sequence[Case] = CASES) -> Dict[str, Any]:
    """The numbers for one engine at one k."""
    need_all = {case.id for case in cases if case.need_all}
    has_forbidden = {case.id for case in cases if case.forbid}
    answerable = [s for s in scores if s.answerable]
    by_kind: Dict[str, Dict[str, float]] = {}
    for kind in dict.fromkeys(s.kind for s in answerable):
        group = [s for s in answerable if s.kind == kind]
        by_kind[kind] = {"n": len(group), "hit": _mean(s.hit for s in group), "mrr": _mean(s.rr for s in group)}
    unanswerable = [s for s in scores if not s.answerable and s.kind == "unanswerable"]
    return {
        "n": len(answerable),
        "hit": _mean(s.hit for s in answerable),
        "mrr": _mean(s.rr for s in answerable),
        "precision": _mean(s.precision for s in answerable),
        "complete": _mean(s.coverage for s in answerable if s.id in need_all),
        "wrong": _mean(s.wrong for s in scores if s.id in has_forbidden),
        "by_kind": by_kind,
        "top_score_answerable": _mean(s.top_score for s in answerable),
        "top_score_unanswerable": _mean(s.top_score for s in unanswerable),
    }


def apply_cutoff(score: CaseScore, case: Case, cutoff: float) -> CaseScore:
    """The same question's result if passages scoring under `cutoff` were never returned."""
    keep = [i for i, s in enumerate(score.scores) if s >= cutoff]
    docs = [Document(page_content="", metadata={"source": score.sources[i], "score": score.scores[i]}) for i in keep]
    return score_case(case, docs, len(docs) or 1)


def sweep(scores: Sequence[CaseScore], cases: Sequence[Case] = CASES, cutoffs: Sequence[float] = CUTOFFS) -> List[Dict[str, float]]:
    """For each relevance cutoff: how many right answers survive, how many wrong files still come back, and how many of the unanswerable questions correctly return nothing."""
    by_id = {case.id: case for case in cases}
    rows = []
    for cutoff in cutoffs:
        cut = [apply_cutoff(s, by_id[s.id], cutoff) for s in scores]
        answerable = [s for s in cut if s.answerable]
        unanswerable = [s for s in cut if s.kind == "unanswerable"]
        rows.append({
            "cutoff": cutoff,
            "hit": _mean(s.hit for s in answerable),
            "wrong": _mean(s.wrong for s in cut if by_id[s.id].forbid),
            "clean": _mean(not s.sources for s in unanswerable),
            "junk": _mean(len(s.sources) for s in unanswerable),
        })
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# Running and reporting
# ---------------------------------------------------------------------------------------------------------------------


def run(harness: Harness, engines: Sequence[str], ks: Sequence[int], cases: Sequence[Case] = CASES, scope: Sequence[str] = SCOPE) -> Dict[str, Dict[int, List[CaseScore]]]:
    out: Dict[str, Dict[int, List[CaseScore]]] = {}
    for engine in engines:
        out[engine] = {}
        for k in ks:
            out[engine][k] = [score_case(case, harness.retrieve(engine, case.query, scope, k), k) for case in cases]
    return out


def pct(value: float) -> str:
    return f"{round(100 * value):>3d}%"


def format_report(results: Dict[str, Dict[str, Dict[int, List[CaseScore]]]], cases: Sequence[Case] = CASES) -> str:
    lines: List[str] = []
    for ingest, engines in results.items():
        lines.append(f"INGEST: {ingest}")
        lines.append(f"  {'engine':<9}{'k':>3}  {'hit':>5}{'complete':>9}{'MRR':>7}{'precision':>10}{'wrong':>7}   top score: answerable / unanswerable")
        for engine, per_k in engines.items():
            for k, scores in per_k.items():
                a = aggregate(scores, cases)
                lines.append(f"  {engine:<9}{k:>3}  {pct(a['hit']):>5}{pct(a['complete']):>9}{a['mrr']:>7.2f}{pct(a['precision']):>10}{pct(a['wrong']):>7}   {a['top_score_answerable']:.3f} / {a['top_score_unanswerable']:.3f}")
        engine, per_k = next(iter(engines.items()))
        k = max(per_k)
        lines.append("")
        lines.append(f"  by kind, engine {engine}, k={k}: " + ", ".join(f"{kind} {pct(v['hit']).strip()} ({int(v['n'])})" for kind, v in aggregate(per_k[k], cases)["by_kind"].items()))
        lines.append(f"  relevance cutoff sweep, engine {engine}, k={k} (hit = right file still found; wrong = a forbidden file still came back; clean = unanswerable questions that return nothing):")
        lines.append(f"    {'cutoff':>7}{'hit':>6}{'wrong':>7}{'clean':>7}{'junk/q':>8}")
        for row in sweep(per_k[k], cases):
            lines.append(f"    {row['cutoff']:>7.2f}{pct(row['hit']):>6}{pct(row['wrong']):>7}{pct(row['clean']):>7}{row['junk']:>8.1f}")
        lines.append("")
    first = next(iter(results.values()))
    engine, per_k = next(iter(first.items()))
    k = min(per_k)
    lines.append(f"WHAT CAME BACK, engine {engine}, k={k}, the first ingest (a miss is marked; a wrong file is marked X):")
    for case, score in zip(cases, per_k[k]):
        mark = "MISS" if case.expect and not score.hit else "    "
        wrong = " X" if score.wrong else ""
        lines.append(f"  {mark} {case.id:<18}{case.kind:<14}{', '.join(dict.fromkeys(score.sources)) or '(nothing)'}{wrong}")
    return "\n".join(lines)


def to_record(results: Dict[str, Dict[str, Dict[int, List[CaseScore]]]]) -> Dict[str, Any]:
    return {
        ingest: {engine: {str(k): {"summary": aggregate(scores), "cases": [s.__dict__ for s in scores]} for k, scores in per_k.items()} for engine, per_k in engines.items()}
        for ingest, engines in results.items()
    }


def list_everything() -> str:
    lines = ["LIBRARIES (the invented person may read: " + ", ".join(SCOPE) + ")"]
    for library in dict.fromkeys(doc.library for doc in DOCS):
        names = [doc.name for doc in DOCS if doc.library == library]
        lines.append(f"  {library:<18}{len(names)} files: {', '.join(names)}")
    lines.append("")
    lines.append(f"QUESTIONS ({len(CASES)})")
    for case in CASES:
        lines.append(f"  {case.id:<18}{case.kind:<12}{case.query}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------------
# The tagging experiment
# ---------------------------------------------------------------------------------------------------------------------

EMPLOYERS = ("northwind", "harbor", "none")
TYPES = ("weekly_report", "letter", "resume", "guide", "notes")
TAG_MODES = ("none", "hard", "soft")
ERROR_RATES = (0.0, 0.15, 0.30)
SEEDS = (0, 1, 2, 3, 4)
SOFT_BOOST = 0.05            # added to a passage's score for each label its file shares with what the question asked for, on Atlas's 0-to-1 scale (answerable questions score about 0.83, junk about 0.77)
RECENT_BOOST = 0.05          # and for the newest week, when the question asks for the most recent


def oracle_tags() -> Dict[str, Dict[str, str]]:
    """A perfect label for every file: its kind, the employer it is about ("none" when it is about neither, or both), and the week it starts (weekly reports only)."""
    tags: Dict[str, Dict[str, str]] = {}
    for doc in DOCS:
        if doc.library == LIB_WEEKLY:
            tags[doc.name] = {"doc_type": "weekly_report", "employer": "northwind", "period": doc.name[len("week-"):-len(".json")]}
        elif doc.library == LIB_PREVIOUS:
            tags[doc.name] = {"doc_type": "weekly_report", "employer": "harbor", "period": doc.name[len("week-"):-len(".json")]}
        elif doc.library == LIB_HEALTH:
            tags[doc.name] = {"doc_type": "guide", "employer": "none", "period": ""}
        elif doc.library == LIB_PRIVATE:
            tags[doc.name] = {"doc_type": "notes", "employer": "none", "period": ""}
        else:
            tags[doc.name] = {"doc_type": "resume" if doc.name.startswith("Resume") else "letter", "employer": {"Cover Letter.pdf": "northwind", "Beths Letter of Recommendation.pdf": "harbor"}.get(doc.name, "none"), "period": ""}
    return tags


# What a perfect reader of each question's OWN WORDS would take from it (only what the words say: "at Northwind" names an employer, "recommendation letter" names a kind, "most recent" asks for the newest week).
# A question that names neither gets nothing, and the search is the plain one.
WANTED: Dict[str, Dict[str, str]] = {
    "manager": {"employer": "northwind"},
    "latest": {"doc_type": "weekly_report", "order": "recent"},
    "progress_nw": {"employer": "northwind", "doc_type": "weekly_report"},
    "seven_weeks": {"employer": "northwind", "doc_type": "weekly_report"},
    "growth": {"employer": "northwind", "doc_type": "weekly_report"},
    "harbor_work": {"employer": "harbor"},
    "harbor_oncall": {"employer": "harbor"},
    "letter_ownership": {"doc_type": "letter"},
    "resume_skills": {"doc_type": "resume"},
    "why_northwind": {"employer": "northwind", "doc_type": "letter"},
    "recovery": {"doc_type": "guide"},
    "meal_prep": {"doc_type": "guide"},
}


def noisy_tags(tags: Dict[str, Dict[str, str]], error_rate: float, seed: int) -> Dict[str, Dict[str, str]]:
    """The same labels with `error_rate` of the files mislabelled (a different kind AND a different employer), which files being picked by `seed`: what a tagger that is sometimes wrong would have written."""
    import random

    rng = random.Random(seed)
    names = sorted(tags)
    wrong = set(rng.sample(names, round(error_rate * len(names)))) if error_rate > 0 else set()
    out: Dict[str, Dict[str, str]] = {}
    for name in names:
        tag = dict(tags[name])
        if name in wrong:
            tag["employer"] = rng.choice([e for e in EMPLOYERS if e != tag["employer"]])
            tag["doc_type"] = rng.choice([t for t in TYPES if t != tag["doc_type"]])
        out[name] = tag
    return out


def _matches(tag: Dict[str, str], want: Dict[str, str]) -> bool:
    return all(tag.get(key) == value for key, value in want.items() if key in ("employer", "doc_type"))


def retrieve_tagged(harness: Harness, mode: str, case: Case, tags: Dict[str, Dict[str, str]], scope: Sequence[str], k: int) -> List[Document]:
    """The app's search, with labels used the way `mode` says. `none` and a question with nothing to ask for are the plain search."""
    want = WANTED.get(case.id, {})
    if mode == "none" or not want:
        return harness.retrieve("app", case.query, scope, k)

    def tag_of(chunk: Chunk) -> Dict[str, str]:
        return tags.get(chunk.source, {})

    if mode == "hard":
        keep = lambda chunk: _matches(tag_of(chunk), want)          # noqa: E731
        if want.get("order") == "recent":
            periods = [tag_of(c).get("period", "") for c, _ in harness.collection.rows if keep(c) and tag_of(c).get("period")]
            newest = max(periods) if periods else None
            keep = lambda chunk, base=keep: base(chunk) and tag_of(chunk).get("period") == newest          # noqa: E731
        with harness.narrowed(keep) as narrowed:
            return narrowed.retrieve("app", case.query, scope, k)
    # soft: search wider, then let matching files rank higher; nothing is removed
    pool = harness.retrieve("app", case.query, scope, k * 4)
    newest = max((tags.get(str(d.metadata.get("source")), {}).get("period", "") for d in pool), default="") if want.get("order") == "recent" else ""
    ranked = []
    for rank, found in enumerate(pool):
        tag = tags.get(str(found.metadata.get("source")), {})
        boost = SOFT_BOOST * sum(1 for key in ("employer", "doc_type") if key in want and tag.get(key) == want[key])
        if newest and tag.get("period") == newest:
            boost += RECENT_BOOST
        ranked.append((float(found.metadata.get("score") or 0.0) + boost, -rank, found))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [found for _, _, found in ranked[:k]]


def run_tagging(harness: Harness, ks: Sequence[int] = KS, modes: Sequence[str] = TAG_MODES, error_rates: Sequence[float] = ERROR_RATES, seeds: Sequence[int] = SEEDS,
                cases: Sequence[Case] = CASES, scope: Sequence[str] = SCOPE) -> Dict[str, Dict[float, Dict[int, Dict[str, Any]]]]:
    """For each way of using labels and each share of wrong labels: the numbers for each k, averaged over `seeds` (which files are mislabelled). Perfect labels need one run; plain search ignores labels, so one too."""
    perfect = oracle_tags()
    out: Dict[str, Dict[float, Dict[int, Dict[str, Any]]]] = {}
    for mode in modes:
        out[mode] = {}
        for rate in ((0.0,) if mode == "none" else error_rates):
            runs = (0,) if rate == 0 else seeds
            out[mode][rate] = {}
            for k in ks:
                summaries = []
                for seed in runs:
                    tags = noisy_tags(perfect, rate, seed)
                    scores = [score_case(case, retrieve_tagged(harness, mode, case, tags, scope, k), k) for case in cases]
                    summaries.append(aggregate(scores, cases))
                keys = ("hit", "mrr", "precision", "complete", "wrong")
                merged = {key: _mean(item[key] for item in summaries) for key in keys}
                kinds = {kind: _mean(item["by_kind"].get(kind, {}).get("hit", 0.0) for item in summaries) for kind in ("latest", "synthesis", "other_job", "career")}
                out[mode][rate][k] = {**merged, "kinds": kinds}
    return out


def format_tagging(results: Dict[str, Dict[float, Dict[int, Dict[str, Any]]]]) -> str:
    lines = ["TAGGING EXPERIMENT (oracle labels: what the most tagging could ever give; mislabelled = share of files given a wrong kind and employer, averaged over several draws)",
             f"  {'use':<6}{'mislabelled':>12}{'k':>3}  {'hit':>5}{'complete':>9}{'MRR':>7}{'precision':>10}{'wrong':>7}   {'latest':>7}{'synthesis':>10}"]
    for mode, per_rate in results.items():
        for rate, per_k in per_rate.items():
            for k, m in per_k.items():
                lines.append(f"  {mode:<6}{pct(rate):>12}{k:>3}  {pct(m['hit']):>5}{pct(m['complete']):>9}{m['mrr']:>7.2f}{pct(m['precision']):>10}{pct(m['wrong']):>7}   {pct(m['kinds']['latest']):>7}{pct(m['kinds']['synthesis']):>10}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score library retrieval against the real embeddings and the app's own search code.")
    parser.add_argument("--engines", default=",".join(ENGINES), help="comma-separated: app (the app's routing), vector, lexical, hybrid")
    parser.add_argument("--ingest", default="both", choices=("both",) + INGESTS, help="baseline = chunks as stored today; headers = the file and library in front of each chunk's embedded text")
    parser.add_argument("--k", default=",".join(str(k) for k in KS), help="comma-separated numbers of passages to return")
    parser.add_argument("--list", action="store_true", help="list the libraries and questions and exit")
    parser.add_argument("--tagging", action="store_true", help="run the tagging experiment (oracle labels, hard filter vs soft boost, with mislabelled files) instead of the engine comparison")
    parser.add_argument("--save", default="", help="write the results to this JSON file")
    parser.add_argument("--cache", default="eval_results/rag_embedding_cache.json", help="keep the model's vectors here so a rerun costs nothing (empty: no cache)")
    args = parser.parse_args(argv)

    if args.list:
        print(list_everything())
        return 0
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in engines if e not in ENGINES]
    if unknown:
        parser.error(f"unknown engine: {', '.join(unknown)}")
    ks = [int(k) for k in args.k.split(",") if k.strip()]

    from dotenv import load_dotenv

    load_dotenv()
    embedder = Embedder(args.cache)
    if args.tagging:
        ingest = "baseline" if args.ingest == "both" else args.ingest
        with Harness(chunk_documents(ingest=ingest), embedder) as harness:
            tagged = run_tagging(harness, [int(k) for k in args.k.split(",") if k.strip()])
        embedder.save()
        print(format_tagging(tagged))
        if args.save:
            os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
            with open(args.save, "w", encoding="utf-8") as handle:
                json.dump({mode: {str(rate): {str(k): v for k, v in per_k.items()} for rate, per_k in per_rate.items()} for mode, per_rate in tagged.items()}, handle, indent=2)
        return 0
    results: Dict[str, Dict[str, Dict[int, List[CaseScore]]]] = {}
    for ingest in (INGESTS if args.ingest == "both" else (args.ingest,)):
        with Harness(chunk_documents(ingest=ingest), embedder) as harness:
            results[ingest] = run(harness, engines, ks)
    print(format_report(results))
    embedder.save()
    print(f"\n{embedder.calls} embedding calls (passages and questions not already in the cache, each once)", file=sys.stderr)
    if args.save:
        os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
        with open(args.save, "w", encoding="utf-8") as handle:
            json.dump(to_record(results), handle, indent=2)
        print(f"saved to {args.save}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from backend.utils import llm_usage

    llm_usage.mark_as_eval()  # counted on the usage page as eval spend, but never toward the budget that can pause chat
    raise SystemExit(main())
