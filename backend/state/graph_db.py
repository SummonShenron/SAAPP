"""The knowledge graph that retrieve_node searches alongside the vector index (GraphRAG).

Entities and the relationships between them are stored in MongoDB (the `graph_edges` collection), one document per edge:
{affiliate, source, s, t, relation}. Every edge carries the knowledge base it came from (`affiliate`) and the file it was read from (`source`),
for two reasons: retrieval only ever reads the edges of the knowledge bases the person is allowed to use (the same tenant isolation the vector
search has), and deleting a document can remove exactly the edges it produced.

The edges are written by one place only, the ingestion function app (local_function_app/function_app.py), when a document is indexed, and by the
one-off backfill (backend/utils/graph_backfill.py), which calls the function app's own code, for documents indexed before that existed. This module
only reads them, and removes a deleted document's edges. The older single-file graph further down (knowledge_graph, graph.json) is kept for the code that still
uses it; retrieval no longer does.
"""
import networkx as nx
import json
import os
import re
import time
import logging
import difflib
from typing import Dict, Iterable, List, Optional, Tuple
from backend.models.models import llm
from backend.components.constraints import RELATIONSHIP_PROMPT
from backend.utils.db_utils import get_db


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
GRAPH_DIR = os.path.join(PROJECT_ROOT, "knowledge_graph")
os.makedirs(GRAPH_DIR, exist_ok=True)
GRAPH_FILE = os.path.join(GRAPH_DIR, "graph.json")
knowledge_graph = nx.DiGraph()
logger = logging.getLogger("SASS Logger")

def load_graph():
    if os.path.exists(GRAPH_FILE):
        with open(GRAPH_FILE, 'r') as f:
            data = json.load(f)
            return nx.node_link_graph(data)
    return nx.DiGraph()

knowledge_graph = load_graph()

def save_graph():
    try:
        data = nx.node_link_data(knowledge_graph)
        with open(GRAPH_FILE, 'w') as f:
            json.dump(data, f)
        logger.info("Knowledge Graph saved!")
    except Exception as e:
        logger.error(f"Knowledge Graph failed to save! {e}")

def update_graph(relationships: list):
    for relationship in relationships:
        knowledge_graph.add_edge(relationship['s'], relationship['t'], relation=relationship['relationship'])
    save_graph()

def extract_and_build(text: str):
    prompt = RELATIONSHIP_PROMPT.format(text=text)
    response = llm.invoke(prompt)
    try:
        data = json.loads(getattr(response, "content", response))
        update_graph(data.get("relationships", []))
        logger.info("Extracted and built Graph!")
    except Exception as e:
        logger.error(f"Failed to extract and build Graph... {e}")

# Tries to relate names with nicknames 
def normalize_entity(entity: str, threshold: float = 0.85, graph: Optional[nx.DiGraph] = None) -> str:
    graph = knowledge_graph if graph is None else graph
    existing_nodes = list(graph.nodes())
    if not existing_nodes:
        return entity
    matches = difflib.get_close_matches(entity, existing_nodes, n=1, cutoff=threshold)
    if matches:
        return matches[0] # og name
    return entity

# Retrieves multi-hop content
def get_dynamic_context(entity: str, hops: int = 1, graph: Optional[nx.DiGraph] = None):
    graph = knowledge_graph if graph is None else graph
    og_entity = normalize_entity(entity, threshold=0.7, graph=graph)
    if og_entity not in graph:
        return []
    context_nodes = set(graph.neighbors(og_entity))
    context_nodes.add(og_entity)
    if hops > 1:
        for neighbor in list(context_nodes):
            context_nodes.update(graph.neighbors(neighbor))
    results = []
    nodes_list = list(context_nodes)
    for current_index, source_node in enumerate(nodes_list):
        for target_node in nodes_list[current_index+1:]:   
            if graph.has_edge(source_node, target_node):
                edge_data = graph.get_edge_data(source_node, target_node)
                results.append(f"{source_node} --({edge_data['relation']})--> {target_node}")
            elif graph.has_edge(target_node, source_node):
                edge_data = graph.get_edge_data(target_node, source_node)
                results.append(f"{target_node} --({edge_data['relation']})--> {source_node}")
    return results        

