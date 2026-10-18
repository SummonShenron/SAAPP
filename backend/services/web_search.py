"""Web search for Sonic: one place that answers "search the web for this", with a dependable provider first and the free scraped engines as a fallback.

Why this exists. Search used to go straight to the free `ddgs` metasearch library (through a langchain wrapper) in "auto" mode, which fans out to whichever scraped
engines it feels like. From one IP most of them refuse: Google and Mojeek answer 403, Brave 429, DuckDuckGo times out, and the wrapper's default region ("wt-wt") even
builds a hostname that does not exist (wt.wikipedia.org). Whether a search worked depended on which engine the library shuffled to first, a failed call took a
dozen seconds, and the model then retried with reworded queries, so a person waited half a minute for nothing.

- A SEARCH API FIRST (Tavily, TAVILY_API_KEY; the key lives on the server like the job-source keys and is never sent to a person or a model). Built for agents: one
  request, cleaned snippets, no scraping. Used only while it is configured, inside its limits and not in a back-off.
- THE FREE ENGINES AS THE FALLBACK, called directly with a real region and a hard time limit, never "auto" with the library's default region.
- A CIRCUIT BREAKER. A provider that just failed is not tried again for a minute (fifteen when the reason is a key or plan problem), so a dead search fails in
  an instant instead of making every retry wait. When nothing can answer, the model is told so in plain words and told not to ask again this turn.
- A WALL-CLOCK LIMIT. One call never takes longer than WEB_SEARCH_TIMEOUT_SECONDS in total, whatever the providers do.
- NO SPEND SURPRISES. Tavily is held under a monthly ceiling (TAVILY_MONTHLY_LIMIT, below the free allowance) and a per-person daily ceiling (TAVILY_USER_DAILY), kept in
  the database. Past either, the free engines answer instead. Scheduled jobs (the reading shelf, user tasks) count under "system".
- NO USER TEXT IN LOGS. Only provider names, counts and status codes are logged, and the libraries that print the full search URL (so the query) at INFO are quieted.

Results keep the shape the old wrapper returned ({"title", "snippet", "link"}), so nothing that reads them changes.
"""
import hashlib
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger("SASS Logger")

# These libraries log every request URL, and the URL of a search is the search. Quiet them: the warnings that matter still come through.
for _noisy in ("primp", "ddgs"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

TAVILY_URL = "https://api.tavily.com/search"
USAGE_COLLECTION = "web_search_usage"

OVERALL_TIMEOUT = float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "12"))
TAVILY_TIMEOUT = float(os.getenv("TAVILY_TIMEOUT_SECONDS", "8"))
DDGS_TIMEOUT = 4                                   # per engine batch inside the library; the overall limit above still bounds the whole call
BREAKER_SECONDS = float(os.getenv("WEB_SEARCH_BREAKER_SECONDS", "60"))
BREAKER_LONG_SECONDS = 15 * 60                     # a key or plan problem does not clear in a minute
TAVILY_MONTHLY_LIMIT = int(os.getenv("TAVILY_MONTHLY_LIMIT", "900"))     # the free allowance is 1,000 credits a month; a basic search is one
TAVILY_USER_DAILY = int(os.getenv("TAVILY_USER_DAILY", "40"))
REGION = os.getenv("WEB_SEARCH_REGION", "us-en")
MAX_RESULTS_CAP = 8
SNIPPET_CHARS = 500
SYSTEM_USER = "system"

TAVILY, FREE = "tavily", "free engines"
_NEWS_RE = re.compile(r"\b(?:news|headlines?|breaking)\b", re.IGNORECASE)
_LONG_STATUSES = {401, 403, 432, 433}              # unauthorized, forbidden, plan limit, pay-as-you-go limit

UNAVAILABLE_MESSAGE = (
    "ERROR: web search is unavailable right now (the search providers are not answering). Do not call web_search again this turn. Tell the user plainly that live "
    "search isn't working at the moment, and only answer from what you already know if you say that it may be out of date."
)


class SearchUnavailable(Exception):
    """No provider could answer: every one that was allowed to try failed, or is resting after a failure."""


class _ProviderFailed(Exception):
    def __init__(self, provider: str, reason: str, long: bool = False):
        super().__init__(reason)
        self.provider, self.cause, self.long = provider, reason, long


# ---------------------------------------------------------------------------------------------------------------
# The breaker: a provider that just failed rests
# ---------------------------------------------------------------------------------------------------------------

_lock = threading.Lock()
_resting_until: Dict[str, float] = {}
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="web-search")


def reset() -> None:
    """Forgets every back-off (tests, and an operator who has just fixed a key)."""
    with _lock:
        _resting_until.clear()


