"""The project folders this computer's runner serves, and the ONLY way a folder gets onto that list.

A runner is installed once per computer and serves any number of that computer's project folders. The list lives in `~/.saapp-runner/projects.json`, in the person's own home folder, and a folder can be
added only by something that happens ON THIS MACHINE:
- the person types it (`saapp-runner add-project --root <folder>`, or `start --root <folder>`), or
- the person chooses it in a dialog the runner shows (SAAPP can ASK for a project by name, but the runner shows its own dialog and the person picks the folder there; see picker.py).
SAAPP never supplies a path. So a compromised server can ask for a project to be added, and the person can say no; it can never point the runner at a folder.

A project's id is a short hash of its full path. It is how SAAPP tells projects apart without ever being sent a path.
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import home, project_key


def path() -> Path:
    return home() / "projects.json"


def project_id(root: Path) -> str:
    """Eight hex characters of a hash of the project's full path: the same folder always has the same id, and two folders never share one (in practice)."""
    return hashlib.sha256(project_key(root).encode("utf-8")).hexdigest()[:8]


def _read() -> List[str]:
    try:
        data: Any = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else None
    return [str(item["root"]) for item in items if isinstance(item, dict) and isinstance(item.get("root"), str)] if isinstance(items, list) else []


def _write(roots: List[str]) -> None:
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps({"projects": [{"root": root} for root in roots]}, indent=1), encoding="utf-8")
    try:
        os.chmod(path(), 0o600)
    except OSError:
        pass


def load() -> List[Path]:
    """The folders the person has allowed, in the order they were added. A folder that no longer exists is still listed (the runner reports it as missing), so a moved folder is noticed, not forgotten."""
    return [Path(root) for root in _read()]


def unsuitable(folder: Path) -> Optional[str]:
    """Why a folder cannot be a project, in words, or None. A project is a project's own folder: not a drive or the file system's root, and not the home folder itself (which holds everything)."""
    try:
        resolved = Path(folder).resolve()
    except OSError:
        return "That folder could not be read."
    if not resolved.is_dir():
        return "That is not a folder."
    if resolved.parent == resolved:
        return "That is the top of a drive. Choose the project's own folder."
    if resolved == Path.home().resolve():
        return "That is your whole home folder. Choose the project's own folder inside it."
    return None


def add(folder: Path) -> Tuple[bool, str]:
    """Allows a folder as a project. (True, a sentence) when it was added or was already there; (False, why) when it cannot be one. Only called for a folder the person typed or chose on this computer."""
    problem = unsuitable(folder)
    if problem:
        return False, problem
    resolved = Path(folder).resolve()
    roots = _read()
    if any(project_key(Path(root)) == project_key(resolved) for root in roots):
        return True, f"{resolved.name} was already one of this computer's projects."
    _write([*roots, str(resolved)])
    return True, f"Added the project {resolved.name}."


def remove(folder: Path) -> bool:
    """Forgets a folder. False when it was not on the list. Nothing in the folder is touched."""
    key = project_key(Path(folder))
    roots = _read()
    kept = [root for root in roots if project_key(Path(root)) != key]
    if len(kept) == len(roots):
        return False
    _write(kept)
    return True


def remove_id(identifier: str) -> bool:
    """Forgets the project with this id (what SAAPP says when the person removes a project in the page). False when there is none."""
    roots = _read()
    kept = [root for root in roots if project_id(Path(root)) != identifier]
    if len(kept) == len(roots):
        return False
    _write(kept)
    return True


def describe() -> List[Dict[str, Any]]:
    """The list for `saapp-runner projects`: [{"id", "root", "exists"}]."""
    return [{"id": project_id(root), "root": str(root), "exists": root.is_dir()} for root in load()]
