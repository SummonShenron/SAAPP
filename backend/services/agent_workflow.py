from __future__ import annotations
from typing import List, Any
import logging
from backend.models.attachment import Attachment
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.documents import Document
from backend.services.search import get_secure_retriever
from backend.models.models import llm
from backend.state import graph_db
from backend.components.constraints import (
    get_system_prompt,
    format_docs,
    CONVERSATIONAL_PROMPT,
    GRADING_PROMPT,
    REWRITING_PROMPT
)
from backend.state.graph_state import GraphState, route_user_query, route_after_grading
from langgraph.graph import StateGraph, START, END
logger = logging.getLogger("SASS Logger")


# ============================================================
# RETRIEVE NODE (sync)
# ============================================================

def retrieve_node(state: GraphState, vector_store) -> dict:
    logger.info("--- RETRIEVING DOCUMENTS & GRAPH CONTEXT ---")
    # Get user prompt parameters
    question = state["messages"][-1].content
    username = state.get("username")
    target_scope = state.get("target_scope")
    current_loops = state.get("loop_count", 0) or 0
    original_question = state.get("original_question") or question
    # memory_docs = flatten_saved_conversations(username)
    session_id = f"{username}_session"
    if state.get("attachment_summaries"):
        logger.info("Attachment detected — skipping vector search and using only priority docs.")
        return {
            **state,
            "documents": [
                Document(
                    page_content=summary,
                    metadata={"source": "user_attachment_summary", "priority": True}
                )
                for summary in state.get("attachment_summaries", [])
            ],
            "loop_count": state.get("loop_count", 0) + 1
        }
    try:
        # 1. Call secure multi-tenant vector search service
        retriever = get_secure_retriever(
            vector_store=vector_store,
            target_scope=target_scope,
            query_text=question,
            top_k=3
        )
        docs = retriever.invoke(question)
        logger.info(f"Retrieved {len(docs)} documents for query: '{question}'")
        for idx, doc in enumerate(docs, start=1):
            src = doc.metadata.get("source", "Unknown")
            page = doc.metadata.get("page", doc.metadata.get("page_label", "N/A"))
            logger.info(f"    - Rank {idx}: {src} (Page {page})")
        if docs is None:
            docs = []
    except Exception as e:
        logger.error(f"Vector search failed to retrieve documents: {e}")
        docs = []
    summaries = state.get("attachment_summaries", [])
    for summary in summaries:
        docs.append(Document(
            page_content=summary,
            metadata={"source": "user_attachment_summary", "priority": True}
        )) 
    # 1. Retrieve vector docs
    docs = retriever.invoke(question) or []
    logger.info(f"Retrieved {len(docs)} documents for query: '{question}'")

    # 2. Priority attachment docs FIRST
    priority_docs = []
    summaries = state.get("attachment_summaries", [])
    for summary in summaries:
        priority_docs.append(Document(
            page_content=summary,
            metadata={"source": "user_attachment_summary", "priority": True}
        ))

    # 3. Merge: priority docs first, then vector docs
    docs = priority_docs + docs
       
    graph_context = []
    try:
        question_lower = question.lower()
        for entity in graph_db.knowledge_graph.nodes:
            if str(entity).lower() in question_lower:
                logger.info(f"GraphRAG Entity match ID found: '{entity}'")
                relations = graph_db.get_dynamic_context(entity, hops=2)
                graph_context.extend(relations)
    except Exception as e:
        logger.error(f"GraphRAG Entity scanner failed: {e}")
    # 3. Securely augment document context array
    if graph_context:
        facts = list(set(graph_context))
        logger.info(f"Merging {len(facts)} GraphRAG relationships into vector arrays")
        for fact in facts:
            docs.append(Document(
                page_content=f"Connection: {fact}",
                metadata={"source": "knowledge_graph_db", "type": "relationship"}
            ))
    return {
        **state,
        "documents": docs,
        "loop_count": current_loops + 1,
        "original_question": original_question
    }

# ============================================================
# CONVERSATIONAL NODE (sync - pass-through for stream)
# ============================================================

