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
import logging
import threading
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage

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


_runs: dict = {}
_lock = threading.Lock()


def open_run(key: str, transcript: list) -> _Run:
    run = _Run(transcript=transcript)
    with _lock:
        _runs[key] = run
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
            run.transcript.append(HumanMessage(content=message))
        run.applied += len(messages)
    logger.info("[steering] %s: applying %d steering message(s).", key, len(messages))
    return messages
