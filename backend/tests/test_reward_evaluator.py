import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.services import reward_evaluator as re_mod


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


@run_async
async def test_evaluate_response_skips_unevaluated_source_type(monkeypatch):
    mock_ainvoke = AsyncMock()
    monkeypatch.setattr(re_mod.lite_llm, "ainvoke", mock_ainvoke)

    verdict = await re_mod.evaluate_response("prompt", "response", "conversational")

    assert verdict["verdict"] == "pass"
    mock_ainvoke.assert_not_called()


@run_async
async def test_evaluate_response_returns_pass_verdict(monkeypatch):
    monkeypatch.setattr(
        re_mod.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "verdict": "pass", "tag": None, "reason": None
        })))
    )

    verdict = await re_mod.evaluate_response("prompt", "a well-grounded response", "kb_strict")

    assert verdict == {"verdict": "pass", "tag": None, "reason": None}


@run_async
async def test_evaluate_response_returns_fail_verdict_with_tag_and_reason(monkeypatch):
    monkeypatch.setattr(
        re_mod.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "verdict": "fail", "tag": "hallucination", "reason": "Claims a fact not in the data."
        })))
    )

    verdict = await re_mod.evaluate_response("prompt", "a hallucinated response", "kb_open")

    assert verdict["verdict"] == "fail"
    assert verdict["tag"] == "hallucination"
    assert verdict["reason"] == "Claims a fact not in the data."


@run_async
async def test_evaluate_response_fails_open_on_malformed_json(monkeypatch):
    monkeypatch.setattr(
        re_mod.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content="not valid json at all"))
    )

    verdict = await re_mod.evaluate_response("prompt", "response", "tool_output")

    assert verdict["verdict"] == "pass"


@run_async
async def test_evaluate_response_fails_open_on_llm_exception(monkeypatch):
    monkeypatch.setattr(
        re_mod.lite_llm, "ainvoke",
        AsyncMock(side_effect=RuntimeError("model unavailable"))
    )

    verdict = await re_mod.evaluate_response("prompt", "response", "kb_strict")

    assert verdict["verdict"] == "pass"


@run_async
async def test_evaluate_response_fails_open_on_unexpected_verdict_value(monkeypatch):
    monkeypatch.setattr(
        re_mod.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"verdict": "maybe"})))
    )

    verdict = await re_mod.evaluate_response("prompt", "response", "kb_strict")

    assert verdict["verdict"] == "pass"


def test_build_correction_prompt_embeds_tag_and_reason():
    result = re_mod.build_correction_prompt("ORIGINAL PROMPT", "formatting", "Broke the markdown table.")

    assert "ORIGINAL PROMPT" in result
    assert "formatting" in result
    assert "Broke the markdown table." in result
    assert result.startswith("ORIGINAL PROMPT")


def test_the_evaluator_is_told_which_tools_really_ran_this_turn_and_that_already_is_not_an_earlier_turn():
    facts = re_mod.turn_facts({"propose_local_edits": {"calls": 1, "errors": 0}, "run_local_check": {"calls": 2, "errors": 1}})
    assert "propose_local_edits x1" in facts and "run_local_check x2 (1 failed)" in facts and "THIS turn" in facts
    assert "\"already\"" in facts and "not evidence that the work happened in an earlier turn" in facts
    assert re_mod.turn_facts({}) == "" and re_mod.turn_facts(None) == "" and re_mod.turn_facts("nonsense") == ""
    many = re_mod.turn_facts({f"tool_{i}": {"calls": 1} for i in range(40)})
    assert many.count(" x1") == re_mod.MAX_TOOLS_LISTED


@run_async
async def test_the_record_reaches_the_judge_prompt_only_when_there_is_one_and_the_prompt_still_formats(monkeypatch):
    seen = []

    async def fake(prompt):
        seen.append(prompt)
        return SimpleNamespace(content=json.dumps({"verdict": "pass", "tag": None, "reason": None}))

    monkeypatch.setattr(re_mod.lite_llm, "ainvoke", fake)
    await re_mod.evaluate_response("P {with} braces", "I put the file in place and ran it.", "tool_output", tools={"run_local_check": {"calls": 1, "errors": 0}})
    await re_mod.evaluate_response("P", "R", "tool_output")
    assert "TOOLS THE ASSISTANT ACTUALLY CALLED THIS TURN" in seen[0] and "run_local_check x1" in seen[0] and "P {with} braces" in seen[0]
    assert "TOOLS THE ASSISTANT ACTUALLY CALLED" not in seen[1]


def test_the_answer_check_in_app_py_passes_the_turns_tools():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "evaluate_response(prompt, full_response, source_type, tools=dict(llm_turn.tools))" in source
