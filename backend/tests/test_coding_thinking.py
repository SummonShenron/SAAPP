"""A turn that makes a change thinks on Haiku, with drop_block on (models.tool_agent_llm(coding=True), tool_agent_node). Nothing here calls a model: the loop is faked."""
import pytest

from backend.models import claude_llm
from backend.models import models as models_module
from backend.models.claude_llm import ClaudeLLM
from backend.services import local_edit_decisions as led
from backend.tests.test_local_edit_decisions import _drive
from backend.tests.test_local_edits import _connect_folder, run_async
from backend.utils import self_counters


@pytest.fixture(autouse=True)
def _claude_key(monkeypatch):
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("CODING_THINKING", raising=False)
    models_module._claude_tool_llms.clear()
    yield
    models_module._claude_tool_llms.clear()


def test_a_coding_turn_on_haiku_thinks_with_drop_block_and_the_gemini_fallback():
    llm = models_module.tool_agent_llm(False, "claude", coding=True)
    assert isinstance(llm, ClaudeLLM) and llm.thinking_off is False and llm.drop_block is True
    assert llm.model_name != claude_llm.SONNET_MODEL and llm.fallback is models_module.lite_llm
    assert models_module.tool_agent_llm(True, "claude", coding=True).fallback is models_module.lite_llm_deep
    assert models_module.tool_agent_llm(False, "claude", coding=True) is llm, "one instance per mode, not one per turn"


def test_any_other_turn_keeps_the_ordinary_model(monkeypatch):
    coding = models_module.tool_agent_llm(False, "claude", coding=True)
    ordinary = models_module.tool_agent_llm(False, "claude")
    assert ordinary is not coding and ordinary.drop_block is claude_llm.DROP_BLOCK and ordinary.thinking_off is claude_llm.THINKING_OFF


def test_the_switch_turns_it_off():
    import os

    os.environ["CODING_THINKING"] = "off"
    try:
        llm = models_module.tool_agent_llm(False, "claude", coding=True)
        assert llm is models_module.tool_agent_llm(False, "claude") and llm.drop_block is claude_llm.DROP_BLOCK
    finally:
        del os.environ["CODING_THINKING"]


def test_sonnet_gemini_and_no_key_are_unchanged(monkeypatch):
    monkeypatch.setattr(models_module, "sonnet_available", lambda: True)
    assert models_module.tool_agent_llm(False, "sonnet", coding=True) is models_module.tool_agent_llm(False, "sonnet")
    assert models_module.tool_agent_llm(False, "gemini", coding=True) is models_module.lite_llm
    monkeypatch.delenv("CLAUDE_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert models_module.tool_agent_llm(False, "claude", coding=True) is models_module.lite_llm


# ---- through the tool agent ----------------------------------------------------------------------------------------------------------------

def _counted(monkeypatch):
    recorded = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    return recorded


@run_async
async def test_an_edit_turn_runs_the_thinking_model_and_is_counted(monkeypatch):
    _connect_folder()
    recorded = _counted(monkeypatch)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"tool_model": "claude"}, question="change greet in src/app.py so it says hello instead of hi")
    llm = seen["kwargs"]["llm"]
    assert isinstance(llm, ClaudeLLM) and llm.thinking_off is False and llm.drop_block is True
    assert {"code_thinking_turns": 1} in recorded and "code_thinking_turns" in self_counters._PLAIN_KEYS


@run_async
async def test_a_question_that_changes_nothing_does_not_think_and_is_not_counted(monkeypatch):
    _connect_folder()
    recorded = _counted(monkeypatch)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), state_extra={"tool_model": "claude"}, question="what does greet return?")
    assert seen["kwargs"]["llm"] is models_module.tool_agent_llm(False, "claude")
    assert {"code_thinking_turns": 1} not in recorded
