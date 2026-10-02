"""Observed failure: for "add hello world to the readme" the model called the right tool but labeled
the step {"action": "final", "tool_action": "propose_local_edits", ...} with no answer text. The loop
honored "final", discarded the tool call, and ended the turn with "I wasn't able to find a
conclusive answer." A "final" with no answer but a tool call must run as a query instead."""
import asyncio
import functools
import json
from types import SimpleNamespace

from backend.services import agent_workflow as aw

GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _resp(**payload):
    return SimpleNamespace(content=json.dumps(payload))


async def _run(responses):
    prompts, acted = [], []

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _resp(grounded=True, unsupported_claims=[])
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    async def act(decision):
        acted.append(decision)
        return "Proposed 1 edit(s) across 1 file(s): README.md."

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="add hello world to the readme", schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}", act=act,
            max_iterations=5, node_name="test_node", initial_attempts=[],
        )
    finally:
        aw.lite_llm.ainvoke = orig
    return result, prompts, acted


@run_async
async def test_final_with_no_answer_but_a_tool_call_runs_the_tool_as_a_query():
    responses = [
        _resp(action="final", purpose="propose it", tool_action="propose_local_edits",
              args={"edits": [{"path": "README.md", "content": "x"}]}, show_work=True),
        _resp(action="final", answer="I proposed the edit; press Apply.", show_work=False),
    ]

    result, prompts, acted = await _run(responses)

    assert [d["tool_action"] for d in acted] == ["propose_local_edits"]
    assert result["final_answer"] == "I proposed the edit; press Apply."
    assert len(prompts) == 2


@run_async
async def test_a_normal_final_with_an_answer_is_untouched():
    result, prompts, acted = await _run([_resp(action="final", answer="Here you go.", show_work=False)])

    assert acted == []
    assert result["final_answer"] == "Here you go."


@run_async
async def test_final_with_an_answer_is_not_reinterpreted_even_if_it_also_names_a_tool():
    result, prompts, acted = await _run([
        _resp(action="final", answer="Done explaining.", tool_action="read_repo_file", args={}, show_work=False),
    ])

    assert acted == []
    assert result["final_answer"] == "Done explaining."
