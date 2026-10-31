"""Read a file where the work is, never from line 1. Pure, no model call, nothing logged.

WHY. A read with no start_line used to return the first 3,500 characters of a big file (imports and setup, almost never what the task needs) with an index that only knew Python definitions, so the model
read the top, then searched, then read again: the "read, read, read, then act" Jack paid for on 2026-10-31 (45 files read, 22 of them re-reads, 8 of the 24 read in edit turns used).

THE RULE. A read from the top of a file (no start_line, or line 1, without a small line_count) is not answered with the top. It is answered with where things are in that file (every top-level declaration
with its line number) and how to get there: search for a word from the task, then read a window around the match. A small file (under the existing character cap) is still returned whole, and a small
window from line 1 (line_count at most TOP_READ_MAX_LINES, say for the imports before adding one) is still allowed.
"""
import re
from typing import Any, List, Optional

TOP_READ_MAX_LINES = 120          # a window this small from line 1 is a deliberate look at the imports, not "the file"
OUTLINE_MAX_ENTRIES = 150         # the old definition index's cap
OUTLINE_MAX_CHARS = 3000          # a tool observation is cut at 4,000 characters (agent_utils._MAX_OBSERVATION_CHARS); the instructions need the rest
OUTLINE_LINE_CHARS = 80

# A top-level declaration in nearly any language: something at the start of the line that is not a closer, a comment or a decorator.
_NOT_A_DECLARATION = re.compile(r"^(?:[})\];,]|//|/\*|\*|#|@|<!--|-->)")
_PY_DEFINITION = re.compile(r"^(?:async\s+)?(def|class)\s+(\w+)")
# Imports are the top of the file: they are what the map exists to skip.
_IMPORT_LINE = re.compile(r"^(?:import\s|from\s+\S+\s+import\b|export\s*\{|export\s+\*\s+from|require\(|using\s|use\s|package\s|#include|['\"]use (?:strict|client|server)['\"])")


def is_top_read(start_line: Any, line_count: Any) -> bool:
    """True for a read that would start at the top of the file and is not a small deliberate window. Pure."""
    try:
        start = int(start_line) if start_line else None
    except (TypeError, ValueError):
        start = None
    if start not in (None, 0, 1):
        return False
    try:
        count = int(line_count) if line_count else None
    except (TypeError, ValueError):
        count = None
    return count is None or count > TOP_READ_MAX_LINES


def outline(lines: List[str]) -> str:
    """Every top-level declaration with its line number, as compact as the old definition index ("  line 120: def alpha") and for any language ("  line 9: export default function Chat() {"),
    within OUTLINE_MAX_CHARS so it fits in a tool observation. Pure."""
    entries: List[str] = []
    used = 0
    for number, line in enumerate(lines, start=1):
        text = line.rstrip()
        if not text or text[0].isspace() or _NOT_A_DECLARATION.match(text) or _IMPORT_LINE.match(text):
            continue
        definition = _PY_DEFINITION.match(text)
        shown = f"{definition.group(1)} {definition.group(2)}" if definition else text[:OUTLINE_LINE_CHARS]
        entry = f"  line {number}: {shown}"
        if len(entries) >= OUTLINE_MAX_ENTRIES or used + len(entry) + 1 > OUTLINE_MAX_CHARS:
            entries.append("  ... (more declarations; search for the one you need)")
            break
        entries.append(entry)
        used += len(entry) + 1
    return "\n".join(entries)


def redirect_text(path: str, lines: List[str], url: Optional[str] = None) -> str:
    """What a read from the top of a big file returns instead of the top. Pure. It carries the phrase "truncated — this file has", which is what the older engine's rule against answering straight after a
    truncated read looks for (backend/utils/agent_utils.py `_mentions_unresolved_truncation`), so that rule still holds."""
    where = outline(lines) or "  (no top-level declarations found: search for a word from the task)"
    head = f"URL: {url}\n" if url else ""
    return (
        f"{head}NOT READ FROM THE TOP: [truncated — this file has {len(lines)} lines total, and its first lines are imports and setup, so this is a map, not its contents.] "
        "Search it for a word from the task with search_literal, then read a window around the match: read_repo_file(path, start_line=<match line minus 15>, line_count=60). "
        f"Or pick a line below.\nWhere things are in {path}:\n{where}"
    )
