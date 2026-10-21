"""Sonic's goals as control loops (the definitions are in backend/components/sonic_goals.py): measure a signal, say how it is going, and, only with a person's approval,
change one bounded behaviour.

What lives here:

- STATUS (`status_view`): for each goal, `on_track`, `drifting` or `not_enough_data`, with the numbers behind it. Computed from the aggregate daily counters
  (backend/utils/self_counters.py: no user, no text) and counts of how many people there is evidence about, cached for a minute and shared by everyone. Shown to every
  user (the status answer reads it) and to admins on Sonic Health.
- APPROVAL (`approval`, `set_approval`): one document per goal in `sonic_goal_approvals` naming the revision an admin approved. A lever acts only while the stored revision
  equals the goal's current one, so editing a bound in the registry withdraws the approval until it is given again. Who approved it and when is kept.
- THE LEVER SWITCH (`lever_applies`): approved AND the goal's guardrail is holding. The guardrail is computed from the same counters, so a lever that quietens Sonic
  everywhere switches itself off. A failed read counts as "not applied": the safe default is Sonic's usual behaviour.

Measuring needs no approval. Nothing here changes what Sonic says to a person in a conversation except through the lever, and the lever only lengthens the wait
before Sonic brings something up unprompted (backend/utils/offer_response.py).
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.components.sonic_goals import GOALS, goal as goal_definition

logger = logging.getLogger("SASS Logger")

APPROVALS_COLLECTION = "sonic_goal_approvals"
CACHE_SECONDS = 60
ON_TRACK, DRIFTING, NOT_ENOUGH = "on_track", "drifting", "not_enough_data"

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "views": None}
_lever_cache: Dict[str, Any] = {}


def reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, views=None)
        _lever_cache.clear()


# ---------------------------------------------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------------------------------------------

class MeasureOnly(ValueError):
    """Approval was asked for a goal that only measures."""


def is_measure_only(definition: Dict[str, Any]) -> bool:
    """A goal with no lever changes nothing Sonic does, so it needs no approval and has no guardrail."""
    return not definition.get("lever")


def approval(db: Any, goal_id: str) -> Dict[str, Any]:
    """{"approved": bool, "by": ..., "at": ..., "stale": bool}. `stale` is an approval of an older revision of the goal, which does not count. Blocking."""
    definition = goal_definition(goal_id)
    row = db[APPROVALS_COLLECTION].find_one({"goal": goal_id}, {"_id": 0}) if definition else None
    if not row or not row.get("approved"):
        return {"approved": False, "by": None, "at": None, "stale": False}
    current = int(row.get("revision", 0) or 0) == int(definition["revision"])
    at = row.get("at")
    return {"approved": current, "stale": not current, "by": row.get("by"), "at": at.isoformat() if isinstance(at, datetime) else None}


def set_approval(db: Any, goal_id: str, approved: bool, admin: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Approves (or withdraws approval of) the CURRENT revision of a goal. Raises ValueError for an unknown goal and MeasureOnly for one with no lever. Blocking."""
    definition = goal_definition(goal_id)
    if definition is None:
        raise ValueError("Unknown goal")
    if is_measure_only(definition):
        raise MeasureOnly("That goal is only measured: it has no lever, so there is nothing to approve")
    now = now or datetime.now(timezone.utc)
    db[APPROVALS_COLLECTION].update_one(
        {"goal": goal_id},
        {"$set": {"goal": goal_id, "revision": int(definition["revision"]), "approved": bool(approved), "by": admin, "at": now}},
        upsert=True,
    )
    with _lock:
        _lever_cache.pop(goal_id, None)
        _cache.update(at=0.0, views=None)
    return approval(db, goal_id)


# ---------------------------------------------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------------------------------------------

def _rate(taken: int, passed: int) -> Optional[float]:
    return round(taken / (taken + passed), 4) if taken + passed else None


def _week_totals(db: Any, now: datetime) -> Tuple[Dict[str, int], Dict[str, int]]:
    """The last 7 days' counter totals and the 7 before them."""
    from backend.utils import self_counters

    summary = self_counters.summarize_counters(self_counters.fetch_counter_docs(db, 7, now), now, 7)
    return summary["totals"], summary["previous_totals"]


def _headline(good: int, bad: int, previous_good: int, previous_bad: int) -> Dict[str, Any]:
    """The numbers every goal shares: the headline share (good out of good + bad) this week and the week before. `taken`/`passed` are the good and bad counts."""
    return {
        "taken": good, "passed": bad, "sample": good + bad, "rate": _rate(good, bad),
        "previous_sample": previous_good + previous_bad, "previous_rate": _rate(previous_good, previous_bad),
    }


def welcome_numbers(db: Any, now: datetime, definition: Dict[str, Any]) -> Dict[str, Any]:
    """The welcome goal's raw numbers: this week and the week before, from the daily counters, plus how many people there is evidence about."""
    from backend.utils import offer_response

    this, before = _week_totals(db, now)
    made, held = this.get("offer_made", 0), this.get("offer_held_back", 0)
    return {
        **_headline(this.get("offer_taken", 0), this.get("offer_passed", 0), before.get("offer_taken", 0), before.get("offer_passed", 0)),
        "made": made, "held_back": held,
        "people": offer_response.people_view(db, now),
        "details": [f"{made} unprompted offers this week, {this.get('offer_taken', 0)} picked up and {this.get('offer_passed', 0)} passed on (sign-offs are not counted)."],
        "said": [],
    }


