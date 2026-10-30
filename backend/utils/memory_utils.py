import os
import json
import threading
import time
import uuid
import logging
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
from pydantic import BaseModel, Field

from backend.utils.db_utils import get_db
from backend.utils.embedding_utils import embed_text, cosine_similarity
from backend.components.constraints import FACT_CONFLICT_PROMPT, GOAL_STATUS_PROMPT
from backend.models.models import lite_llm
from backend.utils import conversation_people, fact_links, name_aliases
from backend.services.memory_search import flag_stale_chunks_for_superseded_fact

logger = logging.getLogger("SASS Logger")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "memory")
os.makedirs(DATA_DIR, exist_ok=True)

# WHICH PART OF THEIR LIFE a fact belongs to, separate from what kind of fact it is: "work" (anything done for or at their employer or a client: the job, its tasks, tools
# and colleagues), "own" (their own projects, done for themselves), "personal" (life outside both) or "general" (true everywhere: a name, pronouns, tastes, how they like
# answers written). Every NEW fact gets one of the four ("general" when nothing else is clearly right: a wrong label is worse than a broad one). None means "never
# classified" (a fact from before this existed), so the one-time cleanup can tell what it still has to sort. It exists because "work" is ambiguous for a person who also builds
# things at home: a flat list of facts let Sonic merge the job's parser with its own development ("the parser nightmares we deal with").
VALID_DOMAINS = ("work", "own", "personal", "general")
DOMAIN_LABELS = {"work": "At their job", "own": "Their own projects", "personal": "Their personal life", "general": "About them in general"}
SPLIT_DOMAINS = ("work", "own", "personal")     # the ones that make the headings worth showing
MAX_ALWAYS_IDENTITY = 8                         # how many foundational identity facts every reply gets, whatever the question


def normalize_domain(value) -> Optional[str]:
    """"work", "own", "personal" or "general" when `value` is one of them (any case), else None."""
    word = str(value or "").strip().lower()
    return word if word in VALID_DOMAINS else None


VALID_CATEGORIES = {
    "preference", "identity", "setting", "trait",
    "career", "project", "goal", "relationship",
    "pattern",  # a synthesized observation across several facts, not an atomic fact itself
    "coding_style",  # how THIS user likes code/tests/PRs written — see fetch_coding_preferences;
                      # injected into the coding agent's own prompt, not the voice composer's.
}
FACT_SIMILARITY_THRESHOLD = float(os.getenv("FACT_SIMILARITY_THRESHOLD", "0.80"))
# Domain fallback for a NEW fact the model could not place (None or "general"): the fact takes "work" or "own" only when its nearest facts in that domain are at least
# DOMAIN_FALLBACK_MIN_SIM similar and beat the other domain by DOMAIN_FALLBACK_MARGIN. Placeholder defaults: set them from the dry-run numbers. Not FACT_SIMILARITY_THRESHOLD,
# which is the "same fact" bar.
DOMAIN_FALLBACK_MIN_SIM = float(os.getenv("DOMAIN_FALLBACK_MIN_SIM", "0.55"))
DOMAIN_FALLBACK_MARGIN = float(os.getenv("DOMAIN_FALLBACK_MARGIN", "0.05"))
FACT_REINFORCEMENT_INCREMENT = float(os.getenv("FACT_REINFORCEMENT_INCREMENT", "0.15"))
FACT_CONFIDENCE_HALF_LIFE_DAYS = float(os.getenv("FACT_CONFIDENCE_HALF_LIFE_DAYS", "90"))
# How long a goal/project fact can go unmentioned before it's worth a conversational check-in —
# see find_stale_goal_to_nudge. Deliberately separate from FACT_CONFIDENCE_HALF_LIFE_DAYS: decay
# is about fading old facts OUT of context, staleness here is about the opposite (surfacing them).
GOAL_NUDGE_INTERVAL_DAYS = float(os.getenv("GOAL_NUDGE_INTERVAL_DAYS", "14"))
# The fact list is stored as one array on one MongoDB document (see save_user_facts), so it needs a cap that keeps it far from Mongo's 16MB single-document limit.
# The cap was 400 when every fact carried its embedding as 768 doubles (~12-15KB each). Since the embeddings were split out and packed as 16-bit floats, a fact costs
# about 2 KB in all (measured 2026-10-23 on the busiest account: 400 facts = 0.8 MB), so 1,500 facts is about 3 MB, and the limit would take about 8,000. At 400 the busiest
# account was full and had already had 597 facts evicted (inferred ones first, as designed), which is why it was raised. Past a few thousand facts the answer is one
# document per fact with a server-side vector index, not a bigger cap.
MAX_STORED_FACTS = int(os.getenv("MAX_STORED_FACTS", "1500"))
# A separate, deliberately uncapped collection (one document per archived fact, not another
# growing array) — so pruning a fact out of the hot MAX_STORED_FACTS-bounded store never
# destroys it outright, only moves it out of active recall. Recoverable, not just logged.
FACT_ARCHIVE_COLLECTION = "user_memory_fact_archive"
FACTS_COLLECTION = "user_memory_facts"
EMBEDDINGS_COLLECTION = "user_memory_fact_embeddings"

# Where a fact came from decides how long it is kept when the cap forces facts out, and whether a later re-observation may relabel it.
# "safety_support" (the people someone said they can turn to, read by the safety layer: backend/utils/safety_utils.py, SUPPORT_FACT_SOURCE,
# which a test keeps equal) is never pruned and never relabelled. Then what the user stated ("explicit"), then recurring patterns, then facts a
# model inferred from compacted summaries. A fact nobody told Sonic should not push out one somebody did.
SUPPORT_SOURCE = "safety_support"
PROTECTED_SOURCES = (SUPPORT_SOURCE,)
# A note the person rewrote on the Memory page: their wording outranks everything a model extracted, so it is the last to be pruned and a model
# restating the same thing later does not replace it (see save_user_fact). Not in PROTECTED_SOURCES: those are never pruned at all, and a person
# editing many notes must not be able to push the store past its cap.
USER_EDIT_SOURCE = "user_edit"
SOURCE_STRENGTH = {SUPPORT_SOURCE: 3, USER_EDIT_SOURCE: 3, "explicit": 2, "pattern": 1, "inferred": 0}
MAX_FACT_CHARS = 500
DEFAULT_SOURCE_STRENGTH = 2  # a source nobody listed is treated like one the user stated


