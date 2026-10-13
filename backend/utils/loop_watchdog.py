"""Notices when the server stops answering, and says what it was doing: counts, paths and seconds only.

THE FAILURE IT IS FOR. The server handles every request on one event loop. If any code on it blocks (a synchronous call that takes a minute),
nothing else is answered until it finishes: requests queue up, and a browser's short-lived login tokens expire while they wait, so when the
server comes back every queued request is refused with "token expired" and nothing in the log says why the server was away. From the outside
it looks like an authentication problem.

- A background task sleeps for half a second at a time and measures how late it wakes up. Waking up much later than asked means the loop was
  blocked for that long, and it logs how long and which requests were in flight at the time (their method, path and age: never a query string,
  a body, a header or a user).
- The request middleware (app.py) reports each request's start and finish here, and a request whose first byte took longer than
  SLOW_REQUEST_SECONDS is logged once it finishes. (A streamed reply is counted up to its first byte, not to the end of the stream.)
- Everything is swallowed on failure: this must never be the reason a request fails.
"""
import asyncio
import itertools
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("SASS Logger")

TICK_SECONDS = 0.5
STALL_SECONDS = float(os.getenv("LOOP_STALL_SECONDS", "2.0"))
SLOW_REQUEST_SECONDS = float(os.getenv("SLOW_REQUEST_SECONDS", "10.0"))
MAX_LISTED = 8

_counter = itertools.count(1)
_inflight: Dict[int, Tuple[str, str, float]] = {}  # id -> (method, path, started on the monotonic clock)


def request_started(method: str, path: str) -> int:
    """Notes that a request began; returns what to hand to `request_finished`. Never raises."""
    try:
        key = next(_counter)
        _inflight[key] = (str(method)[:10], str(path)[:120], time.monotonic())
        return key
    except Exception:
        return 0


def request_finished(key: int, now: Optional[float] = None) -> None:
    """Notes that a request's first byte is ready (or it failed), and logs it if it was slow. Never raises."""
    try:
        entry = _inflight.pop(key, None)
        if entry is None:
            return
        elapsed = (time.monotonic() if now is None else now) - entry[2]
        if elapsed >= SLOW_REQUEST_SECONDS:
            logger.warning("[slow-request] %s %s took %.1fs to start answering.", entry[0], entry[1], elapsed)
    except Exception:
        pass


def describe_inflight(now: Optional[float] = None) -> str:
    """The requests waiting right now, oldest first, as "POST /api/chat (65s), GET /api/budget (60s)". Capped; "none" when there are none."""
    moment = time.monotonic() if now is None else now
    rows: List[Tuple[float, str]] = sorted(((moment - started, f"{method} {path}") for method, path, started in list(_inflight.values())), reverse=True)
    if not rows:
        return "none"
    shown = ", ".join(f"{label} ({age:.0f}s)" for age, label in rows[:MAX_LISTED])
    return shown + (f", and {len(rows) - MAX_LISTED} more" if len(rows) > MAX_LISTED else "")


def stall_message(lateness: float, now: Optional[float] = None) -> str:
    return f"[loop-stall] the server's event loop was blocked for about {lateness:.1f}s. Requests in flight: {describe_inflight(now)}."


async def watch(tick: float = TICK_SECONDS, threshold: Optional[float] = None) -> None:
    """Runs forever (until cancelled): logs whenever waking up was late by more than `threshold` seconds."""
    limit = STALL_SECONDS if threshold is None else threshold
    while True:
        before = time.monotonic()
        await asyncio.sleep(tick)
        lateness = time.monotonic() - before - tick
        if lateness >= limit:
            try:
                logger.warning(stall_message(lateness))
            except Exception:
                pass


def start() -> "asyncio.Task":
    """Starts the watchdog on the running loop and returns its task (cancel it at shutdown)."""
    return asyncio.get_running_loop().create_task(watch())
