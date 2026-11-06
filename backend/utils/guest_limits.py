"""Limits on the shared portfolio guest, per VISITOR instead of for the one shared account (docs/guest-mode-plan.md, step 2).

Every stranger who presses the guest button is the same account (`guest-recruiter@example.com`), so one usage limit used to be everyone's: a few visitors trying the demo could use it up for the
rest, and for the recruiters. Now each visitor has their own small daily allowance of turns, a per-IP allowance behind it (for a browser that clears its storage to look new), and ALL guests together
have one daily spend ceiling so strangers can never use up the owner's budget. Every number is an environment setting, read when it is used, so any of them changes with one setting and no code change:

  GUEST_VISITOR_DAILY_TURNS  turns one visitor may take a day            (default 8; 0 switches this limit off)
  GUEST_IP_DAILY_TURNS       turns one network address may take a day    (default 40; 0 = off). Behind the visitor limit: an office or a university shares one address
  GUEST_DAILY_BUDGET_USD     what all guests together may spend a day    (default 1.00; 0 = off). Spend is the usage store's own cost figure for the guest identities
  GUEST_NUDGE_AT_TURN        the turn after which the page starts saying what an account adds (default 5; used by the page, sent in the allowance)

What is stored: a count per (day, kind, hash). The visitor id and the address are hashed with the day, so a stored count cannot be traced to a person or linked from one day to the next, and nothing
else about the visit is kept. Counts are deleted after three days. A database failure lets the turn through (a limit that fails shut would turn a database hiccup into an outage for every guest);
the spend ceiling and the budget guard are the backstops. Only the portfolio guest is limited: the embedded widget's guest is a client's own deployment.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from backend.utils import billing_time

logger = logging.getLogger("SASS Logger")

PORTFOLIO_GUEST = "guest-recruiter@example.com"
GUEST_SPEND_USERS = (PORTFOLIO_GUEST, "guest")      # the identities whose spend counts toward the guests' daily ceiling
COLLECTION = "guest_turns_daily"
RETENTION_SECONDS = 3 * 24 * 3600
_VISITOR_ID = re.compile(r"^[A-Za-z0-9-]{16,64}$")
_index_ready = False

KIND_MESSAGES = {
    "visitor": "You've used today's free demo turns. Sign up for a free account to keep going: with one, Sonic works on your own projects, remembers you, and runs your own checks.",
    "ip": "This network has used today's free demo turns. Sign up for a free account to keep going.",
    "budget": "The free demo has reached its limit for today. Sign up for a free account to keep going, or come back tomorrow.",
}


def _number(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= minimum else default


def visitor_turns() -> int:
    return int(_number("GUEST_VISITOR_DAILY_TURNS", 8))


def ip_turns() -> int:
    return int(_number("GUEST_IP_DAILY_TURNS", 40))


def daily_budget_usd() -> float:
    return _number("GUEST_DAILY_BUDGET_USD", 1.0)


def nudge_at() -> int:
    return int(_number("GUEST_NUDGE_AT_TURN", 5))


def is_portfolio_guest(username: Optional[str]) -> bool:
    return (username or "").strip().lower() == PORTFOLIO_GUEST


def clean_visitor_id(raw: Optional[str]) -> Optional[str]:
    """The visitor id the browser sent, or None when it is missing or not the shape a browser makes (a UUID). Pure."""
    value = (raw or "").strip()
    return value if _VISITOR_ID.match(value) else None


def client_address(forwarded_for: Optional[str], direct: Optional[str]) -> str:
    """The visitor's network address: the first of a proxy's forwarded list when there is one (the server sits behind one), else the direct peer. Pure."""
    first = (forwarded_for or "").split(",")[0].strip()
    return first or (direct or "").strip() or "unknown"


def hashed(kind: str, value: str, day: str) -> str:
    """The stored key: a hash of the day, the kind and the value, so it cannot be traced back to a person or matched from one day to the next. Pure."""
    return hashlib.sha256(f"{day}|{kind}|{value}".encode("utf-8")).hexdigest()[:24]


def decide(visitor_used: int, ip_used: int, spend_usd: float, *, visitor_cap: int, ip_cap: int, budget_usd: float) -> Dict[str, Any]:
    """Whether one more guest turn may run, and why not when it may not: {"allowed", "reason" ("" | "budget" | "visitor" | "ip"), "remaining"}. A cap of 0 is that limit switched off. Pure."""
    remaining = max(visitor_cap - visitor_used, 0) if visitor_cap > 0 else None
    if budget_usd > 0 and spend_usd >= budget_usd:
        reason = "budget"
    elif visitor_cap > 0 and visitor_used >= visitor_cap:
        reason = "visitor"
    elif ip_cap > 0 and ip_used >= ip_cap:
        reason = "ip"
    else:
        reason = ""
    return {"allowed": not reason, "reason": reason, "remaining": remaining}


# ---------------------------------------------------------------------------------------------------------------
# The store (blocking: call off the event loop)
# ---------------------------------------------------------------------------------------------------------------

def _keys(visitor_id: Optional[str], address: str, day: str) -> Tuple[str, str]:
    """(the visitor's key, the address's key). A visitor with no usable id is bucketed by address, so a script that sends none cannot get an unlimited allowance."""
    visitor = clean_visitor_id(visitor_id)
    return (hashed("visitor", visitor, day) if visitor else hashed("address-visitor", address, day), hashed("ip", address, day))


def _read(db: Any, key: str) -> int:
    doc = db[COLLECTION].find_one({"_id": key}, {"turns": 1})
    return int((doc or {}).get("turns") or 0)


def spend_today(db: Any, day: str) -> float:
    """What the guest identities have spent today, in dollars, from the usage store's per-user daily rows."""
    total = 0
    for doc in db["llm_usage_user_daily"].find({"day": day, "user": {"$in": list(GUEST_SPEND_USERS)}}, {"cost_micro": 1}):
        total += int(doc.get("cost_micro") or 0)
    return total / 1_000_000


