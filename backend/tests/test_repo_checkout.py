import io
import os
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from backend.services import repo_checkout as rc


def _make_tarball(entries: list[tuple[str, bytes]], extra_members: list[tarfile.TarInfo] | None = None) -> io.BytesIO:
    """Builds an in-memory .tar.gz with the given (name, content) file entries, plus any
    hand-crafted TarInfo members (for symlinks/hardlinks or entries with no real content)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        for member in extra_members or []:
            tar.addfile(member)
    buf.seek(0)
    return buf


def _wrapped_repo_tarball() -> io.BytesIO:
    """Mimics GitHub's real tarball shape: everything under one top-level wrapper dir."""
    return _make_tarball([
        ("owner-repo-abc123/app.py", b"import os\n"),
        ("owner-repo-abc123/backend/services/x.py", b"def foo():\n    return 1\n"),
    ])


# ---------------------------------------------------------------------------
# _extract_tarball_safely — the security-critical part
# ---------------------------------------------------------------------------

def test_extract_rejects_path_traversal_member(tmp_path):
    tarball = _make_tarball([("../../evil.txt", b"pwned")])

    with pytest.raises(rc.RepoCheckoutError, match="unsafe tarball member path"):
        rc._extract_tarball_safely(tarball, str(tmp_path))

    # Nothing escaped the destination directory.
    escaped = tmp_path.parent.parent / "evil.txt"
    assert not escaped.exists()


def test_extract_rejects_absolute_path_member(tmp_path):
    tarball = _make_tarball([("/etc/evil.txt", b"pwned")])

    with pytest.raises(rc.RepoCheckoutError, match="unsafe tarball member path"):
        rc._extract_tarball_safely(tarball, str(tmp_path))

    assert not Path("/etc/evil.txt").exists()


def test_extract_rejects_symlink_member(tmp_path):
    link = tarfile.TarInfo(name="malicious_link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    tarball = _make_tarball([], extra_members=[link])

    with pytest.raises(rc.RepoCheckoutError, match="link member"):
        rc._extract_tarball_safely(tarball, str(tmp_path))


def test_extract_rejects_hardlink_member(tmp_path):
    link = tarfile.TarInfo(name="malicious_hardlink")
    link.type = tarfile.LNKTYPE
    link.linkname = "some_target"
    tarball = _make_tarball([], extra_members=[link])

    with pytest.raises(rc.RepoCheckoutError, match="link member"):
        rc._extract_tarball_safely(tarball, str(tmp_path))


def test_extract_aborts_past_the_total_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_CHECKOUT_MAX_EXTRACTED_BYTES", 10)
    tarball = _make_tarball([("small.txt", b"tiny"), ("big.txt", b"x" * 1000)])

    with pytest.raises(rc.RepoCheckoutError, match="extraction cap"):
        rc._extract_tarball_safely(tarball, str(tmp_path))


def test_extract_succeeds_for_a_normal_safe_tarball(tmp_path):
    tarball = _make_tarball([
        ("app.py", b"import os\n"),
        ("backend/services/x.py", b"def foo():\n    return 1\n"),
    ])

    rc._extract_tarball_safely(tarball, str(tmp_path))

    assert (tmp_path / "app.py").read_bytes() == b"import os\n"
    assert (tmp_path / "backend" / "services" / "x.py").read_bytes() == b"def foo():\n    return 1\n"


def test_extract_raises_on_corrupt_tarball(tmp_path):
    with pytest.raises(rc.RepoCheckoutError, match="failed to extract"):
        rc._extract_tarball_safely(io.BytesIO(b"not a real tarball"), str(tmp_path))


# ---------------------------------------------------------------------------
# fetch_and_extract_checkout — download + unwrap
# ---------------------------------------------------------------------------

def test_fetch_and_extract_unwraps_the_single_top_level_wrapper_dir(monkeypatch):
    fake_response = Mock()
    fake_response.status_code = 200
    fake_response.raw = _wrapped_repo_tarball()
    fake_response.close = Mock()
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: fake_response)

    handle = rc.fetch_and_extract_checkout("owner/repo", "main", {"Authorization": "Bearer x"}, "https://api.github.com")
    try:
        assert os.path.isfile(os.path.join(handle.root, "app.py"))
        assert os.path.isfile(os.path.join(handle.root, "backend", "services", "x.py"))
        # root is the unwrapped inner dir, one level below the actual tempdir allocation.
        assert handle.root != handle.tempdir
        assert Path(handle.root).parent == Path(handle.tempdir)
    finally:
        rc.cleanup_checkout(handle)


