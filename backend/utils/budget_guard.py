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

Tune with LLM_BUDGET_NOTICE_AT (0.8), LLM_BUDGET_THROTTLE_AT (1.0) and LLM_BUDGET_BLOCK_AT (1.25). For a budget that is a HARD CAP (the day's total may not
pass it), set LLM_BUDGET_BLOCK_AT=1 and keep a low-power step before it with LLM_BUDGET_THROTTLE_AT=0.8. For example, $5 a day:
LLM_BUDGET_DAILY_USD=5, LLM_BUDGET_NOTICE_AT=0.6, LLM_BUDGET_THROTTLE_AT=0.8, LLM_BUDGET_BLOCK_AT=1.

A FIVE-HOUR WINDOW is PER PERSON: LLM_BUDGET_5H_USD / LLM_BUDGET_5H_TOKENS (or LLM_USER_BUDGET_5H_*, which wins when both are set) limit what one person may
use in their own window, so one person's burst never pauses chat for everyone. A window shared by everyone is still possible but is off unless
LLM_SHARED_BUDGET_5H_USD / LLM_SHARED_BUDGET_5H_TOKENS are set. A window is a session, not a rolling count: the
first use starts a window that lasts exactly five hours, whatever happens in it, and when it ends usage counts from zero again. The next window
starts at the next use after that. So someone who maxes it out three minutes before it ends can use Sonic again in three minutes. It catches a burst
the daily budget would only notice much later. When it blocks, the message gives the exact time the window ends.

PER-PERSON LIMITS: LLM_USER_BUDGET_DAILY_USD and LLM_USER_BUDGET_5H_USD (or the _TOKENS forms) limit what any one signed-in person may use. A
person's spend is counted from their finished turns (backend/utils/user_usage.py: tokens, and dollars priced per call from LLM_PRICES; a model with no
price adds tokens but no dollars). The same steps apply, but only to that person: over the throttle line they get low-power mode, over the block line
they are paused until their day or window moves on, and nobody else is affected. EVERYONE meets these, admins included, so that an admin's testing
cannot use up the shared budget other people's meters are drawn from; set LLM_USER_BUDGET_EXEMPT_ADMINS=true to let admins off. Off until set.
Evals are not part of any of this: they are counted on the usage page but never against a budget (llm_usage.mark_as_eval).

EXTENDING A BLOCK: when the global budget has blocked chat, a global admin can grant +25% (EXTENSION_STEP) once per click, as many times as
they like. A grant raises the block line of the scope that is blocking, from 125% to 150%, then 175% and so on, and ends when that scope
resets (a day, a month, or the end of the current five-hour window). Low-power mode stays on throughout. Each grant is saved with
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
import uuid
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
    "making pictures",
    "deep thinking",
    "extra tool-agent steps",
    "summarising conversations into memory",
    "noting upcoming things",
    "re-checking grounded answers",
    "conversation openers",
    "scheduled tasks",
    "meeting notes",
    "calendar heads-ups",
    "getting-to-know-you questions",
]

