import os
import requests
from typing import Optional
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from backend.models.models import lite_llm
from backend.utils.pr_context import build_review_prompt

logger = logging.getLogger("SASS Logger")

_GITHUB_API_TIMEOUT_SECONDS = 15

# Helper with automatic exponential retry
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=lambda retry_state: logger.warning(
        f"LLM call failed. Retrying in {retry_state.next_action.sleep} seconds (Attempt {retry_state.attempt_number}/3)..."
    ),
    reraise=True
)
def _call_llm_with_retry(prompt: str):
    return lite_llm.invoke(prompt)


def validate_github_token(token: str) -> dict:
    """Asks GitHub who a token belongs to, before it is ever stored. {"valid": True, "login": ...} for a
    working token, {"valid": False} when GitHub rejects it (401), and {"valid": None} when GitHub can't
    give a clear answer (unreachable, rate limited), so callers can tell "bad token" from "try again"."""
    try:
        response = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=_GITHUB_API_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning("Could not reach GitHub to validate a token.")
        return {"valid": None, "login": None}
    if response.status_code == 200:
        return {"valid": True, "login": response.json().get("login")}
    if response.status_code == 401:
        return {"valid": False, "login": None}
    return {"valid": None, "login": None}


class GitHubTokenRejected(ValueError):
    """GitHub says this token is not valid (wrong, expired or revoked)."""


class GitHubUnreachable(RuntimeError):
    """GitHub couldn't give a clear answer, so the token was NOT stored; the caller can retry."""


def verify_and_store_github_token(username: str, token) -> dict:
    """The one way a user's own GitHub token gets saved: refuse shared identities, refuse anything
    not shaped like a token, ask GitHub whether it is real (docs/coding-agent-roadmap.md section 4:
    validated once before it is ever persisted), and only then store it encrypted. An empty token
    removes the stored one. Returns the safe status the API exposes (never the token)."""
    from backend.utils.user_settings_utils import (
        GITHUB_TOKEN_LOCKED_USERS, GitHubTokenInvalid, GitHubTokenNotAllowed,
        is_plausible_github_token, set_user_github_token,
    )

    token = (token or "").strip()
    if username in GITHUB_TOKEN_LOCKED_USERS:
        # Checked before anything is sent anywhere: a shared identity must never even reach GitHub.
        raise GitHubTokenNotAllowed("Shared guest identities can't store a personal GitHub token.")
    if not token:
        return set_user_github_token(username, None)
    if not is_plausible_github_token(token):
        raise GitHubTokenInvalid("That doesn't look like a GitHub token.")

    check = validate_github_token(token)
    if check["valid"] is False:
        raise GitHubTokenRejected("GitHub rejected this token. Check that it's correct and hasn't expired or been revoked.")
    if check["valid"] is None:
        raise GitHubUnreachable("Couldn't reach GitHub to verify this token right now. Try again in a moment.")
    return set_user_github_token(username, token, github_login=check["login"])


# Marks the overview comment as ours, so a later push updates it in place instead of stacking a new
# comment for every commit.
OVERVIEW_MARKER = "<!-- sonic-assistant-pr-overview -->"
_API_BASE = "https://api.github.com"
_PAGE_SIZE = 100
_MAX_FILE_PAGES = 3  # GitHub caps a PR's file list at 3000 entries; 300 is plenty to describe a PR


def _github_get(url: str, headers: dict, params: Optional[dict] = None):
    return requests.get(url, headers=headers, params=params, timeout=_GITHUB_API_TIMEOUT_SECONDS)


def fetch_pr_evidence(repo: str, pr_number: int, headers: dict, api_base: str = _API_BASE) -> dict:
    """Everything a PR description or review is written from: {"pr", "commits", "files", "error"}.
    The file list is required (error is set, and files is None, when it can't be read); the PR's own
    title/description and its commits are best effort, because a review is still worth writing without
    them, just with less to ground the "why" in."""
    files, error = [], None
    for page in range(1, _MAX_FILE_PAGES + 1):
        res = _github_get(f"{api_base}/repos/{repo}/pulls/{pr_number}/files", headers, {"per_page": _PAGE_SIZE, "page": page})
        if res.status_code != 200:
            if page == 1:
                error = f"HTTP {res.status_code}: {res.text}"
            break
        batch = res.json()
        if not isinstance(batch, list):
            break
        files.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
    if error:
        return {"pr": None, "commits": [], "files": None, "error": error}

    pr, commits = None, []
    try:
        pr_res = _github_get(f"{api_base}/repos/{repo}/pulls/{pr_number}", headers)
        if pr_res.status_code == 200 and isinstance(pr_res.json(), dict):
            pr = pr_res.json()
        commits_res = _github_get(f"{api_base}/repos/{repo}/pulls/{pr_number}/commits", headers, {"per_page": _PAGE_SIZE})
        if commits_res.status_code == 200 and isinstance(commits_res.json(), list):
            commits = commits_res.json()
    except requests.RequestException:
        logger.warning("Could not fetch PR details/commits for %s #%s; reviewing from the files alone.", repo, pr_number)
    return {"pr": pr, "commits": commits, "files": files, "error": None}


