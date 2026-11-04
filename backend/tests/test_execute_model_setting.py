"""The execute model setting (backend/utils/user_settings_utils.py): which model carries out an approved plan. "same" (the default) means the person's own Code model."""
import os

import pytest

from backend.utils import user_settings_utils as usu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Local-file settings in a temp folder and no database."""
    monkeypatch.setattr(usu, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(usu, "get_db", lambda: None)


# ---------------------------------------------------------------------------------------------------------------
# What is stored
# ---------------------------------------------------------------------------------------------------------------

def test_a_person_who_never_chose_has_none_which_means_the_same_as_their_code_model():
    assert usu.get_user_execute_model("jack") is None
    assert usu.get_user_settings_bundle("jack")["execute_model"] is None


def test_the_choice_is_saved_per_person_and_same_reads_back_as_none():
    assert usu.set_user_execute_model("jack", "claude") == "claude"
    usu.set_user_execute_model("alice", "gpt")
    assert usu.get_user_execute_model("jack") == "claude" and usu.get_user_execute_model("alice") == "gpt"
    assert usu.get_user_settings_bundle("jack")["execute_model"] == "claude"
    assert usu.set_user_execute_model("jack", "same") == "same"
    assert usu.get_user_execute_model("jack") is None                                     # "same" is the absence of an override


def test_anything_that_is_not_a_known_value_is_refused_and_changes_nothing():
    usu.set_user_execute_model("jack", "claude")
    for bad in ("gpt-9", "", "haiku", "SAME"):
        assert usu.set_user_execute_model("jack", bad) is None, bad
    assert usu.get_user_execute_model("jack") == "claude"


def test_a_stored_value_that_is_not_a_model_reads_as_the_same():
    import json

    with open(usu._get_user_file("jack"), "w") as handle:
        json.dump({"execute_model": "something-old"}, handle)
    assert usu.get_user_execute_model("jack") is None


def test_setting_it_does_not_disturb_the_other_model_settings():
    usu.set_user_tool_model("jack", "sonnet")
    usu.set_user_reply_model("jack", "gpt")
    usu.set_user_execute_model("jack", "claude")
    assert usu.get_user_tool_model("jack") == "sonnet" and usu.get_user_reply_model("jack") == "gpt" and usu.get_user_execute_model("jack") == "claude"


def test_a_guest_and_the_embed_have_no_choice_and_get_none():
    for locked in ("guest", "guest_bty", "guest-recruiter@example.com"):
        assert usu.set_user_execute_model(locked, "claude") == "same", locked
        assert usu.get_user_execute_model(locked) is None, locked
        assert usu.get_user_settings_bundle(locked)["execute_model"] is None, locked


# ---------------------------------------------------------------------------------------------------------------
# What the model chip is shown
# ---------------------------------------------------------------------------------------------------------------

def test_the_view_says_what_is_in_effect_and_what_can_be_offered():
    view = usu.execute_model_view("jack", True, True, False)
    assert view == {"execute_model": "same", "choice": None, "default": "same", "available": True, "claude_available": True, "sonnet_available": True, "gpt_available": False}
    usu.set_user_execute_model("jack", "claude")
    assert usu.execute_model_view("jack", True, True, False)["execute_model"] == "claude"


def test_a_stored_model_that_can_no_longer_be_offered_shows_as_the_same():
    usu.set_user_execute_model("jack", "sonnet")
    assert usu.execute_model_view("jack", True, False, False)["execute_model"] == "same"      # Sonnet lost its price
    assert usu.execute_model_view("jack", False, False, False)["execute_model"] == "same"     # Claude lost its key
    usu.set_user_execute_model("jack", "gpt")
    assert usu.execute_model_view("jack", True, True, False)["execute_model"] == "same"
    assert usu.execute_model_view("jack", True, True, False)["choice"] == "gpt"               # what they chose is still kept


def test_nothing_is_offered_where_no_provider_is_set_up_or_to_a_guest():
    assert usu.execute_model_view("jack", False, False, False)["available"] is False
    assert usu.execute_model_view("guest", True, True, True) == {"execute_model": "same", "choice": None, "default": "same", "available": False,
                                                                 "claude_available": False, "sonnet_available": False, "gpt_available": False}


# ---------------------------------------------------------------------------------------------------------------
# What the endpoint does with a request
# ---------------------------------------------------------------------------------------------------------------

def test_a_valid_choice_is_saved_and_the_new_view_returned():
    view, problem = usu.save_execute_model_choice("jack", "claude", True, True, False)
    assert problem is None and view["execute_model"] == "claude" and usu.get_user_execute_model("jack") == "claude"
    view, problem = usu.save_execute_model_choice("jack", "same", True, True, False)
    assert problem is None and view["execute_model"] == "same"


def test_an_unknown_value_is_a_400_and_a_provider_that_is_not_set_up_is_a_409():
    assert usu.save_execute_model_choice("jack", "gpt-9", True, True, True)[1][0] == 400
    assert usu.save_execute_model_choice("jack", "claude", False, False, False)[1][0] == 409
    assert usu.save_execute_model_choice("jack", "sonnet", True, False, False)[1][0] == 409       # Claude is set up but Sonnet has no price
    assert usu.save_execute_model_choice("jack", "gpt", True, True, False)[1][0] == 409
    assert usu.get_user_execute_model("jack") is None                                           # nothing was saved by any refusal


def test_gemini_needs_nothing_and_same_is_always_allowed():
    assert usu.save_execute_model_choice("jack", "gemini", False, False, False)[1] is None
    assert usu.save_execute_model_choice("jack", "same", False, False, False)[1] is None


def test_the_endpoints_exist_and_pass_the_providers_that_are_set_up():
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert '@app.get("/api/settings/execute-model")' in source and '@app.put("/api/settings/execute-model")' in source
    assert "return execute_model_view(username, claude_available(), sonnet_available(), openai_available())" in source
    assert "view, problem = save_execute_model_choice(username, payload.execute_model, claude_available(), sonnet_available(), openai_available())" in source
    assert "raise HTTPException(status_code=problem[0], detail=problem[1])" in source