_BUDGET_ENV = {
    "day_usd": "LLM_BUDGET_DAILY_USD",
    "month_usd": "LLM_BUDGET_MONTHLY_USD",
    "day_tokens": "LLM_BUDGET_DAILY_TOKENS",
    "month_tokens": "LLM_BUDGET_MONTHLY_TOKENS",
    # A window shared by everyone. Off unless these are set: LLM_BUDGET_5H_* is the PER-PERSON window (below), because one person's burst should not pause chat for everyone.
    "five_usd": "LLM_SHARED_BUDGET_5H_USD",
    "five_tokens": "LLM_SHARED_BUDGET_5H_TOKENS",
}
# Per-person limits, in tokens or dollars (see the module docstring). Their scopes start with "my_".
_USER_BUDGET_ENV = {
    "my_day_tokens": "LLM_USER_BUDGET_DAILY_TOKENS",
    "my_five_tokens": "LLM_USER_BUDGET_5H_TOKENS",
    "my_day_usd": "LLM_USER_BUDGET_DAILY_USD",
    "my_five_usd": "LLM_USER_BUDGET_5H_USD",
}
# The per-person 5-hour window is also read from the original LLM_BUDGET_5H_* names (used when the LLM_USER_ one is not set), so an existing setting keeps working.
_USER_FIVE_FALLBACK = {"my_five_tokens": "LLM_BUDGET_5H_TOKENS", "my_five_usd": "LLM_BUDGET_5H_USD"}
# A budget key's (scope, unit). "five" is the five-hour window that starts at the first use; "my_" scopes are one person's own share.
_KEYS = {
    "day_usd": ("day", "usd"), "month_usd": ("month", "usd"), "day_tokens": ("day", "tokens"), "month_tokens": ("month", "tokens"),
    "five_usd": ("five", "usd"), "five_tokens": ("five", "tokens"),
    "my_day_tokens": ("my_day", "tokens"), "my_five_tokens": ("my_five", "tokens"),
    "my_day_usd": ("my_day", "usd"), "my_five_usd": ("my_five", "usd"),
}
MY_FIVE_KEYS = ("my_five_tokens", "my_five_usd")
GLOBAL_SCOPES = ("day", "month", "five")
FIVE_HOURS = 5
WINDOW = timedelta(hours=FIVE_HOURS)
MAX_WALK = 12  # windows followed forward from a saved start; more than this means nothing has been used for a long time
WINDOW_LOOKBACK = timedelta(hours=48)  # a saved start older than this is not worth following
WINDOWS_COLLECTION = "budget_windows"
EXTENSION_STEP = 0.25
EXTENSIONS_COLLECTION = "budget_extensions"
USER_EXTENSIONS_COLLECTION = "budget_user_extensions"
REQUESTS_COLLECTION = "budget_increase_requests"
OWN_SCOPES = ("my_day", "my_five")
REQUEST_HISTORY = timedelta(days=14)  # how long a decided request stays listed for the admins
_SEVERITY = {OK: 0, NOTICE: 1, THROTTLED: 2, BLOCKED: 3}
# How a scope is said to a person, where it is not already plain English ("day", "month").
_SCOPE_TEXT = {"five": "5-hour", "my_day": "your daily", "my_five": "your 5-hour"}

_cache: Dict[str, Any] = {"at": None, "rows": [], "five_at": None, "five_rows": [], "five_anchor": None, "ext_at": None, "ext": {}, "uext": {}}


def _reset_cache() -> None:
    _cache.update({"at": None, "rows": [], "five_at": None, "five_rows": [], "five_anchor": None, "ext_at": None, "ext": {}, "uext": {}})


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
    """The per-person limits that are set ("my_day_tokens", "my_five_tokens", "my_day_usd", "my_five_usd"). Empty means there are none."""
    found = {key: _positive(env) for key, env in _USER_BUDGET_ENV.items()}
    for key, env in _USER_FIVE_FALLBACK.items():
        if found.get(key) is None:
            found[key] = _positive(env)
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
    """(notice_at, block_at) as fractions of a budget, kept in a sane order whatever was typed. The block line may be as low as 1.0: a budget that is
    a hard cap, not a soft one (set LLM_BUDGET_BLOCK_AT=1 and, to keep a low-power step before it, LLM_BUDGET_THROTTLE_AT=0.8)."""
    notice = _positive("LLM_BUDGET_NOTICE_AT") or 0.8
    block = _positive("LLM_BUDGET_BLOCK_AT") or 1.25
    return min(notice, 0.99), max(block, 1.0)


def throttle_at() -> float:
    """The fraction of a budget at which low-power mode starts (default 1.0, as it always was), never above the block line."""
    notice, block = thresholds()
    return min(_positive("LLM_BUDGET_THROTTLE_AT") or 1.0, block)


