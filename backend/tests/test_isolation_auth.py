import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.auth import isolation_auth as ia

_REAL_LOOKUP = ia._lookup_clerk_email
GUEST_SANDBOX = {"sub": "guest-recruiter@example.com", "email": "guest@example.com"}
GUEST_BTY = {"sub": "guest_bty", "email": "guest_bty@bty.local"}


def _request(headers: dict) -> Request:
    return Request({
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


def _call(headers: dict):
    return asyncio.run(ia.get_current_user(_request(headers)))


def _reject(token):
    raise HTTPException(status_code=401, detail="Authentication failed")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    ia._email_cache.clear()
    # Default: any non-guest bearer token fails verification, so a test that expects acceptance
    # must opt in via _clerk_jwt(). The Clerk API lookup is stubbed out too (no network).
    monkeypatch.setattr(ia, "_verify_clerk_jwt", _reject)
    monkeypatch.setattr(ia, "_lookup_clerk_email", lambda user_id: None)


def _clerk_jwt(monkeypatch, payload):
    monkeypatch.setattr(ia, "_verify_clerk_jwt", lambda token: dict(payload))


# ---- the vulnerability: an email header alone must not authenticate ----

def test_email_header_without_authorization_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _call({"X-Principal": "jackharper0517@gmail.com"})
    assert exc.value.status_code == 401


@pytest.mark.parametrize("header", ["x-principal", "X-Principal", "x-user-id"])
def test_every_principal_header_spelling_is_rejected_without_a_token(header):
    with pytest.raises(HTTPException) as exc:
        _call({header: "admin@example.com"})
    assert exc.value.status_code == 401


def test_email_header_with_invalid_token_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _call({"Authorization": "Bearer forged", "X-Principal": "admin@example.com"})
    assert exc.value.status_code == 401


def test_email_header_does_not_turn_a_guest_token_into_that_user():
    # Impersonation attempt riding on the guest token must still yield the guest identity.
    user = _call({"Authorization": "Bearer guest-sandbox-token", "X-Principal": "admin@example.com"})
    assert user == GUEST_SANDBOX


# ---- verified Clerk JWT + matching email ----

def test_matching_email_claim_is_accepted_and_becomes_sub(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123", "email": "jack@example.com"})
    user = _call({"Authorization": "Bearer real", "X-Principal": "jack@example.com"})
    assert user["sub"] == "jack@example.com"
    assert user["email"] == "jack@example.com"


def test_email_match_is_case_insensitive_and_returns_verified_casing(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123", "email": "Jack@Example.com"})
    user = _call({"Authorization": "Bearer real", "X-Principal": "jack@example.com"})
    assert user["sub"] == "Jack@Example.com"


def test_mismatched_email_header_is_rejected_even_with_a_valid_jwt(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123", "email": "jack@example.com"})
    with pytest.raises(HTTPException) as exc:
        _call({"Authorization": "Bearer real", "X-Principal": "admin@example.com"})
    assert exc.value.status_code == 403


def test_email_from_clerk_api_when_jwt_has_no_email_claim(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123"})
    monkeypatch.setattr(ia, "_lookup_clerk_email", lambda user_id: "jack@example.com" if user_id == "user_123" else None)
    user = _call({"Authorization": "Bearer real", "X-Principal": "jack@example.com"})
    assert user["sub"] == "jack@example.com"


def test_header_email_is_not_used_to_fill_a_missing_claim(monkeypatch):
    # The old code copied the header into payload["email"]; an unverifiable email now fails closed.
    _clerk_jwt(monkeypatch, {"sub": "user_123"})
    with pytest.raises(HTTPException) as exc:
        _call({"Authorization": "Bearer real", "X-Principal": "admin@example.com"})
    assert exc.value.status_code == 401


def test_clerk_api_email_for_a_different_user_does_not_match(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123"})
    monkeypatch.setattr(ia, "_lookup_clerk_email", lambda user_id: "jack@example.com")
    with pytest.raises(HTTPException) as exc:
        _call({"Authorization": "Bearer real", "X-Principal": "admin@example.com"})
    assert exc.value.status_code == 403


def test_valid_jwt_without_email_header_returns_payload_unchanged(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123"})
    assert _call({"Authorization": "Bearer real"}) == {"sub": "user_123"}


def test_non_email_principal_header_is_ignored_for_a_valid_jwt(monkeypatch):
    _clerk_jwt(monkeypatch, {"sub": "user_123"})
    assert _call({"Authorization": "Bearer real", "X-Principal": "jack"}) == {"sub": "user_123"}


# ---- guest flows: behavior intentionally unchanged ----

@pytest.mark.parametrize("headers,expected", [
    ({"X-Principal": "guest"}, GUEST_SANDBOX),
    ({"x-user-id": "guest"}, GUEST_SANDBOX),
    ({"X-Principal": "guest_bty"}, GUEST_BTY),
    ({"Authorization": "Bearer guest-sandbox-token"}, GUEST_SANDBOX),
    ({"Authorization": "Bearer guest-bty-token"}, GUEST_BTY),
    ({"Authorization": "Bearer guest-sandbox-token", "X-Principal": "guest"}, GUEST_SANDBOX),
    ({"Authorization": "Bearer guest-bty-token", "X-Principal": "guest_bty"}, GUEST_BTY),
])
def test_guest_paths_still_work(headers, expected):
    assert _call(headers) == expected


def test_missing_authorization_header_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _call({})
    assert exc.value.status_code == 401


# ---- Clerk API email lookup ----

def _fake_clerk(user, calls):
    class _Users:
        def get(self, user_id):
            calls.append(user_id)
            return user
    return lambda bearer_auth: SimpleNamespace(users=_Users())


def _user(addresses, primary_id):
    return SimpleNamespace(primary_email_address_id=primary_id, email_addresses=addresses)


def _addr(id_, email, status="verified"):
    return SimpleNamespace(id=id_, email_address=email, verification=SimpleNamespace(status=status))


@pytest.fixture
def real_lookup(monkeypatch):
    # Restore the real _lookup_clerk_email that the autouse fixture stubbed out.
    monkeypatch.setattr(ia, "_lookup_clerk_email", _REAL_LOOKUP)
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test")


def test_lookup_returns_primary_verified_email_and_caches_it(real_lookup, monkeypatch):
    calls = []
    user = _user([_addr("a", "other@example.com"), _addr("b", "jack@example.com")], "b")
    monkeypatch.setattr(ia, "Clerk", _fake_clerk(user, calls))
    assert ia._lookup_clerk_email("user_1") == "jack@example.com"
    assert ia._lookup_clerk_email("user_1") == "jack@example.com"
    assert calls == ["user_1"]


def test_lookup_ignores_unverified_primary_email(real_lookup, monkeypatch):
    user = _user([_addr("a", "jack@example.com", status="unverified")], "a")
    monkeypatch.setattr(ia, "Clerk", _fake_clerk(user, []))
    assert ia._lookup_clerk_email("user_1") is None


def test_lookup_without_secret_key_returns_none(real_lookup, monkeypatch):
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    assert ia._lookup_clerk_email("user_1") is None


def test_lookup_api_failure_returns_none_and_is_not_cached(real_lookup, monkeypatch):
    def _boom(bearer_auth):
        raise RuntimeError("clerk down")
    monkeypatch.setattr(ia, "Clerk", _boom)
    assert ia._lookup_clerk_email("user_1") is None
    assert "user_1" not in ia._email_cache
