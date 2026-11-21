"""A ceiling on how big a request may be, for every route (found by the launch probe, 2026-11-19).

WHY. Nothing limited request size. A guest (anyone: the guest identity needs no token) could post a 40 MB file to /api/upload-attachment and the server read all of it into memory before doing anything with it; the app runs
as ONE worker on ONE instance, so a few large uploads can take the whole service down. A limit per endpoint would have to be remembered for every new endpoint; this applies to all of them.

HOW. A plain ASGI middleware (so it sees the body before any route reads it):
- a request that SAYS it is too big (Content-Length) is refused with 413 at once, without reading a byte of the body;
- one that does not say (chunked) is counted as it streams, and refused with 413 the moment it passes the limit;
- the limit is the default, or a larger one for the routes that legitimately take big bodies (UPLOAD_EXACT: document ingestion), and the SMALLER guest limit for the shared guest identity, which anyone can claim
  (the same headers backend/auth/isolation_auth.py reads: `X-Principal: guest` / `guest_bty`, or the two fixed guest tokens).
Pure Python, no framework state; the numbers come from the environment so they can be changed without a deploy of code.

NOT HERE: how big the pieces INSIDE a request may be (a chat attachment, a folder's files): those have their own limits where they are used (backend/services/local_workspace.py, the client's image shrinking).
"""
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Tuple

DEFAULT_LIMIT = int(os.getenv("MAX_REQUEST_BYTES", str(15_000_000)))             # any request: a chat with attachments, a batch of project files (the client sends them in batches of 2.5 MB)
UPLOAD_LIMIT = int(os.getenv("MAX_UPLOAD_BYTES", str(60_000_000)))               # document ingestion (members who may ingest into a library)
GUEST_LIMIT = int(os.getenv("MAX_GUEST_REQUEST_BYTES", str(6_000_000)))          # the shared guest identity: anyone, no token
UPLOAD_EXACT: Tuple[str, ...] = ("/api/upload",)                                 # only document ingestion gets the bigger limit (an exact path: /api/upload-attachment does not)

_GUEST_PRINCIPALS = {"guest", "guest_bty"}
_GUEST_TOKENS = {"bearer guest-sandbox-token", "bearer guest-bty-token"}

Scope = Dict[str, Any]
Receive = Callable[[], Awaitable[Dict[str, Any]]]
Send = Callable[[Dict[str, Any]], Awaitable[None]]


class RequestTooLarge(Exception):
    """Raised inside `receive` when a body streams past its limit."""


def is_guest_request(headers: List[Tuple[bytes, bytes]]) -> bool:
    """Whether the request claims the shared guest identity, by the headers that make it one. Pure."""
    seen = {name.decode("latin-1").lower(): value.decode("latin-1").strip() for name, value in headers}
    principal = (seen.get("x-principal") or seen.get("x-user-id") or "").strip()
    return principal in _GUEST_PRINCIPALS or seen.get("authorization", "").lower() in _GUEST_TOKENS


def limit_for(path: str, headers: List[Tuple[bytes, bytes]]) -> int:
    """How many bytes this request may be. Pure."""
    limit = UPLOAD_LIMIT if path in UPLOAD_EXACT else DEFAULT_LIMIT
    return min(limit, GUEST_LIMIT) if is_guest_request(headers) else limit


def _too_large_response(limit: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    body = json.dumps({"detail": f"That is too large to send in one request (the limit is {limit // 1_000_000} MB)."}).encode("utf-8")
    start = {"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]}
    return start, {"type": "http.response.body", "body": body}


class BodySizeLimitMiddleware:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or []
        limit = limit_for(scope.get("path", ""), headers)
        declared = next((value for name, value in headers if name.lower() == b"content-length"), None)
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                for message in _too_large_response(limit):
                    await send(message)
                return

        total = 0
        started = False

        async def counted_receive() -> Dict[str, Any]:
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body") or b"")
                if total > limit:
                    raise RequestTooLarge()
            return message

        async def watching_send(message: Dict[str, Any]) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, watching_send)
        except RequestTooLarge:
            if not started:
                for message in _too_large_response(limit):
                    await send(message)
