"""Job listings for the Tasks feature, from two job APIs Sonic holds the keys for (Jooble and Adzuna). People pick them as "where to look"; they never see or supply a key.

Each source is a fixed request to a fixed host. A source is available only when its key is set in the environment, and a task that picked one while it is not available simply
gets nothing from it. The listings come back in the same shape as a web search result (a link, a title, some text), so the runner treats them the same way: the model picks
which match, and the links shown are the listings' own.

Keys: JOOBLE_API_KEY; ADZUNA_APP_ID with ADZUNA_APP_KEY (or ADZUNA_API_KEY); ADZUNA_COUNTRY (default "us"). A key is part of the request address for Jooble, so an error from the
network library, whose text includes that address, is never passed on: a failed request raises a plain JobSourceError and nothing else, and nothing here is logged.
"""
import hashlib
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlsplit

import requests

JOOBLE, ADZUNA = "jooble", "adzuna"
SOURCES = (JOOBLE, ADZUNA)
LABELS = {JOOBLE: "Jooble (job listings)", ADZUNA: "Adzuna (job listings)"}
TIMEOUT_SECONDS = 20
MAX_JOBS = 10
TEXT_CHARS = 420
TITLE_CHARS = 120
_TAGS_RE = re.compile(r"<[^>]+>")
_OWN_HOSTS = ("jooble.org", "adzuna.com")


class JobSourceError(Exception):
    """A job source could not be read. Carries no detail on purpose (the request address holds a key)."""


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def configured(source: str) -> bool:
    if source == JOOBLE:
        return bool(_env("JOOBLE_API_KEY"))
    if source == ADZUNA:
        return bool(_env("ADZUNA_APP_ID") and (_env("ADZUNA_APP_KEY") or _env("ADZUNA_API_KEY")))
    return False


def available() -> List[str]:
    return [s for s in SOURCES if configured(s)]


def _flat(text: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", _TAGS_RE.sub(" ", str(text or ""))).strip()[:limit].rstrip()


def _money(low: Any, high: Any) -> str:
    try:
        low_n, high_n = (int(float(low)) if low else 0), (int(float(high)) if high else 0)
    except (TypeError, ValueError):
        return ""
    if low_n and high_n and low_n != high_n:
        return f"Pay {low_n:,} to {high_n:,}."
    return f"Pay {low_n or high_n:,}." if (low_n or high_n) else ""


def _https_on_own_host(url: str) -> str:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme in ("http", "https") and any(host == h or host.endswith("." + h) for h in _OWN_HOSTS):
        return "https://" + parts.netloc + parts.path + (f"?{parts.query}" if parts.query else "")
    return ""                                              # a link to anywhere else is not shown


def _candidate(source: str, job_id: Any, url: str, title: str, company: str, location: str, detail: str) -> Dict[str, Any]:
    from backend.utils import reading_shelf as shelf

    key = hashlib.sha1(f"{source}:{job_id}".encode("utf-8")).hexdigest()[:16] if job_id else shelf.item_key(url)
    heading = _flat(title, TITLE_CHARS) + (f" at {_flat(company, 60)}" if company else "")
    text = " ".join(part for part in (_flat(location, 80) + "." if location else "", detail) if part)
    return {"url": url, "key": key, "domain": "jooble.org" if source == JOOBLE else "adzuna.com", "title": heading[:TITLE_CHARS + 70], "text": _flat(text, TEXT_CHARS)}


def _get_json(call) -> Dict[str, Any]:
    try:
        response = call()
        if response.status_code >= 400:
            raise JobSourceError("the job source refused the request")
        data = response.json()
    except JobSourceError:
        raise
    except Exception:
        raise JobSourceError("the job source could not be read") from None   # never chain: the original message holds the request address
    return data if isinstance(data, dict) else {}


def search_jooble(keywords: str, location: str) -> List[Dict[str, Any]]:
    key = _env("JOOBLE_API_KEY")
    data = _get_json(lambda: requests.post(f"https://jooble.org/api/{key}", json={"keywords": keywords, "location": location, "page": 1}, timeout=TIMEOUT_SECONDS))
    out = []
    for job in (data.get("jobs") or [])[:MAX_JOBS]:
        if not isinstance(job, dict):
            continue
        url = _https_on_own_host(str(job.get("link") or ""))
        if url:
            out.append(_candidate(JOOBLE, job.get("id"), url, job.get("title"), job.get("company"), job.get("location"),
                                  " ".join(filter(None, [_flat(job.get("type"), 30), _flat(job.get("salary"), 40), _flat(job.get("snippet"), TEXT_CHARS)]))))
    return out


def search_adzuna(keywords: str, location: str) -> List[Dict[str, Any]]:
    country = re.sub(r"[^a-z]", "", _env("ADZUNA_COUNTRY").lower())[:2] or "us"
    params = {"app_id": _env("ADZUNA_APP_ID"), "app_key": _env("ADZUNA_APP_KEY") or _env("ADZUNA_API_KEY"), "what": keywords, "results_per_page": MAX_JOBS, "content-type": "application/json"}
    if location:
        params["where"] = location
    data = _get_json(lambda: requests.get(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params, timeout=TIMEOUT_SECONDS))
    out = []
    for job in (data.get("results") or [])[:MAX_JOBS]:
        if not isinstance(job, dict):
            continue
        url = _https_on_own_host(str(job.get("redirect_url") or ""))
        company = (job.get("company") or {}).get("display_name") if isinstance(job.get("company"), dict) else job.get("company")
        where = (job.get("location") or {}).get("display_name") if isinstance(job.get("location"), dict) else job.get("location")
        if url:
            out.append(_candidate(ADZUNA, job.get("id"), url, job.get("title"), company, where,
                                  " ".join(filter(None, [_money(job.get("salary_min"), job.get("salary_max")), _flat(job.get("description"), TEXT_CHARS)]))))
    return out


def search(source: str, keywords: str, location: str = "") -> List[Dict[str, Any]]:
    """Listings as candidates ({url, key, domain, title, text}), or [] when the source is not set up. Blocking. Raises JobSourceError when a configured source fails."""
    if not configured(source):
        return []
    keywords, location = _flat(keywords, 160), _flat(location, 80)
    return search_jooble(keywords, location) if source == JOOBLE else search_adzuna(keywords, location)