def source_strength(source: Optional[str]) -> int:
    return SOURCE_STRENGTH.get(source or "", DEFAULT_SOURCE_STRENGTH)


class UserFact(BaseModel):
    id: str
    username: str
    category: str
    fact: str
    source: str = "explicit"
    confidence: float = 1.0
    created_at: str
    updated_at: str
    active: bool = True
    embedding: Optional[List[float]] = None
    goal_status: Optional[str] = None  # "active"|"achieved"|"abandoned"; only meaningful when
                                        # category is "goal"/"project", else always None
    last_nudged_at: Optional[str] = None  # None until find_stale_goal_to_nudge first surfaces it
    domain: Optional[str] = None  # "work" | "own" | "personal" | None (not known): which part of their life this is about (see VALID_DOMAINS)
    domain_locked: bool = False   # the person chose the domain themselves (Memory page): a model re-observing the fact, or a cleanup run, never changes it
    related: List[str] = Field(default_factory=list)  # ids of facts about the same real thing (backend/utils/fact_links.py); recalling one brings these along


def fact_for_state(fact: UserFact) -> dict:
    """A fact as it should appear in graph state: everything except its embedding. The embedding
    (~12-15KB as JSON) is only ever used for similarity matching inside this module, but anything
    in graph state is re-saved in EVERY checkpoint — 345 facts' embeddings made each checkpoint
    ~3.5MB, and with one checkpoint per graph step that alone filled the 512MB Atlas quota in a
    day. Nothing reads state["memory_facts"] back for the embeddings."""
    return fact.model_dump(exclude={"embedding"})


def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")


# THE FACT LIST IS ONE LARGE DOCUMENT (every fact carries its embedding: ~4 MB for 400 facts), and a chat turn reads it several times. Against
# a database that serves bulk reads at ~0.1 MB/s (measured: 41 s per read, 0.02 s of that to decode), each read blocked the server's event loop
# for most of a minute, a turn blocked it for minutes, requests queued behind it, and the health check saw the app as down. So the list is kept
# in memory per user: a read after the first is instant, a write replaces it, and when it is older than FACTS_CACHE_FRESH_SECONDS it is still
# served at once while a background thread fetches the new copy. Only the very first read in a process waits for the database, and callers on the
# event loop do that read in a worker thread (app.py, tool_agent_node). With several server instances a change made on another one shows up
# after at most that interval.
FACTS_CACHE_FRESH_SECONDS = float(os.getenv("FACTS_CACHE_FRESH_SECONDS", "600"))
_facts_cache: dict = {}  # username -> (monotonic time stored, [UserFact])
# The stamp of the stored list the copy in memory matches (`rev` on the facts document, set by every write): a refresh first reads just the stamp (a few bytes) and re-reads the
# whole list (~2 KB a fact) only when someone else has written since, instead of every FACTS_CACHE_FRESH_SECONDS whether or not anything changed.
_facts_rev: dict = {}
_facts_refreshing: set = set()
_facts_lock = threading.Lock()


def clear_facts_cache() -> None:
    """Forgets every cached fact list (tests, and anything that changed the database behind this module's back)."""
    with _facts_lock:
        _facts_cache.clear()
        _facts_rev.clear()
        _facts_refreshing.clear()


# WHERE THE EMBEDDINGS LIVE. The fact list used to be one document with every fact's embedding as a list of 768 doubles (~4 MB for 345 facts), and
# reading it cold took ~41 s on the slow link. The text of the facts is small (~100 KB); it is the embeddings that are heavy. So they are kept apart,
# in their own document (EMBEDDINGS_COLLECTION, one per user) and as packed 16-bit floats (1.5 KB a fact, about a quarter of the old size even before
# the document split): the facts document is marked `embeddings_split` and carries no embeddings, and a load reads both and puts each embedding
# back on its fact, so nothing else in the app sees a difference. 16-bit precision moves a cosine similarity by about 1e-4, far inside the thresholds.
# A document written before this change (embeddings inline, no marker) still loads as it was, and is converted in the background the first time.
def pack_embedding(vector: List[float]) -> bytes:
    return np.asarray(vector, dtype=np.float16).tobytes()


def unpack_embedding(blob: bytes) -> List[float]:
    return np.frombuffer(bytes(blob), dtype=np.float16).astype(np.float64).tolist()


def _fetch_facts_from_db(db, username: str) -> List[UserFact]:
    doc = db[FACTS_COLLECTION].find_one({"username": username})
    with _facts_lock:
        if doc and doc.get("rev") is not None:
            _facts_rev[username] = doc.get("rev")
        else:
            _facts_rev.pop(username, None)
    if not doc or "facts" not in doc:
        return []
    facts = [UserFact(**item) for item in doc["facts"]]
    if doc.get("embeddings_split"):
        stored = ((db[EMBEDDINGS_COLLECTION].find_one({"username": username}) or {}).get("vectors")) or []
        by_id = {item["id"]: item["v"] for item in stored if isinstance(item, dict) and "id" in item and "v" in item}
        for fact in facts:
            blob = by_id.get(fact.id)
            if blob is not None and fact.embedding is None:
                fact.embedding = unpack_embedding(blob)
    elif any(f.embedding for f in facts):
        _migrate_to_split_in_background(db, username, facts)
    return facts


def _write_facts(db, username: str, facts: List[UserFact], only_if_unsplit: bool = False) -> bool:
    """Writes the fact list in the split layout: the embeddings document first (so a failure between the two writes can leave vectors with no facts,
    never facts that lost theirs), then the facts without embeddings. With `only_if_unsplit` the facts write happens only while the stored document is still
    in the old layout, so the background conversion can never overwrite a newer list saved in the meantime."""
    if facts:
        # A list of {id, v} rather than a map keyed by fact id: a document's keys may not contain dots or start with "$", an id's characters are not ours to promise.
        vectors = [{"id": f.id, "v": pack_embedding(f.embedding)} for f in facts if f.embedding]
        db[EMBEDDINGS_COLLECTION].update_one({"username": username}, {"$set": {"vectors": vectors}}, upsert=True)
    else:
        db[EMBEDDINGS_COLLECTION].delete_one({"username": username})  # forgetting everything leaves no vectors behind either
    query = {"username": username, "embeddings_split": {"$ne": True}} if only_if_unsplit else {"username": username}
    rev = _next_rev()      # a stamp nobody else can repeat: another instance's write is then seen as a change by the cheap check in the refresh
    result = db[FACTS_COLLECTION].update_one(
        query, {"$set": {"facts": [f.model_dump(exclude={"embedding"}) for f in facts], "embeddings_split": True, "rev": rev}}, upsert=not only_if_unsplit
    )
    changed = bool(getattr(result, "modified_count", 1))
    if changed:
        with _facts_lock:
            _facts_rev[username] = rev
    return changed


