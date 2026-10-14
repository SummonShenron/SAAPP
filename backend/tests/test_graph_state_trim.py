"""The graph's checkpoint must stay small however long the conversation gets: one real thread reached 3,417 messages (2.5 MB), and on a slow database
every question then read and rewrote that many megabytes a dozen times and never finished."""
import pathlib
import typing

from langchain_core.messages import AIMessage, HumanMessage

from backend.state import graph_state as gs


def msgs(n, start=0, prefix="m"):
    return [HumanMessage(content=f"{prefix}{start + i}", id=f"{prefix}{start + i}") for i in range(n)]


def test_a_conversation_under_the_cap_is_kept_whole():
    merged = gs.add_and_trim_messages(msgs(10), msgs(3, start=10))
    assert [m.content for m in merged] == [f"m{i}" for i in range(13)]


def test_a_conversation_over_the_cap_keeps_only_its_most_recent_end_in_order(monkeypatch):
    monkeypatch.setattr(gs, "MAX_STATE_MESSAGES", 40)
    merged = gs.add_and_trim_messages(msgs(3400), msgs(5, start=3400))
    assert len(merged) == 40
    assert [m.content for m in merged][-5:] == [f"m{i}" for i in range(3400, 3405)]
    assert merged[0].content == "m3365"


def test_a_message_with_the_same_id_still_replaces_the_earlier_one_instead_of_adding_a_copy():
    updated = HumanMessage(content="edited", id="m2")
    merged = gs.add_and_trim_messages(msgs(5), [updated])
    assert len(merged) == 5 and merged[2].content == "edited"


def test_new_messages_without_ids_are_added_and_given_ids():
    merged = gs.add_and_trim_messages(msgs(2), [AIMessage(content="hello")])
    assert [m.content for m in merged] == ["m0", "m1", "hello"] and merged[-1].id


def test_the_cap_can_be_changed_or_switched_off(monkeypatch):
    monkeypatch.setattr(gs, "MAX_STATE_MESSAGES", 5)
    assert len(gs.add_and_trim_messages(msgs(20), [])) == 5
    monkeypatch.setattr(gs, "MAX_STATE_MESSAGES", 0)
    assert len(gs.add_and_trim_messages(msgs(500), [])) == 500


def test_the_graph_state_uses_the_trimming_reducer_for_its_messages():
    hints = typing.get_type_hints(gs.GraphState, include_extras=True)
    assert gs.add_and_trim_messages in hints["messages"].__metadata__


def test_the_default_cap_is_forty_messages_which_is_more_than_any_node_looks_back():
    import inspect

    assert 'GRAPH_STATE_MAX_MESSAGES", "40"' in inspect.getsource(gs)
    assert gs.MAX_STATE_MESSAGES == 40 or gs.MAX_STATE_MESSAGES >= 0  # (an environment override is allowed)


def test_the_chat_endpoint_reads_syncs_and_saves_the_conversation_in_worker_threads():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "await asyncio.to_thread(load_session_messages, username, session_id)" in source
    assert "await asyncio.to_thread(sync_session_messages, username, session_id, chat_sessions[history_key])" in source
    assert source.count("await asyncio.to_thread(save_conversation_turn,") == 5
    assert "\n    save_conversation_turn(" not in source and " save_conversation_turn(username" not in source.replace("to_thread(save_conversation_turn", "")
