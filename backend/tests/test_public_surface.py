"""What an anonymous caller can reach (the launch probe, 2026-11-19; docs/launch-probe-2026-11-19.md). The guest identity needs no token (`X-Principal: guest` is the whole key), so each of these pins one thing the probe
found open: the synthetic endpoint that ran the full model workflow for anyone, the unsigned GitHub webhook, request sizes, and write endpoints that forgot to turn guests away. Real routes, a FastAPI TestClient, no model, no
database (backend/tests/import_app.py)."""
import asyncio
import hashlib
import hmac
import json
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.auth import guest_policy as gp
from backend.auth import isolation_auth as ia
from backend.tests.import_app import import_app
from backend.utils import body_limit as bl

app_module = import_app()
WRITES = {"POST", "PUT", "PATCH", "DELETE"}
GUEST = {"X-Principal": "guest"}


def routes():
    return [r for r in app_module.app.routes if isinstance(r, APIRoute)]


def calls_of(route):
    """Every dependency callable a route needs, however deep."""
    found, stack = set(), list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


def write_routes():
    return [(m, r.path, r) for r in routes() for m in sorted(r.methods & WRITES)]


def real_path(path):
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.fixture()
def client():
    return TestClient(app_module.app, raise_server_exceptions=False)


# ---- every write endpoint has been decided ------------------------------------------------------------------------------------------------------------

def classify(route):
    calls = calls_of(route)
    if ia.get_member_user in calls or app_module.require_global_admin in calls or app_module.get_runner_key in calls:
        return "members"
    if ia.get_current_user in calls:
        return "guest_reachable"
    return "public"


def test_every_write_endpoint_is_members_only_or_on_the_written_out_list_of_what_a_guest_may_use():
    undecided_guest, undecided_public = [], []
    for method, path, route in write_routes():
        kind = classify(route)
        if kind == "guest_reachable" and (method, path) not in gp.GUEST_WRITES:
            undecided_guest.append((method, path))
        if kind == "public" and (method, path) not in gp.PUBLIC_WRITES:
            undecided_public.append((method, path))
    assert not undecided_guest, f"a guest can call these (anyone can be the guest) and they are not on the allowlist: take get_member_user, or add them to backend/auth/guest_policy.py on purpose: {undecided_guest}"
    assert not undecided_public, f"these need no sign-in and are not on the public list: {undecided_public}"


def test_the_lists_only_name_routes_that_exist_so_a_renamed_endpoint_cannot_leave_a_stale_exception():
    existing = {(m, r.path) for r in routes() for m in r.methods}
    stale = [item for item in (gp.GUEST_WRITES | gp.PUBLIC_WRITES) if item not in existing]
    assert not stale, stale
    assert not (gp.GUEST_WRITES & gp.PUBLIC_WRITES)


def test_the_public_write_endpoints_each_prove_the_caller_some_other_way():
    source = open(app_module.__file__, encoding="utf-8").read()
    for needle in ("exchange_pairing", "api_pair", "INGEST_WEBHOOK_SECRET", "verify_github_signature", "billing_webhook"):
        assert needle in source or needle in open(app_module.__file__.replace("app.py", "backend/utils/runner_jobs.py"), encoding="utf-8").read(), needle


# ---- guests are turned away from everything outside the allowlist --------------------------------------------------------------------------------

MEMBER_ONLY = [(m, p) for m, p, r in write_routes() if classify(r) == "members" and ia.get_member_user in calls_of(r)]


def test_there_are_many_members_only_endpoints_and_the_ones_the_probe_named_are_among_them():
    assert len(MEMBER_ONLY) >= 50
    for item in [("PUT", "/api/settings/github-token"), ("POST", "/api/runner/pairings"), ("DELETE", "/api/memory"), ("POST", "/api/billing/checkout"), ("POST", "/api/tasks"), ("PUT", "/api/settings/tool-model")]:
        assert item in MEMBER_ONLY, item


