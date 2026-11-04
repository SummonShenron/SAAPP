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

async def _drive(monkeypatch, decision_for, *, edit_decisions=True, before=None, after=None, state_extra=None, question="make the greeting friendlier"):
    """Runs tool_agent_node on the native engine with a fake loop that makes one proposal through the real dispatcher while the browser answers via `decision_for`.
    `before(act, loop_kwargs)` and `after(act, loop_kwargs)` are optional coroutines run around the proposal with the real dispatcher (their results land in seen["before"] and seen["after"]), and `state_extra` adds to the turn's state."""
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
        seen["kwargs"] = kwargs
        if before is not None:
            seen["before"] = await before(kwargs["act"], kwargs)
        helper = asyncio.create_task(answer_when_shown())
        decision = {"tool_action": "propose_local_edits", "args": {"summary": "friendlier greeting", "edits": [_replace("src/app.py", "'hi ' + name", "'hello ' + name")]}}
        seen["observation"] = await kwargs["act"](decision)
        await helper
        if after is not None:
            seen["after"] = await after(kwargs["act"], kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    await aw.tool_agent_node({**_state(question, username="jack"), "edit_decisions": edit_decisions, **(state_extra or {})})
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


async def _read_app(act, kwargs=None):
    return await act({"tool_action": "read_repo_file", "args": {"path": "src/app.py"}})


@run_async
async def test_a_file_read_before_an_applied_change_shows_the_change_when_it_is_read_again_in_the_same_turn(monkeypatch):
    # The turn keeps the files it has read (so the same file is not fetched twice). After an Apply that copy was never refreshed, so a plan's NEXT step that touched the same file read the old text,
    # and an edit written against it was checked against the old text too and then failed to apply in the browser.
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), before=_read_app, after=_read_app)
    assert "'hi ' + name" in seen["before"] and "'hello ' + name" not in seen["before"]
    assert "'hello ' + name" in seen["after"] and "'hi ' + name" not in seen["after"]