def _is_resting(provider: str, now: Optional[float] = None) -> bool:
    with _lock:
        return _resting_until.get(provider, 0.0) > (time.monotonic() if now is None else now)


def _rest(provider: str, seconds: float) -> None:
    with _lock:
        _resting_until[provider] = time.monotonic() + seconds


def resting() -> Dict[str, float]:
    """Seconds each resting provider has left, for an admin or a test."""
    now = time.monotonic()
    with _lock:
        return {name: round(until - now, 1) for name, until in _resting_until.items() if until > now}


# ---------------------------------------------------------------------------------------------------------------
# Spend limits (kept in the database so a restart or a second server cannot reset them)
# ---------------------------------------------------------------------------------------------------------------

def tavily_configured() -> bool:
    return bool(os.getenv("TAVILY_API_KEY", "").strip())


def _usage_ids(username: str, now: datetime) -> Dict[str, str]:
    person = hashlib.sha1((username or SYSTEM_USER).encode("utf-8")).hexdigest()[:12]
    return {"month": f"tavily:{now:%Y-%m}", "day": f"tavily:{now:%Y-%m-%d}:{person}"}


_ttl_ready = False


def _ensure_usage_ttl(db: Any) -> None:
    """The usage rows carry an expires_at; this is the index that makes the database remove them once it has passed."""
    global _ttl_ready
    if _ttl_ready:
        return
    try:
        db[USAGE_COLLECTION].create_index("expires_at", expireAfterSeconds=0)
        _ttl_ready = True
    except Exception:
        logger.warning("[web search] could not create the usage expiry index.", exc_info=True)


def reserve_tavily(db: Any, username: str, now: Optional[datetime] = None) -> bool:
    """Takes one Tavily search from this month's and this person's day's allowance, or says there is none left. With no database the search is allowed (search
    must keep working; Tavily's own plan limit is the backstop)."""
    if db is None:
        return True
    moment = now or datetime.now(timezone.utc)
    ids = _usage_ids(username, moment)
    _ensure_usage_ttl(db)
    try:
        month = db[USAGE_COLLECTION].find_one({"_id": ids["month"]}) or {}
        day = db[USAGE_COLLECTION].find_one({"_id": ids["day"]}) or {}
        if int(month.get("count") or 0) >= TAVILY_MONTHLY_LIMIT or int(day.get("count") or 0) >= TAVILY_USER_DAILY:
            return False
        db[USAGE_COLLECTION].update_one({"_id": ids["month"]}, {"$inc": {"count": 1}, "$set": {"expires_at": moment + timedelta(days=45)}}, upsert=True)
        db[USAGE_COLLECTION].update_one({"_id": ids["day"]}, {"$inc": {"count": 1}, "$set": {"expires_at": moment + timedelta(days=3)}}, upsert=True)
        return True
    except Exception:
        logger.warning("[web search] could not read or write the Tavily allowance.", exc_info=True)
        return True


def usage_this_month(db: Any, now: Optional[datetime] = None) -> int:
    """How many Tavily searches have been made this month, for an admin."""
    if db is None:
        return 0
    doc = db[USAGE_COLLECTION].find_one({"_id": _usage_ids(SYSTEM_USER, now or datetime.now(timezone.utc))["month"]}) or {}
    return int(doc.get("count") or 0)


# ---------------------------------------------------------------------------------------------------------------
# The providers
# ---------------------------------------------------------------------------------------------------------------

def _clean(text: Any, limit: int = SNIPPET_CHARS) -> str:
    return " ".join(str(text or "").split())[:limit]


def _tavily(query: str, max_results: int, news: bool, timeout: float) -> List[Dict[str, str]]:
    try:
        response = requests.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {os.getenv('TAVILY_API_KEY', '').strip()}", "Content-Type": "application/json"},
            json={"query": query, "max_results": max_results, "search_depth": "basic", "topic": "news" if news else "general",
                  "include_answer": False, "include_raw_content": False},
            timeout=timeout,
        )
    except requests.Timeout as error:
        raise _ProviderFailed(TAVILY, "timed out") from error
    except requests.RequestException as error:
        raise _ProviderFailed(TAVILY, f"request failed ({type(error).__name__})") from error
    if response.status_code != 200:
        raise _ProviderFailed(TAVILY, f"status {response.status_code}", long=response.status_code in _LONG_STATUSES)
    try:
        rows = (response.json() or {}).get("results") or []
    except ValueError as error:
        raise _ProviderFailed(TAVILY, "unreadable reply") from error
    return [
        {"title": _clean(r.get("title"), 200), "snippet": _clean(r.get("content")), "link": str(r.get("url") or "")}
        for r in rows if isinstance(r, dict) and r.get("url")
    ][:max_results]


