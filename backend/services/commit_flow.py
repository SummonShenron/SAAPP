"""The approval card for a commit, and what happens when it is approved (the tool is `propose_commit`; the GitHub calls are in git_commit.py).

TWO STEPS, THE SAME AS EVERY OTHER OUTWARD ACTION (sending an email, appending to a document):
  1. DRAFT. The tool agent calls propose_commit with a short message (and optionally a new branch and a pull request). Everything is checked against GitHub here, with the person's OWN token: that it
     can push, that the branch exists, what each file would change. The result is a card; nothing has been changed on GitHub.
  2. EXECUTE. Only when the person approves. The card is the only thing that survives between messages, so it is written to be read back (parse_card): the repo, branch, message, files with a short
     fingerprint of each file's text, and the pull request. The files are read again from the person's folder at that point and refused if any fingerprint changed since the card was shown.

NOTHING HERE LOGS FILE TEXT OR THE MESSAGE.
"""
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services import git_commit as gc
from backend.services import local_workspace
from backend.utils import applied_changes

logger = logging.getLogger("SASS Logger")

CARD_MARKER = "Ready to commit these changes"
MAX_PR_TITLE_CHARS = 120
_FILE_RE = re.compile(r"^- `([^`]+)` (new|modified) \+(\d+) −(\d+) `([0-9a-f]{8})`", re.MULTILINE)


class NoGitHubToken(gc.GitCommitError):
    pass


def _own_token(username: str) -> str:
    from backend.utils.user_settings_utils import get_user_github_token

    token = get_user_github_token(username)
    if not token:
        raise NoGitHubToken("Committing uses your own GitHub token, and none is set. Add one under Integrations (it needs write access to the repository), then ask again.")
    return token


def _flat(text: Any, limit: int) -> str:
    return re.sub(r"[`\x00-\x1f\x7f]+", " ", re.sub(r"\s+", " ", str(text or ""))).strip()[:limit]


_COMMIT_VERB = re.compile(r"\b(?:comm+it|push)(?:ting)?\b", re.IGNORECASE)      # "commmit" is a common slip
_NOUN_BEFORE = frozenset({"the", "a", "an", "last", "that", "this", "my", "your", "our", "recent", "latest", "previous", "each", "every", "first", "merge", "from", "of", "per", "git"})
_NEW_BRANCH = re.compile(r"\b(?:(?:new|fresh|separate)\s+branch|(?:open|create|make|start|cut)\w*\s+(?:a\s+)?(?:new\s+)?branch)\b", re.IGNORECASE)


def wants_commit(text: Any) -> bool:
    """True when a request asks for work to be committed, pushed or put on a new branch ("open a new branch called x and commit our changes, then open a PR"). Pure, no model.
    Such a request goes to the tool agent (propose_commit), never to the older pull-request drafter, which only compares two branches that already differ and used to invent the
    branch it was told nothing about. "commit" and "push" count only as verbs ("the last commit", "a commit from Dana" do not)."""
    message = str(text or "")
    if _NEW_BRANCH.search(message):
        return True
    for found in _COMMIT_VERB.finditer(message):
        before = re.findall(r"[A-Za-z']+", message[:found.start()])[-1:]
        if not before or before[0].lower() not in _NOUN_BEFORE:
            return True
    return False


def continues_commit_request(messages: Any, content_of: Callable[[Any], str]) -> bool:
    """True when one of the person's last two messages before this one asked for a commit, push or new branch: a short follow-up ("make the message shorter", "try again", "okay do it") belongs to
    the same request even though it has none of those words itself. `messages` is the whole conversation including this message; `content_of` reads a message's text. Pure."""
    earlier = [m for m in list(messages or [])[:-1] if getattr(m, "type", None) == "human" or (isinstance(m, dict) and m.get("type") == "human")]
    return any(wants_commit(content_of(m)) for m in earlier[-2:])


