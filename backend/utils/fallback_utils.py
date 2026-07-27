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

    preserved_messages = list(messages_state)

    if not preserved_messages:
        logger.warning("rewrite_fallback: no preserved messages; aborting fallback.")
        yield f"data: {json.dumps({'event': 'final_generation', 'text': 'No conversation context available.'})}\n\n"
        return

    # Extract original user query before rewrite
    original_user_query = preserved_messages[-1].content if preserved_messages else state.get("question", "")

    # 1. Run rewrite node defensively and extract the actual rewritten string
    try:
        rewrite_res = rewrite_query_node(state)
        state = { **state, **rewrite_res }
        rewritten_question = state.get("question", original_user_query)
    except Exception as e:
        logger.exception("rewrite_query_node raised; preserving state. %s", e)
        rewritten_question = original_user_query

    rewritten_question = ensure_str(rewritten_question)
    state["original_question"] = rewritten_question

    # 2. Retrieve documents
    try:
        state = { **state, **(await retrieve_node(state, vector_store)) }
    except Exception as e:
        logger.exception("retrieve_node failed: %s", e)

    # 3. Grade retrieved docs
    try:
        state = { **state, **(await grading_node(state)) }
    except Exception as e:
        logger.exception("grading_node failed: %s", e)

    # 4. Handle failed grading — MUST save pending_action and session history before exiting!
    if state.get("relevance_grade") != "yes":
        ask_web_search_msg = (
            "I couldn't find an answer to your question in the knowledge base. "
            "Would you like me to break RAG restrictions and search the web to answer this?"
        )

        # 1. Update session state & pending action FIRST
        chat_sessions.setdefault(username, [])
        chat_sessions[username].append(AIMessage(content=ask_web_search_msg))

        state["pending_action"] = {
            "type": "web_search",
            "status": "hitl_approval_required",
            "original_query": original_user_query
        }

        # 2. Persist state to DB BEFORE streaming the prompt to the user
        try:
            save_chat_history()
        except Exception:
            logger.exception("save_chat_history failed during fallback HITL prompt")

        # 3. Stream to user after DB persist is guaranteed
        yield f"data: {json.dumps({'event': 'final_generation', 'text': ask_web_search_msg})}\n\n"
        return

    # Prepare prompt pieces for successful generation
    formatted_docs = ensure_str(format_docs(state.get("documents", [])))
    history_transcript = ensure_str(format_history_as_text(chat_sessions.get(username, [])))
    instructions = ensure_str(get_system_prompt(username, ", ".join(state.get("target_scope", []) or [])))

    prompt = instructions.format(
        context=formatted_docs,
        history=history_transcript,
        question=rewritten_question,
    )

    full_response = ""
    try:
        async for chunk in llm.astream(prompt):
            token = ensure_str(chunk if isinstance(chunk, str) else getattr(chunk, "content", None) or chunk)
            if not token:
                continue

            full_response += token
            yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
            await asyncio.sleep(0)
    except Exception as e:
        logger.exception("Error in token streaming: %s", e)
        yield f"data: {json.dumps({'event': 'final_generation', 'text': 'An error occurred while generating the response.'})}\n\n"
        return

    yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"

    # Append to session history and persist
    chat_sessions.setdefault(username, [])
    chat_sessions[username].append(HumanMessage(content=rewritten_question))
    chat_sessions[username].append(AIMessage(content=full_response))

    # Clear pending_action on success
    state["pending_action"] = None

    try:
        save_chat_history()
    except Exception:
        logger.exception("save_chat_history failed")