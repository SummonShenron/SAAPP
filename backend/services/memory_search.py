import logging
from datetime import datetime, timezone
from typing import List, Optional

from langchain_core.documents import Document
from langchain_mongodb import MongoDBAtlasVectorSearch

logger = logging.getLogger("SASS Logger")

USER_MEMORY_COLLECTION = "user_memory_chunks"
USER_MEMORY_INDEX = "user_memory_vector_index"


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
