"""How many steps the tool agent's loop used and whether it ran into its limit, counted per turn and shown on Sonic Health (backend/utils/llm_turns.py).

This is what says whether the step budget (TOOL_AGENT_MAX_ITERATIONS, and the deep one) is too tight, instead of guessing: a 7-file library review used a whole 4-step
budget exactly, and nothing recorded how often that happens.
"""
import asyncio
import json
import os
from types import SimpleNamespace

from backend.services import agent_workflow as aw
from backend.utils import llm_turns as lt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _finish(steps, hit_cap, deep=False, day="2026-10-21"):
    lt.reset()
    turn = lt.begin("jack")
    turn.tool_agent = True
    if deep:
        turn.deep_source = "setting"
    token = lt._current.set(turn)
    try:
        lt.note_tool_loop(steps, hit_cap)
    finally:
        lt._current.reset(token)
    lt.finish(turn, day=day)
    return lt.snapshot()[day]


def test_a_loop_that_stopped_early_is_counted_with_its_steps_and_no_cap_hit():
    slot = _finish(3, False)
    assert slot["loop_turns"] == 1 and slot["loop_steps"] == 3 and slot["steps_3_4"] == 1 and slot["cap_hit_turns"] == 0 and slot["deep_loop_turns"] == 0


def test_a_loop_that_ran_into_its_limit_is_counted_as_a_cap_hit_and_deep_ones_separately():
    plain = _finish(4, True)
    assert plain["cap_hit_turns"] == 1 and plain["deep_cap_hit_turns"] == 0 and plain["steps_3_4"] == 1
    deep = _finish(6, True, deep=True)
    assert deep["loop_turns"] == 1 and deep["deep_loop_turns"] == 1 and deep["deep_cap_hit_turns"] == 1 and deep["cap_hit_turns"] == 1 and deep["steps_5_6"] == 1


def test_the_step_buckets_cover_every_step_count():
    assert [lt.steps_bucket(n) for n in (0, 1, 2, 3, 4, 5, 6, 7, 14)] == ["steps_1_2", "steps_1_2", "steps_1_2", "steps_3_4", "steps_3_4", "steps_5_6", "steps_5_6", "steps_7_plus", "steps_7_plus"]


def test_a_turn_with_no_tool_loop_adds_nothing_and_a_resumed_loop_adds_to_the_same_turn():
    lt.reset()
    plain = lt.begin("jack")
    lt.finish(plain, day="2026-10-21")
    assert lt.snapshot()["2026-10-21"]["loop_turns"] == 0
    lt.reset()
    turn = lt.begin("jack")
    turn.tool_agent = True
    lt.note_tool_loop(2, False)
    lt.note_tool_loop(3, True)
    lt.finish(turn, day="2026-10-21")
    slot = lt.snapshot()["2026-10-21"]
    assert slot["loop_turns"] == 1 and slot["loop_steps"] == 5 and slot["cap_hit_turns"] == 1


def test_the_summary_gives_the_share_that_hit_the_limit_and_the_spread():
    docs = [
        {"day": "2026-10-20", "turns": 6, "tool_turns": 4, "loop_turns": 4, "loop_steps": 14, "cap_hit_turns": 1, "steps_1_2": 1, "steps_3_4": 2, "steps_5_6": 1, "deep_loop_turns": 1, "deep_cap_hit_turns": 1},
        {"day": "2026-10-21", "turns": 2, "tool_turns": 1, "loop_turns": 1, "loop_steps": 4, "cap_hit_turns": 1, "steps_3_4": 1},
    ]
    loop = lt.summarize_turns(docs)["loop"]
    assert loop["turns"] == 5 and loop["avg_steps"] == 3.6 and loop["cap_hits"] == 2 and loop["cap_hit_share"] == 0.4
    assert loop["deep_turns"] == 1 and loop["deep_cap_hit_share"] == 1.0
    assert {b["bucket"]: b["turns"] for b in loop["histogram"]} == {"steps_1_2": 1, "steps_3_4": 3, "steps_5_6": 1, "steps_7_plus": 0}
    empty = lt.summarize_turns([])["loop"]
    assert empty["turns"] == 0 and empty["avg_steps"] is None and empty["cap_hit_share"] is None and empty["deep_cap_hit_share"] is None


def _run_react(responses, max_iterations):
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    async def act(decision):
        return "def existing():\n    return 1\n"

    original = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake
    lt.reset()
    turn = lt.begin("jack")
    turn.tool_agent = True
    try:
        asyncio.run(aw.run_react_loop(question="q", schema="s", prompt_template="{question} | {schema} | {attempts}", act=act, max_iterations=max_iterations, node_name="t"))
    finally:
        aw.lite_llm.ainvoke = original
    return turn


def _read(path):
    return SimpleNamespace(content=json.dumps({"action": "query", "purpose": "read", "tool_action": "read_repo_file", "args": {"path": path}}))


def _final(text):
    return SimpleNamespace(content=json.dumps({"action": "final", "answer": text}))


def test_the_react_loop_reports_an_early_answer_and_a_run_into_the_limit():
    early = _run_react([_read("a.py"), _final("done")], max_iterations=6)
    assert early.loop_ran and early.loop_steps == 2 and early.cap_hit is False
    limit = _run_react([_read("a.py"), _read("b.py"), _read("c.py"), _final("done")], max_iterations=4)
    assert limit.loop_ran and limit.loop_steps == 4 and limit.cap_hit is True


def test_the_native_loop_reports_its_steps_and_whether_it_was_forced():
    source = open(os.path.join(REPO, "backend", "services", "native_loop.py"), encoding="utf-8").read()
    assert "llm_turns.note_tool_loop(step, forced)" in source and source.index("note_tool_loop") < source.index('"final_answer": final_text or')


def test_nothing_in_the_counters_is_text_and_the_page_has_a_row_for_them():
    assert all(isinstance(name, str) and name.replace("_", "").isalnum() for name in lt.LOOP_FIELDS + lt.STEP_BUCKETS)
    page = open(os.path.join(REPO, "local", "src", "pages", "SonicHealth.tsx"), encoding="utf-8").read()
    assert "Does the step limit matter?" in page and "STEP_BUCKET_LABELS" in page