@pytest.mark.parametrize("method,path", MEMBER_ONLY)
def test_a_guest_with_no_token_is_refused_before_the_endpoint_runs(client, method, path):
    response = client.request(method, real_path(path), headers=GUEST, json={})
    assert response.status_code == 403 and response.json()["detail"] == "Sign in to use this.", (method, path, response.status_code, response.text[:120])


@pytest.mark.parametrize("token", ["guest-sandbox-token", "guest-bty-token"])
def test_the_fixed_guest_tokens_are_refused_too(client, token):
    assert client.put("/api/settings/github-token", headers={"Authorization": f"Bearer {token}"}, json={}).status_code == 403
    assert client.post("/api/runner/pairings", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_the_embedded_widget_guest_is_refused_as_well(client):
    assert client.delete("/api/memory", headers={"X-Principal": "guest_bty"}).status_code == 403


@pytest.mark.parametrize("method,path", sorted(gp.GUEST_DEMO_WRITES))
def test_what_the_public_demo_calls_is_not_refused_at_the_door(client, method, path):
    response = client.request(method, real_path(path), headers=GUEST, json={})
    assert response.status_code != 403 or response.json().get("detail") != "Sign in to use this.", (method, path)
    assert response.status_code != 401, (method, path)


def test_a_signed_in_member_is_not_refused_by_the_member_check(client):
    app_module.app.dependency_overrides[ia.get_current_user] = lambda: {"sub": "alice@example.com", "email": "alice@example.com"}
    try:
        for method, path in [("PUT", "/api/settings/github-token"), ("POST", "/api/runner/pairings"), ("POST", "/api/tasks"), ("DELETE", "/api/memory")]:
            response = client.request(method, real_path(path), json={})
            detail = response.json().get("detail") if response.headers.get("content-type", "").startswith("application/json") else ""
            assert detail != "Sign in to use this.", (method, path, response.status_code)
    finally:
        app_module.app.dependency_overrides.clear()


def test_the_member_dependency_turns_away_exactly_the_two_guest_identities():
    assert ia.GUEST_SUBJECTS == {"guest-recruiter@example.com", "guest_bty"}
    run = lambda user: asyncio.run(ia.get_member_user(user))          # noqa: E731
    assert run({"sub": "alice@example.com"}) == {"sub": "alice@example.com"}
    for guest in ({"sub": "guest-recruiter@example.com"}, {"sub": "guest_bty"}, {"sub": " Guest_BTY "}):
        with pytest.raises(Exception) as caught:
            run(guest)
        assert getattr(caught.value, "status_code", None) == 403
    assert ia.is_guest_subject(None) is False and ia.is_guest_subject("") is False


# ---- the synthetic endpoint is gone --------------------------------------------------------------------------------------------------------------

def test_the_synthetic_endpoint_that_ran_the_model_for_anyone_no_longer_exists(client):
    calls = []

    class Workflow:
        async def ainvoke(self, *args, **kwargs):
            calls.append(1)
            return {"insight_answer": "x"}

    app_module.services["compiled_workflow"] = Workflow()
    try:
        for headers in (GUEST, {}):
            response = client.post("/api/synthetic/ask", headers=headers, json={"question": "hello"})
            assert response.status_code in (404, 405), response.status_code
    finally:
        app_module.services["compiled_workflow"] = None
    assert calls == [], "no model workflow was started"
    assert not [r for r in routes() if "synthetic" in r.path]


# ---- the GitHub webhook needs its secret ---------------------------------------------------------------------------------------------------------

PR_EVENT = {"action": "opened", "number": 7, "repository": {"full_name": "someone/some-repo"}}


def signed(secret, body: bytes):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_without_a_configured_secret_the_webhook_refuses_everything_including_an_unsigned_event(client, monkeypatch):
    queued = []
    monkeypatch.setattr(app_module, "process_pr_summary", lambda *a, **k: queued.append(a))
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    response = client.post("/webhooks/github", json=PR_EVENT, headers={"X-GitHub-Event": "pull_request"})
    assert response.status_code == 503 and queued == []
    body = json.dumps(PR_EVENT).encode()
    forged = client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": signed("", body)})
    assert forged.status_code == 503 and queued == [], "an empty secret signs nothing"


def test_with_a_secret_a_missing_wrong_or_malformed_signature_is_refused_and_a_right_one_goes_through(client, monkeypatch):
    queued = []
    monkeypatch.setattr(app_module, "process_pr_summary", lambda *a, **k: queued.append(a))
    monkeypatch.setattr(app_module, "find_github_token_for_repo", lambda repo: ("owner", "user-token"))
    monkeypatch.setattr(app_module, "audit_github_token_use", lambda *a, **k: None)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "the-real-secret")
    body = json.dumps(PR_EVENT).encode()
    base = {"X-GitHub-Event": "pull_request"}
    for headers in ({}, {"X-Hub-Signature-256": signed("another-secret", body)}, {"X-Hub-Signature-256": "sha256=deadbeef"}, {"X-Hub-Signature-256": signed("the-real-secret", body)[7:]}):
        assert client.post("/webhooks/github", content=body, headers={**base, **headers}).status_code == 401, headers
    assert queued == []
    ok = client.post("/webhooks/github", content=body, headers={**base, "X-Hub-Signature-256": signed("the-real-secret", body)})
    assert ok.status_code == 200 and ok.json()["status"] == "event_queued"


