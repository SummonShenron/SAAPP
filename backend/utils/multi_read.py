"""Several line ranges, of one file or several, in ONE read_repo_file call (`ranges`).

Measured 2026-11-27 (coding counters since 11-17): turns read 96 distinct files with 188 reads, mostly one file paged a window at a time, and the first edit came after 6.9 steps on average. Every
step resends the whole conversation, so a read that takes three steps costs three resends. `ranges` lets the model ask for every place it needs at once: "40-120" (the file named by `path`),
"backend/x.py:200-260" (another file), or a single line "88" (a window around it).

Limits keep one call from turning into a dump: at most MAX_RANGES ranges, MAX_TOTAL_LINES lines and MAX_CHARS characters in all; what does not fit is named so it can be read next. A range from
the top of a big file is refused like any top read (backend/utils/targeted_read.py), so `ranges` is not a way around that rule. Pure apart from the `fetch` it is handed.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.utils import targeted_read

MAX_RANGES = 6
MAX_TOTAL_LINES = 400
MAX_CHARS = 24000
SINGLE_LINE_WINDOW = 40         # "88" reads 20 lines either side
DEFAULT_SPAN = 60               # "88-" or a range with no end reads this many lines

_RANGE_RE = re.compile(r"^\s*(?:(?P<path>[^:\s][^:]*?)\s*:\s*)?(?P<start>\d+)\s*(?:-\s*(?P<end>\d*))?\s*$")


def parse(path: Any, ranges: Any) -> Tuple[List[Tuple[str, int, int]], List[str]]:
    """(spans, problems): the ranges as (path, start, end) with 1-indexed inclusive lines, overlapping or touching ranges of one file merged, in the order the files were first named; and a line for
    each range that could not be used. At most MAX_RANGES are taken."""
    main = str(path or "").strip()
    items = ranges if isinstance(ranges, list) else [ranges]
    spans: List[Tuple[str, int, int]] = []
    problems: List[str] = []
    for raw in items:
        if len(spans) >= MAX_RANGES:
            problems.append(f"only the first {MAX_RANGES} ranges were read; ask for the rest in another call")
            break
        match = _RANGE_RE.match(str(raw or ""))
        target = (match.group("path") or main).strip() if match else ""
        if not match or not target:
            problems.append(f"'{str(raw)[:60]}' is not a range: write it as 40-120, or path:40-120 for another file")
            continue
        start = int(match.group("start"))
        if match.group("end") is None:
            start, end = max(1, start - SINGLE_LINE_WINDOW // 2), start + SINGLE_LINE_WINDOW // 2
        elif match.group("end") == "":
            end = start + DEFAULT_SPAN - 1
        else:
            end = int(match.group("end"))
        start = max(1, start)
        if end < start:
            problems.append(f"'{str(raw)[:60]}' ends before it starts")
            continue
        spans.append((target, start, end))
    return _merged(spans), problems


def _merged(spans: List[Tuple[str, int, int]]) -> List[Tuple[str, int, int]]:
    order: List[str] = []
    by_file: Dict[str, List[Tuple[int, int]]] = {}
    for path, start, end in spans:
        if path not in by_file:
            order.append(path)
            by_file[path] = []
        by_file[path].append((start, end))
    merged: List[Tuple[str, int, int]] = []
    for path in order:
        current: Optional[List[int]] = None
        for start, end in sorted(by_file[path]):
            if current and start <= current[1] + 1:
                current[1] = max(current[1], end)
                continue
            if current:
                merged.append((path, current[0], current[1]))
            current = [start, end]
        if current:
            merged.append((path, current[0], current[1]))
    return merged


def render(spans: List[Tuple[str, int, int]], problems: List[str], fetch: Callable[[str], Tuple[Optional[str], Optional[str]]], big_file_chars: int) -> str:
    """The text of every span, each under a header naming the file and its lines. `fetch(path)` is (text, None) or (None, "ERROR: ..."); a file is fetched once. A span from line 1 of a file bigger
    than `big_file_chars` is refused like any top read. Stops at MAX_TOTAL_LINES / MAX_CHARS and names what was left."""
    texts: Dict[str, Tuple[Optional[List[str]], Optional[str], int]] = {}
    parts: List[str] = []
    lines_used = chars_used = 0
    left: List[str] = []
    for path, start, end in spans:
        if path not in texts:
            text, error = fetch(path)
            texts[path] = (text.splitlines() if text is not None else None, error, len(text or ""))
        lines, error, size = texts[path]
        if lines is None:
            parts.append(f"== {path} ==\n{error or 'ERROR: could not read it'}")
            continue
        if start > len(lines):
            parts.append(f"== {path} ==\nERROR: it only has {len(lines)} lines, so line {start} is past the end")
            continue
        if size > big_file_chars and targeted_read.is_top_read(start, end - start + 1):
            parts.append(f"== {path} lines {start}-{end} ==\nNOT READ: this is the top of a big file. Search for the word you need (search_literal) and ask for the lines around it instead.")
            continue
        end = min(end, len(lines))
        window = lines[start - 1:end]
        room = MAX_TOTAL_LINES - lines_used
        if len(window) > room:
            left.append(f"{path}:{start + room}-{end}")
            window = window[:room]
        body = "\n".join(window)
        if chars_used + len(body) > MAX_CHARS:
            kept: List[str] = []
            for line in window:
                if chars_used + len(line) + 1 > MAX_CHARS:
                    break
                kept.append(line)
                chars_used += len(line) + 1
            if len(kept) < len(window):
                left.append(f"{path}:{start + len(kept)}-{end}")
            window, body = kept, "\n".join(kept)
        else:
            chars_used += len(body)
        lines_used += len(window)
        if window:
            parts.append(f"== {path} lines {start}-{start + len(window) - 1} of {len(lines)} ==\n{body}")
    notes = list(problems)
    if left:
        notes.append(f"not read (the {MAX_TOTAL_LINES}-line / {MAX_CHARS}-character limit of one call): {', '.join(left)}. Ask for those ranges in another call if you need them.")
    return "\n\n".join(parts + ([("NOTE: " + " ".join(notes))] if notes else [])) or "ERROR: no range could be read"


def fit(window: List[str]) -> List[str]:
    """The lines of one plain window read (start_line, line_count) that one call may show: at most MAX_TOTAL_LINES lines and MAX_CHARS characters, always whole lines, and at least one. The caller
    names the last line shown, so the header never claims lines that were not delivered (2026-11-27: a 150-line read was cut to about 79 lines further on, under a header that said 150). Pure."""
    kept: List[str] = []
    used = 0
    for line in window[:MAX_TOTAL_LINES]:
        if kept and used + len(line) + 1 > MAX_CHARS:
            break
        kept.append(line)
        used += len(line) + 1
    return kept


def places(path: Any, ranges: Any) -> List[Tuple[str, str]]:
    """(path, "start-end") for each span asked for, for the reading counters and the read log. Pure."""
    spans, _ = parse(path, ranges)
    return [(span_path, f"{start}-{end}") for span_path, start, end in spans]
