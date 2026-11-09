"""Pages Sonic read on its own, kept as CANDIDATES for its knowledge base (docs/global-knowledge-base-plan.md, "Candidates", 2026-11-09; Jack's idea: "if sonic gets told to look up a site or performs a search ... store that url
in a health dashboard tab, and if approved, sonic reads and summarizes and ingests the site summary into its kb").

THE FLOW. When Sonic reads a public page in a normal turn (read_url or the browser), its address is noted here, and a repeat read of the same page counts again. Nothing is fetched, summarised or embedded at this point,
so a candidate costs nothing. On Sonic Health, under Learned knowledge, an admin sees the list, most-read first (a page Sonic keeps going back to is a gap in what it knows), and either dismisses a page or says Learn: that runs the
existing proposal (backend/services/knowledge_cards.py: one safe fetch, a card written and checked, a second model confirming the page supports it) and the card then WAITS for a second, separate approval before anyone can read it.
Two gates, so a poor page is stopped at the first or the second; the capture itself can therefore be generous.

WHAT IS KEPT: the address without its query string or fragment (the usual place for tokens and personal data), its domain, when it was first and last read and how many times. NEVER who read it, which question led to it, or any
page text. The address is dropped, not stored, when it cannot be a public page: not https, a login or sharing address, an address with a name and password in it, a private network address, a private-document host
(Google Docs and Drive, OneDrive, Dropbox...), a source the base never reads, or a page the base already holds, is waiting on, or an admin turned away.

BOUNDED: MAX_CANDIDATES open ones (when full the least-read, oldest one makes room), one not read again for CANDIDATE_DAYS is dropped, a dismissed one is remembered for DISMISSED_DAYS so it is not suggested again.
Counters are counts only (self_counters.KB_KEYS). No address is logged.
"""
import ipaddress
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from backend.utils import knowledge_base as kb
from backend.utils import reading_shelf as shelf

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_kb_candidates"
MAX_CANDIDATES = 200
CANDIDATE_DAYS = 30
DISMISSED_DAYS = 90
OPEN, DISMISSED = "open", "dismissed"

# Hosts whose pages are a person's own documents or workspace, not the public web.
PRIVATE_HOSTS = (
    "docs.google.com", "drive.google.com", "mail.google.com", "calendar.google.com", "accounts.google.com", "meet.google.com",
    "onedrive.live.com", "1drv.ms", "sharepoint.com", "outlook.office.com", "outlook.live.com", "teams.microsoft.com",
    "dropbox.com", "box.com", "slack.com", "zoom.us", "atlassian.net", "figma.com", "airtable.com",
)
PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp", ".intranet")
_LOGIN_PATH_RE = re.compile(r"/(?:log-?in|sign-?in|sign-?up|sign-?out|log-?out|auth|oauth2?|sso|saml|callback|reset|password|invite|verify|unsubscribe|checkout|cart|account|settings|admin|dashboard)(?:/|$)", re.IGNORECASE)
_TOKEN_SEGMENT_RE = re.compile(r"/[A-Za-z0-9_-]{32,}(?:/|$)")        # a share link or a secret in the path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_url(url: Any) -> Optional[str]:
    """The page's address as a candidate would be kept (https, host lower-cased, no port, no query, no fragment, no trailing slash), or None when it cannot be a public page. Pure."""
    text = str(url or "").strip()
    if not text or len(text) > kb.MAX_URL_CHARS * 2:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not host or "@" in parts.netloc:
        return None
    if "." not in host or host.endswith(PRIVATE_SUFFIXES) or host in ("localhost",):
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None                                              # an address by number is a machine, not a public page
    except ValueError:
        pass
    if any(host == h or host.endswith("." + h) for h in PRIVATE_HOSTS):
        return None
    path = parts.path.rstrip("/")
    if _LOGIN_PATH_RE.search(path + "/") or _TOKEN_SEGMENT_RE.search(path + "/"):
        return None
    cleaned = urlunsplit(("https", host, path, "", ""))
    return cleaned if len(cleaned) <= kb.MAX_URL_CHARS else None


