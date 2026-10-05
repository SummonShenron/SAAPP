"""Audit trail for GitHub token use.

An LLM agent decides which GitHub calls to make with a real user's token, which is a different risk
profile from a fixed code path using one (docs/coding-agent-roadmap.md, section 4: audit logging
should be required here even though the reference implementation skipped it). Every time a flow
resolves a token to act on a repo, this records who, which kind of token (the user's own or the
shared server one), for what purpose, on which repo, and when. It never records the token itself, and
never raises: auditing must not be able to break a request.
"""
import datetime
import logging
from typing import Optional

from backend.utils.db_utils import get_db

logger = logging.getLogger("SASS Logger")

AUDIT_COLLECTION = "github_token_audit"
AUDIT_RETENTION_SECONDS = 90 * 24 * 3600

_index_ready = False


def _ensure_retention_index(db) -> None:
    """Old entries expire on their own, so the trail can't grow without bound."""
    global _index_ready
    if _index_ready:
        return
    db[AUDIT_COLLECTION].create_index("at", expireAfterSeconds=AUDIT_RETENTION_SECONDS)
    _index_ready = True


def audit_github_token_use(username: Optional[str], source: str, purpose: str, repo: Optional[str] = None) -> None:
    """`source` is "user" (their own token) or "shared" (the server's GITHUB_TOKEN fallback)."""
    logger.info("[github-audit] user=%s source=%s purpose=%s repo=%s", username, source, purpose, repo)
    try:
        db = get_db()
        if db is None:
            return
        _ensure_retention_index(db)
        db[AUDIT_COLLECTION].insert_one({
            "username": username,
            "source": source,
            "purpose": purpose,
            "repo": repo,
            "at": datetime.datetime.now(datetime.timezone.utc),
        })
    except Exception:
        logger.warning("[github-audit] could not record the audit entry.", exc_info=True)
