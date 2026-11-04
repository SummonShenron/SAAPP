"""Which server process answered, and whether this deployment is allowed to run more than one.

THE RULE (2026-11-05, docs/public-launch-readiness.md item 3): Sonic is built to run as ONE server process. A few things live only in that process's memory, and with a second worker or instance they
silently break instead of failing loudly: the wait for the person's Apply (backend/services/local_edit_decisions.py: a decision that lands on another process is not seen), the notes of what a proposal
changed, and the counts waiting to be saved (the usage meters flush in batches, so a restart loses a minute of them). Steering already falls back to a normal follow-up (backend/services/steering.py),
the graph checkpointer is MongoDB, and a conversation's transcript is read back from the database, so those survive. Nothing here enforces the rule; it makes a breach VISIBLE: a warning when the server
starts, and a pid and start time on /api/health, so two different pids answering the same address (two instances) or a start time that keeps moving (restarts) can be seen from outside.
Nothing here reads or stores anything about a person.
"""
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

STARTED_AT = datetime.now(timezone.utc)
_WORKER_ENVS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")
_WORKER_ARG = re.compile(r"(?:--workers[= ]|-w\s*)(\d+)")


def configured_workers(environ: Optional[Dict[str, str]] = None) -> int:
    """How many worker processes the environment asks for: the largest of WEB_CONCURRENCY, UVICORN_WORKERS, GUNICORN_WORKERS and a `--workers N` / `-w N` in GUNICORN_CMD_ARGS; 1 when none is set
    (the server's own start command is not visible from here, so this only sees what the environment says). Pure."""
    env = os.environ if environ is None else environ
    counts = []
    for name in _WORKER_ENVS:
        try:
            counts.append(int(str(env.get(name, "")).strip()))
        except ValueError:
            pass
    match = _WORKER_ARG.search(str(env.get("GUNICORN_CMD_ARGS", "")))
    if match:
        counts.append(int(match.group(1)))
    return max([count for count in counts if count > 0] or [1])


def single_process_warning(environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    """The line to log at start-up when the environment asks for more than one worker, or None. Pure."""
    workers = configured_workers(environ)
    if workers <= 1:
        return None
    return (
        f"The environment asks for {workers} worker processes, but Sonic keeps some state in one process's memory (the wait for Apply, applied-change notes, usage counts waiting to be saved). "
        "With more than one, an Apply can land on a process that is not waiting for it and the turn ends there. Run one worker (docs/public-launch-readiness.md, item 3)."
    )


def health_fields(now: Optional[datetime] = None) -> Dict[str, Any]:
    """What /api/health adds: this process's id, when it started, how long it has been up and how many workers the environment asks for. Two pids at one address means two instances."""
    moment = now or datetime.now(timezone.utc)
    return {"pid": os.getpid(), "started_at": STARTED_AT.isoformat(), "uptime_seconds": max(int((moment - STARTED_AT).total_seconds()), 0), "workers_configured": configured_workers()}