_facts_migrating: set = set()


def _migrate_to_split_in_background(db, username: str, facts: List[UserFact]) -> None:
    with _facts_lock:
        if username in _facts_migrating:
            return
        _facts_migrating.add(username)

    def work() -> None:
        try:
            if _write_facts(db, username, facts, only_if_unsplit=True):
                logger.info("[MemoryUtils] Moved %d facts' embeddings out of the facts document for %s.", len(facts), username)
        except Exception:
            logger.exception("[MemoryUtils] Could not convert the saved facts to the slim layout for %s; they still load as before.", username)
        finally:
            with _facts_lock:
                _facts_migrating.discard(username)

    threading.Thread(target=work, name="facts-slim", daemon=True).start()


_last_rev = 0


def _next_rev() -> int:
    """A revision stamp that is larger than every one this process has made (the clock alone can repeat inside one tick)."""
    global _last_rev
    with _facts_lock:
        _last_rev = max(time.time_ns(), _last_rev + 1)
        return _last_rev


def _stored_rev_matches(db, username: str) -> bool:
    """Whether the stored list still has the stamp of the copy held in memory (one tiny read). False whenever that cannot be told: no stamp held or stored (a list written before
    stamps existed), or a database that does not take a projection."""
    with _facts_lock:
        held = _facts_rev.get(username)
    if held is None:
        return False
    try:
        meta = db[FACTS_COLLECTION].find_one({"username": username}, {"rev": 1})
    except Exception:
        return False
    return bool(meta) and meta.get("rev") == held


def _refresh_facts_in_background(db, username: str) -> None:
    with _facts_lock:
        if username in _facts_refreshing:
            return
        _facts_refreshing.add(username)

    def work() -> None:
        try:
            if _stored_rev_matches(db, username):
                with _facts_lock:            # nothing was written since the copy in memory was taken: it is current, and a few bytes were all it took to know
                    held = _facts_cache.get(username)
                    if held is not None:
                        _facts_cache[username] = (time.monotonic(), held[1])
                return
            fresh = _fetch_facts_from_db(db, username)
            with _facts_lock:
                _facts_cache[username] = (time.monotonic(), fresh)
        except Exception:
            logger.exception("[MemoryUtils] Background refresh of the saved facts failed for %s; keeping the copy in memory.", username)
        finally:
            with _facts_lock:
                _facts_refreshing.discard(username)

    threading.Thread(target=work, name="facts-refresh", daemon=True).start()


def _cached_facts(db, username: str) -> List[UserFact]:
    """Every saved fact (active or not) from memory when held, else from the database (the one read that can be slow). Copies, so a caller
    that edits a fact's fields cannot change what the next caller sees until it saves."""
    with _facts_lock:
        entry = _facts_cache.get(username)
    if entry is not None:
        if time.monotonic() - entry[0] > FACTS_CACHE_FRESH_SECONDS:
            _refresh_facts_in_background(db, username)
        return [f.model_copy() for f in entry[1]]
    facts = _fetch_facts_from_db(db, username)
    with _facts_lock:
        _facts_cache[username] = (time.monotonic(), facts)
    return [f.model_copy() for f in facts]


def load_user_facts(username: str, category: Optional[str] = None) -> List[UserFact]:
    """Loads persistent facts from MongoDB (through the in-memory copy above), falling back to a local JSON file."""
    facts: List[UserFact] = []
    db = get_db()

    if db is not None:
        facts = _cached_facts(db, username)
    else:
        path = _get_user_file(username)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    facts = [UserFact(**item) for item in json.load(f)]
            except Exception:
                logger.exception("Failed to load local memory file for %s", username)
                facts = []

    facts = [f for f in facts if f.active]
    if category:
        facts = [f for f in facts if f.category == category]
    return facts


def save_user_facts(username: str, facts: List[UserFact]) -> None:
    """Saves the full fact list to MongoDB, or to the local JSON fallback when Mongo isn't
    configured — mirroring load_user_facts' own either/or (not both) source of truth."""
    facts_dicts = [f.dict() for f in facts]

    db = get_db()
    if db is not None:
        _write_facts(db, username, facts)
        with _facts_lock:  # what was just written is the newest copy there is
            _facts_cache[username] = (time.monotonic(), [f.model_copy() for f in facts])
    else:
        path = _get_user_file(username)
        with open(path, "w") as f:
            json.dump(facts_dicts, f, indent=2)


def find_similar_fact(facts: List[UserFact], fact_text: str) -> Optional[UserFact]:
    """Naive text-overlap match across ALL categories, used to update an existing fact instead
    of duplicating it. Deliberately not category-scoped — see _find_best_embedding_match for why
    a same-category restriction here is actively harmful, not just narrow."""
    candidate = fact_text.strip().lower()
    for existing in facts:
        if not existing.active:
            continue
        existing_text = existing.fact.strip().lower()
        if existing_text == candidate or existing_text in candidate or candidate in existing_text:
            return existing
    return None


def _find_best_embedding_match(facts: List[UserFact], embedding: List[float]) -> tuple:
    """Returns (best_matching_fact, similarity) among ALL active facts with an embedding,
    regardless of category.

    Used to be scoped to same-category facts only, which meant the same real-world fact
    extracted into different categories on different turns (a fact-extraction judgment call,
    not something the user controls) could never be recognized as a duplicate of itself — this
    is exactly how "works at Principal" ended up duplicated across identity/career/setting
    instead of ever being caught and merged. Duplication is about MEANING, not which category a
    fact happened to land in; _judge_fact_relationship's semantic "duplicate/supersede/distinct"
    call (not this function) is the actual safety net against merging two facts that are only
    superficially similar in embedding space, so relaxing this filter doesn't weaken that check.
    """
    best_fact, best_sim = None, 0.0
    for existing in facts:
        if not existing.active or not existing.embedding:
            continue
        sim = cosine_similarity(embedding, existing.embedding)
        if sim > best_sim:
            best_fact, best_sim = existing, sim
    return best_fact, best_sim


