"""How the crisis safety layer is doing, as counts. Reads the events written by safety_utils.log_safety_event:
no message text and no contact names exist in them, and this module reports distinct users only as a number.

    python -m backend.utils.safety_stats            # the last 7 days
    python -m backend.utils.safety_stats --days 30
"""
import argparse
import datetime
from collections import Counter
from datetime import timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils.safety_utils import (
    KIND_LADDER_ANSWER, KIND_LAST_RESORT_LINE, KIND_LINE_IN_REPLY, KIND_OUTAGE_FALLBACK, KIND_REPLY_REVISED,
    KIND_RISK_RAISED, KIND_RISK_TURN, LADDER, SAFETY_EVENTS_COLLECTION,
)

MAX_EVENTS = 50000


def _as_utc(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 3) if whole else None


def summarize_safety_events(
    events: Iterable[Dict[str, Any]], now: Optional[datetime.datetime] = None, days: int = 7
) -> Dict[str, Any]:
    """Counts and rates over the last `days` days. An event with no "kind" is an older risk-raised record.

    The rates that say whether the layer is working:
    - revised_rate: of replies delivered under a risk level, how many the safety check had to rewrite (high means
      the prompt isn't producing right-shaped replies on its own);
    - last_resort_rate: how many still lacked the human line after a rewrite, so code added it (should stay near
      zero; a rise means the model is ignoring the handoff instruction);
    - line_rate: how many replies named a human line at all (expected to be low, since the ladder comes first)."""
    now = now or datetime.datetime.now(timezone.utc)
    since = now - datetime.timedelta(days=days)
    kinds: Counter = Counter()
    levels: Counter = Counter()
    sources: Counter = Counter()
    per_day: Counter = Counter()
    ladder: Dict[str, Counter] = {key: Counter() for key, _ in LADDER}
    users = set()
    risk_users = set()
    for event in events:
        at = _as_utc(event.get("at"))
        if at is None or at < since or at > now:
            continue
        kind = event.get("kind") or KIND_RISK_RAISED
        kinds[kind] += 1
        if event.get("username"):
            users.add(event["username"])
        if kind == KIND_RISK_RAISED:
            levels[event.get("level") or "unknown"] += 1
            sources[event.get("source") or "unknown"] += 1
            per_day[at.strftime("%Y-%m-%d")] += 1
            if event.get("username"):
                risk_users.add(event["username"])
        elif kind == KIND_LADDER_ANSWER and event.get("rung") in ladder and event.get("status"):
            ladder[event["rung"]][event["status"]] += 1
    turns = kinds[KIND_RISK_TURN]
    return {
        "days": days,
        "since": since.isoformat(),
        "risk_raised": kinds[KIND_RISK_RAISED],
        "people_with_risk_raised": len(risk_users),
        "by_level": dict(levels),
        "by_detector": dict(sources),
        "per_day": dict(sorted(per_day.items())),
        "risk_turns": turns,
        "reply_revised": kinds[KIND_REPLY_REVISED],
        "revised_rate": _rate(kinds[KIND_REPLY_REVISED], turns),
        "line_in_reply": kinds[KIND_LINE_IN_REPLY],
        "line_rate": _rate(kinds[KIND_LINE_IN_REPLY], turns),
        "last_resort_line": kinds[KIND_LAST_RESORT_LINE],
        "last_resort_rate": _rate(kinds[KIND_LAST_RESORT_LINE], turns),
        "outage_fallbacks": kinds[KIND_OUTAGE_FALLBACK],
        "ladder": {key: dict(counts) for key, counts in ladder.items()},
    }


def fetch_recent_events(db: Any, days: int, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """The recent events, newest window only and capped, with no database id."""
    now = now or datetime.datetime.now(timezone.utc)
    since = now - datetime.timedelta(days=days)
    cursor = db[SAFETY_EVENTS_COLLECTION].find({"at": {"$gte": since}}, {"_id": 0}).limit(MAX_EVENTS)
    return list(cursor)


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_summary(summary: Dict[str, Any]) -> str:
    lines = [
        f"Safety layer, last {summary['days']} days",
        f"  Risk raised: {summary['risk_raised']} times, by {summary['people_with_risk_raised']} people",
        f"    by level:    {summary['by_level'] or '-'}",
        f"    by detector: {summary['by_detector'] or '-'}",
        f"  Replies under a risk level: {summary['risk_turns']}",
        f"    safety check rewrote the draft: {summary['reply_revised']} ({_pct(summary['revised_rate'])})",
        f"    named a human line:             {summary['line_in_reply']} ({_pct(summary['line_rate'])})",
        f"    code had to add the line:       {summary['last_resort_line']} ({_pct(summary['last_resort_rate'])})  <- should stay near 0",
        f"  Model down on a risk turn (fixed reply shown): {summary['outage_fallbacks']}",
        "  Ladder answers (rung: available / unavailable):",
    ]
    for key, _ in LADDER:
        counts = summary["ladder"].get(key, {})
        lines.append(f"    {key}: {counts.get('available', 0)} / {counts.get('unavailable', 0)}")
    if summary["per_day"]:
        lines.append("  Risk raised per day: " + ", ".join(f"{d} {n}" for d, n in summary["per_day"].items()))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Counts of how the crisis safety layer has been behaving.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    from backend.utils.db_utils import get_db

    db = get_db()
    if db is None:
        raise SystemExit("No database is configured, so there are no safety events to read.")
    print(format_summary(summarize_safety_events(fetch_recent_events(db, args.days), days=args.days)))


if __name__ == "__main__":
    main()
