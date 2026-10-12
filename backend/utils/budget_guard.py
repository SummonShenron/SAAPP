"""A spending budget for the model calls: Sonic slows itself down before it hits a hard stop, and says so.

Google's own monthly cap is a cliff: spend reaches it and every call fails until the month turns. This puts a ramp in front of it,
using what the usage meter (llm_usage.py) already counts.

SET A BUDGET to turn it on (it is off, and costs nothing, until you do). In dollars, which needs LLM_PRICES (see llm_usage.py), or
in tokens, which needs nothing else:

    LLM_BUDGET_DAILY_USD / LLM_BUDGET_MONTHLY_USD       e.g. 3 and 40
    LLM_BUDGET_DAILY_TOKENS / LLM_BUDGET_MONTHLY_TOKENS e.g. 2000000 and 40000000

Several can be set at once; the one that is furthest along decides. Spend is judged against the budget in four steps:

    ok         under 80%      nothing changes
    notice     80% and over   an admin is told (a banner and a meter chip); nobody else notices anything
    throttled  100% and over  LOW-POWER MODE for everyone: deep thinking is off, the tool agent gets fewer steps, and the extras that
                              each cost a model call are paused (summarising a turn into memory, noting upcoming things, re-checking
                              grounded answers, conversation openers). Replies still work. A visible banner says what is paused
                              and when it ends.
    blocked    125% and over  no model calls for chat at all, until the day or month turns: a plain reply says so. The one exception
                              is a person who has signalled risk: they still get the human support line, in code, no model needed.

Tune with LLM_BUDGET_NOTICE_AT (0.8) and LLM_BUDGET_BLOCK_AT (1.25).

THE METER ONLY COUNTS FROM THE DAY IT WAS DEPLOYED, so in that first month the monthly dollar figure would leave out everything spent
before then. LLM_BUDGET_MONTH_ALREADY_USD="2026-10:50.02" adds that amount to that month's dollars (and only that month's: it does
nothing the month after, so there is nothing to remember to remove). Read it off Google's Spend page for the days before deploy. A sensible setup is a monthly budget a little under Google's cap,
so the throttle starts well before the cliff.

HOW IT BEHAVES
- It FAILS OPEN. If the usage can't be read (database trouble), the guard steps aside: chat keeps working. Cost control must never be
  the reason Sonic is down.
- NO MODEL IS INVOLVED and nothing here is stored: it reads the meter's saved rows (cached 45 seconds) plus what is still in memory.
- WHAT PEOPLE SEE depends on who they are. Everyone sees low-power and paused messages (they explain a change in behaviour); only an
  admin sees amounts and percentages.
- Days and months are Google's: Pacific time (backend/utils/billing_time.py; LLM_BILLING_TIMEZONE changes it), so "today" and "this month"
  turn over when they do on Google's Spend page and when its quota resets. Still keep the budget a little under the cap.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.utils import billing_time, llm_usage

logger = logging.getLogger("SASS Logger")

OFF, OK, NOTICE, THROTTLED, BLOCKED = "off", "ok", "notice", "throttled", "blocked"
LOW_POWER_LEVELS = (THROTTLED, BLOCKED)
THROTTLED_TOOL_STEPS = 4
CACHE_SECONDS = 45

# What low-power mode pauses, as shown to people. Each is something that costs a model call and isn't needed to answer.
PAUSED_FEATURES = [
    "deep thinking",
    "extra tool-agent steps",
    "summarising conversations into memory",
    "noting upcoming things",
    "re-checking grounded answers",
    "conversation openers",
]

_BUDGET_ENV = {
    "day_usd": "LLM_BUDGET_DAILY_USD",
    "month_usd": "LLM_BUDGET_MONTHLY_USD",
    "day_tokens": "LLM_BUDGET_DAILY_TOKENS",
    "month_tokens": "LLM_BUDGET_MONTHLY_TOKENS",
}

_cache: Dict[str, Any] = {"at": None, "rows": []}


def _reset_cache() -> None:
    _cache["at"] = None
    _cache["rows"] = []


def _positive(name: str) -> Optional[float]:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def budgets() -> Dict[str, float]:
    """The budgets that are set, by key ("day_usd", "month_usd", "day_tokens", "month_tokens"). Empty means the guard is off."""
    found = {key: _positive(env) for key, env in _BUDGET_ENV.items()}
    return {key: value for key, value in found.items() if value is not None}


def month_already_spent(now: datetime) -> float:
    """Dollars already spent this month before the meter existed, from LLM_BUDGET_MONTH_ALREADY_USD="YYYY-MM:amount" (several may be
    given, comma separated). Only the entry for the current month counts; anything malformed is ignored."""
    total = 0.0
    for part in os.getenv("LLM_BUDGET_MONTH_ALREADY_USD", "").split(","):
        month, _, amount = part.strip().partition(":")
        if month == billing_time.month(now):
            try:
                total += max(float(amount), 0.0)
            except ValueError:
                continue
    return total


def thresholds() -> Tuple[float, float]:
    """(notice_at, block_at) as fractions of a budget, kept in a sane order whatever was typed."""
    notice = _positive("LLM_BUDGET_NOTICE_AT") or 0.8
    block = _positive("LLM_BUDGET_BLOCK_AT") or 1.25
    notice = min(notice, 0.99)
    return notice, max(block, 1.01)


# ---------------------------------------------------------------------------------------------------------------
# What has been spent
# ---------------------------------------------------------------------------------------------------------------

def _month_start(now: datetime) -> str:
    return billing_time.month_start(now)


def _saved_rows(db: Any, now: datetime) -> List[Dict[str, Any]]:
    """This month's saved usage rows, cached briefly (the guard is asked on every chat turn)."""
    at = _cache["at"]
    if at is not None and time.monotonic() - at < CACHE_SECONDS:
        return _cache["rows"]
    rows = list(db[llm_usage.COLLECTION].find({"day": {"$gte": _month_start(now)}}, {"_id": 0}))
    _cache["at"], _cache["rows"] = time.monotonic(), rows
    return rows