def _judge_fact_relationship(existing_fact: str, new_fact: str) -> str:
    """Asks the LLM whether a new statement duplicates, supersedes, or is distinct from an
    existing fact. Defaults to 'distinct' (the safe, non-destructive choice) on any failure."""
    try:
        response = lite_llm.invoke(FACT_CONFLICT_PROMPT.format(existing_fact=existing_fact, new_fact=new_fact))
        raw_content = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        action = json.loads(clean_json).get("action")
        return action if action in ("duplicate", "supersede", "distinct") else "distinct"
    except Exception:
        logger.exception("[MemoryUtils] Fact conflict judgment failed; treating as distinct.")
        return "distinct"


def _judge_goal_status(existing_fact: str, new_fact: str) -> str:
    """Asks the LLM whether a new statement — already judged to supersede an existing goal/project
    fact — means that goal/project is now finished, abandoned, or still ongoing. Defaults to
    'active' (no status change) on any failure, mirroring _judge_fact_relationship's safe default."""
    try:
        response = lite_llm.invoke(GOAL_STATUS_PROMPT.format(existing_fact=existing_fact, new_fact=new_fact))
        raw_content = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        status = json.loads(clean_json).get("status")
        return status if status in ("active", "achieved", "abandoned") else "active"
    except Exception:
        logger.exception("[MemoryUtils] Goal status judgment failed; leaving status active.")
        return "active"


def effective_confidence(fact: "UserFact", now: datetime) -> float:
    """Read-time-only decay — never mutates stored confidence, always reflects current trust.
    Identity facts are foundational rather than time-sensitive, so they never decay."""
    if fact.category == "identity":
        return fact.confidence
    try:
        updated = datetime.fromisoformat(fact.updated_at)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        days_since_update = (now - updated).total_seconds() / 86400
    except Exception:
        return fact.confidence
    return fact.confidence * (0.5 ** (days_since_update / FACT_CONFIDENCE_HALF_LIFE_DAYS))


def _archive_dropped_facts(dropped: List["UserFact"]) -> None:
    """Pruning never truly destroys a fact — it's archived here first, so an eviction from the
    hot capped store is always recoverable rather than silently permanent. Best-effort: if Mongo
    isn't configured or the write fails, the fact is still dropped from the live store (archiving
    is a safety net on top of pruning, not a precondition for it)."""
    db = get_db()
    if db is None or not dropped:
        return
    try:
        db[FACT_ARCHIVE_COLLECTION].insert_many([
            {**f.dict(), "archived_at": datetime.now(timezone.utc).isoformat()}
            for f in dropped
        ])
    except Exception:
        logger.exception("[MemoryUtils] Failed to archive %d pruned fact(s).", len(dropped))


def _prune_excess_facts(facts: List["UserFact"], protect_id: Optional[str] = None) -> List["UserFact"]:
    """Keeps the store at or under MAX_STORED_FACTS so it can never approach Mongo's 16MB
    single-document limit. Identity facts are never pruned (foundational, and naturally small
    in number — see fetch_relevant_user_facts' same treatment), and neither are the safety layer's remembered
    support contacts (PROTECTED_SOURCES); `protect_id` additionally shields whichever fact save_user_fact just
    touched, so telling it something never results in that exact fact being evicted in the same call. Among
    everything else the facts a model inferred go first, then recurring patterns, and what the user stated
    last (SOURCE_STRENGTH); within each of those, the lowest-effective-confidence facts (most decayed / least
    reinforced) go first. Confidence alone was not enough: facts promoted out of compaction all carried full
    confidence, so the oldest facts went first whoever had said them. Whatever gets evicted is archived,
    not deleted outright (see _archive_dropped_facts)."""
    if len(facts) <= MAX_STORED_FACTS:
        return facts

    now = datetime.now(timezone.utc)
    def never_pruned(f: "UserFact") -> bool:
        return f.category == "identity" or f.id == protect_id or f.source in PROTECTED_SOURCES

    protected = [f for f in facts if never_pruned(f)]
    other = [f for f in facts if not never_pruned(f)]
    other.sort(key=lambda f: (source_strength(f.source), effective_confidence(f, now)), reverse=True)

    keep_count = max(MAX_STORED_FACTS - len(protected), 0)
    kept_other = other[:keep_count]
    dropped = other[keep_count:]
    if dropped:
        by_source: dict = {}
        for f in dropped:
            by_source[f.source] = by_source.get(f.source, 0) + 1
        logger.info(
            "[MemoryUtils] Pruned %d fact(s) for a user to stay under the %d-fact cap, by source %s "
            "(archived, not deleted).", len(dropped), MAX_STORED_FACTS, by_source,
        )
        _archive_dropped_facts(dropped)
    return protected + kept_other


