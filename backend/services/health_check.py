"""Sonic's health check: the routine that looks over its own state and says whether anything needs a person's eyes.

It makes no model call (nothing here costs money) and it reads only things the server already has:

- errAgent: incidents created since the last look, and whether the latest deploy went through (only when errAgent reads are configured; when they are
  not, that part is skipped and not counted as a problem);
- the chat counters: of the last day's turns, how many failed outright, and only when that is a real share of them (a single failure is not a trend);
- the knowledge graph: indexed documents that have no relationships (backend/utils/feedback_stats.py), the gap this check exists to notice;
- the usage budget: whether it has reached its limit.

What comes back is a status and a handful of whole-number counts, never text: an incident's own message is written by whatever failed and can contain
what a user typed, so it is never copied (the same reason the incident digest stores only the error class). Each look is separate, and one that fails
(errAgent is slow, a collection will not read) is counted in `checks_failed` and skipped, so the others still run.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from backend.utils import routines

logger = logging.getLogger("SASS Logger")

READ_TIMEOUT_SECONDS = 8.0
LOOKBACK_HOURS = 24
MIN_TURNS_FOR_A_RATE = 10
FAILURE_SHARE_THAT_COUNTS = 0.10
_FAILED_DEPLOYS = frozenset({"build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"})


def _created(row: Dict[str, Any]) -> Optional[datetime]:
    from backend.utils.ops_digest import _parse
    return _parse(row.get("created_at"))


async def _errAgent_counts(since_hours: float, now: datetime, sdk: Any) -> Dict[str, int]:
    """New incidents, and whether the latest deploy failed. Raises on a problem reading; the caller counts that as a failed look."""
    counts = {"incidents_new": 0, "deploy_failed": 0}
    rows = await asyncio.wait_for(sdk.list_incidents(since=f"{max(1, int(since_hours))}h", limit=50), READ_TIMEOUT_SECONDS)
    floor = now - timedelta(hours=since_hours)
    counts["incidents_new"] = sum(1 for r in rows if isinstance(r, dict) and (_created(r) or now) > floor)
    report = await asyncio.wait_for(sdk.latest_deploy(), READ_TIMEOUT_SECONDS)
    if isinstance(report, dict) and report.get("status") == "ok":
        deploy = report.get("latestDeploy") or {}
        counts["deploy_failed"] = int(str(deploy.get("status") or "").lower() in _FAILED_DEPLOYS)
    return counts


def _turn_counts(db, now: datetime) -> Dict[str, int]:
    from backend.utils.self_counters import fetch_counter_docs, summarize_counters
    totals = summarize_counters(fetch_counter_docs(db, 1, now), now, 1)["totals"]
    turns = int(totals.get("turns_total", 0))
    failures = sum(int(v) for k, v in totals.items() if k.startswith("turn_failed_"))
    flagged = turns >= MIN_TURNS_FOR_A_RATE and failures / turns >= FAILURE_SHARE_THAT_COUNTS
    return {"turns": turns, "turn_failures": failures if flagged else 0}


def _graph_gaps(db) -> int:
    from backend.utils.feedback_stats import summarize_graph
    graph = summarize_graph(db)
    if graph["documents_total"] > 0 and graph["edges_total"] == 0:
        return graph["documents_total"]
    return graph["documents_without_edges"]


def _budget_limited(db, now: datetime) -> int:
    from backend.utils.budget_guard import current_state
    return int((current_state(db, now) or {}).get("level") in ("throttled", "blocked"))


async def run_health_check(db: Any, now: Optional[datetime] = None, sdk: Any = None) -> Tuple[str, Dict[str, int]]:
    """(status, counts) for one look. Never raises."""
    now = now or datetime.now(timezone.utc)
    counts: Dict[str, int] = {"checks_failed": 0}

    async def look(name: str, work) -> None:
        try:
            counts.update(await work())
        except Exception as exc:
            counts["checks_failed"] += 1
            logger.warning("[health_check] the %s look failed: %s", name, type(exc).__name__)

    from backend.services import ops_incidents
    reader = sdk if sdk is not None else (ops_incidents._sdk() if ops_incidents.ops_available() else None)
    if reader is not None:
        interval = (routines.by_id("health_check") or routines.Routine("health_check", "", "", 6.0)).every_hours
        await look("errAgent", lambda: _errAgent_counts(interval + 1, now, reader))
    await look("turns", lambda: asyncio.to_thread(_turn_counts, db, now))
    await look("knowledge graph", lambda: _async_value("graph_gaps", asyncio.to_thread(_graph_gaps, db)))
    await look("budget", lambda: _async_value("budget_limited", asyncio.to_thread(_budget_limited, db, now)))

    flagged = any(counts.get(key) for key in ("incidents_new", "deploy_failed", "turn_failures", "graph_gaps", "budget_limited"))
    status = routines.STATUS_ATTENTION if flagged else routines.STATUS_OK
    if counts["checks_failed"] and not flagged and counts["checks_failed"] >= 4:
        status = routines.STATUS_FAILED          # nothing could be looked at at all: that is not "nothing unusual"
    return status, counts


async def _async_value(key: str, awaitable) -> Dict[str, int]:
    return {key: int(await awaitable)}
