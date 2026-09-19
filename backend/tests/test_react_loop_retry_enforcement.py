import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.services import agent_workflow as aw


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


@run_async
async def test_premature_final_after_error_is_rejected_once_then_accepted():
    """The exact scenario this guards: the model's first action fails, it still has steps left,
    and it tries to conclude right away instead of retrying. The loop must reject that one
    "final" and force a real extra step — but only once, so a second genuine failure doesn't
    loop forever."""
    captured_prompts = []

    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="final", answer="Giving up despite having steps left."),
        _llm_response(action="final", answer="Okay, here is my honest final answer."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: could not fetch x.py (404)"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    # Three LLM calls: the failing query, the rejected premature "final", then the accepted one.
    assert len(captured_prompts) == 3
    assert "Okay, here is my honest final answer." == result["final_answer"]
    assert len(result["attempts"]) == 1  # only the one real (failed) action was ever recorded
    assert "retry it" in captured_prompts[1]  # the nudge shown right before the rejected "final"
    # The tool was never actually retried in this scenario, so the reminder keeps showing — but
    # the HARD rejection only fires once: the second "final" is accepted despite the nudge still
    # being present, rather than forcing a third, unbounded round.
    assert "retry it" in captured_prompts[2]


@run_async
async def test_diagnostic_detour_does_not_hide_an_unretried_failure():
    """Real failure mode this guards: read_repo_file 404s, list_repo_tree (a different action,
    taken to diagnose the 404) then SUCCEEDS, and the model tries to conclude right after —
    checking only 'did the last action fail' would miss this, since the last action (the list)
    genuinely succeeded even though the thing the user actually asked for (the file's contents)
    was never retrieved."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "agent_workflow.py"}),
        _llm_response(action="query", purpose="List the tree to find the right path", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="Giving up despite the tree confirming the path."),
        _llm_response(action="final", answer="Okay, retried and here's the honest answer."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    def act(decision):
        tool_action = decision.get("tool_action")
        if tool_action == "read_repo_file":
            return "ERROR: could not fetch agent_workflow.py (404)"
        if tool_action == "list_repo_tree":
            return "backend/services/agent_workflow.py\n... (real tree contents)"
        return "unexpected"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="read agent_workflow.py and summarize it",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    # 4 calls: failing read, successful list, rejected premature "final", accepted "final".
    assert len(captured_prompts) == 4
    assert result["final_answer"] == "Okay, retried and here's the honest answer."
    assert len(result["attempts"]) == 2  # the rejected "final" never got recorded as an attempt
    assert "read_repo_file" in captured_prompts[2]  # the nudge names the specific unretried tool
    assert "does not count as retrying it" in captured_prompts[2]


@run_async
async def test_final_without_any_prior_error_is_accepted_immediately():
    async def act(decision):
        return "some real result"

    call_count = {"n": 0}

    async def fake_ainvoke(prompt):
        call_count["n"] += 1
        return _llm_response(action="final", answer="Straightforward honest answer.")

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert call_count["n"] == 1
    assert result["final_answer"] == "Straightforward honest answer."


@run_async
async def test_second_consecutive_error_does_not_trigger_a_second_nudge():
    """A genuinely doomed action (still failing after the forced retry) must still get an
    honest 'final' on the next try rather than the loop nudging forever."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="query", purpose="Retry with corrected path", tool_action="read_file", args={"path": "y.py"}),
        _llm_response(action="final", answer="Still couldn't verify it after retrying."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: still not found"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Still couldn't verify it after retrying."
    assert len(result["attempts"]) == 2
    # The nudge appears exactly once (before the second, still-failing query), never again
    # before the final honest answer.
    nudge_count = sum(1 for p in captured_prompts if "retry it" in p)
    assert nudge_count == 1
