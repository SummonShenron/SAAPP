"""A chat turn keeps going when the browser goes away (2026-11-24): the turn runs in its own task behind a relay (backend/utils/detached_stream.py), only an
explicit Stop ends it early (backend/services/steering.py), and a second turn in the same conversation waits its turn. No model, no database."""
import asyncio
import pathlib

from backend.services import steering
from backend.utils import detached_stream, stream_keepalive

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _run(coro):
    """asyncio.run with a time limit: a relay that hangs fails the test instead of stalling the suite."""
    return asyncio.run(asyncio.wait_for(coro, 5))


def _turn(log, steps=5, fail_at=None, pause=0.0):
    async def produce():
        try:
            for i in range(steps):
                if fail_at == i:
                    raise RuntimeError("boom")
                await asyncio.sleep(pause)
                log.append(i)
                yield f"chunk {i}"
        finally:
            log.append("cleanup")
    return produce


def test_the_turn_finishes_after_the_reader_goes_away():
    async def main():
        log, finished = [], []
        stream = detached_stream.relay(_turn(log, pause=0.01), on_finish=lambda: finished.append(True))
        first = await stream.__anext__()
        await stream.aclose()                                   # the browser went away after the first chunk
        assert first == "chunk 0" and finished == []
        for _ in range(100):
            if finished:
                break
            await asyncio.sleep(0.01)
        return log, finished
    log, finished = _run(main())
    assert log == [0, 1, 2, 3, 4, "cleanup"] and finished == [True]
    assert detached_stream.running_count() == 0


def test_through_the_keepalive_a_cancelled_response_leaves_the_turn_running():
    """What Starlette does when the client disconnects: the response's task is cancelled. The keepalive cancels its reader, and only the relay goes with it."""
    async def main():
        log, finished = [], []
        response = stream_keepalive.with_keepalive(detached_stream.relay(_turn(log, pause=0.02), on_finish=lambda: finished.append(True)), interval=5)

        async def client():
            async for _chunk in response:
                pass

        reading = asyncio.create_task(client())
        await asyncio.sleep(0.03)
        reading.cancel()
        try:
            await reading
        except asyncio.CancelledError:
            pass
        await response.aclose()
        for _ in range(200):
            if finished:
                break
            await asyncio.sleep(0.01)
        return log, finished
    log, finished = _run(main())
    assert log[-1] == "cleanup" and [x for x in log if x != "cleanup"] == [0, 1, 2, 3, 4] and finished == [True]


def test_a_reader_that_stays_gets_everything_in_order_and_a_failure_ends_the_stream_and_still_finishes():
    async def collect(produce, finished):
        return [chunk async for chunk in detached_stream.relay(produce, on_finish=lambda: finished.append(True))]
    finished = []
    assert _run(collect(_turn([], steps=3), finished)) == ["chunk 0", "chunk 1", "chunk 2"] and finished == [True]
    log, finished = [], []
    assert _run(collect(_turn(log, steps=3, fail_at=1), finished)) == ["chunk 0"]
    assert finished == [True] and log == [0, "cleanup"]


def test_only_an_explicit_stop_stops_a_turn_and_only_while_it_runs():
    key = "u::s-stop"
    assert steering.request_stop(key) == "no_active_run" and not steering.stop_requested(key) and not steering.is_running(key)
    run = steering.open_run(key, [])
    assert steering.is_running(key) and not steering.stop_requested(key)
    assert steering.request_stop(key) == "stopping" and steering.stop_requested(key)
    assert steering.submit(key, "also do this") == "too_late"             # a stopped turn takes no more steering
    steering.close_run(key, run)
    assert not steering.is_running(key) and not steering.stop_requested(key)
    run2 = steering.open_run(key, [])
    assert not steering.stop_requested(key)                                # a new turn starts unstopped
    steering.close_run(key, run2)


def test_the_chat_endpoint_stops_only_on_a_stop_runs_the_turn_detached_and_refuses_a_second_turn():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "is_disconnected()" not in source
    assert source.count("if steering.stop_requested(history_key):") == 2
    assert "stream_keepalive.with_keepalive(detached_stream.relay(steerable_turn))" in source
    turn = source[source.index("async def steerable_turn():"):source.index("stream_keepalive.with_keepalive(detached_stream.relay(steerable_turn))")]
    assert "run = steering.open_run(history_key, chat_sessions[history_key])" in turn and "steering.close_run(history_key, run)" in turn
    start = source.index("async def secure_chat(")
    busy = source.index("if steering.is_running(history_key):", start)
    assert busy < source.index("async def token_streamer():", start)
    assert "status_code=409" in source[busy:busy + 400]
    assert '@app.post("/api/chat/stop")' in source and '@app.get("/api/chat/turn-status")' in source
    assert 'steering.request_stop(f"{username}::{request.session_id.strip()}")' in source
    assert 'steering.is_running(f"{username}::{session_id.strip()}")' in source


def test_the_relay_logs_no_content():
    source = (ROOT / "backend" / "utils" / "detached_stream.py").read_text(encoding="utf-8")
    for line in [l for l in source.splitlines() if "logger." in l]:
        assert "chunk" not in line and "%s" not in line, line
