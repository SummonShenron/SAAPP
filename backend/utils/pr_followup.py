"""The pull request that FOLLOWS a commit and push made with real git on the person's own machine: one card, one approval (2026-11-20).

WHY. "Commit, push and open a PR" took two approvals of two kinds. The commit and push is a runner card (propose_git_commit: the exact git line, the stronger confirmation, then the person's own computer asks
again). The pull request was a different tool with a different card (propose_commit with open_pull_request, which commits through GitHub's API instead, or the older create_pr write approval). A person who asked
for all three in one sentence was handed two cards and had to understand why.

WHAT. The runner card carries one more sentence: "Then Sonic opens a pull request on GitHub into `main` titled ...". Pressing Run (and the stronger confirmation) approves that too, because it is printed on the
card the person reads and approves. After the push has finished successfully, the server opens the pull request with the PERSON'S OWN GitHub token (never the shared one), once: an open pull request from that
branch into that base is reported instead of being duplicated. Nothing is opened when the push failed, was refused or never ran.

CHECKED BEFORE THE CARD EXISTS, so a person is never asked to push something whose pull request cannot be opened: their own token is set and may push to the repository, the branch is not the pull request's
own target, and the target branch exists on GitHub. Pure helpers here; the GitHub calls take the client from backend/services/git_commit.py (`GitHubApi`) so tests can pass a fake with the same `request`.
No title, body or branch name is written to a log."""
import re
from typing import Any, Dict, Optional

from backend.services import git_commit as gc

MAX_TITLE_CHARS = 120


class PrFollowupError(ValueError):
    """The pull request cannot be arranged safely; the message says why, in words the model can act on."""


def _flat(text: Any, limit: int) -> str:
    return re.sub(r"[`\x00-\x1f\x7f]+", " ", re.sub(r"\s+", " ", str(text or ""))).strip()[:limit]


def build(args: Dict[str, Any], *, repo: str, head: str, default_base: str, subject: str) -> Dict[str, Any]:
    """The pull request to open after the push: {repo, head, base, title, draft}. `head` is the branch that gets pushed, `default_base` the repository's default branch, `subject` the commit's subject line
    (the title when none is given). Raises PrFollowupError. Pure."""
    if not repo:
        raise PrFollowupError("I don't know which repository the pull request belongs to. Set the target repo first.")
    try:
        head_name = gc.clean_branch(head) if str(head or "").strip() else ""
    except gc.GitCommitError as problem:
        raise PrFollowupError(str(problem)) from None
    if not head_name:
        raise PrFollowupError("I don't know which branch their folder is on, so I cannot say which branch the pull request is from. Give new_branch so the commit goes on a branch of its own.")
    try:
        base = gc.clean_branch(args.get("pr_base") or default_base or "main")
    except gc.GitCommitError as problem:
        raise PrFollowupError(str(problem)) from None
    if base == head_name:
        raise PrFollowupError(f"A pull request needs a branch of its own: {head_name!r} is also the branch it would go into. Give new_branch (a new name) so the commit goes on a new branch.")
    title = _flat(args.get("pr_title"), MAX_TITLE_CHARS) or _flat(subject, MAX_TITLE_CHARS)
    if len(title) < 4:
        raise PrFollowupError("Give the pull request a title (pr_title) that says what the change does.")
    return {"repo": repo, "head": head_name, "base": base, "title": title, "draft": bool(args.get("pr_draft"))}


def card_text(follow: Dict[str, Any]) -> str:
    """The sentence printed on the runner card, so what the person approves is all there in words. Pure."""
    state = "as a draft" if follow.get("draft") else "ready for review"
    return (f"Then Sonic opens a pull request on GitHub for {follow['repo']}: \"{follow['title']}\", from {follow['head']} into {follow['base']} ({state}), using your own GitHub token. "
            "That happens only if the push above succeeds.")


def check_before_card(api: Any, follow: Dict[str, Any]) -> None:
    """Refuses (GitCommitError, in words) when the pull request could not be opened after the push: the person's token cannot push to the repository, or the target branch is not on GitHub. Blocking (GitHub calls)."""
    info = gc.repo_info(api, follow["repo"])
    if not info.get("can_push"):
        raise gc.GitCommitError(f"Your GitHub token can read {follow['repo']} but not push to it, so the pull request could not be opened. It needs write access.")
    if gc.branch_head(api, follow["repo"], follow["base"]) is None:
        raise gc.GitCommitError(f"The branch {follow['base']!r} (where the pull request would go) does not exist on GitHub. Name another one with pr_base.")


def open_after_push(api: Any, follow: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Opens the pull request once the push has finished. Returns {"url", "number", "existing"} or {"error"}: an open pull request from this branch into this base is reported (existing True) and not duplicated.
    Never raises. Blocking (GitHub calls)."""
    try:
        repo, head, base = follow["repo"], follow["head"], follow["base"]
        number = gc.find_open_pull_request(api, repo, head, base)
        if number is not None:
            return {"url": f"https://github.com/{repo}/pull/{number}", "number": number, "existing": True}
        status, pr = api.request("POST", f"/repos/{repo}/pulls", {"title": follow["title"], "head": head, "base": base, "body": gc.pull_request_body(message), "draft": bool(follow.get("draft"))})
        if status == 201 and isinstance(pr, dict):
            return {"url": pr.get("html_url"), "number": pr.get("number"), "existing": False}
        return {"error": str(gc._explain(status, pr, "open the pull request"))}
    except gc.GitCommitError as problem:
        return {"error": str(problem)}
    except Exception:
        return {"error": "The pull request could not be opened just now."}


def result_text(opened: Optional[Dict[str, Any]], follow: Dict[str, Any]) -> str:
    """What the model is told about the pull request, appended to the runner's result. Pure."""
    if not opened:
        return ""
    if opened.get("error"):
        return (f"\nThe push worked, but the pull request was NOT opened: {opened['error']} The branch {follow['head']} is on GitHub, so say that, and that the person can open the pull request there "
                "or ask you to try again.")
    verb = "was already open" if opened.get("existing") else "was opened"
    return f"\nPull request #{opened.get('number')} {verb}: {opened.get('url')} (from {follow['head']} into {follow['base']}). Tell the person, with the link."