def pending_line(username: Optional[str], current_branch: Optional[str]) -> str:
    """One line for the tool agent's folder paragraph: the branch the folder is on (as the browser reported it) and what is waiting to be committed. Blocking."""
    pending = applied_changes.pending(username)
    branch = f"The person's folder is on git branch {current_branch!r} (reported by their browser). " if current_branch else "The person's git branch is not known; ask which branch if they want a commit. "
    if not pending:
        return (
            f"COMMITS: {branch}Nothing you changed and they applied is waiting, but when they ask you to commit, push, open a new branch, or open a pull request for their working changes, call propose_commit "
            "with a one or two sentence message: it compares their whole folder with GitHub's copy of the branch and lists every file that differs. For a new branch pass new_branch, and for a pull request pass open_pull_request."
        )
    names = ", ".join(sorted(pending)[:8]) + (f" and {len(pending) - 8} more" if len(pending) > 8 else "")
    return (
        f"COMMITS: {branch}{len(pending)} file(s) you changed and they applied are waiting to be committed: {names}. When they ask you to commit, push, or open a pull request for this work, call "
        "propose_commit with a one or two sentence message (what the change does, like a pull request summary); it shows them a card and commits nothing until they approve. For a new branch pass "
        "new_branch, and for a pull request pass open_pull_request."
    )


# ---------------------------------------------------------------------------------------------------------------
# Step 1: the draft
# ---------------------------------------------------------------------------------------------------------------

def _applied_files(username: str, args: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Tuple[int, int]]]:
    """The files Sonic changed and the person applied that are still waiting, as their folder holds them, with the size of the change each was approved at. Raises GitCommitError when none."""
    waiting = applied_changes.pending(username)
    wanted = args.get("paths")
    if isinstance(wanted, list) and wanted:
        unknown = [p for p in wanted if p not in waiting]
        if unknown:
            raise gc.GitCommitError(f"{unknown[0]} is not one of the files waiting to be committed: {', '.join(sorted(waiting)) or 'none'}.")
        waiting = {p: waiting[p] for p in wanted}
    waiting = {p: v for p, v in waiting.items() if p != gc.NOTES_PATH}          # Sonic's own notes are never committed
    texts = applied_changes.read_texts(username, waiting)
    if not texts:
        raise gc.GitCommitError("Nothing you changed and applied is waiting to be committed.")
    return texts, {p: (int(v.get("additions", 0)), int(v.get("deletions", 0))) for p, v in waiting.items()}


def _working_files(username: str, args: Dict[str, Any], api: Any, repo: str, branch: str) -> Tuple[Dict[str, str], List[str]]:
    """The folder's own changes: every text file in the synced folder that is new or different compared with `branch` on GitHub (what `git status` would list, whoever made the change), except the
    files Sonic never commits (CI workflows, secrets, dependency files, binaries, files over the size limit), which are named in the notes that come back. Blocking (GitHub calls)."""
    all_paths = local_workspace.snapshot_paths(username)
    if not all_paths:
        raise gc.GitCommitError("No folder is connected, so there are no working changes to commit.")
    wanted = [p for p in (args.get("paths") or []) if isinstance(p, str)] if isinstance(args.get("paths"), list) else []
    if wanted:
        missing = [p for p in wanted if p not in set(all_paths)]
        if missing:
            raise gc.GitCommitError(f"{missing[0]} is not in your synced folder.")
        all_paths = wanted
    candidates = [p for p in all_paths if not p.startswith(".sonic/") and not p.lower().endswith(gc._BINARY_SUFFIXES)]      # Sonic's own scratch folder is never swept in
    folder = applied_changes.read_texts(username, candidates + [".gitignore"])          # a file that is not text is left out here
    gitignore = folder.get(".gitignore", "")
    changed = gc.differing_from_repo(api, repo, branch, {p: t for p, t in folder.items() if p in set(candidates)}, gitignore)
    left_out = [p for p in changed if gc.refusal_for_path(p) or len(folder[p].encode("utf-8")) > gc.MAX_FILE_BYTES]
    notes = [f"Compared your synced folder with `{branch}` on GitHub. A file that someone changed on GitHub after you last pulled would show here as reverted, so read the +/− counts."]
    if left_out:
        shown = ", ".join(f"`{p}`" for p in sorted(left_out)[:6]) + (f" and {len(left_out) - 6} more" if len(left_out) > 6 else "")
        notes.append(f"Left out because Sonic never commits them (CI workflows, secrets, dependency files, very large files): {shown}.")
    return {p: folder[p] for p in changed if p not in left_out}, notes


