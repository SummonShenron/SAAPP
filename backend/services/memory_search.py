import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from langchain_core.documents import Document
from langchain_mongodb import MongoDBAtlasVectorSearch

from backend.utils.time_utils import describe_gap

logger = logging.getLogger("SASS Logger")

USER_MEMORY_COLLECTION = "user_memory_chunks"
USER_MEMORY_INDEX = "user_memory_vector_index"

# Atlas Vector Search scores are already normalized to [0, 1] (langchain_mongodb's default
# relevance_score_fn="cosine" is the identity function on that score), so this threshold is
# on that same scale.
DEFAULT_RECALL_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_RECALL_SIMILARITY_THRESHOLD", "0.75"))
# Same "these are about the same underlying thing" bar memory_compaction.py's
# DEFAULT_SIMILARITY_THRESHOLD uses for its own clustering (0.85) — duplicated rather than
# imported, to avoid an import edge back into memory_compaction.py (which already imports FROM
# this module).
STALE_CHUNK_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_STALE_CHUNK_SIMILARITY", "0.85"))


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
) -> None:
    """Fire-and-forget-safe: embeds and stores one chunk of personal memory text."""
    if vector_store is None or not text or not text.strip():
        return
    try:
        vector_store.add_texts(
            texts=[text.strip()],
            metadatas=[{
                "username": username,
                "source_type": source_type,
                "source_ref": source_ref,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
    except Exception:
        logger.exception("[MemorySearch] Failed to embed memory chunk for %s", username)


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
            {"_id": {"$in": stale_ids}},
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
) -> List[Document]:
    """Semantic search over one user's own memory chunks only (pre_filter on username),
    excluding any chunk flagged stale by a since-superseded fact."""
    if vector_store is None or not query or not query.strip():
        return []
    try:
        return vector_store.similarity_search(
            query,
            k=top_k,
            pre_filter={"username": username, "stale": {"$ne": True}},
        )
    except Exception:
        logger.exception("[MemorySearch] Semantic memory retrieval failed for %s", username)
        return []


def _age_prefix(doc: Document, now: Optional[datetime] = None) -> str:
    """"(about 3 weeks ago) " for a recalled chunk, so a callback can say roughly when without inventing it; "" when the
    chunk has no usable timestamp (an unknown time is never guessed)."""
    raw = (getattr(doc, "metadata", None) or {}).get("created_at")
    if not raw:
        return ""
    try:
        created = datetime.fromisoformat(str(raw))
    except ValueError:
        return ""
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - created).total_seconds()
    if seconds < 0:
        return ""
    return f"({describe_gap(seconds)} ago) "


def retrieve_relevant_memory_context(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    query: str,
    top_k: int = 3,
    score_threshold: float = DEFAULT_RECALL_SIMILARITY_THRESHOLD,
    precomputed_embedding: Optional[List[float]] = None,
) -> str:
    """Passive, always-on counterpart to memory_recall_node's explicit-intent search. Runs on
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
    scores."""
    if vector_store is None or not query or not query.strip():
        return ""
    try:
        if precomputed_embedding is not None:
            hits = vector_store._similarity_search_with_score(
                precomputed_embedding, k=top_k, pre_filter={"username": username, "stale": {"$ne": True}}
            )
        else:
            hits = vector_store.similarity_search_with_score(
                query, k=top_k, pre_filter={"username": username, "stale": {"$ne": True}}
            )
    except Exception:
        logger.exception("[MemorySearch] Passive semantic recall failed for %s", username)
        return ""

    relevant = [doc for doc, score in hits if score >= score_threshold]
    if not relevant:
        return ""

    return format_recall_block([f"{_age_prefix(doc)}{doc.page_content.strip()}" for doc in relevant])


def format_recall_block(items: List[str]) -> str:
    """The prompt section for recalled chunks (each item already carries its age prefix, if it has one)."""
    lines = "\n".join(f"- {item}" for item in items)
    return (
        "\n\nRELEVANT PAST CONTEXT (genuinely recalled from this user's history — weave it in "
        f"naturally if it fits, don't ignore it):\n{lines}\n"
    )