def test_fetch_and_extract_follows_redirect_and_reattaches_auth_to_codeload(monkeypatch):
    """The actual production bug this fixes: requests strips Authorization on any redirect whose
    hostname differs from the original request's, but GitHub's tarball endpoint always 302s
    cross-host to codeload.github.com — so following it automatically silently drops the token,
    and a private repo's tarball download fails every single time with no clean error, just a
    connection-level failure downstream. Must manually re-follow and re-attach the token."""
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"Location": "https://codeload.github.com/owner/repo/tar.gz/main"}
    redirect_response.close = Mock()

    final_response = Mock()
    final_response.status_code = 200
    final_response.raw = _wrapped_repo_tarball()
    final_response.close = Mock()

    calls = []

    def fake_get(url, headers=None, **kwargs):
        calls.append((url, headers, kwargs))
        if url == "https://api.github.com/repos/owner/repo/tarball/main":
            return redirect_response
        if url == "https://codeload.github.com/owner/repo/tar.gz/main":
            return final_response
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(rc.requests, "get", fake_get)

    handle = rc.fetch_and_extract_checkout(
        "owner/repo", "main", {"Authorization": "Bearer secret-token"}, "https://api.github.com"
    )
    try:
        assert os.path.isfile(os.path.join(handle.root, "app.py"))
    finally:
        rc.cleanup_checkout(handle)

    assert len(calls) == 2
    assert calls[0][2].get("allow_redirects") is False
    # The token must have been manually re-attached to the codeload request — this is the part
    # requests itself refuses to do across a host change.
    assert calls[1][1] == {"Authorization": "Bearer secret-token"}


def test_fetch_and_extract_refuses_to_follow_redirect_to_untrusted_host(monkeypatch):
    """The token must never be forwarded to wherever an arbitrary Location header points — only
    to the one confirmed GitHub download host."""
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {"Location": "https://evil.example.com/steal"}
    redirect_response.close = Mock()

    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: redirect_response)

    with pytest.raises(rc.RepoCheckoutError, match="untrusted host"):
        rc.fetch_and_extract_checkout(
            "owner/repo", "main", {"Authorization": "Bearer secret-token"}, "https://api.github.com"
        )


def test_fetch_and_extract_raises_on_redirect_with_no_location(monkeypatch):
    redirect_response = Mock()
    redirect_response.status_code = 302
    redirect_response.headers = {}
    redirect_response.close = Mock()

    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: redirect_response)

    with pytest.raises(rc.RepoCheckoutError, match="no Location header"):
        rc.fetch_and_extract_checkout("owner/repo", "main", {}, "https://api.github.com")


def test_fetch_and_extract_raises_on_non_200(monkeypatch):
    fake_response = Mock()
    fake_response.status_code = 404
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: fake_response)

    with pytest.raises(rc.RepoCheckoutError, match="404"):
        rc.fetch_and_extract_checkout("owner/repo", "main", {}, "https://api.github.com")


def test_fetch_and_extract_raises_on_request_exception(monkeypatch):
    def fake_get(*a, **k):
        raise requests.ConnectionError("network blip")

    monkeypatch.setattr(rc.requests, "get", fake_get)

    with pytest.raises(rc.RepoCheckoutError, match="tarball download failed"):
        rc.fetch_and_extract_checkout("owner/repo", "main", {}, "https://api.github.com")


def test_fetch_and_extract_cleans_up_partial_dir_on_extraction_failure(monkeypatch):
    """A malformed/malicious tarball must never leave a half-extracted temp directory behind."""
    fake_response = Mock()
    fake_response.status_code = 200
    fake_response.raw = _make_tarball([("../../evil.txt", b"pwned")])
    fake_response.close = Mock()
    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: fake_response)

    created_tempdirs = []
    orig_mkdtemp = rc.tempfile.mkdtemp

    def tracking_mkdtemp(*a, **k):
        d = orig_mkdtemp(*a, **k)
        created_tempdirs.append(d)
        return d

    monkeypatch.setattr(rc.tempfile, "mkdtemp", tracking_mkdtemp)

    with pytest.raises(rc.RepoCheckoutError):
        rc.fetch_and_extract_checkout("owner/repo", "main", {}, "https://api.github.com")

    assert len(created_tempdirs) == 1
    assert not os.path.exists(created_tempdirs[0])


# ---------------------------------------------------------------------------
# cleanup_checkout
# ---------------------------------------------------------------------------

def test_cleanup_checkout_removes_the_real_tempdir(tmp_path):
    tempdir = tmp_path / "checkout"
    tempdir.mkdir()
    (tempdir / "file.txt").write_text("hi")
    handle = rc.CheckoutHandle(root=str(tempdir), tempdir=str(tempdir))

    rc.cleanup_checkout(handle)

    assert not tempdir.exists()


def test_cleanup_checkout_is_a_noop_for_none():
    rc.cleanup_checkout(None)  # must not raise


def test_cleanup_checkout_is_safe_on_an_already_removed_directory(tmp_path):
    tempdir = tmp_path / "already_gone"
    handle = rc.CheckoutHandle(root=str(tempdir), tempdir=str(tempdir))

    rc.cleanup_checkout(handle)  # must not raise even though it never existed
