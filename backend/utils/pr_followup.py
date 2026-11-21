"""The pull request that FOLLOWS a commit and push made with real git on the person's own machine: one card, one approval (2026-11-20).

WHY. "Commit, push and open a PR" took two approvals of two kinds. The commit and push is a runner card (propose_git_commit: the exact git line, the stronger confirmation, then the person's own computer asks
again). The pull request was a different tool with a different card (propose_commit with open_pull_request, which commits through GitHub's API instead, or the older create_pr write approval). A person who asked
for all three in one sentence was handed two cards and had to understand why.

WHAT. The runner card carries one more sentence: "Then Sonic opens a pull request on GitHub into `main` titled ...". Pressing Run (and the stronger confirmation) approves that too, because it is printed on the
card the person reads and approves. After the push has finished successfully, the server opens the pull request with the PERSON'S OWN GitHub token (never the shared one), once: an open pull request from that
branch into that base is reported instead of being duplicated. Nothing is opened when the push failed, was refused or never ran.

CHECKED BEFORE THE CARD EXISTS, so a person is never asked to push something whose pull request cannot be opened: their own token is set and may push to the repository, the branch is not the pull request's
own target, and the target branch exists on GitHub. Pure helpers here; the GitHub calls take the client from backend/services/git_commit.py (`GitHubApi`) so tests can pass a fake with the same `request`.
No title, body or branch name is written to a log.

MERGING TOO (2026-11-21). `merge_after` adds one more step to the same card: once the pull request is open and ITS CHECKS PASS, Sonic merges it, with the person's own token. Merging used to be a second card and a
second approval on purpose, and it deploys when the target is the default branch, so this is narrower than "merge": it happens only when the person asked for the merge in their own words (`asks_to_merge`),
the card says so in plain words, the checks are waited for (and a failed or unfinished check means no merge), the commit merged is the one that was checked (the head commit is pinned, and re-read just before),
and GitHub's own rules (conflicts, required reviews, protected branches) still decide. Every one of those refusals leaves the pull request open and says why."""
import os
import re
from typing import Any, Dict, Iterable, Optional

from backend.services import git_commit as gc

MAX_TITLE_CHARS = 120
POLL_SECONDS = float(os.getenv("SONIC_MERGE_POLL_SECONDS", "15"))                       # how often the checks are looked at while waiting
NO_CHECKS_GRACE_SECONDS = float(os.getenv("SONIC_MERGE_NO_CHECKS_SECONDS", "60"))       # a repository with no checks at all shows none for this long before that is believed (checks appear a few seconds after a pull request opens)
MAX_WAIT_SECONDS = float(os.getenv("SONIC_MERGE_WAIT_SECONDS", "600"))                  # the longest the turn waits for checks to finish
BAD_CHECKS = ("failure", "timed_out", "startup_failure", "action_required", "cancelled", "stale")


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
    follow: Dict[str, Any] = {"repo": repo, "head": head_name, "base": base, "title": title, "draft": bool(args.get("pr_draft")), "merge": None}
    if args.get("merge_after"):
        method = str(args.get("merge_method") or "squash").strip().lower()
        if method not in gc.MERGE_METHODS:
            raise PrFollowupError("The merge method has to be squash, merge or rebase.")
        if follow["draft"]:
            raise PrFollowupError("A draft pull request cannot be merged. Leave out pr_draft, or leave out merge_after.")
        follow["merge"] = method
    return follow


def card_text(follow: Dict[str, Any]) -> str:
    """The sentence printed on the runner card, so what the person approves is all there in words. Pure."""
    state = "as a draft" if follow.get("draft") else "ready for review"
    text = (f"Then Sonic opens a pull request on GitHub for {follow['repo']}: \"{follow['title']}\", from {follow['head']} into {follow['base']} ({state}), using your own GitHub token. "
            "That happens only if the push above succeeds.")
    if follow.get("merge"):
        text += (f" Then, once its checks pass (Sonic waits up to {int(MAX_WAIT_SECONDS // 60)} minutes for them), Sonic {follow['merge']}-merges it into {follow['base']}, which is a change to that branch "
                 "that cannot be taken back here. Nothing is merged if a check fails or is still running, if the pull request changes after it is opened, or if GitHub refuses (conflicts, required reviews).")
    return text


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


# ---- merging once the checks pass --------------------------------------------------------------------------------------------------------------------

_NEGATED_MERGE = re.compile(r"\b(?:don'?t|dont|do not|never|not|no|without|skip|except|hold off)\b[^.?!\n]{0,30}\bmerg(?:e|ing)\b|\bno merge\b", re.IGNORECASE)
_NOT_A_MERGE_REQUEST = r"(?!\s+(?:conflicts?|commits?|base|strategy|queue))"
_MERGE_REQUEST = re.compile(
    rf"(?:\b(?:and|then|also|plus)\s+|&\s*)(?:then\s+|also\s+)?(?:squash[- ]|rebase[- ])?merg(?:e|ing)\b{_NOT_A_MERGE_REQUEST}"
    rf"|\bmerge\s+(?:it|that|this|them|the\s+(?:pr|pull request|branch|changes))\b"
    rf"|\bmerge\s+when\s+(?:it(?:'s| is)\s+)?(?:green|checks?|ci|tests?)"
    rf"|\b(?:squash|rebase)[- ]merge\b"
    rf"|^\W*(?:(?:please|can you|could you|go ahead and)\s+)?merg(?:e|ing)\b{_NOT_A_MERGE_REQUEST}",
    re.IGNORECASE,
)