def save_user_fact(
    username: str,
    fact: str,
    category: str = "preference",
    source: str = "explicit",
    confidence: float = 1.0,
    memory_vector_store=None,
    domain: Optional[str] = None,
) -> UserFact:
    """Saves a durable fact for a user, using embedding similarity + an LLM judgment call to
    detect near-duplicates and contradictions (superseding the old fact) instead of a naive
    text-overlap check. Falls back to that naive check if embedding generation fails.
    `domain` ("work", "own", "personal") says which part of their life it is about; a restatement that gives none keeps the one already stored."""
    if category not in VALID_CATEGORIES:
        category = "preference"
    domain = normalize_domain(domain)           # None here means the caller gave none: a restatement keeps the stored one, a new fact is "general"

    now = datetime.now(timezone.utc).isoformat()
    all_facts = load_user_facts(username)
    fact_text = fact.strip()
    new_embedding = embed_text(fact_text)

    existing = None
    relationship_action = None
    if new_embedding is not None:
        best_match, best_sim = _find_best_embedding_match(all_facts, new_embedding)
        if best_match is not None and best_sim >= FACT_SIMILARITY_THRESHOLD:
            relationship_action = _judge_fact_relationship(best_match.fact, fact_text)
            if relationship_action in ("duplicate", "supersede"):
                existing = best_match
    else:
        # Embeddings unavailable for some reason — fall back to the old naive substring check
        # rather than always creating a new fact. Treated as reinforcement (same fact restated),
        # since there's no LLM judgment available to detect a genuine contradiction here.
        existing = find_similar_fact(all_facts, fact_text)
        if existing is not None:
            relationship_action = "duplicate"

    if existing:
        if relationship_action == "duplicate":
            # Independent re-observation of something already known — reinforce, don't reset.
            existing.confidence = min(1.0, existing.confidence + FACT_REINFORCEMENT_INCREMENT)
        else:  # "supersede" — a genuinely changed fact resets to the passed-in baseline
            existing.confidence = confidence
            # Only for goal/project facts, and only on a genuine update (not a plain restatement,
            # which "duplicate" already means) — checks whether this update means the goal/project
            # is now finished or abandoned, using existing.fact (still the pre-update text here).
            if existing.category in ("goal", "project"):
                new_status = _judge_goal_status(existing.fact, fact_text)
                if new_status in ("achieved", "abandoned"):
                    existing.goal_status = new_status
            # Capture the old embedding NOW — this is the last point at which existing.embedding
            # still describes what's being superseded, right before it's overwritten below. A
            # stale compacted chunk that still says the OLD thing can otherwise get "genuinely
            # recalled" right alongside the corrected fact with nothing to arbitrate between them.
            flag_stale_chunks_for_superseded_fact(memory_vector_store, username, existing.embedding)
        if relationship_action == "supersede" and existing.related:
            # What the fact says has changed ("works at Principal" became "works at Acme"), so what it was bound to may no longer belong with it:
            # the links are cleared both ways and judged again for the new wording below.
            for other in all_facts:
                if existing.id in other.related:
                    other.related = [linked for linked in other.related if linked != existing.id]
            existing.related = []
        # A note the person wrote themselves keeps its wording when a model merely restates it; a real change (supersede) still replaces it.
        keeps_wording = relationship_action == "duplicate" and existing.source == USER_EDIT_SOURCE
        if not keeps_wording:
            existing.fact = fact_text
            # Corrects category drift on every re-observation instead of freezing whichever category
            # the fact happened to get on its very first (possibly inconsistent) extraction — this is
            # the other half of fixing the identity/career/setting fragmentation bug: cross-category
            # matching finds the old fact, and this line stops it from keeping a stale category.
            existing.category = category
            existing.embedding = new_embedding if new_embedding is not None else existing.embedding
        if relationship_action == "duplicate":
            # the same fact seen again keeps the strongest evidence for it: a fact the user stated stays "explicit" when compaction later
            # restates it as "inferred" (it used to be relabelled, which made it the first to be pruned)
            if source_strength(source) > source_strength(existing.source):
                existing.source = source
        elif existing.source not in PROTECTED_SOURCES:
            existing.source = source  # changed content has its own provenance; a remembered support contact stays one
        existing.updated_at = now
        if domain and not keeps_wording and not existing.domain_locked:
            existing.domain = domain
        result = existing
        if relationship_action == "supersede":
            fact_links.link_new_fact(all_facts, result, cosine_similarity, lite_llm.invoke)
    else:
        result = UserFact(
            id=str(uuid.uuid4()),
            username=username,
            category=category,
            fact=fact_text,
            source=source,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            active=True,
            embedding=new_embedding,
            goal_status="active" if category in ("goal", "project") else None,
            domain=domain or "general",
        )
        all_facts.append(result)
        # Bind it to the facts that are about the same thing (it may say "at work" without ever naming the employer).
        fact_links.link_new_fact(all_facts, result, cosine_similarity, lite_llm.invoke)

    all_facts = _prune_excess_facts(all_facts, protect_id=result.id)
    fact_links.drop_dangling_links(all_facts)     # a pruned fact leaves no link behind
    save_user_facts(username, all_facts)
    logger.info("Saved memory fact for %s: [%s] (%d chars, id %s)", username, category, len(result.fact), result.id)
    return result


def delete_user_fact(username: str, fact_id: str) -> bool:
    all_facts = load_user_facts(username)
    remaining = [f for f in all_facts if f.id != fact_id]
    if len(remaining) == len(all_facts):
        return False
    fact_links.drop_dangling_links(remaining)     # nothing stays linked to what was just forgotten
    save_user_facts(username, remaining)
    return True


class FactEditError(ValueError):
    """The edit cannot be made; the message is safe to show the person."""


def edit_user_fact(
    username: str, fact_id: str, text: Optional[str], category: Optional[str] = None, memory_vector_store=None, domain: Optional[str] = None,
) -> Optional[UserFact]:
    """The person rewrites a saved note on the Memory page, and/or says which part of their life it belongs to. Their wording replaces the old one and is trusted
    over anything a model extracted: the note is re-embedded (recall finds it by its new meaning), marked as theirs, and given full confidence; its links are cleared
    and judged again, as for any changed fact. `text` None keeps the wording (a change of domain or category alone). A DOMAIN change alone touches nothing else: no
    embedding, no model call, the same source and confidence (the person is correcting a label, not restating the fact), and the domain is locked so nothing re-files it.
    Returns None when there is no such note; raises FactEditError for text that cannot be saved."""
    if text is None:
        new_text = None
    else:
        new_text = " ".join(text.split())
        if not new_text:
            raise FactEditError("A memory can't be empty. Use the bin icon to forget it instead.")
        if len(new_text) > MAX_FACT_CHARS:
            raise FactEditError(f"Keep it under {MAX_FACT_CHARS} characters.")
    if category is not None and category not in VALID_CATEGORIES:
        raise FactEditError("That isn't a category Sonic uses.")
    if domain is not None and normalize_domain(domain) is None:
        raise FactEditError("That isn't a part of your life Sonic files memories under.")
    domain = normalize_domain(domain)

    all_facts = load_user_facts(username)
    fact = next((f for f in all_facts if f.id == fact_id), None)
    if fact is None:
        return None
    if new_text is None:
        new_text = fact.fact
    domain_changed = domain is not None and (domain != fact.domain or not fact.domain_locked)
    if new_text == fact.fact and (category is None or category == fact.category):
        if not domain_changed:
            return fact                               # nothing changed: no embedding, no model call, no write
        fact.domain, fact.domain_locked = domain, True
        save_user_facts(username, all_facts)
        logger.info("User set the domain of a memory fact for %s (id %s).", username, fact.id)
        return fact

    text_changed = new_text != fact.fact
    if text_changed:
        embedding = embed_text(new_text)
        if embedding is None:
            raise FactEditError("Couldn't save that just now. Please try again.")   # never keep new words on an embedding of the old ones
        # A compacted note that still says the old thing would otherwise be recalled beside the corrected one.
        flag_stale_chunks_for_superseded_fact(memory_vector_store, username, fact.embedding)
        for other in all_facts:
            if fact.id in other.related:
                other.related = [linked for linked in other.related if linked != fact.id]
        fact.related = []
        fact.fact = new_text
        fact.embedding = embedding
    if category is not None:
        fact.category = category
        if category in ("goal", "project") and fact.goal_status is None:
            fact.goal_status = "active"
        elif category not in ("goal", "project"):
            fact.goal_status = None
    if domain is not None:
        fact.domain, fact.domain_locked = domain, True
    if fact.source not in PROTECTED_SOURCES:
        fact.source = USER_EDIT_SOURCE                # a remembered support contact stays one
    fact.confidence = 1.0                             # they said it themselves
    fact.updated_at = datetime.now(timezone.utc).isoformat()
    if text_changed and fact.embedding:
        fact_links.link_new_fact(all_facts, fact, cosine_similarity, lite_llm.invoke)
    save_user_facts(username, all_facts)
    logger.info("User edited memory fact for %s (id %s, %d chars)", username, fact.id, len(fact.fact))
    return fact


