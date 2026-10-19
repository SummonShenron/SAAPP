import os

import pytest

from backend.models import models as models_module
from backend.models.claude_llm import ClaudeLLM
from backend.utils import user_settings_utils as usu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Local-file settings in a temp folder, no database, no key, no server default, until a test says otherwise."""
    monkeypatch.setattr(usu, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(usu, "get_db", lambda: None)
    monkeypatch.delenv("TOOL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.setattr(models_module, "_claude_tool_llms", {})


# ---------------------------------------------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------------------------------------------

def test_a_person_who_never_chose_has_no_stored_value():
    assert usu.get_user_tool_model("jack") is None
    assert usu.get_user_settings_bundle("jack")["tool_model"] is None


def test_the_choice_is_saved_per_person():
    assert usu.set_user_tool_model("jack", "claude") == "claude"
    usu.set_user_tool_model("alice", "gemini")
    assert usu.get_user_tool_model("jack") == "claude" and usu.get_user_tool_model("alice") == "gemini"
    assert usu.get_user_settings_bundle("jack")["tool_model"] == "claude"


def test_anything_that_is_not_a_known_model_is_refused_and_changes_nothing():
    usu.set_user_tool_model("jack", "claude")
    assert usu.set_user_tool_model("jack", "gpt-9") is None
    assert usu.set_user_tool_model("jack", "") is None
    assert usu.get_user_tool_model("jack") == "claude"


def test_a_stored_value_that_is_not_a_model_reads_as_never_chose():
    import json

    with open(usu._get_user_file("jack"), "w") as handle:
        json.dump({"tool_model": "something-old"}, handle)
    assert usu.get_user_tool_model("jack") is None


def test_setting_it_does_not_clobber_the_other_settings():
    usu.set_user_rag_mode("jack", "strict")
    usu.set_user_deep_thinking_mode("jack", True)
    usu.set_user_tool_model("jack", "claude")
    assert usu.get_user_rag_mode("jack") == "strict" and usu.get_user_deep_thinking_mode("jack") is True and usu.get_user_tool_model("jack") == "claude"


@pytest.mark.parametrize("who", ["guest_bty", "guest", "guest-recruiter@example.com"])
def test_guests_and_the_embed_are_always_gemini_and_cannot_change_it(who):
    assert usu.set_user_tool_model(who, "claude") == "gemini"
    assert usu.get_user_tool_model(who) == "gemini"
    assert usu.get_user_settings_bundle(who)["tool_model"] == "gemini"


# ---------------------------------------------------------------------------------------------------------------
# What the menu shows
# ---------------------------------------------------------------------------------------------------------------

def test_with_nothing_set_it_shows_gemini_offered_when_claude_is_configured():
    assert usu.tool_model_view("jack", claude_ready=True) == {"tool_model": "gemini", "choice": None, "default": "gemini", "available": True}


def test_a_choice_is_shown_as_chosen():
    usu.set_user_tool_model("jack", "claude")
    view = usu.tool_model_view("jack", claude_ready=True)
    assert view["tool_model"] == "claude" and view["choice"] == "claude" and view["available"] is True


def test_the_server_default_applies_until_the_person_chooses(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_PROVIDER", "anthropic")
    assert usu.tool_model_view("jack", True)["tool_model"] == "claude" and usu.tool_model_view("jack", True)["choice"] is None
    usu.set_user_tool_model("jack", "gemini")
    assert usu.tool_model_view("jack", True)["tool_model"] == "gemini", "their own choice beats the server's default"


def test_with_no_key_nothing_is_offered_and_the_effective_model_is_gemini():
    usu.set_user_tool_model("jack", "claude")
    view = usu.tool_model_view("jack", claude_ready=False)
    assert view["available"] is False and view["tool_model"] == "gemini" and view["choice"] == "claude", "the stored choice is kept for when the key returns"


def test_guests_are_never_offered_it():
    view = usu.tool_model_view("guest-recruiter@example.com", claude_ready=True)
    assert view["available"] is False and view["tool_model"] == "gemini"


# ---------------------------------------------------------------------------------------------------------------
# Which model runs
# ---------------------------------------------------------------------------------------------------------------

def test_the_persons_choice_decides_the_model():
    assert isinstance(models_module.tool_agent_llm(False, "claude"), ClaudeLLM)
    assert models_module.tool_agent_llm(False, "gemini") is models_module.lite_llm
    assert models_module.tool_agent_llm(True, "gemini") is models_module.lite_llm_deep


def test_no_choice_falls_back_to_the_server_default(monkeypatch):
    assert models_module.tool_agent_llm(False, None) is models_module.lite_llm
    monkeypatch.setenv("TOOL_AGENT_PROVIDER", "anthropic")
    assert isinstance(models_module.tool_agent_llm(False, None), ClaudeLLM)
    assert models_module.tool_agent_llm(False, "gemini") is models_module.lite_llm, "a person on Gemini stays on Gemini whatever the default"


def test_choosing_claude_without_a_key_quietly_stays_on_gemini(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert models_module.tool_agent_llm(False, "claude") is models_module.lite_llm


def test_the_claude_the_person_chose_falls_back_to_the_gemini_model_for_that_mode():
    assert models_module.tool_agent_llm(False, "claude").fallback is models_module.lite_llm
    assert models_module.tool_agent_llm(True, "claude").fallback is models_module.lite_llm_deep


# ---------------------------------------------------------------------------------------------------------------
# The endpoints and how the choice reaches the agent
# ---------------------------------------------------------------------------------------------------------------

def test_saving_returns_the_view_and_remembers_the_choice():
    view, problem = usu.save_tool_model_choice("jack", "claude", claude_ready=True)
    assert problem is None and view["tool_model"] == "claude" and view["choice"] == "claude"
    assert usu.get_user_tool_model("jack") == "claude"


def test_an_unknown_model_is_a_400_and_saves_nothing():
    view, problem = usu.save_tool_model_choice("jack", "gpt-9", claude_ready=True)
    assert view is None and problem[0] == 400 and usu.get_user_tool_model("jack") is None


def test_claude_when_it_is_not_set_up_is_a_409_and_saves_nothing():
    view, problem = usu.save_tool_model_choice("jack", "claude", claude_ready=False)
    assert view is None and problem[0] == 409 and usu.get_user_tool_model("jack") is None


def test_going_back_to_gemini_never_needs_claude_to_be_set_up():
    usu.set_user_tool_model("jack", "claude")
    view, problem = usu.save_tool_model_choice("jack", "gemini", claude_ready=False)
    assert problem is None and view["tool_model"] == "gemini"


def test_a_guest_cannot_switch_it_for_everyone():
    view, problem = usu.save_tool_model_choice("guest-recruiter@example.com", "claude", claude_ready=True)
    assert problem is None and view["tool_model"] == "gemini" and view["available"] is False


def test_the_endpoints_use_those_rules():
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert 'view, problem = save_tool_model_choice(username, payload.tool_model, claude_available())' in source
    assert 'raise HTTPException(status_code=problem[0], detail=problem[1])' in source
    assert 'return tool_model_view(username, claude_available())' in source


def test_the_chat_turn_hands_the_choice_to_the_agent():
    app_source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert '"tool_model": effective_tool_model,' in app_source and 'effective_tool_model = _settings.get("tool_model")' in app_source
    node_source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'llm=tool_agent_llm(bool(deep_thinking), state.get("tool_model")),' in node_source


def test_the_menu_offers_it_only_when_available_and_it_stays_open_like_the_other_toggles():
    chat = open(os.path.join(REPO, "local", "src", "pages", "Chat.tsx"), encoding="utf-8").read()
    start = chat.index("...(!toolModelAvailable ? [] : [{")
    entry = chat[start:chat.index("}]),", start)]
    assert "Code model:" in entry and "keepOpen: true" in entry and "handleToolModelChange" in entry
