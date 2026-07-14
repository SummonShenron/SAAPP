from __future__ import annotations
from typing import List, Any, Optional, Dict
from typing_extensions import TypedDict
from backend.models.attachment import Attachment
from langchain_core.messages import BaseMessage
import logging

logger = logging.getLogger("SASS Logger")

class GraphState(TypedDict):
    """
    Message-based state for LangGraph streaming.
    Every node receives this state, modifies it, and passes it forward.
    """
    messages: List[BaseMessage]     # Full conversation history (Human + AI)
    username: str                   # Authenticated user identity
    target_scope: List[str]         # Allowed tenant affiliates
    documents: List[Any]            # Retrieved vector + GraphRAG docs
    relevance_grade: str            # yes/no relevance evaluation
    loop_count: int                 # Rewrite loop counter
    original_question: str          # First question before rewrites
    attachment_summaries: List[str] # Summary of user attached content
    coordinator_intent: str         # e.g. "retrieve", "summarize", "paapp", etc.
    coordinator_plan: List[str]     # ordered list of agents to run
    snapshot: Optional[Dict[str, Any]]
    classified: Optional[Dict[str, Any]]
    analysis_output: Optional[Dict[str, Any]]
    patterns: Optional[Dict[str, Any]]
    trends: Optional[Dict[str, Any]]
    insights: Optional[List[Dict[str, Any]]]

# def route_user_query(state: GraphState) -> str:
#     """
#     Routes between conversational and retrieval modes.
#     Now uses the latest message instead of state['question'].
#     """
#     query = state["messages"][-1].content.lower().strip()
#     words = query.split()

#     info_markers = [
#         "find", "get", "doc", "pdf", "release",
#         "date", "status", "report", "what",
#         "how do", "how would"
#     ]
#     if any(marker in query for marker in info_markers):
#         return "retrieve_node"

#     conversational_triggers = [
#         "hello", "hi", "hey", "who are you",
#         "clear", "thanks", "thank you",
#         "how are", "how is"
#     ]
#     if any(trigger in query for trigger in conversational_triggers):
#         return "conversational_node"

#     if len(words) > 5:
#         return "retrieve_node"

#     return "retrieve_node"


def route_after_grading(state: GraphState) -> str:
    """
    Routes after grading: generate, rewrite, or fallback.
    Attachments ALWAYS skip rewrite.
    """
    if state.get("attachment_summaries"):
        logger.info("Priority attachment detected — skipping rewrite and routing directly to Generation.")
        return "generate_node"

    grade = state.get("relevance_grade")
    loops = state.get("loop_count", 0)

    if grade == "yes":
        logger.info("Documents graded RELEVANT. Routing to Generation.")
        return "generate_node"

    if loops < 2:
        logger.info(f"Documents graded IRRELEVANT (Loop {loops}/2). Routing to Query Rewrite.")
        return "rewrite_query_node"

    logger.warning("Max loops reached, routing to fallback")
    return "fallback_empty"

