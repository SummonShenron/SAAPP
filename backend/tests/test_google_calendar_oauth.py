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


def test_get_valid_access_token_returns_token_without_refresh_when_not_expired(monkeypatch):
    fernet = Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    doc = {
        "access_token_encrypted": fernet.encrypt(b"still-valid-token").decode(),
        "refresh_token_encrypted": fernet.encrypt(b"refresh-tok").decode(),
        "scopes": ["calendar.events"],
    }
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: doc)

    fake_credentials = SimpleNamespace(token="still-valid-token", expired=False, refresh_token="refresh-tok")
    monkeypatch.setattr(gco, "Credentials", lambda **kwargs: fake_credentials)

    token = gco.GoogleCalendarOAuth().get_valid_access_token("jack")
    assert token == "still-valid-token"


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


def test_revoke_is_a_noop_without_an_existing_connection(monkeypatch):
    monkeypatch.setattr("backend.utils.google_calendar_utils.get_encrypted_tokens", lambda username: None)
    mock_post = Mock()
    monkeypatch.setattr(gco.requests, "post", mock_post)

    gco.GoogleCalendarOAuth().revoke("jack")  # must not raise
    mock_post.assert_not_called()
