"""The per-address request limit (backend/utils/rate_limit.py): the launch probe's item 5, no request-volume limit on login, pairing, webhooks or the shared guest identity.

Time is driven by hand (the limiter takes the clock as an argument), the model and the database are never touched, and the real app is exercised through its real middleware stack."""
import asyncio
import itertools

import pytest
from fastapi.testclient import TestClient

from backend.tests.import_app import import_app
from backend.utils import guest_limits, rate_limit as rl, self_counters
from backend.utils.body_limit import BodySizeLimitMiddleware

app_module = import_app()
GUEST = [(b"x-principal", b"guest")]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("RATE_AUTH_PER_MIN", "RATE_AUTH_ALL_PER_MIN", "RATE_WEBHOOK_PER_MIN", "RATE_WEBHOOK_ALL_PER_MIN", "RATE_GUEST_PER_MIN", "RATE_GUEST_ALL_PER_MIN", "TRUSTED_PROXY_HOPS"):
        monkeypatch.delenv(name, raising=False)


# ---- the address -------------------------------------------------------------------------------------------------------------------------------------

def test_with_no_trusted_proxy_the_address_is_the_first_forwarded_entry_as_the_guest_limit_always_read_it():
    assert rl.address_from("198.51.100.4, 10.0.0.1", "10.0.0.9") == "198.51.100.4"


def test_with_trusted_proxies_it_is_the_entry_that_many_from_the_right_so_a_made_up_first_entry_changes_nothing():
    # the caller sends "1.2.3.4"; the one proxy in front of the app appends the address it really saw
    assert rl.address_from("1.2.3.4, 203.0.113.7", "10.0.0.9", hops=1) == "203.0.113.7"
    assert rl.address_from("1.2.3.4, 5.6.7.8, 203.0.113.7, 10.1.1.1", "10.0.0.9", hops=2) == "203.0.113.7"
    # a list shorter than the number of proxies cannot be trusted past its start
    assert rl.address_from("1.2.3.4", "10.0.0.9", hops=3) == "1.2.3.4"


def test_with_no_forwarded_list_it_is_the_direct_peer_and_then_unknown():
    assert rl.address_from(None, "10.0.0.9") == "10.0.0.9"
    assert rl.address_from("", "10.0.0.9", hops=1) == "10.0.0.9"
    assert rl.address_from(" , ", None) == "unknown"


def test_the_number_of_proxies_comes_from_the_environment_and_a_bad_value_means_none(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert rl.address_from("1.2.3.4, 203.0.113.7", None) == "203.0.113.7"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "lots")
    assert rl.address_from("1.2.3.4, 203.0.113.7", None) == "1.2.3.4"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "-2")
    assert rl.address_from("1.2.3.4, 203.0.113.7", None) == "1.2.3.4"


def test_the_guest_turn_limit_reads_the_address_the_same_way(monkeypatch):
    assert guest_limits.client_address("1.2.3.4, 203.0.113.7", "10.0.0.9") == "1.2.3.4"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    assert guest_limits.client_address("1.2.3.4, 203.0.113.7", "10.0.0.9") == "203.0.113.7"


# ---- which requests are limited ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,rule", [
    ("/api/login", "auth"), ("/api/login/", "auth"), ("/api/runner/pair", "auth"),
    ("/webhooks/github", "webhook"), ("/api/v1/webhooks/ingest", "webhook"), ("/api/billing/webhook", "webhook"),
])
def test_the_doors_anyone_can_knock_on_have_their_own_rules_whoever_the_caller_claims_to_be(path, rule):
    assert rl.classify("POST", path, []) == rule
    assert rl.classify("POST", path, GUEST) == rule


def test_a_request_that_claims_the_guest_identity_is_limited_on_any_other_path():
    assert rl.classify("GET", "/api/conversations", GUEST) == "guest"
    assert rl.classify("POST", "/api/chat", [(b"authorization", b"Bearer guest-sandbox-token")]) == "guest"