def test_the_ingest_webhook_still_needs_its_own_secret_and_compares_it_without_leaking_timing(client, monkeypatch):
    monkeypatch.setenv("INGEST_WEBHOOK_SECRET", "ingest-secret")
    payload = {"service_name": "x", "error_message": "boom", "stack_trace": "t"}
    assert client.post("/api/v1/webhooks/ingest", json=payload).status_code == 401
    assert client.post("/api/v1/webhooks/ingest", json=payload, headers={"X-Ingest-Secret": "ingest-secreT"}).status_code == 401
    source = open(app_module.__file__, encoding="utf-8").read()
    assert "hmac.compare_digest((x_ingest_secret" in source and "x_ingest_secret != configured_secret" not in source


# ---- request sizes -------------------------------------------------------------------------------------------------------------------------------

def header_list(**pairs):
    return [(k.replace("_", "-").encode(), v.encode()) for k, v in pairs.items()]


def test_the_limit_is_the_default_the_bigger_one_for_ingestion_and_the_smaller_one_for_a_guest():
    assert bl.limit_for("/api/chat", []) == bl.DEFAULT_LIMIT
    assert bl.limit_for("/api/upload", []) == bl.UPLOAD_LIMIT > bl.DEFAULT_LIMIT
    assert bl.limit_for("/api/upload-attachment", []) == bl.DEFAULT_LIMIT, "only ingestion gets the big one: the attachment route does not"
    for guest in (header_list(x_principal="guest"), header_list(x_principal="guest_bty"), header_list(authorization="Bearer guest-sandbox-token"), header_list(authorization="Bearer guest-bty-token")):
        assert bl.is_guest_request(guest) and bl.limit_for("/api/chat", guest) == bl.GUEST_LIMIT < bl.DEFAULT_LIMIT
        assert bl.limit_for("/api/upload", guest) == bl.GUEST_LIMIT, "a guest never gets the upload allowance"
    assert not bl.is_guest_request(header_list(authorization="Bearer a-real-clerk-token")) and not bl.is_guest_request(header_list(x_principal="alice@example.com"))