def link_user_facts(username: str, fact_id: str, other_id: str) -> bool:
    """Links two saved facts both ways because the person said they belong together (no embedding or model guess), within the per-fact cap. False when either is
    gone, they are the same fact, they were already linked, or a cap stopped it."""
    all_facts = load_user_facts(username)
    by_id = {f.id: f for f in all_facts}
    first, second = by_id.get(fact_id), by_id.get(other_id)
    if first is None or second is None or first.id == second.id:
        return False
    if not fact_links.add_links(first, [second]):
        return False
    save_user_facts(username, all_facts)
    return True


def unlink_user_facts(username: str, fact_id: str, other_id: str) -> bool:
    """Removes the link between two facts, both ways (the person disagrees that they belong together). False when there was no such link."""
    all_facts = load_user_facts(username)
    by_id = {f.id: f for f in all_facts}
    first, second = by_id.get(fact_id), by_id.get(other_id)
    if first is None or second is None or other_id not in first.related:
        return False
    first.related = [linked for linked in first.related if linked != other_id]
    second.related = [linked for linked in second.related if linked != fact_id]
    save_user_facts(username, all_facts)
    return True


def linked_fact_summaries(fact: "UserFact", by_id: dict, limit: int = 6) -> list:
    """[{id, fact}] for the facts this one is linked with that still exist and are active, for showing on the Memory page."""
    out = []
    for linked_id in fact.related:
        other = by_id.get(linked_id)
        if other is not None and other.active:
            out.append({"id": other.id, "fact": other.fact})
        if len(out) >= limit:
            break
    return out


def delete_all_user_facts(username: str) -> None:
    save_user_facts(username, [])


def find_stale_goal_to_nudge(username: str, interval_days: Optional[float] = None) -> Optional["UserFact"]:
    """Finds the single most-overdue active goal/project fact worth checking in on: category in
    {"goal","project"}, goal_status == "active", and it's been at least `interval_days` since the
    LATER of (updated_at, last_nudged_at) — using last_nudged_at as a floor is what makes this a
    periodic cadence rather than a one-shot: without it, nudging would itself reset the very
    staleness clock that triggered the nudge. Returns at most one fact so a single turn never
    surfaces more than one check-in. Never raises."""
    threshold = interval_days if interval_days is not None else GOAL_NUDGE_INTERVAL_DAYS
    try:
        facts = load_user_facts(username)
        candidates = [
            f for f in facts
            if f.category in ("goal", "project") and (f.goal_status or "active") == "active"
        ]
        if not candidates:
            return None

        now = datetime.now(timezone.utc)

        def _parse(ts: str) -> datetime:
            dt = datetime.fromisoformat(ts)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        overdue = []
        for f in candidates:
            try:
                reference = _parse(f.updated_at)
                if f.last_nudged_at:
                    nudged = _parse(f.last_nudged_at)
                    if nudged > reference:
                        reference = nudged
                days_stale = (now - reference).total_seconds() / 86400
            except Exception:
                continue
            if days_stale >= threshold:
                overdue.append((days_stale, f))

        if not overdue:
            return None
        overdue.sort(key=lambda item: item[0], reverse=True)
        return overdue[0][1]
    except Exception:
        logger.exception("[MemoryUtils] Could not evaluate stale goals for %s", username)
        return None


def fetch_goal_nudge_context(username: str) -> str:
    """Builds a short, explicitly-optional 'STALE GOAL CHECK-IN' block for passive per-turn
    injection — a sibling to fetch_relevant_user_facts, called the same way from app.py. Surfaces
    at most one stale goal/project fact and stamps its last_nudged_at so the periodic cadence is
    measured from the nudge itself, not just the last time the user mentioned it. Never raises —
    mirrors fetch_relevant_user_facts' try/except-returns-"" pattern."""
    try:
        fact = find_stale_goal_to_nudge(username)
        if not fact:
            return ""

        all_facts = load_user_facts(username)
        now_iso = datetime.now(timezone.utc).isoformat()
        for f in all_facts:
            if f.id == fact.id:
                f.last_nudged_at = now_iso
        save_user_facts(username, all_facts)

        return (
            "\n\nSTALE GOAL CHECK-IN (this is optional and conversational — only weave it in if "
            "it fits naturally, never force it into an unrelated reply):\n"
            f"- The user hasn't mentioned this {fact.category} in a while: \"{fact.fact}\". If it "
            "feels natural, you may casually ask how it's going.\n"
        )
    except Exception:
        logger.exception("[MemoryUtils] Could not build goal nudge context for %s", username)
        return ""


