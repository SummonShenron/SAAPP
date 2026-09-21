import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from backend.models.models import lite_llm
from backend.components.constraints import MEMORY_COMPACTION_PROMPT, PATTERN_EXTRACTION_PROMPT
from backend.services.memory_search import USER_MEMORY_COLLECTION, embed_and_store_memory_chunk
from backend.utils.memory_utils import save_user_fact, load_user_facts
from backend.utils.embedding_utils import cosine_similarity

logger = logging.getLogger("SASS Logger")

DEFAULT_COMPACTION_THRESHOLD = int(os.getenv("MEMORY_COMPACTION_THRESHOLD", "100"))
DEFAULT_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_COMPACTION_SIMILARITY", "0.85"))
DEFAULT_META_COMPACTION_THRESHOLD = int(os.getenv("MEMORY_META_COMPACTION_THRESHOLD", "500"))
DEFAULT_META_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_META_COMPACTION_SIMILARITY", "0.90"))
MIN_FACTS_FOR_PATTERN_EXTRACTION = int(os.getenv("MIN_FACTS_FOR_PATTERN_EXTRACTION", "8"))
# Re-running pattern extraction over a barely-changed fact set just gives the model another
# chance to phrase the same underlying pattern slightly differently — fact-level dedup catches
# exact/near-exact restatements, but not that kind of drift. Skip unless real time has passed
# OR the fact set has actually grown meaningfully since the last completed run.
MIN_PATTERN_EXTRACTION_INTERVAL_HOURS = float(os.getenv("MIN_PATTERN_EXTRACTION_INTERVAL_HOURS", "24"))
MIN_NEW_FACTS_FOR_PATTERN_EXTRACTION = int(os.getenv("MIN_NEW_FACTS_FOR_PATTERN_EXTRACTION", "5"))

TIER1_SOURCE_TYPE = "compacted_summary"
TIER2_SOURCE_TYPE = "compacted_meta_summary"

logger.info(
    "[MemoryCompaction] Loaded thresholds: tier1=%d (similarity=%.2f) tier2=%d (similarity=%.2f)",
    DEFAULT_COMPACTION_THRESHOLD, DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_META_COMPACTION_THRESHOLD, DEFAULT_META_SIMILARITY_THRESHOLD,
)

# Per-username guard so two near-simultaneous turns can't run overlapping compaction passes.
# Shared by both tiers: Tier-1 fully releases it before a chained Tier-2 check ever acquires it.
_compaction_in_progress: set = set()


def count_uncompacted_chunks(db, username: str) -> int:
    """Tier-1 gate: raw/manual chunks not yet folded into either compacted tier."""
    if db is None:
        return 0
    return db[USER_MEMORY_COLLECTION].count_documents(
        {"username": username, "source_type": {"$nin": [TIER1_SOURCE_TYPE, TIER2_SOURCE_TYPE]}}
    )


def _count_tier2_pool(db, username: str) -> int:
    """Tier-2 gate: everything already consolidated once (Tier-1 summaries + prior meta-summaries)."""
    if db is None:
        return 0
    return db[USER_MEMORY_COLLECTION].count_documents(
        {"username": username, "source_type": {"$in": [TIER1_SOURCE_TYPE, TIER2_SOURCE_TYPE]}}
    )


def _count_all_chunks(db, username: str) -> int:
    return db[USER_MEMORY_COLLECTION].count_documents({"username": username})


def _fetch_chunks(db, username: str, source_type_filter: dict) -> List[dict]:
    cursor = db[USER_MEMORY_COLLECTION].find(
        {"username": username, "source_type": source_type_filter},
        {"text": 1, "embedding": 1, "source_type": 1, "source_ref": 1, "created_at": 1},
    )
    return list(cursor)


def _cluster_chunks(chunks: List[dict], threshold: float = DEFAULT_SIMILARITY_THRESHOLD) -> List[List[dict]]:
    """
    Pure greedy cosine-similarity clustering: no DB/LLM calls, safe to unit test directly.
    Each unclustered chunk seeds a new cluster and absorbs any other unclustered chunk whose
    embedding is similar enough to it.
    """
    clusters: List[List[dict]] = []
    assigned = [False] * len(chunks)

    for i, chunk in enumerate(chunks):
        if assigned[i]:
            continue
        cluster = [chunk]
        assigned[i] = True
        anchor_embedding = chunk.get("embedding")

        if anchor_embedding:
            for j in range(i + 1, len(chunks)):
                if assigned[j]:
                    continue
                other_embedding = chunks[j].get("embedding")
                if not other_embedding:
                    continue
                if cosine_similarity(anchor_embedding, other_embedding) >= threshold:
                    cluster.append(chunks[j])
                    assigned[j] = True

        clusters.append(cluster)

    return clusters


