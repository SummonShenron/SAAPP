"""A memory gauge for the server: what is really using the container's memory, for the admin's Sonic Health page.

WHY. The sandbox test run was refused with "69 MB free, 200 MB needed" (backend/services/sandbox_tests.py). That figure is the container's limit minus its `memory.current`, and
`memory.current` counts the page cache: every file the server writes or reads (the connected folder's copy, the sandbox's copy of it) stays cached until the kernel needs the space,
and the kernel gives it back under pressure instead of killing anything. So "69 MB free" may be mostly cache that is free to reclaim, or it may be real use. The two look the same from
the old number, and guessing which was how the 200 MB limit got there. This reports them apart.

WHAT IT REPORTS (sizes and counts only: no person, no text, no file name):
- the container: its limit, what it is using, and how that splits into program memory (anon), file cache (and how much of the cache is inactive, so reclaimable);
- the headroom the sandbox check sees today, and the headroom with the reclaimable cache taken out (the "working set", the same subtraction `docker stats` makes);
- this process: its resident size now and at its peak;
- what the app holds on purpose: conversations and messages kept in memory, and the connected folders' files on disk (and whether the temp directory is RAM-backed, in which case they count as memory too).

Everything is read from /proc and /sys/fs/cgroup, so on a developer's machine most fields are None, which the page shows as "not available here".
"""
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("SASS Logger")

MAX_WALK_FILES = 50_000          # the walk of the folders' files stops here, so a huge temp directory cannot make the gauge itself slow


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _mb(value: Any) -> Optional[float]:
    try:
        return round(float(value) / 1e6, 1)
    except (TypeError, ValueError):
        return None


def _int(text: Optional[str]) -> Optional[int]:
    return int(text) if text and text.isdigit() else None


def _stat(text: Optional[str]) -> Dict[str, int]:
    """memory.stat as {key: bytes}. Pure."""
    out: Dict[str, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def container(read: Callable[[str], Optional[str]] = _read) -> Dict[str, Optional[float]]:
    """The container's memory in MB, from cgroup v2 or v1: limit, used, program memory (anon), file cache, the inactive part of it, and the working set (used minus inactive cache).
    Every field is None when the server is not in a limited container. Pure apart from `read`."""
    empty = {"limit_mb": None, "used_mb": None, "anon_mb": None, "file_cache_mb": None, "inactive_cache_mb": None, "working_set_mb": None}
    limit, used = _int(read("/sys/fs/cgroup/memory.max")), _int(read("/sys/fs/cgroup/memory.current"))
    stat = _stat(read("/sys/fs/cgroup/memory.stat"))
    anon, cache, inactive = stat.get("anon"), stat.get("file"), stat.get("inactive_file")
    if limit is None or used is None:
        limit, used = _int(read("/sys/fs/cgroup/memory/memory.limit_in_bytes")), _int(read("/sys/fs/cgroup/memory/memory.usage_in_bytes"))
        stat = _stat(read("/sys/fs/cgroup/memory/memory.stat"))
        anon, cache, inactive = stat.get("total_rss") or stat.get("rss"), stat.get("total_cache") or stat.get("cache"), stat.get("total_inactive_file") or stat.get("inactive_file")
        if limit is not None and limit >= 2 ** 60:
            limit = None                                      # v1 reports "no limit" as a huge number
    if used is None:
        return empty
    working = max(used - (inactive or 0), 0)
    return {"limit_mb": _mb(limit), "used_mb": _mb(used), "anon_mb": _mb(anon), "file_cache_mb": _mb(cache), "inactive_cache_mb": _mb(inactive), "working_set_mb": _mb(working)}


def process(read: Callable[[str], Optional[str]] = _read) -> Dict[str, Optional[float]]:
    """This process's resident memory now and at its peak, in MB (from /proc/self/status). None where there is no /proc."""
    values: Dict[str, Optional[float]] = {"rss_mb": None, "peak_rss_mb": None}
    for line in (read("/proc/self/status") or "").splitlines():
        name, _, rest = line.partition(":")
        if name in ("VmRSS", "VmHWM"):
            digits = rest.strip().split()[0] if rest.strip() else ""
            if digits.isdigit():
                values["rss_mb" if name == "VmRSS" else "peak_rss_mb"] = round(int(digits) / 1000, 1)
    return values


def _mount_type(path: str, read: Callable[[str], Optional[str]] = _read) -> Optional[str]:
    """The filesystem type holding `path` (the longest matching mount point in /proc/mounts), or None."""
    best, kind = "", None
    for line in (read("/proc/mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and (path == parts[1] or path.startswith(parts[1].rstrip("/") + "/")) and len(parts[1]) > len(best):
            best, kind = parts[1], parts[2]
    return kind


def held_by_app(chat_sessions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the app keeps on purpose: conversations and messages held in memory, and the connected folders' files on disk (bytes, file count, people), with whether the temp directory is
    RAM-backed. Counts and sizes only."""
    from backend.services import local_workspace

    conversations = len(chat_sessions or {})
    messages = sum(len(v) for v in (chat_sessions or {}).values() if hasattr(v, "__len__"))
    base = local_workspace.WORKSPACE_BASE_DIR
    files = size = people = 0
    try:
        if base.exists():
            for person in base.iterdir():
                if person.is_dir():
                    people += 1
                    for root, _dirs, names in os.walk(person):
                        for name in names:
                            files += 1
                            if files > MAX_WALK_FILES:
                                raise StopIteration
                            try:
                                size += os.path.getsize(os.path.join(root, name))
                            except OSError:
                                pass
    except StopIteration:
        pass
    except OSError:
        logger.debug("[server memory] could not walk the workspace folders.", exc_info=True)
    kind = _mount_type(str(base))
    return {
        "conversations_in_memory": conversations, "messages_in_memory": messages,
        "folder_people": people, "folder_files": min(files, MAX_WALK_FILES), "folder_mb": _mb(size),
        "temp_filesystem": kind, "temp_is_memory": kind in ("tmpfs", "ramfs") if kind else None,
    }


def snapshot(chat_sessions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything above in one dict for GET /api/admin/server-memory. Blocking (reads files); never raises."""
    try:
        from backend.services import sandbox_tests

        box = container()
        sees = sandbox_tests.headroom_mb()
        limit, working = box.get("limit_mb"), box.get("working_set_mb")
        without_cache = round(limit - working, 1) if limit is not None and working is not None else None
        cache_share = round(box["inactive_cache_mb"] / box["used_mb"], 2) if box.get("inactive_cache_mb") is not None and box.get("used_mb") else None
        need_warm, need_cold = sandbox_tests.NEED_MB_WARM, sandbox_tests.NEED_MB_COLD
        return {
            "container": box, "process": process(), "held": held_by_app(chat_sessions),
            "sandbox": {
                "headroom_now_mb": round(sees, 1) if sees is not None else None, "headroom_without_cache_mb": without_cache,
                "need_warm_mb": need_warm, "need_cold_mb": need_cold, "compiled_cache_present": sandbox_tests._cache_path().exists(),
                "would_run_now": None if sees is None else sees >= (need_warm if sandbox_tests._cache_path().exists() else need_cold),
                "would_run_without_cache": None if without_cache is None else without_cache >= (need_warm if sandbox_tests._cache_path().exists() else need_cold),
            },
            "reclaimable_share_of_used": cache_share,
        }
    except Exception:
        logger.warning("[server memory] could not read the memory figures.", exc_info=True)
        return {"container": {}, "process": {}, "held": {}, "sandbox": {}, "reclaimable_share_of_used": None}
