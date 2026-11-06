import json
import os
import pathlib

import pytest

from backend.models import models as models_module
from backend.models.openai_llm import OpenAILLM
from backend.utils import user_settings_utils as usu

PRICED = json.dumps({"gpt-5.4-mini": {"input": 0.75, "output": 4.5, "cached_input": 0.075}})
APP = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Local-file settings in a temp folder, no database, no OpenAI key or price, and empty model caches, until a test says otherwise."""
    monkeypatch.setattr(usu, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(usu, "get_db", lambda: None)
    monkeypatch.delenv("TOOL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PRICES", raising=False)
    monkeypatch.setattr(models_module, "_openai_llms", {})
    monkeypatch.setattr(models_module, "_claude_tool_llms", {})


def offer_gpt(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    monkeypatch.setenv("LLM_PRICES", PRICED)


# ---- what is stored -----------------------------------------------------------------------------------------------------

def test_a_person_who_never_chose_has_no_stored_base_model():
    assert usu.get_user_reply_model("jack") is None
    assert usu.get_user_settings_bundle("jack")["reply_model"] is None


def test_the_base_model_is_saved_per_person_and_does_not_touch_the_code_model():
    usu.set_user_tool_model("jack", "claude")
    assert usu.set_user_reply_model("jack", "gpt") == "gpt"
    usu.set_user_reply_model("alice", "gemini")
    assert usu.get_user_reply_model("jack") == "gpt" and usu.get_user_reply_model("alice") == "gemini"
    assert usu.get_user_tool_model("jack") == "claude"            # the two settings are independent
    bundle = usu.get_user_settings_bundle("jack")
    assert bundle["reply_model"] == "gpt" and bundle["tool_model"] == "claude"


def test_an_unknown_base_model_is_refused_and_a_stored_garbage_value_reads_as_never_chose():
    usu.set_user_reply_model("jack", "gpt")
    assert usu.set_user_reply_model("jack", "claude") is None and usu.set_user_reply_model("jack", "") is None      # Claude is a code model, not a base model
    assert usu.get_user_reply_model("jack") == "gpt"
    with open(usu._get_user_file("alice"), "w") as handle:
        json.dump({"reply_model": "something-old"}, handle)
    assert usu.get_user_reply_model("alice") is None


@pytest.mark.parametrize("who", ["guest_bty", "guest", "guest-recruiter@example.com"])
def test_guests_and_the_embed_always_get_gemini_as_the_base_model(who):
    assert usu.set_user_reply_model(who, "gpt") == "gemini"
    assert usu.get_user_reply_model(who) == "gemini"
    assert usu.get_user_settings_bundle(who)["reply_model"] == "gemini"


# ---- what the chip shows -------------------------------------------------------------------------------------------------

def test_the_base_model_view_offers_gpt_only_where_the_server_can():
    usu.set_user_reply_model("jack", "gpt")
    assert usu.reply_model_view("jack", gpt_ready=False) == {"reply_model": "gemini", "choice": "gpt", "default": "gemini", "available": False, "gpt_available": False, "lite_available": False, "haiku_available": False, "browser_only": False}
    on = usu.reply_model_view("jack", gpt_ready=True)
    assert on["reply_model"] == "gpt" and on["available"] is True and on["gpt_available"] is True
    assert usu.reply_model_view("alice", gpt_ready=True)["reply_model"] == "gemini"                 # never chose: the default
    assert usu.reply_model_view("guest", gpt_ready=True)["available"] is False                      # never for a guest


def test_saving_a_base_model_refuses_what_cannot_be_offered():
    assert usu.save_reply_model_choice("jack", "gpt-9", True) == (None, (400, "reply_model must be 'gemini', 'gpt', 'lite', 'haiku' or 'auto'"))
    view, problem = usu.save_reply_model_choice("jack", "gpt", gpt_ready=False)
    assert view is None and problem[0] == 409 and usu.get_user_reply_model("jack") is None              # nothing saved
    view, problem = usu.save_reply_model_choice("jack", "gpt", gpt_ready=True)
    assert problem is None and view["reply_model"] == "gpt" and usu.get_user_reply_model("jack") == "gpt"
    assert usu.save_reply_model_choice("jack", "gemini", gpt_ready=False)[1] is None                    # going back to Gemini is always allowed


# ---- the code model with GPT among its choices ---------------------------------------------------------------------------

def test_gpt_alone_is_enough_to_offer_the_code_model_chip_and_claude_is_then_hidden():
    view = usu.tool_model_view("jack", claude_ready=False, sonnet_ready=False, gpt_ready=True)
    assert view["available"] is True and view["gpt_available"] is True and view["claude_available"] is False and view["sonnet_available"] is False
    usu.set_user_tool_model("jack", "gpt")
    assert usu.tool_model_view("jack", False, False, True)["tool_model"] == "gpt"
    usu.set_user_tool_model("jack", "claude")
    assert usu.tool_model_view("jack", False, False, True)["tool_model"] == "gemini"      # Claude cannot be offered here: the effective model is Gemini


def test_with_neither_provider_set_up_there_is_nothing_to_choose_and_the_model_is_gemini():
    usu.set_user_tool_model("jack", "gpt")
    view = usu.tool_model_view("jack", claude_ready=False, sonnet_ready=False, gpt_ready=False)
    assert view["available"] is False and view["tool_model"] == "gemini" and view["choice"] == "gpt"


def test_a_stored_gpt_that_can_no_longer_be_offered_is_gemini_while_claude_is_unaffected():
    usu.set_user_tool_model("jack", "gpt")
    assert usu.tool_model_view("jack", claude_ready=True, sonnet_ready=False, gpt_ready=False)["tool_model"] == "gemini"
    usu.set_user_tool_model("jack", "sonnet")
    assert usu.tool_model_view("jack", True, False, True)["tool_model"] == "claude"             # unchanged: Sonnet without a price is served by Haiku


def test_saving_gpt_as_the_code_model_needs_it_to_be_set_up():
    view, problem = usu.save_tool_model_choice("jack", "gpt", claude_ready=True, sonnet_ready=True, gpt_ready=False)
    assert view is None and problem[0] == 409 and usu.get_user_tool_model("jack") is None
    view, problem = usu.save_tool_model_choice("jack", "gpt", claude_ready=False, sonnet_ready=False, gpt_ready=True)
    assert problem is None and view["tool_model"] == "gpt"
    assert usu.save_tool_model_choice("jack", "gpt-9", True)[1][0] == 400 and "gpt" in usu.save_tool_model_choice("jack", "x", True)[1][1]


def test_the_old_three_argument_calls_still_mean_what_they_did():
    view = usu.tool_model_view("jack", True, True)
    assert view["available"] is True and view["sonnet_available"] is True and view["gpt_available"] is False
    assert usu.save_tool_model_choice("jack", "claude", True)[1] is None


# ---- the models themselves -----------------------------------------------------------------------------------------------

def test_openai_is_offered_only_with_a_key_and_a_price(monkeypatch):
    assert models_module.openai_available() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    assert models_module.openai_available() is False                       # a key with no price: its spend would be invisible to the budget guard
    monkeypatch.setenv("LLM_PRICES", PRICED)
    assert models_module.openai_available() is True
    monkeypatch.delenv("OPENAI_API_KEY")
    assert models_module.openai_available() is False                       # a price with no key


def test_the_reply_model_is_gemini_unless_the_person_chose_gpt_and_the_server_offers_it(monkeypatch):
    assert models_module.get_stream_llm("jack") is models_module.stream_llm
    assert models_module.get_stream_llm("jack", choice="gpt") is models_module.stream_llm                  # chose it, not offered: Gemini
    offer_gpt(monkeypatch)
    assert models_module.get_stream_llm("jack") is models_module.stream_llm                               # offered, never chose: Gemini
    chosen = models_module.get_stream_llm("jack", choice="gpt")
    assert isinstance(chosen, OpenAILLM) and chosen.model_name == "gpt-5.4-mini" and chosen.effort is None
    assert chosen.fallback is models_module.stream_llm                                                    # a failure before the first word is answered by Gemini
    assert models_module.get_stream_llm("alice", choice="gpt") is chosen                                   # built once
    assert models_module.get_stream_llm("guest_bty", choice="gpt") is models_module.embedded_llm           # never for the embedded view
    assert models_module.get_stream_llm("jack", choice="gemini") is models_module.stream_llm


def test_the_gpt_code_model_runs_the_tool_loop_at_low_effort_and_falls_back_to_the_gemini_model_it_replaces(monkeypatch):
    assert models_module.tool_agent_llm(False, "gpt") is models_module.lite_llm                           # not offered: unchanged behaviour
    offer_gpt(monkeypatch)
    normal, deep = models_module.tool_agent_llm(False, "gpt"), models_module.tool_agent_llm(True, "gpt")
    assert isinstance(normal, OpenAILLM) and normal.effort == "low" and normal.fallback is models_module.lite_llm
    assert deep.fallback is models_module.lite_llm_deep and deep is not normal
    assert models_module.tool_agent_llm(False, "gpt") is normal
    assert models_module.tool_agent_llm(False, "gemini") is models_module.lite_llm
    assert models_module.tool_agent_llm(False, None) is models_module.lite_llm


def test_the_gpt_reply_and_the_gpt_tool_loop_are_separate_models_with_their_own_effort(monkeypatch):
    offer_gpt(monkeypatch)
    reply, tools = models_module.get_stream_llm("jack", choice="gpt"), models_module.tool_agent_llm(False, "gpt")
    assert reply is not tools and reply.effort is None and tools.effort == "low"


# ---- the app ------------------------------------------------------------------------------------------------------------

def test_the_app_has_signed_in_endpoints_for_the_base_model_and_offers_gpt_only_when_it_can():
    for route in ('@app.get("/api/settings/reply-model")', '@app.put("/api/settings/reply-model")'):
        start = APP.index(route)
        assert "Depends(get_current_user)" in APP[start: start + 260], route
    assert "reply_model_view(username, openai_available(), lite_reply_available(), haiku_reply_available())" in APP
    assert "save_reply_model_choice(username, payload.reply_model, openai_available(), lite_reply_available(), haiku_reply_available())" in APP
    assert "save_tool_model_choice(username, payload.tool_model, claude_available(), sonnet_available(), openai_available())" in APP
    assert "tool_model_view(username, claude_available(), sonnet_available(), openai_available())" in APP
    assert "class ReplyModelUpdate(BaseModel):\n    reply_model: str" in APP


def test_the_chosen_base_model_writes_the_reply_except_inside_a_safety_context():
    assert 'if reply_choice == "gpt":\n        response_llm = get_stream_llm(username, choice="gpt")' in APP
    assert "turn_llm = response_llm if risk_level == RISK_NONE else get_stream_llm(username)" in APP
    assert "tier, tier_reason = reply_tier.pick(reply_choice, lite_ready=lite_reply_available(), haiku_ready=haiku_reply_available(), risk_active=risk_level != RISK_NONE, prompt=prompt, question=question)" in APP     # a safety context is never lite
    assert "response_llm.astream" not in APP                                                      # every stream, a rewrite and a correction included, goes through turn_llm
    assert APP.count("turn_llm.astream(") == 3
