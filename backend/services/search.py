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

# Environment-driven configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "saapp_database")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))

# Singleton MongoDB Client connection pool
_mongo_client: Optional[MongoClient] = None

def _get_mongo_collection(collection_name: str):
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    db = _mongo_client[DB_NAME]
    return db[collection_name]

# Embeddings helper
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
    """
    collection = _get_mongo_collection(collection_name)
    try:
        query_vector = _embeddings.embed_query(query)
        
        vector_search_stage: Dict[str, Any] = {
            "queryVector": query_vector,
            "index": "vector_index",
            "path": "embedding",
            "numCandidates": max(100, top_k * 10),
            "limit": top_k,
        }

        # Fix: Only apply affiliate filter if specific scopes are passed (ignoring "All")
        active_filters = [a for a in affiliate_scope if a and a != "All"]
        if active_filters:
            vector_search_stage["filter"] = {
                "affiliate": {"$in": active_filters}
            }

        pipeline = [
            {"$vectorSearch": vector_search_stage},
            {
                "$project": {
                    "page_content": {"$ifNull": ["$text", "$page_content"]},
                    "source": 1,
                    "filename": 1,
                    "page": 1,
                    "page_label": 1,
                    "affiliate": 1,
                    "priority": 1,
                    "metadata": 1,
                    "score": {"$meta": "searchScore"}
                }
            }
        ]
        
        results = list(collection.aggregate(pipeline))
        docs: List[Document] = []
        
        for r in results:
            page_content = r.get("page_content") or r.get("text") or ""
            raw_meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            
            metadata = {
                **raw_meta,
                "source": r.get("source") or r.get("filename") or raw_meta.get("source") or raw_meta.get("filename") or "Unknown",
                "page": r.get("page") if "page" in r else raw_meta.get("page"),
                "page_label": r.get("page_label") or raw_meta.get("page_label"),
                "affiliate": r.get("affiliate") or raw_meta.get("affiliate"),
                "priority": r.get("priority") or raw_meta.get("priority") or False,
                "score": r.get("score", 0.0)
            }
            docs.append(Document(page_content=page_content, metadata=metadata))
            
        return docs
    except Exception as e:
        logger.error(f"MongoDB vector search failed: {e}")
        return []

# -------------------------
# Lexical TF-IDF Scoring Helper
# -------------------------
def _score_lexical(candidate_pool: List[Document], query: str, top_k: int) -> List[Document]:
    keywords = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2]
    if not keywords or not candidate_pool:
        return candidate_pool[:top_k]

    num_docs = len(candidate_pool)
    doc_frequencies = {
        kw: sum(1 for doc in candidate_pool if kw in doc.page_content.lower()) 
        for kw in keywords
    }

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

# -------------------------
# Retrieval Strategies
# -------------------------
def _retrieve_lexical(query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    logger.info(f"Retrieving Lexical context (k={top_k}).")
    affiliate_scope = search_filter.get("metadata.affiliate", {}).get("$in", ["All"])
    candidate_pool = _mongo_vector_search(query, affiliate_scope, top_k * 5)
    return _score_lexical(candidate_pool, query, top_k)

def _retrieve_vector(query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    logger.info(f"Retrieving Vector context (k={top_k}).")
    affiliate_scope = search_filter.get("metadata.affiliate", {}).get("$in", ["All"])
    return _mongo_vector_search(query, affiliate_scope, top_k)

def _retrieve_hybrid(query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    logger.info("Executing Hybrid Retrieval (Vector + Lexical)")
    try:
        affiliate_scope = search_filter.get("metadata.affiliate", {}).get("$in", ["All"])
        
        # Optimization: Embed and retrieve candidate pool ONCE
        candidate_pool = _mongo_vector_search(query, affiliate_scope, top_k * 4)
        if not candidate_pool:
            return []

        vector_docs = candidate_pool[:top_k]
        lexical_docs = _score_lexical(candidate_pool, query, top_k)

        rrf_scores: Dict[str, float] = {}
        doc_mapping: Dict[str, Document] = {}

        def process_rankings(docs: List[Document]):
            for rank, doc in enumerate(docs):
                # Unique ID prevents collisions on duplicate chunk texts
                doc_id = f"{doc.metadata.get('source')}_{doc.metadata.get('page')}_{hash(doc.page_content)}"
                doc_mapping[doc_id] = doc
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (60.0 + (rank + 1)))

        process_rankings(vector_docs)
        process_rankings(lexical_docs)

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
    Enumerate unique filenames using fast indexed MongoDB distinct calls.
    """
    if not collection_name:
        return []

    try:
        collection = _get_mongo_collection(collection_name)
        
        query_filter = {}
        if affiliate_scope and affiliate_scope != "All":
            query_filter = {
                "$or": [
                    {"affiliate": affiliate_scope},
                    {"metadata.affiliate": affiliate_scope}
                ]
            }

        # Fix: Use native distinct queries rather than loading full documents into Python memory
        sources = collection.distinct("metadata.source", query_filter)
        if not sources:
            sources = collection.distinct("filename", query_filter)

        unique_files = {os.path.basename(s) for s in sources if s}
        return sorted(list(unique_files))
    except Exception as e:
        logger.error(f"[-] Document discovery disruption: {str(e)}")
        return []

# -------------------------
# Secure retriever factory (LangChain-compatible)
# -------------------------
def get_secure_retriever(
    vector_store: Optional[Any], 
    target_scope: List[str], 
    query_text: str, 
    top_k: int = 3, 
    collection_name: str = "documents"
) -> Callable[[str], List[Document]]:

    if not target_scope:
        target_scope = ["All"]

    strategy = _detect_routing_strategy(query_text)
    logger.info(f"Routing query to [{strategy.upper()}] engine.")

    search_filter = {"metadata.affiliate": {"$in": target_scope}}

    def retrieve(query: str) -> List[Document]:
        if strategy == "vector":
            return _retrieve_vector(query, search_filter, top_k)
        elif strategy == "lexical":
            return _retrieve_lexical(query, search_filter, top_k)
        elif strategy == "hybrid":
            return _retrieve_hybrid(query, search_filter, top_k)
        else:
            return _retrieve_vector(query, search_filter, top_k)

    retrieve.invoke = retrieve
    return retrieve