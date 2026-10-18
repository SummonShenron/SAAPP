"""Adds one item to Sonic's reading shelf: search a topic it leans toward, read one page, write a short grounded summary.

The rules for what may be stored live in backend/utils/reading_shelf.py; this is the part that touches the network and a model. Run on
its own schedule (run_scheduled_refresh_loop, checked hourly, so a refresh happens about every twelve hours whether or not anyone is around), lazily
as a backstop (backend/utils/reading_shelf.refresh_due, checked at the start of a conversation), and from the admin screen's "search now". All three
share one attempt claim, so they never run twice for the same window. It never
raises, logs only names and counts (a topic id, a public site, a reason: no user content exists anywhere in it), and does at most one
search, a few page fetches and two small model calls per run.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.utils import reading_shelf as shelf

from backend.utils import routines
logger = logging.getLogger("SASS Logger")

MAX_RESULTS = 6
MAX_PAGES_TRIED = 3
CHECK_EVERY_SECONDS = 600  # at most one database look at "is a refresh due?" per process per ten minutes

_next_check = 0.0


def default_search(query: str) -> List[Dict[str, Any]]:
    from langchain_community.utilities import DuckDuckGoSearchAPIWrapper

    results = DuckDuckGoSearchAPIWrapper().results(query, max_results=MAX_RESULTS)
    return [r for r in (results or []) if isinstance(r, dict)]


def default_fetch(url: str):
    from backend.services.url_reader import try_lightweight_fetch

    return try_lightweight_fetch(url)


async def _summarize(invoke: Callable[[str], Awaitable[Any]], title: str, text: str, about: str = "") -> Optional[str]:
    """A summary that passes every fixed check and is confirmed by a second look at the page (it supports the summary and is really about
    the topic searched for), or None. Anything but a clear yes is a no."""
    summary = shelf.parse_json_field(await invoke(shelf.build_summary_prompt(title, text)), "summary")
    if not isinstance(summary, str):
        return None
    summary = " ".join(summary.split())
    issue = shelf.summary_issue(summary, text)
    if issue:
        logger.info("[shelf] a summary was refused: %s.", issue)
        return None
    verdict = await invoke(shelf.build_support_prompt(text, summary, about))
    if shelf.parse_json_field(verdict, "supported") is not True:
        logger.info("[shelf] a summary was refused: the page did not support it.")
        return None
    if about and shelf.parse_json_field(verdict, "on_topic") is not True:
        logger.info("[shelf] a page was refused: it is not about the topic it was searched for.")
        return None
    return summary


async def refresh_shelf(
    db,
    invoke: Callable[[str], Awaitable[Any]],
    *,
    search: Callable[[str], List[Dict[str, Any]]] = default_search,
    fetch: Callable[[str], Any] = default_fetch,
    now: Optional[datetime] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """{"status": "added"|"nothing"|"skipped"|"error", ...}. `force` (the admin's "search now") ignores the interval. Every real attempt (not a skip)
    is written down as a run of the "reading_shelf" routine (backend/utils/routines.py), which is what Sonic can later say it did."""
    started = datetime.now(timezone.utc)
    result = await _refresh_shelf(db, invoke, search=search, fetch=fetch, now=now, force=force)
    if result.get("status") != "skipped":
        try:
            await asyncio.to_thread(
                routines.record_run, db, "reading_shelf",
                routines.STATUS_FAILED if result.get("status") == "error" else routines.STATUS_OK,
                {"items_added": int(result.get("status") == "added")}, started,
            )
        except Exception:
            logger.warning("[shelf] could not record the run.", exc_info=True)
    return result


async def _refresh_shelf(
    db,
    invoke: Callable[[str], Awaitable[Any]],
    *,
    search: Callable[[str], List[Dict[str, Any]]] = default_search,
    fetch: Callable[[str], Any] = default_fetch,
    now: Optional[datetime] = None,
    force: bool = False,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    try:
        if force:
            await asyncio.to_thread(shelf.force_claim, db, now)
        elif not await asyncio.to_thread(shelf.claim_refresh, db, now):
            return {"status": "skipped"}
        chosen = await asyncio.to_thread(shelf.next_topic, db)
        topic, query = chosen["topic"], chosen["query"]
        await asyncio.to_thread(shelf.record_search, db, topic["id"], now)
        known, blocked = await asyncio.gather(asyncio.to_thread(shelf.known_keys, db), asyncio.to_thread(shelf.blocked_domains, db))
        results = await asyncio.to_thread(search, query)
        tried, reasons = 0, {}
        for result in results:
            url = shelf.normalize_url(str(result.get("link") or ""))
            reason = shelf.acceptable_source(url, known, blocked)
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            if tried >= MAX_PAGES_TRIED:
                break
            tried += 1
            page = await asyncio.to_thread(fetch, url)
            text = (getattr(page, "text", None) or "").strip()
            final_url = shelf.normalize_url(getattr(page, "final_url", None) or url)
            if len(text) < shelf.MIN_PAGE_CHARS:
                reasons["too little text"] = reasons.get("too little text", 0) + 1
                continue
            reason = shelf.acceptable_source(final_url, known, blocked)  # a redirect may have landed somewhere that is not allowed
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            title = shelf.clean_title(result.get("title"))
            summary = await _summarize(invoke, title, text, topic.get("about", ""))
            if not summary:
                reasons["summary refused"] = reasons.get("summary refused", 0) + 1
                continue
            doc = await asyncio.to_thread(
                lambda: shelf.add_item(db, url=final_url, title=title or shelf.domain_of(final_url), summary=summary, topic=topic["id"], now=now)
            )
            await asyncio.to_thread(shelf.trim_to_limit, db)
            logger.info("[shelf] added an item from %s for the %s topic.", doc["domain"], topic["id"])
            return {"status": "added", "topic": topic["id"], "domain": doc["domain"], "key": doc["_id"]}
        logger.info("[shelf] nothing added for the %s topic (%s).", topic["id"], reasons or "no results")
        return {"status": "nothing", "topic": topic["id"], "reasons": reasons}
    except Exception:
        logger.warning("[shelf] the refresh failed.", exc_info=True)
        return {"status": "error"}


SCHEDULE_CHECK_SECONDS = float(os.getenv("SHELF_SCHEDULE_CHECK_SECONDS", str(60 * 60)))
SCHEDULE_FIRST_DELAY_SECONDS = float(os.getenv("SHELF_SCHEDULE_FIRST_DELAY_SECONDS", str(5 * 60)))   # let a starting server settle before it goes reading


async def run_scheduled_refresh_loop(
    get_db: Callable[[], Any],
    invoke: Callable[[str], Awaitable[Any]],
    allowed: Optional[Callable[[], Awaitable[bool]]] = None,
    *,
    check_every: float = SCHEDULE_CHECK_SECONDS,
    first_delay: float = SCHEDULE_FIRST_DELAY_SECONDS,
) -> None:
    """Sonic's reading between conversations. Wakes up every `check_every` seconds and, when the shelf is due (one database read), does one refresh.
    The due check lives in the database, not in this loop, so a restart or a second server cannot make it read more often than the interval, and a
    deploy does not make it start over. `allowed` is the cost guard (low-power mode pauses it). One failed pass is logged and the next hour tries
    again. The reading is real (a search and a page), but nothing here knows anything about any user: it never touches a conversation."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            if shelf.refresh_enabled() and shelf.schedule_enabled() and (allowed is None or await allowed()):
                db = get_db()
                if db is not None and await asyncio.to_thread(shelf.refresh_due, db):
                    result = await refresh_shelf(db, invoke)
                    logger.info("[shelf] scheduled refresh: %s.", result.get("status"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[shelf] the scheduled refresh pass failed.", exc_info=True)
        await asyncio.sleep(check_every)


async def maybe_refresh(db, invoke: Callable[[str], Awaitable[Any]]) -> Optional[Dict[str, Any]]:
    """The lazy trigger: called when a conversation starts, off the critical path. Cheap when nothing is due (one database read per
    process per ten minutes); the attempt claim inside refresh_shelf keeps overlapping callers from both running it."""
    global _next_check
    if not shelf.refresh_enabled() or time.monotonic() < _next_check:
        return None
    _next_check = time.monotonic() + CHECK_EVERY_SECONDS
    try:
        if not await asyncio.to_thread(shelf.refresh_due, db):
            return None
    except Exception:
        logger.warning("[shelf] could not check whether a refresh is due.", exc_info=True)
        return None
    return await refresh_shelf(db, invoke)