def limit_username(username: Optional[str], is_admin: bool) -> Optional[str]:
    """Who the per-person limits are judged for: the person, admins included, so an admin's own testing cannot use up the shared budget that
    everyone else's meter is drawn from. An admin is let off only when LLM_USER_BUDGET_EXEMPT_ADMINS is set to true."""
    if is_admin and os.getenv("LLM_USER_BUDGET_EXEMPT_ADMINS", "").strip().lower() in ("1", "true", "yes"):
        return None
    return username


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


def resolve_window(anchor: Optional[datetime], usage_times: List[datetime], now: datetime) -> Optional[Tuple[datetime, datetime]]:
    """(start, end) of the five-hour window `now` falls in, or None when it is idle (nothing used since the last window ended). A window starts at
    the first use after the previous one ended and lasts exactly five hours, whatever happens inside it. `anchor` is the start of the last window
    known (None if there is none), `usage_times` the moments something was used (in order). Pure."""
    start = anchor
    for _ in range(MAX_WALK):
        if start is not None and now < start + WINDOW:
            return start, start + WINDOW
        after = start + WINDOW if start is not None else now - WINDOW
        following = next((moment for moment in usage_times if moment >= after), None)
        if following is None or following > now:
            return None
        start = following
    return None


def _usage_time(row: Dict[str, Any]) -> Optional[datetime]:
    """When a minute row says something was used, or None for a row that is empty or malformed."""
    try:
        if not (int(row.get("calls") or 0) or int(row.get("tokens") or 0) or int(row.get("input") or 0) or int(row.get("output") or 0)):
            return None
        return llm_usage.minute_start(str(row["minute"]))
    except (KeyError, ValueError, TypeError):
        return None


def _read_anchor(db: Any, scope: str, now: datetime, user: str = "") -> Optional[datetime]:
    """The saved start of the last window for a scope (and person) as of `now`, or None when there is none or it is too old to follow. Raises
    on a database error."""
    doc = db[WINDOWS_COLLECTION].find_one({"scope": scope, "user": user}, {"_id": 0}) or {}
    start = _as_aware(doc.get("start"))
    return start if start is not None and start >= now - WINDOW_LOOKBACK else None


def _save_anchor(db: Any, scope: str, start: datetime, user: str = "") -> None:
    """Remembers when the current window began, so the next look continues from it. Best effort: it can be worked out again from the usage."""
    try:
        db[WINDOWS_COLLECTION].update_one(
            {"scope": scope, "user": user}, {"$set": {"scope": scope, "user": user, "start": start.isoformat()}}, upsert=True
        )
    except Exception:
        logger.warning("[budget] could not save when the %s window began; it will be worked out again.", scope)


def fetch_since(anchor: Optional[datetime], now: datetime) -> str:
    """How far back minute rows must be read to follow a window from its saved start (or, with none, to find the one in progress)."""
    return llm_usage.minute_key(max(anchor, now - WINDOW_LOOKBACK) if anchor is not None else now - WINDOW)


def _saved_five(db: Any, now: datetime) -> Tuple[List[Dict[str, Any]], Optional[datetime]]:
    """The saved minute rows the shared window needs and its saved start, cached briefly like the day rows. Raises on a database error."""
    at = _cache["five_at"]
    if at is not None and time.monotonic() - at < CACHE_SECONDS:
        return _cache["five_rows"], _cache["five_anchor"]
    anchor = _read_anchor(db, "five", now)
    rows = llm_usage.fetch_minute_docs(db, fetch_since(anchor, now))
    _cache["five_at"], _cache["five_rows"], _cache["five_anchor"] = time.monotonic(), rows, anchor
    return rows, anchor


def _buffered_minutes() -> List[Dict[str, Any]]:
    return [{"minute": minute, "model": model, **counts} for (minute, model), counts in llm_usage.minute_snapshot().items()]


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


def _saved_user_extensions(db: Any, username: str, now: datetime) -> Dict[str, float]:
    """{scope: extra fraction} of the increases an admin has approved for this person that have not run out, cached briefly per person.
    Raises on a database error."""
    hit = _cache["uext"].get(username)
    if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    found: Dict[str, float] = {}
    for doc in db[USER_EXTENSIONS_COLLECTION].find({"user": username}, {"_id": 0}):
        until = _as_aware(doc.get("until"))
        if until is not None and until > now and doc.get("scope") in OWN_SCOPES:
            found[str(doc["scope"])] = max(float(doc.get("extra") or 0), 0.0)
    _cache["uext"][username] = (time.monotonic(), found)
    return found


