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

A FIVE-HOUR WINDOW can be set the same way, in the same units (LLM_BUDGET_5H_USD / LLM_BUDGET_5H_TOKENS). It is rolling: the last five hours of
usage (this hour and the four before it, so between four and five hours of it), not a window that resets at a fixed time, so it catches a burst the
daily budget would only notice much later. When it blocks, the message says when the oldest hour leaves the window.

PER-PERSON LIMITS: LLM_USER_BUDGET_5H_TOKENS and LLM_USER_BUDGET_DAILY_TOKENS limit what any one signed-in person may use, in tokens (a
person's spend is counted from their finished turns, backend/utils/user_usage.py, which has no per-model prices). The same steps apply, but
only to that person: over 100% they get low-power mode, over the block line they are paused until their window moves on, and nobody else is
affected. Global admins are exempt (they are the ones who manage the budget). Off until set.

EXTENDING A BLOCK: when the global budget has blocked chat, a global admin can grant +25% (EXTENSION_STEP) once per click, as many times as
they like. A grant raises the block line of the scope that is blocking, from 125% to 150%, then 175% and so on, and ends when that scope
resets (a day, a month, or five hours from the grant for the five-hour window). Low-power mode stays on throughout. Each grant is saved with
who and when. A per-person block is not extended by this.

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
    "five_usd": "LLM_BUDGET_5H_USD",
    "five_tokens": "LLM_BUDGET_5H_TOKENS",
}
# Per-person limits, in tokens (see the module docstring). Their scopes start with "my_".
_USER_BUDGET_ENV = {
    "my_day_tokens": "LLM_USER_BUDGET_DAILY_TOKENS",
    "my_five_tokens": "LLM_USER_BUDGET_5H_TOKENS",
}
# A budget key's (scope, unit). "five" is the rolling five-hour window; "my_" scopes are one person's own share.
_KEYS = {
    "day_usd": ("day", "usd"), "month_usd": ("month", "usd"), "day_tokens": ("day", "tokens"), "month_tokens": ("month", "tokens"),
    "five_usd": ("five", "usd"), "five_tokens": ("five", "tokens"),
    "my_day_tokens": ("my_day", "tokens"), "my_five_tokens": ("my_five", "tokens"),
}
GLOBAL_SCOPES = ("day", "month", "five")
FIVE_HOURS = 5
EXTENSION_STEP = 0.25
EXTENSIONS_COLLECTION = "budget_extensions"
_SEVERITY = {OK: 0, NOTICE: 1, THROTTLED: 2, BLOCKED: 3}
# How a scope is said to a person, where it is not already plain English ("day", "month").
_SCOPE_TEXT = {"five": "5-hour", "my_day": "your daily", "my_five": "your 5-hour"}

_cache: Dict[str, Any] = {"at": None, "rows": [], "hourly_at": None, "hourly": [], "ext_at": None, "ext": {}}


def _reset_cache() -> None:
    _cache.update({"at": None, "rows": [], "hourly_at": None, "hourly": [], "ext_at": None, "ext": {}})


def _positive(name: str) -> Optional[float]:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def budgets() -> Dict[str, float]:
    """The global budgets that are set, by key ("day_usd", "month_usd", "day_tokens", "month_tokens", "five_usd", "five_tokens"). Empty
    means there is no global budget."""
    found = {key: _positive(env) for key, env in _BUDGET_ENV.items()}
    return {key: value for key, value in found.items() if value is not None}


def user_budgets() -> Dict[str, float]:
    """The per-person limits that are set ("my_day_tokens", "my_five_tokens"). Empty means there are none."""
    found = {key: _positive(env) for key, env in _USER_BUDGET_ENV.items()}
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


def window_start_hour(now: datetime) -> str:
    """The first hour bucket still inside the five-hour window: this hour and the four before it."""
    return llm_usage.hour_key(now - timedelta(hours=FIVE_HOURS - 1))


def _saved_hourly(db: Any, now: datetime) -> List[Dict[str, Any]]:
    """The saved hour rows inside the five-hour window, cached briefly like the day rows."""
    at = _cache["hourly_at"]
    if at is not None and time.monotonic() - at < CACHE_SECONDS:
        return _cache["hourly"]
    rows = llm_usage.fetch_hourly_docs(db, window_start_hour(now))
    _cache["hourly_at"], _cache["hourly"] = time.monotonic(), rows
    return rows


