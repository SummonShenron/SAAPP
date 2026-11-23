import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional

from bson import ObjectId
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_mongodb import MongoDBAtlasVectorSearch

from backend.utils.time_utils import describe_gap

logger = logging.getLogger("SASS Logger")

USER_MEMORY_COLLECTION = "user_memory_chunks"
USER_MEMORY_INDEX = "user_memory_vector_index"
# Where compaction copies the notes it merges, so a merge can be undone. Personal data like the rest: "Clear All" must empty it too.
CHUNK_ARCHIVE_COLLECTION = "user_memory_chunk_archive"

# Atlas Vector Search scores are already normalized to [0, 1] (langchain_mongodb's default
# relevance_score_fn="cosine" is the identity function on that score), so this threshold is
# on that same scale (it is (1 + cosine) / 2, so 0.75 is only a cosine of 0.5).
# 0.80, not 0.75: measured on the real store (backend/evals/memory_eval.py), questions about nothing stored scored 0.76 to 0.82 against
# the closest notes, so at 0.75 every turn was handed three unrelated "recalled" lines. Notes that genuinely answer a question score
# about 0.80 to 0.90. Tune with the env var; re-run the eval after any change.
DEFAULT_RECALL_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_RECALL_SIMILARITY_THRESHOLD", "0.80"))
# How close a note must be to a superseded fact to be hidden as outdated. On the SAME Atlas scale as the recall cutoff above, NOT the raw
# cosine compaction clusters on, so the old 0.85 (a cosine of only 0.70) was far looser than it looked. It never mattered while the flagger
# could not match any note (see mongo_ids); measured on the real store once it could, at 0.85 about 5 notes would be hidden per superseded fact
# (54 of 60 sampled facts would hide all five), at 0.90 about 1, and the notes it hid were judged genuinely outdated 15 of 16 times or better.
STALE_CHUNK_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_STALE_CHUNK_SIMILARITY", "0.90"))


def get_user_memory_vector_store(db, embeddings) -> MongoDBAtlasVectorSearch:
    """Sibling vector store to the shared KB one in orchestrator.py, scoped to personal memory."""
    return MongoDBAtlasVectorSearch(
        collection=db[USER_MEMORY_COLLECTION],
        embedding=embeddings,
        index_name=USER_MEMORY_INDEX,
    )


