import os
import re
import json
import logging
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "saapp_data", "settings")
os.makedirs(DATA_DIR, exist_ok=True)

RAG_MODE_STRICT = "strict"
RAG_MODE_OPEN = "open"
VALID_RAG_MODES = {RAG_MODE_STRICT, RAG_MODE_OPEN}

DEFAULT_DEEP_THINKING = False

# PAAPP's legacy calendar tool hardcoded every event to 'America/Chicago' for a single operator —
# now that calendar actions are per-user (see backend/services/google_calendar_oauth.py), this is
# only ever used as the fallback before a user has explicitly set their own.
DEFAULT_TIMEZONE = "America/Chicago"

_TARGET_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# Google Doc IDs are opaque alphanumeric-ish strings, typically 40+ chars, but no fixed format is
# documented — this charset/length check just filters out obviously-not-an-ID garbage input.
_TARGET_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_TARGET_DOC_URL_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")

# Identities that must never opt into a heavier-than-default setting (open RAG scope, deep
# thinking's larger step budget, a pinned target repo) — guest_bty is the single shared
# identity every anonymous BTY Fitness embed visitor authenticates as, so honoring a stored
# override for it would leak across every visitor of that widget, and for deep thinking would
# also hand every anonymous visitor a much more expensive request by default.
TOGGLE_LOCKED_USERS = {"guest_bty"}


def _get_user_file(username: str) -> str:
    return os.path.join(DATA_DIR, f"{username}.json")


def _read_local_settings(username: str) -> dict:
    path = _get_user_file(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        logger.exception("Failed to load local settings file for %s", username)
        return {}


def _write_local_setting(username: str, key: str, value) -> None:
    """Merges a single key into the user's local settings file instead of overwriting it —
    each setting (rag_mode, deep_thinking, ...) is saved independently, so setting one must
    never erase another that was already saved."""
    data = _read_local_settings(username)
    data[key] = value
    with open(_get_user_file(username), "w") as f:
        json.dump(data, f, indent=2)


def _fetch_settings_doc(username: str) -> dict:
    """One read of the user's whole settings document (Mongo) or file (local fallback) — every
    getter below reads from this instead of issuing its own query, since rag_mode,
    deep_thinking, and target_repo all live on the exact same per-user record."""
    db = get_db()
    if db is not None:
        # The encrypted GitHub token is excluded here so it is never loaded by (or leaked through)
        # the ordinary settings reads on the hot chat path; _fetch_encrypted_github_token is the one
        # place that asks for it.
        return db["user_settings"].find_one({"username": username}, {"github_token_encrypted": 0}) or {}
    return _read_local_settings(username)


def _resolve_rag_mode(doc: dict) -> str:
    mode = doc.get("rag_mode")
    return mode if mode in VALID_RAG_MODES else RAG_MODE_STRICT


def _resolve_deep_thinking(doc: dict) -> bool:
    enabled = doc.get("deep_thinking")
    return enabled if isinstance(enabled, bool) else DEFAULT_DEEP_THINKING


def _resolve_target_repo(doc: dict) -> Optional[str]:
    repo = doc.get("target_repo")
    return repo if repo and _TARGET_REPO_RE.match(repo) else None


def _extract_doc_id(raw: str) -> Optional[str]:
    """Accepts either a bare Doc ID or a full docs.google.com URL — a user pasting the URL
    straight from their browser's address bar should just work, not require them to dig the ID
    out of it themselves."""
    url_match = _TARGET_DOC_URL_RE.search(raw)
    if url_match:
        return url_match.group(1)
    return raw if _TARGET_DOC_ID_RE.match(raw) else None


def _resolve_target_doc_id(doc: dict) -> Optional[str]:
    doc_id = doc.get("target_doc_id")
    return doc_id if doc_id and _TARGET_DOC_ID_RE.match(doc_id) else None


def _is_valid_timezone(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def _resolve_timezone(doc: dict) -> str:
    tz = doc.get("timezone")
    return tz if tz and _is_valid_timezone(tz) else DEFAULT_TIMEZONE


def get_user_rag_mode(username: str) -> str:
    """Returns the user's stored RAG mode, defaulting to (and locking) 'strict'."""
    if username in TOGGLE_LOCKED_USERS:
        return RAG_MODE_STRICT
    return _resolve_rag_mode(_fetch_settings_doc(username))


def set_user_rag_mode(username: str, mode: str) -> str:
    """Validates and persists a user's RAG mode. Locked identities are always forced to strict."""
    if mode not in VALID_RAG_MODES:
        mode = RAG_MODE_STRICT
    if username in TOGGLE_LOCKED_USERS:
        mode = RAG_MODE_STRICT

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"rag_mode": mode}},
            upsert=True,
        )

    _write_local_setting(username, "rag_mode", mode)
    return mode


