import asyncio
import functools
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from backend.models import models as m


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# _is_transient_llm_error — shared by invoke/astream/ainvoke so a pattern
# added once (like 504, added after a real production trace) protects every
# call path instead of drifting across separately-maintained copies.
# ---------------------------------------------------------------------------

def test_is_transient_llm_error_detects_503_and_unavailable():
    assert m._is_transient_llm_error(Exception("503 Service Unavailable"))
    assert m._is_transient_llm_error(Exception("Error: UNAVAILABLE"))


def test_is_transient_llm_error_detects_504_and_deadline_exceeded():
    # The real production trace this covers: a 504 Gateway Timeout crashed tool_agent_node's
    # ReAct loop mid-investigation via ainvoke, which previously had no protection at all.
    assert m._is_transient_llm_error(Exception("504 Gateway Timeout"))
    assert m._is_transient_llm_error(Exception('{"status": "DEADLINE_EXCEEDED"}'))


def test_is_transient_llm_error_negative_for_unrelated_errors():
    assert not m._is_transient_llm_error(Exception("400 Bad Request: invalid API key"))
    assert not m._is_transient_llm_error(ValueError("could not parse response"))


# ---------------------------------------------------------------------------
# LazyLLM.ainvoke — previously undefined on the class at all, so calls fell
# through __getattr__ straight to the real client's own ainvoke, completely
# bypassing the graceful-degradation handling invoke()/astream() already had.
# tool_agent_node's entire ReAct loop calls exactly this method.
# ---------------------------------------------------------------------------

def _lazy_llm_with_fake_client(fake_client):
    llm = m.LazyLLM(model_name="test-model")
    llm._real_llm = fake_client  # pre-set so _ensure_initialized() is a no-op
    return llm


@run_async
async def test_ainvoke_returns_real_result_on_success():
    fake_client = MagicMock()
    fake_client.ainvoke = AsyncMock(return_value=AIMessage(content="real answer"))
    llm = _lazy_llm_with_fake_client(fake_client)

    result = await llm.ainvoke("some prompt")

    assert result.content == "real answer"


@run_async
async def test_ainvoke_gracefully_degrades_on_504_instead_of_raising():
    fake_client = MagicMock()
    fake_client.ainvoke = AsyncMock(side_effect=Exception("504 Gateway Timeout. DEADLINE_EXCEEDED"))
    llm = _lazy_llm_with_fake_client(fake_client)

    result = await llm.ainvoke("some prompt")

    assert "experiencing high traffic" in result.content


@run_async
async def test_ainvoke_gracefully_degrades_on_503_instead_of_raising():
    fake_client = MagicMock()
    fake_client.ainvoke = AsyncMock(side_effect=Exception("503 UNAVAILABLE"))
    llm = _lazy_llm_with_fake_client(fake_client)

    result = await llm.ainvoke("some prompt")

    assert "experiencing high traffic" in result.content


@run_async
async def test_ainvoke_reraises_non_transient_errors():
    fake_client = MagicMock()
    fake_client.ainvoke = AsyncMock(side_effect=ValueError("400 Bad Request: invalid API key"))
    llm = _lazy_llm_with_fake_client(fake_client)

    with pytest.raises(ValueError):
        await llm.ainvoke("some prompt")


@run_async
async def test_ainvoke_dev_mode_returns_mock_without_touching_real_client():
    llm = m.LazyLLM(model_name="test-model")
    llm.dev_mode = True

    result = await llm.ainvoke("some prompt")

    assert "DEV_MODE" in result.content


# ---------------------------------------------------------------------------
# invoke/astream — same transient-error coverage, now sharing
# _is_transient_llm_error with ainvoke instead of each hand-rolling its own
# "503" in str(e) check (which is exactly what let 504 fall through unnoticed
# on the async path).
# ---------------------------------------------------------------------------

def test_invoke_gracefully_degrades_on_504():
    fake_client = MagicMock()
    fake_client.invoke = MagicMock(side_effect=Exception("504 Gateway Timeout"))
    llm = _lazy_llm_with_fake_client(fake_client)

    result = llm.invoke("some prompt")

    assert "experiencing high traffic" in result.content


def test_invoke_reraises_non_transient_errors():
    fake_client = MagicMock()
    fake_client.invoke = MagicMock(side_effect=ValueError("400 Bad Request"))
    llm = _lazy_llm_with_fake_client(fake_client)

    with pytest.raises(ValueError):
        llm.invoke("some prompt")


@run_async
async def test_astream_gracefully_degrades_on_504():
    fake_client = MagicMock()

    async def fake_astream(*args, **kwargs):
        raise Exception("504 Gateway Timeout")
        yield  # pragma: no cover - makes this an async generator

    fake_client.astream = fake_astream
    llm = _lazy_llm_with_fake_client(fake_client)

    chunks = [chunk async for chunk in llm.astream("some prompt")]

    assert len(chunks) == 1
    assert "experiencing high traffic" in chunks[0].content
