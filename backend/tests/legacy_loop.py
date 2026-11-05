"""Test helpers for the tool agent's loop (2026-11-05: the old ReAct loop was deleted; the native loop is the only one).

- `react_style(fake)`: a test that fakes the loop patches `aw.native_loop.run` with it; `tool_agent_node` calls it with the native loop's arguments, and `fake` receives those plus
  the old names it reads: `prompt_template` (the tools this turn offers, as menu lines, "- name — description"), `max_iterations` (the step cap), `schema` (what the turn is reading
  from) and `node_name`.
- `ScriptedDecisionLLM`: the tool agent's model in a test that scripts it the old way, through `aw.lite_llm.ainvoke` (a JSON decision per call). `observed()` is what the tools
  returned to it, and `CALLS` the tools it asked for: where the old tests read the steps footer of the answer.
"""
import json

NEWLINE = chr(10)

RESULTS = []   # what the tools returned to the model this test, in order (the text of each tool message it was sent)
CALLS = []     # the names of the tools the scripted model asked for, in order


def reset():
    RESULTS.clear()
    CALLS.clear()


def observed():
    """Everything the tools returned to the model so far this test, as one text."""
    return NEWLINE.join(RESULTS)


def menu_of(tools):
    return NEWLINE.join(f"- {tool.get('name')} — {tool.get('description', '')}" for tool in (tools or []))


def react_style(fake):
    async def run(**kwargs):
        legacy = dict(kwargs)
        legacy.setdefault("prompt_template", menu_of(kwargs.get("tools")))
        legacy.setdefault("max_iterations", kwargs.get("max_steps"))
        legacy.setdefault("schema", kwargs.get("environment_note", ""))
        legacy.setdefault("node_name", "tool_agent_node")
        return await fake(**legacy)

    return run


class ScriptedDecisionLLM:
    """Each call reads the next response a test put on aw.lite_llm.ainvoke (a JSON decision: {"action": "query", "tool_action", "args"} or "queries": [...], {"action": "final",
    "answer"}, or {"action": "clarify", "question"}) and answers the native loop with the matching tool calls or text."""
    caches_prompt = False

    def __init__(self):
        self._count = 0
        self._seen_results = 0

    async def ainvoke_tools(self, messages, tools):
        from langchain_core.messages import AIMessage, ToolMessage
        from backend.services import agent_workflow as aw

        results = [str(m.content) for m in messages if isinstance(m, ToolMessage)]
        for text in results[self._seen_results:]:
            RESULTS.append(text)
        self._seen_results = len(results)
        # The prompt a scripted model "sees": the conversation, then the tools this turn offers as menu lines, so a test can check what was offered.
        prompt = NEWLINE.join(str(getattr(m, "content", "")) for m in messages) + NEWLINE + "AVAILABLE ACTIONS THIS TURN:" + NEWLINE + menu_of(tools)
        raw = await aw.lite_llm.ainvoke(prompt)
        text = getattr(raw, "content", raw)
        try:
            decision = json.loads(text) if isinstance(text, str) else dict(text)
        except (TypeError, ValueError):
            return AIMessage(content=str(text))
        if not isinstance(decision, dict):
            return AIMessage(content=str(text))
        action = decision.get("action")
        if action == "final":
            return AIMessage(content=str(decision.get("answer") or ""))
        if action == "clarify":
            return AIMessage(content=str(decision.get("question") or ""))
        wanted = decision.get("queries") or ([decision] if decision.get("tool_action") else [])
        calls = []
        for item in wanted:
            self._count += 1
            CALLS.append(item.get("tool_action"))
            calls.append({"name": item.get("tool_action"), "args": item.get("args") or {}, "id": f"scripted_{self._count}", "type": "tool_call"})
        return AIMessage(content="", tool_calls=calls)
