"""The Sonnet option in the Code model chip (2026-11-01): Claude Sonnet 5.5 as a third choice for the tool agent's reasoning steps. It is 20 times Haiku's price per token ($2.00 / $10.00 against
$0.10 / $0.50 per million), so it is offered only when LLM_PRICES lists it (the budget guard counts dollars from those prices), it carries its own effort and thinking settings, and a stored
choice that can no longer be offered is served by Haiku."""
import json
import os

import pytest

from backend.models import claude_llm
from backend.models import models as models_module
from backend.models.claude_llm import ClaudeLLM
from backend.utils import llm_usage
from backend.utils import user_settings_utils as usu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PRICES_WITH_SONNET = json.dumps({
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cached_input": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cached_input": 0.20},
})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(usu, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(usu, "get_db", lambda: None)
    monkeypatch.delenv("TOOL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("LLM_PRICES", raising=False)
    monkeypatch.setattr(models_module, "_claude_tool_llms", {})


def priced(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", PRICES_WITH_SONNET)


# ---- when it can be offered -----------------------------------------------------------------------------------------------------------------------

def test_sonnet_is_offered_only_with_a_key_and_a_price(monkeypatch):
    assert models_module.sonnet_available() is False                         # a key, but no price: its spend would be invisible to the budget guard
    priced(monkeypatch)
    assert models_module.sonnet_available() is True
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert models_module.sonnet_available() is False                         # a price, but no key


def test_the_price_in_the_environment_is_the_published_one(monkeypatch):
    priced(monkeypatch)
    sonnet = llm_usage.cached_prices()[claude_llm.SONNET_MODEL]
    assert claude_llm.SONNET_MODEL == "claude-sonnet-5-5" and sonnet == {"input": 2.0, "output": 10.0, "cached_input": 0.2}
    assert round(sonnet["input"] / llm_usage.cached_prices()["claude-haiku-5-5"]["input"]) == 20           # the chip says "about 20 times"


# ---- choosing it -----------------------------------------------------------------------------------------------------------------------------

def test_a_priced_server_lets_a_person_choose_sonnet_and_shows_it():
    view, problem = usu.save_tool_model_choice("jack", "sonnet", claude_ready=True, sonnet_ready=True)
    assert problem is None and view["tool_model"] == "sonnet" and view["choice"] == "sonnet" and view["sonnet_available"] is True
    assert usu.get_user_tool_model("jack") == "sonnet" and usu.get_user_settings_bundle("jack")["tool_model"] == "sonnet"


def test_without_a_price_or_a_key_sonnet_cannot_be_chosen_and_nothing_is_saved():
    assert usu.save_tool_model_choice("jack", "sonnet", claude_ready=True, sonnet_ready=False) == (None, (409, "Claude Sonnet isn't priced on this server, so it can't be used yet."))
    assert usu.save_tool_model_choice("jack", "sonnet", claude_ready=False, sonnet_ready=False)[1][0] == 409
    assert usu.get_user_tool_model("jack") is None


def test_an_unknown_model_is_still_a_400_and_the_message_names_all_three():
    view, problem = usu.save_tool_model_choice("jack", "gpt", claude_ready=True, sonnet_ready=True)
    assert view is None and problem == (400, "tool_model must be 'gemini', 'claude' or 'sonnet'")


def test_a_stored_sonnet_that_can_no_longer_be_offered_shows_as_claude():
    usu.set_user_tool_model("jack", "sonnet")
    assert usu.tool_model_view("jack", claude_ready=True, sonnet_ready=False)["tool_model"] == "claude"
    assert usu.tool_model_view("jack", claude_ready=True, sonnet_ready=True)["tool_model"] == "sonnet"
    assert usu.tool_model_view("jack", claude_ready=False, sonnet_ready=True)["tool_model"] == "gemini"            # no key at all: Gemini whatever was stored


def test_a_guest_can_never_have_sonnet():
    usu.set_user_tool_model("guest_bty", "sonnet")
    assert usu.get_user_tool_model("guest_bty") == "gemini"


# ---- the model behind the steps ----------------------------------------------------------------------------------------------------------------

def test_the_sonnet_choice_gives_sonnet_with_the_matching_gemini_model_as_its_fallback_and_its_own_effort(monkeypatch):
    priced(monkeypatch)
    ordinary, deep = models_module.tool_agent_llm(False, "sonnet"), models_module.tool_agent_llm(True, "sonnet")
    assert isinstance(ordinary, ClaudeLLM) and ordinary.model_name == "claude-sonnet-5-5" and ordinary.effort == claude_llm.SONNET_EFFORT == "medium"
    assert ordinary.fallback is models_module.lite_llm and deep.fallback is models_module.lite_llm_deep
    assert models_module.tool_agent_llm(False, "sonnet") is ordinary, "one instance per mode, not one per turn"


def test_haiku_and_sonnet_are_separate_instances_and_haiku_is_unchanged(monkeypatch):
    priced(monkeypatch)
    haiku, sonnet = models_module.tool_agent_llm(False, "claude"), models_module.tool_agent_llm(False, "sonnet")
    assert haiku is not sonnet and haiku.model_name == claude_llm.DEFAULT_MODEL == "claude-haiku-5-5" and haiku.effort == claude_llm.DEFAULT_EFFORT


def test_a_sonnet_choice_that_cannot_be_offered_is_served_by_haiku_never_silently_unpriced():
    chosen = models_module.tool_agent_llm(False, "sonnet")                    # no price configured
    assert isinstance(chosen, ClaudeLLM) and chosen.model_name == "claude-haiku-5-5"


def test_with_no_key_every_choice_stays_on_gemini(monkeypatch):
    priced(monkeypatch)
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert models_module.tool_agent_llm(False, "sonnet") is models_module.lite_llm


# ---- Sonnet's own request settings -------------------------------------------------------------------------------------------------------------

def test_turning_thinking_off_uses_the_form_sonnet_accepts_and_haiku_keeps_its_own(monkeypatch):
    seen = {}

    class FakeChat:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    import langchain_anthropic

    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", FakeChat)
    ClaudeLLM(model_name="claude-sonnet-5-5", thinking_off=True)._client()
    assert seen["thinking"] == {"type": "between_tools"}                   # Sonnet 5.5 answers {"type": "disabled"} with a 400
    ClaudeLLM(model_name="claude-haiku-5-5", thinking_off=True)._client()
    assert seen["thinking"] == {"type": "disabled"}


def test_the_endpoints_and_the_chip_know_about_it():
    app_source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "from backend.models.models import llm, get_stream_llm, claude_available, sonnet_available" in app_source
    api = open(os.path.join(REPO, "local", "src", "api.ts"), encoding="utf-8").read()
    assert "export type ToolModel = 'gemini' | 'claude' | 'sonnet';" in api and "sonnet_available: boolean;" in api
