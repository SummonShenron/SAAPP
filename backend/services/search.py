import os
import logging
import math
import re
from typing import List, Dict, Any
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from backend.state.graph_db import get_dynamic_context, knowledge_graph

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR) # Navigates up to \local-rag\
DB_DIR = os.path.join(PROJECT_ROOT, "chroma_db")
logger = logging.getLogger("SASS Logger")
logger.info("Initializing Unified Search Service Engine...")

def _detect_routing_strategy(query: str) -> str:
    clean_query = query.lower().strip()
    # 1. TEMPORAL / KEYWORD MARKERS -> Route to Lexical
    # Captures specific timeline requests, release dates, and versions
    lexical_markers = [
        "recent", "latest", "newest", "oldest", "release", "date", 
        "year", "when", "timeline", "chronology", "current", "last"
    ]
    if any(marker in clean_query for marker in lexical_markers):
        return "lexical"

    # 2. COMPLEX/ANALYTICAL CONCEPTUAL MARKERS -> Route to Hybrid
    # Captures comparative, multi-clause requests or deeply analytical questions
    hybrid_markers = [
        "compare", "difference", "versus", "vs", "relationship", 
        "how does", "why did", "explain the connection", "analyze"
    ]
    
    # Route to hybrid if explicit complex markers are found, OR if the query 
    # is a long, highly descriptive sentence (typically > 12 words)
    if any(marker in clean_query for marker in hybrid_markers) or len(clean_query.split()) > 12:
        return "hybrid"

    # 3. GENERAL CONCEPTUAL FALLBACK -> Route to Vector
    # Perfect for open-ended lore, summaries, or semantic themes
    return "vector"

def _retrieve_vector(vector_store, query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    """Runs standard dense vector semantic similarity search."""
    logger.info(f"Retrieving Vector context (k={top_k}).")
    if not vector_store:
        logger.error("Vector store is uninitialized.")
        return []
    try:
        return vector_store.similarity_search(query, k=top_k, filter=search_filter)
    except Exception as e:
        logger.error(f"Dense vector search failure: {e}")
        return []
    
def _retrieve_lexical(vector_store, query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    logger.info(f"Retrieving Lexical context (k={top_k}).")
    if not vector_store:
        logger.error("Lexical routing failed.")
        return []
    try:
        # Step 1: Fetch candidate pool securely
        candidate_pool = vector_store.similarity_search(query, k=top_k * 5, filter=search_filter)
        if not candidate_pool:
            return []

        # Step 2: Clean query keywords
        keywords = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2]
        if not keywords:
            return candidate_pool[:top_k]

        # Step 3: Compute TF-IDF ranking across fetched context candidates
        scored_candidates = []
        num_docs = len(candidate_pool)
        
        doc_frequencies = {}
        for kw in keywords:
            doc_frequencies[kw] = sum(1 for doc in candidate_pool if kw in doc.page_content.lower())

        for doc in candidate_pool:
            content_lower = doc.page_content.lower()
            doc_score = 0.0
            
            for kw in keywords:
                term_count = content_lower.count(kw)
                if term_count > 0:
                    tf = 1 + math.log(term_count)
                    df = doc_frequencies[kw]
                    idf = math.log(1 + (num_docs / (1 + df)))
                    doc_score += tf * idf
            
            scored_candidates.append((doc, doc_score))

        # Step 4: Sort candidates by score descending
        scored_candidates.sort(key=lambda x: x[1], reverse=True)
        return [doc for doc, score in scored_candidates if score > 0.0][:top_k] or candidate_pool[:top_k]

    except Exception as e:
        logger.error(f"Lexical retrieval pipeline exception: {e}")
        return []


def _retrieve_hybrid(vector_store, query: str, search_filter: Dict[str, Any], top_k: int) -> List[Document]:
    logger.info(f"Executing Hybrid Retrieval (Vector + Lexical)")
    try:
        vector_docs = _retrieve_vector(vector_store, query, search_filter, top_k)
        lexical_docs = _retrieve_lexical(vector_store, query, search_filter, top_k)

        # Compute RRF scores: RRF_Score(d) = sum( 1 / (60 + rank(d)) )
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

def discover_workspace_documents(vector_store, affiliate_scope: str) -> List[str]:
    """
    Mimics Azure's search("*") wildcard capability.
    """
    if not vector_store:
        return []

    try:
        raw_data = vector_store.get(include=["metadatas"])
        metadatas = raw_data.get("metadatas", [])

        unique_files = set()
        for meta in metadatas:
            if meta.get("affiliate") == affiliate_scope or affiliate_scope == "All":
                source_path = meta.get("source", "Unknown")
                filename = os.path.basename(source_path)
                unique_files.add(filename)

        return sorted(list(unique_files))
    except Exception as e:
        logger.error(f"[-] Document discovery disruption: {str(e)}")
        return []

def get_secure_retriever(vector_store, target_scope: List[str], query_text: str, top_k: int = 3):
    """
    Returns a secured LangChain retriever instance with auto-detected retrieval strategy.
    """
    if not vector_store:
        raise RuntimeError("Vector store layer is uninitialized.")

    # Automatically determine the best engine mapping for the user's specific prompt
    strategy = _detect_routing_strategy(query_text)
    logger.info(f"Routing query to [{strategy.upper()}] engine.")
    logger.info(f"Strategy used: {strategy}")
    search_filter = {"affiliate": {"$in": target_scope}}
    

    def retrieve(query: str) -> List[Document]:
        if strategy == "vector":
            return _retrieve_vector(vector_store, query, search_filter, top_k)
        elif strategy == "lexical":
            return _retrieve_lexical(vector_store, query, search_filter, top_k)
        elif strategy == "hybrid":
            return _retrieve_hybrid(vector_store, query, search_filter, top_k)
        else:
            logger.warning(f"Unknown strategy '{strategy}'. Falling back to vector.")
            return _retrieve_vector(vector_store, query, search_filter, top_k)

    # Attach itself to its own invoke property to meet LangChain's contract
    retrieve.invoke = retrieve
    return retrieve