def _buffered_hourly() -> List[Dict[str, Any]]:
    return [{"hour": hour, "model": model, **counts} for (hour, model), counts in llm_usage.hourly_snapshot().items()]


def _as_aware(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return None


def _saved_extensions(db: Any, now: datetime) -> Dict[str, float]:
    """{scope: extra fraction} for the extensions an admin has granted that have not run out, cached briefly. Raises on a database error."""
    at = _cache["ext_at"]
    if at is not None and time.monotonic() - at < CACHE_SECONDS:
        return _cache["ext"]
    found: Dict[str, float] = {}
    for doc in db[EXTENSIONS_COLLECTION].find({}, {"_id": 0}):
        until = _as_aware(doc.get("until"))
        if until is not None and until > now and doc.get("scope") in GLOBAL_SCOPES:
            found[str(doc["scope"])] = max(float(doc.get("extra") or 0), 0.0)
    _cache["ext_at"], _cache["ext"] = time.monotonic(), found
    return found


def _hour_start(hour: str) -> datetime:
    return datetime.strptime(hour, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)


def five_hour_reset(oldest_hour: Optional[str], now: datetime) -> datetime:
    """When the five-hour window next gets room: when the oldest hour that has any usage in it falls out of the window. With no usage in it
    there is nothing to wait for, so an hour from now."""
    if not oldest_hour:
        return now + timedelta(hours=1)
    return _hour_start(oldest_hour) + timedelta(hours=FIVE_HOURS)


def spent(rows: List[Dict[str, Any]], now: datetime, prices: Dict[str, Dict[str, float]], hourly_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Tokens and (for priced models) dollars used today, this month and (from `hourly_rows`) in the five-hour window. Pure. `unpriced_tokens`
    is what the dollar figure leaves out."""
    today = billing_time.day(now)
    totals = {"day_tokens": 0, "month_tokens": 0, "day_usd": 0.0, "month_usd": 0.0, "unpriced_tokens": 0, "five_tokens": 0, "five_usd": 0.0}
    for row in rows:
        if llm_usage.is_eval_label(row.get("label")):
            continue  # an eval run is counted on the usage page, but it must never be able to pause chat
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
    since = window_start_hour(now)
    oldest: Optional[str] = None
    for row in hourly_rows or []:
        hour = str(row.get("hour") or "")
        if not hour or hour < since:
            continue
        counts = {field: int(row.get(field) or 0) for field in llm_usage.FIELDS}
        tokens = counts["input"] + counts["output"]
        cost = llm_usage.estimate_cost(str(row.get("model")), counts, prices)
        totals["five_tokens"] += tokens
        totals["five_usd"] += cost or 0.0
        if tokens and (oldest is None or hour < oldest):
            oldest = hour
    totals["five_reset"] = five_hour_reset(oldest, now)
    return totals


def _billing_day_start_hour(now: datetime) -> str:
    return llm_usage.hour_key(billing_time.next_day_start(now) - timedelta(days=1))


def person_totals(rows: List[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """One person's tokens in the five-hour window and in today's billing day, from their hour rows (saved plus unsaved). Pure."""
    from backend.utils import user_usage

    five_since = window_start_hour(now)
    five_rows = [row for row in rows if str(row.get("hour") or "") >= five_since and int(row.get("tokens") or 0)]
    oldest = min((str(row["hour"]) for row in five_rows), default=None)
    return {
        "my_five_tokens": user_usage.tokens_since(rows, five_since),
        "my_day_tokens": user_usage.tokens_since(rows, _billing_day_start_hour(now)),
        "my_five_reset": five_hour_reset(oldest, now),
    }


def person_fetch_since(now: datetime) -> str:
    """How far back a person's hour rows must be read to answer both of their limits."""
    return min(window_start_hour(now), _billing_day_start_hour(now))


# ---------------------------------------------------------------------------------------------------------------
# Judging it
# ---------------------------------------------------------------------------------------------------------------

def _next_reset(scope: str, now: datetime, totals: Optional[Dict[str, Any]] = None) -> datetime:
    if scope in ("five", "my_five"):
        found = (totals or {}).get("five_reset" if scope == "five" else "my_five_reset")
        return found if isinstance(found, datetime) else now + timedelta(hours=1)
    return billing_time.next_day_start(now) if scope in ("day", "my_day") else billing_time.next_month_start(now)


def evaluate(totals: Dict[str, Any], limits: Dict[str, float], now: datetime, extensions: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """The guard's state from what has been spent and the limits that are set. Pure. `extensions` is {scope: extra fraction} an admin has
    granted: it raises only that global scope's block line (the throttle line stays at 100%)."""
    notice_at, block_at = thresholds()
    extensions = extensions or {}
    detail: List[Dict[str, Any]] = []
    levels: List[str] = []
    for key, limit in limits.items():
        scope, unit = _KEYS.get(key) or tuple(key.split("_", 1))
        used = float(totals.get(key, 0) or 0)
        fraction = round(used / limit, 4)
        line = block_at + (float(extensions.get(scope, 0) or 0) if scope in GLOBAL_SCOPES else 0.0)
        levels.append(BLOCKED if fraction >= line else THROTTLED if fraction >= 1.0 else NOTICE if fraction >= notice_at else OK)
        detail.append({"scope": scope, "unit": unit, "spent": round(used, 4), "limit": limit, "fraction": fraction})
    if not detail:
        return {"level": OFF, "detail": [], "paused": []}
    # The worst level decides, and among equals the one furthest along. (A raised block line can make the highest fraction a lower level.)
    index = max(range(len(detail)), key=lambda i: (_SEVERITY[levels[i]], detail[i]["fraction"]))
    leading, level = detail[index], levels[index]
    return {
        "level": level,
        "fraction": leading["fraction"],
        "scope": leading["scope"],
        "unit": leading["unit"],
        "spent": leading["spent"],
        "limit": leading["limit"],
        "resets_at": _next_reset(leading["scope"], now, totals).isoformat(),
        "detail": detail,
        "paused": list(PAUSED_FEATURES) if level in LOW_POWER_LEVELS else [],
        "unpriced_tokens": totals.get("unpriced_tokens", 0),
        "blocked_scopes": [item["scope"] for item, lvl in zip(detail, levels) if lvl == BLOCKED],
        "extended_by": float(extensions.get(leading["scope"], 0) or 0) if leading["scope"] in GLOBAL_SCOPES else 0.0,
    }


def current_state(db: Any = None, now: Optional[datetime] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """The guard's state right now. Blocking (a database read when the cache is stale): call it off the event loop. It never
    raises: if the usage can't be read, the guard is off for this call, so cost control can't take chat down. `username` adds that person's
    own limits (user_budgets) to the global ones; leave it out for an admin, who is exempt, and for anything that is not a person's turn."""
    limits = budgets()
    own = user_budgets() if username else {}
    if not limits and not own:
        return {"level": OFF, "detail": [], "paused": []}
    moment = now or datetime.now(timezone.utc)
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            return {"level": OFF, "detail": [], "paused": [], "error": True}
        totals: Dict[str, Any] = {}
        extensions: Dict[str, float] = {}
        if limits:
            rows = _saved_rows(db, moment) + _buffered_rows()
            hourly = (_saved_hourly(db, moment) + _buffered_hourly()) if ("five_usd" in limits or "five_tokens" in limits) else []
            totals = spent(rows, moment, llm_usage.load_prices(), hourly)
            totals["month_usd"] += month_already_spent(moment)
            extensions = _saved_extensions(db, moment)
        if own:
            from backend.utils import user_usage

            mine = user_usage.fetch(db, str(username), person_fetch_since(moment)) + user_usage.unsaved_rows(str(username))
            totals.update(person_totals(mine, moment))
        return evaluate(totals, {**limits, **own}, moment, extensions)
    except Exception:
        logger.warning("[budget] could not read the usage; the budget guard is stepping aside for this call.")
        return {"level": OFF, "detail": [], "paused": [], "error": True}


# ---------------------------------------------------------------------------------------------------------------
# Extending a block
# ---------------------------------------------------------------------------------------------------------------

def extendable_scopes(state: Optional[Dict[str, Any]]) -> List[str]:
    """The global scopes that are blocking chat right now, i.e. the ones an admin's +25% would apply to. A per-person block is not one."""
    return [scope for scope in (state or {}).get("blocked_scopes", []) if scope in GLOBAL_SCOPES]


def grant_extension(db: Any, admin: str, state: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Adds EXTENSION_STEP to every global scope that is blocking chat, until that scope resets (five hours from now for the five-hour window,
    since it has no fixed end). Each grant is recorded with who and when. Nothing blocked, nothing changed. Blocking: call it off the event
    loop. Raises on a database error, so the caller can say it did not work."""
    moment = now or datetime.now(timezone.utc)
    granted = []
    for scope in extendable_scopes(state):
        until = (moment + timedelta(hours=FIVE_HOURS)) if scope == "five" else _next_reset(scope, moment)
        # A grant while an earlier one is still running adds to it; one after it ran out starts again from zero.
        existing = db[EXTENSIONS_COLLECTION].find_one({"scope": scope}, {"_id": 0}) or {}
        until_old = _as_aware(existing.get("until"))
        running = until_old is not None and until_old > moment
        extra = (float(existing.get("extra") or 0) if running else 0.0) + EXTENSION_STEP
        grants = (list(existing.get("grants") or []) if running else [])[-49:]
        grants.append({"by": str(admin)[:200], "at": moment.isoformat(), "extra": round(extra, 4)})
        db[EXTENSIONS_COLLECTION].update_one(
            {"scope": scope}, {"$set": {"scope": scope, "extra": round(extra, 4), "until": until.isoformat(), "grants": grants}}, upsert=True
        )
        granted.append({"scope": scope, "extra": round(extra, 4), "until": until.isoformat()})
    _reset_cache()
    return {"extended": granted}


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


def _when_text(resets_at: Optional[str], scope: Optional[str]) -> str:
    """" until 2026-10-15 (Pacific time)" for a day or a month; " until 3:40 PM (Pacific time)" for a five-hour window, which ends at a time of day."""
    if not resets_at:
        return " for now"
    if scope in ("five", "my_five"):
        moment = _as_aware(resets_at)
        if moment is not None:
            clock = billing_time.local(moment).strftime("%I:%M %p").lstrip("0")
            return f" until {clock} ({billing_time.label()})"
    return f" until {resets_at[:10]} ({billing_time.label()})"


def blocked_reply(resets_at: Optional[str], crisis: bool = False, scope: Optional[str] = None) -> str:
    """What someone is told when chat is paused by the budget. In a crisis the human support line comes first, in code, because
    the model is not being called. `scope` says which limit it was: the person's own, or the shared one."""
    from backend.utils.safety_utils import CRISIS_RESOURCE_LINE

    if crisis:
        return (
            "I'm so sorry: I can't give you a proper answer right now, and I know this is the worst possible moment for that. "
            "Please don't wait on me." + CRISIS_RESOURCE_LINE + " I'd rather you had a person with you in the meantime."
        )
    when = _when_text(resets_at, scope)
    if scope in ("my_day", "my_five"):
        return (
            f"You've reached your own usage limit for now, so I'm paused for you{when}. It's a limit on how much I can do for one person "
            "in a stretch of time, not something you did wrong, and nothing you sent was lost. Please try again after it resets."
        )
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
    scope = state.get("scope")
    own = scope in ("my_day", "my_five")  # the limit in force is this person's own, not the shared one
    view: Dict[str, Any] = {"level": shown, "paused": state.get("paused", []), "resets_at": state.get("resets_at")}
    if own and shown in LOW_POWER_LEVELS:
        view["own_limit"] = True  # lets the banner say "your" limit without sending anyone an amount
    if shown == THROTTLED:
        view["message"] = (
            "Low-power mode for you: deep thinking and some background extras are paused to stay within your usage limit."
            if own else "Low-power mode: deep thinking and some background extras are paused to stay within the usage budget."
        )
    elif shown == BLOCKED:
        view["message"] = "Paused: you have reached your usage limit." if own else "Paused: the usage budget has been reached."
    elif shown == NOTICE:
        view["message"] = f"{round(state.get('fraction', 0) * 100)}% of the {_SCOPE_TEXT.get(scope, scope)} usage budget is used."
    else:
        view["message"] = ""
    if admin and level != OFF:
        view.update({
            "fraction": state.get("fraction"), "scope": scope, "unit": state.get("unit"),
            "spent": state.get("spent"), "limit": state.get("limit"), "detail": state.get("detail", []),
            "unpriced_tokens": state.get("unpriced_tokens", 0),
            # An admin sees whether +25% is on offer (the global budget is what is blocking) and how far it has already been raised.
            "can_extend": bool(extendable_scopes(state)), "extended_by": state.get("extended_by", 0.0),
        })
    return view
