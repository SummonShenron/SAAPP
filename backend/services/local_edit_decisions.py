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
import os
from typing import Any, Awaitable, Callable, Dict, Optional

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


def changes_for(username: str, proposal_id: str) -> Dict[str, Any]:
    """The {path: (added, removed)} a proposal made for this person, or {} (unknown, expired, or someone else's)."""
    entry = _proposals.get(proposal_id)
    return dict(entry["changes"]) if entry and entry["username"] == username else {}


def forget(proposal_id: str) -> None:
    _waiting.pop(proposal_id, None)


def waiting_count() -> int:
    return len(_waiting)


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
    try:
        while waited < limit:
            step = min(HEARTBEAT_SECONDS, limit - waited)
            try:
                await asyncio.wait_for(entry["event"].wait(), timeout=step)
                return entry["outcome"]
            except asyncio.TimeoutError:
                waited += step
                if on_tick is not None and waited < limit:
                    try:
                        await on_tick(int(waited))
                    except Exception:
                        pass
        return None
    finally:
        forget(proposal_id)