def conversational_node(state: GraphState) -> dict:
    logger.info("--- CONVERSATIONAL NODE (PASS-THROUGH ENFORCED) ---")
    # Mark state so the gateway knows to format conversation rules
    return {**state, "relevance_grade": "conversational"}


# ============================================================
# GENERATE NODE (sync - pass-through for stream)
# ============================================================

def generate_node(state: GraphState) -> dict:
    logger.info("--- GENERATING RESPONSE ---")
    # No-op node. Exits graph instantly so FastAPI can execute the direct stream.
    return state


# ============================================================
# GRADING NODE (sync)
# ============================================================

def grading_node(state: GraphState) -> dict:
    logger.info("--- GRADING RETRIEVED CONTENT ---")
    question = state["messages"][-1].content
    documents = state.get("documents", [])

    # If no documents, preserve state
    if not documents:
        return { **state, "relevance_grade": "no" }

    combined_docs = format_docs(documents)
    formatted_prompt = GRADING_PROMPT.format(
        context=combined_docs,
        question=question,
        history=""
    )

    try:
        logger.info("Grading response")
        response = llm.invoke(formatted_prompt)
        response_text = response.content if hasattr(response, "content") else str(response)
        response_clean = response_text.lower().strip()

        grade = "yes" if "yes" in response_clean else "no"

        logger.info(f"Document grading complete. Grade: {grade}")
        for idx, doc in enumerate(state["documents"], start=1):
            src = doc.metadata.get("source", "Unknown")
            page = doc.metadata.get("page", doc.metadata.get("page_label", "N/A"))
            logger.info(f"    - Doc {idx}: {src} (Page {page}) → Grade: {grade}")

        # Preserve entire state
        return { **state, "relevance_grade": grade }

    except Exception as e:
        logger.error(f"Grading failed: {e}. Defaulting to no.")
        return { **state, "relevance_grade": "no" }

    
# ============================================================
# QUERY REWRITE NODE (sync)
# ============================================================

def rewrite_query_node(state: GraphState) -> dict:
    logger.info("--- REWRITING QUERY FOR BETTER RETRIEVAL ---")

    # Original question
    original_question = state["messages"][-1].content
    # Build rewrite prompt
    formatted_prompt = REWRITING_PROMPT.format(question=original_question)

    try:
        # Call LLM
        response = llm.invoke(formatted_prompt)
        rewrite_text = response.content if hasattr(response, "content") else str(response)
        rewrite_clean = rewrite_text.strip()

        logger.info(f"Query rewritten: '{original_question}' -> '{rewrite_clean}'")

        # Replace the last HumanMessage with rewritten query
        new_messages = list(state["messages"])
        new_messages[-1] = HumanMessage(content=rewrite_clean)

        # Return updated state
        return {
            **state,
            "messages": new_messages,
            "question": rewrite_clean
        }

    except Exception as e:
        logger.error(f"Query rewrite node failed: {e}")
        return state

# ============================================================
# WORKFLOW ASSEMBLY & COMPILATION
# ============================================================

def create_workflow(vector_store):
    workflow = StateGraph(GraphState)
    def retrieve_node_with_store(state):
        return retrieve_node(state, vector_store)
    workflow.add_node("retrieve_node", retrieve_node_with_store)
    workflow.add_node("grade_documents_node", grading_node)
    workflow.add_node("rewrite_query_node", rewrite_query_node)
    workflow.add_node("generate_node", generate_node)
    workflow.add_node("conversational_node", conversational_node)
    workflow.add_conditional_edges(
        START,
        route_user_query,
        {
            "retrieve_node": "retrieve_node",
            "conversational_node": "conversational_node"
        }
    )
    workflow.add_edge("retrieve_node", "grade_documents_node")
    workflow.add_edge("rewrite_query_node", "retrieve_node")
    workflow.add_conditional_edges(
        "grade_documents_node",
        route_after_grading,
        {
            "generate_node": "generate_node",
            "rewrite_query_node": "rewrite_query_node",
            "fallback_empty": "generate_node"
        }
    )
    workflow.add_edge("generate_node", END)
    workflow.add_edge("conversational_node", END)
    return workflow.compile()