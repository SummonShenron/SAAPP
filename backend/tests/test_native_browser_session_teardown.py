"""The cloud page session is held for a whole turn and closed when the turn ends.

Native page-driving turns reuse the same holder as the old engine, so the close in the
`finally` of tool_agent_node (agent_workflow.py) must stay. This reads the source: it does not
run a turn.
"""
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "services" / "agent_workflow.py").read_text(encoding="utf-8")


def test_the_session_is_held_in_a_per_turn_holder_and_created_only_once():
    assert 'browser_session_holder: dict = {"session": None, "live_view_emitted": False}' in SOURCE
    assert 'if browser_session_holder["session"] is None:' in SOURCE


def test_the_held_session_is_closed_in_a_finally_when_the_turn_ends():
    close_at = SOURCE.index("await browser_session.close()")
    finally_at = SOURCE.rindex("finally:", 0, close_at)
    between = SOURCE[finally_at:close_at]
    assert 'browser_session_holder.get("session")' in between
    assert "if browser_session is not None:" in between


def test_a_failed_close_is_logged_without_user_content_and_does_not_break_the_turn():
    close_at = SOURCE.index("await browser_session.close()")
    window = SOURCE[close_at:close_at + 250]
    assert "except Exception:" in window
    assert 'logger.exception("[tool_agent_node] failed to close browser session cleanly.")' in window
