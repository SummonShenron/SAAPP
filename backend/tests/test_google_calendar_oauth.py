import datetime
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from backend.services import google_calendar_oauth as gco


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://saapp.example.com/api/calendar/callback")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_flow_raises_clearly_when_oauth_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        gco.GoogleCalendarOAuth()._flow()


def test_fernet_raises_clearly_when_encryption_key_not_configured(monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(ValueError, match="Token storage is not configured"):
        gco.GoogleCalendarOAuth()._fernet()


def test_fernet_raises_clearly_on_invalid_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(ValueError, match="must be a valid Fernet key"):
        gco.GoogleCalendarOAuth()._fernet()


def test_authorization_url_generates_state_and_pkce_verifier(monkeypatch):
    fake_flow = SimpleNamespace(
        authorization_url=lambda **kwargs: ("https://accounts.google.com/o/oauth2/auth?...", None),
        code_verifier="fake-verifier",
    )
    monkeypatch.setattr(gco.GoogleCalendarOAuth, "_flow", lambda self, state=None, code_verifier=None: fake_flow)

    url, state, code_verifier = gco.GoogleCalendarOAuth().authorization_url()

    assert url.startswith("https://accounts.google.com")
    assert state  # a real random token was generated
    assert code_verifier == "fake-verifier"


def test_authorization_url_raises_if_no_pkce_verifier_generated(monkeypatch):
    fake_flow = SimpleNamespace(authorization_url=lambda **kwargs: ("https://x", None), code_verifier=None)
    monkeypatch.setattr(gco.GoogleCalendarOAuth, "_flow", lambda self, state=None, code_verifier=None: fake_flow)

    with pytest.raises(ValueError, match="PKCE verifier"):
        gco.GoogleCalendarOAuth().authorization_url()


def _fake_credentials(token="access-tok", refresh_token="refresh-tok", scopes=None, expiry="2026-01-01"):
    # scopes=None means "use the default" — distinct from scopes=[] (explicitly empty, used by
    # test_build_connection_raises_if_no_scopes_granted), so this can't use `scopes or [...]`.
    if scopes is None:
        scopes = ["calendar.events"]
    return SimpleNamespace(token=token, refresh_token=refresh_token, scopes=scopes, expiry=expiry)


def test_build_connection_encrypts_tokens_and_fetches_email(monkeypatch):
    credentials = _fake_credentials()
    fake_flow = SimpleNamespace(fetch_token=lambda authorization_response: None, credentials=credentials)
    monkeypatch.setattr(gco.GoogleCalendarOAuth, "_flow", lambda self, state=None, code_verifier=None: fake_flow)
    monkeypatch.setattr(
        gco.requests, "get",
        lambda url, headers=None, timeout=None: SimpleNamespace(status_code=200, json=lambda: {"email": "jack@example.com"}),
    )

    result = gco.GoogleCalendarOAuth().build_connection("https://.../callback?code=abc", "state-1", "verifier-1")

    assert result["account_email"] == "jack@example.com"
    assert result["scopes"] == ["calendar.events"]
    assert result["expires_at"] == "2026-01-01"
    # Round-trips through the real Fernet key set by the fixture.
    fernet = gco.GoogleCalendarOAuth()._fernet()
    assert fernet.decrypt(result["access_token_encrypted"].encode()).decode() == "access-tok"
    assert fernet.decrypt(result["refresh_token_encrypted"].encode()).decode() == "refresh-tok"


def test_build_connection_raises_if_no_scopes_granted(monkeypatch):
    credentials = _fake_credentials(scopes=[])
    fake_flow = SimpleNamespace(fetch_token=lambda authorization_response: None, credentials=credentials)
    monkeypatch.setattr(gco.GoogleCalendarOAuth, "_flow", lambda self, state=None, code_verifier=None: fake_flow)

    with pytest.raises(ValueError, match="did not grant any Calendar scopes"):
        gco.GoogleCalendarOAuth().build_connection("https://.../callback?code=abc", "state-1", "verifier-1")


# ---------------------------------------------------------------------------
# _allow_insecure_transport_for_local_dev — oauthlib refuses to even PARSE a non-https
# authorization response (InsecureTransportError), a real crash hit testing locally against
# http://localhost, where Google itself explicitly allows a loopback redirect URI for testing —
# this is oauthlib's own client-side safety check, not something Google requires. Scoped
# narrowly to an actual loopback GOOGLE_REDIRECT_URI so it can never weaken the real check for a
# genuine https:// production redirect URI.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_oauthlib_env_var():
    # Production code sets this via os.environ directly (not monkeypatch, since it's a real
    # runtime behavior change, not a test fixture) — monkeypatch can't auto-revert a mutation it
    # didn't make itself, so every test in this section must clean up explicitly or the var leaks
    # into every other test in the same process.
    original = os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    yield
    if original is None:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
    else:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = original


def test_allows_insecure_transport_for_localhost_redirect_uri(monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/calendar/callback")

    gco.GoogleCalendarOAuth()._allow_insecure_transport_for_local_dev()

    assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"


def test_allows_insecure_transport_for_loopback_ip_redirect_uri(monkeypatch):
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/calendar/callback")

    gco.GoogleCalendarOAuth()._allow_insecure_transport_for_local_dev()

    assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"


def test_does_not_touch_insecure_transport_for_a_real_https_redirect_uri():
    # The autouse `oauth_env` fixture already sets a real https:// GOOGLE_REDIRECT_URI.
    gco.GoogleCalendarOAuth()._allow_insecure_transport_for_local_dev()

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ


def test_build_connection_calls_the_local_dev_check(monkeypatch):
    """Integration-level: build_connection must actually invoke the check, not just have it
    exist unused — this is the exact real crash (InsecureTransportError from flow.fetch_token)
    this whole fix closes."""
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/calendar/callback")
    credentials = _fake_credentials()
    fake_flow = SimpleNamespace(fetch_token=lambda authorization_response: None, credentials=credentials)
    monkeypatch.setattr(gco.GoogleCalendarOAuth, "_flow", lambda self, state=None, code_verifier=None: fake_flow)
    monkeypatch.setattr(
        gco.requests, "get",
        lambda url, headers=None, timeout=None: SimpleNamespace(status_code=200, json=lambda: {"email": "jack@example.com"}),
    )

    gco.GoogleCalendarOAuth().build_connection("http://localhost:8000/api/calendar/callback?code=abc", "state-1", "verifier-1")

    assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"


def test_get_valid_access_token_returns_token_without_refresh_when_not_expired(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = {
        "access_token_encrypted": fernet.encrypt(b"still-valid-token").decode(),
        "refresh_token_encrypted": fernet.encrypt(b"refresh-tok").decode(),
        "scopes": ["calendar.events"],
        "expires_at": datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
    }
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)

    fake_credentials = SimpleNamespace(token="still-valid-token", expired=False, refresh_token="refresh-tok")
    monkeypatch.setattr(gco, "Credentials", lambda **kwargs: fake_credentials)

    token = gco.GoogleCalendarOAuth().get_valid_access_token("jack")
    assert token == "still-valid-token"


def _real_credentials_spy(monkeypatch, refreshed_token="fresh-token"):
    """Uses the REAL google-auth Credentials (so its own `expired` logic runs) with only the network
    refresh stubbed out — the earlier tests mocked Credentials wholesale with `expired` hardcoded,
    which is exactly why a missing `expiry=` went unnoticed."""
    calls = {"refreshed": 0, "persisted": None}

    def fake_refresh(self, request):
        calls["refreshed"] += 1
        self.token = refreshed_token
        self.expiry = datetime.datetime.utcnow() + datetime.timedelta(hours=1)

    monkeypatch.setattr(gco.Credentials, "refresh", fake_refresh)
    monkeypatch.setattr(
        "backend.utils.google_calendar_utils.update_tokens",
        lambda username, **kwargs: calls.__setitem__("persisted", kwargs),
    )
    return calls


def _stored_doc(fernet, expires_at, with_refresh=True):
    return {
        "access_token_encrypted": fernet.encrypt(b"stored-token").decode(),
        "refresh_token_encrypted": fernet.encrypt(b"refresh-tok").decode() if with_refresh else None,
        "scopes": ["calendar.events"],
        "expires_at": expires_at,
    }


def test_expired_stored_token_is_refreshed_and_persisted(monkeypatch):
    # The real failure: a token stored two days ago was returned as-is, Google answered 401, and the
    # calendar call died with "credentials do not contain the necessary fields to refresh".
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = _stored_doc(fernet, datetime.datetime.utcnow() - datetime.timedelta(days=2))
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)
    calls = _real_credentials_spy(monkeypatch)

    token = gco.GoogleCalendarOAuth().get_valid_access_token("jack")

    assert token == "fresh-token"
    assert calls["refreshed"] == 1
    assert fernet.decrypt(calls["persisted"]["access_token_encrypted"].encode()) == b"fresh-token"


def test_unexpired_stored_token_is_returned_without_a_network_refresh(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = _stored_doc(fernet, datetime.datetime.utcnow() + datetime.timedelta(minutes=30))
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)
    calls = _real_credentials_spy(monkeypatch)

    assert gco.GoogleCalendarOAuth().get_valid_access_token("jack") == "stored-token"
    assert calls["refreshed"] == 0


def test_timezone_aware_and_iso_string_expiries_are_understood(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    monkeypatch.setattr(
        "backend.utils.google_calendar_utils.get_encrypted_tokens",
        lambda username: _stored_doc(fernet, datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)),
    )
    calls = _real_credentials_spy(monkeypatch)
    assert gco.GoogleCalendarOAuth().get_valid_access_token("jack") == "fresh-token"
    assert calls["refreshed"] == 1

    future_iso = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)).isoformat()
    monkeypatch.setattr(
        "backend.utils.google_calendar_utils.get_encrypted_tokens",
        lambda username: _stored_doc(fernet, future_iso),
    )
    calls = _real_credentials_spy(monkeypatch)
    assert gco.GoogleCalendarOAuth().get_valid_access_token("jack") == "stored-token"
    assert calls["refreshed"] == 0


