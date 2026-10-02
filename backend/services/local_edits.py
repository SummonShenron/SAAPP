"""Validation and diffing for edits Sonic proposes against a user's connected local folder.

Sonic never writes anything itself: it proposes a set of edits, this module checks them against the
folder's server-side snapshot (see local_workspace.py) exactly the way the browser will later apply
them, and the user applies them from a diff card in the app — the browser, which holds the folder
permission, does the actual writing and keeps the undo copy. Checking here is what gives Sonic
immediate, specific feedback ("old_string matches 2 places", "syntax error at line 40") so a bad
edit is corrected inside the same turn instead of surfacing as a failed Apply.

Pure functions apart from the injected `read_file`; never raises on bad input — problems come back
as human-readable error strings for the model to act on.
"""
import ast
import difflib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.local_workspace import MAX_FILE_BYTES, path_rejection, safe_relative_path

MAX_EDIT_OPS = 25
MAX_EDIT_FILES = 12
_MAX_DIFF_LINES_PER_FILE = 400
_MAX_ERRORS_REPORTED = 12


def _to_lf(text: str) -> Tuple[str, bool]:
    uses_crlf = "\r\n" in text
    return (text.replace("\r\n", "\n") if uses_crlf else text), uses_crlf


def _not_found_hint(content_lf: str, old_lf: str) -> str:
    first_line = next((line.strip() for line in old_lf.split("\n") if line.strip()), "")
    if not first_line:
        return ""
    for line_no, line in enumerate(content_lf.split("\n"), start=1):
        if first_line in line:
            return (
                f" Its first line does appear at line {line_no}, but the rest of old_string doesn't "
                f"match what follows — re-read that region and copy it exactly."
            )
    return " Not even its first line appears in this file — you may be quoting the wrong file or stale code."


def apply_replace(content: str, old: str, new: str) -> Tuple[Optional[str], Optional[str]]:
    """Replaces the one exact occurrence of `old` in `content` with `new`, returning
    (new_content, None) or (None, error). Matching ignores CRLF vs LF (the model only ever sees
    one), and a CRLF file stays CRLF. The browser applies edits with the same rules (see
    local/src/localEditsCore.ts) — keep the two in step."""
    content_lf, crlf = _to_lf(content)
    old_lf = old.replace("\r\n", "\n")
    new_lf = new.replace("\r\n", "\n")
    if old_lf == "":
        return None, "old_string is empty"
    if old_lf == new_lf:
        return None, "old_string and new_string are identical"
    count = content_lf.count(old_lf)
    if count == 0:
        return None, "old_string was not found in the file." + _not_found_hint(content_lf, old_lf)
    if count > 1:
        return None, (
            f"old_string matches {count} places in the file; include more surrounding lines so it "
            f"matches exactly one."
        )
    result = content_lf.replace(old_lf, new_lf, 1)
    return (result.replace("\n", "\r\n") if crlf else result), None


def _syntax_error(path: str, text: str) -> Optional[str]:
    lower = path.lower()
    try:
        if lower.endswith(".py"):
            ast.parse(text)
        elif lower.endswith(".json"):
            json.loads(text)
    except SyntaxError as e:
        return f"{path}: the result would have a Python syntax error at line {e.lineno}: {e.msg}"
    except ValueError as e:
        return f"{path}: the result would not be valid JSON ({e})"
    return None


def _diff_for(path: str, before: Optional[str], after: str) -> Dict[str, Any]:
    before_lines = _to_lf(before)[0].split("\n") if before is not None else []
    after_lines = _to_lf(after)[0].split("\n")
    lines = list(difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{path}" if before is not None else "/dev/null", tofile=f"b/{path}", lineterm="", n=3,
    ))
    additions = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    if len(lines) > _MAX_DIFF_LINES_PER_FILE:
        lines = lines[:_MAX_DIFF_LINES_PER_FILE] + [f"... ({len(lines) - _MAX_DIFF_LINES_PER_FILE} more diff lines not shown)"]
    return {
        "path": path,
        "kind": "edit" if before is not None else "create",
        "diff": "\n".join(lines),
        "additions": additions,
        "deletions": deletions,
    }


def validate_edit_proposal(edits: Any, read_file: Callable[[str], Optional[str]]) -> Dict[str, Any]:
    """Simulates `edits` in order against the snapshot (`read_file(path)` returns a file's text, or
    None if it doesn't exist) and returns {"ok": True, "edits": [...normalized...], "files": [diff
    cards]} or {"ok": False, "errors": [...]}. Each edit is either
    {"path","old_string","new_string"} (replace the one exact occurrence) or {"path","content"}
    (create a new file that doesn't exist yet)."""
    if not isinstance(edits, list) or not edits:
        return {"ok": False, "errors": ["edits must be a non-empty list"]}
    if len(edits) > MAX_EDIT_OPS:
        return {"ok": False, "errors": [f"too many edits ({len(edits)}); propose at most {MAX_EDIT_OPS} at once"]}

    errors: List[str] = []
    original: Dict[str, Optional[str]] = {}
    working: Dict[str, Optional[str]] = {}
    normalized: List[Dict[str, str]] = []

    for index, op in enumerate(edits, start=1):
        if not isinstance(op, dict):
            errors.append(f"edit {index}: must be an object")
            continue
        path = safe_relative_path(op.get("path"))
        if path is None:
            errors.append(f"edit {index}: invalid path {op.get('path')!r} (use a path relative to the folder root)")
            continue
        reason = path_rejection(path)
        if reason:
            errors.append(f"edit {index} ({path}): not allowed — {reason}")
            continue
        if path not in working:
            original[path] = read_file(path)
            working[path] = original[path]
        label = f"edit {index} ({path})"

        if "content" in op and "old_string" not in op:
            content = op.get("content")
            if not isinstance(content, str):
                errors.append(f"{label}: content must be text")
            elif working[path] is not None:
                errors.append(f"{label}: the file already exists; use old_string/new_string edits to change it")
            elif len(content.encode("utf-8")) > MAX_FILE_BYTES:
                errors.append(f"{label}: new file is too large")
            else:
                working[path] = content
                normalized.append({"type": "create", "path": path, "content": content})
            continue

        old, new = op.get("old_string"), op.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            errors.append(f"{label}: needs old_string and new_string (both text), or content to create a file")
        elif working[path] is None:
            errors.append(
                f"{label}: no such file in the connected folder. To make a new file give "
                f"{{path, content}}; to edit an existing one, check the path with find_file first"
            )
        else:
            replaced, error = apply_replace(working[path], old, new)
            if error:
                errors.append(f"{label}: {error}")
            else:
                working[path] = replaced
                normalized.append({"type": "replace", "path": path, "old_string": old, "new_string": new})

    touched = [path for path in working if working[path] != original[path]]
    if len(touched) > MAX_EDIT_FILES:
        errors.append(f"these edits touch {len(touched)} files; propose at most {MAX_EDIT_FILES} at once")
    if not errors:
        for path in touched:
            after = working[path]
            before = original[path]
            error = _syntax_error(path, after)
            # A file that was already broken before the edit isn't this proposal's fault.
            if error and (before is None or _syntax_error(path, before) is None):
                errors.append(error)

    if errors:
        return {"ok": False, "errors": errors[:_MAX_ERRORS_REPORTED]}
    return {
        "ok": True,
        "edits": normalized,
        "files": [_diff_for(path, original[path], working[path]) for path in touched],
    }