def _ensure_index(db: Any) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        db[COLLECTION].create_index("day_dt", expireAfterSeconds=RETENTION_SECONDS)
        _index_ready = True
    except Exception:
        logger.debug("[guest_limits] could not create the expiry index.", exc_info=True)


def _settings() -> Dict[str, Any]:
    return {"visitor_cap": visitor_turns(), "ip_cap": ip_turns(), "budget_usd": daily_budget_usd()}


def allowance(db: Any, visitor_id: Optional[str], address: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What this visitor may still do today, for the page: {"used", "limit", "remaining", "nudge_at", "blocked" (the reason or "")}. Reads only. Never raises: on a failure it reports no limit."""
    moment = now or datetime.now(timezone.utc)
    day = billing_time.day(moment)
    settings = _settings()
    try:
        visitor_key, ip_key = _keys(visitor_id, address, day)
        used, ip_used = _read(db, visitor_key), _read(db, ip_key)
        spent = spend_today(db, day) if settings["budget_usd"] > 0 else 0.0
    except Exception:
        logger.warning("[guest_limits] could not read the allowance; reporting none.")
        return {"used": 0, "limit": 0, "remaining": None, "nudge_at": nudge_at(), "blocked": ""}
    verdict = decide(used, ip_used, spent, **settings)
    return {"used": used, "limit": settings["visitor_cap"], "remaining": verdict["remaining"], "nudge_at": nudge_at(), "blocked": verdict["reason"]}


def check_and_count(db: Any, visitor_id: Optional[str], address: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Whether this guest turn may run, and counts it when it may: {"allowed", "reason", "remaining", "message"}. Never raises: when the store cannot be read the turn is let through. Two turns at the same
    instant may both pass the last place; that is one extra turn, not a hole."""
    moment = now or datetime.now(timezone.utc)
    day = billing_time.day(moment)
    settings = _settings()
    if db is None:
        return {"allowed": True, "reason": "", "remaining": None, "message": ""}
    try:
        visitor_key, ip_key = _keys(visitor_id, address, day)
        used, ip_used = _read(db, visitor_key), _read(db, ip_key)
        spent = spend_today(db, day) if settings["budget_usd"] > 0 else 0.0
        verdict = decide(used, ip_used, spent, **settings)
        if verdict["allowed"]:
            _ensure_index(db)
            for key in (visitor_key, ip_key):
                db[COLLECTION].update_one({"_id": key}, {"$inc": {"turns": 1}, "$set": {"day": day, "day_dt": moment}}, upsert=True)
            if verdict["remaining"] is not None:
                verdict["remaining"] -= 1
    except Exception:
        logger.warning("[guest_limits] could not check the guest limits; letting the turn through.")
        return {"allowed": True, "reason": "", "remaining": None, "message": ""}
    return {**verdict, "message": KIND_MESSAGES.get(verdict["reason"], "")}