def get_user_deep_thinking_mode(username: str) -> bool:
    """Returns whether the user has deep thinking enabled, defaulting to (and locking) off."""
    if username in TOGGLE_LOCKED_USERS:
        return DEFAULT_DEEP_THINKING
    return _resolve_deep_thinking(_fetch_settings_doc(username))


def set_user_deep_thinking_mode(username: str, enabled: bool) -> bool:
    """Validates and persists a user's deep thinking setting. Locked identities are always forced off."""
    enabled = bool(enabled)
    if username in TOGGLE_LOCKED_USERS:
        enabled = DEFAULT_DEEP_THINKING

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"deep_thinking": enabled}},
            upsert=True,
        )

    _write_local_setting(username, "deep_thinking", enabled)
    return enabled


def get_user_target_repo(username: str) -> Optional[str]:
    """Returns the user's pinned "owner/repo", or None to keep the existing per-message
    auto-detection (resolve_recent_mention) as the fallback. Locked identities never get a
    pinned repo of their own."""
    if username in TOGGLE_LOCKED_USERS:
        return None
    return _resolve_target_repo(_fetch_settings_doc(username))


def get_user_settings_bundle(username: str) -> dict:
    """Fetches rag_mode, deep_thinking, and target_repo in a single DB round trip — for the hot
    chat-request path, which used to call the three getters above separately (3 network round
    trips per message for 3 fields on the exact same document) instead of once."""
    if username in TOGGLE_LOCKED_USERS:
        return {"rag_mode": RAG_MODE_STRICT, "deep_thinking": DEFAULT_DEEP_THINKING, "target_repo": None}
    doc = _fetch_settings_doc(username)
    return {
        "rag_mode": _resolve_rag_mode(doc),
        "deep_thinking": _resolve_deep_thinking(doc),
        "target_repo": _resolve_target_repo(doc),
    }


def set_user_target_repo(username: str, repo: Optional[str]) -> Optional[str]:
    """Validates and persists a user's pinned target repo. A falsy or malformed value clears
    the pin (back to per-message auto-detection) rather than being silently ignored — an
    invalid save should read as "not set", not as "still whatever it was before"."""
    repo = (repo or "").strip()
    repo = repo if _TARGET_REPO_RE.match(repo) else None
    if username in TOGGLE_LOCKED_USERS:
        repo = None

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"target_repo": repo}},
            upsert=True,
        )

    _write_local_setting(username, "target_repo", repo)
    return repo


def get_user_target_doc_id(username: str) -> Optional[str]:
    """Returns the user's configured target Google Doc ID — a generic "the document SAAPP
    appends to when asked" setting, not tied to any one purpose (a weekly report is just one
    user's own first use case for it). Not subject to TOGGLE_LOCKED_USERS — same reasoning as
    timezone, no elevated-resource concern."""
    return _resolve_target_doc_id(_fetch_settings_doc(username))


def set_user_target_doc_id(username: str, raw_url_or_id: Optional[str]) -> Optional[str]:
    """Validates and persists a user's target document. Accepts either a bare Doc ID or a full
    docs.google.com URL. A falsy or malformed value clears the setting, matching
    set_user_target_repo's invalid-value-clears convention."""
    doc_id = _extract_doc_id((raw_url_or_id or "").strip())

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"target_doc_id": doc_id}},
            upsert=True,
        )

    _write_local_setting(username, "target_doc_id", doc_id)
    return doc_id


def get_user_timezone(username: str) -> str:
    """Returns the user's stored IANA timezone (e.g. 'America/New_York'), defaulting to
    DEFAULT_TIMEZONE if never set. Not subject to TOGGLE_LOCKED_USERS — a timezone preference
    carries no elevated-resource/scope concern the way rag_mode/deep_thinking/target_repo do."""
    return _resolve_timezone(_fetch_settings_doc(username))


