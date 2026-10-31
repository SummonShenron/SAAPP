"""The chat stream's keepalive (backend/utils/stream_keepalive.py): everything passes through in order, silence is answered with comment lines, the stream's context survives, and closing cleans up."""
import asyncio
import contextvars
import pathlib

import pytest

from backend.utils import stream_keepalive as sk

ROOT = pathlib.Path(__file__).resolve().parents[2]


async def _collect(stream):
    return [chunk async for chunk in stream]


def test_it_says_it_is_alive_first_and_passes_every_chunk_through_in_order():
    async def source():
        for item in ("data: 1\n\n", "data: 2\n\n", "data: 3\n\n"):
            yield item

    out = asyncio.run(_collect(sk.with_keepalive(source(), 5)))
    assert out == [sk.READY, "data: 1\n\n", "data: 2\n\n", "data: 3\n\n"]


def test_silence_is_answered_with_a_comment_line_each_interval_and_the_data_is_not_delayed_or_lost():
    async def slow():
        yield "data: first\n\n"
        await asyncio.sleep(0.35)
        yield "data: second\n\n"

    out = asyncio.run(_collect(sk.with_keepalive(slow(), 0.1)))
    assert out[0] == sk.READY and out[1] == "data: first\n\n" and out[-1] == "data: second\n\n"
    beats = [chunk for chunk in out if chunk == sk.BEAT]
    assert 2 <= len(beats) <= 4                                  # about 0.35 s of silence at 0.1 s: a few, not a flood
    assert all(chunk.startswith(":") for chunk in out if chunk in (sk.READY, sk.BEAT))   # SSE comments: a page that ignores them loses nothing


def test_an_error_in_the_stream_reaches_the_reader_after_what_came_before_it():
    async def broken():
        yield "data: ok\n\n"
        raise RuntimeError("boom")

    async def go():
        seen = []
        with pytest.raises(RuntimeError):
            async for chunk in sk.with_keepalive(broken(), 5):
                seen.append(chunk)
        return seen

    assert asyncio.run(go()) == [sk.READY, "data: ok\n\n"]


def test_what_the_stream_sets_in_its_context_is_still_there_for_its_later_chunks():
    current = contextvars.ContextVar("current_turn", default=None)

    async def turn():
        current.set("turn-1")                                    # the turn record, set once at the start
        yield "a"
        await asyncio.sleep(0.05)
        yield f"b sees {current.get()}"                          # ... and read many chunks later

    out = asyncio.run(_collect(sk.with_keepalive(turn(), 0.01)))
    assert [c for c in out if not c.startswith(":")] == ["a", "b sees turn-1"]


def test_closing_the_stream_early_cancels_the_source_and_lets_its_own_cleanup_run():
    log = []

    async def source():
        try:
            yield "data: 1\n\n"
            await asyncio.sleep(30)
            yield "data: never\n\n"
        except asyncio.CancelledError:
            log.append("cancelled")
            raise
        finally:
            log.append("cleaned up")

    async def go():
        stream = sk.with_keepalive(source(), 5)
        assert await stream.__anext__() == sk.READY
        assert await stream.__anext__() == "data: 1\n\n"
        await stream.aclose()                                    # the person left

    asyncio.run(asyncio.wait_for(go(), 5))
    assert log == ["cancelled", "cleaned up"]


def test_a_non_positive_interval_passes_the_stream_through_untouched():
    async def source():
        yield "x"
        yield "y"

    assert asyncio.run(_collect(sk.with_keepalive(source(), 0))) == ["x", "y"]


def test_the_chat_endpoint_streams_through_it():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "async for chunk in stream_keepalive.with_keepalive(token_streamer()):" in source
    assert "from backend.utils import stream_keepalive" in source
