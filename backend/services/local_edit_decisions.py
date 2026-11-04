"""Waiting for the person's decision on a proposed change to their folder, so the tool loop can carry on after it.

THE PROBLEM. A proposal used to end the turn: Sonic streamed a diff card, said what it had proposed, and stopped. Whether the person applied it, declined it or ignored it
never reached the loop, so a change that needed a second step (fix what the first one broke, edit the next file, check the result) needed a new message from the person.

THE FIX. After the card is streamed the loop WAITS here, at no model cost, for the card's Apply or Decline button to report back (POST /api/local-edits/{id}/decision). The
result it gets is a fact it can act on: applied (and the server's copy of those files is brought up to date, so a read afterwards sees them), declined (with the person's
reason when they give one), or no answer within the timeout, in which case nothing was changed and the turn says so and stops.

- ONE PERSON'S. A decision is accepted only from the person the proposal was made for.
- NOTHING IS APPLIED HERE. The browser still writes the files; this only carries its report back. A report with no one waiting (the turn ended, or the wait timed out) is
  ignored, and the card behaves as it always did.
- BOUNDED. LOCAL_EDIT_WAIT_SECONDS (default 600, ten minutes); a turn that is cancelled (the person pressed Stop, or left) stops waiting at once. The waiting is in this process,
  so it needs the decision to reach the same server instance (one worker today; with more, a report that lands elsewhere is simply not seen and the wait times out).
- ONLY WHEN THE BROWSER SAYS IT CAN REPORT. A page from before this existed would never answer, and a turn must not wait ten minutes for it (the chat request carries
  `edit_decisions`).
- NO TEXT IS LOGGED: a decision's reason is the person's words.
"""
import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("SASS Logger")

WAIT_SECONDS = float(os.getenv("LOCAL_EDIT_WAIT_SECONDS", "600"))
HEARTBEAT_SECONDS = 20.0            # how often the stream is touched while waiting, so a proxy does not close it as idle, and the person sees Sonic is waiting
OUTCOMES = ("applied", "declined")
MAX_REASON_CHARS = 300

_waiting: Dict[str, Dict[str, Any]] = {}

# What each proposal touched ({path: (lines added, lines removed)}), kept for a while whether or not a turn is waiting on it, so that an Apply reported later can be noted as work waiting to be
# committed (backend/utils/applied_changes.py). Paths and counts only, in memory, six hours.
_PROPOSALS_TTL_SECONDS = 6 * 3600
_PROPOSALS_CAP = 300
_proposals: Dict[str, Dict[str, Any]] = {}


def enabled() -> bool:
    return os.getenv("LOCAL_EDIT_WAIT", "on").strip().lower() not in ("0", "false", "no", "off")


def _clean_reason(text: Any) -> str:
    return " ".join(str(text or "").split())[:MAX_REASON_CHARS]


def register(username: str, proposal_id: str) -> None:
    """Starts waiting for `proposal_id`. Call before the card is streamed, so a very quick button press cannot arrive first."""
    _waiting[proposal_id] = {"username": username, "event": asyncio.Event(), "outcome": None}


def remember_proposal(username: str, proposal_id: str, changes: Dict[str, Any]) -> None:
    """Notes which paths a proposal changes and by how much, for applied_changes. `changes` is {path: (added, removed)}."""
    import time

    now = time.time()
    for key in [k for k, v in _proposals.items() if now - v["at"] > _PROPOSALS_TTL_SECONDS]:
        _proposals.pop(key, None)
    while len(_proposals) >= _PROPOSALS_CAP:
        _proposals.pop(next(iter(_proposals)))
    _proposals[proposal_id] = {"username": username, "changes": dict(changes), "at": now}


def note_outcome(username: str, proposal_id: str, outcome: Any) -> Dict[str, Any]:
    """The {path: (added, removed)} of a proposal the FIRST time this outcome ("applied", "declined" or "undone") is reported for it, so a button pressed twice or a report that arrives twice is
    counted once. {} when it was already counted, is not this person's, or is unknown (expired): nothing is then counted. Never raises."""
    try:
        entry = _proposals.get(proposal_id)
        if not entry or entry["username"] != username or outcome not in ("applied", "declined", "undone"):
            return {}
        done = entry.setdefault("counted", set())
        if outcome in done:
            return {}
        done.add(outcome)
        return dict(entry["changes"]) or {"": (0, 0)}
    except Exception:
        return {}


