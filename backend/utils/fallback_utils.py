from backend.services.agent_workflow import rewrite_query_node, retrieve_node, grading_node
from backend.models.models import llm
import json
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from backend.models.attachment import Attachment
import asyncio
import logging
from backend.components.constraints import get_system_prompt, format_docs
from backend.utils.app_utils import format_history_as_text
from backend.utils.normalize_utils import ensure_str
logger = logging.getLogger("SASS Logger")

async def rewrite_fallback(
    vector_store,
    state,
    username,
    messages_state,
    chat_sessions,
    save_chat_history
):
    logger.info("Executing rewrite fallback...")

    # Preserve the original messages list (do not mutate caller's list)
    preserved_messages = list(messages_state)

    # Defensive: ensure messages exist and last message is a string
    if not preserved_messages:
        logger.warning("rewrite_fallback: no preserved messages; aborting fallback.")
        yield f"data: {json.dumps({'event': 'final_generation', 'text': 'No conversation context available.'})}\n\n"
        return

    # Run rewrite node defensively and merge results into a copy of state
    try:
        state = { **state, **rewrite_query_node(state) }
    except Exception as e:
        logger.exception("rewrite_query_node raised; preserving state. %s", e)

    # Restore preserved messages into state to avoid node side-effects
    state["messages"] = preserved_messages

    # Ensure the rewritten question is a string
    try:
        rewritten_question = preserved_messages[-1].content
    except Exception:
        rewritten_question = state.get("question", "")
    rewritten_question = ensure_str(rewritten_question)
    state["original_question"] = rewritten_question

    # Retrieve documents (defensive)
    try:
        state = { **state, **retrieve_node(state, vector_store) }
    except Exception as e:
        logger.exception("retrieve_node failed: %s", e)
    state["messages"] = preserved_messages

    # Grade retrieved docs (defensive)
    try:
        state = { **state, **grading_node(state) }
    except Exception as e:
        logger.exception("grading_node failed: %s", e)
    state["messages"] = preserved_messages

    # If re-retrieval/rewrite still fails relevance grading:
    if state.get("relevance_grade") != "yes":
        ask_web_search_msg = (
            "I couldn't find an answer to your question in the knowledge base. "
            "Would you like me to break RAG restrictions and search the web to answer this?"
        )
        yield f"data: {json.dumps({'event': 'final_generation', 'text': ask_web_search_msg})}\n\n"
        return

    # Prepare prompt pieces defensively
    formatted_docs = ensure_str(format_docs(state.get("documents", [])))
    history_transcript = ensure_str(format_history_as_text(chat_sessions.get(username, [])))
    instructions = ensure_str(get_system_prompt(username, ", ".join(state.get("target_scope", []) or [])))

    prompt = instructions.format(
        context=formatted_docs,
        history=history_transcript,
        question=rewritten_question,
    )

    # Stream tokens from the LLM, normalizing each chunk
    full_response = ""
    try:
        async for chunk in llm.astream(prompt):
            # Normalize chunk to string
            token = ensure_str(chunk if isinstance(chunk, str) else getattr(chunk, "content", None) or chunk)
            if not token:
                continue

            # Log once if chunk was non-string originally
            if not isinstance(chunk, str):
                logger.debug("rewrite_fallback: normalized non-str chunk of type %s to string length %d", type(chunk), len(token))

            full_response += token
            yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
            await asyncio.sleep(0)
    except Exception as e:
        logger.exception("Error in token streaming: %s", e)
        # Provide a graceful final message on error
        yield f"data: {json.dumps({'event': 'final_generation', 'text': 'An error occurred while generating the response.'})}\n\n"
        return

    # Final generation event
    yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"

    # Append to session history defensively
    chat_sessions.setdefault(username, [])
    chat_sessions[username].append(HumanMessage(content=rewritten_question))
    chat_sessions[username].append(AIMessage(content=full_response))

    # Persist chat history
    try:
        save_chat_history()
    except Exception:
        logger.exception("save_chat_history failed")