def test_a_signed_in_member_and_a_preflight_are_never_counted():
    assert rl.classify("GET", "/api/conversations", [(b"authorization", b"Bearer some.real.jwt")]) is None
    assert rl.classify("POST", "/api/chat", []) is None
    assert rl.classify("OPTIONS", "/api/login", []) is None
    assert rl.classify("OPTIONS", "/api/chat", GUEST) is None


# ---- the counters ------------------------------------------------------------------------------------------------------------------------------------

def test_an_address_may_send_its_limit_in_a_minute_and_the_next_is_refused_with_how_long_to_wait(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "3")
    lim = rl.Limiter()
    assert [lim.check("auth", "a", t) for t in (0.0, 1.0, 2.0)] == [None, None, None]
    which, wait = lim.check("auth", "a", 10.0)
    assert which == "address" and wait == 50            # the oldest hit (t=0) leaves the window at t=60


def test_the_window_slides_so_an_address_is_let_back_in_as_old_requests_age_out(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "2")
    lim = rl.Limiter()
    assert lim.check("auth", "a", 0.0) is None and lim.check("auth", "a", 30.0) is None
    assert lim.check("auth", "a", 59.0) is not None
    assert lim.check("auth", "a", 61.0) is None           # the request from t=0 is gone, the one from t=30 is not
    assert lim.check("auth", "a", 62.0) is not None


def test_each_address_and_each_rule_has_its_own_allowance(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")
    monkeypatch.setenv("RATE_GUEST_PER_MIN", "1")
    lim = rl.Limiter()
    assert lim.check("auth", "a", 0.0) is None
    assert lim.check("auth", "a", 1.0) is not None
    assert lim.check("auth", "b", 1.0) is None
    assert lim.check("guest", "a", 1.0) is None


def test_everyone_together_has_a_total_that_holds_even_when_every_request_comes_from_a_new_address(monkeypatch):
    monkeypatch.setenv("RATE_WEBHOOK_PER_MIN", "100")
    monkeypatch.setenv("RATE_WEBHOOK_ALL_PER_MIN", "5")
    lim = rl.Limiter()
    for i in range(5):
        assert lim.check("webhook", f"made-up-{i}", float(i)) is None
    assert lim.check("webhook", "made-up-5", 6.0)[0] == "all"


def test_a_request_refused_for_its_address_does_not_use_up_everyone_elses_total(monkeypatch):
    monkeypatch.setenv("RATE_WEBHOOK_PER_MIN", "2")
    monkeypatch.setenv("RATE_WEBHOOK_ALL_PER_MIN", "4")
    lim = rl.Limiter()
    assert lim.check("webhook", "noisy", 0.0) is None and lim.check("webhook", "noisy", 0.1) is None
    for t in range(1, 50):
        assert lim.check("webhook", "noisy", float(t) / 10)[0] == "address"
    assert lim.check("webhook", "quiet-1", 5.0) is None and lim.check("webhook", "quiet-2", 5.0) is None     # 2 + 2 = the total of 4, so noisy's 49 refusals cost nothing
    assert lim.check("webhook", "quiet-3", 5.0)[0] == "all"


def test_zero_switches_a_limit_off_and_a_bad_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "0")
    monkeypatch.setenv("RATE_AUTH_ALL_PER_MIN", "0")
    lim = rl.Limiter()
    assert all(lim.check("auth", "a", float(t)) is None for t in range(500))
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "ten")
    assert rl.per_address_limit("auth") == 10


def test_memory_is_bounded_so_an_endless_supply_of_made_up_addresses_cannot_grow_it_and_the_total_still_holds(monkeypatch):
    monkeypatch.setattr(rl, "MAX_KEYS", 5)
    monkeypatch.setenv("RATE_GUEST_PER_MIN", "100")
    monkeypatch.setenv("RATE_GUEST_ALL_PER_MIN", "50")
    lim = rl.Limiter()
    allowed = 0
    for i in range(200):
        if lim.check("guest", f"made-up-{i}", 1.0 + i / 1000) is None:
            allowed += 1
    assert len(lim._hits) <= 6                       # the cap, plus the one shared total
    assert allowed == 50


