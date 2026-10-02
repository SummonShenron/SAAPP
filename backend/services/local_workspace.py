"""A user's connected local folder, mirrored on the server as a read-only snapshot.

The browser (see local/src/localWorkspace.ts) reads the folder the user picked via the File System
Access API and uploads its text files here in batches; tool_agent_node then reads this snapshot
instead of downloading the GitHub tarball, so Sonic sees the user's real working tree — uncommitted
changes included — through the exact same read_repo_file / list_repo_tree / find_file /
search_literal code paths it already uses for a checkout (see repo_checkout.CheckoutHandle).

Never writes anything back to the user's disk — this module only ever touches its own temp
directory. Its own service module rather than a utils file for the same reason repo_checkout.py is:
it manages real lifecycle state on disk (per-user directories with a TTL).

Every public function is sync and blocking; async callers run them via asyncio.to_thread.
"""
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.repo_checkout import CheckoutHandle

logger = logging.getLogger("SASS Logger")

WORKSPACE_BASE_DIR = Path(tempfile.gettempdir()) / "saapp_local_workspaces"
MAX_FILE_BYTES = 500_000
MAX_TOTAL_BYTES = int(os.getenv("LOCAL_WORKSPACE_MAX_TOTAL_BYTES", str(60_000_000)))
MAX_FILE_COUNT = int(os.getenv("LOCAL_WORKSPACE_MAX_FILES", "8000"))
WORKSPACE_TTL_SECONDS = float(os.getenv("LOCAL_WORKSPACE_TTL_HOURS", "12")) * 3600
_MAX_PATH_CHARS = 500
# Bumped whenever how files are stored changes. A snapshot written by an older format is treated as
# not connected, and a delta sync onto one wipes it first, so the browser's next sync (which checks
# the server's state before sending a delta) rewrites everything cleanly. v2: files are written as
# exact bytes — v1 wrote text, which on a Windows server turned every CRLF into CR CR LF and made
# each line read back followed by a blank one.
SNAPSHOT_FORMAT_VERSION = 2
_MAX_REJECTIONS_REPORTED = 20

# These are the sub values get_current_user returns for the shared guest sandbox identities.
# Every guest shares one identity, so a per-user snapshot for one would be visible to all of them.
_GUEST_USERNAMES = {"guest-recruiter@example.com", "guest_bty@bty.local", "guest_bty", "guest"}

# Defense in depth: the browser already skips all of this before uploading, but this module never
# trusts the client — secrets and vendored/build directories are refused here regardless.
_SKIPPED_DIR_NAMES = {
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", ".next", "coverage",
}
_BLOCKED_FILE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", ".npmrc", ".pypirc", ".netrc", "credentials.json", "secrets.json", "secrets.yaml",
    "secrets.yml", "secrets.toml", "*.keystore",
)
_ALLOWED_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")

_lock = threading.Lock()


def is_guest_username(username: Optional[str]) -> bool:
    return (username or "").strip().lower() in _GUEST_USERNAMES


def _workspace_dir(username: str) -> Path:
    return WORKSPACE_BASE_DIR / hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()[:32]


def _files_dir(username: str) -> Path:
    return _workspace_dir(username) / "files"


def _manifest_path(username: str) -> Path:
    return _workspace_dir(username) / "manifest.json"


def safe_relative_path(raw: Any) -> Optional[str]:
    """The normalized posix relative path, or None if it could reach outside the snapshot root
    (absolute, drive-lettered, backslashed, containing ./.. segments, NULs, or absurdly long)."""
    if not isinstance(raw, str) or not raw or len(raw) > _MAX_PATH_CHARS:
        return None
    if "\x00" in raw or "\\" in raw or raw.startswith("/") or ":" in raw:
        return None
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _rejection_reason(path: str) -> Optional[str]:
    """Why this (already-safe) path must not be stored, or None if it's fine."""
    parts = path.split("/")
    if any(part in _SKIPPED_DIR_NAMES for part in parts[:-1]):
        return "in a skipped directory"
    name = parts[-1].lower()
    if name.endswith(_ALLOWED_ENV_TEMPLATE_SUFFIXES):
        return None
    if any(fnmatch.fnmatch(name, pattern) for pattern in _BLOCKED_FILE_PATTERNS):
        return "looks like a secret/credential file"
    return None


def path_rejection(path: str) -> Optional[str]:
    """Public form of _rejection_reason for callers (local_edits.py) that need to apply the same
    never-touch rules to a path before proposing a write to it."""
    return _rejection_reason(path)


