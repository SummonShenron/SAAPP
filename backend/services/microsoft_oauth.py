"""Per-user Microsoft OAuth (authorization code + PKCE) for personal Microsoft accounts, talking to the Microsoft identity platform directly.

Plain `requests`, no msal: the flow is three HTTP calls and this keeps the dependency list as it is. Mirrors backend/services/google_calendar_oauth.py in
shape (authorization_url / build_connection / get_valid_access_token / revoke) and stays DB-free at import time, so it can be unit tested with the
HTTP layer mocked. Callers persist whatever it returns.

The "consumers" authority is deliberate: personal accounts only, which never need an admin to approve the app.
"""
import base64
import datetime
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet

logger = logging.getLogger("SASS Logger")

_AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
_AUTHORIZE_URL = f"{_AUTHORITY}/authorize"
_TOKEN_URL = f"{_AUTHORITY}/token"
_ME_URL = "https://graph.microsoft.com/v1.0/me"

# offline_access is what yields a refresh token. Mail.Send and Calendars.ReadWrite are write scopes, but every write in this app
# still goes through an approval card the person confirms.
MICROSOFT_SCOPES = [
    "offline_access",
    "openid",
    "email",
    "User.Read",
    "Mail.Read",
    "Mail.Send",
    "Calendars.ReadWrite",
    "Files.Read",
]

_REFRESH_SKEW_SECONDS = 120


class MicrosoftConnectionError(Exception):
    """No usable Microsoft connection: none saved, or the grant was revoked. Callers turn this into a plain "connect Microsoft first" message."""


class MicrosoftTemporaryError(MicrosoftConnectionError):
    """A refresh failed for a reason that says nothing about the grant (network, Microsoft outage). The saved connection is intact."""


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value) -> datetime.datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class MicrosoftOAuth:
    def _config(self) -> tuple[str, str, str]:
        client_id = os.getenv("MICROSOFT_CLIENT_ID")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        redirect_uri = os.getenv("MICROSOFT_REDIRECT_URI")
        if not client_id or not client_secret or not redirect_uri:
            raise ValueError("Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, and MICROSOFT_REDIRECT_URI.")
        return client_id, client_secret, redirect_uri

    def _fernet(self) -> Fernet:
        key = os.getenv("TOKEN_ENCRYPTION_KEY")
        if not key:
            raise ValueError("Token storage is not configured. Set TOKEN_ENCRYPTION_KEY.")
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError) as error:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from error

    def authorization_url(self) -> tuple[str, str, str]:
        """(url, state, code_verifier). The verifier is kept server-side with the pending record, never sent to the browser."""
        client_id, _, redirect_uri = self._config()
        state = secrets.token_urlsafe(32)
        verifier, challenge = _pkce_pair()
        query = urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(MICROSOFT_SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Forces the consent screen so a reconnect after a disconnect re-issues a refresh token.
            "prompt": "consent",
        })
        return f"{_AUTHORIZE_URL}?{query}", state, verifier

    def build_connection(self, code: str, code_verifier: str) -> dict:
        """Exchanges the authorization code and returns a dict shaped for microsoft_utils.save_connection(username, **result)."""
        client_id, client_secret, redirect_uri = self._config()
        response = requests.post(_TOKEN_URL, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }, timeout=15)
        if response.status_code != 200:
            raise ValueError(f"Microsoft rejected the sign-in ({response.status_code}).")
        token = response.json()
        access_token = token.get("access_token")
        if not access_token:
            raise ValueError("Microsoft did not return an access token.")
        granted = [s for s in str(token.get("scope") or "").split() if s]
        if not granted:
            raise ValueError("Microsoft did not grant any permissions. Reconnect and approve the requested permissions.")

        account_email = None
        try:
            me = requests.get(_ME_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
            if me.status_code == 200:
                body = me.json()
                account_email = body.get("mail") or body.get("userPrincipalName")
        except requests.RequestException:
            logger.warning("[microsoft_oauth] Failed to fetch the account email for a new connection.")

        fernet = self._fernet()
        refresh_token = token.get("refresh_token")
        return {
            "account_email": account_email,
            "scopes": granted,
            "expires_at": _utcnow() + datetime.timedelta(seconds=int(token.get("expires_in") or 3600)),
            "access_token_encrypted": fernet.encrypt(access_token.encode()).decode(),
            "refresh_token_encrypted": fernet.encrypt(refresh_token.encode()).decode() if refresh_token else None,
        }

    def get_valid_access_token(self, username: str) -> str:
        """Decrypts the stored tokens and refreshes (and re-saves) them when near expiry. A refresh Microsoft answers with invalid_grant
        means the person revoked access, so the stale connection is deleted; any other failure leaves it in place."""
        from backend.utils import microsoft_utils  # local import keeps this module DB-free at import time

        doc = microsoft_utils.get_encrypted_tokens(username)
        if not doc:
            raise MicrosoftConnectionError(f"No Microsoft connection found for {username}")

        fernet = self._fernet()
        access_token = fernet.decrypt(doc["access_token_encrypted"].encode()).decode()
        expiry = _as_naive_utc(doc.get("expires_at"))
        if expiry is not None and expiry - _utcnow() > datetime.timedelta(seconds=_REFRESH_SKEW_SECONDS):
            return access_token

        refresh_encrypted = doc.get("refresh_token_encrypted")
        if not refresh_encrypted:
            microsoft_utils.delete_connection(username)
            raise MicrosoftConnectionError(f"The Microsoft connection for {username} expired; reconnect required")
        refresh_token = fernet.decrypt(refresh_encrypted.encode()).decode()

        client_id, client_secret, _ = self._config()
        try:
            response = requests.post(_TOKEN_URL, data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(MICROSOFT_SCOPES),
            }, timeout=15)
        except requests.RequestException as error:
            logger.warning("[microsoft_oauth] Token refresh failed for %s without a revoked grant; keeping the connection: %r", username, error)
            raise MicrosoftTemporaryError("Microsoft couldn't be reached to refresh the connection just now. It is still saved, so try again in a moment.") from error

        if response.status_code != 200:
            try:
                error_code = response.json().get("error")
            except ValueError:
                error_code = None
            if error_code == "invalid_grant":
                microsoft_utils.delete_connection(username)
                raise MicrosoftConnectionError(f"The Microsoft connection for {username} is no longer valid; reconnect required")
            logger.warning("[microsoft_oauth] Token refresh got %s for %s; keeping the connection.", response.status_code, username)
            raise MicrosoftTemporaryError("Microsoft couldn't refresh the connection just now. It is still saved, so try again in a moment.")

        token = response.json()
        new_access = token.get("access_token")
        if not new_access:
            raise MicrosoftTemporaryError("Microsoft didn't return a new access token. Try again in a moment.")
        rotated = token.get("refresh_token")
        microsoft_utils.update_tokens(
            username,
            access_token_encrypted=fernet.encrypt(new_access.encode()).decode(),
            expires_at=_utcnow() + datetime.timedelta(seconds=int(token.get("expires_in") or 3600)),
            refresh_token_encrypted=fernet.encrypt(rotated.encode()).decode() if rotated and rotated != refresh_token else None,
        )
        return new_access

    def revoke(self, username: str) -> None:
        """Microsoft has no token-revocation endpoint for personal accounts: disconnecting deletes our copy, and the person can remove the app's
        access at account.live.com/consent/Manage. Kept so the endpoint reads like the Google one."""
        return None
