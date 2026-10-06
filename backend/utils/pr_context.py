import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# Turns GitHub's "files changed" data into the evidence block a PR description or review is written
# from. The two writers (the webhook's PR overview comment and the PR Sonic drafts on request) used to
# see only the first ten files in API order, lockfiles and generated output included, with an
# unbounded patch each, or just a bare list of file names, so the model had little to ground a
# description in and the little it had was often noise. Pure functions, no I/O.

_NOISE_PATTERNS = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "uv.lock",
    "composer.lock", "Gemfile.lock", "Cargo.lock", "go.sum", "*.min.js", "*.min.css", "*.map",
    "*.svg", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.woff", "*.woff2", "*.pdf",
    "dist/*", "build/*", "node_modules/*", "vendor/*", "*/dist/*", "*/build/*", "*/node_modules/*",
    "*/__snapshots__/*", "*.snap", "*.pyc",
]
_TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$", re.IGNORECASE)
_DOC_EXTENSIONS = (".md", ".rst", ".txt")

# Sized from a real PR: at 1.8k per file / 14k total, five of seven files were cut mid-patch and the
# overview came back vague about them. The old unbounded prompt was ~65k characters, which was
# wasteful on lockfiles but never starved a real file.
DEFAULT_MAX_FILES = 30
DEFAULT_PER_FILE_CHARS = 6000
DEFAULT_TOTAL_CHARS = 36000
_MAX_BODY_CHARS = 1500


def is_noise_file(path: str) -> bool:
    """Lockfiles, minified or generated output, images and the like: churn that says nothing about
    what a PR means, and that would otherwise crowd real changes out of the prompt's budget."""
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in _NOISE_PATTERNS)


def is_test_file(path: str) -> bool:
    return bool(_TEST_RE.search(path))


def _kind_rank(path: str) -> int:
    """Source first, then tests, then docs and other files: what a reviewer reads in that order."""
    if is_test_file(path):
        return 1
    if path.lower().endswith(_DOC_EXTENSIONS):
        return 2
    return 0


@dataclass
class DiffContext:
    text: str
    shown: int = 0
    total: int = 0
    noise_skipped: List[str] = field(default_factory=list)
    omitted: List[str] = field(default_factory=list)  # real files left out for budget
    omitted_detail: List[str] = field(default_factory=list)  # the same, with each file's +/- counts
    truncated_files: List[str] = field(default_factory=list)  # shown, but with the patch cut short
    test_files: List[str] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True when every real (non-noise) file was shown whole."""
        return not self.omitted and not self.truncated_files


def _stat(f: Dict[str, Any]) -> str:
    return f"+{int(f.get('additions') or 0)}/-{int(f.get('deletions') or 0)}"


def build_diff_context(
    files: Iterable[Dict[str, Any]],
    max_files: int = DEFAULT_MAX_FILES,
    per_file_chars: int = DEFAULT_PER_FILE_CHARS,
    total_chars: int = DEFAULT_TOTAL_CHARS,
) -> DiffContext:
    """The evidence block for a PR, from GitHub file entries ({filename, status, additions,
    deletions, patch}). Real files are ordered source, tests, docs and then by size of change; noise is
    named but not shown; each patch is cut to per_file_chars and the whole to total_chars, and what was
    cut or left out is recorded so the writer (and the reader) is told rather than left to assume the
    PR is smaller than it is."""
    files = [f for f in files if isinstance(f, dict) and f.get("filename")]
    ctx = DiffContext(text="", total=len(files))
    real = []
    for f in files:
        if is_noise_file(f["filename"]):
            ctx.noise_skipped.append(f["filename"])
        else:
            real.append(f)
    real.sort(key=lambda f: (_kind_rank(f["filename"]), -(int(f.get("additions") or 0) + int(f.get("deletions") or 0)), f["filename"]))
    ctx.test_files = [f["filename"] for f in real if is_test_file(f["filename"])]
    ctx.source_files = [f["filename"] for f in real if _kind_rank(f["filename"]) == 0]

    blocks: List[str] = []
    used = 0
    for f in real:
        name = f["filename"]
        if len(blocks) >= max_files or used >= total_chars:
            ctx.omitted.append(name)
            ctx.omitted_detail.append(f"{name} ({_stat(f)})")
            continue
        patch = f.get("patch")
        header = f"File: {name} ({f.get('status', 'modified')}, {_stat(f)})"
        if not patch:
            blocks.append(f"{header}\n(no text patch: binary, renamed without edits, or too large for GitHub to include)")
            continue
        budget = min(per_file_chars, max(total_chars - used, 0))
        if len(patch) > budget:
            patch = patch[:budget].rstrip() + "\n... [patch truncated]"
            ctx.truncated_files.append(name)
        block = f"{header}\n```diff\n{patch}\n```"
        blocks.append(block)
        used += len(patch)
    ctx.shown = len(blocks)

    parts = ["\n\n".join(blocks)] if blocks else ["(no readable text changes)"]
    if ctx.omitted:
        parts.append(f"[{len(ctx.omitted)} more changed file(s), diff not included: " + ", ".join(ctx.omitted_detail[:15]) + (", ..." if len(ctx.omitted) > 15 else "") + "]")
    if ctx.noise_skipped:
        parts.append(f"[{len(ctx.noise_skipped)} lockfile/generated/asset file(s) changed and left out: " + ", ".join(ctx.noise_skipped[:8]) + (", ..." if len(ctx.noise_skipped) > 8 else "") + "]")
    ctx.text = "\n\n".join(parts)
    return ctx


def commit_subjects(commits: Iterable[Dict[str, Any]], limit: int = 15) -> List[str]:
    """First line of each commit message, merge commits dropped (they describe nothing)."""
    subjects = []
    for c in commits:
        message = ((c.get("commit") or {}).get("message") or c.get("message") or "").strip()
        subject = message.split("\n", 1)[0].strip()
        if subject and not subject.lower().startswith("merge "):
            subjects.append(subject)
    return subjects[:limit]


def build_pr_header(pr: Optional[Dict[str, Any]], subjects: List[str], ctx: DiffContext) -> str:
    """What a reviewer knows before reading any code: the author's own stated intent, the size, the
    commit history and whether tests moved with the source, so claims about intent and about missing
    tests can be grounded instead of guessed."""
    lines = []
    if pr:
        lines.append(f"PR #{pr.get('number')}: {pr.get('title', '').strip()}")
        author = (pr.get("user") or {}).get("login")
        base, head = (pr.get("base") or {}).get("ref"), (pr.get("head") or {}).get("ref")
        meta = []
        if author:
            meta.append(f"by @{author}")
        if base and head:
            meta.append(f"{head} -> {base}")
        if pr.get("changed_files") is not None:
            meta.append(f"{pr.get('changed_files')} files, +{pr.get('additions', 0)}/-{pr.get('deletions', 0)}")
        if pr.get("draft"):
            meta.append("draft")
        if meta:
            lines.append("; ".join(meta))
        body = (pr.get("body") or "").strip()
        if body:
            lines.append("Author's description:\n" + (body[:_MAX_BODY_CHARS] + (" ... [truncated]" if len(body) > _MAX_BODY_CHARS else "")))
        else:
            lines.append("Author's description: (none)")
    if subjects:
        lines.append(f"Commits ({len(subjects)} shown):\n- " + "\n- ".join(subjects))
    if ctx.source_files:
        lines.append(
            "Tests changed alongside the source: "
            + (", ".join(ctx.test_files[:8]) if ctx.test_files else "NONE (no test file is part of this PR)")
        )
    return "\n".join(lines)


def fit_note(ctx: DiffContext) -> str:
    """One line telling the OVERVIEW writer how much of the PR it was shown. What it could not read is
    listed for the reader by coverage_note (in code, not by the model), so the model is told not to
    narrate it."""
    if ctx.complete:
        return f"You were shown the full diff of all {ctx.shown} reviewed file(s)."
    cut = len(ctx.omitted) + len(ctx.truncated_files)
    return (
        f"You were shown only part of this PR ({cut} file(s) omitted or cut short). Never describe code you "
        "were not shown, and do not write about what you could or could not see: a note listing the files "
        "that were not fully covered is added to your comment automatically."
    )


def draft_fit_note(ctx: DiffContext) -> str:
    """The same, for a PR description the user will publish under their name: nothing about the
    tooling's limits may end up in the PR text itself."""
    if ctx.complete:
        return ""
    return (
        "Some changed files are listed without their full diff. Describe those from their names, their size "
        "and the commit messages only. Never write in the title or description that any part of the diff was "
        "unavailable, truncated, omitted or not shown."
    )