async def _summarize_cluster(cluster: List[dict]) -> dict:
    """Calls the LLM once per cluster to produce a dense summary + any durable facts."""
    chunk_texts = "\n".join(f"- {c.get('text', '')}" for c in cluster)
    fallback = {"summary": chunk_texts, "facts": []}

    try:
        response = await lite_llm.ainvoke(MEMORY_COMPACTION_PROMPT.format(chunk_texts=chunk_texts))
        raw_content = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)

        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        return {
            "summary": parsed.get("summary") or chunk_texts,
            "facts": parsed.get("facts") or [],
        }
    except Exception:
        logger.exception("[MemoryCompaction] Cluster summarization failed; falling back to raw concatenation.")
        return fallback


def _get_last_pattern_extraction(db, username: str) -> Optional[dict]:
    """Most recent COMPLETED pattern_extraction event for this user, or None if there isn't
    one (or db is unavailable) — used to gate re-runs via MIN_PATTERN_EXTRACTION_INTERVAL_HOURS/
    MIN_NEW_FACTS_FOR_PATTERN_EXTRACTION below. Only "completed" runs count, so a run skipped
    for not having enough facts yet never blocks a later attempt once facts do exist."""
    if db is None:
        return None
    return db["memory_compaction_events"].find_one(
        {"username": username, "type": "pattern_extraction", "status": "completed"},
        sort=[("timestamp", -1)],
    )


async def extract_user_patterns(db, username: str) -> dict:
    """Analyzes a user's accumulated facts as a whole to find recurring higher-level patterns
    (e.g. "consistently gravitates toward agent systems, workflow engines, operational
    tooling") — an observation that only emerges by looking across several distinct facts
    together, not something any single fact-extraction call would produce. Each pattern is
    saved via save_user_fact with category="pattern", which gets deduplication against
    previously-saved patterns for free via that function's existing conflict-detection."""
    facts = load_user_facts(username)
    if len(facts) < MIN_FACTS_FOR_PATTERN_EXTRACTION:
        return {"status": "skipped", "reason": "not_enough_facts", "fact_count": len(facts)}

    last_run = _get_last_pattern_extraction(db, username)
    if last_run:
        last_timestamp = datetime.fromisoformat(last_run["timestamp"])
        if last_timestamp.tzinfo is None:
            last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
        hours_since = (datetime.now(timezone.utc) - last_timestamp).total_seconds() / 3600
        new_facts_since = len(facts) - last_run.get("fact_count", 0)
        if hours_since < MIN_PATTERN_EXTRACTION_INTERVAL_HOURS and new_facts_since < MIN_NEW_FACTS_FOR_PATTERN_EXTRACTION:
            logger.info(
                "[MemoryCompaction] Pattern extraction cooldown active for %s (%.1fh since last "
                "run, %d new fact(s)) — skipping.", username, hours_since, new_facts_since,
            )
            return {"status": "skipped", "reason": "cooldown", "fact_count": len(facts)}

    facts_text = "\n".join(f"- [{f.category}] {f.fact}" for f in facts)

    try:
        response = await lite_llm.ainvoke(PATTERN_EXTRACTION_PROMPT.format(facts_text=facts_text))
        raw_content = response.content if hasattr(response, "content") else str(response)
        if isinstance(raw_content, list):
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in raw_content])
        else:
            raw_text = str(raw_content)
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        patterns = json.loads(clean_json).get("patterns") or []
    except Exception:
        logger.exception("[MemoryCompaction] Pattern extraction failed for %s", username)
        return {"status": "error", "fact_count": len(facts)}

    patterns_saved = 0
    for pattern_text in patterns:
        if isinstance(pattern_text, str) and pattern_text.strip():
            save_user_fact(username, pattern_text, category="pattern", source="pattern")
            patterns_saved += 1

    result = {"status": "completed", "fact_count": len(facts), "patterns_saved": patterns_saved}
    log_compaction_event(db, username, {**result, "type": "pattern_extraction"})
    return result


