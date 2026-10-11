"""One payload for the admin "Sonic Health" page: how the safety layer is doing, how Sonic itself is behaving, the
self-observations waiting for approval or live, and what the model calls have used (tokens by model and by caller). Everything
here is an aggregate count or approved wording; none of it contains a user's words or a person's name (see safety_stats.py,
self_counters.py, self_observations.py, llm_usage.py).
"""
import datetime
from datetime import timezone
from typing import Any, Dict, Optional

from backend.utils.budget_guard import current_state as budget_state, public_view as budget_view
from backend.utils.llm_usage import fetch_route_docs, fetch_usage_docs, load_prices, summarize_routes, summarize_usage
from backend.utils.safety_stats import fetch_recent_events, summarize_safety_events
from backend.utils.self_counters import fetch_counter_docs, summarize_counters
from backend.utils.self_observations import list_observations



def build_sonic_health(db: Any, days: int, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """The whole page's data for a period of `days`. Blocking (database reads): call it off the event loop."""
    now = now or datetime.datetime.now(timezone.utc)
    return {
        "days": days,
        "generated_at": now.isoformat(),
        "safety": summarize_safety_events(fetch_recent_events(db, days, now), now, days),
        "counters": summarize_counters(fetch_counter_docs(db, days, now), now, days),
        "observations": list_observations(db, now),
        "usage": {**summarize_usage(fetch_usage_docs(db, days, now), load_prices()), "routes": summarize_routes(fetch_route_docs(db, days, now))},
        "budget": budget_view(budget_state(db, now), True),
    }
