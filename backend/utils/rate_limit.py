"""How many requests one network address may send, for the doors anyone can knock on (found by the launch probe, 2026-11-19, item 5).

WHY. The per-person and global budgets cap what the MODEL can cost, but nothing limited request VOLUME: login and runner pairing (a pairing code can be guessed at any speed), the three webhooks (an unsigned flood costs
CPU and database reads even when every one is refused), and the shared guest identity (anyone can claim it with one header, so every endpoint a guest may reach is reachable by the whole internet). The app is ONE
worker on ONE instance, so a flood from one address can slow it for everybody.

HOW. A plain ASGI middleware (it looks at headers only, never at the body), one counter per (rule, address) in memory. The app is one process, so memory is the right place: nothing to share, nothing stored, nothing that
could be traced to a person after a restart. Three rules, each with TWO limits:

  auth     POST /api/login, /api/runner/pair   10 a minute per address, 200 a minute across everyone
  webhook  the three webhook endpoints         120 a minute per address, 1000 across everyone (GitHub sends from a few addresses and a busy repo sends bursts)
  guest    any other request that claims the shared guest identity   120 a minute per address, 1500 across everyone (a page load is a dozen requests, and an office shares one address)

The second limit is the one that still works when the first is dodged: if a caller can make up the address the server sees (see TRUSTED_PROXY_HOPS), each made-up address has its own allowance, but the total across
everyone does not move. A request refused for ITS ADDRESS is not counted toward the total, so one noisy caller cannot use up everybody else's share.

Every number is an environment setting, read when it is used (0 switches that limit off):
  RATE_AUTH_PER_MIN, RATE_AUTH_ALL_PER_MIN, RATE_WEBHOOK_PER_MIN, RATE_WEBHOOK_ALL_PER_MIN, RATE_GUEST_PER_MIN, RATE_GUEST_ALL_PER_MIN
  TRUSTED_PROXY_HOPS  how many proxies sit in front of the app and append to X-Forwarded-For (default 0 = take the FIRST entry, which is what the guest turn limit has always done). With N, the address is the entry N from
                      the RIGHT, the one the proxy nearest the app wrote itself, which a caller cannot choose. Render's own documentation does not say whether it replaces or appends to a header the caller sent: test
                      with a request that sends its own X-Forwarded-For before relying on the default.

Refused requests get 429 with a Retry-After, `code: "rate_limited"` and `reason` (the rule). Memory is bounded (MAX_KEYS addresses; past that, new addresses are held only to the total limit). The Sonic Health counters
`rate_limited_<rule>` (one address over its limit) and `rate_limited_<rule>_all` (everyone together) are flushed at most once a minute, so a flood cannot become a flood of database writes. Nothing is logged about who: only the rule.
"""
import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

from backend.utils.body_limit import is_guest_request

logger = logging.getLogger("SASS Logger")

WINDOW_SECONDS = 60.0
MAX_KEYS = 20_000
FLUSH_EVERY_SECONDS = 60.0

# rule -> (setting for one address, its default, setting for everyone together, its default)
RULES: Dict[str, Tuple[str, int, str, int]] = {
    "auth": ("RATE_AUTH_PER_MIN", 10, "RATE_AUTH_ALL_PER_MIN", 200),
    "webhook": ("RATE_WEBHOOK_PER_MIN", 120, "RATE_WEBHOOK_ALL_PER_MIN", 1000),
    "guest": ("RATE_GUEST_PER_MIN", 120, "RATE_GUEST_ALL_PER_MIN", 1500),
}
AUTH_PATHS = frozenset({"/api/login", "/api/runner/pair"})
WEBHOOK_PATHS = frozenset({"/webhooks/github", "/api/v1/webhooks/ingest", "/api/billing/webhook"})
KEYS = tuple(f"rate_limited_{rule}{suffix}" for rule in RULES for suffix in ("", "_all"))    # the Sonic Health counters (backend/utils/self_counters.py lists the same)

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]


def _setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


def per_address_limit(rule: str) -> int:
    name, default, _, _ = RULES[rule]
    return _setting(name, default)


def total_limit(rule: str) -> int:
    _, _, name, default = RULES[rule]
    return _setting(name, default)


def trusted_hops() -> int:
    return _setting("TRUSTED_PROXY_HOPS", 0)


