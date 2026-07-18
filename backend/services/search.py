import os
import logging
import math
import re
from typing import List, Dict, Any, Callable, Optional

from pymongo import MongoClient
from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# --- Configuration and logger ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
logger = logging.getLogger("SASS Logger")
logger.setLevel(logging.INFO)
logger.info("Initializing Unified Search Service Engine (MongoDB backend)...")

# Environment-driven configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "saapp_database")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
EMBEDDINGS_BATCH_SIZE = int(os.getenv("EMBEDDINGS_BATCH_SIZE", "16"))

# Initialize MongoDB client and collection handle
def _get_mongo_collection(collection_name: str):
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    db = client[DB_NAME]
    return db[collection_name]

# Embeddings helper (reused by retrieval functions)
_embeddings = GoogleGenerativeAIEmbeddings(
    model=EMBEDDING_MODEL,
    output_dimensionality=EMBEDDING_DIM
)

# -------------------------
# Routing strategy
# -------------------------
def _detect_routing_strategy(query: str) -> str:
    clean_query = query.lower().strip()
    lexical_markers = [
        "recent", "latest", "newest", "oldest", "release", "date",
        "year", "when", "timeline", "chronology", "current", "last"
    ]
    if any(marker in clean_query for marker in lexical_markers):
        return "lexical"

    hybrid_markers = [
        "compare", "difference", "versus", "vs", "relationship",
        "how does", "why did", "explain the connection", "analyze"
    ]
    if any(marker in clean_query for marker in hybrid_markers) or len(clean_query.split()) > 12:
        return "hybrid"

    return "vector"

# -------------------------
# Core MongoDB vector search
# -------------------------
def _mongo_vector_search(
    query: str,
    affiliate_scope: List[str],
    top_k: int = 3,
    collection_name: str = "documents"
) -> List[Document]:
    """
    Run a MongoDB $vectorSearch aggregation against the specified collection.
    Returns a list of LangChain Document objects.
    """
    collection = _get_mongo_collection(collection_name)
    try:
        # Embed the query using the same embedding model used at ingestion
        query_vector = _embeddings.embed_query(query)

        pipeline = [
            {
                "$vectorSearch": {
                    "queryVector": query_vector,
                    "index": "vector_index",
                    "path": "embedding",
                    "numCandidates": max(100, top_k * 10),
                    "limit": top_k,
                    "filter": {
                        "affiliate": {"$in": affiliate_scope}
                    }
                }
            },
            {
                "$project": {
                    "page_content": {
                        "$ifNull": ["$text", "$page_content"]
                    },
                    "metadata": 1,
                    "score": {"$meta": "searchScore"}
                }
            }
        ]

        results = list(collection.aggregate(pipeline))
        docs: List[Document] = []
        for r in results:
            page_content = r.get("page_content") or r.get("text") or ""
            metadata = r.get("metadata", {})
            # Keep original source and page_label if present
            docs.append(Document(page_content=page_content, metadata=metadata))
        return docs

    except Exception as e:
        logger.error(f"MongoDB vector search failed: {e}")
        return []

# -------------------------
# Lexical retrieval (TF-IDF over candidate pool)
# -------------------------
def _retrieve_lexical(
    vector_store: Optional[Any],
    query: str,
    search_filter: Dict[str, Any],
    top_k: int
) -> List[Document]:
    logger.info(f"Retrieving Lexical context (k={top_k}).")
    try:
        affiliate_scope = search_filter.get("metadata.affiliate", {}).get("$in", ["All"])
        # Use vector search to get a candidate pool, then rank lexically
        candidate_pool = _mongo_vector_search(query, affiliate_scope, top_k * 5)
        if not candidate_pool:
            return []

        keywords = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2]
        if not keywords:
            return candidate_pool[:top_k]

        num_docs = len(candidate_pool)
        doc_frequencies = {}
        for kw in keywords:
            doc_frequencies[kw] = sum(1 for doc in candidate_pool if kw in doc.page_content.lower())

        scored_candidates = []
        for doc in candidate_pool:
            content_lower = doc.page_content.lower()
            doc_score = 0.0
            for kw in keywords:
                term_count = content_lower.count(kw)
                if term_count > 0:
                    tf = 1 + math.log(term_count)
                    df = doc_frequencies.get(kw, 0)
                    idf = math.log(1 + (num_docs / (1 + df)))
                    doc_score += tf * idf
            scored_candidates.append((doc, doc_score))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        top_docs = [doc for doc, score in scored_candidates if score > 0.0][:top_k]
        return top_docs or candidate_pool[:top_k]

    except Exception as e:
        logger.error(f"Lexical retrieval pipeline exception: {e}")
        return []