def _files_to_commit(username: str, args: Dict[str, Any], api: Any, repo: str, branch: str) -> Tuple[Dict[str, str], Optional[Dict[str, Tuple[int, int]]], List[str]]:
    """(file texts, approved change sizes or None, notes for the card). scope "applied" (the default when something is waiting) is the files Sonic changed and the person applied; scope "working" (the
    default when nothing is waiting, and what "commit our working changes" means) is everything in the folder that differs from the branch on GitHub."""
    scope = str(args.get("scope") or "").strip().lower()
    if scope not in ("applied", "working"):
        scope = "applied" if applied_changes.pending(username) else "working"
    if scope == "applied":
        texts, expected = _applied_files(username, args)
        return texts, expected, []
    texts, notes = _working_files(username, args, api, repo, branch)
    if not texts:
        raise gc.GitCommitError(f"Your synced folder matches `{branch}` on GitHub, so there is nothing to commit.")
    return texts, None, notes


def draft(username: str, args: Dict[str, Any], *, repo: str, current_branch: Optional[str], api_factory: Optional[Callable[[str], Any]] = None) -> Tuple[Dict[str, Any], str]:
    """(details, card text) for a commit the model proposed, or GitCommitError in words for the person. Blocking (GitHub calls)."""
    if not repo:
        raise gc.GitCommitError("I don't know which repository to commit to. Set your target repo first.")
    message = gc.clean_message(args.get("message"))
    api = (api_factory or gc.GitHubApi)(_own_token(username))
    info = gc.repo_info(api, repo)
    if not info["can_push"]:
        raise gc.GitCommitError(f"Your GitHub token can read {repo} but not push to it. It needs write access.")

    requested = _flat(args.get("branch"), gc.MAX_BRANCH_CHARS) or (current_branch or "")
    new_branch = gc.clean_branch(args["new_branch"]) if args.get("new_branch") else None
    notes: List[str] = []
    if new_branch:
        if gc.branch_head(api, repo, new_branch) is not None:
            raise gc.GitCommitError(f"The branch {new_branch!r} already exists on GitHub. Pick another name.")
        base_branch = requested if requested and gc.branch_head(api, repo, gc.clean_branch(requested)) is not None else info["default_branch"]
        if requested and base_branch != requested:
            notes.append(f"Your branch `{requested}` is not on GitHub yet, so the new branch starts from `{info['default_branch']}`.")
    else:
        if not requested:
            raise gc.GitCommitError("I don't know which branch your folder is on. Tell me the branch to commit to, or give a new_branch name.")
        base_branch = gc.clean_branch(requested)

    texts, expected, scope_notes = _files_to_commit(username, args, api, repo, base_branch)
    notes.extend(scope_notes)
    plan = gc.build_plan(api, repo, base_branch, texts, expected, skip_unreadable=expected is None)
    if plan.get("skipped"):
        shown = ", ".join(f"`{p}`" for p in plan["skipped"][:6]) + (f" and {len(plan['skipped']) - 6} more" if len(plan["skipped"]) > 6 else "")
        notes.append(f"Left out because the copy on GitHub is not UTF-8 text (Sonic will not overwrite it): {shown}.")
    target = new_branch or base_branch

    pr: Optional[Dict[str, Any]] = None
    if args.get("open_pull_request") or args.get("pull_request"):
        nested = args["pull_request"] if isinstance(args.get("pull_request"), dict) else {}
        raw_pr = {"title": args.get("pr_title") or nested.get("title"), "base": args.get("pr_base") or nested.get("base"), "draft": args.get("pr_draft") if "pr_draft" in args else nested.get("draft")}
        pr_base = gc.clean_branch(raw_pr.get("base") or info["default_branch"])
        if pr_base == target:
            raise gc.GitCommitError(f"A pull request needs a different branch from its target; {target!r} is both. Give a new_branch.")
        if gc.branch_head(api, repo, pr_base) is None:
            raise gc.GitCommitError(f"The branch {pr_base!r} (the pull request's target) does not exist on GitHub.")
        title = _flat(raw_pr.get("title"), MAX_PR_TITLE_CHARS) or _flat(message.split("\n", 1)[0], MAX_PR_TITLE_CHARS)
        pr = {"title": title, "base": pr_base, "draft": bool(raw_pr.get("draft"))}
    if not new_branch and base_branch == info["default_branch"]:
        notes.append(f"`{base_branch}` is the default branch, so this commits straight to it.")
    drift = [f["path"] for f in plan["files"] if f["drift"]]
    for path in drift:
        notes.append(f"`{path}` on GitHub differs from the change you approved here by more than that change. The commit holds the full text from your folder, so anything on `{base_branch}` that you haven't pulled would be replaced.")

    details = {
        "repo": repo, "branch": base_branch, "new_branch": new_branch, "message": message, "pr": pr,
        "paths": [f["path"] for f in plan["files"]], "fingerprints": {f["path"]: f["fingerprint"] for f in plan["files"]},
    }
    return details, build_card(details, plan, private=info["private"], notes=notes)


