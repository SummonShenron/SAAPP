from __future__ import annotations
from typing import List, Any, Dict
import logging
import requests
from backend.models.attachment import Attachment
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.documents import Document
from settings import PAAPP_BASE_URL
from backend.services.search import get_secure_retriever
from backend.models.models import llm
from backend.state import graph_db
from backend.components.constraints import (
    format_docs,
    SUMMARIZER_PROMPT,
    GRADING_PROMPT,
    REWRITING_PROMPT,
    FORMATTER_PROMPT
)
from backend.components.time_storage import add_time_entry, TimeEntryCreate
from backend.state.graph_state import GraphState, route_after_grading
from langgraph.graph import StateGraph, START, END

logger = logging.getLogger("SASS Logger")

# ============================================================
# COORDINATOR_NODE (sync)
# ============================================================

def coordinator_node(state: GraphState) -> GraphState:
    last_msg = state["messages"][-1].content.lower().strip()
    logger.info("--- COORDINATOR NODE START ---")
    logger.info(f"User message: {last_msg}")
    # Run reasoner first
    state = reasoner_node(state)
    intent = classify_intent(last_msg, state.get("attachment_summaries", []))
    logger.info(f"Intent classified as: {intent}")
    plan = build_agent_plan(intent, state)
    logger.info(f"Agent plan created: {plan}")
    state["coordinator_intent"] = intent
    state["coordinator_plan"] = plan["agents"]
    logger.info(f"Stored plan list: {state['coordinator_plan']}")
    return state

def coordinator_router(state: GraphState) -> str:
    """
    Reads coordinator_plan and returns the next node to execute.
    Pops one agent at a time until the plan is empty.
    """
    logger.info("Preparing next step.")
    logger.info("--- COORDINATOR NODE END ---")
    plan = state.get("coordinator_plan", [])
    intent = state.get("coordinator_intent", [])
    # If no plan, default to conversational
    if not plan:
        return "conversational_node"
    # Pop the next agent from the plan
    next_agent = plan.pop(0)
    state["coordinator_plan"] = plan
    state["last_intent"] = intent
    if next_agent == "paapp":
        return "paapp_node"  # save updated plan
    # Map agent names → actual node names you currently have
    mapping = {
        "retriever": "retrieve_node",
        "reasoner": "reasoner_node",      
        "conversational": "conversational_node",
        "formatter": "formatter_node",          
        "summarizer": "summarizer_node",        
        "paapp": "paapp_node",        
        "workflow": "conversational_node",     # TEMP until workflow_node exists
        "tool": "conversational_node",         # TEMP until tool_node exists
        "memory": "memory_node",       
    }
    # Return the mapped node, or fallback to conversational
    logger.info(f"sending request to {next_agent}")
    return mapping.get(next_agent, "conversational_node")

def classify_intent(message: str, attachments) -> str:
    msg = message.lower()
    if "plan my day" in msg or "schedule" in msg:
        return "task_paapp"
    if "summarize" in msg or "tl;dr" in msg:
        return "summarize"
    if any(w in msg for w in ["find", "lookup", "policy", "docs", "search"]):
        return "retrieve"
    if any(w in msg for w in ["calculate", "web search", "google", "api"]):
        return "tool"
    if any(w in msg for w in ["workflow", "ticket", "request form"]):
        return "workflow"
    if any(w in msg for w in ["remember", "recall", "what did i ask before"]):
        return "memory"
    if any(w in msg for w in ["bullet", "report", "format this"]):
        return "format"
    return "conversational"

def build_agent_plan(intent, state):
    flags = state.get("reasoner_flags", {})
    agents = []
    # FOLLOW-UP OVERRIDE
    if flags.get("follow_up_intent"):
        intent = state.get("last_intent", intent)
        logger.info(f"[Coordinator] Follow-up detected. Reusing last intent: {intent}")
    if flags.get("needs_memory"):
        agents.append("memory")
    if flags.get("needs_retrieval"):
        agents.append("retriever")
    if flags.get("needs_rewrite"):
        agents.append("rewriter")
    if flags.get("needs_summary"):
        agents.append("summarizer")
    if flags.get("needs_formatting"):
        agents.append("formatter")
    if flags.get("needs_paapp"):
        agents.append("paapp")
    if flags.get("needs_conversation"):
        agents.append("conversational")
    # Always end with formatter
    if "formatter" not in agents:
        agents.append("formatter")
    # Store last intent for future follow-ups
    state["last_intent"] = intent
    return {"agents": agents, "skip": []}

