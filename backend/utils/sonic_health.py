"""One payload for the admin "Sonic Health" page: how the safety layer is doing, how Sonic itself is behaving, the
self-observations waiting for approval or live, and what the model calls have used (tokens by model and by caller). Everything
here is an aggregate count or approved wording; none of it contains a user's words (see safety_stats.py, self_counters.py,
self_observations.py, llm_usage.py). The ONE place a person is named is the "users" section: what each account cost, as counts and dollars
only (backend/utils/user_usage.py), because working out what a person costs needs to know who. The page is admin-only.
"""
import datetime
import logging
from datetime import timezone
from typing import Any, Dict, List, Optional

from backend.utils.budget_guard import current_state as budget_state, public_view as budget_view
from backend.utils import billing_time, user_usage
from backend.utils.llm_turns import daily_turns, fetch_turn_docs, summarize_turns
from backend.utils.llm_usage import fetch_route_docs, fetch_usage_docs, load_prices, summarize_daily, summarize_routes, summarize_usage
from backend.utils.safety_stats import fetch_recent_events, summarize_safety_events
from backend.utils.self_counters import fetch_counter_docs, summarize_counters
from backend.utils.self_observations import list_observations
from backend.utils.feedback_stats import fetch_corrections, summarize_feedback, summarize_graph
from backend.utils import goals as goals_utils, routines as routines_utils

logger = logging.getLogger("SASS Logger")



def billing_days(days: int, now: datetime.datetime) -> List[str]:
    """The billing days (Pacific, see billing_time.py) of a period of `days` days ending today, oldest first, as "YYYY-MM-DD"."""
    today = billing_time.local(now).date()
    return [(today - datetime.timedelta(days=offset)).isoformat() for offset in range(max(days, 1) - 1, -1, -1)]


def _section(name: str, build) -> Optional[Dict[str, Any]]:
    """One part of the page, or None when it cannot be built: a problem reading one collection must not take the rest of the page down."""
    try:
        return build()
    except Exception as exc:
        logger.warning("Sonic Health: the %s section could not be built: %s", name, type(exc).__name__)
        return None


def _users_section(db: Any, days: int, now: datetime.datetime, usage_summary: Dict[str, Any], daily: List[Dict[str, Any]], viewer: Optional[str]) -> Dict[str, Any]:
    """What each account cost over the period, plus, for every day, who used it and what each cost against the day's total, with the viewing admin marked
    (`viewer` is their account id)."""
    period = billing_days(days, now)
    rows = user_usage.fetch_daily(db, days, now)
    summary = user_usage.summarize_users(rows, period, total_cost=usage_summary.get("estimated_cost"), evals=daily)
    summary["daily_people"] = user_usage.day_breakdowns(rows, daily, period, viewer)
    return summary


def build_sonic_health(db: Any, days: int, now: Optional[datetime.datetime] = None, viewer: Optional[str] = None) -> Dict[str, Any]:
    """The whole page's data for a period of `days`. Blocking (database reads): call it off the event loop. `viewer` is the signed-in admin's account id."""
    now = now or datetime.datetime.now(timezone.utc)
    usage_docs = fetch_usage_docs(db, days, now)
    turn_docs = fetch_turn_docs(db, days, now)
    prices = load_prices()
    per_day_turns = daily_turns(turn_docs)
    # One row per day for the chart: what the model calls used and cost, and how many chat turns there were that day.
    daily = [
        {**row, **{key: per_day_turns.get(row["day"], {}).get(key, 0) for key in ("turns", "tool_turns")}}
        for row in summarize_daily(usage_docs, prices, billing_days(days, now))
    ]
    counters = summarize_counters(fetch_counter_docs(db, days, now), now, days)
    usage_summary = summarize_usage(usage_docs, prices)
    return {
        "days": days,
        "generated_at": now.isoformat(),
        "safety": summarize_safety_events(fetch_recent_events(db, days, now), now, days),
        "counters": counters,
        # Feedback is reused: a person's thumbs and the answer checker's redos are shown to the model on later, similar questions.
        "feedback": _section("feedback", lambda: summarize_feedback(
            fetch_corrections(db), counters["totals"].get("guardrail_applied", 0), counters["totals"].get("turns_total", 0), now, days)),
        # The knowledge graph retrieval adds to answers: an empty one looks like one that found nothing, so it is counted here.
        "graph": _section("graph", lambda: summarize_graph(db)),
        # The jobs the server runs on a schedule, and the last result of each: what Sonic can truthfully say it does on its own (backend/utils/routines.py).
        "routines": _section("routines", lambda: routines_utils.routines_view(db, now)),
        # Sonic's goals: what each is, how it is going, whether its lever is approved and what the lever did (backend/utils/goals.py). Counts only.
        "goals": _section("goals", lambda: goals_utils.status_view(db, now, use_cache=False)),
        "observations": list_observations(db, now),
        "usage": {
            **usage_summary,
            "routes": summarize_routes(fetch_route_docs(db, days, now)),
            "turns": summarize_turns(turn_docs),
            "daily": daily,
        },
        # What each account cost over the period (counts and dollars only; the one place a person is named, on an admin-only page).
        "users": _section("users", lambda: _users_section(db, days, now, usage_summary, daily, viewer)),
        "budget": budget_view(budget_state(db, now), True),
    }
