import networkx as nx
import json
import os
import logging
import difflib
from backend.models.models import llm
from backend.components.constraints import RELATIONSHIP_PROMPT


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
        data = json.loads(response)
        update_graph(data.get("relationships", []))
        logger.info("Extracted and built Graph!")
    except Exception as e:
        logger.error(f"Failed to extract and build Graph... {e}")

# Tries to relate names with nicknames 
def normalize_entity(entity: str, threshold: float = 0.85) -> str:
    existing_nodes = list(knowledge_graph.nodes())
    if not existing_nodes:
        return entity
    matches = difflib.get_close_matches(entity, existing_nodes, n=1, cutoff=threshold)
    if matches:
        return matches[0] # og name
    return entity

# Retrieves multi-hop content
def get_dynamic_context(entity: str, hops: int = 1):
    og_entity = normalize_entity(entity, threshold=0.7)
    if og_entity not in knowledge_graph:
        return []
    context_nodes = set(knowledge_graph.neighbors(og_entity))
    context_nodes.add(og_entity)
    if hops > 1:
        for neighbor in list(context_nodes):
            context_nodes.update(knowledge_graph.neighbors(neighbor))
    results = []
    nodes_list = list(context_nodes)
    for current_index, source_node in enumerate(nodes_list):
        for target_node in nodes_list[current_index+1:]:   
            if knowledge_graph.has_edge(source_node, target_node):
                edge_data = knowledge_graph.get_edge_data(source_node, target_node)
                results.append(f"{source_node} --({edge_data['relation']})--> {target_node}")
            elif knowledge_graph.has_edge(target_node, source_node):
                edge_data = knowledge_graph.get_edge_data(target_node, source_node)
                results.append(f"{target_node} --({edge_data['relation']})--> {source_node}")
    return results        

def add_document_to_graph(doc_content: str):
    extract_and_build(doc_content)
