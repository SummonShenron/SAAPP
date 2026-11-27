"""A turn that is waiting for a person's press or answer ends when they press Stop or send a new message instead (2026-11-27), and is NOT ended by the browser going away.

Why: since the detached turns (backend/utils/detached_stream.py) a lost connection no longer cancels the turn, and Stop was only a flag read between graph events, so a turn blocked on a card
could not be stopped and the conversation answered 409 for up to ten minutes. The four waits (change cards, choice cards, edit Apply/Decline, runner checks) now listen to the turn's
interrupt (backend/services/steering.py wait_event). A closed tab or locked phone must still leave the card waiting: that is what the detached turn is for. No model, no database."""
import asyncio
import pathlib
import time

import pytest

from backend.services import change_cards as cards
from backend.services import choice_cards as choices
from backend.services import local_edit_decisions as edits
from backend.services import steering
from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_runner_jobs import connected
from backend.utils import detached_stream
from backend.utils import runner_jobs as rj

ROOT = pathlib.Path(__file__).resolve().parents[2]
KEY = "jack::s1"


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


@pytest.fixture(autouse=True)
def _clean():
    cards._waiting.clear(); choices._waiting.clear(); edits._waiting.clear()
    yield
    cards._waiting.clear(); choices._waiting.clear(); edits._waiting.clear()


def _change_proposal():
    return {"proposal_id": "p1", "username": "jack", "subject": "calendar", "title": "Dentist", "apply": lambda p: "done",
            "card": {"action": "delete", "heading": "h", "apply_label": "a", "title": "Dentist", "rows": [], "notice": "", "destructive": True}}


def _ticker():
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)
    return ticks, tick


async def _in_a_turn(wait, after, body_time=0.1):
    """Runs `wait()` as a turn registered with steering (as steerable_turn does), calls `after()` once it is waiting, and returns (what the wait returned, seconds it took)."""
    seen = {}

    async def produce():
        run = steering.open_run(KEY, [])
        try:
            started = time.monotonic()
            seen["result"] = await wait()
            seen["took"] = time.monotonic() - started
            yield "done"
        finally:
            steering.close_run(KEY, run)

    stream = detached_stream.relay(produce)
    reader = asyncio.create_task(_drain(stream))
    await asyncio.sleep(body_time)
    await after()
    await reader
    return seen["result"], seen["took"]


async def _drain(stream):
    async for _ in stream:
        pass


# ---- Stop ends each wait ---------------------------------------------------------------------------------------------------------------------

def test_stop_ends_a_change_card_wait_at_once_and_nothing_is_applied():
    applied = []
    proposal = _change_proposal()
    proposal["apply"] = lambda p: applied.append(1) or "done"
    cards.register(proposal)

    async def stop():
        assert steering.request_stop(KEY) == "stopping"

    ticks, tick = _ticker()
    result, took = _run(_in_a_turn(lambda: cards.wait_for("p1", timeout=30, on_tick=tick), stop))
    assert result is None and took < 2 and applied == [] and cards.waiting_count() == 0
    assert ticks == []                                          # it ended as stopped, not as a wait that merely ran out of time (a tick would say "still waiting")