def test_unknown_expiry_refreshes_instead_of_trusting_a_possibly_stale_token(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    for unknown in (None, "not-a-date"):
        monkeypatch.setattr(
            "backend.utils.google_calendar_utils.get_encrypted_tokens",
            lambda username, u=unknown: _stored_doc(fernet, u),
        )
        calls = _real_credentials_spy(monkeypatch)
        assert gco.GoogleCalendarOAuth().get_valid_access_token("jack") == "fresh-token"
        assert calls["refreshed"] == 1


def test_expired_token_with_no_refresh_token_asks_the_user_to_reconnect(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = _stored_doc(fernet, datetime.datetime.utcnow() - datetime.timedelta(days=2), with_refresh=False)
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)
    deleted = []
    monkeypatch.setattr("backend.utils.google_calendar_utils.delete_connection", deleted.append)

    with pytest.raises(gco.GoogleCalendarConnectionError, match="reconnect required"):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")
    assert deleted == ["jack"]


def test_get_valid_access_token_raises_with_no_connection(monkeypatch):
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: None)
    with pytest.raises(gco.GoogleCalendarConnectionError, match="No Google Calendar connection"):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")


def test_get_valid_access_token_self_heals_on_revoked_grant(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = {
        "access_token_encrypted": fernet.encrypt(b"expired-token").decode(),
        "refresh_token_encrypted": fernet.encrypt(b"refresh-tok").decode(),
        "scopes": ["calendar.events"],
    }
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)
    deleted = {"called_with": None}
    monkeypatch.setattr("backend.utils.google_calendar_utils.delete_connection", lambda username: deleted.__setitem__("called_with", username))

    def _raise_refresh(request):
        raise Exception("invalid_grant: Token has been expired or revoked.")

    fake_credentials = SimpleNamespace(token="expired-token", expired=True, refresh_token="refresh-tok", refresh=_raise_refresh)
    monkeypatch.setattr(gco, "Credentials", lambda **kwargs: fake_credentials)

    with pytest.raises(gco.GoogleCalendarConnectionError, match="no longer valid"):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")

    assert deleted["called_with"] == "jack"