def spent(
    rows: List[Dict[str, Any]], now: datetime, prices: Dict[str, Dict[str, float]],
    minute_rows: Optional[List[Dict[str, Any]]] = None, anchor: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Tokens and (for priced models) dollars used today, this month and, from `minute_rows`, in the five-hour window that `anchor` (the start
    of the last known window) leads to. Pure. `unpriced_tokens` is what the dollar figure leaves out. `five_start` is when the window in progress
    began (None when idle) and `five_reset` when it ends."""
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
    minute_rows = minute_rows or []
    window = resolve_window(anchor, sorted(t for t in (_usage_time(r) for r in minute_rows) if t is not None), now)
    totals["five_start"], totals["five_reset"] = (window if window else (None, now + WINDOW))
    if window:
        first = llm_usage.minute_key(window[0])
        for row in minute_rows:
            if str(row.get("minute") or "") < first:
                continue
            counts = {field: int(row.get(field) or 0) for field in llm_usage.FIELDS}
            cost = llm_usage.estimate_cost(str(row.get("model")), counts, prices)
            totals["five_tokens"] += counts["input"] + counts["output"]
            totals["five_usd"] += cost or 0.0
    return totals


def _billing_day_start_minute(now: datetime) -> str:
    return llm_usage.minute_key(billing_time.next_day_start(now) - timedelta(days=1))


def person_totals(rows: List[Dict[str, Any]], now: datetime, anchor: Optional[datetime] = None, estimate_rate: Optional[float] = None) -> Dict[str, Any]:
    """One person's tokens and dollars in their own five-hour window (started by their first use, like the shared one) and in today's billing day, from
    their minute rows (saved plus unsaved). `estimate_rate` (dollars per token) prices rows saved before dollars were counted. Pure."""
    from backend.utils import user_usage

    times = sorted(t for t in (_usage_time(r) for r in rows) if t is not None)
    window = resolve_window(anchor, times, now)
    return {
        "my_five_tokens": user_usage.tokens_since(rows, llm_usage.minute_key(window[0])) if window else 0,
        "my_day_tokens": user_usage.tokens_since(rows, _billing_day_start_minute(now)),
        "my_five_usd": user_usage.cost_since(rows, llm_usage.minute_key(window[0]), estimate_rate) if window else 0.0,
        "my_day_usd": user_usage.cost_since(rows, _billing_day_start_minute(now), estimate_rate),
        "my_five_start": window[0] if window else None,
        "my_five_reset": window[1] if window else now + WINDOW,
    }


def person_fetch_since(now: datetime, anchor: Optional[datetime] = None) -> str:
    """How far back a person's minute rows must be read to answer both of their limits."""
    return min(fetch_since(anchor, now), _billing_day_start_minute(now))


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
    throttle = throttle_at()
    notice_at = min(notice_at, max(throttle - 0.01, 0.01))
    extensions = extensions or {}
    detail: List[Dict[str, Any]] = []
    levels: List[str] = []
    for key, limit in limits.items():
        scope, unit = _KEYS.get(key) or tuple(key.split("_", 1))
        used = float(totals.get(key, 0) or 0)
        fraction = round(used / limit, 4)
        line = block_at + (float(extensions.get(scope, 0) or 0) if scope in GLOBAL_SCOPES else 0.0)
        levels.append(BLOCKED if fraction >= line else THROTTLED if fraction >= throttle else NOTICE if fraction >= notice_at else OK)
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
        "resets": {item["scope"]: _next_reset(item["scope"], now, totals).isoformat() for item in detail},
        "extended_by": float(extensions.get(leading["scope"], 0) or 0) if leading["scope"] in GLOBAL_SCOPES else 0.0,
    }


