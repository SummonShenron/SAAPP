"""How many tokens each person has used, by minute: what the per-user limits in budget_guard.py are judged against.

llm_usage.py (the usage meter) deliberately keeps no user: it says what the calls cost, not who made them. A per-user limit cannot be
judged without one, so this is a SEPARATE, small store, so the privacy line stays where it was and is easy to see: one row per person and
UTC minute holding only counts (tokens, calls, turns, dollars), never a prompt, a reply or any text. It is written from the end of a chat turn
(llm_turns.finish), in memory first and in batches with the other meters (llm_usage.flush), and every failure here is swallowed: counting
must never cost a reply.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("SASS Logger")

COLLECTION = "llm_usage_user_minutely"
FIELDS = ("tokens", "calls", "turns", "cost_micro")   # cost_micro: what the person's priced calls cost, in millionths of a dollar
MAX_USER_LENGTH = 200
# The same counts rolled up by billing day (Pacific, as everywhere else on the usage page), kept long enough to see a month and a bit of what each person
# costs. One row per person per day, counts only. The minute rows above stay at a few days because the guard only needs the last few hours.
DAILY_COLLECTION = "llm_usage_user_daily"
DAILY_KEEP_SECONDS = 120 * 24 * 60 * 60
MAX_PEOPLE_SHOWN = 25

_lock = threading.Lock()
_pending: Dict[Tuple[str, str], Dict[str, int]] = {}  # (user, minute) -> counts not yet saved
_pending_daily: Dict[Tuple[str, str], Dict[str, int]] = {}  # (user, billing day) -> counts not yet saved
_daily_index_ready = False


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def minute_key(now: Optional[datetime] = None) -> str:
    """Same minutes as llm_usage.minute_key (UTC, "YYYY-MM-DDTHH:MM"); kept here so this module needs nothing else to be read on its own."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def add(user: Optional[str], tokens: int, calls: int = 0, now: Optional[datetime] = None, cost_micro: int = 0) -> None:
    """Counts one finished turn for a person. A turn with no one attached (or nothing used) is not counted. Never raises."""
    try:
        name = str(user or "").strip()[:MAX_USER_LENGTH]
        used = _count(tokens)
        if not name or not (used or calls or cost_micro):
            return
        from backend.utils import billing_time

        with _lock:
            for pending, key in ((_pending, (name, minute_key(now))), (_pending_daily, (name, billing_time.day(now)))):
                slot = pending.setdefault(key, {field: 0 for field in FIELDS})
                slot["tokens"] += used
                slot["calls"] += _count(calls)
                slot["turns"] += 1
                slot["cost_micro"] += _count(cost_micro)
    except Exception:
        pass


def snapshot() -> Dict[Tuple[str, str], Dict[str, int]]:
    with _lock:
        return {key: dict(value) for key, value in _pending.items()}


def reset() -> None:
    with _lock:
        _pending.clear()
        _pending_daily.clear()


