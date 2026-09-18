import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.services import memory_compaction as mc
from backend.utils import memory_utils


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _facts(n, category="preference"):
    return [SimpleNamespace(category=category, fact=f"Fact number {i}") for i in range(n)]


@run_async
async def test_extract_user_patterns_skips_when_not_enough_facts(monkeypatch):
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION - 1))
    mock_ainvoke = AsyncMock()
    monkeypatch.setattr(mc.lite_llm, "ainvoke", mock_ainvoke)

    result = await mc.extract_user_patterns(db=None, username="jack")

    assert result["status"] == "skipped"
    assert result["reason"] == "not_enough_facts"
    mock_ainvoke.assert_not_called()


@run_async
async def test_extract_user_patterns_saves_each_returned_pattern(monkeypatch):
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "patterns": ["Consistently gravitates toward agent systems.", "Prefers hands-on engineering work."]
        })))
    )
    mock_save_user_fact = Mock()
    monkeypatch.setattr(mc, "save_user_fact", mock_save_user_fact)

    result = await mc.extract_user_patterns(db=None, username="jack")

    assert result["status"] == "completed"
    assert result["patterns_saved"] == 2
    assert mock_save_user_fact.call_count == 2
    for call in mock_save_user_fact.call_args_list:
        _, kwargs = call
        assert kwargs["category"] == "pattern"
        assert kwargs["source"] == "pattern"


@run_async
async def test_extract_user_patterns_empty_patterns_list_saves_nothing(monkeypatch):
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"patterns": []})))
    )
    mock_save_user_fact = Mock()
    monkeypatch.setattr(mc, "save_user_fact", mock_save_user_fact)

    result = await mc.extract_user_patterns(db=None, username="jack")

    assert result["status"] == "completed"
    assert result["patterns_saved"] == 0
    mock_save_user_fact.assert_not_called()


@run_async
async def test_extract_user_patterns_malformed_llm_response_falls_back_gracefully(monkeypatch):
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content="not valid json at all"))
    )
    mock_save_user_fact = Mock()
    monkeypatch.setattr(mc, "save_user_fact", mock_save_user_fact)

    result = await mc.extract_user_patterns(db=None, username="jack")

    assert result["status"] == "error"
    mock_save_user_fact.assert_not_called()


@run_async
async def test_extract_user_patterns_ignores_non_string_entries(monkeypatch):
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"patterns": ["Real pattern.", "", None, 123]})))
    )
    mock_save_user_fact = Mock()
    monkeypatch.setattr(mc, "save_user_fact", mock_save_user_fact)

    result = await mc.extract_user_patterns(db=None, username="jack")

    assert result["patterns_saved"] == 1
    mock_save_user_fact.assert_called_once()


# ---------------------------------------------------------------------------
# Integration-style test: extract_user_patterns calling the REAL save_user_fact,
# confirming patterns actually get deduplication for free via its existing
# embedding-similarity + LLM-judgment conflict detection (not something
# extract_user_patterns implements itself).
# ---------------------------------------------------------------------------

def _fake_embed(text):
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    bucket = hash(normalized) % 9973
    vector = [0.0] * 9973
    vector[bucket] = 1.0
    return vector


@run_async
async def test_extract_user_patterns_real_save_user_fact_dedupes_repeated_pattern(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    monkeypatch.setattr(memory_utils, "embed_text", _fake_embed)
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "duplicate"})))
    )
    monkeypatch.setattr(mc, "save_user_fact", memory_utils.save_user_fact)
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "patterns": ["Consistently gravitates toward agent systems."]
        })))
    )

    await mc.extract_user_patterns(db=None, username="jack")
    await mc.extract_user_patterns(db=None, username="jack")

    saved = memory_utils.load_user_facts("jack", category="pattern")
    assert len(saved) == 1
    assert saved[0].fact == "Consistently gravitates toward agent systems."
