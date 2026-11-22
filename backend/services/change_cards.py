"""The approval card for a change to one of the person's Google things (their calendar, their documents), and the wait for Apply or Decline: the same mechanism as a proposed edit's card
(backend/services/local_edit_decisions.py) and a choice card (backend/services/choice_cards.py).

BEFORE (text). A calendar or document change used to be a paragraph in the chat ("Approval Required ... Please Approve, Modify parameters, or Reject"); the person typed or tapped "approve", the NEXT turn found the card by its
wording and rebuilt the details from that text. Every part of that could go wrong (a reworded card, a lost detail, a stale "approve" two turns later), and Sonic could not carry on afterwards.

NOW (a card). `propose_calendar_change` / `propose_doc_change` prepare the change against the real event or document (backend/services/calendar_changes_service.py, backend/services/doc_changes_service.py), stream ONE card (what will
change, to what, who else sees it) with Apply and Decline, and WAIT in the same turn, at no model cost, for the person's press (POST /api/chat/change/{id}/decision). Apply runs the stored request on the server, so the Google call is made
once, by the person's own token, only after the press; the tool then returns what really happened to the loop, which says so and carries on.

A PROPOSAL is a plain dict made by the service: {proposal_id, username, title, subject ("calendar" or "document", for the words), card: {action, heading, apply_label, title, rows, notice, destructive}, apply: a function that takes the
proposal, makes the Google call and returns one sentence, raising ChangeError (backend/utils/change_error.py) with the reason when it could not}. This module knows nothing else about it.

- ONE PERSON'S. A press is accepted only from the person the card was shown to.
- ONCE. The first press decides; a second press (a double click, another tab) changes nothing.
- BOUNDED. CHANGE_CARD_WAIT_SECONDS (default 600). A turn that is cancelled stops waiting at once. No press means nothing was changed, and the card says it is no longer waiting. Nothing is ever applied without a waiting turn.
- ONLY WHEN THE PAGE CAN ANSWER. The chat request carries `change_cards`; without it the tools are not offered and the older text path (Outlook, or a page from before this) is used.
- NO EVENT OR DOCUMENT TEXT IS LOGGED: only that a wait ended, and how.
"""
import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from backend.utils.change_error import ChangeError

logger = logging.getLogger("SASS Logger")

WAIT_SECONDS = float(os.getenv("CHANGE_CARD_WAIT_SECONDS", "600"))
HEARTBEAT_SECONDS = 20.0
MAX_PROPOSALS_PER_TURN = 6
DECISIONS = ("apply", "decline")
_waiting: Dict[str, Dict[str, Any]] = {}


def enabled() -> bool:
    return os.getenv("CHANGE_CARDS", "on").strip().lower() not in ("0", "false", "no", "off")


def card_payload(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """What is streamed to the page: only what the card draws (never the stored request, the etag or the token)."""
    card = proposal["card"]
    return {"proposal_id": proposal["proposal_id"], "action": card["action"], "heading": card["heading"], "apply_label": card["apply_label"], "title": card["title"], "rows": card["rows"], "notice": card["notice"],
            "destructive": card["destructive"]}


def register(proposal: Dict[str, Any]) -> None:
    """Starts waiting for this proposal's decision. Call before the card is streamed, so a very quick press cannot arrive first."""
    _waiting[proposal["proposal_id"]] = {"proposal": proposal, "event": asyncio.Event(), "outcome": None, "deciding": False}


def forget(proposal_id: str) -> None:
    _waiting.pop(proposal_id, None)


def waiting_count() -> int:
    return len(_waiting)


async def decide(username: str, proposal_id: str, decision: Any) -> Dict[str, Any]:
    """The person's press. Returns {"status": ..., "message": ...}: "applied" (it is done), "declined", "failed" (nothing was changed; the message says why), "expired" (nobody is waiting: the turn ended or timed
    out, the id is not theirs, or it was already decided) or "invalid" (not apply or decline). Applying makes the Google call here, off the event loop (the proposal's own `apply`)."""
    entry = _waiting.get(proposal_id)
    if entry is None or entry["proposal"]["username"] != username or entry["deciding"]:
        return {"status": "expired", "message": ""}
    word = str(decision or "").strip().lower()
    if word not in DECISIONS:
        return {"status": "invalid", "message": ""}
    entry["deciding"] = True                 # the first press decides; a second one finds this set
    if word == "decline":
        entry["outcome"] = {"kind": "declined", "message": ""}
    else:
        try:
            entry["outcome"] = {"kind": "applied", "message": await asyncio.to_thread(entry["proposal"]["apply"], entry["proposal"])}
        except ChangeError as problem:
            entry["outcome"] = {"kind": "failed", "message": str(problem)}
        except Exception:
            logger.exception("[calendar cards] applying a change failed unexpectedly.")
            entry["outcome"] = {"kind": "failed", "message": "Something went wrong, so nothing was changed."}
    entry["event"].set()
    return {"status": entry["outcome"]["kind"], "message": entry["outcome"]["message"]}


async def wait_for(proposal_id: str, timeout: Optional[float] = None, on_tick: Optional[Callable[[int], Awaitable[None]]] = None) -> Optional[Dict[str, Any]]:
    """Waits for the decision on `proposal_id` and returns the outcome ({"kind", "message"}), or None when there was none within `timeout` seconds. `on_tick(seconds_waited)` runs every HEARTBEAT_SECONDS so the
    stream stays open. Always forgets the proposal afterwards, including when the wait is cancelled."""
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
                ended = entry["outcome"]["kind"]
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
        ended = "cancelled"
        raise
    finally:
        logger.info("[calendar cards] a wait for a calendar decision ended: %s after about %ds.", ended, int(waited))     # no title, no time, no person
        forget(proposal_id)


def result_text(proposal: Dict[str, Any], outcome: Optional[Dict[str, Any]]) -> str:
    """What the tool returns to the loop. Pure."""
    if outcome is None:
        minutes = max(int(WAIT_SECONDS // 60), 1)
        return (f"NO DECISION: nothing was pressed within {minutes} minutes, so nothing in their {proposal.get('subject', 'calendar')} was changed, and the card no longer works. Say that plainly in your final answer and stop; "
                "they can ask again.")
    if outcome["kind"] == "applied":
        return f"APPLIED: {outcome['message']} It is done. Say so in one short sentence and, if the person asked for more, carry on with the rest. Do not propose the same change again."
    if outcome["kind"] == "declined":
        return "DECLINED: the person pressed Decline, so nothing was changed. Do not propose the same change again. Ask what they would like instead, or stop."
    return f"FAILED: nothing was changed. {outcome['message']} Tell the person exactly that, and what they can do about it; do not claim it was done."