def _key(url: str) -> str:
    return shelf.item_key(url)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def prune(db, now: Optional[datetime] = None) -> int:
    """Drops open candidates not read again for CANDIDATE_DAYS and dismissals older than DISMISSED_DAYS."""
    now = now or _now()
    gone: List[Any] = []
    for doc in db[COLLECTION].find({}):
        seen_at = kb._parse(doc.get("dismissed_at") if doc.get("status") == DISMISSED else doc.get("last_seen")) or now
        limit = DISMISSED_DAYS if doc.get("status") == DISMISSED else CANDIDATE_DAYS
        if seen_at < now - timedelta(days=limit):
            gone.append(doc["_id"])
    if gone:
        db[COLLECTION].delete_many({"_id": {"$in": gone}})
    return len(gone)


def _make_room(db) -> None:
    open_docs = [d for d in db[COLLECTION].find({"status": OPEN})]
    if len(open_docs) < MAX_CANDIDATES:
        return
    weakest = min(open_docs, key=lambda d: (int(d.get("seen") or 0), str(d.get("last_seen") or "")))
    db[COLLECTION].delete_many({"_id": {"$in": [weakest["_id"]]}})


def record(db, url: Any, now: Optional[datetime] = None) -> str:
    """Notes one page Sonic read: "new", "repeat" (already a candidate, counted again), "dismissed" (an admin said no before), or "skipped" (it cannot be a candidate, or the base already has it). Counts only,
    never raises, and writes nothing about who read it."""
    try:
        if db is None:
            return "skipped"
        now = now or _now()
        clean = clean_url(url)
        if clean is None or kb.refusal_for(db, clean) is not None:
            kb.count("kb_candidate_skipped")
            return "skipped"
        key = _key(clean)
        existing = db[COLLECTION].find_one({"_id": key})
        if existing and existing.get("status") == DISMISSED:
            return "dismissed"
        if existing:
            db[COLLECTION].update_one({"_id": key}, {"$inc": {"seen": 1}, "$set": {"last_seen": _iso(now)}})
            kb.count("kb_candidate_repeat")
            return "repeat"
        prune(db, now)
        _make_room(db)
        db[COLLECTION].insert_one({"_id": key, "url": clean, "domain": shelf.domain_of(clean), "status": OPEN, "seen": 1, "first_seen": _iso(now), "last_seen": _iso(now)})
        kb.count("kb_candidate_seen")
        return "new"
    except Exception:
        logger.warning("[knowledge base] could not note a candidate page.", exc_info=True)
        return "skipped"


def open_list(db, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The open candidates for the admin page, most-read first, then most recently read."""
    prune(db, now)
    rows = [d for d in db[COLLECTION].find({"status": OPEN})]
    rows.sort(key=lambda d: str(d.get("last_seen") or ""), reverse=True)      # most recently read first ...
    rows.sort(key=lambda d: -int(d.get("seen") or 0))                          # ... then, stably, the most-read first
    return [{"key": d["_id"], **{k: d.get(k) for k in ("url", "domain", "seen", "first_seen", "last_seen", "problem")}} for d in rows]


def get(db, key: str) -> Optional[Dict[str, Any]]:
    doc = db[COLLECTION].find_one({"_id": str(key or "")})
    return doc if doc and doc.get("status") == OPEN else None


def dismiss(db, key: str, now: Optional[datetime] = None) -> bool:
    """Turns a candidate away without fetching it: it is remembered (without a count) for DISMISSED_DAYS so it is not suggested again. False when there is no such open candidate."""
    if get(db, key) is None:
        return False
    db[COLLECTION].update_one({"_id": str(key)}, {"$set": {"status": DISMISSED, "dismissed_at": _iso(now or _now())}})
    kb.count("kb_candidate_dismissed")
    return True


def forget(db, key: str) -> None:
    db[COLLECTION].delete_many({"_id": {"$in": [str(key)]}})


def note_problem(db, key: str, problem: str) -> None:
    """Keeps a candidate that could not be learned (the page would not read, the card failed its checks) with the reason, so the admin sees why. The reason is one of the proposal's own fixed sentences."""
    db[COLLECTION].update_one({"_id": str(key)}, {"$set": {"problem": str(problem or "")[:200]}})
