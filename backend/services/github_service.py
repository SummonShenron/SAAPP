import os
import requests
from typing import Optional
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from backend.models.models import lite_llm
from backend.components.constraints import PR_REVIEW_PROMPT

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


def process_pr_summary(repo: str, pr_number: int, token: Optional[str] = None):
    """Fetches PR diffs, generates an LLM review, and posts it to the target repo's PR."""
    logger.info(f"--- PROCESSING PR SUMMARY FOR {repo} #{pr_number} ---")

    token = token or os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN is not provided and the environment variable is not set!")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    api_base = "https://api.github.com"

    # 1. Fetch changed files
    files_url = f"{api_base}/repos/{repo}/pulls/{pr_number}/files"
    logger.info(f"Requesting PR files from GitHub: {files_url}")
    files_res = requests.get(files_url, headers=headers, timeout=_GITHUB_API_TIMEOUT_SECONDS)

    if files_res.status_code != 200:
        logger.error(f"Failed to fetch PR files (HTTP {files_res.status_code}): {files_res.text}")
        return

    changed_files = files_res.json()
    logger.info(f"Successfully fetched {len(changed_files)} changed file(s).")

    diff_context = []
    for f in changed_files[:10]:
        filename = f.get("filename")
        status = f.get("status")
        patch = f.get("patch", "No patch available")
        diff_context.append(f"File: {filename} ({status})\nPatch:\n```diff\n{patch}\n```")

    formatted_diffs = "\n\n".join(diff_context)

    # 2. Format prompt & generate summary with retries
    if "{diffs}" in PR_REVIEW_PROMPT:
        review_prompt = PR_REVIEW_PROMPT.format(diffs=formatted_diffs)
    else:
        review_prompt = f"{PR_REVIEW_PROMPT}\n\nPull Request Diffs:\n{formatted_diffs}"

    try:
        logger.info("Invoking LLM for PR analysis...")
        review_response = _call_llm_with_retry(review_prompt)

        raw_content = getattr(review_response, "content", review_response)
        if isinstance(raw_content, list):
            text_blocks = []
            for block in raw_content:
                if isinstance(block, str):
                    text_blocks.append(block)
                elif isinstance(block, dict) and "text" in block:
                    text_blocks.append(block["text"])
            comment_body = "\n".join(text_blocks).strip()
        else:
            comment_body = str(raw_content).strip()

        logger.info("LLM summary generated successfully.")
    except Exception as e:
        logger.error(f"LLM generation failed after retries: {str(e)}")
        comment_body = f"Could not generate automated PR summary due to upstream API limits: {str(e)}"

    # 3. Post comment to the target repo's PR
    comment_url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
    payload = {"body": f"**Sonic Assistant PR Overview**\n\n{comment_body}"}

    logger.info(f"Posting review comment to GitHub repository {repo} PR #{pr_number}...")
    post_res = requests.post(comment_url, headers=headers, json=payload, timeout=_GITHUB_API_TIMEOUT_SECONDS)

    if post_res.status_code == 201:
        comment_url_posted = post_res.json().get("html_url")
        logger.info(f"SUCCESS! PR comment posted to {repo}: {comment_url_posted}")
    else:
        logger.error(f"Failed to post comment to {repo} (HTTP {post_res.status_code}): {post_res.text}")

    return {
        "repo": repo,
        "pr_number": pr_number,
        "status": "comment_posted" if post_res.status_code == 201 else "comment_failed",
        "comment_url": post_res.json().get("html_url") if post_res.status_code == 201 else None,
    }