def _about_share(part: int, whole: int) -> int:
    return int(round(part / whole * 100 / 5.0)) * 5


def coding_numbers(db: Any, now: datetime, definition: Dict[str, Any]) -> Dict[str, Any]:
    """The coding goal's raw numbers (backend/utils/code_outcomes.py): follow-up messages that did NOT say the work failed, against those that did; plus how many coding turns ran
    out of steps and how many edits were also tested. The counters hold no person, so there is no people count: the sample floor is the only gate."""
    this, before = _week_totals(db, now)
    followed, redo = this.get("code_followed", 0), this.get("code_redo", 0)
    previous_followed, previous_redo = before.get("code_followed", 0), before.get("code_redo", 0)
    turns, capped = this.get("code_turns", 0), this.get("code_cap_hit", 0)
    edits, tested = this.get("code_edited", 0), this.get("code_verified", 0)
    said = []
    if turns >= definition["min_sample"]:
        said.append(f"About {_about_share(capped, turns)}% of my coding turns this week ran out of steps before I finished.")
    if edits >= 10:
        said.append(f"About {_about_share(tested, edits)}% of the edits I proposed this week I also tested in the same turn.")
    return {
        **_headline(max(followed - redo, 0), redo, max(previous_followed - previous_redo, 0), previous_redo),
        "made": turns, "held_back": 0,
        "people": {"measured": 0, "stretched": 0, "any": 0},
        "details": [f"{turns} coding turns this week; {capped} ran out of steps. {edits} proposed edits; {tested} also tested in the same turn. {followed} follow-up messages; {redo} said it did not work."],
        "said": said,
    }


NUMBERS = {"welcome": welcome_numbers, "coding": coding_numbers}


def judge(numbers: Dict[str, Any], definition: Dict[str, Any]) -> str:
    """on_track / drifting / not_enough_data. Drifting is a rate below the target or a fall of `drop_alarm` against the week before."""
    if numbers["sample"] < definition["min_sample"] or numbers["rate"] is None:
        return NOT_ENOUGH
    fell = (
        numbers["previous_rate"] is not None and numbers["previous_sample"] >= definition["min_sample"]
        and numbers["previous_rate"] - numbers["rate"] >= definition["drop_alarm"]
    )
    return DRIFTING if numbers["rate"] < definition["target_rate"] or fell else ON_TRACK


def guardrail_tripped(numbers: Dict[str, Any], definition: Dict[str, Any]) -> bool:
    """The lever has held back more than the allowed share of the offers that were otherwise due: it is quietening Sonic too much, so it stands down. A measure-only goal has no lever to stand down."""
    if is_measure_only(definition):
        return False
    due = numbers["held_back"] + numbers["made"]
    return due >= definition["guardrail_min_events"] and numbers["held_back"] / due > definition["guardrail_share"]


def _view(db: Any, definition: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    numbers = NUMBERS[definition["id"]](db, now, definition)
    measure_only = is_measure_only(definition)
    approved = {"approved": False, "by": None, "at": None, "stale": False} if measure_only else approval(db, definition["id"])
    tripped = guardrail_tripped(numbers, definition)
    return {
        "id": definition["id"], "title": definition["title"], "statement": definition["statement"], "signal": definition["signal"],
        "lever": definition.get("lever"), "guardrail": definition.get("guardrail"), "measure_only": measure_only,
        "state": judge(numbers, definition), "numbers": numbers, "target_rate": definition["target_rate"], "min_sample": definition["min_sample"],
        "rate_label": definition["rate_label"], "sample_label": definition["sample_label"], "rate_phrase": definition["rate_phrase"],
        "people_gated": bool(definition.get("people_gated")),
        "approval": approved, "guardrail_tripped": tripped, "lever_applies": bool(not measure_only and approved["approved"] and not tripped),
        "revision": definition["revision"],
    }


def status_view(db: Any, now: Optional[datetime] = None, *, use_cache: bool = True) -> List[Dict[str, Any]]:
    """Every goal's status, once a minute for the whole server. A goal that cannot be read is left out (the caller says it cannot see its goals). Blocking."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        if use_cache and _cache["views"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["views"]
    views = []
    for definition in GOALS:
        try:
            views.append(_view(db, definition, now))
        except Exception:
            logger.warning("[goals] could not read the %s goal.", definition["id"], exc_info=True)
    with _lock:
        _cache.update(at=time.monotonic(), views=views)
        for view in views:
            _lever_cache[view["id"]] = (time.monotonic(), view["lever_applies"])
    return views


def lever_applies(db: Any, goal_id: str, now: Optional[datetime] = None) -> bool:
    """Whether the goal's lever may act right now: approved and its guardrail holding. A failed read means no (Sonic's usual behaviour). Blocking, cached for a minute."""
    with _lock:
        hit = _lever_cache.get(goal_id)
        if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    try:
        for view in status_view(db, now, use_cache=False):
            if view["id"] == goal_id:
                return bool(view["lever_applies"])
    except Exception:
        logger.warning("[goals] could not tell whether the %s lever applies.", goal_id, exc_info=True)
    with _lock:
        _lever_cache[goal_id] = (time.monotonic(), False)
    return False
