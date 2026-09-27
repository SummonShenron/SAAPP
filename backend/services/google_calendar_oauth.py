"""Per-user Google Calendar OAuth — a real "web application" flow with PKCE, replacing PAAPP's
global single-account desktop-app flow (backend/tools/calendar_tool.py in the separate PAAPP repo,
InstalledAppFlow.run_local_server). Mirrors the reference implementation already proven in the
sibling `workflow_builder` repo's backend/app/services/google_oauth.py, adapted to this codebase's
own conventions: fully synchronous (get_db() is plain pymongo, not motor), env vars read directly
via os.getenv() (no pydantic Settings object here), and plain dicts instead of pydantic models
(backend/utils/*_utils.py works in plain dicts throughout).

Deliberately DB-free — this module never imports backend.utils.google_calendar_utils itself, so it
stays unit-testable in isolation (mock Flow/Credentials/requests, no Mongo needed). Callers pass in
whatever persisted state they already fetched and are responsible for persisting whatever this
module returns.
"""
import logging
import os
import secrets

import requests
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger("SASS Logger")

# calendar.events (not PAAPP's full-access 'calendar' scope) is enough for create/list/update.
# openid + userinfo.email exist solely so build_connection() can show which real Google account is
# connected — bare calendar.events alone doesn't return an email.
GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class GoogleCalendarConnectionError(Exception):
    """Raised when a user has no usable Google Calendar connection — no connection at all, or a
    refresh that failed because the grant was revoked. Callers (tool_agent_node's read action,
    the create/update_calendar_event write actions) catch this and surface a plain, actionable
    "connect your calendar first" message rather than a raw stack trace."""


class GoogleCalendarOAuth:
    def _flow(self, state: str | None = None, code_verifier: str | None = None) -> Flow:
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        if not client_id or not client_secret or not redirect_uri:
            raise ValueError(
                "Google Calendar OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI."
            )
        return Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=GOOGLE_CALENDAR_SCOPES,
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=code_verifier is None,
        )

    def authorization_url(self) -> tuple[str, str, str]:
        state = secrets.token_urlsafe(32)
        flow = self._flow(state)
        # prompt="consent" (not just access_type="offline") so a *re*-connect after a prior
        # disconnect reliably re-issues a refresh token — Google only returns one on first
        # consent, or when consent is forced again like this.
        url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="false")
        if not flow.code_verifier:
            raise ValueError("Google OAuth did not generate a PKCE verifier.")
        return url, state, flow.code_verifier

    def build_connection(self, authorization_response: str, state: str, code_verifier: str) -> dict:
        """Exchanges the authorization code, fetches the connected account's email, and returns a
        plain dict shaped for google_calendar_utils.save_connection(username, **result)."""
        flow = self._flow(state, code_verifier)
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials

        granted_scopes = list(credentials.scopes or [])
        if not granted_scopes:
            raise ValueError("Google did not grant any Calendar scopes. Reconnect and approve the requested permissions.")

        account_email = None
        try:
            resp = requests.get(_USERINFO_URL, headers={"Authorization": f"Bearer {credentials.token}"}, timeout=10)
            if resp.status_code == 200:
                account_email = resp.json().get("email")
        except requests.RequestException:
            logger.warning("[google_calendar_oauth] Failed to fetch account email for new connection.")

        fernet = self._fernet()
        return {
            "account_email": account_email,
            "scopes": granted_scopes,
            "expires_at": credentials.expiry,
            "access_token_encrypted": fernet.encrypt(credentials.token.encode()).decode(),
            "refresh_token_encrypted": (
                fernet.encrypt(credentials.refresh_token.encode()).decode() if credentials.refresh_token else None
            ),
        }

    def get_valid_access_token(self, username: str) -> str:
        """Decrypts the user's stored tokens, refreshing (and re-persisting) if expired. Self-heals
        on a revoked grant: if Google's refresh fails because the user revoked access directly from
        their Google account settings (bypassing SAAPP's own disconnect button), the stale
        connection is deleted so /api/calendar/status never reports a permanently-wrong
        "connected: true"."""
        from backend.utils import google_calendar_utils  # local import — keeps this module DB-free at import time

        doc = google_calendar_utils.get_encrypted_tokens(username)
        if not doc:
            raise GoogleCalendarConnectionError(f"No Google Calendar connection found for {username}")

        fernet = self._fernet()
        refresh_token_encrypted = doc.get("refresh_token_encrypted")
        original_refresh_token = fernet.decrypt(refresh_token_encrypted.encode()).decode() if refresh_token_encrypted else None
        credentials = Credentials(
            token=fernet.decrypt(doc["access_token_encrypted"].encode()).decode(),
            refresh_token=original_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=doc.get("scopes"),
        )

        if credentials.expired:
            if not credentials.refresh_token:
                google_calendar_utils.delete_connection(username)
                raise GoogleCalendarConnectionError(f"Google Calendar connection expired for {username}; reconnect required")
            try:
                credentials.refresh(Request())
            except Exception as error:
                # A revoked grant surfaces here as a token-refresh failure (Google returns
                # invalid_grant) — treat any refresh failure as "connection is dead" rather than
                # trying to distinguish exact error bodies, and self-heal by deleting the stale
                # record so it stops reporting connected: true.
                google_calendar_utils.delete_connection(username)
                raise GoogleCalendarConnectionError(f"Google Calendar connection for {username} is no longer valid: {error}") from error

            # Google only sends a new refresh_token back on rotation, which is rare for this grant
            # type — only re-encrypt and overwrite it if it actually changed from what we started
            # with, per update_tokens' "None means leave it alone" contract.
            rotated_refresh_token = (
                credentials.refresh_token
                if credentials.refresh_token and credentials.refresh_token != original_refresh_token
                else None
            )
            google_calendar_utils.update_tokens(
                username,
                access_token_encrypted=fernet.encrypt(credentials.token.encode()).decode(),
                expires_at=credentials.expiry,
                refresh_token_encrypted=(
                    fernet.encrypt(rotated_refresh_token.encode()).decode() if rotated_refresh_token else None
                ),
            )

        return credentials.token

    def revoke(self, username: str) -> None:
        """Best-effort — Google's revoke endpoint returns 200 on success and 400 if the token was
        already invalid/revoked, both of which mean "the account is no longer connected" from our
        side, so both are treated as success."""
        from backend.utils import google_calendar_utils

        doc = google_calendar_utils.get_encrypted_tokens(username)
        if not doc:
            return
        fernet = self._fernet()
        token = fernet.decrypt(doc["access_token_encrypted"].encode()).decode()
        try:
            response = requests.post(_REVOKE_URL, params={"token": token}, timeout=10)
            if response.status_code not in (200, 400):
                logger.warning("[google_calendar_oauth] Unexpected revoke response for %s: %s", username, response.status_code)
        except requests.RequestException:
            logger.warning("[google_calendar_oauth] Revoke request failed for %s — proceeding to delete the local record anyway.", username)

    def _fernet(self) -> Fernet:
        key = os.getenv("TOKEN_ENCRYPTION_KEY")
        if not key:
            raise ValueError("Token storage is not configured. Set TOKEN_ENCRYPTION_KEY.")
        try:
            return Fernet(key.encode())
        except (ValueError, TypeError) as error:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key.") from error