def set_user_timezone(username: str, tz: str) -> str:
    """Validates and persists a user's IANA timezone. An invalid or unrecognized value resets to
    DEFAULT_TIMEZONE rather than being silently ignored, matching set_user_target_repo's
    invalid-value-clears-the-setting convention."""
    tz = (tz or "").strip()
    tz = tz if _is_valid_timezone(tz) else DEFAULT_TIMEZONE

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"timezone": tz}},
            upsert=True,
        )

    _write_local_setting(username, "timezone", tz)
    return tz


def get_user_has_seen_help(username: str) -> bool:
    """Whether this user has already dismissed the onboarding/help overlay — a layout-level
    concern, not something the chat request path needs, so unlike rag_mode/deep_thinking/
    target_repo it deliberately isn't part of get_user_settings_bundle. Not subject to
    TOGGLE_LOCKED_USERS: seeing the help panel repeatedly carries no cost/scope concern the way
    an elevated resource setting would."""
    doc = _fetch_settings_doc(username)
    seen = doc.get("has_seen_help")
    return seen if isinstance(seen, bool) else False


def set_user_has_seen_help(username: str, seen: bool) -> bool:
    """Persists that this user has dismissed the onboarding/help overlay, so it doesn't
    auto-show again on their next sign-in (on any device)."""
    seen = bool(seen)

    db = get_db()
    if db is not None:
        db["user_settings"].update_one(
            {"username": username},
            {"$set": {"has_seen_help": seen}},
            upsert=True,
        )

    _write_local_setting(username, "has_seen_help", seen)
    return seen


# ---------------------------------------------------------------------------------------------------
# Per-user GitHub token. Until now the GitHub token was a single global env var (GITHUB_TOKEN) that
# every user's requests shared. A user can now supply their own personal access token so Sonic reads,
# summarizes and opens PRs/issues on THEIR repos as them. The token is encrypted at rest (the same
# TOKEN_ENCRYPTION_KEY the Google tokens use), never written to the local settings file in plaintext,
# and never sent back to the browser: the API only reports whether one is set and its last four
# characters. The env var remains a fallback for users who haven't supplied one.
# ---------------------------------------------------------------------------------------------------

# Shared identities (everyone who uses the guest sandbox or the BTY embed is the SAME user): a token
# saved against one of them would be used by every visitor, so none may be stored.
GITHUB_TOKEN_LOCKED_USERS = TOGGLE_LOCKED_USERS | {"guest", "guest-recruiter@example.com"}

# GitHub tokens are ASCII with no whitespace (ghp_..., github_pat_..., gho_..., legacy 40-hex). This
# only rejects obviously-not-a-token input; whether GitHub accepts it is checked when it is saved.
_GITHUB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{20,255}$")


class GitHubTokenInvalid(ValueError):
    """The supplied value is not shaped like a GitHub token."""


class GitHubTokenNotAllowed(PermissionError):
    """This identity is shared between visitors, so it can't hold a personal token."""


def _fetch_encrypted_github_token(username: str) -> Optional[str]:
    """The only read that returns the encrypted token. The filter is on `username` inside the query
    itself (never fetch a document and then check whose it is), and `username` is always an explicit
    argument, never ambient state."""
    db = get_db()
    if db is not None:
        doc = db["user_settings"].find_one({"username": username}, {"github_token_encrypted": 1, "_id": 0}) or {}
        return doc.get("github_token_encrypted")
    return _read_local_settings(username).get("github_token_encrypted")


def is_plausible_github_token(token: Optional[str]) -> bool:
    return bool(token) and bool(_GITHUB_TOKEN_RE.match(token.strip()))


def get_github_token_status(username: str) -> dict:
    """What the browser is allowed to know: whether a token is set, and its last four characters."""
    doc = _fetch_settings_doc(username)
    configured = bool(doc.get("github_token_last4"))
    return {
        "configured": configured,
        "last4": doc.get("github_token_last4") if configured else None,
        "github_login": doc.get("github_login") if configured else None,
    }


def get_user_github_token(username: str) -> Optional[str]:
    """The user's own decrypted GitHub token, or None if they haven't set one (or it can't be read)."""
    if username in GITHUB_TOKEN_LOCKED_USERS:
        return None
    from backend.utils.secret_utils import decrypt_secret, SecretStorageNotConfigured

    encrypted = _fetch_encrypted_github_token(username)
    if not encrypted:
        return None
    try:
        token = decrypt_secret(encrypted)
    except SecretStorageNotConfigured:
        logger.warning("A GitHub token is stored for %s but TOKEN_ENCRYPTION_KEY is not usable.", username)
        return None
    if token is None:
        logger.warning("The stored GitHub token for %s could not be decrypted (key changed?).", username)
    return token


