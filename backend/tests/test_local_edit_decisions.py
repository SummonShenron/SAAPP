"""Waiting for the person's Apply or Decline on a proposed folder change, and carrying on with it (backend/services/local_edit_decisions.py, tool_agent_node)."""
import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_edit_decisions as led
from backend.services import local_workspace as lw
from backend.services import repo_checkout as rc
from backend.tests.test_local_edits import _connect_folder, _replace, run_async

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    led._waiting.clear()
    yield
    led._waiting.clear()


# ---- the registry ----------------------------------------------------------------------------------------------------------------------------

@run_async
async def test_a_decision_wakes_the_waiter_with_what_the_person_said():
    led.register("jack", "p1")
    waiter = asyncio.create_task(led.wait_for("p1", timeout=5))
    await asyncio.sleep(0)
    assert led.decide("jack", "p1", "declined", "  too\nbig a change  ") == "recorded"
    assert await waiter == {"outcome": "declined", "reason": "too big a change"}
    assert led.waiting_count() == 0                                                  # forgotten once answered


@run_async
async def test_only_the_person_it_was_made_for_can_decide_and_a_stranger_learns_nothing():
    led.register("jack", "p1")
    assert led.decide("mallory", "p1", "applied") == "no_waiter" and led.decide("jack", "nope", "applied") == "no_waiter" and led.decide("jack", "p1", "maybe") == "no_waiter"
    assert await led.wait_for("p1", timeout=0.05) is None                            # nothing valid arrived


@run_async
async def test_a_decision_that_arrives_before_the_wait_starts_is_not_lost():
    led.register("jack", "p1")
    assert led.decide("jack", "p1", "applied") == "recorded"
    assert (await led.wait_for("p1", timeout=1))["outcome"] == "applied"


@run_async
async def test_no_answer_in_time_gives_none_ticks_while_waiting_and_a_late_decision_finds_no_waiter(monkeypatch):
    monkeypatch.setattr(led, "HEARTBEAT_SECONDS", 0.02)
    led.register("jack", "p1")
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)

    assert await led.wait_for("p1", timeout=0.1, on_tick=tick) is None
    assert ticks and led.decide("jack", "p1", "applied") == "no_waiter"


@run_async
async def test_a_cancelled_turn_stops_waiting_and_leaves_nothing_behind():
    led.register("jack", "p1")
    waiter = asyncio.create_task(led.wait_for("p1", timeout=30))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert led.waiting_count() == 0


def test_the_wait_is_ten_minutes_by_default_and_can_be_switched_off():
    source = (ROOT / "backend" / "services" / "local_edit_decisions.py").read_text(encoding="utf-8")
    assert '"LOCAL_EDIT_WAIT_SECONDS", "600"' in source and "LOCAL_EDIT_WAIT" in source


# ---- through the tool agent --------------------------------------------------------------------------------------------------------------

async def _drive(monkeypatch, decision_for, *, edit_decisions=True):
    """Runs tool_agent_node on the native engine with a fake loop that makes one proposal through the real dispatcher while the browser answers via `decision_for`."""
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {}

    async def answer_when_shown():
        for _ in range(400):
            shown = [c.args[1] for c in emit.call_args_list if c.args[0] == "local_edit_proposal"]
            if shown:
                seen["card"] = shown[0]
                decision_for(shown[0]["proposal_id"])
                return
            await asyncio.sleep(0.005)

    async def fake_native_run(**kwargs):
        helper = asyncio.create_task(answer_when_shown())
        decision = {"tool_action": "propose_local_edits", "args": {"summary": "friendlier greeting", "edits": [_replace("src/app.py", "'hi ' + name", "'hello ' + name")]}}
        seen["observation"] = await kwargs["act"](decision)
        await helper
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    await aw.tool_agent_node({**_state("make the greeting friendlier", username="jack"), "edit_decisions": edit_decisions})
    return seen


def _text_on_server(path):
    return (pathlib.Path(lw.get_workspace_handle("jack").root) / path).read_text(encoding="utf-8")


@run_async
async def test_applied_tells_the_loop_so_and_brings_the_servers_copy_up_to_date(monkeypatch):
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"))
    assert seen["card"]["awaiting_decision"] is True
    assert "APPLIED" in seen["observation"] and "src/app.py" in seen["observation"] and "read the changed files to check them" in seen["observation"]
    assert "'hello ' + name" in _text_on_server("src/app.py") and "'hi ' + name" not in _text_on_server("src/app.py")      # a read after this sees the change


@run_async
async def test_declined_tells_the_loop_why_and_changes_nothing_on_the_server(monkeypatch):
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "declined", "keep it formal"))
    assert "DECLINED" in seen["observation"] and "keep it formal" in seen["observation"] and "Do not propose the same change again" in seen["observation"]
    assert "'hi ' + name" in _text_on_server("src/app.py")


@run_async
async def test_no_answer_in_time_says_nothing_changed_and_to_stop(monkeypatch):
    _connect_folder()
    monkeypatch.setattr(led, "WAIT_SECONDS", 0.05)
    seen = await _drive(monkeypatch, lambda pid: None)
    assert "NO DECISION" in seen["observation"] and "nothing was changed" in seen["observation"] and "'hi ' + name" in _text_on_server("src/app.py")


@run_async
async def test_a_page_that_cannot_report_a_decision_gets_the_old_behaviour_and_no_wait(monkeypatch):
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: None, edit_decisions=False)
    assert seen["card"]["awaiting_decision"] is False and "nothing has been changed yet" in seen["observation"] and led.waiting_count() == 0


@run_async
async def test_the_old_engine_never_waits(monkeypatch):
    _connect_folder()
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {}

    async def fake_react(**kwargs):
        seen["observation"] = await kwargs["act"]({"tool_action": "propose_local_edits", "args": {"edits": [_replace("src/app.py", "'hi ' + name", "'hello ' + name")]}})
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_react)
    await aw.tool_agent_node({**_state("make the greeting friendlier", username="jack"), "edit_decisions": True})
    assert "nothing has been changed yet" in seen["observation"] and led.waiting_count() == 0


# ---- the wiring ----------------------------------------------------------------------------------------------------------------------------

def test_the_endpoint_the_request_flag_and_the_card_are_wired():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app_source[app_source.index('@app.post("/api/local-edits/{proposal_id}/decision")'):app_source.index('@app.get("/api/reading-shelf")')]
    assert "Depends(get_current_user)" in block and 'local_edit_decisions.decide(current_user.get("sub")' in block
    assert "edit_decisions: bool = False" in app_source and '"edit_decisions": bool(request.edit_decisions)' in app_source
    card = (ROOT / "local" / "src" / "components" / "LocalEditCard.tsx").read_text(encoding="utf-8")
    assert "onDecision?.(proposal.proposal_id, 'applied'" in card and "onDecision?.(proposal.proposal_id, 'declined'" in card and "proposal.awaiting" in card
    api_source = (ROOT / "local" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "edit_decisions: editDecisions" in api_source and "/decision`" in api_source
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "awaiting: payload.awaiting_decision === true" in chat


def test_the_decisions_module_logs_nothing_the_person_wrote():
    import ast
    tree = ast.parse((ROOT / "backend" / "services" / "local_edit_decisions.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            raise AssertionError("a log line in the decisions module")