def test_stop_ends_a_choice_card_wait():
    card, _ = choices.validate_card({"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]})
    choices.register("jack", card)

    async def stop():
        steering.request_stop(KEY)

    ticks, tick = _ticker()
    result, took = _run(_in_a_turn(lambda: choices.wait_for(card["choice_id"], timeout=30, on_tick=tick), stop))
    assert result is None and took < 2 and choices.waiting_count() == 0 and ticks == []


def test_stop_ends_an_edit_decision_wait():
    edits.register("jack", "e1")

    async def stop():
        steering.request_stop(KEY)

    ticks, tick = _ticker()
    result, took = _run(_in_a_turn(lambda: edits.wait_for("e1", timeout=30, on_tick=tick), stop))
    assert result is None and took < 2 and edits.waiting_count() == 0 and ticks == []


def test_stop_ends_a_runner_check_wait_and_cancels_the_check_so_it_cannot_run_unseen():
    db = FakeDB()
    record, _ = connected(db, now=rj._now())
    job = rj.create_job(db, "jack@example.com", record["_id"], "tests", None)

    async def stop():
        steering.request_stop(KEY)

    result, took = _run(_in_a_turn(lambda: rj.wait_for_result(db, "jack@example.com", job["id"], timeout=30, poll=0.05), stop, body_time=0.3))
    assert took < 3 and result["status"] == rj.CANCELLED
    assert rj.approve_job(db, "jack@example.com", job["id"]) is None


def test_a_turn_that_is_already_stopped_does_not_start_a_wait():
    cards.register(_change_proposal())

    async def go():
        run = steering.open_run(KEY, [])
        try:
            steering.request_stop(KEY)
            started = time.monotonic()
            return await cards.wait_for("p1", timeout=30), time.monotonic() - started
        finally:
            steering.close_run(KEY, run)

    result, took = _run(go())
    assert result is None and took < 1


# ---- a press still wins, and a browser that leaves does not end the wait ---------------------------------------------------------------------

def test_a_press_still_ends_the_wait_with_its_outcome():
    cards.register(_change_proposal())

    async def press():
        assert (await cards.decide("jack", "p1", "decline"))["status"] == "declined"

    result, _ = _run(_in_a_turn(lambda: cards.wait_for("p1", timeout=30), press))
    assert result == {"kind": "declined", "message": ""}


def test_the_browser_going_away_does_not_end_a_wait_so_a_locked_phone_keeps_its_card():
    """The reason for the detached turns: only Stop (or a new message) ends the wait. The reader is closed at 0.1 s; the wait must still be running at 0.6 s and still take a press."""
    cards.register(_change_proposal())
    seen = {}

    async def go():
        async def produce():
            run = steering.open_run(KEY, [])
            try:
                yield "card shown"
                seen["result"] = await cards.wait_for("p1", timeout=30)
            finally:
                steering.close_run(KEY, run)

        stream = detached_stream.relay(produce)
        await stream.__anext__()
        await stream.aclose()                                   # the browser went away
        await asyncio.sleep(0.6)
        seen["still_waiting"] = steering.is_running(KEY) and "p1" in cards._waiting and "result" not in seen
        await cards.decide("jack", "p1", "decline")             # the person comes back and presses
        for _ in range(100):
            if not steering.is_running(KEY):
                break
            await asyncio.sleep(0.02)

    _run(go())
    assert seen["still_waiting"] and seen["result"]["kind"] == "declined"


# ---- a new message instead of an answer ------------------------------------------------------------------------------------------------------

def test_a_new_message_ends_a_wait_but_leaves_a_turn_that_is_working_alone():
    async def go():
        working = {}

        async def produce():
            run = steering.open_run(KEY, [])
            try:
                yield "x"
                await asyncio.sleep(0.3)                           # busy with the model, not waiting for a person
                working["abandoned_while_working"] = steering.abandon_wait(KEY)
                cards.register(_change_proposal())
                working["result"] = await cards.wait_for("p1", timeout=30)
            finally:
                steering.close_run(KEY, run)

        stream = detached_stream.relay(produce)
        reader = asyncio.create_task(_drain(stream))
        await asyncio.sleep(0.5)                                   # now it is waiting for a person
        working["abandoned_while_waiting"] = steering.abandon_wait(KEY)
        await reader
        return working

    outcome = _run(go())
    assert outcome["abandoned_while_working"] is False and outcome["abandoned_while_waiting"] is True and outcome["result"] is None
    assert steering.abandon_wait(KEY) is False                      # no turn any more


def test_the_chat_endpoint_gives_a_waiting_turn_a_moment_to_end_instead_of_a_409():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("history_key = f\"{username}::{session_id}\"")
    end = source.index("Sonic is still finishing", start)
    block = source[start:end]
    assert "steering.is_running(history_key) and steering.abandon_wait(history_key)" in block and "await asyncio.sleep(0.1)" in block
    assert block.count("steering.is_running(history_key)") == 3 and block.index("abandon_wait") < len(block)


# ---- the helper itself -----------------------------------------------------------------------------------------------------------------------

def test_a_press_and_a_stop_at_the_same_moment_is_a_press():
    async def go():
        run = steering.open_run(KEY, [])
        try:
            event = asyncio.Event()
            await steering.wait_event(asyncio.Event(), 0.01)    # an earlier wait of this turn, so the turn's interrupt already exists when Stop arrives
            event.set()
            steering.request_stop(KEY)                         # which sets it, in the same moment as the press
            return await steering.wait_event(event, 5)
        finally:
            steering.close_run(KEY, run)
    assert _run(go()) == "set"


def test_a_finished_wait_leaves_no_tasks_behind_and_the_turn_is_no_longer_waiting():
    async def go():
        run = steering.open_run(KEY, [])
        try:
            how = await steering.wait_event(asyncio.Event(), 0.05)
            await asyncio.sleep(0)
            leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            return how, leftovers, run.waiting, steering.abandon_wait(KEY)
        finally:
            steering.close_run(KEY, run)
    how, leftovers, waiting, abandoned = _run(go())
    assert how == "timeout" and leftovers == [] and waiting == 0 and abandoned is False


def test_a_runner_check_wait_counts_as_waiting_for_a_person_so_a_new_message_can_end_it():
    db = FakeDB()
    record, _ = connected(db, now=rj._now())
    job = rj.create_job(db, "jack@example.com", record["_id"], "tests", None)
    seen = {}

    async def abandon():
        seen["abandoned"] = steering.abandon_wait(KEY)

    result, took = _run(_in_a_turn(lambda: rj.wait_for_result(db, "jack@example.com", job["id"], timeout=30, poll=0.05), abandon, body_time=0.3))
    assert seen["abandoned"] is True and took < 3 and result["status"] == rj.CANCELLED