def add_document_to_graph(doc_content: str):
    extract_and_build(doc_content)



# ---------------------------------------------------------------------------------------------------------------
# The per-knowledge-base graph in MongoDB (what retrieval uses)
# ---------------------------------------------------------------------------------------------------------------

GRAPH_EDGES_COLLECTION = "graph_edges"
SCOPED_GRAPH_TTL_SECONDS = 300      # a freshly ingested document's edges show up in retrieval within this long
MAX_GRAPH_FACTS = 12                # relationship lines added to one answer's context, however big the graph is
MIN_ENTITY_CHARS = 3                # a name this short ("a", "it") would match inside nearly every question
# Page titles and headings become "entities" ("About", "Home", "Overview") and would match the ordinary word in any question that happens to use it.
GENERIC_ENTITY_WORDS = frozenset({
    "about", "home", "contact", "overview", "introduction", "summary", "info", "information", "menu", "login", "help", "faq", "faqs", "pricing",
    "schedule", "page", "pages", "site", "website", "app", "application", "user", "users", "file", "files", "text", "data", "system", "service",
    "services", "guide", "document", "documents", "section", "chapter", "notes", "note", "list", "table", "example", "examples", "name", "title",
    "this", "that", "they", "them", "there", "then", "here", "what", "when", "where", "which", "who", "how", "why", "the", "and", "for", "with",
})

_affiliate_graphs: Dict[str, Tuple[float, nx.DiGraph]] = {}


def delete_source_edges(db, source: str, affiliate: Optional[str] = None) -> int:
    """Remove every edge a file produced (in all knowledge bases when `affiliate` is not given). Used when a document is deleted."""
    query = {"source": source}
    if affiliate:
        query["affiliate"] = affiliate
    deleted = db[GRAPH_EDGES_COLLECTION].delete_many(query).deleted_count
    invalidate_scoped_graph(affiliate)
    return deleted


def invalidate_scoped_graph(affiliate: Optional[str] = None) -> None:
    if affiliate is None:
        _affiliate_graphs.clear()
    else:
        _affiliate_graphs.pop(affiliate, None)


def _affiliate_graph(affiliate: str, db=None, now: Optional[float] = None) -> nx.DiGraph:
    now = time.monotonic() if now is None else now
    cached = _affiliate_graphs.get(affiliate)
    if cached and now - cached[0] < SCOPED_GRAPH_TTL_SECONDS:
        return cached[1]
    db = get_db() if db is None else db
    graph = nx.DiGraph()
    if db is not None:
        for edge in db[GRAPH_EDGES_COLLECTION].find({"affiliate": affiliate}, {"s": 1, "t": 1, "relation": 1}):
            graph.add_edge(edge["s"], edge["t"], relation=edge.get("relation", "related to"))
    _affiliate_graphs[affiliate] = (now, graph)
    return graph


def scoped_graph(affiliates: Iterable[str], db=None) -> nx.DiGraph:
    """The graph of only the given knowledge bases (the caller passes the ones this person may use; "All" is not a knowledge base)."""
    graph = nx.DiGraph()
    for affiliate in dict.fromkeys(a for a in (affiliates or []) if a and a != "All"):
        graph = nx.compose(graph, _affiliate_graph(affiliate, db=db))
    return graph


def scoped_context(question: str, affiliates: Iterable[str], hops: int = 2, db=None) -> List[str]:
    """Relationship lines for every entity named in the question, from the given knowledge bases only, at most MAX_GRAPH_FACTS of them."""
    graph = scoped_graph(affiliates, db=db)
    if not graph.number_of_nodes():
        return []
    lowered = (question or "").lower()
    facts: List[str] = []
    for entity in graph.nodes:
        name = str(entity)
        if len(name) < MIN_ENTITY_CHARS or name.lower() in GENERIC_ENTITY_WORDS:
            continue
        if not re.search(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])", lowered):   # the whole name, not a piece of another word
            continue
        for fact in get_dynamic_context(name, hops=hops, graph=graph):
            if fact not in facts:
                facts.append(fact)
            if len(facts) >= MAX_GRAPH_FACTS:
                return facts
    return facts