def coverage_note(ctx: DiffContext) -> str:
    """A footnote for the posted overview naming what the model could not fully read, so a reader knows
    where the overview is thin. Empty when it saw everything."""
    if ctx.complete:
        return ""
    parts = []
    if ctx.truncated_files:
        parts.append("diff cut short: " + ", ".join(f"`{n}`" for n in ctx.truncated_files[:8]) + (", ..." if len(ctx.truncated_files) > 8 else ""))
    if ctx.omitted:
        parts.append("not read: " + ", ".join(f"`{n}`" for n in ctx.omitted[:8]) + (", ..." if len(ctx.omitted) > 8 else ""))
    return "<sub>Not fully covered by this overview (" + "; ".join(parts) + ").</sub>"


def unescape_flattened_newlines(text: str) -> str:
    """Repairs a drafted PR title/body whose line breaks arrived as the two characters backslash and n,
    which GitHub shows literally ("Summary\\nUpdates...### Changes\\n- ..." on one line). Only applied
    when the text has no real line break at all, so a body that legitimately mentions "\\n" inside code
    is left alone."""
    if not text or "\n" in text.strip() or chr(92) + "n" not in text:
        return text
    return text.replace(chr(92) + "r" + chr(92) + "n", "\n").replace(chr(92) + "n", "\n").replace(chr(92) + "t", "\t")


def build_review_prompt(repo: str, pr: Optional[Dict[str, Any]], commits: Iterable[Dict[str, Any]], files: Iterable[Dict[str, Any]]):
    """(prompt, DiffContext) for the PR overview/review, used by both the webhook comment and the
    in-chat review so they describe a PR from the same evidence in the same way."""
    from backend.components.constraints import PR_REVIEW_PROMPT

    ctx = build_diff_context(files)
    header = build_pr_header(pr if isinstance(pr, dict) else None, commit_subjects(commits or []), ctx)
    prompt = PR_REVIEW_PROMPT.format(
        repo=repo,
        pr_header=header or "(no pull request details available)",
        formatted_diffs=ctx.text,
        fit_note=fit_note(ctx),
    )
    return prompt, ctx
