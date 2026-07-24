import os
import requests
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from backend.models.models import lite_llm
from backend.components.constraints import PR_REVIEW_PROMPT

logger = logging.getLogger("SASS Logger")

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


def process_pr_summary(repo: str, pr_number: int):
    """Fetches PR diffs, generates an LLM review, and posts it to GitHub."""
    logger.info(f"--- PROCESSING PR SUMMARY FOR {repo} #{pr_number} ---")
    
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN environment variable is not set!")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json"
    }
    api_base = "https://api.github.com"
    
    # 1. Fetch changed files
    files_url = f"{api_base}/repos/{repo}/pulls/{pr_number}/files"
    logger.info(f"Requesting PR files from GitHub: {files_url}")
    files_res = requests.get(files_url, headers=headers)
    
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
        
        # Safe content parsing
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

    # 3. Post comment to GitHub PR
    comment_url = f"{api_base}/repos/{repo}/issues/{pr_number}/comments"
    payload = {"body": f"**Sonic Assistant PR Overview**\n\n{comment_body}"}
    
    logger.info(f"Posting review comment to GitHub PR #{pr_number}...")
    post_res = requests.post(comment_url, headers=headers, json=payload)
    
    if post_res.status_code == 201:
        comment_url_posted = post_res.json().get("html_url")
        logger.info(f"SUCCESS! PR comment posted: {comment_url_posted}")
    else:
        logger.error(f"Failed to post comment (HTTP {post_res.status_code}): {post_res.text}")