# -------------------------
# Vector retrieval wrapper (uses MongoDB)
# -------------------------
def _retrieve_vector(
    vector_store: Optional[Any],
    query: str,
    search_filter: Dict[str, Any],
    top_k: int
) -> List[Document]:
    logger.info(f"Retrieving Vector context (k={top_k}).")
    print(f"DEBUG: Searching with Filter: {search_filter}")
    print(f"DEBUG: Searching with Query: {query}")
    try:
        affiliate_scope = search_filter.get("metadata.affiliate", {}).get("$in", ["All"])
        return _mongo_vector_search(query, affiliate_scope, top_k)
    except Exception as e:
        logger.error(f"Dense vector search failure: {e}")
        return []

# -------------------------
# Hybrid retrieval (RRF fusion)
# -------------------------
def _retrieve_hybrid(
    vector_store: Optional[Any],
    query: str,
    search_filter: Dict[str, Any],
    top_k: int
) -> List[Document]:
    logger.info("Executing Hybrid Retrieval (Vector + Lexical)")
    try:
        vector_docs = _retrieve_vector(vector_store, query, search_filter, top_k)
        lexical_docs = _retrieve_lexical(vector_store, query, search_filter, top_k)

        rrf_scores: Dict[str, float] = {}
        doc_mapping: Dict[str, Document] = {}

        for rank, doc in enumerate(vector_docs):
            doc_id = doc.page_content
            doc_mapping[doc_id] = doc
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60.0 + (rank + 1)))

        for rank, doc in enumerate(lexical_docs):
            doc_id = doc.page_content
            doc_mapping[doc_id] = doc
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60.0 + (rank + 1)))

        fused_results = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        return [doc_mapping[doc_id] for doc_id in fused_results][:top_k]

    except Exception as e:
        logger.error(f"Hybrid RRF execution failure: {e}")
        return []

# -------------------------
# Workspace discovery (list files for affiliate)
# -------------------------
def discover_workspace_documents(vector_store: Optional[Any], affiliate_scope: str, collection_name: str = "documents") -> List[str]:
    """
    Enumerate unique filenames (source) for the given affiliate scope.
    """
    if not collection_name:
        return []

    try:
        collection = _get_mongo_collection(collection_name)
        cursor = collection.find({}, {"metadata": 1})
        unique_files = set()
        for doc in cursor:
            nested_meta = doc.get("metadata", {})
            meta = nested_meta.get("metadata", nested_meta)
            if meta.get("affiliate") == affiliate_scope or affiliate_scope == "All":
                source_path = meta.get("source", "Unknown")
                filename = os.path.basename(source_path)
                unique_files.add(filename)
        return sorted(list(unique_files))
    except Exception as e:
        logger.error(f"[-] Document discovery disruption: {str(e)}")
        return []

# -------------------------
# Secure retriever factory (LangChain-compatible)
# -------------------------
def get_secure_retriever(vector_store: Optional[Any], target_scope: List[str], query_text: str, top_k: int = 3, collection_name: str = "documents") -> Callable[[str], List[Document]]:
    """
    Returns a callable retriever that LangChain-style code can call.
    The 'vector_store' parameter is accepted for compatibility but is not used (MongoDB is the source).
    """
    if not target_scope:
        target_scope = ["All"]

    strategy = _detect_routing_strategy(query_text)
    logger.info(f"Routing query to [{strategy.upper()}] engine.")

    search_filter = {"metadata.affiliate": {"$in": target_scope}}

    def retrieve(query: str) -> List[Document]:
        if strategy == "vector":
            return _retrieve_vector(None, query, search_filter, top_k)
        elif strategy == "lexical":
            return _retrieve_lexical(None, query, search_filter, top_k)
        elif strategy == "hybrid":
            return _retrieve_hybrid(None, query, search_filter, top_k)
        else:
            logger.warning(f"Unknown strategy '{strategy}'. Falling back to vector.")
            return _retrieve_vector(None, query, search_filter, top_k)

    # LangChain compatibility: attach invoke
    retrieve.invoke = retrieve
    return retrieve
