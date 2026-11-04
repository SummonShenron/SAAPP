"""A cheaper model for carrying out an approved plan (backend/utils/code_plan.py execute_phase_choice): the plan is written by the person's own choice, its execution turns run on the model
CODE_PLAN_EXECUTE_MODEL names, and everything else is exactly as it was."""
import os

from backend.models import claude_llm, models
from backend.utils import code_plan as cp

CHOICES = (None, "gemini", "claude", "sonnet", "gpt")


def test_nothing_changes_when_the_setting_is_unset(monkeypatch):
    monkeypatch.delenv(cp.EXECUTE_MODEL_ENV, raising=False)
    for phase in (None, cp.PLAN_PHASE, cp.EXECUTE_PHASE):
        for choice in CHOICES:
            assert cp.execute_phase_choice(choice, phase) == choice, (phase, choice)


def test_only_the_execute_phase_is_swapped(monkeypatch):
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE) == "claude"
    assert cp.execute_phase_choice("sonnet", cp.PLAN_PHASE) == "sonnet"       # the plan is still written by the person's own choice
    assert cp.execute_phase_choice("sonnet", None) == "sonnet"                 # an ordinary turn, with no plan


def test_a_person_with_no_choice_of_their_own_still_gets_the_swap(monkeypatch):
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    assert cp.execute_phase_choice(None, cp.EXECUTE_PHASE) == "claude"


def test_the_value_is_trimmed_and_not_case_sensitive_and_an_unknown_one_is_ignored(monkeypatch):
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "  Claude ")
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE) == "claude"
    for bad in ("", "   ", "gpt-9", "haiku", "none"):
        monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, bad)
        assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE) == "sonnet", bad          # not a known choice: the person's own stays


def test_the_swap_reaches_the_model_that_is_actually_built(monkeypatch):
    # Sonnet chosen, Haiku for execution: the plan turn builds Sonnet, the execution turn builds Haiku (each with Gemini as its fallback, as always).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(models, "sonnet_available", lambda: True)
    monkeypatch.setattr(models, "_claude_tool_llms", {})
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    plan_llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.PLAN_PHASE))
    exec_llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE))
    assert plan_llm.model_name == claude_llm.SONNET_MODEL
    assert exec_llm.model_name == claude_llm.DEFAULT_MODEL
    assert plan_llm is not exec_llm and exec_llm.fallback is not None


def test_a_choice_that_cannot_be_offered_falls_back_as_it_always_did(monkeypatch):
    # No Claude key on this server: asking for Haiku at execution time is served by Gemini, never an error.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(models, "_claude_tool_llms", {})
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE))
    assert llm is models.lite_llm


def test_the_tool_agent_builds_its_model_through_the_phase_choice():
    source = open(os.path.join(os.path.dirname(__file__), "..", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'llm=tool_agent_llm(bool(deep_thinking), code_plan.execute_phase_choice(state.get("tool_model"), plan_phase)),' in source