def current_state(db: Any = None, now: Optional[datetime] = None, username: Optional[str] = None) -> Dict[str, Any]:
    """The guard's state right now. Blocking (a database read when the cache is stale): call it off the event loop. It never
    raises: if the usage can't be read, the guard is off for this call, so cost control can't take chat down. `username` adds that person's
    own limits (user_budgets) to the global ones; leave it out for anything that is not a person's turn (use limit_username to decide for an admin)."""
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
        user_extended: Dict[str, float] = {}
        if limits:
            rows = _saved_rows(db, moment) + _buffered_rows()
            minute_rows: List[Dict[str, Any]] = []
            anchor: Optional[datetime] = None
            has_window = "five_usd" in limits or "five_tokens" in limits
            if has_window:
                saved, anchor = _saved_five(db, moment)
                minute_rows = saved + _buffered_minutes()
            totals = spent(rows, moment, llm_usage.load_prices(), minute_rows, anchor)
            totals["month_usd"] += month_already_spent(moment)
            if has_window and totals["five_start"] is not None and totals["five_start"] != anchor:
                _save_anchor(db, "five", totals["five_start"])  # a new window began: remember it, so the next look continues from it
                _cache["five_anchor"] = totals["five_start"]
            extensions = _saved_extensions(db, moment)
        if own:
            from backend.utils import user_usage

            who = str(username)
            # An approved increase raises that person's limit for the scope it was granted for, until that window or day ends.
            user_extended = _saved_user_extensions(db, who, moment)
            if user_extended:
                own = {key: limit * (1 + user_extended.get(_KEYS[key][0], 0.0)) for key, limit in own.items()}
            my_anchor = _read_anchor(db, "my_five", moment, who) if any(key in own for key in MY_FIVE_KEYS) else None
            mine = user_usage.fetch(db, who, person_fetch_since(moment, my_anchor)) + user_usage.unsaved_rows(who)
            # Turns recorded before dollars were counted are priced at today's average price per token (known only when a global budget is set).
            rate = (totals.get("day_usd", 0.0) / totals["day_tokens"]) if totals.get("day_tokens") else None
            totals.update(person_totals(mine, moment, my_anchor, rate))
            if any(key in own for key in MY_FIVE_KEYS) and totals["my_five_start"] is not None and totals["my_five_start"] != my_anchor:
                _save_anchor(db, "my_five", totals["my_five_start"], who)
        state = evaluate(totals, {**limits, **own}, moment, extensions)
        if user_extended:
            state["user_extended"] = user_extended
        return state
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
    """Adds EXTENSION_STEP to every global scope that is blocking chat, until that scope resets (the end of the window in progress for the five-hour one,
    the end of the day or month otherwise). Each grant is recorded with who and when. Nothing blocked, nothing changed. Blocking: call it off the event
    loop. Raises on a database error, so the caller can say it did not work."""
    moment = now or datetime.now(timezone.utc)
    granted = []
    for scope in extendable_scopes(state):
        # It ends when the scope does: the end of the five-hour window in progress, the day or the month.
        until = _as_aware(((state or {}).get("resets") or {}).get(scope)) or _next_reset(scope, moment)
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
# A person asking for more
# ---------------------------------------------------------------------------------------------------------------

