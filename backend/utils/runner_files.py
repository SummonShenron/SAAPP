"""A person's runner filling their connected-folder snapshot, on their say-so (docs/runner-files-plan.md).

Sonic reads a person's folder through a copy on the server (backend/services/local_workspace.py). The browser can fill it; this lets the runner do it too, so a person on a browser with no folder access
can still work on a project. The server never reaches into the runner: it can only ASK (runner_jobs.request_files), the runner shows the person a window on their own computer, and only a yes there
makes it send anything. Until it has answered, the request stays open; the batches a runner sends are accepted only while a request for that project is open and was handed to the runner, so a runner key
on its own cannot upload files nobody asked for, and a request the person never answered (or that expired) lets nothing in.

Every file goes through local_workspace.apply_sync like the browser's, which refuses secrets, vendored and build folders, odd paths and over-size files whatever the sender says.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.services import local_workspace
from backend.utils import runner_jobs
from backend.utils.runner_jobs import KEYS, KIND_FILES, RUNNERS, RunnerError

logger = logging.getLogger("SASS Logger")

UPLOAD_MINUTES = 60          # how long after a request was handed to the runner its batches are still taken (a big project takes a while to send)


def _count(key: str) -> None:
    try:
        from backend.utils.self_counters import record_turn_counts

        record_turn_counts({key: 1})
    except Exception:
        logger.warning("[runner files] could not count %r.", key, exc_info=True)


def open_request(runner: Dict[str, Any], project_id: str, now: datetime) -> Optional[Dict[str, Any]]:
    """The request that lets this project's files in right now: one for it that was handed to the runner, has no answer yet and was handed over within UPLOAD_MINUTES. None when there is none. Pure."""
    for request in runner.get("requests") or []:
        if request.get("kind") != KIND_FILES or request.get("project_id") != project_id or request.get("result"):
            continue
        delivered = runner_jobs._aware(request.get("delivered_at"))
        if delivered and now - delivered <= timedelta(minutes=UPLOAD_MINUTES):
            return request
    return None


def api_sync(db: Any, key: Dict[str, Any], body: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """One batch of a runner's upload of a project's files: {"accepted", "rejected", "file_count", "total_bytes"} as the browser's upload answers. Raises RunnerError("not_asked") when no
    open request lets this project's files in, RunnerError("no_project") for a project the runner does not serve, and RunnerError("stale") for a replaced upload. Blocking."""
    now = now or runner_jobs._now()
    runner = db[RUNNERS].find_one({"_id": key["_id"]}) or {}
    project_id = str((body or {}).get("project_id") or "")
    project = next((p for p in runner.get("projects") or [] if p["id"] == project_id), None)
    if project is None:
        _count("runner_files_refused")
        raise RunnerError("no_project", "That runner does not serve that project.")
    if open_request(runner, project_id, now) is None:
        _count("runner_files_refused")
        raise RunnerError("not_asked", "Nobody asked this project to send its files, or the window on the computer has not been answered yet. Press the button in SAAPP first.")
    files: List[Dict[str, Any]] = [dict(f) for f in (body.get("files") or []) if isinstance(f, dict)]
    try:
        result = local_workspace.apply_sync(
            key["username"], project["name"], files, [str(p) for p in body.get("deleted") or []], bool(body.get("reset")), body.get("upload_id") or None, bool(body.get("commit")), source="runner",
        )
    except local_workspace.StaleUpload:
        _count("runner_files_failed")
        raise RunnerError("stale", "That upload was replaced by a newer one. Start again.")
    except PermissionError:
        _count("runner_files_refused")
        raise RunnerError("not_asked", "Local folders are only available to signed-in people.")
    except Exception:
        _count("runner_files_failed")
        logger.exception("[runner files] a batch from a runner could not be applied")
        raise
    _count("runner_files_ok")
    return result


def api_clear(db: Any, key: Dict[str, Any]) -> Dict[str, Any]:
    """The runner removes the files it sent: only when the person's snapshot was filled by a runner (a folder the browser connected is left alone). Blocking."""
    if local_workspace.snapshot_source(key["username"]) == "runner":
        local_workspace.clear_workspace(key["username"])
        return {"status": "cleared"}
    return {"status": "nothing_to_clear"}
