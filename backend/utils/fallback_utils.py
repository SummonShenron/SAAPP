from backend.services.agent_workflow import rewrite_query_node, retrieve_node, grading_node
from backend.models.models import llm
import json
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from backend.models.attachment import Attachment
import asyncio
import logging
from backend.components.constraints import get_system_prompt, format_docs
from backend.utils.app_utils import format_history_as_text
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

    preserved_messages = messages_state

    state = { **state, **rewrite_query_node(state) }
    state["messages"] = preserved_messages

    rewritten_question = preserved_messages[-1].content
    state["original_question"] = rewritten_question

    state = { **state, **retrieve_node(state, vector_store) }
    state["messages"] = preserved_messages

    state = { **state, **grading_node(state) }
    state["messages"] = preserved_messages


    if state.get("relevance_grade") != "yes":
        yield f"data: {json.dumps({'event': 'final_generation', 'text': 'I cannot find the answer in the provided knowledge base.'})}\n\n"
        return

    formatted_docs = format_docs(state.get("documents", []))
    history_transcript = format_history_as_text(chat_sessions[username])
    instructions = get_system_prompt(username, ", ".join(state.get("target_scope", [])))

    prompt = instructions.format(
        context=formatted_docs,
        history=history_transcript,
        question=rewritten_question,
    )

    full_response = ""
    async for chunk in llm.astream(prompt):
        token = chunk if isinstance(chunk, str) else getattr(chunk, "content", None) or str(chunk)
        if not token:
            continue
        full_response += token
        yield f"data: {json.dumps({'event': 'token', 'text': token})}\n\n"
        await asyncio.sleep(0)

    yield f"data: {json.dumps({'event': 'final_generation', 'text': full_response})}\n\n"

    chat_sessions[username].append(HumanMessage(content=rewritten_question))
    chat_sessions[username].append(AIMessage(content=full_response))

    save_chat_history()