async def drive(middleware, path, headers, chunks, declared=None):
    sent, state = [], {"app_saw_bytes": 0, "app_finished": False}

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            state["app_saw_bytes"] += len(message.get("body") or b"")
            if not message.get("more_body"):
                break
        state["app_finished"] = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1} for i, c in enumerate(chunks)]

    async def receive():
        return queue.pop(0)

    async def send(message):
        sent.append(message)

    hdrs = list(headers) + ([(b"content-length", str(declared).encode())] if declared is not None else [])
    await middleware(inner)({"type": "http", "path": path, "headers": hdrs}, receive, send)
    return sent, state


def run(coro):
    return asyncio.run(coro)


def test_a_request_that_declares_itself_too_big_is_refused_without_reading_any_of_it(monkeypatch):
    monkeypatch.setattr(bl, "DEFAULT_LIMIT", 100)
    sent, state = run(drive(bl.BodySizeLimitMiddleware, "/api/chat", [], [b"x" * 500], declared=500))
    assert sent[0]["status"] == 413 and b"too large" in sent[1]["body"] and state["app_saw_bytes"] == 0


def test_a_request_that_does_not_declare_its_size_is_stopped_the_moment_it_streams_past_the_limit(monkeypatch):
    monkeypatch.setattr(bl, "DEFAULT_LIMIT", 100)
    sent, state = run(drive(bl.BodySizeLimitMiddleware, "/api/chat", [], [b"x" * 60, b"x" * 60, b"x" * 60]))
    assert sent[0]["status"] == 413 and state["app_finished"] is False and state["app_saw_bytes"] == 60, "the first chunk fit, the second did not"


def test_a_request_within_the_limit_passes_through_untouched_and_a_guest_is_held_to_the_smaller_limit(monkeypatch):
    monkeypatch.setattr(bl, "DEFAULT_LIMIT", 1000)
    monkeypatch.setattr(bl, "GUEST_LIMIT", 100)
    sent, state = run(drive(bl.BodySizeLimitMiddleware, "/api/chat", [], [b"x" * 900], declared=900))
    assert sent[0]["status"] == 200 and state["app_saw_bytes"] == 900
    sent, state = run(drive(bl.BodySizeLimitMiddleware, "/api/chat", header_list(x_principal="guest"), [b"x" * 900], declared=900))
    assert sent[0]["status"] == 413 and state["app_saw_bytes"] == 0


def test_only_http_requests_are_limited_and_a_bad_content_length_header_is_ignored_not_trusted(monkeypatch):
    monkeypatch.setattr(bl, "DEFAULT_LIMIT", 10)
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    run(bl.BodySizeLimitMiddleware(inner)({"type": "websocket", "path": "/ws", "headers": []}, None, None))
    assert reached == ["websocket"]
    sent, state = run(drive(bl.BodySizeLimitMiddleware, "/x", [(b"content-length", b"not-a-number")], [b"y" * 5], declared=None))
    assert sent[0]["status"] == 200 and state["app_finished"]


def test_the_real_app_refuses_a_guest_upload_over_the_guest_limit_before_reading_it_and_still_takes_a_small_one(client, monkeypatch):
    monkeypatch.setattr(bl, "GUEST_LIMIT", 50_000)
    big = client.post("/api/upload-attachment", data={"session_id": "s"}, files={"file": ("big.bin", b"x" * 200_000)}, headers=GUEST)
    assert big.status_code == 413 and "too large" in big.json()["detail"]
    small = client.post("/api/upload-attachment", data={"session_id": "s"}, files={"file": ("small.txt", b"hello")}, headers=GUEST)
    assert small.status_code == 200 and small.json()["filename"] == "small.txt"


def test_the_limit_is_registered_inside_the_cors_layer_so_a_413_still_carries_the_headers_a_browser_needs():
    names = [m.cls.__name__ for m in app_module.app.user_middleware]
    assert "BodySizeLimitMiddleware" in names and "CORSMiddleware" in names
    assert names.index("CORSMiddleware") < names.index("BodySizeLimitMiddleware"), "user_middleware lists the outermost first: CORS wraps the limit"
