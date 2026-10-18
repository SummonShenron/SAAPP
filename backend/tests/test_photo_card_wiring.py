import asyncio
import os
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.services import agent_workflow as aw
from backend.services import photo_cards as pc
from backend.utils.app_utils import _deserialize_messages, _serialize_messages
from backend.utils.attachment_utils import attachment_image_visible_to

REF = {"filename": "family.jpg", "gridfs_id": "photo1", "content_type": "image/jpeg"}
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _plan(text, refs=None, history=(), intent="conversational"):
    state = {"messages": [*history, HumanMessage(content=text)], "image_refs": refs or []}
    return aw.build_agent_plan(intent, state)["agents"], state


def test_words_on_an_attached_photo_go_to_the_card_maker_and_then_the_voice():
    agents, state = _plan('put "Merry Christmas" across the bottom in gold', [REF])
    assert agents == ["image_card", "formatter"] and state["last_intent"] == "image_card"


def test_a_change_right_after_a_card_goes_to_the_card_maker():
    reply = AIMessage(content="done", additional_kwargs={"kb_images": [{"filename": "a.jpg", "fileId": "c1", "generated": "card"}]})
    agents, _ = _plan("make it bigger", history=[HumanMessage(content="hi"), reply])
    assert agents == ["image_card", "formatter"]


def test_the_same_words_without_a_card_to_change_are_an_ordinary_message():
    agents, _ = _plan("make it bigger", history=[HumanMessage(content="hi"), AIMessage(content="hello")])
    assert "image_card" not in agents


@pytest.mark.parametrize("text", ["what's the weather like today", "write a message to my boss", "explain how the retry logic works"])
def test_ordinary_messages_never_reach_the_card_maker(text):
    agents, _ = _plan(text, [REF] if "boss" in text else [])
    assert "image_card" not in agents


def test_a_pending_approval_or_cancel_still_wins_over_a_card_request():
    agents, _ = _plan('put "Hi" on this photo', [REF], intent="execute_write")
    assert agents == ["execute_write", "formatter"]
    agents, _ = _plan('put "Hi" on this photo', [REF], intent="cancel_action")
    assert agents == ["conversational"]


def test_the_card_maker_is_a_real_node_in_the_graph():
    assert aw._AGENT_NODE_MAP["image_card"] == "image_card_node"
    assert "image_card_node" in aw.create_workflow(SimpleNamespace()).get_graph().nodes


def test_a_pictures_made_last_turn_are_not_shown_again_this_turn():
    state = aw.reset_transient_state({"generated_images": [{"filename": "a.jpg", "fileId": "c1"}]})
    assert state["generated_images"] == []


# ---------------------------------------------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------------------------------------------

def _node_state(**extra):
    return {"messages": [HumanMessage(content="put Hi on it")], "username": "jack", "session_id": "s1", "image_refs": [REF], **extra}


@pytest.fixture
def node_env(monkeypatch):
    async def quiet(*args, **kwargs):
        return None

    monkeypatch.setattr(aw, "safe_emit_event", quiet)
    monkeypatch.setattr(aw, "get_db", lambda: object())
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")


def test_a_finished_card_is_handed_to_the_voice_as_tool_output_with_its_picture(monkeypatch, node_env):
    images = [{"filename": "hi.jpg", "fileId": "c1", "generated": "card"}]

    async def made(**kwargs):
        assert kwargs["attached"] == REF and kwargs["username"] == "jack" and kwargs["session_id"] == "s1"
        return pc.CardOutcome(pc.MADE, "A card was made.", images)

    monkeypatch.setattr(pc, "make_card", made)
    result = asyncio.run(aw.image_card_node(_node_state()))
    assert result["relevance_grade"] == "tool_agent" and result["content_to_format"] == "A card was made." and result["generated_images"] == images


def test_a_message_that_was_not_about_a_picture_is_answered_normally(monkeypatch, node_env):
    async def not_for_me(**kwargs):
        return pc.CardOutcome(pc.NOT_FOR_ME)

    monkeypatch.setattr(pc, "make_card", not_for_me)
    result = asyncio.run(aw.image_card_node(_node_state()))
    assert result["relevance_grade"] == "conversational" and not result.get("generated_images")


def test_a_problem_is_explained_by_the_voice_with_no_picture(monkeypatch, node_env):
    async def no_photo(**kwargs):
        return pc.CardOutcome(pc.NO_IMAGE, "There is no photo here.")

    monkeypatch.setattr(pc, "make_card", no_photo)
    result = asyncio.run(aw.image_card_node(_node_state(image_refs=[])))
    assert result["content_to_format"] == "There is no photo here." and result["generated_images"] == []


def test_guests_are_told_to_sign_in_and_nothing_is_stored(monkeypatch, node_env):
    async def never(**kwargs):
        raise AssertionError("a guest must never reach the card maker")

    monkeypatch.setattr(pc, "make_card", never)
    result = asyncio.run(aw.image_card_node(_node_state(username="guest-recruiter@example.com")))
    assert "sign in" in result["content_to_format"] and not result.get("generated_images")


def test_without_a_database_it_says_so(monkeypatch, node_env):
    monkeypatch.setattr(aw, "get_db", lambda: None)
    result = asyncio.run(aw.image_card_node(_node_state()))
    assert "can't be stored" in result["content_to_format"]


# ---------------------------------------------------------------------------------------------------------------
# The app around it
# ---------------------------------------------------------------------------------------------------------------

def test_a_chat_attachment_is_visible_only_to_its_owner():
    mine = {"metadata": {"kind": "chat_attachment", "username": "jack"}}
    assert attachment_image_visible_to(mine, "jack") is True
    assert attachment_image_visible_to(mine, "alice") is False
    assert attachment_image_visible_to(mine, None) is False
    assert attachment_image_visible_to(mine, "") is False


