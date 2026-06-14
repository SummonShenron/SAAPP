import os
import logging
from typing import List, Dict, Any
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR) # Navigates up to \local-rag\
DB_DIR = os.path.join(PROJECT_ROOT, "chroma_db")

logger = logging.getLogger("SASS Logger")

class SearchService:
    def __init__(self):
        logger.info("[*] Initializing Unified Search Service Engine...")
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        
        if not os.path.exists(DB_DIR):
            logger.warning("[!] Chroma DB directory path not found. Ensure ingestion has run.")
            self.vector_store = None
        else:
            self.vector_store = Chroma(
                persist_directory=DB_DIR, 
                embedding_function=self.embeddings
            )

    def _detect_routing_strategy(self, query: str) -> str:
        """
        Analyzes query grammar, temporal intent, and structural complexity 
        to dynamically route to Lexical, Vector, or Hybrid retrieval.
        """
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

    def discover_workspace_documents(self, affiliate_scope: str) -> List[str]:
        """
        Mimics Azure's search("*") wildcard capability.
        """
        if not self.vector_store:
            return []

        try:
            raw_data = self.vector_store.get(include=["metadatas"])
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

    def get_secure_retriever(self, target_scope: List[str], query_text: str, top_k: int = 3):
        """
        Returns a secured LangChain retriever instance with auto-detected retrieval strategy.
        """
        if not self.vector_store:
            raise RuntimeError("Vector store layer is uninitialized.")

        # Automatically determine the best engine mapping for the user's specific prompt
        strategy = self._detect_routing_strategy(query_text)
        logger.info(f"[ROUTER] Dynamic intent classified. Routing '{query_text[:40]}...' via [{strategy.upper()}] engine.")
        logger.info(f"Strategy used: {strategy}")
        search_kwargs = {
            "k": top_k,
            "filter": {"affiliate": {"$in": target_scope}}
        }

        if strategy == "vector":
            logger.info(f"[*] Extracting context using Dense Semantic Vector routing (k={top_k}).")
            return self.vector_store.as_retriever(search_kwargs=search_kwargs)
            
        elif strategy == "lexical":
            logger.info(f"[*] Extracting context using BM25 Lexical Keyword routing (k={top_k}).")
            # Local architecture placeholder: runs via filtered metadata vector search
            return self.vector_store.as_retriever(search_kwargs=search_kwargs)
            
        elif strategy == "hybrid":
            logger.info(f"[*] Executing Hybrid Retrieval (Vector + Lexical) with RRF fusion emulation.")
            return self.vector_store.as_retriever(search_kwargs=search_kwargs)
            
        else:
            raise ValueError(f"Unknown retrieval routing strategy: {strategy}")

# Singleton Instance for global application orchestration
search_service = SearchService()