def test_addresses_that_have_gone_quiet_are_swept_when_the_table_is_full(monkeypatch):
    monkeypatch.setattr(rl, "MAX_KEYS", 3)
    lim = rl.Limiter()
    for name in ("a", "b", "c"):
        assert lim.check("guest", name, 0.0) is None
    assert lim.check("guest", "d", 500.0) is None
    assert ("guest", "d") in lim._hits and ("guest", "a") not in lim._hits


# ---- the middleware ----------------------------------------------------------------------------------------------------------------------------------

def run_request(mw, method="POST", path="/api/login", headers=None, peer="203.0.113.7"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers or [], "client": (peer, 5555)}
    asyncio.run(mw(scope, receive, send))
    return sent


def make_middleware(clock):
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return rl.RateLimitMiddleware(inner, clock=clock), calls


def test_a_request_within_the_limit_reaches_the_app_and_one_over_it_does_not_and_gets_a_429_with_retry_after(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "2")
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: None)
    now = [0.0]
    mw, calls = make_middleware(lambda: now[0])
    assert [run_request(mw)[0]["status"] for _ in range(2)] == [200, 200]
    sent = run_request(mw)
    assert sent[0]["status"] == 429 and len(calls) == 2
    assert dict(sent[0]["headers"])[b"retry-after"] == b"60"
    assert b'"code": "rate_limited"' in sent[1]["body"] and b'"reason": "auth"' in sent[1]["body"]


def test_the_address_is_read_from_the_forwarded_list_so_two_callers_behind_one_proxy_are_told_apart(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: None)
    mw, _ = make_middleware(lambda: 0.0)
    first = run_request(mw, headers=[(b"x-forwarded-for", b"198.51.100.1")], peer="10.0.0.1")
    other = run_request(mw, headers=[(b"x-forwarded-for", b"198.51.100.2")], peer="10.0.0.1")
    again = run_request(mw, headers=[(b"x-forwarded-for", b"198.51.100.1")], peer="10.0.0.1")
    assert [first[0]["status"], other[0]["status"], again[0]["status"]] == [200, 200, 429]


def test_a_request_no_rule_covers_goes_straight_through_and_so_does_a_websocket(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")
    mw, calls = make_middleware(lambda: 0.0)
    for _ in range(5):
        assert run_request(mw, method="GET", path="/api/conversations")[0]["status"] == 200
    assert len(calls) == 5
    sent = []

    async def inner(scope, receive, send):
        sent.append(scope["type"])

    ws = rl.RateLimitMiddleware(inner)
    for _ in range(3):                               # more than the limit of 1: a websocket is never counted, so none is refused (a refusal would try to send an HTTP response on a None channel)
        asyncio.run(ws({"type": "websocket", "path": "/api/login", "headers": []}, None, None))
    assert sent == ["websocket"] * 3


def test_refusals_are_counted_by_rule_and_written_at_most_once_a_minute_so_a_flood_is_not_a_flood_of_writes(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")
    monkeypatch.setenv("RATE_AUTH_ALL_PER_MIN", "0")
    written = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: written.append(dict(counts)))
    now = [1000.0]
    mw, _ = make_middleware(lambda: now[0])
    run_request(mw)                                   # allowed
    for _ in range(50):                               # refused, all inside one minute
        run_request(mw)
    assert written == [{"rate_limited_auth": 1}]     # the first refusal was written at once; the other 49 wait in memory
    now[0] += 61.0
    run_request(mw)                                   # a new minute: the window has slid, so this one is let in
    run_request(mw)                                   # and this one is refused, which sends out the 49 waiting plus itself
    assert written[-1] == {"rate_limited_auth": 50} and len(written) == 2


def test_a_refusal_for_the_total_is_counted_under_its_own_key(monkeypatch):
    monkeypatch.setenv("RATE_WEBHOOK_PER_MIN", "100")
    monkeypatch.setenv("RATE_WEBHOOK_ALL_PER_MIN", "1")
    written = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: written.append(dict(counts)))
    mw, _ = make_middleware(lambda: 0.0)
    run_request(mw, path="/webhooks/github", peer="1.1.1.1")
    run_request(mw, path="/webhooks/github", peer="2.2.2.2")
    assert written == [{"rate_limited_webhook_all": 1}]


