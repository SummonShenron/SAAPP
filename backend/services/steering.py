"""Mid-run steering: lets a user send a message while Sonic is still working and have it change what
the work does next, the way you can redirect a coding agent partway through a task.

One in-memory "run" per active conversation turn. The chat endpoint opens it, POST /api/chat/steer
queues messages into it, and the ReAct loop drains the queue at the start of every step (and right
after each model decision), folding the messages into the prompt. A queued message can only take
effect at a step boundary: an LLM call or tool already in flight is never cancelled.

State is process-local on purpose. If the steer request lands on a different backend instance than
the running turn (or no turn is running), submit() says so and the browser sends the message as a
normal follow-up instead, so a steer is never silently lost.
"""
import asyncio
import contextvars
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.messages import HumanMessage

from backend.utils.time_utils import stamp

logger = logging.getLogger("SASS Logger")

MAX_PENDING_STEERS = 5
MAX_STEER_CHARS = 2000


@dataclass
class _Run:
    # The conversation's in-memory transcript. Applied steers are appended to it as they are
    # consumed, so the saved order is: the original message, each steer, then Sonic's reply.
    transcript: list
    pending: list = field(default_factory=list)
    applied: int = 0
    accepting: bool = True
    stop: bool = False          # the person pressed Stop (POST /api/chat/stop): the only thing that ends a turn early now that a lost connection does not
    interrupt: Optional["asyncio.Event"] = None     # set with `stop`, so a turn blocked on a person's answer (wait_event) wakes at once instead of at its next check
    waiting: int = 0            # how many waits for a person's answer this turn is inside right now


_runs: dict = {}
_lock = threading.Lock()


_turn_key: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("steering_turn_key", default=None)


def open_run(key: str, transcript: list) -> _Run:
    """Registers the turn. It also remembers the key for everything this turn runs (the context is copied into the tasks and threads it starts), so a wait for a person's
    answer can find its own turn without being handed the key (wait_event)."""
    run = _Run(transcript=transcript)
    with _lock:
        _runs[key] = run
    _turn_key.set(key)
    return run


def close_run(key: str, run: _Run) -> list:
    """Ends a run and returns whatever was queued but never consumed (the browser re-sends those as
    a normal message). Only removes the entry if it is still this run, so a newer turn that replaced
    it isn't torn down."""
    with _lock:
        if _runs.get(key) is run:
            del _runs[key]
        leftover = list(run.pending)
        run.pending.clear()
        run.accepting = False
    return leftover


# ---- Stop, and whether a turn is still running (2026-11-24) -----------------------------------------------------------------------------
# A turn used to end whenever the browser's connection dropped, because Stop was only the browser cancelling its request and the server could
# not tell the two apart. So closing Safari, or the phone suspending the page, stopped the turn and saved "[Stopped by user]". Now the turn runs
# to the end whatever happens to the connection (backend/utils/detached_stream.py), and Stop is its own request that sets this flag.

def request_stop(key: str) -> str:
    """Asks the running turn of this conversation to stop at its next check. "stopping", or "no_active_run" (none here: it finished, or it runs
    on another server instance)."""
    with _lock:
        run = _runs.get(key)
        if run is None:
            return "no_active_run"
        run.stop = True
        run.accepting = False
        if run.interrupt is not None:
            run.interrupt.set()
    return "stopping"


def abandon_wait(key: str) -> bool:
    """The person sent a new message while the last turn is blocked on a card or a check waiting for THEIR answer: they have moved on, so that wait ends the way Stop would end it
    (nothing is applied, the turn finishes). True only when the turn really is in such a wait; a turn that is working is left alone (the caller answers 409)."""
    with _lock:
        run = _runs.get(key)
        if run is None or run.waiting <= 0:
            return False
    return request_stop(key) == "stopping"


class person_wait:
    """`with steering.person_wait():` marks the turn as blocked on a person for as long as the block runs (for a wait that polls instead of using one event), so a new message can end it (abandon_wait)."""
    def __enter__(self):
        key = _turn_key.get()
        with _lock:
            self._run = _runs.get(key) if key else None
            if self._run is not None:
                self._run.waiting += 1
        return self

    def __exit__(self, *exc):
        if self._run is not None:
            with _lock:
                self._run.waiting -= 1
        return False


def wait_stopped(key: Optional[str] = None) -> bool:
    """Whether the turn this code runs in has been told to stop (Stop, or a new message instead of an answer)."""
    return stop_requested(key or _turn_key.get() or "")


async def wait_event(event: "asyncio.Event", timeout: float) -> str:
    """Waits up to `timeout` seconds for `event` (the person's press or answer) and says how it ended: "set", "timeout", or "stopped" (the person pressed Stop, or sent a new message instead
    of answering, while this turn was waiting). A press that arrived at the same moment wins. A browser that merely went away does NOT end it: the turn is meant to carry on (a locked phone).
    Outside a registered turn it is a plain timed wait."""
    key = _turn_key.get()
    with _lock:
        run = _runs.get(key) if key else None
        if run is not None:
            if run.stop and not event.is_set():
                return "stopped"
            if run.interrupt is None:
                run.interrupt = asyncio.Event()
            run.waiting += 1
            interrupt = run.interrupt
    if run is None:
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return "set"
        except asyncio.TimeoutError:
            return "timeout"
    pressed = asyncio.ensure_future(event.wait())
    interrupted = asyncio.ensure_future(interrupt.wait())
    try:
        await asyncio.wait({pressed, interrupted}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        with _lock:
            run.waiting -= 1
        for task in (pressed, interrupted):
            if not task.done():
                task.cancel()
    if pressed.done() and not pressed.cancelled():
        return "set"
    if interrupted.done() and not interrupted.cancelled():
        return "stopped"
    return "timeout"


def stop_requested(key: str) -> bool:
    """Whether the person asked the running turn of this conversation to stop."""
    with _lock:
        run = _runs.get(key)
        return bool(run and run.stop)


def is_running(key: str) -> bool:
    """Whether a turn of this conversation is still running here (it may be finishing after the browser went away)."""
    with _lock:
        return key in _runs


def stop_accepting(key: str) -> None:
    """Called when the part of the turn that can act on a steer (the tool-agent loop) is over."""
    with _lock:
        run = _runs.get(key)
        if run:
            run.accepting = False


def submit(key: str, text: str) -> str:
    """Queues a steering message. Returns "queued", or why it wasn't: "empty", "no_active_run",
    "too_late" (the work phase already finished), or "too_many"."""
    text = (text or "").strip()
    if not text:
        return "empty"
    with _lock:
        run = _runs.get(key)
        if run is None:
            return "no_active_run"
        if not run.accepting:
            return "too_late"
        if len(run.pending) >= MAX_PENDING_STEERS:
            return "too_many"
        run.pending.append(text[:MAX_STEER_CHARS])
    return "queued"


def drain(key: str) -> list:
    """Takes every queued message (oldest first), records them in the transcript, and returns them."""
    with _lock:
        run = _runs.get(key)
        if run is None or not run.pending:
            return []
        messages = list(run.pending)
        run.pending.clear()
        for message in messages:
            run.transcript.append(stamp(HumanMessage(content=message)))
        run.applied += len(messages)
    logger.info("[steering] %s: applying %d steering message(s).", key, len(messages))
    return messages