def _expired_doc_with_failing_refresh(monkeypatch, error):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = _stored_doc(fernet, datetime.datetime.utcnow() - datetime.timedelta(days=2))
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)
    deleted = []
    monkeypatch.setattr("backend.utils.google_calendar_utils.delete_connection", deleted.append)

    def _raise(self, request):
        raise error

    monkeypatch.setattr(gco.Credentials, "refresh", _raise)
    return deleted


def test_a_real_revoked_grant_deletes_the_connection(monkeypatch):
    from google.auth.exceptions import RefreshError

    error = RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
    deleted = _expired_doc_with_failing_refresh(monkeypatch, error)

    with pytest.raises(gco.GoogleCalendarConnectionError, match="no longer valid") as raised:
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")

    assert not isinstance(raised.value, gco.GoogleCalendarTemporaryError)
    assert deleted == ["jack"]


def test_a_revoked_grant_is_recognised_from_the_response_body_alone(monkeypatch):
    from google.auth.exceptions import RefreshError

    deleted = _expired_doc_with_failing_refresh(monkeypatch, RefreshError("Bad Request", {"error": "invalid_grant"}))

    with pytest.raises(gco.GoogleCalendarConnectionError):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")
    assert deleted == ["jack"]


@pytest.mark.parametrize("error", [
    ConnectionError("network unreachable"),
    TimeoutError("timed out"),
    Exception("503 Service Unavailable"),
])
def test_a_transient_refresh_failure_keeps_the_connection(monkeypatch, error):
    deleted = _expired_doc_with_failing_refresh(monkeypatch, error)

    with pytest.raises(gco.GoogleCalendarTemporaryError, match="still saved"):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")

    assert deleted == []  # the user is NOT forced to re-consent over a network blip


def test_a_misconfigured_client_secret_does_not_delete_the_users_connection(monkeypatch):
    from google.auth.exceptions import RefreshError

    deleted = _expired_doc_with_failing_refresh(
        monkeypatch, RefreshError("invalid_client: Unauthorized", {"error": "invalid_client"})
    )

    with pytest.raises(gco.GoogleCalendarTemporaryError):
        gco.GoogleCalendarOAuth().get_valid_access_token("jack")
    assert deleted == []


def test_temporary_error_is_still_a_connection_error_for_handlers_that_only_know_the_base():
    assert issubclass(gco.GoogleCalendarTemporaryError, gco.GoogleCalendarConnectionError)
def test_revoke_is_a_noop_without_an_existing_connection(monkeypatch):
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: None)
    mock_post = Mock()
    monkeypatch.setattr(gco.requests, "post", mock_post)

    gco.GoogleCalendarOAuth().revoke("jack")  # must not raise
    mock_post.assert_not_called()
