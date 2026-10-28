"""The files SAAPP asks a check to run on, checked HERE as well as on the server: the runner never trusts what it is told.

A file must be a plain path inside the project that exists and is a file. Absolute paths, `..`, anything that resolves outside the folder (a link pointing out of it included) and anything starting with a
dash (which a tool could read as an option) are refused. A good file is handed on as `./path`, so even an odd name cannot be read as an option.
"""
import re
from pathlib import Path
from typing import Any, List

MAX_FILES = 20
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")


class Refusal(Exception):
    """A file or a request this runner will not act on, said in words."""


def clean_files(root: Path, files: Any) -> List[str]:
    """The files as plain relative forward-slash paths that exist inside `root`, or a Refusal."""
    if files in (None, "", []):
        return []
    if not isinstance(files, (list, tuple)) or len(files) > MAX_FILES:
        raise Refusal(f"at most {MAX_FILES} files can be given.")
    base = Path(root).resolve()
    out: List[str] = []
    for raw in files:
        text = str(raw or "").replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        parts = text.split("/")
        if not text or len(text) > 200 or text.startswith("/") or _DRIVE.match(text) or _CONTROL.search(text) or text.startswith("-") or any(p in ("", "..") for p in parts):
            raise Refusal("a file must be a plain relative path inside the project.")
        target = (base / text).resolve()
        try:
            relative = target.relative_to(base)
        except ValueError:
            raise Refusal("a file resolves to somewhere outside the project.") from None
        if not target.is_file():
            raise Refusal(f"{relative.as_posix()} is not a file in the project.")
        if relative.as_posix() not in out:
            out.append(relative.as_posix())
    return out


def as_argument(relative: str) -> str:
    """A cleaned file as one command-line word. The leading ./ means no tool can take it for an option."""
    return "./" + relative