def apply_conditional_skips(plan: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    agents = plan["agents"]
    skip = plan["skip"]
    # Example: if no retrieval context configured, drop retriever
    if "retriever" in agents and not state.get("rag_enabled", True):
        agents.remove("retriever")
        skip.append("retriever")
    # Example: if query already clean, drop reasoner
    if "reasoner" in agents and state.get("query_is_clean", False):
        agents.remove("reasoner")
        skip.append("reasoner")
    plan["agents"] = agents
    plan["skip"] = skip
    return plan

# ============================================================
# REASONER NODE (sync)
# ============================================================

def reasoner_node(state: GraphState) -> GraphState:
    msg = state["messages"][-1].content.lower().strip()
    attachments = state.get("attachment_summaries", [])
    history = state.get("messages", [])
    logger.info("--- REASONER NODE START ---")
    logger.info(f"[Reasoner] Message: {msg}")

    # --- FOLLOW-UP DETECTION ---
    follow_up_phrases = [
        "yes", "yeah", "yep", "sure", "do it", "go ahead",
        "nope", "save it", "that one", "please do", "ok", "okay",
        "confirm", "confirm it", "make it happen", "do that",
        "that's fine", "sounds good", "alright", "fine"
    ]
    is_follow_up = any(p in msg for p in follow_up_phrases)

    # --- NORMAL INTENT FLAGS ---
    explicit_memory = any(
        phrase in msg for phrase in [
            "remember that", "remember this", "remember me",
            "save this", "store this", "keep this", "don't forget",
            "my preference is", "i prefer", "track this", "log this",
            "add to memory"
        ]
    )

    is_knowledge_query = any(
        phrase in msg for phrase in [
            "what is", "what are", "who is", "who are",
            "define", "meaning of", "explain", "tell me about"
        ]
    )

    needs_memory = explicit_memory

    # --- CALENDAR DETECTION ---
    calendar_keywords = [
        "calendar", "google calendar", "create an event",
        "create event", "calendar event", "add an event",
        "make an event", "put this on my calendar"
    ]
    needs_paapp = any(kw in msg for kw in calendar_keywords)

    # --- TIME TRACKING DETECTION (NEW + IMPORTANT) ---
    time_tracking_keywords = [
        "log time",
        "record time",
        "track time",
        "time tracking",
        "log 1 hour",
        "log one hour",
        "log 30 minutes",
        "add another hour",
        "log more time",
        "job apps",
        "job applications",
        "coding",
        "work",
        "today"
    ]

    if any(kw in msg for kw in time_tracking_keywords):
        needs_paapp = True

    # --- RETRIEVAL DETECTION ---
    needs_retrieval = (
        is_knowledge_query
        or len(attachments) > 0
        or any(word in msg for word in ["find", "lookup", "search", "policy", "document", "docs"])
    )

    explicit_rewrite = any(word in msg for word in [
        "rewrite", "reword", "improve wording", "optimize query", "rewrite this"
    ])

    needs_summary = any(word in msg for word in ["summarize", "tl;dr", "shorten"])
    needs_formatting = any(word in msg for word in ["bullet", "format", "report", "clean up", "structure"])
    needs_conversation = not (needs_retrieval or explicit_rewrite or needs_summary or needs_formatting or needs_paapp)

    # --- BUILD FLAGS ---
    flags = {
        "needs_retrieval": needs_retrieval,
        "needs_rewrite": explicit_rewrite,
        "needs_summary": needs_summary,
        "needs_formatting": needs_formatting,
        "needs_conversation": needs_conversation,
        "needs_memory": needs_memory,
        "needs_paapp": needs_paapp,
        "follow_up_intent": is_follow_up
    }

    logger.info(f"[Reasoner] Flags: {flags}")
    state["reasoner_flags"] = flags
    logger.info("--- REASONER NODE END ---")
    return state

# ============================================================
# MEMORY NODE
# ============================================================

def memory_node(state: GraphState) -> dict:
    logger.info("--- MEMORY NODE CALLED ---")
    user_msg = state["messages"][-1].content.strip()
    # Extract the memory content
    lower_msg = user_msg.lower()
    # Detect explicit memory commands
    triggers = [
           "remember that",
            "remember this",
            "remember me",
            "remember my",
            "can you remember",
            "save this",
            "store this",
            "keep this",
            "don't forget",
            "my preference is",
            "i prefer",
            "track this",
            "log this",
            "add to memory"
    ]
    extracted = user_msg
    for t in triggers:
        if t in lower_msg:
            extracted = user_msg.lower().split(t, 1)[-1].strip()
            break
    # If extraction fails, fallback to full message
    if not extracted:
        extracted = user_msg
    logger.info(f"Extracted memory content: {extracted}")
    # Store memory in state (later you can move this to a DB)
    memory_store = state.get("memory_store", [])
    memory_store.append(extracted)
    state["memory_store"] = memory_store
    logger.info(f"Updated memory store: {memory_store}")
    # Build confirmation message
    confirmation = f"I’ve saved that preference: {extracted}"
    # Pass to formatter
    state["raw_generation"] = confirmation
    state["content_to_format"] = confirmation
    return state

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
# SUMMARIZER NODE
# ============================================================

def summarizer_node(state: GraphState) -> GraphState:
    logger.info("--- SUMMARIZER NODE CALLED ---")
    docs = state.get("documents", [])
    user_msg = state["messages"][-1].content
    if not docs:
        logger.info("[Summarizer] No documents found in state; skipping summarization.")
        state["summary"] = None
        logger.info("--- SUMMARIZER NODE END ---")
        return state
    # Build a concise context block
    context_chunks = []
    for i, doc in enumerate(docs, start=1):
        page = getattr(doc.metadata, "page", doc.metadata.get("page", "N/A")) if hasattr(doc, "metadata") else "N/A"
        text = doc.page_content if hasattr(doc, "page_content") else str(doc)
        context_chunks.append(f"--- DOCUMENT {i} (Page {page}) ---\n{text}")
    context_block = "\n\n".join(context_chunks)
    prompt = SUMMARIZER_PROMPT
    logger.info("Sending summarization prompt to LLM.")
    # Assuming you have a `llm` or `model` in scope
    summary = llm.invoke(prompt)  # adjust to your LLM interface
    # If your LLM returns an object, extract `.content` or similar
    if hasattr(summary, "content"):
        summary_text = summary.content
    else:
        summary_text = str(summary)
    logger.info("Summary generated.")
    state["summary"] = summary_text
    logger.info("--- SUMMARIZER NODE END ---")
    return state

# ============================================================
# FORMATTER NODE
# ============================================================

def formatter_node(state: GraphState) -> dict:
    logger.info("--- FORMATTER NODE CALLED ---")
    # 1. Choose the correct content source
    content_to_format = state.get("content_to_format")
    user_msg = state["messages"][-1].content
    lower_msg = user_msg.lower()
    # Fallback if memory/summarizer/generator didn't set content
    if not content_to_format:
        content_to_format = user_msg
    logger.info(f"Reformatting content: {content_to_format}")
    # 2. Implicit formatting signals
    is_long = len(content_to_format.split()) > 120
    is_multi_section = any(word in lower_msg for word in ["explain", "tell me about", "overview", "details"])
    is_list_like = any(word in lower_msg for word in ["types", "kinds", "examples", "steps"])
    is_policy_like = any(word in lower_msg for word in ["policy", "rules", "requirements"])
    is_character_lore = any(word in lower_msg for word in ["race", "lore", "history", "origin"])
    if is_policy_like or is_multi_section:
        format_style = "sections"
    elif is_list_like:
        format_style = "bullets"
    elif is_character_lore:
        format_style = "sections"
    elif is_long:
        format_style = "summary"
    else:
        format_style = "clean"
    # 3. Build the prompt
    prompt = FORMATTER_PROMPT.format(
        format_style=format_style,
        content_to_format=content_to_format
    )
    # 4. Save formatted output
    formatted = llm.invoke(prompt)
    formatted_text = formatted.content if hasattr(formatted, "content") else str(formatted)
    state["formatted_output"] = formatted_text
    return state


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
# PAAPP NODE (sync)
# ============================================================

def paapp_node(state: GraphState) -> GraphState:
    msg = state["messages"][-1].content
    username = state.get("username", "default_user")

    try:
        response = call_paapp_chat(username, msg)
    except Exception as e:
        fallback = f"PAAPP communication error: {str(e)}"
        state["raw_generation"] = fallback
        state["content_to_format"] = fallback
        return state

    intent = response.get("intent")

    # If the agent identified a log_time tool call
    if intent and intent.get("tool") == "log_time":
        try:
            entry_payload = TimeEntryCreate(
                username=username,
                activity=str(intent.get("activity", "Unknown Activity")),
                minutes=int(intent.get("minutes", 0)),
                date_iso=str(intent.get("date_iso")),
                notes=str(intent.get("notes", "No description provided")), # Capture the description
                hours=float(intent.get("minutes", 0) / 60) # Helper hours field
            )
            
            add_time_entry(entry_payload)
            logger.info(f"[PAAPP] Successfully logged time locally for {username}")
            
        except Exception as e:
            logger.error(f"[PAAPP] Direct time log failed: {e}")
            state["raw_generation"] = f"Time log failed: {str(e)}"
            return state
        except Exception as e:
            logger.error(f"[PAAPP] Unexpected time log error: {e}")
            state["raw_generation"] = f"Time log error: {str(e)}"
            return state

    message = response.get("message", "PAAPP returned no message.")
    state["raw_generation"] = message
    state["content_to_format"] = message
    return state


def call_paapp_chat(username: str, question: str) -> dict:
    url = f"{PAAPP_BASE_URL}/api/headless-chat"
    r = requests.post(
    url,
    headers={"x-saapp": "true"},
    json={
        "username": username,
        "question": question
    }
)
    r.raise_for_status()
    return r.json()

# ============================================================
# WORKFLOW ASSEMBLY & COMPILATION
# ============================================================

def create_workflow(vector_store):
    workflow = StateGraph(GraphState)
    def retrieve_node_with_store(state):
        return retrieve_node(state, vector_store)
    workflow.add_node("memory_node", memory_node)
    workflow.add_node("retrieve_node", retrieve_node_with_store)
    workflow.add_node("grade_documents_node", grading_node)
    workflow.add_node("rewrite_query_node", rewrite_query_node)
    workflow.add_node("generate_node", generate_node)
    workflow.add_node("conversational_node", conversational_node)
    workflow.add_node("coordinator_node", coordinator_node)
    workflow.add_node("summarizer_node", summarizer_node)
    workflow.add_node("formatter_node", formatter_node)
    workflow.add_node("paapp_node", paapp_node)
    workflow.add_edge(START, "coordinator_node")
    workflow.add_conditional_edges(
    "coordinator_node",
        coordinator_router,  
        {
            "memory_node": "memory_node",
            "retrieve_node": "retrieve_node",
            "rewrite_query_node": "rewrite_query_node",
            "conversational_node": "conversational_node",
            "generate_node": "generate_node",
            "summarizer_node": "summarizer_node",
            "formatter_node": "formatter_node",
            "paapp_node": "paapp_node"
        }
    )
    workflow.add_edge("paapp_node", "formatter_node")
    workflow.add_edge("memory_node", "formatter_node")
    workflow.add_edge("summarizer_node", "formatter_node")
    workflow.add_edge("formatter_node", "generate_node")
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
    workflow.add_edge("conversational_node", "formatter_node")
    return workflow.compile()