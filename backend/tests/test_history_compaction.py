import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import agent_utils as au

RECEIPT = "\n\n**Step 1 — Run read_repo_file:**\n```\nread_repo_file(path=x)\n```\n**Result:**\n```\n" + ("def helper(rows):\n    return rows\n" * 80) + "```"


def test_a_short_message_goes_in_unchanged():
    assert au.compact_history_message("  Use the app factory.  ", 100) == "Use the app factory."


def test_the_tool_call_transcript_an_answer_ends_with_is_dropped():
    answer = "The status is set in overdue.py." + RECEIPT
    assert au.compact_history_message(answer, 5000) == "The status is set in overdue.py."


def test_a_long_message_keeps_its_start_and_its_end_with_a_mark_between():
    text = "START " + ("middle " * 600) + "THE CONCLUSION. Want me to apply it?"
    out = au.compact_history_message(text, 800)
    assert len(out) <= 800 + 5 and out.startswith("START") and out.endswith("Want me to apply it?")
    assert "[... shortened ...]" in out and "THE CONCLUSION" in out


def test_the_last_message_gets_more_room_than_the_others():
    long = "word " * 1000
    lines = au.format_history([HumanMessage(content=long), AIMessage(content=long)], limit=500, last_limit=2000).split("\nai: ")
    assert len(lines[0]) < 600 and 1500 < len(lines[1]) <= 2010


def test_formatting_keeps_the_type_prefix_and_gives_nothing_for_no_messages():
    assert au.format_history([HumanMessage(content="hi"), AIMessage(content="hello")]) == "human: hi\nai: hello"
    assert au.format_history([]) == "" and au.format_history(None) == ""


def test_a_list_valued_content_does_not_break_it():
    assert "text" in au.format_history([SimpleNamespace(type="human", content=[{"type": "text", "text": "hello"}])])


def test_the_limits_are_tunable_and_zero_means_no_cap():
    assert au.compact_history_message("x" * 9000, 0) == "x" * 9000


def test_the_reasoner_and_the_tool_agent_prompts_both_get_compacted_history(monkeypatch):
    from backend.evals import behavior_eval as be
    from backend.services import agent_workflow as aw

    seen = []

    async def spy(prompt):
        seen.append(prompt)
        return SimpleNamespace(content=json.dumps({"action": "final", "answer": "ok"}))

    history = [HumanMessage(content="earlier question"), AIMessage(content="ANSWER-START " + ("filler " * 900) + "ANSWER-END" + RECEIPT)]
    asyncio.run(be.run_tool_agent(spy, "and what about now?", repo=True, engine="react", history=history))
    prompt = seen[0]
    assert "ANSWER-START" in prompt and "ANSWER-END" in prompt and "[... shortened ...]" in prompt
    assert "**Step 1" not in prompt and prompt.count("def helper(rows)") == 0  # the transcript is not in the prompt
    assert prompt.count("filler") < 900  # and the long body is cut down

    seen.clear()

    async def reasoner_spy(prompt):
        seen.append(prompt)
        return SimpleNamespace(content=json.dumps({"needs_conversation": True}))

    monkeypatch.setattr(aw.lite_llm, "ainvoke", reasoner_spy)
    asyncio.run(aw.reasoner_node({"messages": [*history, HumanMessage(content="and what about now?")]}))
    assert "**Step 1" not in seen[0] and seen[0].count("filler") < 900 and "ANSWER-END" in seen[0]