def _read_manifest(username: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_manifest_path(username).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _current_manifest(username: str) -> Optional[Dict[str, Any]]:
    """The manifest, but only if it was written by this version of the storage format."""
    manifest = _read_manifest(username)
    if manifest and manifest.get("version") == SNAPSHOT_FORMAT_VERSION:
        return manifest
    return None


def _write_manifest(username: str, manifest: Dict[str, Any]) -> None:
    _manifest_path(username).write_text(json.dumps(manifest), encoding="utf-8")


def clear_workspace(username: str) -> None:
    with _lock:
        shutil.rmtree(_workspace_dir(username), ignore_errors=True)


def apply_sync(
    username: str,
    name: str,
    files: List[Dict[str, Any]],
    deleted: List[str],
    reset: bool = False,
) -> Dict[str, Any]:
    """Applies one batch of an upload: `reset` wipes the existing snapshot first (the initial
    connect / a full re-scan), `files` are {"path","content"} text files to write, `deleted` are
    paths to remove. Per-file problems are reported back and skipped, never raised, so one bad
    path can't abort the whole batch. Returns {"accepted", "rejected": [...], "file_count",
    "total_bytes"}."""
    if not username or is_guest_username(username):
        raise PermissionError("local workspaces are only available to signed-in users")

    with _lock:
        existing = _current_manifest(username)
        if reset or existing is None:
            # Also covers a snapshot left by an older storage format (see SNAPSHOT_FORMAT_VERSION).
            shutil.rmtree(_workspace_dir(username), ignore_errors=True)
        root = _files_dir(username)
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        manifest = (existing if not reset else None) or {"files": {}}
        index: Dict[str, int] = manifest.get("files", {})
        rejected: List[Dict[str, str]] = []
        accepted = 0

        def _reject(path: Any, reason: str) -> None:
            if len(rejected) < _MAX_REJECTIONS_REPORTED:
                rejected.append({"path": str(path)[:_MAX_PATH_CHARS], "reason": reason})

        for raw_path in deleted or []:
            path = safe_relative_path(raw_path)
            if path is None:
                continue
            target = (resolved_root / path).resolve()
            if target.is_relative_to(resolved_root):
                try:
                    target.unlink()
                except OSError:
                    pass
            index.pop(path, None)

        for entry in files or []:
            raw_path = entry.get("path") if isinstance(entry, dict) else None
            content = entry.get("content") if isinstance(entry, dict) else None
            path = safe_relative_path(raw_path)
            if path is None:
                _reject(raw_path, "unsafe path")
                continue
            if not isinstance(content, str):
                _reject(path, "content is not text")
                continue
            reason = _rejection_reason(path)
            if reason:
                _reject(path, reason)
                continue
            size = len(content.encode("utf-8"))
            if size > MAX_FILE_BYTES:
                _reject(path, "file too large")
                continue
            if path not in index and len(index) >= MAX_FILE_COUNT:
                _reject(path, "too many files")
                continue
            if sum(index.values()) - index.get(path, 0) + size > MAX_TOTAL_BYTES:
                _reject(path, "snapshot size limit reached")
                continue
            target = (resolved_root / path).resolve()
            if not target.is_relative_to(resolved_root):
                _reject(path, "unsafe path")
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exact bytes, not write_text: text mode translates newlines per platform.
                target.write_bytes(content.encode("utf-8"))
            except OSError:
                _reject(path, "could not be written")
                continue
            index[path] = size
            accepted += 1

        manifest = {
            "version": SNAPSHOT_FORMAT_VERSION,
            "name": (name or "local folder")[:200],
            "synced_at": time.time(),
            "files": index,
        }
        _write_manifest(username, manifest)
        return {
            "accepted": accepted,
            "rejected": rejected,
            "file_count": len(index),
            "total_bytes": sum(index.values()),
        }


def workspace_status(username: Optional[str]) -> Dict[str, Any]:
    """{"connected": False} or the folder's name, size and sync time. An expired snapshot is
    deleted on the way past, so a stale copy of someone's code never lingers on the server."""
    if not username or is_guest_username(username):
        return {"connected": False}
    manifest = _current_manifest(username)
    if not manifest or not manifest.get("files"):
        return {"connected": False}
    synced_at = float(manifest.get("synced_at", 0))
    if time.time() - synced_at > WORKSPACE_TTL_SECONDS:
        logger.info("[local_workspace] snapshot expired; removing it.")
        clear_workspace(username)
        return {"connected": False}
    files = manifest["files"]
    return {
        "connected": True,
        "name": manifest.get("name", "local folder"),
        "file_count": len(files),
        "total_bytes": sum(files.values()),
        "synced_at": synced_at,
    }


def get_workspace_handle(username: Optional[str]) -> Optional[CheckoutHandle]:
    """The snapshot as a CheckoutHandle for tool_agent_node's local-read path, or None when the
    user has no (unexpired) connected folder. `owned=False` so cleanup_checkout never deletes it —
    it outlives any single turn."""
    if not workspace_status(username)["connected"]:
        return None
    return CheckoutHandle(root=str(_files_dir(username)), tempdir=str(_workspace_dir(username)), owned=False)