def build_card(details: Dict[str, Any], plan: Dict[str, Any], *, private: bool, notes: List[str]) -> str:
    lines = [f"**{CARD_MARKER}**", "", f"**Repo:** `{details['repo']}` ({'private' if private else 'public'})"]
    if details["new_branch"]:
        lines.append(f"**New branch:** `{details['new_branch']}` from `{details['branch']}`")
    else:
        lines.append(f"**Branch:** `{details['branch']}`")
    lines += ["**Message:**"] + [f"> {ln}" if ln else ">" for ln in details["message"].split("\n")] + [">", "> Co-authored-by: Sonic Assistant", "", f"**Files ({len(plan['files'])}):**"]
    for f in plan["files"]:
        lines.append(f"- `{f['path']}` {f['status']} +{f['additions']} −{f['deletions']} `{f['fingerprint']}`")
    if details["pr"]:
        pr = details["pr"]
        lines += ["", f"**Pull request:** `{pr['title']}` into `{pr['base']}` ({'draft' if pr['draft'] else 'ready for review'})"]
    for note in notes:
        lines += ["", f"⚠️ {note}"]
    return "\n".join(lines)


def approval_message(card: str, merge: bool = False) -> str:
    return "**Approval Required**\n\n" + card + f"\n\n*Nothing is {'merged' if merge else 'committed'} until you approve. Please Approve or Reject.*"


# ---------------------------------------------------------------------------------------------------------------
# What to run afterwards: Sonic works through GitHub's API, so the person's own git has not seen the commit
# ---------------------------------------------------------------------------------------------------------------

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _ref(name: Any) -> str:
    """A branch name that is safe to print in a command the person will paste (gc.clean_branch already limits what can be a branch; this is the last check before it reaches a shell). Pure."""
    text = str(name or "").strip()
    return text if _SAFE_REF_RE.match(text) and ".." not in text else ""


def catch_up_after_commit(branch: str, *, new_branch: bool) -> str:
    """The commands that bring the person's own repository in line with a commit Sonic made on GitHub, as a copyable block. Their working files already hold the committed changes, so the
    old copy is stashed (nothing is thrown away) before the branch is checked out; a plain push to a branch they were already on is followed by a fast-forward pull. Pure."""
    target = _ref(branch)
    if not target:
        return "Your own git has not seen this commit yet: run `git fetch origin`, then check out the branch."
    commands = ["git fetch origin", 'git stash push -u -m "before syncing with the sonic commit"', f"git checkout {target}"]
    if not new_branch:
        commands.append(f"git pull --ff-only origin {target}")
    block = "```bash\n" + "\n".join(commands) + "\n```"
    return (
        f"**Your own git has not seen this commit yet** (Sonic made it on GitHub). To catch up, run these in your terminal. The stash keeps your current files, which already hold these changes, "
        f"in case anything was left out of the commit:\n\n{block}\n\nThen `git stash show --stat --include-untracked` lists anything the stash holds that is not in the commit; when you have checked it, `git stash drop` clears it."
    )


def catch_up_after_merge(base: str, head: str) -> str:
    """The commands to bring the person's copy of the target branch up to date after Sonic merged a pull request on GitHub, and a note on the merged branch. Pure."""
    target, merged = _ref(base), _ref(head)
    if not target:
        return "Your own git has not seen this merge yet: run `git fetch origin` and pull the target branch."
    block = "```bash\ngit fetch origin\ngit checkout " + target + "\ngit pull --ff-only origin " + target + "\n```"
    text = f"To bring your own copy of `{target}` up to date, run this in your terminal (if `git checkout` refuses because of local changes, run `git stash push -u` first):\n\n{block}"
    if merged and merged != target:
        text += f"\n\n`{merged}` is merged now. When you like, delete your local copy with `git branch -D {merged}` (capital D, because a squash merge looks unmerged to git)."
    return text


# ---------------------------------------------------------------------------------------------------------------
# Step 2: the approval (the card is all that is left)
# ---------------------------------------------------------------------------------------------------------------

