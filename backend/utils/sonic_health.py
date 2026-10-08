"""One payload for the admin "Sonic Health" page: how the safety layer is doing, how Sonic itself is behaving, and the
self-observations waiting for approval or live. Everything here is an aggregate count or approved wording; none of it
contains a user's words or a person's name (see safety_stats.py, self_counters.py, self_observations.py).
"""
import datetime
from datetime import timezone
from typing import Any, Dict, Optional

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
    }
