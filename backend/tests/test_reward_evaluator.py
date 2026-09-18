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