def fetch_relevant_user_facts(
    username: str, question: str, limit: int = 5, precomputed_embedding: Optional[List[float]] = None, messages=None,
) -> str:
    """
    Builds a short "known about this user" context block for passive injection into every
    generation prompt, mirroring app_utils.fetch_relevant_corrections. Foundational identity
    facts are always included (up to MAX_ALWAYS_IDENTITY; foundational context shouldn't disappear
    just because the current question isn't about it); `limit` slots then go to whichever facts are most relevant to the
    current question by embedding similarity weighted by confidence (reinforced/fresh facts
    outrank decayed ones at equal similarity), falling back to recency if that's unavailable.

    precomputed_embedding lets a caller that's already embedded this exact question elsewhere
    this turn (e.g. retrieve_relevant_memory_context, called with the same question string when
    a turn is classified "conversational") pass it in instead of paying for a second real
    embed_text call against the same text — same model/dimensionality as embed_text uses, so the
    vectors are directly comparable.

    messages: the conversation so far (backend/utils/conversation_people.py). A message with no name in it ("I'm afraid I won't see her again") used to be matched on its words
    alone and picked facts about people who had not come up in weeks; now facts about the people the conversation has been about rank higher, and a reply to a message that names
    nobody is told who they are.
    """
    try:
        facts = load_user_facts(username)
        if not facts:
            return ""

        now = datetime.now(timezone.utc)
        people = conversation_people.people_in_conversation(messages, facts) if messages else []
        # Foundational facts (a name, pronouns, how they address the AI) are always shown, up to MAX_ALWAYS_IDENTITY of them. An identity fact that is about the person's
        # JOB, their own projects or their personal life (domain work / own / personal) is not foundational: it competes by relevance like any other. Before this, every
        # "identity" fact was always shown and took a slot from `limit`, so with twenty of them no fact about the question ever got in ("how is work going" showed no work).
        identity_all = [f for f in facts if f.category == "identity"]
        identity_facts = sorted(
            (f for f in identity_all if f.domain not in SPLIT_DOMAINS), key=lambda f: effective_confidence(f, now), reverse=True,
        )[:MAX_ALWAYS_IDENTITY]
        always_ids = {f.id for f in identity_facts}
        other_facts = [f for f in facts if f.id not in always_ids]
        remaining_slots = max(limit, 0)

        question_embedding = precomputed_embedding
        if question_embedding is None:
            question_embedding = embed_text(question) if question else None
        if question_embedding is not None:
            scored = [
                (
                    (cosine_similarity(question_embedding, f.embedding) if f.embedding else -1.0)
                    * effective_confidence(f, now)
                    * (conversation_people.boost_for(f.fact, people) if people else 1.0),
                    f,
                )
                for f in other_facts
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked_other = [f for _, f in scored]
        else:
            # Reverse before the stable sort so that facts with an identical updated_at
            # timestamp (e.g. two saved within the same tick) still break ties in favor of
            # whichever was appended most recently, rather than falling back to insertion
            # (oldest-first) order. Confidence is a secondary key after recency.
            ranked_other = sorted(
                reversed(other_facts),
                key=lambda f: (f.updated_at, effective_confidence(f, now)),
                reverse=True,
            )

        matched = ranked_other[:remaining_slots]
        # A fact chosen because it matches the question brings along the facts bound to it (the project, and where it is done), so the context the
        # person already gave about the same thing is not lost. Identity facts are always shown anyway and do not bring anything extra.
        top_facts = identity_facts + domain_anchors(other_facts) + matched + fact_links.linked_extras(matched, facts)
        seen_ids: set = set()
        top_facts = [f for f in top_facts if not (f.id in seen_ids or seen_ids.add(f.id))]
        if not top_facts:
            return ""

        return format_facts_block(top_facts) + conversation_people.people_note(people, question)
    except Exception:
        logger.exception("[MEMORY WARNING] Could not fetch user facts for %s", username)
        return ""


def domain_anchors(facts: List["UserFact"]) -> List["UserFact"]:
    """One fact that anchors each of the two parts of life that get confused: the best-evidenced identity or career fact about their JOB (the employer) and about their OWN
    projects, so a vague question ("how is work going?") still shows the model that both exist and which is which. Best means stated by the person over inferred, then an
    identity fact over a career one, then newest. Nothing for a domain with no such fact (including a person whose facts were never sorted)."""
    anchors: List["UserFact"] = []
    for domain in ("work", "own"):
        candidates = [f for f in facts if f.domain == domain and f.category in ("identity", "career") and f.active]
        if candidates:
            anchors.append(max(candidates, key=lambda f: (source_strength(f.source), f.category == "identity", f.updated_at)))
    return anchors


# Said once, with the headings, whenever facts are grouped by domain: it is what stops "work" meaning both the job and the person's own projects, and "we" covering
# work Sonic has no part in.
SEPARATE_LIFE_NOTE = (
    "Their job and their own projects are separate things. When they say \"work\" without saying which, go by what they were just talking about, or ask which they "
    "mean. Never describe what they do at their job as something you and they do together (no \"we\"), and never fold a job task into a project of their own or the reverse."
)


def _plain_cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two vectors in plain Python (0.0 when either is empty or all zeros)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def infer_domain_from_neighbors(new_embedding: Optional[List[float]], facts: List["UserFact"]) -> Optional[str]:
    """"work" or "own" for a new fact whose embedding is clearly nearest one of those domains, else None. Compares it with the active work and own facts that
    have an embedding and are identity, career or project facts (domain_anchors is the sparse core of that set). A domain wins only if its best similarity is at
    least DOMAIN_FALLBACK_MIN_SIM AND beats the other domain's best by at least DOMAIN_FALLBACK_MARGIN. No embedding, no candidates, or no clear winner -> None."""
    if not new_embedding:
        return None
    best = {"work": None, "own": None}
    for f in facts:
        if not f.active or f.domain not in best or not f.embedding or f.category not in ("identity", "career", "project"):
            continue
        sim = _plain_cosine(new_embedding, f.embedding)
        if best[f.domain] is None or sim > best[f.domain]:
            best[f.domain] = sim
    if best["work"] is None and best["own"] is None:
        return None
    work_sim = best["work"] if best["work"] is not None else -1.0
    own_sim = best["own"] if best["own"] is not None else -1.0
    winner, top, runner_up = ("work", work_sim, own_sim) if work_sim >= own_sim else ("own", own_sim, work_sim)
    if top < DOMAIN_FALLBACK_MIN_SIM:
        return None
    if top - runner_up < DOMAIN_FALLBACK_MARGIN:
        return None
    return winner


def format_facts_block(facts: List["UserFact"]) -> str:
    """The facts block (see `_facts_block`), plus a line when facts bound together spell one name two ways ("Ashlyn" / "Ashlynn": backend/utils/name_aliases.py) saying which spelling to
    use. The line is the exception: the people's own settings are read only when such a pair is actually found."""
    body = _facts_block(facts)
    if not body or not name_aliases.variant_groups(facts):
        return body
    return body + name_aliases.facts_note(facts, name_aliases.denied_for(getattr(facts[0], "username", "")))


def _saved_on(fact: "UserFact") -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(fact.created_at or fact.updated_at))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def dated_fact_texts(facts: List["UserFact"], now: Optional[datetime] = None) -> List[str]:
    """Each fact's text, with the relative days in it ("today", "tomorrow", "tonight", "this week") followed by the real date they meant WHEN THE FACT WAS SAVED, for a fact saved on an
    earlier day. "The user has a job interview at 2pm today", saved on Oct 6, was read on Oct 22 as an interview today. A fact saved today, or one with no relative day, is unchanged,
    and stored facts are never rewritten (this is only how they are shown). Never raises."""
    texts = [f.fact for f in facts]
    try:
        from backend.utils import date_resolve
        from backend.utils.time_utils import local_now

        moment = now or datetime.now(timezone.utc)
        tz_name = None
        looked_up = False
        for index, fact in enumerate(facts):
            saved = _saved_on(fact)
            if saved is None or (moment - saved).total_seconds() < 20 * 3600:
                continue                                  # saved within the last day: its "today" may still be today (tz is checked below only when it matters)
            if date_resolve.annotate_dates(fact.fact, "UTC", saved) == fact.fact:
                continue                                  # no relative day in it: nothing to date, and no timezone lookup
            if not looked_up:
                from backend.utils.user_settings_utils import get_user_timezone

                tz_name, looked_up = get_user_timezone(fact.username), True
            if local_now(tz_name, saved).date() == local_now(tz_name, moment).date():
                continue
            texts[index] = date_resolve.annotate_dates(fact.fact, tz_name, saved)
    except Exception:
        logger.warning("[memory] could not date the relative days in the saved facts.", exc_info=True)
    return texts


