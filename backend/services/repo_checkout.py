"""Per-turn local checkout of a GitHub repo via its tarball endpoint.

Replaces N individual Contents/Tree API calls with one tarball download + local disk reads for
the read-heavy tool_agent_node actions (list_repo_tree, read_repo_file, find_file,
search_literal, and — for free, since they already take a fetch_content callable — the
architecture map and README-first context). This is a pure speed optimization: any failure here
(rate limit, oversized repo, network blip, a malformed tarball) must be caught by the caller and
degrade to the existing GitHub API path, never surface as a new user-facing failure mode.

diff_branches/list_commits/list_pull_requests (real git history/PR metadata) and
search_code/trace_symbol (GitHub's hosted search index) stay on the real API regardless — a
tarball is a snapshot of one ref with no git history or search index, so those genuinely need it.

Its own service module, not a "utils" file — mirrors why react_loop.py got its own file (see
docs/coding-agent-roadmap.md, Section 13): this manages real lifecycle state (a temp directory on
disk), not just plain stateless helpers.
"""
import shutil
import tarfile
import tempfile
import urllib.parse
from pathlib import Path
from typing import NamedTuple

import requests

_CHECKOUT_TIMEOUT_SECONDS = 30
# GitHub's tarball endpoint always 302s cross-host to codeload.github.com. requests strips the
# Authorization header on any redirect whose hostname differs from the original request's (a
# genuine security feature — see requests.sessions.Session.should_strip_auth) — which means a
# private repo's tarball fetch fails 100% of the time if the redirect is followed automatically,
# since codeload then sees an unauthenticated request. The manual-redirect handling below
# re-attaches the token, but only once the redirect target is confirmed to be exactly this one
# known GitHub download host — never forward credentials to wherever an arbitrary Location header
# points. (api_base is always the public "https://api.github.com" in this app — see agent_workflow
# .py — so there's no GitHub Enterprise host to account for here.)
_TRUSTED_REDIRECT_HOST = "codeload.github.com"
# Generous for any real source repo — this bounds a malicious or genuinely oversized tarball, not
# a realistic one. Checked against cumulative REAL bytes written during extraction, not each
# member's declared header size (which a malformed/malicious tarball can simply lie about).
_CHECKOUT_MAX_EXTRACTED_BYTES = 200_000_000


class RepoCheckoutError(Exception):
    """Raised on any failure to fetch or safely extract a repo checkout. Callers must catch this
    and fall back to the existing GitHub API path — never let it propagate as a hard failure."""


class CheckoutHandle(NamedTuple):
    # GitHub's tarball wraps everything in one top-level "<owner>-<repo>-<sha>/" directory —
    # `root` is that directory unwrapped, so callers see real repo-relative paths directly.
    root: str
    # The actual tempfile.mkdtemp() allocation one level up from `root` — what cleanup_checkout
    # actually removes. Kept separate from `root` since `root` alone isn't the real allocation
    # boundary once the wrapper directory is unwrapped.
    tempdir: str


def fetch_and_extract_checkout(repo: str, ref: str, headers: dict, api_base: str) -> CheckoutHandle:
    """Downloads GET {api_base}/repos/{repo}/tarball/{ref} (same headers/auth every other GitHub
    call in this app already builds) and extracts it into a fresh, isolated temp directory.
    Streamed straight into tarfile via requests' raw response stream — never buffers the whole
    tarball in memory. Raises RepoCheckoutError on any failure; see the module docstring."""
    url = f"{api_base}/repos/{repo}/tarball/{ref}"
    try:
        res = requests.get(
            url, headers=headers, stream=True, timeout=_CHECKOUT_TIMEOUT_SECONDS, allow_redirects=False
        )
        if res.status_code in (301, 302, 303, 307, 308):
            location = res.headers.get("Location")
            res.close()
            if not location:
                raise RepoCheckoutError("tarball redirect response had no Location header")
            redirect_host = urllib.parse.urlparse(location).hostname or ""
            if redirect_host != _TRUSTED_REDIRECT_HOST:
                raise RepoCheckoutError(f"refusing to follow tarball redirect to untrusted host: {redirect_host!r}")
            # Re-attach the same headers (including Authorization) ourselves — see the module
            # comment above _TRUSTED_REDIRECT_HOST for why requests won't do this safely on its
            # own, and why it's safe to do manually now that the host is verified.
            res = requests.get(location, headers=headers, stream=True, timeout=_CHECKOUT_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise RepoCheckoutError(f"tarball download failed: {e}") from e
    if res.status_code != 200:
        raise RepoCheckoutError(f"tarball download failed ({res.status_code})")

    tempdir = tempfile.mkdtemp(prefix="saapp_checkout_")
    try:
        _extract_tarball_safely(res.raw, tempdir)
    except Exception:
        # Never leave a half-extracted directory behind on failure.
        shutil.rmtree(tempdir, ignore_errors=True)
        raise
    finally:
        res.close()

    entries = list(Path(tempdir).iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return CheckoutHandle(root=str(entries[0]), tempdir=tempdir)
    return CheckoutHandle(root=tempdir, tempdir=tempdir)


def _extract_tarball_safely(fileobj, dest_dir: str) -> None:
    """The security-critical part — defense in depth rather than trusting any one layer. Every
    member's resolved path must stay inside dest_dir (the classic tar-slip footgun: a malicious
    or malformed tarball entry named "../../etc/passwd"), no symlink/hardlink members are ever
    extracted (no legitimate reason a GitHub source tarball contains one pointing outside the
    checkout), and cumulative real extracted bytes are capped as extraction proceeds. Also passes
    filter="data" (PEP 706) as an additional layer — available on this repo's local Python 3.12,
    and the production deploy's default on Python 3.14 — but the manual checks above are the ones
    actually relied on, not the filter default."""
    dest_root = Path(dest_dir).resolve()
    total_extracted = 0
    try:
        with tarfile.open(fileobj=fileobj, mode="r|gz") as tar:
            for member in tar:
                member_path = (dest_root / member.name).resolve()
                if not member_path.is_relative_to(dest_root):
                    raise RepoCheckoutError(f"refusing to extract unsafe tarball member path: {member.name}")
                if member.issym() or member.islnk():
                    raise RepoCheckoutError(f"refusing to extract link member: {member.name}")
                if member.isfile():
                    total_extracted += member.size
                    if total_extracted > _CHECKOUT_MAX_EXTRACTED_BYTES:
                        raise RepoCheckoutError(
                            f"tarball exceeds the {_CHECKOUT_MAX_EXTRACTED_BYTES}-byte extraction cap"
                        )
                tar.extract(member, path=dest_dir, filter="data")
    except tarfile.TarError as e:
        raise RepoCheckoutError(f"failed to extract tarball: {e}") from e


def cleanup_checkout(handle: CheckoutHandle | None) -> None:
    """Removes the checkout's real temp allocation. Safe to call with None, or with a handle
    whose directory is already gone."""
    if handle is None:
        return
    shutil.rmtree(handle.tempdir, ignore_errors=True)