def parse_card(content: str) -> Optional[Dict[str, Any]]:
    """The details out of a card Sonic wrote, or None. Pure."""
    if CARD_MARKER not in (content or ""):
        return None
    repo = re.search(r"\*\*Repo:\*\* `([^`]+)`", content)
    new = re.search(r"\*\*New branch:\*\* `([^`]+)` from `([^`]+)`", content)
    branch = re.search(r"\*\*Branch:\*\* `([^`]+)`", content)
    message = re.search(r"\*\*Message:\*\*\n((?:>.*\n?)+)", content)
    files = _FILE_RE.findall(content)
    if not (repo and message and files and (new or branch)):
        return None
    message_lines = [ln[1:].lstrip(" ") if ln.startswith(">") else ln for ln in message.group(1).rstrip("\n").split("\n")]
    while message_lines and message_lines[-1] in ("", "Co-authored-by: Sonic Assistant"):
        message_lines.pop()
    pr = re.search(r"\*\*Pull request:\*\* `([^`]*)` into `([^`]+)` \((draft|ready for review)\)", content)
    return {
        "repo": repo.group(1), "branch": new.group(2) if new else branch.group(1), "new_branch": new.group(1) if new else None, "message": "\n".join(message_lines).strip(),
        "pr": {"title": pr.group(1), "base": pr.group(2), "draft": pr.group(3) == "draft"} if pr else None,
        "paths": [f[0] for f in files], "fingerprints": {f[0]: f[4] for f in files},
    }


def is_complete(details: Dict[str, Any]) -> bool:
    return bool(details.get("repo") and details.get("branch") and details.get("message") and details.get("paths") and details.get("fingerprints"))


def execute(username: str, details: Dict[str, Any], api_factory: Optional[Callable[[str], Any]] = None) -> str:
    """Makes the approved commit. Returns markdown for the person, success or the plain reason it did not happen. Blocking (GitHub calls)."""
    try:
        api = (api_factory or gc.GitHubApi)(_own_token(username))
        texts = applied_changes.read_texts(username, details["paths"])
        for path in details["paths"]:
            if path not in texts:
                raise gc.GitCommitError(f"`{path}` is not in your synced folder any more, so nothing was committed. Ask again.")
            if gc.fingerprint(texts[path]) != details["fingerprints"].get(path):
                raise gc.GitCommitError(f"`{path}` changed after the card was shown, so nothing was committed. Ask again and check the new card.")
        info = gc.repo_info(api, details["repo"])
        if not info["can_push"]:
            raise gc.GitCommitError(f"Your GitHub token can read {details['repo']} but not push to it.")
        message = gc.clean_message(details["message"])
        new_branch = gc.clean_branch(details["new_branch"]) if details.get("new_branch") else None
        base_branch = gc.clean_branch(details["branch"])
        plan = gc.build_plan(api, details["repo"], base_branch, texts)
        result = gc.commit_and_push(api, details["repo"], plan, message, base_branch, new_branch=new_branch, pull_request=details.get("pr"))
    except gc.GitCommitError as problem:
        return f"**Not committed:** {problem}"
    except Exception:
        logger.warning("[commit] the approved commit failed.", exc_info=True)
        return "**Not committed:** something went wrong talking to GitHub. Nothing was changed."
    applied_changes.clear(username, details["paths"])
    short = result["commit"][:7]
    lines = [f"**Committed** [`{short}`]({result['commit_url']}) to `{result['branch']}` on `{details['repo']}` ({len(plan['files'])} file{'s' if len(plan['files']) != 1 else ''})."]
    if result.get("pr_url"):
        lines.append(f"**Opened pull request** [#{result['pr_number']}]({result['pr_url']}) into `{details['pr']['base']}`.")
    elif result.get("pr_error"):
        lines.append(f"The commit is on GitHub, but the pull request was not opened: {result['pr_error']}")
    lines.append(catch_up_after_commit(result["branch"], new_branch=bool(details.get("new_branch"))))
    if result.get("pr_url") and not (details.get("pr") or {}).get("draft"):
        try:
            info = gc.pull_request_info(api, details["repo"], result["pr_number"])
            lines.append(approval_message(merge_card(details["repo"], result["pr_number"], details["pr"]["title"], info["head_ref"] or result["branch"], details["pr"]["base"], info["head_sha"] or "", "squash"), merge=True))
        except gc.GitCommitError:
            lines.append("I couldn't read the pull request back to offer the merge. Ask me to merge it and I'll show a card.")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# Merging the pull request: a second card, a second approval
# ---------------------------------------------------------------------------------------------------------------

