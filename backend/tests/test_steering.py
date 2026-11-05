import asyncio
import functools
import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.services import steering


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


# ---------------------------------------------------------------------------
# The registry: one run per conversation turn, queued messages, bounded and isolated.
# ---------------------------------------------------------------------------

def _open(key="jack::c1"):
    transcript = []
    return steering.open_run(key, transcript), transcript


def test_submit_without_a_running_turn_says_so_so_the_browser_can_send_it_normally():
    assert steering.submit("jack::nothing-running", "change of plan") == "no_active_run"


def test_blank_messages_are_not_queued():
    _open()
    assert steering.submit("jack::c1", "   \n ") == "empty"
    assert steering.drain("jack::c1") == []


def test_queued_messages_are_drained_oldest_first_and_recorded_in_the_transcript_in_order():
    run, transcript = _open()
    assert steering.submit("jack::c1", "first") == "queued"
    assert steering.submit("jack::c1", "second") == "queued"

    assert steering.drain("jack::c1") == ["first", "second"]
    assert [m.content for m in transcript] == ["first", "second"]
    assert all(isinstance(m, HumanMessage) for m in transcript)
    assert steering.drain("jack::c1") == []  # nothing is applied twice
    steering.close_run("jack::c1", run)


def test_once_the_work_phase_is_over_a_steer_is_too_late():
    run, _ = _open()
    steering.stop_accepting("jack::c1")
    assert steering.submit("jack::c1", "too slow") == "too_late"
    steering.close_run("jack::c1", run)


def test_the_queue_is_bounded_and_long_messages_are_cut():
    run, _ = _open()
    for i in range(steering.MAX_PENDING_STEERS):
        assert steering.submit("jack::c1", f"m{i}") == "queued"
    assert steering.submit("jack::c1", "one too many") == "too_many"
    steering.drain("jack::c1")
    steering.submit("jack::c1", "x" * (steering.MAX_STEER_CHARS + 500))
    assert len(steering.drain("jack::c1")[0]) == steering.MAX_STEER_CHARS
    steering.close_run("jack::c1", run)


def test_conversations_and_users_do_not_see_each_others_steers():
    run_a, transcript_a = _open("jack::a")
    run_b, transcript_b = _open("jack::b")
    steering.submit("jack::a", "for a")
    assert steering.drain("jack::b") == []
    assert steering.submit("alice::a", "wrong user") == "no_active_run"
    assert steering.drain("jack::a") == ["for a"]
    assert transcript_b == []
    steering.close_run("jack::a", run_a)
    steering.close_run("jack::b", run_b)


def test_closing_a_run_returns_what_was_never_consumed_and_stops_accepting():
    run, _ = _open()
    steering.submit("jack::c1", "never used")
    assert steering.close_run("jack::c1", run) == ["never used"]
    assert steering.submit("jack::c1", "after") == "no_active_run"


def test_an_older_run_closing_does_not_tear_down_a_newer_one_for_the_same_conversation():
    old, _ = _open()
    new, _ = _open()  # same key: a newer turn replaced it
    steering.close_run("jack::c1", old)
    assert steering.submit("jack::c1", "still routed to the newer turn") == "queued"
    steering.close_run("jack::c1", new)



# ---------------------------------------------------------------------------
# The ReAct loop: steering is absorbed at step boundaries and shapes later steps.
# ---------------------------------------------------------------------------









