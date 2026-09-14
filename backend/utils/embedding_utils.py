import logging
from typing import List, Optional

import numpy as np
from langchain_google_genai import GoogleGenerativeAIEmbeddings

logger = logging.getLogger("SASS Logger")

_embeddings: Optional[GoogleGenerativeAIEmbeddings] = None


def _get_embeddings_client() -> GoogleGenerativeAIEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = GoogleGenerativeAIEmbeddings(model="gemini-embedding-001", output_dimensionality=768)
    return _embeddings


def embed_text(text: str) -> Optional[List[float]]:
    """Best-effort synchronous embedding of a short piece of text. Returns None (and logs)
    on any failure rather than raising, so callers can always treat 'no embedding' as a
    safe, comparable-to-nothing default instead of crashing."""
    if not text or not text.strip():
        return None
    try:
        return _get_embeddings_client().embed_query(text.strip())
    except Exception:
        logger.exception("[EmbeddingUtils] Failed to embed text.")
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)
