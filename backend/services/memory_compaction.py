import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from backend.models.models import lite_llm
from backend.components.constraints import MEMORY_COMPACTION_PROMPT
from backend.services.memory_search import USER_MEMORY_COLLECTION, embed_and_store_memory_chunk
from backend.utils.memory_utils import save_user_fact
from backend.utils.embedding_utils import cosine_similarity

logger = logging.getLogger("SASS Logger")

DEFAULT_COMPACTION_THRESHOLD = int(os.getenv("MEMORY_COMPACTION_THRESHOLD", "100"))
DEFAULT_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_COMPACTION_SIMILARITY", "0.85"))
DEFAULT_META_COMPACTION_THRESHOLD = int(os.getenv("MEMORY_META_COMPACTION_THRESHOLD", "500"))
DEFAULT_META_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_META_COMPACTION_SIMILARITY", "0.90"))

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
        logger.info(
            "[MemoryCompaction] Tier %s: %d candidate chunk(s) for %s formed %d cluster(s) (threshold=%.2f).",
            tier, len(candidates), username, len(multi_clusters), similarity_threshold,
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

        result = {
            "status": "completed",
            "chunks_before": chunks_before,
            "chunks_after": _count_all_chunks(db, username),
            "clusters_formed": clusters_formed,
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
        logger.info("[MemoryCompaction] Tier 2 check for %s: pool=%d threshold=%d", username, pool_size, effective_threshold)
        if pool_size > effective_threshold:
            await compact_meta_memory(db, vector_store, username)
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
        logger.info("[MemoryCompaction] Tier 1 check for %s: uncompacted=%d threshold=%d", username, uncompacted, effective_threshold)
        if uncompacted > effective_threshold:
            result = await compact_user_memory(db, vector_store, username)
            if result.get("status") == "completed":
                await maybe_trigger_meta_compaction(db, vector_store, username)
    except Exception:
        logger.exception("[MemoryCompaction] Failed to check/trigger compaction for %s", username)
