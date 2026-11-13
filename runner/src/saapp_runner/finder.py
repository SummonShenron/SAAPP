"""Looking for the folder SAAPP asked about, on THIS computer, so the person can say yes to it instead of finding it.

SAAPP only ever sends a project's NAME (the browser's folder picker gives a page no path). Before the runner opens a folder chooser it looks, under the person's own home folder, for a folder with that name, and
when it finds exactly one likely candidate the dialog shows the full path with Yes / choose another / No. The search only READS folder names on this computer: nothing it finds is sent anywhere, and the folder is
added only if the person says yes in the dialog (picker.py), the same consent as before.

It is a bounded, polite search: breadth first (shallow folders first), a few levels deep, a short time budget and a cap on how many folders it looks at, skipping hidden folders (a name starting with a dot), the
places that hold other programs' files (node_modules, AppData, Library, virtual environments, build output...), and never following a link or junction out of the home folder. It finds nothing rather than taking long.
"""
import os
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import projects

MAX_DEPTH = 5                     # levels below the home folder: ~/Documents/work/clients/my-app is 4
TIME_BUDGET_SECONDS = 4.0
MAX_DIRECTORIES = 30000           # folders looked into at most
MAX_CANDIDATES = 5
# Folder names (lower case) never searched: other programs' files, caches, media and the system's own folders.
SKIP_NAMES = frozenset({
    "node_modules", "__pycache__", "venv", "env", "site-packages", "dist", "build", "target", "vendor", "bower_components", "appdata", "library", "applications", "program files", "program files (x86)",
    "programdata", "windows", "system volume information", "$recycle.bin", "music", "movies", "videos", "pictures", "photos",
})
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_link(entry: Any) -> bool:
    """A symbolic link, a Windows junction or any other reparse point: not followed, so the search cannot leave the home folder by a link."""
    try:
        if entry.is_symlink():
            return True
        junction = getattr(entry, "is_junction", None)
        if junction is not None and junction():
            return True
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def find_candidates(name: str, home: Optional[Path] = None, *, max_depth: int = MAX_DEPTH, seconds: float = TIME_BUDGET_SECONDS, max_directories: int = MAX_DIRECTORIES,
                    clock: Callable[[], float] = time.monotonic) -> List[Path]:
    """The folders under `home` (the person's home folder) named like `name`, case aside, shallowest first, at most MAX_CANDIDATES. Only folders that could be a project (projects.unsuitable) count, and a match is
    not searched inside. Never raises: a folder that cannot be read is skipped."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return []
    top = Path(home) if home is not None else Path.home()
    deadline = clock() + seconds
    found: List[Path] = []
    level: List[Path] = [top]
    looked = 0
    for depth in range(max_depth + 1):
        following: List[Path] = []
        for folder in level:
            if clock() > deadline or looked >= max_directories:
                return found
            looked += 1
            try:
                with os.scandir(folder) as entries:
                    children = sorted((e for e in entries), key=lambda e: e.name.lower())
            except OSError:
                continue
            for entry in children:
                try:
                    if not entry.is_dir(follow_symlinks=False) or _is_link(entry):
                        continue
                except OSError:
                    continue
                lowered = entry.name.lower()
                if lowered.startswith(".") or lowered in SKIP_NAMES:
                    continue
                if lowered == wanted:
                    path = Path(entry.path)
                    if projects.unsuitable(path) is None:
                        found.append(path)
                        if len(found) >= MAX_CANDIDATES:
                            return found
                    continue                      # a match is not searched inside
                if depth < max_depth:
                    following.append(Path(entry.path))
        level = following
        if not level:
            break
    return found


def likely_one(candidates: List[Path]) -> Optional[Path]:
    """The one folder worth offering, or None to fall back to the chooser: the only candidate, or the only one of several that is a git repository (a clone, which is what a project is). Two or more equally
    likely folders are the person's to choose between."""
    if len(candidates) == 1:
        return candidates[0]
    repos = [c for c in candidates if (c / ".git").exists()]
    return repos[0] if len(repos) == 1 else None
