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
import base64
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
from backend.utils import docx_edit

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
    "chroma_db", "index-db",
    # Claude Code keeps full duplicate checkouts of the repo under .claude/worktrees. Snapshotting
    # them doubles the file count (crowding real files out of search_literal's scan cap) and puts a
    # second, differently-numbered copy of every file in front of the model.
    ".claude",
}
_BLOCKED_FILE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa", "id_dsa", "id_ecdsa",
    "id_ed25519", ".npmrc", ".pypirc", ".netrc", "credentials.json", "secrets.json", "secrets.yaml",
    "secrets.yml", "secrets.toml", "*.keystore",
)
_ALLOWED_ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")

_lock = threading.Lock()                      # guards the registry of per-person locks below
_user_locks: Dict[str, threading.Lock] = {}
STAGING_TTL_SECONDS = 30 * 60                 # an upload nobody finishes is dropped after this
TOUCH_EVERY_SECONDS = 5 * 60


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


def _docx_outline(path: str, encoded: str) -> tuple:
    """(outline text, "") for a base64 .docx, or (None, why it was not taken)."""
    if not path.lower().endswith(".docx"):
        return None, "only .docx files can be sent as documents"
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None, "could not be read"
    try:
        return docx_edit.outline_text(data), ""
    except docx_edit.DocxEditError as error:
        return None, str(error)[:120]


class StaleUpload(Exception):
    """A batch belongs to an upload that has been replaced (another tab started a newer one) or expired; the browser starts over."""


def _user_lock(username: str) -> threading.Lock:
    """One lock per person, so one person's upload never makes another's wait (a single global lock was held for the whole of each batch)."""
    key = hashlib.sha256(username.strip().lower().encode("utf-8")).hexdigest()[:32]
    with _lock:
        lock = _user_locks.get(key)
        if lock is None:
            lock = _user_locks[key] = threading.Lock()
        return lock


def clear_workspace(username: str) -> None:
    with _user_lock(username):
        shutil.rmtree(_workspace_dir(username), ignore_errors=True)


def _staging_dir(username: str) -> Path:
    return _workspace_dir(username) / "staging"


