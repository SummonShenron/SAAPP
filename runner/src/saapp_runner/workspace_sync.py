"""Reading a project's files and sending them to SAAPP, after the person said yes in a window on their own computer (sharing.py), so Sonic can read the project (docs/runner-files-plan.md).

It READS only the project folder it was given, never follows a link or junction out of it, skips what filerules.py says to skip (secrets, build output, lockfiles, big files), and sends the rest in
batches to the same snapshot the browser's folder upload fills. A FULL upload: its batches carry one upload id and the first resets, the last commits, so the server swaps the new copy in at once and a
failed upload leaves the old copy as it was. The server refuses a batch unless a request for this project is open and was handed to this runner, and applies its own path and secret rules again.
"""
import base64
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import filerules
from .client import Client, ServerError, Unreachable
from .finder import _is_link


@dataclass
class Entry:
    path: str          # posix, relative to the project folder
    size: int
    office: bool


@dataclass
class Scan:
    entries: List[Entry] = field(default_factory=list)
    total_bytes: int = 0
    too_big: int = 0            # files over their size limit, left out
    truncated: str = ""         # "" or why the walk stopped early: "files" or "bytes"


def scan(root: Path) -> Scan:
    """The files of the project that may be sent, in a stable order. Bounded by the same limits the server applies (8,000 files, 60 MB); when one is reached the rest is left out and `truncated` says which."""
    found = Scan()
    stack: List[tuple] = [(Path(root), "")]
    while stack:
        folder, prefix = stack.pop()
        try:
            with os.scandir(folder) as entries:
                children = sorted(entries, key=lambda e: e.name.lower())
        except OSError:
            continue
        subfolders = []
        for entry in children:
            try:
                if _is_link(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in filerules.SKIP_DIRS:
                        subfolders.append((Path(entry.path), f"{prefix}{entry.name}/"))
                    continue
                if not entry.is_file(follow_symlinks=False) or not filerules.is_syncable_file_name(entry.name):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if size > filerules.size_limit(entry.name):
                found.too_big += 1
                continue
            if len(found.entries) >= filerules.MAX_FILES:
                found.truncated = found.truncated or "files"
                continue
            if found.total_bytes + size > filerules.MAX_TOTAL_BYTES:
                found.truncated = found.truncated or "bytes"
                continue
            found.entries.append(Entry(f"{prefix}{entry.name}", size, filerules.is_office_name(entry.name)))
            found.total_bytes += size
        stack.extend(reversed(subfolders))
    found.entries.sort(key=lambda e: e.path)
    return found


def summary(found: Scan) -> str:
    """A person-sized line for the consent window: how many files and how much, and what was left out."""
    megabytes = found.total_bytes / 1_000_000
    size = f"{megabytes:.1f} MB" if megabytes >= 0.1 else f"{max(found.total_bytes // 1000, 1)} KB"
    extra = []
    if found.too_big:
        extra.append(f"{found.too_big} too big to send")
    if found.truncated:
        extra.append("the project is larger than the limit, so only part of it is sent")
    return f"{len(found.entries)} text files ({size})" + (f"; {', '.join(extra)}" if extra else "")


@dataclass
class Outcome:
    ok: bool
    sent: int = 0
    refused: int = 0
    message: str = ""


def _read(root: Path, entry: Entry) -> Optional[Dict[str, str]]:
    try:
        data = (Path(root) / entry.path).read_bytes()
    except OSError:
        return None                                       # changed or gone since the scan: left out
    encoded = base64.b64encode(data).decode("ascii")
    return {"path": entry.path, "content_base64": encoded} if entry.office else {"path": entry.path, "text_b64": encoded}


def upload(client: Client, project_id: str, root: Path, found: Scan, say: Callable[[str], None] = lambda text: None, upload_id: Optional[str] = None) -> Outcome:
    """Sends the scanned files as one full upload. Returns how it went; never raises (a network problem or a refusal is the outcome's message, which names no file contents)."""
    entries = found.entries
    if not entries:
        return Outcome(False, message="There were no files to send.")
    identifier = upload_id or uuid.uuid4().hex
    batches: List[List[Entry]] = [[]]
    size = 0
    for entry in entries:
        if batches[-1] and (len(batches[-1]) >= filerules.BATCH_MAX_FILES or size + entry.size > filerules.BATCH_MAX_BYTES):
            batches.append([])
            size = 0
        batches[-1].append(entry)
        size += entry.size
    sent = refused = 0
    for index, batch in enumerate(batches):
        files = [item for item in (_read(root, e) for e in batch) if item is not None]
        body: Dict[str, Any] = {"project_id": project_id, "files": files, "upload_id": identifier, "reset": index == 0, "commit": index == len(batches) - 1}
        try:
            answer = client.workspace_sync(body)
        except ServerError as problem:
            return Outcome(False, sent, refused, f"The server did not take the files: {problem.message}")
        except Unreachable as problem:
            return Outcome(False, sent, refused, str(problem))
        sent += int(answer.get("accepted") or 0)
        refused += len(answer.get("rejected") or [])
        say(f"Sent {sent} of {len(entries)} files.")
    return Outcome(True, sent, refused, f"Sent {sent} files.")