def leading_own(state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The person's own limit that is furthest along (the 5-hour window or the day), as a detail row; None if they have none."""
    mine = [item for item in (state or {}).get("detail", []) if item.get("scope") in OWN_SCOPES]
    return max(mine, key=lambda item: item["fraction"]) if mine else None


def can_request_increase(state: Optional[Dict[str, Any]]) -> bool:
    """Whether the person is far enough along their own limit (the notice line or past it) to ask for more."""
    lead = leading_own(state)
    return lead is not None and fraction_level(lead["fraction"]) != OK


def _active_requests(db: Any, username: str, scope: str, now: datetime) -> List[Dict[str, Any]]:
    """This person's requests for this scope whose day or window has not ended yet."""
    found = db[REQUESTS_COLLECTION].find({"user": username, "scope": scope}, {"_id": 0})
    return [doc for doc in found if (_as_aware(doc.get("until")) or now) > now]


def request_status(db: Any, username: str, state: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[str]:
    """"pending" or "declined" for the person's request on the limit that is furthest along, while that day or window lasts; else None.
    Blocking (one small read): call it off the event loop. Raises on a database error."""
    lead = leading_own(state)
    if lead is None:
        return None
    moment = now or datetime.now(timezone.utc)
    statuses = {doc.get("status") for doc in _active_requests(db, username, lead["scope"], moment)}
    return "pending" if "pending" in statuses else "declined" if "declined" in statuses else None


def request_increase(db: Any, username: str, state: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """A person asks the admins to raise their own limit by EXTENSION_STEP, for the limit that is furthest along (the 5-hour window or the day),
    until that window or day ends. Asking again while a request is waiting returns that one. Raises ValueError (with words for the person) when there is
    nothing to ask for or a request for this period was already declined. Blocking: call it off the event loop. Raises on a database error."""
    lead = leading_own(state)
    if lead is None:
        raise ValueError("You have no usage limit of your own to raise.")
    if not can_request_increase(state):
        raise ValueError("You have not used enough of your limit to ask for more yet.")
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scope = lead["scope"]
    existing = _active_requests(db, username, scope, moment)
    waiting = next((doc for doc in existing if doc.get("status") == "pending"), None)
    if waiting is not None:
        return waiting
    if any(doc.get("status") == "declined" for doc in existing):
        raise ValueError("An admin has declined your request for this period. You can ask again once it resets.")
    until = _as_aware(((state or {}).get("resets") or {}).get(scope)) or _next_reset(scope, moment)
    doc = {
        "id": uuid.uuid4().hex, "user": str(username)[:200], "scope": scope, "status": "pending", "requested_at": moment.isoformat(),
        "until": until.isoformat(), "unit": lead.get("unit"), "spent": lead.get("spent"), "limit": lead.get("limit"), "fraction": lead.get("fraction"),
    }
    db[REQUESTS_COLLECTION].update_one({"id": doc["id"]}, {"$set": doc}, upsert=True)
    return doc


def list_requests(db: Any, now: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """For the admins: requests waiting (whose day or window has not ended), and the last few decided or lapsed ones from the past two weeks.
    Blocking: call it off the event loop. Raises on a database error."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    docs = list(db[REQUESTS_COLLECTION].find({"requested_at": {"$gte": (moment - REQUEST_HISTORY).isoformat()}}, {"_id": 0}))
    pending, recent = [], []
    for doc in docs:
        waiting = doc.get("status") == "pending" and (_as_aware(doc.get("until")) or moment) > moment
        (pending if waiting else recent).append(doc)
    pending.sort(key=lambda doc: str(doc.get("requested_at")))
    recent.sort(key=lambda doc: str(doc.get("decided_at") or doc.get("requested_at")), reverse=True)
    return {"pending": pending, "recent": recent[:10]}


def decide_request(db: Any, admin: str, request_id: str, approve: bool, now: Optional[datetime] = None) -> Dict[str, Any]:
    """An admin approves or declines one waiting request. Approving raises that person's limit for that scope by EXTENSION_STEP (on top of any increase
    still running), until the day or window the request was made in ends; it is saved with who and when. Raises LookupError for a request that does
    not exist and ValueError for one already decided or whose day or window has ended. Blocking: call it off the event loop."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    doc = db[REQUESTS_COLLECTION].find_one({"id": request_id}, {"_id": 0})
    if not doc:
        raise LookupError("No such request.")
    if doc.get("status") != "pending":
        raise ValueError("That request has already been decided.")
    until = _as_aware(doc.get("until"))
    if until is None or until <= moment:
        raise ValueError("That period has already ended, so the request no longer matters.")
    decided = {"status": "approved" if approve else "declined", "decided_by": str(admin)[:200], "decided_at": moment.isoformat()}
    if approve:
        user, scope = str(doc["user"]), str(doc["scope"])
        existing = db[USER_EXTENSIONS_COLLECTION].find_one({"user": user, "scope": scope}, {"_id": 0}) or {}
        until_old = _as_aware(existing.get("until"))
        running = until_old is not None and until_old > moment
        extra = (float(existing.get("extra") or 0) if running else 0.0) + EXTENSION_STEP
        grants = (list(existing.get("grants") or []) if running else [])[-49:]
        grants.append({"by": decided["decided_by"], "at": decided["decided_at"], "extra": round(extra, 4)})
        db[USER_EXTENSIONS_COLLECTION].update_one(
            {"user": user, "scope": scope}, {"$set": {"user": user, "scope": scope, "extra": round(extra, 4), "until": until.isoformat(), "grants": grants}},
            upsert=True,
        )
        decided["extra"] = round(extra, 4)
    db[REQUESTS_COLLECTION].update_one({"id": request_id}, {"$set": decided})
    _reset_cache()
    return {**doc, **decided}


# ---------------------------------------------------------------------------------------------------------------
# What it changes
# ---------------------------------------------------------------------------------------------------------------

def low_power(state: Optional[Dict[str, Any]]) -> bool:
    return (state or {}).get("level") in LOW_POWER_LEVELS


def allows(feature: str, state: Optional[Dict[str, Any]]) -> bool:
    """Whether an optional extra (one that costs a model call and isn't needed to answer) may run. `feature` names it for readers
    and for a future per-feature rule; today every extra is paused together in low-power mode."""
    return not low_power(state)


SCHEDULED_HEADROOM_DEFAULT = 0.65


def scheduled_headroom_limit() -> float:
    """How much of any shared budget scheduled work (the tasks people set up) may find already used and still run; LLM_SCHEDULED_HEADROOM overrides it. Below the
    notice line on purpose: work nobody is waiting for must stop before live chat is anywhere near the limit."""
    return min(_positive("LLM_SCHEDULED_HEADROOM") or SCHEDULED_HEADROOM_DEFAULT, 1.0)


def scheduled_headroom(state: Optional[Dict[str, Any]]) -> bool:
    """Whether scheduled work may still run: every SHARED budget (day, month, five-hour window) is below the headroom limit. A person's own limits are judged by
    allows(); an unreadable or switched-off guard leaves the way open, as it does everywhere else."""
    limit = scheduled_headroom_limit()
    shared = [d for d in (state or {}).get("detail", []) if d.get("scope") not in ("my_day", "my_five")]
    return all(float(d.get("fraction") or 0.0) < limit for d in shared)


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


def fraction_level(fraction: float) -> str:
    """The level a single budget at this fraction is at (the same lines evaluate() uses), for showing one person their own limit by itself."""
    notice_at, block_at = thresholds()
    throttle = throttle_at()
    notice_at = min(notice_at, max(throttle - 0.01, 0.01))
    return BLOCKED if fraction >= block_at else THROTTLED if fraction >= throttle else NOTICE if fraction >= notice_at else OK


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
    # Everyone may see how far along THEIR OWN limit is, as a percentage and nothing else (no amount, and nothing about the shared budget or anyone else's use).
    mine = [d for d in state.get("detail", []) if d.get("scope") in ("my_day", "my_five")]
    if mine and level != OFF:
        lead = max(mine, key=lambda d: d["fraction"])
        view.update({"own_fraction": lead["fraction"], "own_scope": lead["scope"], "own_level": fraction_level(lead["fraction"])})
        # When that limit starts over (a time, not an amount), so the ring can say so.
        own_resets_at = (state.get("resets") or {}).get(lead["scope"])
        if own_resets_at:
            view["own_resets_at"] = own_resets_at
        # Whether to offer "request more": the person is at their own notice line or past it. The raised amount, if an admin has approved one, is theirs to know.
        view["can_request_increase"] = can_request_increase(state)
        raised = (state.get("user_extended") or {}).get(lead["scope"])
        if raised:
            view["own_extended_by"] = raised
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