def test_a_library_image_is_still_visible_to_everyone_signed_in():
    assert attachment_image_visible_to({"metadata": {"content_type": "image/png"}}, "alice") is True
    assert attachment_image_visible_to({}, "alice") is True
    assert attachment_image_visible_to(None, "alice") is True


def test_the_image_endpoint_uses_the_ownership_check():
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    start = source.index('@app.get("/api/attachments/image/{file_id}")')
    handler = source[start:source.index("@app.", start + 10)]
    assert "attachment_image_visible_to(file_doc, current_user.get(\"sub\"))" in handler


def test_the_chat_turn_passes_the_attached_photos_in_and_shows_what_was_made():
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert 'initial_state["image_refs"] = attachment_refs' in source
    assert 'final_state.get("generated_images")' in source
    assert "library_images" in source          # a made card is not described by the library-image note


def test_a_card_is_still_known_as_a_card_after_the_conversation_is_reloaded():
    reply = AIMessage(content="here", additional_kwargs={"kb_images": [{"filename": "a.jpg", "fileId": "c1", "generated": "card"}]})
    reloaded = _deserialize_messages(_serialize_messages([HumanMessage(content="hi"), reply]))
    assert pc.made_card_recently(reloaded) is True


# ---------------------------------------------------------------------------------------------------------------
# The picture maker
# ---------------------------------------------------------------------------------------------------------------

def _image_reply():
    return AIMessage(content="here", additional_kwargs={"kb_images": [{"filename": "a.jpg", "fileId": "i1", "generated": "image"}]})


def test_a_request_to_make_a_picture_goes_to_the_picture_maker():
    for text in ("generate an image of a snowy stadium at dusk", "make me a christmas card with a snowy kinnick stadium", "draw a cartoon hawk"):
        assert _plan(text)[0] == ["image_gen", "formatter"], text


def test_a_change_to_the_last_picture_goes_to_the_picture_maker_and_words_on_it_go_to_the_card_maker():
    history = [HumanMessage(content="hi"), _image_reply()]
    assert _plan("make it snowier", history=history)[0] == ["image_gen", "formatter"]
    assert _plan('put "Merry Christmas" on it across the bottom in gold', history=history)[0] == ["image_card", "formatter"]


def test_requests_about_an_attached_photo_or_a_library_picture_never_reach_the_picture_maker():
    assert "image_gen" not in _plan("draw a picture of a dog", [REF])[0]
    assert "image_gen" not in _plan("show me a picture of the lease")[0]
    assert "image_gen" not in _plan("build a docker image for the api")[0]


def test_a_pending_approval_still_wins_over_a_picture_request():
    assert _plan("generate an image of a dog", intent="execute_write")[0] == ["execute_write", "formatter"]


def test_the_picture_maker_is_a_real_node_in_the_graph():
    assert aw._AGENT_NODE_MAP["image_gen"] == "image_gen_node"
    assert "image_gen_node" in aw.create_workflow(SimpleNamespace()).get_graph().nodes


def test_low_power_mode_lists_picture_making_as_paused():
    from backend.utils import budget_guard

    assert "making pictures" in budget_guard.PAUSED_FEATURES


def _gen_state(**extra):
    return {"messages": [HumanMessage(content="make a picture of a lake")], "username": "jack", "session_id": "s1", "budget_level": "ok", **extra}


def test_a_made_picture_is_handed_to_the_voice_with_its_picture(monkeypatch, node_env):
    from backend.services import image_gen

    images = [{"filename": "lake.jpg", "fileId": "i1", "generated": "image"}]

    async def made(**kwargs):
        assert kwargs["username"] == "jack" and kwargs["session_id"] == "s1"
        return pc.CardOutcome(image_gen.MADE, "A picture was made.", images)

    monkeypatch.setattr(image_gen, "make_image", made)
    result = asyncio.run(aw.image_gen_node(_gen_state()))
    assert result["relevance_grade"] == "tool_agent" and result["content_to_format"] == "A picture was made." and result["generated_images"] == images


def test_a_message_that_was_not_a_picture_request_is_answered_normally(monkeypatch, node_env):
    from backend.services import image_gen

    async def not_for_me(**kwargs):
        return pc.CardOutcome(image_gen.NOT_FOR_ME)

    monkeypatch.setattr(image_gen, "make_image", not_for_me)
    assert asyncio.run(aw.image_gen_node(_gen_state()))["relevance_grade"] == "conversational"


def test_guests_never_reach_the_image_model(monkeypatch, node_env):
    from backend.services import image_gen

    async def never(**kwargs):
        raise AssertionError("a guest must never cost an image")

    monkeypatch.setattr(image_gen, "make_image", never)
    result = asyncio.run(aw.image_gen_node(_gen_state(username="guest-recruiter@example.com")))
    assert "sign in" in result["content_to_format"] and not result.get("generated_images")


def test_low_power_mode_pauses_picture_making_before_anything_is_spent(monkeypatch, node_env):
    from backend.services import image_gen

    async def never(**kwargs):
        raise AssertionError("must not run while paused")

    monkeypatch.setattr(image_gen, "make_image", never)
    for level in ("throttled", "blocked"):
        result = asyncio.run(aw.image_gen_node(_gen_state(budget_level=level)))
        assert "paused" in result["content_to_format"]


def test_without_a_database_the_picture_maker_says_so(monkeypatch, node_env):
    monkeypatch.setattr(aw, "get_db", lambda: None)
    assert "can't be stored" in asyncio.run(aw.image_gen_node(_gen_state()))["content_to_format"]