def _llm_text(response) -> str:
    raw_content = getattr(response, "content", response)
    if isinstance(raw_content, list):
        blocks = []
        for block in raw_content:
            if isinstance(block, str):
                blocks.append(block)
            elif isinstance(block, dict) and "text" in block:
                blocks.append(block["text"])
        return "\n".join(blocks).strip()
    return str(raw_content).strip()


def _find_overview_comment(repo: str, pr_number: int, headers: dict, api_base: str = _API_BASE):
    """The overview comment we posted earlier on this PR, if any."""
    for page in range(1, 4):
        res = _github_get(f"{api_base}/repos/{repo}/issues/{pr_number}/comments", headers, {"per_page": _PAGE_SIZE, "page": page})
        if res.status_code != 200:
            return None
        batch = res.json()
        if not isinstance(batch, list):
            return None
        for comment in batch:
            if isinstance(comment, dict) and OVERVIEW_MARKER in (comment.get("body") or ""):
                return comment
        if len(batch) < _PAGE_SIZE:
            break
    return None


def process_pr_summary(repo: str, pr_number: int, token: Optional[str] = None):
    """Reads a PR (its description, commits and diff), writes an overview, and posts it to the target
    repo's PR, updating its own earlier comment instead of adding a new one on every push."""
    logger.info(f"--- PROCESSING PR SUMMARY FOR {repo} #{pr_number} ---")

    token = token or os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN is not provided and the environment variable is not set!")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    api_base = _API_BASE

    # 1. Gather the evidence
    logger.info(f"Requesting PR files from GitHub: {api_base}/repos/{repo}/pulls/{pr_number}/files")
    evidence = fetch_pr_evidence(repo, pr_number, headers, api_base)
    if evidence["error"]:
        logger.error(
            "Failed to fetch PR files for %s #%s (%s). If this is a 404, the token used cannot see this repo: "
            "save a token under Integrations on the account that owns it (or give the shared token access).",
            repo, pr_number, evidence["error"],
        )
        return
    logger.info(f"Successfully fetched {len(evidence['files'])} changed file(s).")

    # 2. Write the overview from that evidence, with retries
    review_prompt, _ctx = build_review_prompt(repo, evidence["pr"], evidence["commits"], evidence["files"])
    try:
        logger.info("Invoking LLM for PR analysis...")
        comment_body = _llm_text(_call_llm_with_retry(review_prompt))
        if not comment_body:
            raise ValueError("the model returned an empty overview")
        logger.info("LLM summary generated successfully.")
    except Exception as e:
        # Never post the error into a public PR, and never overwrite a good earlier overview with one.
        logger.error(f"LLM generation failed after retries; no comment was posted to {repo} #{pr_number}: {str(e)}")
        return {"repo": repo, "pr_number": pr_number, "status": "summary_failed", "comment_url": None}

    # 3. Post the comment, or update the one we posted before
    head_sha = ((evidence["pr"] or {}).get("head") or {}).get("sha") or ""
    footer = f"\n\n<sub>Updated for commit `{head_sha[:7]}`</sub>" if head_sha else ""
    body = f"{OVERVIEW_MARKER}\n**Sonic Assistant PR Overview**\n\n{comment_body}{footer}"

    existing = _find_overview_comment(repo, pr_number, headers, api_base)
    if existing and existing.get("id"):
        logger.info(f"Updating the existing overview comment on {repo} #{pr_number}...")
        patch_res = requests.patch(
            f"{api_base}/repos/{repo}/issues/comments/{existing['id']}",
            headers=headers, json={"body": body}, timeout=_GITHUB_API_TIMEOUT_SECONDS,
        )
        if patch_res.status_code == 200:
            posted_url = patch_res.json().get("html_url")
            logger.info(f"SUCCESS! PR comment updated on {repo}: {posted_url}")
            return {"repo": repo, "pr_number": pr_number, "status": "comment_updated", "comment_url": posted_url}
        logger.warning(f"Could not update the existing comment (HTTP {patch_res.status_code}); posting a new one instead.")

    logger.info(f"Posting review comment to GitHub repository {repo} PR #{pr_number}...")
    post_res = requests.post(
        f"{api_base}/repos/{repo}/issues/{pr_number}/comments",
        headers=headers, json={"body": body}, timeout=_GITHUB_API_TIMEOUT_SECONDS,
    )

    if post_res.status_code == 201:
        logger.info(f"SUCCESS! PR comment posted to {repo}: {post_res.json().get('html_url')}")
    else:
        logger.error(f"Failed to post comment to {repo} (HTTP {post_res.status_code}): {post_res.text}")

    return {
        "repo": repo,
        "pr_number": pr_number,
        "status": "comment_posted" if post_res.status_code == 201 else "comment_failed",
        "comment_url": post_res.json().get("html_url") if post_res.status_code == 201 else None,
    }