def _free_engines(query: str, max_results: int, news: bool, timeout: float) -> List[Dict[str, str]]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, TimeoutException

    try:
        with DDGS(timeout=min(DDGS_TIMEOUT, max(1, int(timeout)))) as search:
            if news:
                rows = search.news(query, region=REGION, safesearch="moderate", timelimit="m", max_results=max_results)
                return [{"title": _clean(r.get("title"), 200), "snippet": _clean(r.get("body")), "link": str(r.get("url") or "")} for r in rows or [] if r.get("url")]
            rows = search.text(query, region=REGION, safesearch="moderate", timelimit="y", max_results=max_results, backend="auto")
            return [{"title": _clean(r.get("title"), 200), "snippet": _clean(r.get("body")), "link": str(r.get("href") or "")} for r in rows or [] if r.get("href")]
    except TimeoutException as error:
        raise _ProviderFailed(FREE, "timed out") from error
    except DDGSException as error:
        if str(error).strip().rstrip(".").lower() == "no results found":
            return []                                     # a search that found nothing is an answer, not a failure
        raise _ProviderFailed(FREE, "every engine refused") from error
    except Exception as error:
        raise _ProviderFailed(FREE, f"failed ({type(error).__name__})") from error


def _with_deadline(fn: Callable[..., List[Dict[str, str]]], timeout: float, *args: Any) -> List[Dict[str, str]]:
    """Runs a provider call with a hard time limit. The call may run on in the background after this gives up (a thread cannot be killed), but the caller is free."""
    future = _pool.submit(fn, *args)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout as error:
        raise _ProviderFailed(FREE if fn is _free_engines else TAVILY, "ran out of time") from error


# ---------------------------------------------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------------------------------------------

def wants_news(query: str) -> bool:
    return bool(_NEWS_RE.search(query or ""))


def search(query: str, max_results: int = 3, *, username: Optional[str] = None, news: Optional[bool] = None, db: Any = None) -> List[Dict[str, str]]:
    """Results for a query as [{"title", "snippet", "link"}], [] when the search found nothing. Raises SearchUnavailable when no provider could answer.
    Blocking: call it off the event loop (search_async does)."""
    query = " ".join(str(query or "").split())
    if not query:
        return []
    limit = max(1, min(int(max_results or 3), MAX_RESULTS_CAP))
    is_news = wants_news(query) if news is None else news
    started = time.monotonic()
    tried = 0

    def remaining() -> float:
        return OVERALL_TIMEOUT - (time.monotonic() - started)

    if tavily_configured() and not _is_resting(TAVILY) and remaining() > 1:
        if db is None:
            try:
                from backend.utils.db_utils import get_db

                db = get_db()
            except Exception:
                db = None
        if reserve_tavily(db, username or SYSTEM_USER):
            tried += 1
            try:
                rows = _with_deadline(_tavily, min(TAVILY_TIMEOUT, remaining()) + 0.5, query, limit, is_news, min(TAVILY_TIMEOUT, remaining()))
                logger.info("[web search] %s answered with %d result(s).", TAVILY, len(rows))
                return rows
            except _ProviderFailed as failure:
                _rest(TAVILY, BREAKER_LONG_SECONDS if failure.long else BREAKER_SECONDS)
                logger.warning("[web search] %s failed (%s); resting it for %ds.", TAVILY, failure.cause, BREAKER_LONG_SECONDS if failure.long else BREAKER_SECONDS)
        else:
            logger.info("[web search] the %s allowance is used up for now; using the free engines.", TAVILY)

    if not _is_resting(FREE) and remaining() > 1:
        tried += 1
        try:
            rows = _with_deadline(_free_engines, remaining(), query, limit, is_news, remaining())
            logger.info("[web search] %s answered with %d result(s).", FREE, len(rows))
            return rows
        except _ProviderFailed as failure:
            _rest(FREE, BREAKER_SECONDS)
            logger.warning("[web search] %s failed (%s); resting them for %ds.", FREE, failure.cause, int(BREAKER_SECONDS))

    logger.warning("[web search] unavailable: %d provider(s) tried, resting: %s.", tried, ", ".join(sorted(resting())) or "none")
    raise SearchUnavailable("no search provider could answer")


async def search_async(query: str, max_results: int = 3, *, username: Optional[str] = None, news: Optional[bool] = None) -> List[Dict[str, str]]:
    import asyncio

    return await asyncio.to_thread(search, query, max_results, username=username, news=news)