def asks_to_merge(person_messages: Iterable[Any]) -> bool:
    """Whether the person asked for a merge in their own words: the newest of their messages given (the one this turn answers) says so and does not say not to, or one of the others did ("open a PR" ... then
    "ok, and merge it" is one request) and the newest does not take it back. Only the person's messages are passed in, never Sonic's or anything it read, so nothing else can ask for it. Pure."""
    texts = [str(t or "") for t in person_messages]
    if not texts:
        return False
    newest = texts[-1]
    if _NEGATED_MERGE.search(newest):
        return False
    return any(_MERGE_REQUEST.search(t) and not _NEGATED_MERGE.search(t) for t in texts[-2:])


def checks_state(api: Any, repo: str, sha: str) -> Dict[str, Any]:
    """The checks on one commit, from GitHub now: {"state": "passed" | "failed" | "pending" | "none", "failed": [names], "pending": [names]}. Check runs (Actions and apps) and the older commit statuses both
    count. A skipped or neutral check is not a failure. Raises GitCommitError in words when GitHub will not say. Blocking."""
    status, data = api.request("GET", f"/repos/{repo}/commits/{sha}/check-runs?per_page=100")
    if status != 200 or not isinstance(data, dict):
        raise gc._explain(status, data, "read the checks")
    runs = [r for r in data.get("check_runs") or [] if isinstance(r, dict)]
    status2, legacy = api.request("GET", f"/repos/{repo}/commits/{sha}/status")
    statuses = int(legacy.get("total_count") or 0) if status2 == 200 and isinstance(legacy, dict) else 0
    legacy_state = legacy.get("state") if status2 == 200 and isinstance(legacy, dict) else None
    failed = [_flat(r.get("name"), 40) for r in runs if r.get("status") == "completed" and r.get("conclusion") in BAD_CHECKS]
    pending = [_flat(r.get("name"), 40) for r in runs if r.get("status") != "completed"]
    if statuses and legacy_state in ("failure", "error"):
        failed.append("commit statuses")
    elif statuses and legacy_state == "pending":
        pending.append("commit statuses")
    if failed:
        return {"state": "failed", "failed": failed, "pending": pending}
    if pending:
        return {"state": "pending", "failed": [], "pending": pending}
    return {"state": "passed" if (runs or statuses) else "none", "failed": [], "pending": []}


def decide(state: Dict[str, Any], elapsed: float, max_wait: float, grace: float) -> str:
    """What to do after looking at the checks: "merge", "wait", "failed" (a check failed: never merge) or "timeout" (still running after the longest wait). "none" waits for `grace` seconds, since checks show a
    little after a pull request opens, and only then is believed. Pure."""
    kind = state.get("state")
    if kind == "failed":
        return "failed"
    if kind == "passed":
        return "merge"
    if kind == "none":
        return "merge" if elapsed >= grace else "wait"
    return "timeout" if elapsed >= max_wait else "wait"


def merge_now(api: Any, follow: Dict[str, Any], number: int, expected_sha: str) -> Dict[str, Any]:
    """Merges the pull request, after reading it once more: still open, not a draft, not already merged, no conflicts, and STILL the commit whose checks were looked at. Returns {"merged": True, "sha",
    "method"} or {"error"}. Never raises. Blocking (GitHub calls)."""
    try:
        repo = follow["repo"]
        info = gc.pull_request_info(api, repo, number)
        if info["merged"]:
            return {"merged": True, "sha": "", "method": follow.get("merge") or "squash", "already": True}
        if info["state"] != "open":
            return {"error": f"Pull request #{number} was closed, so nothing was merged."}
        if info["draft"]:
            return {"error": f"Pull request #{number} is a draft, so nothing was merged."}
        if expected_sha and info["head_sha"] and info["head_sha"] != expected_sha:
            return {"error": f"Pull request #{number} has new commits since its checks were looked at, so nothing was merged."}
        if info["mergeable"] is False:
            return {"error": f"GitHub says pull request #{number} has conflicts with {info['base_ref']}, so nothing was merged."}
        result = gc.merge_pull_request(api, repo, number, info["head_sha"] or expected_sha, info["title"] or follow["title"], follow.get("merge") or "squash")
        return {"merged": True, "sha": result.get("sha", ""), "method": result.get("method", follow.get("merge") or "squash")}
    except gc.GitCommitError as problem:
        return {"error": str(problem)}
    except Exception:
        return {"error": "The merge could not be done just now, and nothing was merged."}


def merge_result_text(kind: str, follow: Dict[str, Any], number: Any, detail: Optional[Dict[str, Any]] = None) -> str:
    """What the model is told about the merge, appended to the rest. `kind`: "merged", "failed" (a check failed), "timeout" (checks still running), "error" (it could not be done). Pure."""
    detail = detail or {}
    base, head = follow["base"], follow["head"]
    if kind == "merged":
        sha = detail.get("sha") or ""
        sha_text = f" as {sha[:7]}" if re.fullmatch(r"[0-9a-f]{40}", sha) else ""
        already = " (it was already merged)" if detail.get("already") else ""
        return f"\nPull request #{number} was MERGED into {base} ({detail.get('method') or follow.get('merge')}){sha_text}{already}. Tell the person, plainly, including that {base} changed."
    if kind == "failed":
        names = ", ".join(detail.get("failed") or ["a check"])
        return f"\nThe pull request was NOT merged: these checks failed: {names}. It is still open and nothing on {base} changed. Say so; ci_status and read_ci_log show why, and the person decides what next."
    if kind == "timeout":
        names = ", ".join(detail.get("pending") or ["its checks"])
        return f"\nThe pull request was NOT merged: {names} had not finished after the longest wait. It is still open and nothing on {base} changed. Say so, and that merging it later is one more request."
    return f"\nThe pull request was NOT merged: {detail.get('error') or 'it could not be done.'} It is still open ({head} into {base}); nothing on {base} changed. Say so plainly."
