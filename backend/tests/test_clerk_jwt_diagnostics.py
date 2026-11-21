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


# ---- clock skew: a machine whose clock is a little behind Clerk's must not refuse every fresh token ---------------------------------------------------------

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException


@pytest.fixture
def signer(monkeypatch):
    """A real RS256 key pair; the app is told the public half is Clerk's, so tokens made here go through PyJWT's own validation (no stub of decode)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk["kid"] = "test-key"
    monkeypatch.setattr(ia, "get_clerk_public_key", lambda: {"keys": [jwk]})

    def make_token(iat_offset, exp_offset, **extra):
        now = int(time.time())
        return jwt.encode({"sub": "user_abc", "iat": now + iat_offset, "exp": now + exp_offset, "nbf": now + iat_offset, **extra}, private, algorithm="RS256", headers={"kid": "test-key"})

    return make_token


def test_a_token_stamped_a_few_seconds_ahead_of_this_clock_is_accepted(signer):
    assert ia.CLOCK_SKEW_SECONDS == 10
    assert ia._verify_clerk_jwt(signer(+5, +65))["sub"] == "user_abc"       # this machine's clock is 5 s behind Clerk's: "not yet valid (iat)" before the fix


def test_a_token_stamped_further_ahead_than_the_allowed_skew_is_still_refused_and_the_log_says_by_how_much_and_what_to_do(signer, caplog):
    with caplog.at_level("ERROR", logger="SASS Logger"):
        with pytest.raises(HTTPException) as caught:
            ia._verify_clerk_jwt(signer(+60, +120))
    assert caught.value.status_code == 401 and caught.value.detail == "Authentication failed"
    line = next(r.message for r in caplog.records if "not yet valid" in r.message)
    assert "issued_ahead_of_this_clock_by=" in line and "allowed_skew=10s" in line and "w32tm /resync" in line
    assert "user_abc" not in line, "nothing from the token's content is logged"


def test_an_expired_token_is_still_refused_beyond_the_skew_and_a_tolerance_only_widens_it_by_that_much(signer):
    with pytest.raises(HTTPException):
        ia._verify_clerk_jwt(signer(-200, -30))                              # 30 s past its end, more than the 10 s of skew
    assert ia._verify_clerk_jwt(signer(-60, -4))["sub"] == "user_abc"        # 4 s past its end: inside the tolerance (a session token lives about 60 s)


def test_with_no_skew_allowed_the_old_strictness_is_back(signer, monkeypatch):
    monkeypatch.setattr(ia, "CLOCK_SKEW_SECONDS", 0)
    with pytest.raises(HTTPException):
        ia._verify_clerk_jwt(signer(+5, +65))
    assert ia._verify_clerk_jwt(signer(-1, +59))["sub"] == "user_abc", "an ordinary fresh token is fine"


def test_a_forged_or_wrongly_signed_token_is_refused_whatever_the_skew(signer, monkeypatch):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"sub": "attacker", "iat": int(time.time()), "exp": int(time.time()) + 60}, other, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(HTTPException):
        ia._verify_clerk_jwt(forged)
    monkeypatch.setattr(ia, "CLOCK_SKEW_SECONDS", 120)
    with pytest.raises(HTTPException):
        ia._verify_clerk_jwt(forged)


@pytest.mark.parametrize("raw, expected", [(None, 10), ("0", 0), ("30", 30), ("500", 120), ("-5", 0), ("abc", 10), ("", 10), ("2.7", 2), (7, 7)])
def test_the_allowed_skew_is_read_from_the_environment_clamped_and_never_crashes(raw, expected, monkeypatch):
    monkeypatch.delenv("JWT_CLOCK_SKEW_SECONDS", raising=False)
    assert ia.clock_skew_seconds(raw) == expected


def test_the_skew_comes_from_the_environment_variable_when_no_value_is_passed(monkeypatch):
    monkeypatch.setenv("JWT_CLOCK_SKEW_SECONDS", "45")
    assert ia.clock_skew_seconds() == 45


def test_an_early_token_is_described_by_its_timing_alone_and_a_malformed_one_does_not_raise():
    now = 1_800_000_000
    out = ia.describe_early_token(make(now + 12, now + 72), now=now)
    assert "issued_ahead_of_this_clock_by=12s" in out and "user_secret_value" not in out and "someone@example.com" not in out
    assert ia.describe_early_token("not-a-token") == "token timing could not be read"
    assert "no iat" in ia.describe_early_token(jwt.encode({"sub": "x"}, "k", algorithm="HS256"))


def test_the_decode_call_passes_the_skew_as_leeway():
    source = open("backend/auth/isolation_auth.py", encoding="utf-8").read()
    assert 'jwt.decode(token, public_key, algorithms=["RS256"], leeway=CLOCK_SKEW_SECONDS)' in source
