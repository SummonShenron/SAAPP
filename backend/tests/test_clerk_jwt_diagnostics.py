import time

import jwt

from backend.auth import isolation_auth as ia


def make(iat, exp):
    return jwt.encode({"iat": iat, "exp": exp, "sub": "user_secret_value", "email": "someone@example.com"}, "k", algorithm="HS256")


def test_an_expired_token_is_described_by_its_timing_alone():
    now = 1_800_000_000
    out = ia.describe_expired_token(make(now - 190, now - 130), now=now)
    assert "lifetime=60s" in out and "age_on_arrival=190s" in out and "expired_for=130s" in out


def test_a_token_that_is_expired_the_moment_it_arrives_reads_as_a_clock_disagreement():
    now = 1_800_000_000
    out = ia.describe_expired_token(make(now - 62, now - 2), now=now)
    assert "lifetime=60s" in out and "age_on_arrival=62s" in out and "expired_for=2s" in out


def test_the_description_never_contains_the_token_or_anything_from_its_content():
    token = make(10, 70)
    out = ia.describe_expired_token(token, now=100)
    assert token not in out and "user_secret_value" not in out and "someone@example.com" not in out and token[:20] not in out


def test_a_malformed_token_is_described_without_raising():
    assert ia.describe_expired_token("not-a-token") == "token timing could not be read"
    assert "no iat/exp" in ia.describe_expired_token(jwt.encode({"sub": "x"}, "k", algorithm="HS256"))


def test_an_expired_token_is_still_refused_with_a_401_and_the_timing_is_logged(monkeypatch, caplog):
    import pytest
    from fastapi import HTTPException

    token = jwt.encode({"iat": int(time.time()) - 200, "exp": int(time.time()) - 140}, "k", algorithm="HS256", headers={"kid": "abc"})
    monkeypatch.setattr(ia, "get_clerk_public_key", lambda: {"keys": [{"kid": "abc"}]})

    def expired(*args, **kwargs):
        raise jwt.ExpiredSignatureError("Signature has expired")

    monkeypatch.setattr(ia.jwt.algorithms.RSAAlgorithm, "from_jwk", staticmethod(lambda data: "key"))
    monkeypatch.setattr(ia.jwt, "decode", lambda t, *a, **k: expired() if k.get("algorithms") else jwt.api_jwt.decode_complete(t, options={"verify_signature": False, "verify_exp": False})["payload"])
    with caplog.at_level("ERROR", logger="SASS Logger"):
        with pytest.raises(HTTPException) as caught:
            ia._verify_clerk_jwt(token)
    assert caught.value.status_code == 401
    assert any("Signature has expired" in r.message and "expired_for=" in r.message for r in caplog.records)