def log_compaction_event(db, username: str, result: dict) -> None:
    logger.info("[MemoryCompaction] %s: %s", username, result)
    if db is None:
        return
    try:
        db["memory_compaction_events"].insert_one({
            "username": username,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **result,
        })
    except Exception:
        logger.exception("[MemoryCompaction] Failed to log compaction event for %s", username)


async def _run_compaction(
    db, vector_store, username: str, *,
    input_filter: dict, output_source_type: str, similarity_threshold: float, tier: int,
) -> dict:
    """
    The shared compaction pipeline: fetch -> cluster -> summarize -> insert-then-delete -> promote
    facts -> log. Never raises; failures are logged and reported in the returned dict instead,
    matching the "log and swallow" convention used throughout the rest of the memory system.
    Used by both compact_user_memory (tier 1) and compact_meta_memory (tier 2) — they differ only
    in which documents qualify as input and what the output gets tagged as.
    """
    if db is None:
        return {"status": "skipped", "reason": "no_database"}

    if username in _compaction_in_progress:
        logger.info("[MemoryCompaction] Compaction already running for %s; skipping.", username)
        return {"status": "skipped", "reason": "already_running"}

    _compaction_in_progress.add(username)
    chunks_before = _count_all_chunks(db, username)
    clusters_formed = 0
    facts_promoted = 0

    try:
        candidates = _fetch_chunks(db, username, input_filter)
        if len(candidates) < 2:
            logger.info("[MemoryCompaction] Tier %s: only %d candidate chunk(s) for %s; nothing to compact.", tier, len(candidates), username)
            return {"status": "skipped", "reason": "not_enough_chunks", "chunks_before": chunks_before, "tier": tier}

        clusters = _cluster_chunks(candidates, threshold=similarity_threshold)
        multi_clusters = [c for c in clusters if len(c) >= 2]
        singleton_chunks = [c[0] for c in clusters if len(c) == 1]
        logger.info(
            "[MemoryCompaction] Tier %s: %d candidate chunk(s) for %s formed %d cluster(s) "
            "and %d singleton(s) (threshold=%.2f).",
            tier, len(candidates), username, len(multi_clusters), len(singleton_chunks), similarity_threshold,
        )
        clusters_formed = len(multi_clusters)

        for cluster in multi_clusters:
            summarized = await _summarize_cluster(cluster)

            source_refs = [c.get("source_ref") for c in cluster if c.get("source_ref")]
            provenance = f"compacted:{len(cluster)} chunks"
            if source_refs:
                provenance += f" ({', '.join(str(r) for r in source_refs[:3])})"

            # Insert the compacted summary FIRST; only delete originals once that succeeds,
            # so a mid-run crash can never lose the underlying content.
            embed_and_store_memory_chunk(
                vector_store, username, summarized["summary"],
                source_type=output_source_type, source_ref=provenance,
            )
            db[USER_MEMORY_COLLECTION].delete_many({"_id": {"$in": [c["_id"] for c in cluster]}})

            for fact in summarized["facts"]:
                fact_text = fact.get("fact") if isinstance(fact, dict) else None
                if fact_text:
                    save_user_fact(username, fact_text, category=fact.get("category") or "preference", source="inferred")
                    facts_promoted += 1

        singletons_promoted = 0
        if singleton_chunks:
            # A chunk with nothing similar enough to merge with isn't a failure to compact —
            # it's genuinely distinct information, and a normal, healthy memory is full of
            # that. The bug this fixes: leaving it out of both the delete and the "already
            # compacted" tag meant it stayed in the "uncompacted" pool forever, getting
            # re-fetched and re-clustered (again finding nothing to merge with) on every
            # future run — real production logs showed this exact pattern, e.g. "107
            # candidate chunk(s)... formed 0 cluster(s)" twice in a row. Retagging it to this
            # tier's output source_type — no LLM call, no content change, just relabeling —
            # removes it from that query so it stops being repeatedly rescanned, while still
            # leaving it eligible to cluster with something else later at Tier 2's own pass.
            db[USER_MEMORY_COLLECTION].update_many(
                {"_id": {"$in": [c["_id"] for c in singleton_chunks]}},
                {"$set": {"source_type": output_source_type}},
            )
            singletons_promoted = len(singleton_chunks)

        result = {
            "status": "completed",
            "chunks_before": chunks_before,
            "chunks_after": _count_all_chunks(db, username),
            "clusters_formed": clusters_formed,
            "singletons_promoted": singletons_promoted,
            "facts_promoted": facts_promoted,
            "tier": tier,
        }
        log_compaction_event(db, username, result)
        return result
    except Exception:
        logger.exception("[MemoryCompaction] Compaction run failed for %s", username)
        return {"status": "error", "chunks_before": chunks_before, "tier": tier}
    finally:
        _compaction_in_progress.discard(username)


