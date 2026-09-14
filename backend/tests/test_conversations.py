import pytest
from langchain_core.messages import HumanMessage, AIMessage

from backend.utils import app_utils


@pytest.fixture(autouse=True)
def isolated_conversations_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir and forces get_db() to None
    so every test exercises the local-file fallback path deterministically."""
    monkeypatch.setattr(app_utils, "CONVERSATIONS_DIR", str(tmp_path))
    monkeypatch.setattr(app_utils, "get_db", lambda: None)
    yield


def test_save_conversation_turn_creates_and_titles_new_conversation():
    messages = [HumanMessage(content="What is the capital of France?"), AIMessage(content="Paris.")]
    doc = app_utils.save_conversation_turn("jack", "conv-1", messages)

    assert doc["session_id"] == "conv-1"
    assert doc["title"] == "What is the capital of France?"
    assert doc["messages"] == [
        {"type": "human", "content": "What is the capital of France?"},
        {"type": "ai", "content": "Paris."},
    ]

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1
    assert conversations[0]["session_id"] == "conv-1"


def test_save_conversation_turn_preserves_title_and_created_at_on_update():
    first = app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="First question")])
    second = app_utils.save_conversation_turn(
        "jack", "conv-1",
        [HumanMessage(content="First question"), AIMessage(content="An answer"), HumanMessage(content="Follow-up")]
    )

    assert second["title"] == first["title"] == "First question"
    assert second["created_at"] == first["created_at"]
    assert len(second["messages"]) == 3

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1  # updated in place, not duplicated


def test_save_conversation_turn_with_no_human_message_defaults_title():
    doc = app_utils.save_conversation_turn("jack", "conv-1", [])
    assert doc["title"] == "New conversation"


def test_clear_chat_semantics_empties_messages_but_keeps_title():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Remember this title")])
    cleared = app_utils.save_conversation_turn("jack", "conv-1", [])

    assert cleared["title"] == "Remember this title"
    assert cleared["messages"] == []

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1
    assert conversations[0]["messages"] == []


def test_conversations_are_scoped_per_username():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Jack's message")])
    app_utils.save_conversation_turn("alice", "conv-2", [HumanMessage(content="Alice's message")])

    jack_convos = app_utils.load_user_conversations("jack")
    alice_convos = app_utils.load_user_conversations("alice")
    assert [c["session_id"] for c in jack_convos] == ["conv-1"]
    assert [c["session_id"] for c in alice_convos] == ["conv-2"]


def test_multiple_conversations_per_user_are_independent():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Thread one")])
    app_utils.save_conversation_turn("jack", "conv-2", [HumanMessage(content="Thread two")])

    conversations = app_utils.load_user_conversations("jack")
    session_ids = {c["session_id"] for c in conversations}
    assert session_ids == {"conv-1", "conv-2"}


def test_delete_user_conversation():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Hello")])

    assert app_utils.delete_user_conversation("jack", "conv-1") is True
    assert app_utils.load_user_conversations("jack") == []
    assert app_utils.delete_user_conversation("jack", "conv-1") is False


def test_load_chat_history_reconstructs_langchain_messages():
    app_utils.save_conversation_turn(
        "jack", "conv-1",
        [HumanMessage(content="Hi"), AIMessage(content="Hello there")]
    )

    sessions = app_utils.load_chat_history()
    assert "jack::conv-1" in sessions
    restored = sessions["jack::conv-1"]
    assert len(restored) == 2
    assert isinstance(restored[0], HumanMessage) and restored[0].content == "Hi"
    assert isinstance(restored[1], AIMessage) and restored[1].content == "Hello there"