MERGE_MARKER = "Ready to merge this pull request"
_MERGE_REPO_RE = re.compile(r"\*\*Repo:\*\* `([^`]+)`")
_MERGE_PR_RE = re.compile(r"\*\*Pull request:\*\* #(\d+)(?: `([^`]*)`)?")
_MERGE_BRANCH_RE = re.compile(r"\*\*Merge:\*\* `([^`]+)` into `([^`]+)` \((squash|merge|rebase)\)")
_MERGE_HEAD_RE = re.compile(r"\*\*Head commit:\*\* `([0-9a-f]{7,40})`")


def merge_card(repo: str, number: Any, title: str, head: str, base: str, head_sha: str, method: str) -> str:
    lines = [f"**{MERGE_MARKER}**", "", f"**Repo:** `{repo}`", f"**Pull request:** #{int(number)} `{_flat(title, MAX_PR_TITLE_CHARS)}`", f"**Merge:** `{head}` into `{base}` ({method})"]
    if head_sha:
        lines.append(f"**Head commit:** `{head_sha[:12]}`")
    return "\n".join(lines)


def parse_merge_card(content: str) -> Optional[Dict[str, Any]]:
    """The details out of a merge card Sonic wrote, or None. Reads only what is needed to find the pull request again; whatever the page is now is checked against GitHub before anything happens. Pure."""
    if MERGE_MARKER not in (content or ""):
        return None
    repo, pr, branches, head = _MERGE_REPO_RE.search(content), _MERGE_PR_RE.search(content), _MERGE_BRANCH_RE.search(content), _MERGE_HEAD_RE.search(content)
    if not (repo and pr):
        return None
    return {
        "repo": repo.group(1), "number": int(pr.group(1)), "title": pr.group(2) or "", "head": branches.group(1) if branches else None, "base": branches.group(2) if branches else None,
        "method": branches.group(3) if branches else "squash", "head_sha": head.group(1) if head else None,
    }


def is_complete_merge(details: Dict[str, Any]) -> bool:
    return bool(details.get("repo") and details.get("number"))


def execute_merge(username: str, details: Dict[str, Any], api_factory: Optional[Callable[[str], Any]] = None) -> str:
    """Merges the pull request the person approved. Checks it against GitHub first: still open, not a draft, and still the head commit and branches the card showed. Blocking."""
    try:
        api = (api_factory or gc.GitHubApi)(_own_token(username))
        info = gc.pull_request_info(api, details["repo"], details["number"])
        if info["merged"]:
            return f"**Already merged:** #{details['number']} was merged before this."
        if info["state"] != "open":
            raise gc.GitCommitError(f"Pull request #{details['number']} is closed, so nothing was merged.")
        if info["draft"]:
            raise gc.GitCommitError(f"Pull request #{details['number']} is a draft; mark it ready for review on GitHub first. Nothing was merged.")
        if (details.get("head") and info["head_ref"] and details["head"] != info["head_ref"]) or (details.get("base") and info["base_ref"] and details["base"] != info["base_ref"]):
            raise gc.GitCommitError("The pull request's branches are not the ones on the card, so nothing was merged. Ask again to see the new card.")
        if details.get("head_sha") and info["head_sha"] and not info["head_sha"].startswith(details["head_sha"]):
            raise gc.GitCommitError("The pull request has new commits since the card was shown, so nothing was merged. Ask again to see the new card.")
        if info["mergeable"] is False:
            raise gc.GitCommitError("GitHub says this pull request has conflicts with its target branch, so it can't be merged yet. Nothing was merged.")
        result = gc.merge_pull_request(api, details["repo"], details["number"], info["head_sha"], info["title"] or details.get("title") or "Merge", details.get("method") or "squash")
    except gc.GitCommitError as problem:
        return f"**Not merged:** {problem}"
    except Exception:
        logger.warning("[commit] the approved merge failed.", exc_info=True)
        return "**Not merged:** something went wrong talking to GitHub. Nothing was changed."
    merged = f" [`{result['sha'][:7]}`](https://github.com/{details['repo']}/commit/{result['sha']})" if re.fullmatch(r"[0-9a-f]{40}", result.get("sha") or "") else ""
    return f"**Merged** pull request #{details['number']} into `{info['base_ref']}` ({result['method']}) on `{details['repo']}`:{merged}\n\n" + catch_up_after_merge(info["base_ref"], info["head_ref"])