def flush(db: Any = None) -> int:
    """Saves the counts (one `$inc` per person and minute) and returns how many rows. If the write fails the counts go back to be tried again
    with the next flush. Blocking: call it off the event loop. Never raises."""
    with _lock:
        pending, pending_daily = dict(_pending), dict(_pending_daily)
        _pending.clear()
        _pending_daily.clear()
    if not pending and not pending_daily:
        return 0
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        from backend.utils import llm_usage

        llm_usage.ensure_expiry(db, COLLECTION)
        for (user, minute), counts in pending.items():
            db[COLLECTION].update_one(
                {"user": user, "minute": minute},
                {"$inc": {f: counts.get(f, 0) for f in FIELDS}, "$setOnInsert": {"at": llm_usage.minute_start(minute)}},
                upsert=True,
            )
        if pending_daily:
            _ensure_daily_expiry(db)
        for (user, day), counts in pending_daily.items():
            db[DAILY_COLLECTION].update_one(
                {"user": user, "day": day},
                {"$inc": {f: counts.get(f, 0) for f in FIELDS}, "$setOnInsert": {"at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        return len(pending)
    except Exception:
        logger.warning("[user_usage] could not save the per-person counts; keeping them for the next flush.")
        with _lock:
            for store, saved in ((_pending, pending), (_pending_daily, pending_daily)):
                for key, counts in saved.items():
                    slot = store.setdefault(key, {field: 0 for field in FIELDS})
                    for field in FIELDS:
                        slot[field] += counts.get(field, 0)
        return 0


def _ensure_daily_expiry(db: Any) -> None:
    """The index that makes the database drop daily rows after DAILY_KEEP_SECONDS. Created once per process; a failure is only logged."""
    global _daily_index_ready
    if _daily_index_ready:
        return
    try:
        db[DAILY_COLLECTION].create_index("at", expireAfterSeconds=DAILY_KEEP_SECONDS)
        _daily_index_ready = True
    except Exception:
        logger.warning("[user_usage] could not create the daily expiry index.")


def fetch_daily(db: Any, days: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The saved per-person day rows for the last `days` billing days, today included, plus what has not been saved yet. Blocking."""
    from backend.utils import billing_time

    first = billing_time.days_back(now or datetime.now(timezone.utc), days)
    rows = list(db[DAILY_COLLECTION].find({"day": {"$gte": first}}, {"_id": 0}))
    with _lock:
        rows.extend({"user": user, "day": day, **counts} for (user, day), counts in _pending_daily.items() if day >= first)
    return rows


EVALS_NAME = "Testing and evals"


def _evals_row(daily: Optional[Iterable[Dict[str, Any]]], days: List[str], grand_cost: float, grand_tokens: int) -> Optional[Dict[str, Any]]:
    """The runs of the evals and tests as one more entry beside the people, from the usage page's daily rows (summarize_daily: `eval_calls`, `eval_tokens`,
    `eval_cost`, the part of each day that came from an eval run). They are not chat turns, so they have no turns and no account; None when there were none."""
    by_day = {str(row.get("day")): row for row in (daily or []) if str(row.get("day")) in set(days)}
    per_day = [round(float((by_day.get(day) or {}).get("eval_cost") or 0), 4) for day in days]
    cost = sum(float((row or {}).get("eval_cost") or 0) for row in by_day.values())
    tokens = sum(_count((row or {}).get("eval_tokens")) for row in by_day.values())
    calls = sum(_count((row or {}).get("eval_calls")) for row in by_day.values())
    if not (calls or tokens or cost):
        return None
    active_days = sum(1 for row in by_day.values() if _count(row.get("eval_calls")) or float(row.get("eval_cost") or 0) > 0)
    return {
        "user": EVALS_NAME, "kind": "evals", "cost": round(cost, 4), "tokens": tokens, "turns": 0, "calls": calls,
        "cost_per_turn": None, "cost_per_active_day": round(cost / active_days, 4) if active_days and cost else None, "active_days": active_days,
        "share": round(cost / grand_cost, 4) if grand_cost else (round(tokens / grand_tokens, 4) if grand_tokens else None),
        "per_day": per_day,
    }


def summarize_users(
    rows: Iterable[Dict[str, Any]], days: List[str], total_cost: Optional[float] = None, limit: int = MAX_PEOPLE_SHOWN, evals: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """What each person cost over `days` (billing days, oldest first), biggest first, for the admin's Sonic Health page. Counts and dollars only. Pure.
    `evals` is the usage page's daily rows: the runs of the evals and tests get their own entry (kind "evals") in the same list, sorted in by cost, so
    testing spend is visible next to what real people cost and is not counted as one of them. `total_cost` is the usage page's own estimate for the whole
    period, so what is tied to neither (background jobs, anything run before the person was known) shows as the difference. `priced` is False when no
    dollars were counted at all (no prices set), and the page then shows tokens instead."""
    wanted = set(days)
    people: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        day = str(row.get("day") or "")
        if day not in wanted:
            continue
        person = people.setdefault(str(row.get("user") or "unknown"), {
            "cost": 0.0, "tokens": 0, "calls": 0, "turns": 0, "per_day": {d: 0.0 for d in days},
        })
        cost = _count(row.get("cost_micro")) / 1_000_000
        person["cost"] += cost
        person["tokens"] += _count(row.get("tokens"))
        person["calls"] += _count(row.get("calls"))
        person["turns"] += _count(row.get("turns"))
        person["per_day"][day] += cost
    total = sum(p["cost"] for p in people.values())
    total_tokens = sum(p["tokens"] for p in people.values())
    evals_row = _evals_row(evals, days, 0.0, 0)
    eval_cost, eval_tokens = (evals_row["cost"], evals_row["tokens"]) if evals_row else (0.0, 0)
    grand_cost, grand_tokens = total + eval_cost, total_tokens + eval_tokens      # shares are of everything counted, people and tests together
    ranked = sorted(people.items(), key=lambda item: (item[1]["cost"], item[1]["tokens"]), reverse=True)
    shown = []
    for user, p in ranked[:max(limit, 1)]:
        active_days = sum(1 for value in p["per_day"].values() if value > 0) or (1 if p["tokens"] else 0)
        shown.append({
            "user": user,
            "kind": "person",
            "cost": round(p["cost"], 4),
            "tokens": p["tokens"],
            "turns": p["turns"],
            "calls": p["calls"],
            "cost_per_turn": round(p["cost"] / p["turns"], 4) if p["turns"] else None,
            "cost_per_active_day": round(p["cost"] / active_days, 4) if active_days else None,
            "active_days": active_days,
            "share": round(p["cost"] / grand_cost, 4) if grand_cost else (round(p["tokens"] / grand_tokens, 4) if grand_tokens else None),
            "per_day": [round(p["per_day"][d], 4) for d in days],
        })
    if evals_row:
        evals_row = _evals_row(evals, days, grand_cost, grand_tokens)
        shown.append(evals_row)
        shown.sort(key=lambda row: (row["cost"], row["tokens"]), reverse=True)
    rest = ranked[max(limit, 1):]
    return {
        "days": list(days),
        "people": len(people),
        "priced": grand_cost > 0,
        "cost": round(total, 4),
        "tokens": total_tokens,
        "rows": shown,
        "others": {"people": len(rest), "cost": round(sum(p["cost"] for _, p in rest), 4), "tokens": sum(p["tokens"] for _, p in rest)} if rest else None,
        "evals": {"cost": evals_row["cost"], "tokens": evals_row["tokens"], "calls": evals_row["calls"]} if evals_row else None,
        "unattributed_cost": round(max(total_cost - total - eval_cost, 0.0), 4) if (total_cost is not None and grand_cost > 0) else None,
    }


MAX_PEOPLE_PER_DAY = 60


def day_breakdowns(rows: Iterable[Dict[str, Any]], daily: Optional[Iterable[Dict[str, Any]]], days: List[str], viewer: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """For every billing day: who used it and what each cost, against the day's whole estimated cost (the usage page's own figure, `daily` = summarize_daily's rows),
    so a person's share is a share of the DAY. Each day lists up to MAX_PEOPLE_PER_DAY accounts, biggest first (`you` marks the viewing admin), the evals and tests as
    their own entry, and what no account owns (background jobs) as the remainder. Counts and dollars only. None when no dollars were counted (no prices set). Pure."""
    day_rows = {str(row.get("day")): row for row in (daily or [])}
    if not any(float((row or {}).get("cost") or 0) > 0 for row in day_rows.values()):
        return None
    people: Dict[str, Dict[str, Dict[str, float]]] = {day: {} for day in days}
    for row in rows:
        day = str(row.get("day") or "")
        if day not in people:
            continue
        entry = people[day].setdefault(str(row.get("user") or "unknown"), {"cost": 0.0, "tokens": 0, "calls": 0, "turns": 0})
        entry["cost"] += _count(row.get("cost_micro")) / 1_000_000
        entry["tokens"] += _count(row.get("tokens"))
        entry["calls"] += _count(row.get("calls"))
        entry["turns"] += _count(row.get("turns"))
    by_day = []
    for day in days:
        source = day_rows.get(day) or {}
        total = round(float(source.get("cost") or 0), 4)
        ranked = sorted(people[day].items(), key=lambda item: (item[1]["cost"], item[1]["tokens"]), reverse=True)
        listed = [{
            "user": user, "you": bool(viewer) and user == viewer, "cost": round(v["cost"], 4), "tokens": int(v["tokens"]), "calls": int(v["calls"]), "turns": int(v["turns"]),
            "share": round(v["cost"] / total, 4) if total > 0 else None,
        } for user, v in ranked[:MAX_PEOPLE_PER_DAY]]
        evals_cost = round(float(source.get("eval_cost") or 0), 4)
        evals = {"cost": evals_cost, "tokens": _count(source.get("eval_tokens")), "calls": _count(source.get("eval_calls")),
                 "share": round(evals_cost / total, 4) if total > 0 else None} if (evals_cost or source.get("eval_calls")) else None
        accounted = sum(v["cost"] for v in people[day].values()) + evals_cost
        background = round(max(total - accounted, 0.0), 4)
        by_day.append({
            "day": day, "total": total, "people": listed, "more_people": max(len(ranked) - MAX_PEOPLE_PER_DAY, 0), "evals": evals,
            "background": {"cost": background, "share": round(background / total, 4) if total > 0 else None} if background > 0 else None,
        })
    return {"days": list(days), "viewer": viewer or None, "by_day": by_day}


def fetch(db: Any, user: str, since_minute: str) -> List[Dict[str, Any]]:
    """One person's saved minute rows from `since_minute` on. Blocking. Raises on a database error (the guard decides what that means)."""
    return list(db[COLLECTION].find({"user": user, "minute": {"$gte": since_minute}}, {"_id": 0}))


def tokens_since(rows: Iterable[Dict[str, Any]], since_minute: str) -> int:
    """Tokens in `rows` (saved or still in memory) from `since_minute` on. Pure."""
    return sum(_count(row.get("tokens")) for row in rows if str(row.get("minute") or "") >= since_minute)


def cost_since(rows: Iterable[Dict[str, Any]], since_minute: str, estimate_rate: Optional[float] = None) -> float:
    """Dollars in `rows` (saved or still in memory) from `since_minute` on. Pure. A row saved before dollars were counted has no `cost_micro` at all (a row
    saved since always has one, even if it is 0); with `estimate_rate` (dollars per token, the day's average) such a row is estimated from its tokens, so a
    person's meter is meaningful from the day it starts counting instead of starting at zero."""
    total = 0.0
    for row in rows:
        if str(row.get("minute") or "") < since_minute:
            continue
        if "cost_micro" in row:
            total += _count(row.get("cost_micro")) / 1_000_000
        elif estimate_rate:
            total += _count(row.get("tokens")) * estimate_rate
    return total


def unsaved_rows(user: str) -> List[Dict[str, Any]]:
    """What this person has used that is not yet saved, shaped like a saved row, so a limit counts the turn that just ended."""
    with _lock:
        return [{"user": u, "minute": minute, **counts} for (u, minute), counts in _pending.items() if u == user]
