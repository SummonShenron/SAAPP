"""What the ReAct loop tells the model as its steps run out.

Measured with a 4-step cap: a request to WRITE code came back as a description of the research ("I investigated the codebase and found...") in 8 of 18 runs, because the
last step only asked the model to "honestly summarize what you tried and found". The last step now asks for the code itself, and the final tool steps are announced.
"""
import asyncio
import json
from types import SimpleNamespace

from backend.services import agent_workflow as aw
from backend.services import react_loop


def _read(path):
    return SimpleNamespace(content=json.dumps({"action": "query", "purpose": "read", "tool_action": "read_repo_file", "args": {"path": path}}))


def _final(text):
    return SimpleNamespace(content=json.dumps({"action": "final", "answer": text}))


async def _run(responses, max_iterations):
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    async def act(decision):
        return "def existing():\n    return 1\n"

    original = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake
    try:
        result = await aw.run_react_loop(question="write a route", schema="repo=x", prompt_template="{question} | {schema} | {attempts}", act=act,
                                         max_iterations=max_iterations, node_name="t")
    finally:
        aw.lite_llm.ainvoke = original
    return result, prompts


def run(responses, max_iterations=4):
    return asyncio.run(_run(responses, max_iterations))


def test_the_last_step_asks_for_the_code_itself_and_not_a_summary_of_the_research():
    _, prompts = run([_read("a.py"), _read("b.py"), _read("c.py"), _final("done")])
    last = prompts[-1]
    assert "You have used all your steps" in last and "write it now, in full, in a fenced code block" in last
    assert "do not answer with a description of your research instead" in last


def test_the_final_tool_steps_are_announced_and_earlier_ones_are_not():
    _, prompts = run([_read("a.py"), _read("b.py"), _read("c.py"), _final("done")])
    assert "Steps:" not in prompts[0]
    assert "you have 2 tool steps left" in prompts[1]
    assert "you have 1 tool step left" in prompts[2]
    assert "Steps:" not in prompts[3], "the forced answer step carries the final notice, not a count"


def test_a_roomy_budget_is_not_nagged_until_the_end():
    _, prompts = run([_read("a.py")] * 6 + [_final("done")], max_iterations=7)
    announced = [index for index, prompt in enumerate(prompts) if "Steps:" in prompt]
    assert announced == [4, 5], f"only the last two tool steps are announced, got {announced}"


def test_the_announcement_says_the_answer_must_be_the_code_when_code_was_asked():
    _, prompts = run([_read("a.py"), _read("b.py"), _read("c.py"), _final("done")])
    assert "your final answer must be the code itself, in full" in prompts[2]


def test_a_change_to_a_connected_folder_is_told_to_propose_the_edit_not_to_paste_code_as_steps_run_out():
    async def go():
        prompts = []

        async def fake(prompt):
            prompts.append(prompt)
            return _read("a.py") if len(prompts) < 4 else _final("done")

        async def act(decision):
            return "def existing():\n    return 1\n"

        original = aw.lite_llm.ainvoke
        aw.lite_llm.ainvoke = fake
        try:
            await aw.run_react_loop(question="swap the orange hand", schema="repo=x", prompt_template="{question} | {schema} | {attempts}", act=act,
                                    max_iterations=4, node_name="t", required_action=aw._LOCAL_EDIT_REQUIRED_ACTION)
        finally:
            aw.lite_llm.ainvoke = original
        return prompts

    prompts = asyncio.run(go())
    assert "Your next tool step must be propose_local_edits" in prompts[1] and "must be the code itself" not in prompts[1]


def test_an_edit_turn_never_gets_fewer_steps_than_the_floor_but_low_power_mode_still_caps_it():
    from backend.utils import budget_guard as bg

    floor = aw.TOOL_AGENT_LOCAL_EDIT_MIN_STEPS
    assert floor >= 6
    assert bg.tool_steps(max(4, floor), {"level": "ok"}) == floor        # a server tuned to 4 steps still lets an edit find, read, propose and answer
    assert bg.tool_steps(max(4, floor), {"level": next(iter(bg.LOW_POWER_LEVELS))}) == bg.THROTTLED_TOOL_STEPS


def test_the_synthesis_after_a_loop_that_ran_out_uses_the_same_notice():
    # A loop that never says "final" ends in one more synthesis call; it must ask for code the same way.
    result, prompts = run([_read("a.py")] * 3 + [_read("d.py")] * 2 + [_final("here is the code")], max_iterations=4)
    assert any(react_loop.FORCED_FINAL_NOTICE.strip()[:60] in prompt for prompt in prompts[-2:])
    assert result["final_answer"]


def test_the_notice_text_is_one_shared_constant():
    assert "fenced code block" in react_loop.FORCED_FINAL_NOTICE and "honestly summarize" in react_loop.FORCED_FINAL_NOTICE