def _buffered_rows() -> List[Dict[str, Any]]:
    return [{"day": day, "model": model, "label": label, **counts} for (day, model, label), counts in llm_usage.snapshot().items()]


def spent(rows: List[Dict[str, Any]], now: datetime, prices: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Tokens and (for priced models) dollars used today and this month. Pure. `unpriced_tokens` is what the dollar figure leaves out."""
    today = billing_time.day(now)
    totals = {"day_tokens": 0, "month_tokens": 0, "day_usd": 0.0, "month_usd": 0.0, "unpriced_tokens": 0}
    for row in rows:
        counts = {field: int(row.get(field) or 0) for field in llm_usage.FIELDS}
        tokens = counts["input"] + counts["output"]
        cost = llm_usage.estimate_cost(str(row.get("model")), counts, prices)
        is_today = row.get("day") == today
        totals["month_tokens"] += tokens
        totals["day_tokens"] += tokens if is_today else 0
        if cost is None:
            totals["unpriced_tokens"] += tokens
        else:
            totals["month_usd"] += cost
            totals["day_usd"] += cost if is_today else 0.0
    return totals


# ---------------------------------------------------------------------------------------------------------------
# Judging it
# ---------------------------------------------------------------------------------------------------------------

def _next_reset(scope: str, now: datetime) -> datetime:
    return billing_time.next_day_start(now) if scope == "day" else billing_time.next_month_start(now)


def evaluate(totals: Dict[str, Any], limits: Dict[str, float], now: datetime) -> Dict[str, Any]:
    """The guard's state from what has been spent and the budgets that are set. Pure."""
    notice_at, block_at = thresholds()
    detail = []
    for key, limit in limits.items():
        scope, unit = key.split("_", 1)
        used = float(totals.get(key, 0) or 0)
        detail.append({"scope": scope, "unit": unit, "spent": round(used, 4), "limit": limit, "fraction": round(used / limit, 4)})
    if not detail:
        return {"level": OFF, "detail": [], "paused": []}
    leading = max(detail, key=lambda item: item["fraction"])
    fraction = leading["fraction"]
    level = BLOCKED if fraction >= block_at else THROTTLED if fraction >= 1.0 else NOTICE if fraction >= notice_at else OK
    return {
        "level": level,
        "fraction": fraction,
        "scope": leading["scope"],
        "unit": leading["unit"],
        "spent": leading["spent"],
        "limit": leading["limit"],
        "resets_at": _next_reset(leading["scope"], now).isoformat(),
        "detail": detail,
        "paused": list(PAUSED_FEATURES) if level in LOW_POWER_LEVELS else [],
        "unpriced_tokens": totals.get("unpriced_tokens", 0),
    }


def current_state(db: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The guard's state right now. Blocking (a database read when the cache is stale): call it off the event loop. It never
    raises: if the usage can't be read, the guard is off for this call, so cost control can't take chat down."""
    limits = budgets()
    if not limits:
        return {"level": OFF, "detail": [], "paused": []}
    moment = now or datetime.now(timezone.utc)
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            return {"level": OFF, "detail": [], "paused": [], "error": True}
        rows = _saved_rows(db, moment) + _buffered_rows()
        totals = spent(rows, moment, llm_usage.load_prices())
        totals["month_usd"] += month_already_spent(moment)
        return evaluate(totals, limits, moment)
    except Exception:
        logger.warning("[budget] could not read the usage; the budget guard is stepping aside for this call.")
        return {"level": OFF, "detail": [], "paused": [], "error": True}


# ---------------------------------------------------------------------------------------------------------------
# What it changes
# ---------------------------------------------------------------------------------------------------------------

def low_power(state: Optional[Dict[str, Any]]) -> bool:
    return (state or {}).get("level") in LOW_POWER_LEVELS


def allows(feature: str, state: Optional[Dict[str, Any]]) -> bool:
    """Whether an optional extra (one that costs a model call and isn't needed to answer) may run. `feature` names it for readers
    and for a future per-feature rule; today every extra is paused together in low-power mode."""
    return not low_power(state)


def tool_steps(default: int, state: Optional[Dict[str, Any]]) -> int:
    return min(default, THROTTLED_TOOL_STEPS) if low_power(state) else default


def blocked(state: Optional[Dict[str, Any]]) -> bool:
    return (state or {}).get("level") == BLOCKED


def blocked_reply(resets_at: Optional[str], crisis: bool = False) -> str:
    """What someone is told when chat is paused by the budget. In a crisis the human support line comes first, in code, because
    the model is not being called."""
    from backend.utils.safety_utils import CRISIS_RESOURCE_LINE

    if crisis:
        return (
            "I'm so sorry: I can't give you a proper answer right now, and I know this is the worst possible moment for that. "
            "Please don't wait on me." + CRISIS_RESOURCE_LINE + " I'd rather you had a person with you in the meantime."
        )
    when = f" until {resets_at[:10]} ({billing_time.label()})" if resets_at else " for now"
    return (
        f"I've reached my usage limit and I'm paused{when}. That's a limit on my side, not something you did, "
        "and nothing you sent was lost. Please try again after it resets."
    )


def public_view(state: Optional[Dict[str, Any]], admin: bool) -> Dict[str, Any]:
    """What the app is told. Everyone gets the level, what is paused and when it ends; only an admin gets amounts and percentages,
    and only an admin is told about the early 'notice' step (for everyone else it is invisible, since nothing has changed yet)."""
    state = state or {"level": OFF}
    level = state.get("level", OFF)
    shown = level if (admin or level in LOW_POWER_LEVELS) else OK if level == NOTICE else level
    view: Dict[str, Any] = {"level": shown, "paused": state.get("paused", []), "resets_at": state.get("resets_at")}
    if shown == THROTTLED:
        view["message"] = "Low-power mode: deep thinking and some background extras are paused to stay within the usage budget."
    elif shown == BLOCKED:
        view["message"] = "Paused: the usage budget has been reached."
    elif shown == NOTICE:
        view["message"] = f"{round(state.get('fraction', 0) * 100)}% of the {state.get('scope')} usage budget is used."
    else:
        view["message"] = ""
    if admin and level != OFF:
        view.update({
            "fraction": state.get("fraction"), "scope": state.get("scope"), "unit": state.get("unit"),
            "spent": state.get("spent"), "limit": state.get("limit"), "detail": state.get("detail", []),
            "unpriced_tokens": state.get("unpriced_tokens", 0),
        })
    return view
