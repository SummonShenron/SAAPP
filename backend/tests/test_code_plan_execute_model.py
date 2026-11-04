"""A different model for carrying out an approved plan (backend/utils/code_plan.py execute_phase_choice). The plan is written by the person's own Code model; the execution turns run on the model they chose for
that, or on the server default CODE_PLAN_EXECUTE_MODEL when they are already on Claude. Nothing may move a person's code to a provider they did not pick."""
import os

from backend.models import claude_llm, models
from backend.utils import code_plan as cp

CHOICES = (None, "gemini", "claude", "sonnet", "gpt")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_nothing_changes_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv(cp.EXECUTE_MODEL_ENV, raising=False)
    for phase in (None, cp.PLAN_PHASE, cp.EXECUTE_PHASE):
        for choice in CHOICES:
            assert cp.execute_phase_choice(choice, phase) == choice, (phase, choice)
            assert cp.execute_phase_choice(choice, phase, None) == choice, (phase, choice)


def test_a_persons_own_execute_choice_applies_only_while_the_plan_is_carried_out(monkeypatch):
    monkeypatch.delenv(cp.EXECUTE_MODEL_ENV, raising=False)
    for phase in (None, cp.PLAN_PHASE):
        assert cp.execute_phase_choice("sonnet", phase, "claude") == "sonnet", phase          # the plan, and ordinary turns, stay on the Code model
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, "claude") == "claude"
    # whatever their Code model is, including Gemini or none: choosing the provider is what sends their code there
    assert cp.execute_phase_choice("gemini", cp.EXECUTE_PHASE, "claude") == "claude"
    assert cp.execute_phase_choice(None, cp.EXECUTE_PHASE, "gpt") == "gpt"
    for not_a_choice in (None, "same", "", "haiku", "gpt-9"):
        assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, not_a_choice) == "sonnet", not_a_choice     # "same" is no override


def test_the_persons_own_choice_wins_over_the_server_default(monkeypatch):
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, "gemini") == "gemini"
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, "sonnet") == "sonnet"


def test_the_server_default_only_applies_to_someone_already_on_claude(monkeypatch):
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "claude")
    assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE) == "claude"
    assert cp.execute_phase_choice("claude", cp.EXECUTE_PHASE) == "claude"
    for own in (None, "gemini", "gpt"):
        assert cp.execute_phase_choice(own, cp.EXECUTE_PHASE) == own, own            # never moves the code of a Gemini or GPT person to Anthropic
    for phase in (None, cp.PLAN_PHASE):
        assert cp.execute_phase_choice("sonnet", phase) == "sonnet"


def test_the_server_default_can_only_name_a_claude_model(monkeypatch):
    for value in ("gemini", "gpt", "haiku", "", "   ", "none"):
        monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, value)
        assert cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE) == "sonnet", value
    monkeypatch.setenv(cp.EXECUTE_MODEL_ENV, "  Sonnet ")
    assert cp.execute_phase_choice("claude", cp.EXECUTE_PHASE) == "sonnet"             # trimmed and not case sensitive


def test_every_choice_has_a_name_for_the_trace():
    assert set(cp.EXECUTE_MODEL_NAMES) == set(cp.TOOL_MODEL_CHOICES)


def test_the_swap_reaches_the_model_that_is_actually_built(monkeypatch):
    # Sonnet is the Code model, Haiku the person's execute model: the plan turn builds Sonnet, the execution turn builds Haiku (each with Gemini as its fallback, as always).
    monkeypatch.delenv(cp.EXECUTE_MODEL_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(models, "sonnet_available", lambda: True)
    monkeypatch.setattr(models, "_claude_tool_llms", {})
    plan_llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.PLAN_PHASE, "claude"))
    exec_llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, "claude"))
    assert plan_llm.model_name == claude_llm.SONNET_MODEL
    assert exec_llm.model_name == claude_llm.DEFAULT_MODEL
    assert plan_llm is not exec_llm and exec_llm.fallback is not None


def test_a_choice_that_cannot_be_offered_falls_back_as_it_always_did(monkeypatch):
    # No Claude key on this server: Haiku asked for at execution time is served by Gemini, never an error.
    monkeypatch.delenv(cp.EXECUTE_MODEL_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    monkeypatch.setattr(models, "_claude_tool_llms", {})
    llm = models.tool_agent_llm(False, cp.execute_phase_choice("sonnet", cp.EXECUTE_PHASE, "claude"))
    assert llm is models.lite_llm


def test_the_tool_agent_builds_its_model_through_the_phase_choice_and_says_so_in_the_trace():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'tool_model_choice = code_plan.execute_phase_choice(state.get("tool_model"), plan_phase, state.get("execute_model"))' in source
    assert "llm=tool_agent_llm(bool(deep_thinking), tool_model_choice)," in source
    assert "code_plan.EXECUTE_MODEL_NAMES.get(str(tool_model_choice)" in source        # the trace names the model that runs the step
    assert 'if plan_phase == code_plan.EXECUTE_PHASE and tool_model_choice != state.get("tool_model"):' in source


def test_the_chat_turn_hands_the_execute_choice_to_the_agent_state():
    app_source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert '"execute_model": _settings.get("execute_model"),' in app_source
    state_source = open(os.path.join(REPO, "backend", "state", "graph_state.py"), encoding="utf-8").read()
    assert "execute_model: Optional[str]" in state_source