@run_async
async def test_a_file_read_before_a_declined_change_is_unchanged_when_read_again(monkeypatch):
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "declined"), before=_read_app, after=_read_app)
    assert seen["before"] == seen["after"] and "'hi ' + name" in seen["after"]


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
    assert "Depends(get_current_user)" in block and "local_edit_decisions.decide(username, proposal_id" in block and "applied_changes.record(username" in block
    assert "edit_decisions: bool = False" in app_source and '"edit_decisions": bool(request.edit_decisions)' in app_source
    card = (ROOT / "local" / "src" / "components" / "LocalEditCard.tsx").read_text(encoding="utf-8")
    assert "onDecision?.(proposal.proposal_id, 'applied'" in card and "onDecision?.(proposal.proposal_id, 'declined'" in card and "proposal.awaiting" in card
    api_source = (ROOT / "local" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "edit_decisions: editDecisions" in api_source and "/decision`" in api_source
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "awaiting: payload.awaiting_decision === true" in chat


def test_the_decisions_module_logs_nothing_the_person_wrote():
    """The one log line allowed is how a wait ended (2026-11-05): a fixed sentence, the word for how it ended, and the seconds. Never the reason, a path, a summary or a person's name."""
    import ast
    source = (ROOT / "backend" / "services" / "local_edit_decisions.py").read_text(encoding="utf-8")
    logs = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug")]
    assert len(logs) == 1, "a log line in the decisions module beyond the one that says how a wait ended"
    call = logs[0]
    assert node_text(call.args[0]) == "[local_edit_decisions] a wait for a decision ended: %s after about %ds."
    assert [ast.unparse(arg) for arg in call.args[1:]] == ["ended", "int(waited)"]


def node_text(node):
    import ast
    return node.value if isinstance(node, ast.Constant) else ast.unparse(node)


def test_an_outcome_is_counted_once_per_proposal_and_only_for_its_owner():
    from backend.services import local_edit_decisions as led

    led.remember_proposal("jack", "p-count", {"a.py": (3, 1)})
    assert led.note_outcome("jack", "p-count", "applied") == {"a.py": (3, 1)}
    assert led.note_outcome("jack", "p-count", "applied") == {}, "the same report twice counts once"
    assert led.note_outcome("jack", "p-count", "undone") == {"a.py": (3, 1)} and led.note_outcome("jack", "p-count", "undone") == {}
    assert led.note_outcome("mallory", "p-count", "declined") == {} and led.note_outcome("jack", "p-count", "sideways") == {} and led.note_outcome("jack", "unknown", "applied") == {}
    led.remember_proposal("jack", "p-empty", {})
    assert led.note_outcome("jack", "p-empty", "declined") == {"": (0, 0)}, "a proposal with no recorded files is still counted (its size reads as small)"


def test_the_decision_endpoint_counts_outcomes_and_the_card_reports_declines_and_undos():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    app = (root / "app.py").read_text(encoding="utf-8")
    assert "local_edit_decisions.note_outcome(username, proposal_id, payload.outcome)" in app and "code_outcomes.outcome_counts(payload.outcome" in app and '"applied", "declined" or "undone"' in app
    card = (root / "local" / "src" / "components" / "LocalEditCard.tsx").read_text(encoding="utf-8")
    assert "onDecision?.(proposal.proposal_id, 'undone', '')" in card and "if (proposal.awaiting) onDecision" not in card
    assert "'applied' | 'declined' | 'undone'" in (root / "local" / "src" / "api.ts").read_text(encoding="utf-8")


# ---- the next plan step's files after an applied edit ------------------------------------------------------------------------------------------

def _approved_plan_two_steps():
    return {"goal": "greeting", "state": "approved", "files": [{"path": "src/app.py", "why": "the greeting"}, {"path": "src/data.json", "why": "its data"}],
            "steps": [{"id": "s1", "text": "Friendlier greeting", "files": ["src/app.py"], "status": "done"},
                      {"id": "s2", "text": "Use it in the data", "files": ["src/app.py", "src/data.json"], "status": "todo"}]}


@run_async
async def test_after_an_applied_change_the_next_plan_steps_files_are_handed_over_as_they_are_now(monkeypatch):
    _connect_folder()
    from backend.utils import self_counters as sc

    monkeypatch.setattr(aw, "get_db", lambda: None)             # the plan store is stubbed below; nothing here may reach a real database
    monkeypatch.setattr(aw.code_plan, "load", lambda db, user, session: _approved_plan_two_steps())
    monkeypatch.setattr(aw.code_plan, "record_applied", lambda db, user, session, paths: 1)
    counted = []
    monkeypatch.setattr(sc, "record_turn_counts", lambda counts, *a, **k: counted.append(counts))

    async def feed(act, kwargs):
        return await kwargs["plan_feed"]()

    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), after=feed, state_extra={"code_plan_phase": "execute", "code_plan_context": "PLAN"}, question="Go ahead with the approved plan.")
    block = seen["after"]
    assert block.startswith("FILES FOR STEP s2") and "--- src/app.py" in block and "--- src/data.json" in block and '{"a": 1}' in block
    assert "'hello ' + name" in block and "'hi ' + name" not in block                       # the text the step just applied, not the old text
    assert any(count.get("code_plan_fed_next") == 1 for count in counted)


@run_async
async def test_a_turn_that_is_not_carrying_out_a_plan_is_given_no_such_hook(monkeypatch):
    _connect_folder()
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"))
    assert seen["kwargs"]["plan_feed"] is None


@run_async
async def test_nothing_is_handed_over_when_the_plan_has_no_step_left_or_reading_the_files_fails(monkeypatch):
    _connect_folder()
    finished = _approved_plan_two_steps()
    finished["steps"][1]["status"] = "done"
    monkeypatch.setattr(aw, "get_db", lambda: None)
    monkeypatch.setattr(aw.code_plan, "load", lambda db, user, session: finished)
    monkeypatch.setattr(aw.code_plan, "record_applied", lambda db, user, session, paths: 1)

    async def feed(act, kwargs):
        return await kwargs["plan_feed"]()

    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), after=feed, state_extra={"code_plan_phase": "execute", "code_plan_context": "PLAN"}, question="Go ahead with the approved plan.")
    assert seen["after"] == ""

    def broken(plan, read):
        raise RuntimeError("could not read")

    monkeypatch.setattr(aw.code_plan, "load", lambda db, user, session: _approved_plan_two_steps())
    monkeypatch.setattr(aw.code_plan, "files_block", broken)
    seen = await _drive(monkeypatch, lambda pid: led.decide("jack", pid, "applied"), after=feed, state_extra={"code_plan_phase": "execute", "code_plan_context": "PLAN"}, question="Go ahead with the approved plan.")
    assert seen["after"] == ""                                                               # it never raises: the turn just goes on without the head start