def resolve_github_token(username: Optional[str], purpose: str = "unspecified", repo: Optional[str] = None) -> Optional[str]:
    """The token to use for GitHub calls made on behalf of `username`: their own if they set one,
    otherwise the shared GITHUB_TOKEN env var (how it worked before per-user tokens existed). Every
    resolution is audited (who, which kind of token, why, which repo); the token itself never is."""
    from backend.utils.github_audit import audit_github_token_use

    if username:
        own = get_user_github_token(username)
        if own:
            audit_github_token_use(username, "user", purpose, repo)
            return own
    shared = os.getenv("GITHUB_TOKEN") or None
    if shared:
        audit_github_token_use(username, "shared", purpose, repo)
    return shared


def set_user_github_token(username: str, token: Optional[str], github_login: Optional[str] = None) -> dict:
    """Stores (or, with an empty value, clears) the user's own GitHub token, encrypted. Returns the
    same safe status the API exposes. Raises GitHubTokenNotAllowed for shared identities,
    GitHubTokenInvalid for something that isn't shaped like a token, and
    secret_utils.SecretStorageNotConfigured if there is no encryption key to protect it with."""
    if username in GITHUB_TOKEN_LOCKED_USERS:
        raise GitHubTokenNotAllowed("Shared guest identities can't store a personal GitHub token.")

    token = (token or "").strip()
    if token:
        if not _GITHUB_TOKEN_RE.match(token):
            raise GitHubTokenInvalid("That doesn't look like a GitHub token.")
        from backend.utils.secret_utils import encrypt_secret

        fields = {
            "github_token_encrypted": encrypt_secret(token),
            "github_token_last4": token[-4:],
            "github_login": github_login,
        }
    else:
        fields = {"github_token_encrypted": None, "github_token_last4": None, "github_login": None}

    db = get_db()
    if db is not None:
        db["user_settings"].update_one({"username": username}, {"$set": fields}, upsert=True)
    # Only the encrypted form (never the token itself) ever reaches the local settings file.
    for key, value in fields.items():
        _write_local_setting(username, key, value)
    return get_github_token_status(username)


def find_github_token_for_repo(repo: str) -> tuple:
    """For a webhook that names only a repository: (username, token) of a user whose own token should
    act on it, or (None, None). Two kinds of user qualify: one who pinned that repo as their target,
    or one whose GitHub login OWNS the repo. The second matters because a user typically adds a token
    once and then wires webhooks on every repo they own, without pinning each of them; matching only on
    the pin meant the one pinned repo (SAAPP) worked and every other repo of theirs silently got no
    token once the shared env token was gone. The token of an account that owns the repo is the one
    that can read and comment on it, so no user's token is ever used on someone else's repo.
    Preference: owner and pinned, then owner, then pinned only. The shared env token is NOT a fallback
    here, so callers can tell "a user's token" from "the global one"."""
    db = get_db()
    if db is None or not repo:
        return None, None
    from backend.utils.secret_utils import decrypt_secret, SecretStorageNotConfigured

    owner = repo.split("/", 1)[0].lower()
    candidates = list(db["user_settings"].find({
        "$or": [
            {"target_repo": {"$regex": f"^{re.escape(repo)}$", "$options": "i"}},
            {"github_login": {"$regex": f"^{re.escape(owner)}$", "$options": "i"}},
        ],
        "github_token_encrypted": {"$exists": True, "$ne": None},
    }))
    candidates = [c for c in candidates if c.get("username") not in GITHUB_TOKEN_LOCKED_USERS]

    def _rank(candidate):
        is_owner = str(candidate.get("github_login") or "").lower() == owner
        is_pinned = str(candidate.get("target_repo") or "").lower() == repo.lower()
        return (not is_owner, not is_pinned, str(candidate.get("username")))

    candidates.sort(key=_rank)
    for candidate in candidates:
        try:
            token = decrypt_secret(candidate["github_token_encrypted"])
        except SecretStorageNotConfigured:
            return None, None
        if token:
            return candidate.get("username"), token
    return None, None

