"""Observed with a connected local folder: asked to change a file, the model answered "I am only able
to read and search through your repository files... I cannot write the physical changes" and described
the edit in prose, while propose_local_edits sat in its action menu. The capability-denial backstop
must reject that once (only when the tool is actually offered) and let the model use the tool."""
import asyncio
import functools
import json
from types import SimpleNamespace

from backend.services import agent_workflow as aw
from backend.utils.agent_utils import _CAPABILITY_DENIAL_RE

GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"
OBSERVED_DENIAL = (
    "I would love to handle that for you. Since I am only able to read and search through your "
    "repository files from here, I cannot write the physical changes directly to your local Chat.tsx "
    "file. However, I can show you exactly what needs to be changed so you can apply it easily."
)


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _resp(**payload):
    return SimpleNamespace(content=json.dumps(payload))


async def _run(responses, menu_has_edit_tool):
    prompts, acted = [], []

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _resp(grounded=True, unsupported_claims=[])
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    async def act(decision):
        acted.append(decision["tool_action"])
        return "Proposed 1 edit(s) across 1 file(s): local/src/pages/Chat.tsx. Nothing has been changed yet."

    template = "{question} | {schema} | {attempts}"
    if menu_has_edit_tool:
        template += " | - propose_local_edits — args: summary, edits"
    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="change the trace header to say Tracing request", schema="repo=x",
            prompt_template=template, act=act, max_iterations=6, node_name="test_node",
            initial_attempts=[], capability_denial_watchlist=aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST,
        )
    finally:
        aw.lite_llm.ainvoke = orig
    return result, prompts, acted


def test_the_observed_denial_wording_is_recognized_as_a_denial():
    assert _CAPABILITY_DENIAL_RE.search(OBSERVED_DENIAL)
    for phrase in ("I cannot edit that file.", "I am unable to modify your folder.", "This is read-only for me."):
        assert _CAPABILITY_DENIAL_RE.search(phrase), phrase


@run_async
async def test_denying_edit_ability_while_the_edit_tool_is_offered_is_rejected_once_then_the_tool_is_used():
    responses = [
        _resp(action="final", answer=OBSERVED_DENIAL, show_work=False),
        _resp(action="query", purpose="propose it", tool_action="propose_local_edits",
              args={"edits": [{"path": "local/src/pages/Chat.tsx", "content": "x"}]}),
        _resp(action="final", answer="I proposed the edit; press Apply.", show_work=False),
    ]

    result, prompts, acted = await _run(responses, menu_has_edit_tool=True)

    assert acted == ["propose_local_edits"]
    assert "'- propose_local_edits —' is listed in AVAILABLE ACTIONS" in prompts[1]
    assert result["final_answer"] == "I proposed the edit; press Apply."


@run_async
async def test_the_same_denial_is_accepted_when_the_edit_tool_is_not_offered():
    """No connected folder -> no propose_local_edits in the menu -> "I can only read" is simply true."""
    result, prompts, acted = await _run([_resp(action="final", answer=OBSERVED_DENIAL, show_work=False)], menu_has_edit_tool=False)

    assert acted == []
    assert len(prompts) == 1
    assert result["final_answer"] == OBSERVED_DENIAL