def test_the_hook_is_given_to_the_native_loop_only_while_a_plan_is_carried_out_and_each_hand_over_is_counted():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "plan_feed=_next_step_files_after_apply if plan_phase == code_plan.EXECUTE_PHASE else None" in source
    assert "_turn_file_cache[path] = text" in source and '{"code_plan_fed_next": 1}' in source
    from backend.utils import self_counters

    assert "code_plan_fed_next" in self_counters.KEYS


# ---- why nobody was waiting (2026-11-05: the turn twice ended the moment Apply was pressed in production, and nothing said why) ---------------------------------

@pytest.fixture
def remembered():
    led._proposals.clear()
    yield
    led._proposals.clear()


@run_async
async def test_a_report_after_the_stream_was_cut_while_waiting_is_told_it_was_cancelled(remembered):
    led.remember_proposal("jack", "p1", {"a.py": (1, 0)})
    led.register("jack", "p1")
    waiter = asyncio.create_task(led.wait_for("p1", timeout=30))
    await asyncio.sleep(0)
    waiter.cancel()                                                                  # the request is cancelled, as when a proxy or the browser drops the stream
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert led.decide("jack", "p1", "applied") == "no_waiter"
    assert led.why_no_waiter("jack", "p1") == "cancelled"


@run_async
async def test_a_report_after_the_wait_ran_out_or_after_a_decision_already_made_says_which(remembered):
    led.remember_proposal("jack", "late", {})
    led.register("jack", "late")
    assert await led.wait_for("late", timeout=0.05) is None
    assert led.why_no_waiter("jack", "late") == "timeout"
    led.remember_proposal("jack", "twice", {})
    led.register("jack", "twice")
    waiter = asyncio.create_task(led.wait_for("twice", timeout=5))
    await asyncio.sleep(0)
    led.decide("jack", "twice", "applied")
    assert (await waiter)["outcome"] == "applied"
    assert led.decide("jack", "twice", "applied") == "no_waiter"                     # the second press of the button
    assert led.why_no_waiter("jack", "twice") == "decided"


def test_a_proposal_this_process_never_held_is_not_here_and_a_strangers_looks_the_same(remembered):
    assert led.why_no_waiter("jack", "never-seen") == "not_here"                     # another instance, a restart, or older than six hours
    led.remember_proposal("jack", "mine", {})
    assert led.why_no_waiter("mallory", "mine") == "not_here"                        # nothing is revealed to someone else
    assert led.why_no_waiter("jack", "mine") == "not_here"                           # remembered but never waited on: no reason on record


def test_only_an_apply_or_decline_with_nobody_waiting_is_counted_and_with_a_key_the_tally_accepts():
    from backend.utils.self_counters import KEYS

    for why in led.NO_WAITER_REASONS:
        counts = led.no_waiter_counts("applied", "no_waiter", why)
        assert counts == {f"apply_no_waiter_{why}": 1} and set(counts) <= KEYS
    assert led.no_waiter_counts("declined", "no_waiter", "cancelled") == {"apply_no_waiter_cancelled": 1}
    assert led.no_waiter_counts("undone", "no_waiter", "not_here") == {}             # an Undo never has a waiter: not a failure
    assert led.no_waiter_counts("applied", "recorded", "cancelled") == {}            # someone was waiting: nothing went wrong
    assert led.no_waiter_counts("applied", "no_waiter", "made-up") == {}


def test_the_endpoint_counts_the_failure_and_returns_the_reason_for_an_apply_or_decline_only():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'why = local_edit_decisions.why_no_waiter(username, proposal_id) if status == "no_waiter" else None' in source
    assert "waiter_counts = local_edit_decisions.no_waiter_counts(payload.outcome, status, why or \"\")" in source
    assert "await asyncio.to_thread(record_turn_counts, waiter_counts)" in source
    assert 'return {"status": status, **({"why": why} if why and payload.outcome in local_edit_decisions.OUTCOMES else {})}' in source