def test_a_failing_counter_write_never_stops_the_refusal(monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")

    def boom(counts, now=None):
        raise RuntimeError("database is down")

    monkeypatch.setattr(self_counters, "record_turn_counts", boom)
    mw, _ = make_middleware(lambda: 0.0)
    run_request(mw)
    assert run_request(mw)[0]["status"] == 429


def test_the_counter_keys_the_limiter_writes_are_the_keys_the_store_accepts():
    assert set(rl.KEYS) == {f"rate_limited_{rule}{suffix}" for rule in ("auth", "webhook", "guest") for suffix in ("", "_all")}
    assert set(rl.KEYS) <= self_counters.KEYS
    assert set(self_counters.RATE_LIMIT_KEYS) == set(rl.KEYS)


# ---- the real app ------------------------------------------------------------------------------------------------------------------------------------

_ids = itertools.count(1)


def fresh_ip():
    return f"203.0.113.{next(_ids) % 250 + 1}"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: None)
    return TestClient(app_module.app, raise_server_exceptions=False)


def test_the_middleware_sits_inside_cors_and_outside_the_size_limit():
    order = [m.cls for m in app_module.app.user_middleware]
    names = [c.__name__ for c in order]
    assert "RateLimitMiddleware" in names
    assert names.index("CORSMiddleware") < names.index("RateLimitMiddleware") < names.index("BodySizeLimitMiddleware")
    assert BodySizeLimitMiddleware in order


def test_the_real_login_door_refuses_an_address_after_its_limit_and_another_address_is_unaffected(client, monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "3")
    ip, other = fresh_ip(), fresh_ip()
    codes = [client.post("/api/login", json={}, headers={"X-Forwarded-For": ip}).status_code for _ in range(5)]
    assert 429 not in codes[:3] and codes[3:] == [429, 429]
    assert client.post("/api/login", json={}, headers={"X-Forwarded-For": other}).status_code != 429


def test_the_real_webhooks_and_pairing_are_limited_too_while_an_ordinary_signed_in_route_is_not(client, monkeypatch):
    monkeypatch.setenv("RATE_WEBHOOK_PER_MIN", "2")
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "2")
    for path in ("/webhooks/github", "/api/v1/webhooks/ingest", "/api/billing/webhook", "/api/runner/pair"):
        ip = fresh_ip()                              # the three webhooks share one allowance per address, so each path gets its own address here
        codes = [client.post(path, content=b"{}", headers={"X-Forwarded-For": ip}).status_code for _ in range(4)]
        assert codes[2:] == [429, 429], path
        assert 429 not in codes[:2], path


def test_a_guest_over_its_limit_is_refused_on_any_endpoint_and_a_member_from_the_same_address_is_not(client, monkeypatch):
    monkeypatch.setenv("RATE_GUEST_PER_MIN", "2")
    ip = fresh_ip()
    guest = {"X-Principal": "guest", "X-Forwarded-For": ip}
    codes = [client.get("/api/health", headers=guest).status_code for _ in range(4)]
    assert codes[2:] == [429, 429] and 429 not in codes[:2]
    member = {"Authorization": "Bearer not-a-real-token", "X-Forwarded-For": ip}
    assert all(client.get("/api/health", headers=member).status_code != 429 for _ in range(6))


def test_a_refusal_still_carries_the_cors_headers_a_browser_needs_to_read_it(client, monkeypatch):
    monkeypatch.setenv("RATE_AUTH_PER_MIN", "1")
    ip = fresh_ip()
    headers = {"X-Forwarded-For": ip, "Origin": "https://sonicassistant.com"}
    client.post("/api/login", json={}, headers=headers)
    refused = client.post("/api/login", json={}, headers=headers)
    assert refused.status_code == 429
    assert refused.headers["access-control-allow-origin"] == "https://sonicassistant.com"
    assert refused.headers["retry-after"].isdigit()
    assert refused.json()["code"] == "rate_limited"
