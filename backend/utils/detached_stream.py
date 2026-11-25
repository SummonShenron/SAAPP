"""A chat turn that keeps going after the browser goes away (2026-11-24).

WHY. The reply was produced inside the HTTP response's own generator, so when the connection dropped (Safari closed, the phone suspended the page, a
tab switched on a flaky network) the server stopped the turn, and the conversation recorded "[Stopped by user]" for something the person never did.

HOW. The turn's generator runs in its own task and puts each chunk on a queue; the response only relays the queue. If the connection drops, the relay
ends and the task carries on to the end, so the reply is saved as usual and the page finds it when it comes back (it asks /api/chat/turn-status and
reloads). Only an explicit Stop ends a turn early (backend/services/steering.py request_stop). A task is kept referenced until it ends, so it is not
collected half way, and a failure inside it is logged (no content) and ends the relay instead of hanging it.
"""
import asyncio
import logging
from typing import AsyncIterator, Callable, Optional, Set

logger = logging.getLogger("SASS Logger")

_running: Set["asyncio.Task"] = set()
_DONE = object()


def running_count() -> int:
    """How many detached turns are still running in this process."""
    return len(_running)


async def relay(produce: Callable[[], AsyncIterator[str]], on_finish: Optional[Callable[[], None]] = None) -> AsyncIterator[str]:
    """Runs `produce()` to the end in its own task and yields what it produces for as long as someone is reading. `on_finish` runs when the TASK
    ends (not when the reader leaves). Closing this generator (the client went away) leaves the task running."""
    queue: "asyncio.Queue" = asyncio.Queue()

    async def run() -> None:
        try:
            async for chunk in produce():
                queue.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[detached_stream] a chat turn failed while running on its own.")
        finally:
            queue.put_nowait(_DONE)
            if on_finish is not None:
                try:
                    on_finish()
                except Exception:
                    logger.exception("[detached_stream] the end-of-turn step failed.")

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)
    while True:
        item = await queue.get()
        if item is _DONE:
            return
        yield item