def embed_and_store_memory_chunk(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    text: str,
    source_type: str = "manual",
    source_ref: Optional[str] = None,
    created_at: Optional[str] = None,
    latest_at: Optional[str] = None,
) -> None:
    """Fire-and-forget-safe: embeds and stores one chunk of personal memory text.

    `created_at` is when the remembered thing happened; it defaults to now. A compacted summary passes the OLDEST date of the
    notes it replaces (and `latest_at`, the newest), so folding a months-old memory into a summary never makes it look new."""
    if vector_store is None or not text or not text.strip():
        return
    try:
        metadata = {
            "username": username,
            "source_type": source_type,
            "source_ref": source_ref,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        if latest_at and latest_at != metadata["created_at"]:
            metadata["latest_at"] = latest_at
        vector_store.add_texts(texts=[text.strip()], metadatas=[metadata])
    except Exception:
        logger.exception("[MemorySearch] Failed to embed memory chunk for %s", username)


def mongo_ids(ids) -> list:
    """The ids a search result carries (strings) in the forms a collection may store them: each 24-hex id as an ObjectId AND as the string.
    Atlas search results give `doc.id` as a string while the stored `_id` is an ObjectId, so filtering on the string alone matched nothing
    and the stale flag below was never once set."""
    out = []
    for raw in ids:
        out.append(raw)
        if isinstance(raw, str) and ObjectId.is_valid(raw):
            out.append(ObjectId(raw))
    return out


def flag_stale_chunks_for_superseded_fact(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    old_fact_embedding: Optional[List[float]],
    top_k: int = 5,
    similarity_threshold: float = STALE_CHUNK_SIMILARITY_THRESHOLD,
) -> int:
    """Fire-and-forget-safe: when a fact is superseded, finds chunks semantically about the OLD
    version and soft-tags them {"stale": True} — never deletes, so retrieval can exclude them
    later without losing the audit trail (same reversibility spirit as _run_compaction's own
    insert-before-delete pattern). Searches via the vector store's own private
    _similarity_search_with_score with a precomputed embedding, the same pattern
    retrieve_relevant_memory_context's precomputed_embedding already established, rather than a
    linear scan. Returns the count flagged; 0 on any failure or no-op."""
    if vector_store is None or not old_fact_embedding:
        return 0
    try:
        hits = vector_store._similarity_search_with_score(
            old_fact_embedding, k=top_k, pre_filter={"username": username}
        )
    except Exception:
        logger.exception("[MemorySearch] Stale-chunk lookup failed for %s", username)
        return 0

    stale_ids = [doc.id for doc, score in hits if score >= similarity_threshold and doc.id is not None]
    if not stale_ids:
        return 0

    try:
        vector_store.collection.update_many(
            {"_id": {"$in": mongo_ids(stale_ids)}},
            {"$set": {
                "stale": True,
                "stale_reason": "fact_superseded",
                "stale_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception:
        logger.exception("[MemorySearch] Failed to tag stale chunks for %s", username)
        return 0

    logger.info("[MemorySearch] Flagged %d stale chunk(s) for %s (fact superseded).", len(stale_ids), username)
    return len(stale_ids)


def retrieve_user_memory(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    query: str,
    top_k: int = 4,
    score_threshold: Optional[float] = None,
) -> List[Document]:
    """Semantic search over one user's own memory chunks only (pre_filter on username),
    excluding any chunk flagged stale by a since-superseded fact. With `score_threshold`, only notes that score at least that are
    returned (so a question about something never stored finds nothing instead of its nearest unrelated notes); without it, the
    `top_k` nearest, however far."""
    if vector_store is None or not query or not query.strip():
        return []
    try:
        pre_filter = {"username": username, "stale": {"$ne": True}}
        if score_threshold is None:
            return vector_store.similarity_search(query, k=top_k, pre_filter=pre_filter)
        return [doc for doc, score in vector_store.similarity_search_with_score(query, k=top_k, pre_filter=pre_filter) if score >= score_threshold]
    except Exception:
        logger.exception("[MemorySearch] Semantic memory retrieval failed for %s", username)
        return []


SPAN_MIN_DAYS = 14  # a compacted summary whose notes span longer than this is dated as a stretch, not as one moment


def _parse_time(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_prefix(doc: Document, now: Optional[datetime] = None) -> str:
    """"(about 3 weeks ago) " for a recalled chunk, so a callback can say roughly when without inventing it; "" when the
    chunk has no usable timestamp (an unknown time is never guessed). A summary of notes spread over more than two weeks
    reads "(since about 3 months ago) ": its oldest note's date, said as a stretch, because it also holds newer ones."""
    metadata = getattr(doc, "metadata", None) or {}
    created = _parse_time(metadata.get("created_at"))
    if created is None:
        return ""
    seconds = ((now or datetime.now(timezone.utc)) - created).total_seconds()
    if seconds < 0:
        return ""
    latest = _parse_time(metadata.get("latest_at"))
    if latest is not None and (latest - created).days >= SPAN_MIN_DAYS:
        return f"(since {describe_gap(seconds)} ago) "
    return f"({describe_gap(seconds)} ago) "


RECALL_CONTEXT_MAX_CHARS = 300  # how much of the previous user message goes into the second search
RECALL_CONTEXT_MAX_QUESTION_WORDS = 20  # a longer question carries enough on its own; the second search is for short follow-ups
# A joined query is less specific than the question alone and drags in loosely related notes (measured: wanted notes scored 0.83+, the noise
# 0.80 to 0.81), so a note found only through it must clear the cutoff by this much.
RECALL_CONTEXT_EXTRA_BAR = 0.02


def previous_user_message(messages) -> str:
    """The user's message before the current one, trimmed, or "" when there is none. `messages` is the conversation so far with the
    current message last, as the app holds it."""
    human = [m for m in (messages or []) if isinstance(m, HumanMessage)]
    if len(human) < 2 or not isinstance(human[-2].content, str):
        return ""
    return human[-2].content.strip()[:RECALL_CONTEXT_MAX_CHARS]


def context_query_for(messages, question: str) -> str:
    """What to search in addition to the question itself when the question is a short follow-up ("make it funnier", "anyway, he did it
    again"): the previous user message and the question together, so the search sees what "it" and "he" refer to. "" when there is
    nothing to add (no earlier message, or a question long enough to stand alone)."""
    if not question or len(question.split()) > RECALL_CONTEXT_MAX_QUESTION_WORDS:
        return ""
    previous = previous_user_message(messages)
    return f"{previous}\n{question.strip()}" if previous else ""


def discount_context_hits(hits) -> list:
    """The hits of the joined-query search with the extra bar taken off their scores, so the one cutoff then applies to them."""
    return [(doc, score - RECALL_CONTEXT_EXTRA_BAR) for doc, score in hits]


def merge_hits(*hit_lists, top_k: int) -> list:
    """The best `top_k` (document, score) pairs across several searches. A note found by more than one keeps its highest score."""
    best: dict = {}
    for hits in hit_lists:
        for doc, score in hits:
            key = doc.page_content
            if key not in best or score > best[key][1]:
                best[key] = (doc, score)
    return sorted(best.values(), key=lambda pair: pair[1], reverse=True)[:top_k]


def retrieve_relevant_memory_context(*args, **kwargs) -> str:
    """The recalled notes as the block the chat reply's prompt carries ("" when nothing qualifies). Arguments as for
    retrieve_relevant_memory_items, which does the work."""
    items = retrieve_relevant_memory_items(*args, **kwargs)
    return format_recall_block(items) if items else ""


# THE STAND-OUT GATE (2026-11-23, measured on the busiest real store: 2,174 notes, nearly all about the same person). With that many notes about one life, ANY personal message
# lands close to something: all 40 sampled real messages cleared the 0.80 cutoff with three notes each (the weakest top score was 0.823), and so did questions about things
# never stored ("do you remember my dog's name", "how's my sister doing": 0.817 to 0.829), each of which would have been handed three unrelated notes. A fixed cutoff cannot
# separate them on such a store. What does is whether a note STANDS OUT from the person's own background: the mean score of the notes ranked STANDOUT_FROM to STANDOUT_POOL
# for the same question. A note must clear that background by STANDOUT_MARGIN. At 0.025 the gate kept a note on 28 of the 40 real messages and passed 0 of 10 never-stored
# questions; only 4 of 40 kept a third note. A store too small to have a background (fewer than STANDOUT_POOL notes found) is judged by the cutoff alone, as before.
# Notes that clear the cutoff but not the gate become near misses: on a message about the person, the second look (the small model) decides, as it already does for notes
# just under the cutoff.
STANDOUT_MARGIN = float(os.getenv("MEMORY_RECALL_STANDOUT", "0.025"))
STANDOUT_POOL = 30
STANDOUT_FROM = 10


def standout_floor(scores: List[float]) -> Optional[float]:
    """The score a note must reach to stand out from this question's background, or None when there are too few notes to have one. Pure."""
    ranked = sorted(scores, reverse=True)
    if STANDOUT_MARGIN <= 0 or len(ranked) < STANDOUT_POOL:
        return None
    background = ranked[STANDOUT_FROM:STANDOUT_POOL]
    return sum(background) / len(background) + STANDOUT_MARGIN


def retrieve_relevant_memory_items(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    query: str,
    top_k: int = 3,
    score_threshold: float = DEFAULT_RECALL_SIMILARITY_THRESHOLD,
    precomputed_embedding: Optional[List[float]] = None,
    context_query: str = "",
    near_misses: Optional[list] = None,
    standout: bool = True,
    stats: Optional[dict] = None,
) -> List[str]:
    """The recalled notes, each as one line with its age in front ("(about 3 weeks ago) ..."), best first; [] when none qualifies.
    Passive, always-on counterpart to memory_recall_node's explicit-intent search. Runs on
    every conversational turn regardless of how the message is classified, but only surfaces
    chunks that clear score_threshold — so indirect phrasing like "do you remember..." still
    gets grounded in genuinely stored content instead of the model plausibly extrapolating,
    without misfiring on turns that have nothing relevant stored.

    precomputed_embedding lets a caller that already embedded this exact question elsewhere this
    turn (e.g. fetch_relevant_user_facts, called with the same question string) pass the vector
    in directly instead of this call re-embedding the same text again via the vector store's own
    internal embeddings client — a second real network round trip for identical input otherwise.
    Goes through the vector store's own private _similarity_search_with_score, which both public
    similarity_search_with_score (string) and similarity_search_by_vector (vector, no scores)
    already dispatch to — the one place in this library that accepts either input and returns
    scores.

    context_query (see context_query_for) is a second search for a short follow-up; the notes it finds are merged with those of the
    question's own search, each keeping its best score, and the same cutoff applies. The question's own search still runs alone first, so
    a question that stands on its own recalls exactly what it did before.

    `standout` applies the stand-out gate above (off for a search the agent asked for itself). `stats`, when given, is filled with "recalled" (how many came back) and
    "standout_held" (how many cleared the cutoff but not the gate)."""
    if vector_store is None or not query or not query.strip():
        return []
    pool = max(top_k, STANDOUT_POOL) if standout else top_k
    try:
        if precomputed_embedding is not None:
            hits = vector_store._similarity_search_with_score(
                precomputed_embedding, k=pool, pre_filter={"username": username, "stale": {"$ne": True}}
            )
        else:
            hits = vector_store.similarity_search_with_score(
                query, k=pool, pre_filter={"username": username, "stale": {"$ne": True}}
            )
    except Exception:
        logger.exception("[MemorySearch] Passive semantic recall failed for %s", username)
        return []
    floor = standout_floor([score for _doc, score in hits]) if standout else None   # the background comes from the question's own search

    if context_query and context_query.strip():
        try:
            extra = vector_store.similarity_search_with_score(
                context_query, k=pool, pre_filter={"username": username, "stale": {"$ne": True}}
            )
            hits = merge_hits(hits, discount_context_hits(extra), top_k=pool)
        except Exception:
            logger.exception("[MemorySearch] Follow-up semantic recall failed for %s", username)

    ranked = sorted(hits, key=lambda pair: pair[1], reverse=True)[:top_k]
    cleared = [(doc, score) for doc, score in ranked if score >= score_threshold]
    relevant = [doc for doc, score in cleared if floor is None or score >= floor]
    if stats is not None:
        stats["recalled"] = len(relevant)
        stats["standout_held"] = len(cleared) - len(relevant)
    if not relevant:
        if near_misses is not None:
            # Nothing qualified: hand back, for the second look, what cleared the cutoff but did not stand out, then what fell just short of the cutoff.
            near_misses.extend(doc for doc, _score in cleared)
            near_misses.extend(doc for doc, score in ranked if score_threshold - NEAR_MISS_BAND <= score < score_threshold)
            del near_misses[NEAR_MISS_MAX_CANDIDATES:]
        return []

    return [f"{_age_prefix(doc)}{doc.page_content.strip()}" for doc in relevant]


NEAR_MISS_BAND = 0.06  # notes scoring this far under the cutoff or less are "near misses"
NEAR_MISS_MAX_CANDIDATES = 3
SECOND_LOOK_ENABLED = os.getenv("MEMORY_SECOND_LOOK", "true").strip().lower() not in ("0", "false", "no", "off")
# The reply waits for this, so it gets a time limit: measured about 0.65s typically, 2.5s at worst. Past the limit nothing is recalled for it.
SECOND_LOOK_TIMEOUT_SECONDS = float(os.getenv("MEMORY_SECOND_LOOK_TIMEOUT_SECONDS", "2.0"))

# Paraphrases ("my pup" for "greyhound named Biscuit") score just under the cutoff, but so do unrelated questions, so a score cannot tell them
# apart. Only the small model can, and only for the few notes that fell just short. It is asked only when nothing cleared the cutoff and
# the message is about the user (a question about the capital of Australia is not, and never costs a call).
SECOND_LOOK_PROMPT = """A user just said this to their assistant:

{said}

These notes were saved from earlier conversations with the same user:
{notes}

Which notes would a thoughtful friend who remembered them actually use in replying to what the user just said? Be strict: when in doubt,
the answer is NONE. A note counts only if it names or clearly describes the SAME specific person, pet, event, place, project or thing that the
user's message points at, even in different words. A note does NOT count when it merely resembles the kind of request (both are about writing,
jokes, trivia, coding, advice or the like) or is in the same general area but about something else. If the message is a general request or
question that does not point at anything specific in the user's own life, the answer is NONE.
Reply with the numbers of the notes that count, separated by commas, or with exactly: NONE"""

_USER_REFERENCE = re.compile(r"\b(my|mine|me|myself|i|i'm|i've|i'd|i'll|we|our|ours|us|remember|remind|earlier|last time)\b", re.IGNORECASE)


def refers_to_user(message: str) -> bool:
    """True when the message speaks of the user's own life or of something said before. A question that does neither has nothing in memory
    to be recalled, so it never earns a second look."""
    return bool(message and _USER_REFERENCE.search(message))


def parse_choice(reply: str, count: int) -> List[int]:
    """The 0-based positions the judge picked from a reply such as "1, 3" or "NONE"; numbers outside 1..count are ignored."""
    if not reply or reply.strip().upper().startswith("NONE"):
        return []
    picked = []
    for number in re.findall(r"\d+", reply):
        index = int(number) - 1
        if 0 <= index < count and index not in picked:
            picked.append(index)
    return picked


def judge_prompt(said: str, candidates: list) -> str:
    notes = "\n".join(f"{i}. {doc.page_content.strip()[:400]}" for i, doc in enumerate(candidates, 1))
    return SECOND_LOOK_PROMPT.format(said=said.strip()[:600], notes=notes)


def reply_text(reply) -> str:
    content = reply.content if hasattr(reply, "content") else str(reply)
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)


async def judge_near_misses(said: str, candidates: list, llm=None) -> list:
    """The candidates (documents) the small model judges to be about what the user just said. Never raises: on any failure nothing is kept,
    which is what recall would have returned without a second look."""
    if not candidates or not said or not said.strip():
        return []
    try:
        if llm is None:
            from backend.models.models import lite_llm

            llm = lite_llm.ainvoke
        reply = await asyncio.wait_for(llm(judge_prompt(said, candidates)), timeout=SECOND_LOOK_TIMEOUT_SECONDS)
        return [candidates[i] for i in parse_choice(reply_text(reply), len(candidates))]
    except asyncio.TimeoutError:
        logger.warning("[MemorySearch] Second look took longer than %.1fs; recalling nothing for it.", SECOND_LOOK_TIMEOUT_SECONDS)
        return []
    except Exception:
        logger.exception("[MemorySearch] Second look at near-miss notes failed; recalling nothing for them.")
        return []


# ---- the explicit "do you remember ..." path (memory_recall_node) ------------------------------------------------------------------
# Asked by name, memory must find what is there and say so when it is not. It used to return the 4 nearest notes whatever their score, so a
# question about something never stored got 4 unrelated notes labelled "relevant past context". Measured on the real store (see
# backend/evals/memory_eval.py, part "explicit"): short never-stored questions ("my car") score 0.78 to 0.81 against unrelated notes, so no single
# cutoff both keeps natural asks and drops those. So: notes of at least SURE are kept as they are, notes between FLOOR and SURE are kept only if the
# small model says they are what the user is asking about, and anything under FLOOR is dropped.
EXPLICIT_RECALL_FLOOR = float(os.getenv("MEMORY_EXPLICIT_RECALL_FLOOR", "0.76"))
EXPLICIT_RECALL_SURE = float(os.getenv("MEMORY_EXPLICIT_RECALL_SURE", "0.85"))
EXPLICIT_JUDGE_ENABLED = os.getenv("MEMORY_EXPLICIT_JUDGE", "true").strip().lower() not in ("0", "false", "no", "off")

# "Do you remember ...", "remind me ...", "what did I tell you about ..." is the same on every such question, so it pulls every note a little
# closer and blurs the one thing that matters. Only a leading phrase is removed, and only if two or more words are left.
_RECALL_PHRASING = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*(?:do|can|could|would) you (?:still |even )?remember\b[\s,]*(?:when i (?:told|said to|mentioned to) you (?:about )?)?",
        r"^\s*remember when i (?:told|said to|mentioned to) you (?:about )?",
        r"^\s*remind me[\s,]*",
        r"^\s*what did i (?:tell|say to|mention to) you about\s*",
        r"^\s*what do you remember about\s*",
        r"^\s*did i (?:ever )?(?:tell|mention)(?: to)? you (?:about )?",
    )
]


def strip_recall_phrasing(message: str) -> str:
    """The message without its leading "do you remember" style phrase, for searching; the message itself when nothing useful would be left."""
    out = message or ""
    for pattern in _RECALL_PHRASING:
        out = pattern.sub("", out, count=1)
    if out == (message or ""):
        return out  # no leading phrase: leave the message exactly as it was
    out = out.strip(" ?.!,")
    return out if len(out.split()) >= 2 else (message or "")


def judge_notes_sync(said: str, candidates: list, invoke) -> list:
    """judge_near_misses for synchronous callers (a graph node): which candidates the small model says are what the user means. Never raises;
    on any failure nothing is kept."""
    if not candidates or not said or not said.strip():
        return []
    try:
        return [candidates[i] for i in parse_choice(reply_text(invoke(judge_prompt(said, candidates))), len(candidates))]
    except Exception:
        logger.exception("[MemorySearch] Judging explicit-recall candidates failed; keeping only the sure ones.")
        return []


def recall_explicit(vector_store, username: str, message: str, invoke=None, top_k: int = 4) -> list:
    """The notes that answer a "do you remember ..." question, best first: those scoring at least EXPLICIT_RECALL_SURE, plus those between
    EXPLICIT_RECALL_FLOOR and that which the small model confirms. [] when nothing qualifies, which the caller must say plainly."""
    query = strip_recall_phrasing(message)
    if vector_store is None or not query or not query.strip():
        return []
    try:
        scored = vector_store.similarity_search_with_score(query, k=top_k, pre_filter={"username": username, "stale": {"$ne": True}})
    except Exception:
        logger.exception("[MemorySearch] Explicit semantic recall failed for %s", username)
        return []
    maybe = [doc for doc, score in scored if EXPLICIT_RECALL_FLOOR <= score < EXPLICIT_RECALL_SURE]
    confirmed = []
    if maybe and EXPLICIT_JUDGE_ENABLED:
        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.invoke
        confirmed = judge_notes_sync(message, maybe, invoke)
    keep = {id(doc) for doc in confirmed}
    return [doc for doc, score in scored if score >= EXPLICIT_RECALL_SURE or id(doc) in keep]


async def recall_items_with_second_look(
    vector_store, username: str, question: str, *, precomputed_embedding=None, context_query: str = "", llm=None, stats: Optional[dict] = None,
) -> List[str]:
    """retrieve_relevant_memory_items, and when it finds nothing but some notes fell just short of the cutoff and the message is about the
    user, the small model picks which of those near misses (at most three) really are what the user means."""
    near: list = []
    stats = stats if stats is not None else {}
    # a synchronous network search: in a worker thread, so a slow one cannot freeze the server
    items = await asyncio.to_thread(
        lambda: retrieve_relevant_memory_items(
            vector_store, username, question, precomputed_embedding=precomputed_embedding, context_query=context_query, near_misses=near, stats=stats,
        )
    )
    if items or not near or not SECOND_LOOK_ENABLED or not refers_to_user(question):
        return items
    kept = await judge_near_misses(context_query or question, near, llm)
    stats["second_look"] = True
    stats["recalled"] = len(kept)
    return [f"{_age_prefix(doc)}{doc.page_content.strip()}" for doc in kept]


async def recall_with_second_look(*args, **kwargs) -> str:
    """The same, as the block the chat reply's prompt carries ("" when nothing qualifies)."""
    items = await recall_items_with_second_look(*args, **kwargs)
    return format_recall_block(items) if items else ""


_RECALLED_WORDS = ("none", "one", "two", "three")


def tally_recall(counts: dict, stats: Optional[dict], tally) -> None:
    """Adds a turn's recall to its counters (self_counters: notes_recalled, notes_standout_held, notes_second_look, notes_second_look_kept). `tally` is self_counters.tally. Pure apart from it."""
    stats = stats or {}
    recalled = int(stats.get("recalled", 0) or 0)
    tally(counts, "notes_recalled", _RECALLED_WORDS[min(max(recalled, 0), len(_RECALLED_WORDS) - 1)])
    if stats.get("standout_held"):
        tally(counts, "notes_standout_held")
    if stats.get("second_look"):
        tally(counts, "notes_second_look")
        if recalled:
            tally(counts, "notes_second_look_kept")


def format_recall_block(items: List[str]) -> str:
    """The prompt section for recalled chunks (each item already carries its age prefix, if it has one)."""
    lines = "\n".join(f"- {item}" for item in items)
    return (
        "\n\nRELEVANT PAST CONTEXT (genuinely recalled from this user's history — weave it in "
        f"naturally if it fits, don't ignore it):\n{lines}\n"
    )