def _facts_block(facts: List["UserFact"]) -> str:
    """The "known about this person" block for a reply prompt. When any fact is about their job, their own projects or their personal life, the facts are grouped under a
    heading for each (At their job / Their own projects / Their personal life, then "About them in general") with the SEPARATE_LIFE_NOTE; a fact never classified sits
    under "Other". When none is, it is the plain list it has always been. Empty for no facts. A fact saved on an earlier day shows the real date of its relative days
    (dated_fact_texts)."""
    if not facts:
        return ""
    shown = dict(zip((id(f) for f in facts), dated_fact_texts(facts)))
    header = "\n\nKNOWN USER CONTEXT (use naturally, do not restate unless relevant):\n"
    if not any(f.domain in SPLIT_DOMAINS for f in facts):
        return header + "\n".join(f"- {shown[id(f)]}" for f in facts) + "\n"
    sections = []
    for domain in VALID_DOMAINS:
        group = [f for f in facts if f.domain == domain]
        if group:
            sections.append(f"{DOMAIN_LABELS[domain]}:\n" + "\n".join(f"- {shown[id(f)]}" for f in group))
    rest = [f for f in facts if f.domain not in VALID_DOMAINS]
    if rest:
        sections.append("Other:\n" + "\n".join(f"- {shown[id(f)]}" for f in rest))
    return header + "\n".join(sections) + "\n" + SEPARATE_LIFE_NOTE + "\n"


# How close (raw cosine) a fact must be to a request to count as about it. Measured on real facts: unrelated technical questions top out at 0.58,
# questions about the user's own projects start at 0.67, so 0.62 separates them. Facts are short and specific, unlike recalled notes.
RELEVANT_FACT_MIN_SIMILARITY = float(os.getenv("RELEVANT_FACT_MIN_SIMILARITY", "0.62"))


def select_relevant_user_facts(
    username: str, question: str, limit: int = 4, min_similarity: Optional[float] = None, precomputed_embedding: Optional[List[float]] = None,
) -> List["UserFact"]:
    """The saved facts that are actually about `question`, best first: at least `min_similarity` close to it (any category), ranked by
    similarity times effective confidence. Unlike fetch_relevant_user_facts, identity facts get no automatic seat: they compete like the
    rest, so a person with many of them still gets the facts relevant to the request. [] when none is close enough. Never raises."""
    floor = RELEVANT_FACT_MIN_SIMILARITY if min_similarity is None else min_similarity
    try:
        if not question or not question.strip():
            return []
        facts = [f for f in load_user_facts(username) if f.embedding]
        if not facts:
            return []
        embedding = precomputed_embedding if precomputed_embedding is not None else embed_text(question)
        if embedding is None:
            return []
        now = datetime.now(timezone.utc)
        scored = [(cosine_similarity(embedding, f.embedding), f) for f in facts]
        close = [(sim * effective_confidence(f, now), f) for sim, f in scored if sim >= floor]
        close.sort(key=lambda pair: pair[0], reverse=True)
        chosen = [f for _, f in close[:limit]]
        return chosen + fact_links.linked_extras(chosen, facts)
    except Exception:
        logger.exception("[MEMORY WARNING] Could not select relevant facts for %s", username)
        return []


def fetch_coding_preferences(username: str, limit: int = 10) -> str:
    """Sibling to fetch_relevant_user_facts, for the coding agent's own prompt (TOOL_AGENT_PROMPT)
    rather than the voice composer's. "coding_style" facts ("always use monkeypatch-style tests",
    "never use --no-verify", "prefer small PRs") apply to every coding task regardless of what
    the current question is about — the same always-relevant treatment identity facts get in
    fetch_relevant_user_facts — so this surfaces all of them (ranked by effective confidence, not
    by relevance to the current question) rather than running a per-question similarity search."""
    try:
        facts = load_user_facts(username, category="coding_style")
        if not facts:
            return ""

        now = datetime.now(timezone.utc)
        ranked = sorted(facts, key=lambda f: effective_confidence(f, now), reverse=True)[:limit]

        lines = "\n".join(f"- {f.fact}" for f in ranked)
        return (
            "\n\nKNOWN CODING PREFERENCES (how this user likes code/tests/PRs written — follow "
            f"these the same way you would an explicit instruction):\n{lines}\n"
        )
    except Exception:
        logger.exception("[MEMORY WARNING] Could not fetch coding preferences for %s", username)
        return ""