def address_from(forwarded_for: Optional[str], direct: Optional[str], hops: Optional[int] = None) -> str:
    """The caller's network address. With no trusted proxy configured (hops 0) the first forwarded entry, as the guest turn limit always read it; with N proxies, the entry N from the right (the one the proxy nearest the
    app wrote, not one the caller could choose); the direct peer when there is no forwarded list; "unknown" when there is nothing. Pure."""
    hops = trusted_hops() if hops is None else hops
    entries = [part.strip() for part in (forwarded_for or "").split(",") if part.strip()]
    if entries:
        if hops <= 0:
            return entries[0]
        return entries[-hops] if hops <= len(entries) else entries[0]
    return (direct or "").strip() or "unknown"


def classify(method: str, path: str, headers: List[Tuple[bytes, bytes]]) -> Optional[str]:
    """Which rule this request falls under, or None. A preflight never counts. Pure."""
    if method.upper() == "OPTIONS":
        return None
    clean = path.rstrip("/") or "/"
    if clean in AUTH_PATHS:
        return "auth"
    if clean in WEBHOOK_PATHS:
        return "webhook"
    if is_guest_request(headers):
        return "guest"
    return None


class Limiter:
    """Sliding-window counters in memory. Pure Python; the clock is passed in, so a test can drive time by hand."""

    def __init__(self) -> None:
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}

    def _window(self, key: Tuple[str, str], now: float, create: bool) -> Optional[Deque[float]]:
        hits = self._hits.get(key)
        if hits is None:
            if not create:
                return None
            hits = self._hits[key] = deque()
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        return hits

    def _sweep(self, now: float) -> None:
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - WINDOW_SECONDS]:
            del self._hits[key]

    def check(self, rule: str, address: str, now: float) -> Optional[Tuple[str, int]]:
        """None when the request may go ahead (and it is counted), else (which limit, seconds to wait): "address" or "all". A request refused for its address is not counted toward everyone's total."""
        one, everyone = per_address_limit(rule), total_limit(rule)
        mine = None
        if one:
            if len(self._hits) >= MAX_KEYS:
                self._sweep(now)
            mine = self._window((rule, address), now, create=len(self._hits) < MAX_KEYS)
            if mine is not None and len(mine) >= one:
                return "address", self._wait(mine, now)
        everyone_hits = self._window((rule, "*"), now, create=True) if everyone else None
        if everyone_hits is not None and len(everyone_hits) >= everyone:
            return "all", self._wait(everyone_hits, now)
        if mine is not None:
            mine.append(now)
        if everyone_hits is not None:
            everyone_hits.append(now)
        return None

    @staticmethod
    def _wait(hits: Deque[float], now: float) -> int:
        return max(1, math.ceil(hits[0] + WINDOW_SECONDS - now)) if hits else 1


def _refusal(rule: str, which: str, wait: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    body = json.dumps({"detail": "Too many requests from this connection. Wait a moment and try again.", "code": "rate_limited", "reason": rule}).encode("utf-8")
    start = {"type": "http.response.start", "status": 429, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")), (b"retry-after", str(wait).encode("ascii")),
    ]}
    return start, {"type": "http.response.body", "body": body}


class RateLimitMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], limiter: Optional[Limiter] = None, clock: Callable[[], float] = time.monotonic):
        self.app = app
        self.limiter = limiter or Limiter()
        self.clock = clock
        self._pending: Dict[str, int] = {}
        self._last_flush = float("-inf")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        rule = classify(scope.get("method", "GET"), scope.get("path", ""), headers)
        if rule is None:
            await self.app(scope, receive, send)
            return
        seen = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in headers}
        peer = (scope.get("client") or (None, None))[0]
        now = self.clock()
        verdict = self.limiter.check(rule, address_from(seen.get("x-forwarded-for"), peer), now)
        if verdict is None:
            await self.app(scope, receive, send)
            return
        which, wait = verdict
        await self._count(f"rate_limited_{rule}" + ("_all" if which == "all" else ""), rule, now)
        for message in _refusal(rule, which, wait):
            await send(message)

    async def _count(self, key: str, rule: str, now: float) -> None:
        """Counts the refusal in memory and writes the totals at most once a minute, so a flood of refused requests is not a flood of database writes."""
        self._pending[key] = self._pending.get(key, 0) + 1
        if now - self._last_flush < FLUSH_EVERY_SECONDS:
            return
        self._last_flush = now
        counts, self._pending = self._pending, {}
        logger.warning("[rate limit] requests are being refused under the '%s' rule.", rule)
        try:
            from backend.utils.self_counters import record_turn_counts

            await asyncio.to_thread(record_turn_counts, counts)
        except Exception:
            logger.warning("[rate limit] could not record the refusal counts.", exc_info=True)
