"""Turning one public page into a PENDING card for Sonic's knowledge base (the `propose_kb_entry` tool: any signed-in member may ask, only an admin can approve). The store and its size rules are backend/utils/knowledge_base.py.

The steps, in order, each cheaper than the next so nothing expensive runs for a page that was never going to pass:
1. fixed address rules (https, not a blocked source, not already known, not turned away before);
2. today's cap and the waiting queue's cap;
3. one safe fetch of the page (url_reader: private addresses refused, redirects checked hop by hop, 2 MB cap), and its text must be long enough to say something;
4. the same text under another address is a duplicate;
5. a model writes the card; fixed checks refuse it unless it is a plain third-person passage every name and number of which is in the page (the reading shelf's rules, with a longer length);
6. a second model call must confirm the page supports every claim in the card.
Only then is it stored, PENDING. It cannot be read by anyone until an ADMIN approves it on Sonic Health, so the person who asked (a member, possibly with bad intent), the model and the page itself do not decide what everyone is told.

The page is untrusted text throughout: it is only ever shown to the model inside a prompt that says so, and what comes back is checked by code. Nothing here logs or stores the page, an address or a word of a card; the counts are
`kb_proposed` and `kb_proposal_refused` (backend/utils/self_counters.py).
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from backend.utils import kb_candidates
from backend.utils import knowledge_base as kb
from backend.utils import reading_shelf as shelf

logger = logging.getLogger("SASS Logger")


def default_fetch(url: str):
    from backend.services.url_reader import try_lightweight_fetch

    return try_lightweight_fetch(url)


def _refused(reason: str) -> str:
    kb.count("kb_proposal_refused")
    return f"ERROR: that page was not proposed: {reason}."


async def propose_for_agent(
    db: Any,
    url: Any,
    invoke: Callable[[str], Awaitable[Any]],
    *,
    fetch: Callable[[str], Any] = default_fetch,
    now: Optional[datetime] = None,
    who: str = "",
    admin: bool = False,
) -> str:
    """What the `propose_kb_entry` tool returns: a plain statement that a card is waiting for approval, or an "ERROR: ..." the model can relay. Never raises. The caller has already refused guests;
    `who` is the member's name (used only for their count for the day) and `admin` lifts the daily caps."""
    address = str(url or "").strip()
    if not address:
        return "ERROR: propose_kb_entry needs a url (an https address of a public page)."
    if db is None:
        return "ERROR: the knowledge base cannot be reached right now."
    try:
        reason = await asyncio.to_thread(kb.refusal_for, db, address)
        if reason:
            return _refused(reason)
        reason = await asyncio.to_thread(kb.claim_proposal, db, now, who=who, admin=admin)
        if reason:
            return _refused(reason)
        page = await asyncio.to_thread(fetch, shelf.normalize_url(address))
        text = (getattr(page, "text", None) or "").strip()
        if getattr(page, "text", None) is None:
            return _refused(f"it could not be read ({getattr(page, 'error', None) or 'no reason given'})")
        if len(text) < kb.MIN_PAGE_CHARS:
            return _refused("it has too little readable text (a page that needs a browser to show its content cannot be read this way)")
        final_url = shelf.normalize_url(getattr(page, "final_url", None) or address)
        if final_url != shelf.normalize_url(address):                # a redirect may have landed somewhere that is not allowed, or somewhere already known
            reason = await asyncio.to_thread(kb.refusal_for, db, final_url)
            if reason:
                return _refused(reason)
        digest = kb.content_hash(text)
        reason = await asyncio.to_thread(kb.duplicate_of, db, digest)
        if reason:
            return _refused(reason)

        card = kb.parse_card(await invoke(kb.build_card_prompt(text)))
        if not card:
            return _refused("the card could not be written from it")
        issue = kb.card_issue(card["title"], card["body"], text)
        if issue:
            logger.info("[knowledge base] a card was refused: %s.", issue)
            return _refused(f"the card did not pass the checks ({issue})")
        verdict = await invoke(shelf.build_support_prompt(text, card["body"]))
        if shelf.parse_json_field(verdict, "supported") is not True:
            logger.info("[knowledge base] a card was refused: the page did not support it.")
            return _refused("the page did not support every claim in the card")

        await asyncio.to_thread(kb.add_pending, db, url=final_url, card=card, digest=digest, now=now, admin=admin)
        kb.count("kb_proposed")
        logger.info("[knowledge base] a card is waiting for approval.")
        return (
            f"PROPOSED: \"{card['title']}\" ({card['kind']}, from {shelf.domain_of(final_url)}) is now waiting for an admin to approve it on Sonic Health, under Learned knowledge. "
            "Until then nobody can read it, Sonic included. Tell the person it is waiting for an admin and that you cannot say when, and do not say it has been learned."
        )
    except Exception:
        logger.warning("[knowledge base] a proposal failed.", exc_info=True)
        return "ERROR: the page could not be proposed right now; nothing was saved."


# A refusal that means the page is not a candidate any more (it is in the base, waiting, or was turned away) rather than "try again later".
_FINAL_REFUSALS = ("already in the knowledge base", "already waiting", "turned that page away", "the same text is already")


async def learn_candidate(
    db: Any,
    key: Any,
    invoke: Callable[[str], Awaitable[Any]],
    *,
    admin_name: str = "admin",
    fetch: Callable[[str], Any] = default_fetch,
    now: Optional[datetime] = None,
) -> dict:
    """An admin says Learn on a candidate page (backend/utils/kb_candidates.py): the same proposal as `propose_kb_entry` (one safe fetch, a card written and checked), as an admin, so it lands PENDING and
    needs the second approval before anyone can read it. Returns {"ok": bool, "message": str}. On success the candidate is removed; when the page is already known or was turned away it is removed too; when the page would
    not read or the card failed its checks it stays, with the reason, so the admin can see why or dismiss it. Never raises."""
    try:
        candidate = await asyncio.to_thread(kb_candidates.get, db, key) if db is not None else None
        if candidate is None:
            return {"ok": False, "message": "No such candidate page."}
        result = await propose_for_agent(db, candidate["url"], invoke, fetch=fetch, now=now, who=admin_name, admin=True)
        if result.startswith("PROPOSED"):
            await asyncio.to_thread(kb_candidates.forget, db, candidate["_id"])
            kb.count("kb_candidate_learned")
            return {"ok": True, "message": "The card is waiting for your approval under Waiting for approval."}
        reason = result.replace("ERROR: that page was not proposed: ", "").replace("ERROR: ", "").strip().rstrip(".")
        if any(marker in reason for marker in _FINAL_REFUSALS):
            await asyncio.to_thread(kb_candidates.forget, db, candidate["_id"])
        else:
            await asyncio.to_thread(kb_candidates.note_problem, db, candidate["_id"], reason)
        return {"ok": False, "message": f"Not learned: {reason}."}
    except Exception:
        logger.warning("[knowledge base] learning a candidate failed.", exc_info=True)
        return {"ok": False, "message": "Could not learn that page right now; nothing was saved."}