def _read_staging(username: str) -> Optional[Dict[str, Any]]:
    try:
        meta = json.loads((_staging_dir(username) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(meta.get("started_at", 0)) > STAGING_TTL_SECONDS:
        shutil.rmtree(_staging_dir(username), ignore_errors=True)       # an upload nobody finished: its half-copy is not kept
        return None
    return meta


def _write_batch(resolved_root: Path, index: Dict[str, int], files: List[Dict[str, Any]], deleted: List[str]) -> Dict[str, Any]:
    """Writes one batch into the tree under `resolved_root` and its `index` (path -> size), in place. Per-file problems are reported back and skipped, never raised, so one bad
    path can't abort the whole batch. Returns {"accepted", "rejected"}."""
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
        encoded = entry.get("content_base64") if isinstance(entry, dict) else None
        if isinstance(encoded, str) and content is None:
            # A Word document: only a numbered text outline of it is kept (so Sonic can read it and propose edits), never the file itself.
            content, problem = _docx_outline(path, encoded)
            if content is None:
                _reject(path, problem)
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
    return {"accepted": accepted, "rejected": rejected}


def _result(accepted: int, rejected: List[Dict[str, str]], index: Dict[str, int], **extra: Any) -> Dict[str, Any]:
    return {"accepted": accepted, "rejected": rejected, "file_count": len(index), "total_bytes": sum(index.values()), **extra}


def apply_sync(
    username: str,
    name: str,
    files: List[Dict[str, Any]],
    deleted: List[str],
    reset: bool = False,
    upload_id: Optional[str] = None,
    commit: bool = False,
) -> Dict[str, Any]:
    """Applies one batch of an upload. `files` are {"path","content"} text files to write, `deleted` are paths to remove. Per-file problems are reported back and skipped,
    never raised. Returns {"accepted", "rejected": [...], "file_count", "total_bytes"}.

    A FULL upload (the initial connect, a re-scan, or a recovery after the server lost its copy) carries an `upload_id`: its batches are written into a STAGING copy and replace
    the live snapshot only when the final batch says `commit`, in one swap. Until then the live snapshot is untouched and still serves chat, and a failed or abandoned upload
    leaves nothing half-done (it used to wipe the working copy with the first batch). A batch whose upload was replaced by a newer one raises StaleUpload.

    Without an `upload_id` (a small delta of what changed since the last sync) the batch is applied to the live snapshot directly; a `reset` there wipes it first, as before."""
    if not username or is_guest_username(username):
        raise PermissionError("local workspaces are only available to signed-in users")
    with _user_lock(username):
        if upload_id:
            return _apply_staged(username, name, files, deleted, reset, upload_id, commit)
        return _apply_direct(username, name, files, deleted, reset)


def _apply_direct(username: str, name: str, files: List[Dict[str, Any]], deleted: List[str], reset: bool) -> Dict[str, Any]:
    existing = _current_manifest(username)
    if reset or existing is None:
        # Also covers a snapshot left by an older storage format (see SNAPSHOT_FORMAT_VERSION).
        shutil.rmtree(_workspace_dir(username), ignore_errors=True)
    root = _files_dir(username)
    root.mkdir(parents=True, exist_ok=True)
    manifest = (existing if not reset else None) or {"files": {}}
    index: Dict[str, int] = manifest.get("files", {})
    outcome = _write_batch(root.resolve(), index, files, deleted)
    now = time.time()
    _write_manifest(username, {"version": SNAPSHOT_FORMAT_VERSION, "name": (name or "local folder")[:200], "synced_at": now, "last_used_at": now, "files": index})
    logger.info("[local_workspace] sync applied: accepted=%d rejected=%d file_count=%d", outcome["accepted"], len(outcome["rejected"]), len(index))
    return _result(outcome["accepted"], outcome["rejected"], index)


def _apply_staged(username: str, name: str, files: List[Dict[str, Any]], deleted: List[str], reset: bool, upload_id: str, commit: bool) -> Dict[str, Any]:
    staging = _staging_dir(username)
    meta = _read_staging(username)
    if reset:
        shutil.rmtree(staging, ignore_errors=True)           # a newer upload replaces any earlier unfinished one
        meta = {"id": upload_id, "name": (name or "local folder")[:200], "started_at": time.time(), "files": {}}
    elif not meta or meta.get("id") != upload_id:
        raise StaleUpload("That upload was replaced by a newer one, or expired.")
    (staging / "files").mkdir(parents=True, exist_ok=True)
    index: Dict[str, int] = meta["files"]
    outcome = _write_batch((staging / "files").resolve(), index, files, deleted)
    (staging / "meta.json").write_text(json.dumps({**meta, "files": index}), encoding="utf-8")
    if commit:
        _swap_in(username, meta, index)
        logger.info("[local_workspace] upload committed: accepted=%d rejected=%d file_count=%d", outcome["accepted"], len(outcome["rejected"]), len(index))
    else:
        logger.info("[local_workspace] upload batch staged: accepted=%d rejected=%d file_count=%d", outcome["accepted"], len(outcome["rejected"]), len(index))
    return _result(outcome["accepted"], outcome["rejected"], index, committed=bool(commit))


def _swap_in(username: str, meta: Dict[str, Any], index: Dict[str, int]) -> None:
    """Replaces the live snapshot with the staged one: the staged files become `files`, the manifest is written, the old copy and the staging area are removed."""
    workspace = _workspace_dir(username)
    live, old = workspace / "files", workspace / "files.old"
    shutil.rmtree(old, ignore_errors=True)
    if live.exists():
        os.replace(live, old)
    os.replace(_staging_dir(username) / "files", live)
    now = time.time()
    _write_manifest(username, {"version": SNAPSHOT_FORMAT_VERSION, "name": meta.get("name", "local folder"), "synced_at": now, "last_used_at": now, "files": index})
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(_staging_dir(username), ignore_errors=True)


def _touch(username: str) -> None:
    """Notes that the snapshot was just used, so a copy someone is actively chatting with does not expire 12 hours after its last UPLOAD. Written at most every few minutes."""
    with _user_lock(username):
        manifest = _current_manifest(username)
        if manifest and time.time() - float(manifest.get("last_used_at", 0)) > TOUCH_EVERY_SECONDS:
            manifest["last_used_at"] = time.time()
            try:
                _write_manifest(username, manifest)
            except OSError:
                pass


def workspace_status(username: Optional[str]) -> Dict[str, Any]:
    """{"connected": False} or the folder's name, size and sync time. An expired snapshot is
    deleted on the way past, so a stale copy of someone's code never lingers on the server."""
    if not username or is_guest_username(username):
        return {"connected": False}
    manifest = _current_manifest(username)
    if not manifest or not manifest.get("files"):
        return {"connected": False}
    synced_at = float(manifest.get("synced_at", 0))
    if time.time() - max(synced_at, float(manifest.get("last_used_at", 0))) > WORKSPACE_TTL_SECONDS:
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
    _touch(username)
    return CheckoutHandle(root=str(_files_dir(username)), tempdir=str(_workspace_dir(username)), owned=False)