def changes_for(username: str, proposal_id: str) -> Dict[str, Any]:
    """The {path: (added, removed)} a proposal made for this person, or {} (unknown, expired, or someone else's)."""
    entry = _proposals.get(proposal_id)
    return dict(entry["changes"]) if entry and entry["username"] == username else {}


def forget(proposal_id: str) -> None:
    _waiting.pop(proposal_id, None)


def waiting_count() -> int:
    return len(_waiting)


# WHY NOBODY WAS WAITING (2026-11-05). In production the turn twice ended the moment Apply was pressed, and nothing said why. A report that finds no waiter now says which of these it was, and is counted:
# "cancelled" (the stream was cut while the turn waited: a proxy, the browser, a reload), "timeout" (the wait ran out), "decided" (this proposal was already answered: a second press), or "not_here" (this process
# never held it: another instance took the report, the server restarted, or it is more than six hours old). Only the first two cases and the last are failures; "decided" is a harmless second press.
NO_WAITER_REASONS = ("cancelled", "timeout", "decided", "not_here")


def why_no_waiter(username: str, proposal_id: str) -> str:
    """Which of NO_WAITER_REASONS applies to a report that found nobody waiting (see above). Reads only what this process remembers about the proposal; never raises."""
    try:
        entry = _proposals.get(proposal_id)
        if entry is None or entry["username"] != username:
            return "not_here"
        return entry.get("ended") if entry.get("ended") in NO_WAITER_REASONS else "not_here"
    except Exception:
        return "not_here"


def no_waiter_counts(outcome: Any, status: str, why: str) -> Dict[str, int]:
    """The day's tally (self_counters.APPLY_WAIT_KEYS) for a report: one `apply_no_waiter_<why>` when an Apply or a Decline found nobody waiting. {} for anything else (an Undo never has a waiter). Counts only."""
    if status != "no_waiter" or outcome not in OUTCOMES or why not in NO_WAITER_REASONS:
        return {}
    return {f"apply_no_waiter_{why}": 1}


def _note_end(proposal_id: str, ended: str) -> None:
    entry = _proposals.get(proposal_id)
    if entry is not None:
        entry["ended"] = ended


def decide(username: str, proposal_id: str, outcome: Any, reason: Any = "") -> str:
    """Records the person's decision. Returns "recorded", "no_waiter" (no turn is waiting for it: it ended or timed out) or "not_yours". Nothing about the proposal is revealed
    to someone else: an id that belongs to another person looks like one that is not waiting."""
    entry = _waiting.get(proposal_id)
    if entry is None or entry["username"] != username:
        return "no_waiter"
    if outcome not in OUTCOMES:
        return "no_waiter"
    entry["outcome"] = {"outcome": outcome, "reason": _clean_reason(reason)}
    entry["event"].set()
    return "recorded"


async def wait_for(proposal_id: str, timeout: Optional[float] = None, on_tick: Optional[Callable[[int], Awaitable[None]]] = None) -> Optional[Dict[str, str]]:
    """Waits for the decision on `proposal_id` and returns {"outcome", "reason"}, or None if there was none within `timeout` seconds. `on_tick(seconds_waited)` is called every
    HEARTBEAT_SECONDS. Always forgets the proposal afterwards, including when the wait is cancelled."""
    entry = _waiting.get(proposal_id)
    if entry is None:
        return None
    limit = WAIT_SECONDS if timeout is None else timeout
    waited = 0.0
    ended = "timeout"
    try:
        while waited < limit:
            step = min(HEARTBEAT_SECONDS, limit - waited)
            try:
                await asyncio.wait_for(entry["event"].wait(), timeout=step)
                ended = "decided"
                return entry["outcome"]
            except asyncio.TimeoutError:
                waited += step
                if on_tick is not None and waited < limit:
                    try:
                        await on_tick(int(waited))
                    except Exception:
                        pass
        return None
    except asyncio.CancelledError:
        ended = "cancelled"           # the turn was cancelled while it waited: the stream was cut, or the person pressed Stop or left
        raise
    finally:
        _note_end(proposal_id, ended)
        logger.info("[local_edit_decisions] a wait for a decision ended: %s after about %ds.", ended, int(waited))     # no proposal text, no path, no person
        forget(proposal_id)
