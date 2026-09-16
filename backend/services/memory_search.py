import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from langchain_core.documents import Document
from langchain_mongodb import MongoDBAtlasVectorSearch

logger = logging.getLogger("SASS Logger")

USER_MEMORY_COLLECTION = "user_memory_chunks"
USER_MEMORY_INDEX = "user_memory_vector_index"

# Atlas Vector Search scores are already normalized to [0, 1] (langchain_mongodb's default
# relevance_score_fn="cosine" is the identity function on that score), so this threshold is
# on that same scale.
DEFAULT_RECALL_SIMILARITY_THRESHOLD = float(os.getenv("MEMORY_RECALL_SIMILARITY_THRESHOLD", "0.75"))


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


def retrieve_user_memory(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    query: str,
    top_k: int = 4,
) -> List[Document]:
    """Semantic search over one user's own memory chunks only (pre_filter on username)."""
    if vector_store is None or not query or not query.strip():
        return []
    try:
        return vector_store.similarity_search(
            query,
            k=top_k,
            pre_filter={"username": username},
        )
    except Exception:
        logger.exception("[MemorySearch] Semantic memory retrieval failed for %s", username)
        return []


def retrieve_relevant_memory_context(
    vector_store: Optional[MongoDBAtlasVectorSearch],
    username: str,
    query: str,
    top_k: int = 3,
    score_threshold: float = DEFAULT_RECALL_SIMILARITY_THRESHOLD,
) -> str:
    """Passive, always-on counterpart to memory_recall_node's explicit-intent search. Runs on
    every conversational turn regardless of how the message is classified, but only surfaces
    chunks that clear score_threshold — so indirect phrasing like "do you remember..." still
    gets grounded in genuinely stored content instead of the model plausibly extrapolating,
    without misfiring on turns that have nothing relevant stored."""
    if vector_store is None or not query or not query.strip():
        return ""
    try:
        hits = vector_store.similarity_search_with_score(query, k=top_k, pre_filter={"username": username})
    except Exception:
        logger.exception("[MemorySearch] Passive semantic recall failed for %s", username)
        return ""

    relevant = [doc for doc, score in hits if score >= score_threshold]
    if not relevant:
        return ""

    lines = "\n".join(f"- {doc.page_content.strip()}" for doc in relevant)
    return (
        "\n\nRELEVANT PAST CONTEXT (genuinely recalled from this user's history — weave it in "
        f"naturally if it fits, don't ignore it):\n{lines}\n"
    )
