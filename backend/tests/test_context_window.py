"""The context ring's numbers (backend/utils/context_window.py): the biggest request of a turn against its model's window, counts only."""
import pathlib

from backend.utils import context_window as cw
from backend.utils import llm_turns

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_windows_come_from_the_model_family_and_an_unknown_model_has_none(monkeypatch):
    monkeypatch.delenv("CONTEXT_WINDOW_TOKENS", raising=False)
    assert cw.window_for("gemini-3.6-flash") == 1_048_576 and cw.window_for("Gemini-3.1-Flash-Lite") == 1_048_576
    assert cw.window_for("claude-haiku-5-5") == 200_000
    assert cw.window_for("something-new") is None and cw.window_for("") is None and cw.window_for(None) is None


def test_one_setting_overrides_every_model_and_nonsense_is_ignored(monkeypatch):
    monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", "64000")
    assert cw.window_for("gemini-3.6-flash") == 64_000 and cw.window_for("something-new") == 64_000
    for bad in ("", "abc", "-5", "0"):
        monkeypatch.setenv("CONTEXT_WINDOW_TOKENS", bad)
        assert cw.window_for("claude-x") == 200_000


def test_the_view_is_used_window_and_fraction_and_nothing_when_unknown(monkeypatch):
    monkeypatch.delenv("CONTEXT_WINDOW_TOKENS", raising=False)
    assert cw.view(52_428, "gemini-3.6-flash") == {"used": 52_428, "window": 1_048_576, "fraction": 0.05}
    assert cw.view(300_000, "claude-haiku-5-5")["fraction"] == 1.0                      # over the window is capped, not > 1
    assert cw.view(0, "gemini-3.6-flash") is None and cw.view(None, "gemini-3.6-flash") is None and cw.view("x", "gemini-3.6-flash") is None
    assert cw.view(1000, "something-new") is None


def test_a_turn_keeps_its_biggest_request_and_the_model_that_answered_it():
    turn = llm_turns.begin("jack")
    try:
        llm_turns.note_call({"input": 1200, "output": 10}, "gemini-3.1-flash-lite")
        llm_turns.note_call({"input": 48_000, "output": 20}, "gemini-3.6-flash")
        llm_turns.note_call({"input": 9000, "output": 5}, "gemini-3.1-flash-lite")
        assert (turn.peak_input, turn.peak_model, turn.calls) == (48_000, "gemini-3.6-flash", 3)
        assert turn.input == 1200 + 48_000 + 9000                                        # the total is unchanged
    finally:
        turn.done = True


def test_a_turn_with_no_calls_has_no_ring():
    turn = llm_turns.begin("jack")
    try:
        assert cw.view(turn.peak_input, turn.peak_model) is None
    finally:
        turn.done = True


def test_the_chat_stream_sends_the_event_once_before_the_final_answer_and_it_carries_only_counts():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    sent = source.index("context_window.view(llm_turn.peak_input, llm_turn.peak_model)")
    assert sent < source.index("'event': 'final_generation', 'text': full_response", sent) and "'event': 'context', **context_view" in source
    assert set(cw.view(1000, "gemini-3.6-flash")) == {"used", "window", "fraction"}


def test_the_module_logs_and_stores_nothing():
    import ast

    tree = ast.parse((ROOT / "backend" / "utils" / "context_window.py").read_text(encoding="utf-8"))
    calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {"info", "warning", "error", "exception", "debug", "insert_one", "update_one", "open", "write", "print"}
