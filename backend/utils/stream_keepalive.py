"""A keepalive for the chat stream, so a page can tell a quiet turn from a dead connection.

THE PROBLEM. The chat answer is a long-lived stream. When the server restarts under it (`uvicorn --reload` after an applied edit to a backend file, a deploy), or a proxy holds the socket open after the
server behind it is gone, the page gets nothing and is never told: it stays "busy" with Send and Continue disabled until the person reloads (2026-10-30: six Continue presses that cost money and saved no reply).
A long step with no output (a slow model call, a test run) looks exactly the same from the page, so the page cannot guess.

THE FIX. The stream says it is alive. `with_keepalive` sends `: ready` at once (an SSE comment: a page that does not know about it ignores it, and one that does learns the server keeps this promise) and `: keepalive`
whenever KEEPALIVE_SECONDS pass with nothing else to send. The page (local/src/streamWatchdog.ts) gives up on a stream that has gone quiet for several of those, but only on a stream that said it would keep
the promise, so a page ahead of its server never cuts a long turn short.

THE STREAM RUNS IN ONE TASK. The turn's code sets context variables as it goes (the turn record, the current user), and they must survive from one chunk to the next, so the wrapped stream is read by a single
task that feeds a queue, never one task per chunk. Closing this stream (the person left, the server is stopping) cancels that task and waits for it, so the turn's own cleanup still runs. Nothing is logged.
"""
import asyncio
import os
from typing import Any, AsyncIterator, Optional

KEEPALIVE_SECONDS = float(os.getenv("CHAT_KEEPALIVE_SECONDS", "15"))
READY = ": ready\n\n"
BEAT = ": keepalive\n\n"

_DONE = object()


async def with_keepalive(stream: AsyncIterator[Any], interval: Optional[float] = None) -> AsyncIterator[Any]:
    """Yields everything `stream` yields, in order, plus a comment line first and one after every `interval` seconds of silence. An exception in `stream` is raised here. With a non-positive
    interval the stream is passed through untouched."""
    wait = KEEPALIVE_SECONDS if interval is None else interval
    if wait <= 0:
        async for chunk in stream:
            yield chunk
        return

    queue: "asyncio.Queue[Any]" = asyncio.Queue()

    async def pump() -> None:
        try:
            async for chunk in stream:
                await queue.put(chunk)
            await queue.put(_DONE)
        except asyncio.CancelledError:
            raise
        except BaseException as problem:                       # handed to the reader, which raises it in its own task
            await queue.put(problem)

    producer = asyncio.ensure_future(pump())
    try:
        yield READY
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=wait)
            except asyncio.TimeoutError:
                yield BEAT
                continue
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