async def compact_user_memory(db, vector_store, username: str) -> dict:
    """Tier 1: folds raw/manual chunks into compacted_summary docs."""
    return await _run_compaction(
        db, vector_store, username,
        input_filter={"$nin": [TIER1_SOURCE_TYPE, TIER2_SOURCE_TYPE]},
        output_source_type=TIER1_SOURCE_TYPE,
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        tier=1,
    )


async def compact_meta_memory(db, vector_store, username: str) -> dict:
    """
    Tier 2: self-compacting. Pools Tier-1 summaries together with its OWN prior output and
    re-clusters/re-summarizes the whole thing, replacing old meta-summaries rather than
    accumulating them — this is what keeps total memory storage bounded without ever needing
    a Tier 3.
    """
    return await _run_compaction(
        db, vector_store, username,
        input_filter={"$in": [TIER1_SOURCE_TYPE, TIER2_SOURCE_TYPE]},
        output_source_type=TIER2_SOURCE_TYPE,
        similarity_threshold=DEFAULT_META_SIMILARITY_THRESHOLD,
        tier=2,
    )


async def maybe_trigger_meta_compaction(db, vector_store, username: str, threshold: Optional[int] = None) -> None:
    """Lazy trigger for tier 2: checked only right after a tier-1 run actually completes."""
    if db is None:
        return
    effective_threshold = DEFAULT_META_COMPACTION_THRESHOLD if threshold is None else threshold
    try:
        pool_size = _count_tier2_pool(db, username)
        # DEBUG, not INFO — this check runs after every tier-1 completion regardless of whether
        # it actually triggers anything, so at INFO it was pure per-message noise; the "compacted
        # N chunks" result below (an actual event) stays at INFO.
        logger.debug("[MemoryCompaction] Tier 2 check for %s: pool=%d threshold=%d", username, pool_size, effective_threshold)
        if pool_size > effective_threshold:
            await compact_meta_memory(db, vector_store, username)
            # Reuse this same "enough has accumulated to warrant a deeper pass" checkpoint to
            # also look for recurring patterns across the user's accumulated facts.
            await extract_user_patterns(db, username)
    except Exception:
        logger.exception("[MemoryCompaction] Failed to check/trigger meta-compaction for %s", username)


async def maybe_trigger_compaction(db, vector_store, username: str, threshold: Optional[int] = None) -> None:
    """Lazy trigger: called after every new chunk is embedded; only actually compacts over threshold.
    Chains into the tier-2 check once tier-1 completes a run (tier-1 summary counts only ever
    change when tier-1 actually runs, so there's nothing to gain from checking more often)."""
    if db is None:
        return
    effective_threshold = DEFAULT_COMPACTION_THRESHOLD if threshold is None else threshold
    try:
        uncompacted = count_uncompacted_chunks(db, username)
        # DEBUG, not INFO — called after every new chunk is embedded (i.e. after every message
        # that gets memory-indexed), regardless of whether the threshold is actually crossed;
        # the real event (a compaction run actually happening) is logged at INFO further down.
        logger.debug("[MemoryCompaction] Tier 1 check for %s: uncompacted=%d threshold=%d", username, uncompacted, effective_threshold)
        if uncompacted > effective_threshold:
            result = await compact_user_memory(db, vector_store, username)
            if result.get("status") == "completed":
                await maybe_trigger_meta_compaction(db, vector_store, username)
    except Exception:
        logger.exception("[MemoryCompaction] Failed to check/trigger compaction for %s", username)
