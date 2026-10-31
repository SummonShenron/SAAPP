"""How much of what a coding turn reads is the right material: counts, so the question "does it read the wrong things?" has an answer.

84% of Sonic's tool calls are repo reading (30 days of usage, 2026-10-29): about 3.4 file reads and 2.9 searches per turn, and a listing of the whole tree on a third of them. Reads almost never fail (3%), so it is
not asking for paths that do not exist; the open question is whether the files it opens are the ones that mattered. Nothing measured that. This follows one native-loop turn and keeps only counts:

- files READ (distinct), and how many of them were later EDITED in the same turn (the ones that mattered), in turns where an edit was applied: the share is how often what it opened was what it changed.
- RE-READS: a file read again in the same turn.
- SEARCHES, and how many found nothing (a guess that did not land); whole-tree LISTINGS and find_file calls (the "where do I start" moves).

- the OPENING MOVE: the first thing the loop did (read from the top of a file, read a window, search, list the tree, find a file, propose an edit). It says how the model decides where to start.
- TOP reads against WINDOW reads: a read from the top of a file against a read that starts where the work is (backend/utils/targeted_read.py).
- ORIENTATION (backend/utils/orientation.py): when the "where to look" block was given to this turn, whether the model's first read was one of its files, and, in a turn that applied an edit, where the best
  edited file ranked in the block (1 to 4, or absent). This is how the block is judged: does what it lists turn out to be what gets changed?

No path, no term, no text is kept (the paths live in memory for the turn only, to answer "was this file edited?" and "was it in the block?"), and nothing here logs. Pure."""
from typing import Any, Dict, Optional, Sequence

from backend.utils import targeted_read

READ_TOOLS = ("read_repo_file",)
SEARCH_TOOLS = ("search_literal", "search_code")
EMPTY_PREFIXES = ("no occurrences", "no matches", "no results", "found no")
OPENING_KINDS = ("read_top", "read_window", "search", "tree", "find", "edit", "other")
ORIENT_RANKS = ("first", "second", "third", "fourth", "absent")
_REDIRECT_MARK = "NOT READ FROM THE TOP"


def _path(args: Any) -> str:
    return str((args or {}).get("path") or "").strip().replace("\\", "/") if isinstance(args, dict) else ""


def _norm(path: Any) -> str:
    text = str(path or "").strip().replace(chr(92), "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def opening_kind(name: str, args: Any, result: Any) -> str:
    """What kind of move a tool call is, for the opening-move count. Pure."""
    if name in READ_TOOLS:
        arguments = args if isinstance(args, dict) else {}
        if _REDIRECT_MARK in str(result or "") or targeted_read.is_top_read(arguments.get("start_line"), arguments.get("line_count")):
            return "read_top"
        return "read_window"
    if name in SEARCH_TOOLS:
        return "search"
    if name == "list_repo_tree":
        return "tree"
    if name == "find_file":
        return "find"
    if name == "propose_local_edits":
        return "edit"
    return "other"


def _edited_paths(args: Any) -> Sequence[str]:
    edits = args.get("edits") if isinstance(args, dict) and isinstance(args.get("edits"), list) else []
    return [str(e.get("path") or "").strip().replace("\\", "/") for e in edits if isinstance(e, dict) and str(e.get("path") or "").strip()]


class ReadingLog:
    """Follows one loop's tool calls. `note(name, args, result)` after each call; `summary()` at the end."""

    def __init__(self) -> None:
        self._read: Dict[str, int] = {}
        self._edited: set = set()
        self.searches = self.empty_searches = self.tree_listings = self.find_files = 0
        self.applied = 0
        self.opening = ""
        self.top_reads = self.window_reads = 0
        self._first_read = ""
        self._block: list = []

    def set_orientation(self, paths: Sequence[str]) -> None:
        """The files the turn's "where to look" block listed, best first (empty when it had none). Kept in memory for the turn only."""
        self._block = [_norm(path) for path in (paths or []) if _norm(path)]

    def note(self, name: str, args: Any, result: Any) -> None:
        text = str(result or "")
        failed = text.startswith("ERROR")
        if not self.opening:
            self.opening = opening_kind(name, args, text)
        if name in READ_TOOLS and not failed:
            path = _path(args)
            if path:
                self._read[path] = self._read.get(path, 0) + 1
                self._first_read = self._first_read or _norm(path)
                if opening_kind(name, args, text) == "read_top":
                    self.top_reads += 1
                else:
                    self.window_reads += 1
        elif name in SEARCH_TOOLS and not failed:
            self.searches += 1
            if text.strip().lower().startswith(EMPTY_PREFIXES):
                self.empty_searches += 1
        elif name == "list_repo_tree" and not failed:
            self.tree_listings += 1
        elif name == "find_file" and not failed:
            self.find_files += 1
        elif name == "propose_local_edits" and " APPLIED:" in text:
            self.applied += 1
            self._edited.update(_edited_paths(args))

    def summary(self) -> Dict[str, int]:
        """Counts for the turn. `edited_and_read` is how many of the distinct files read were also edited (a file the turn created was not read first, so it does not count)."""
        read = set(self._read)
        block = self._block
        edited = {_norm(path) for path in self._edited}
        # The best (lowest) rank any edited file had in the block: 1 to 4, or 0 when none of them was listed. Only meaningful when a block was shown and an edit was applied.
        ranks = [block.index(path) + 1 for path in edited if path in block]
        return {
            "files_read": len(read), "edited_and_read": len(read & self._edited), "files_edited": len(self._edited), "rereads": sum(count - 1 for count in self._read.values()),
            "searches": self.searches, "empty_searches": self.empty_searches, "tree_listings": self.tree_listings, "find_files": self.find_files, "applied": self.applied,
            "top_reads": self.top_reads, "window_reads": self.window_reads,
            "orient_shown": 1 if block else 0, "orient_first_read_in_block": 1 if block and self._first_read and self._first_read in block else 0,
            "orient_rank": min(ranks) if ranks else 0,
            "opening": self.opening if self.opening in OPENING_KINDS else "",
        }


def counts_from(summary: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """The counter increments for one turn (keys in self_counters.READING_KEYS). Empty when the turn read nothing at all. The "edit turn" counters count only turns where an edit was applied."""
    if not isinstance(summary, dict):
        return {}

    def n(key: str) -> int:
        value = summary.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    opening = summary.get("opening") if summary.get("opening") in OPENING_KINDS else ""
    shown = bool(n("orient_shown"))
    counts: Dict[str, int] = {}
    if opening:
        counts[f"loop_open_{opening}"] = 1
        if shown:
            counts[f"loop_open_block_{opening}"] = 1
    if shown:
        counts["orient_turns"] = 1
        if n("files_read"):
            counts["orient_read_turns"] = 1
            if n("orient_first_read_in_block"):
                counts["orient_first_read_in_block"] = 1
        if n("applied"):
            counts["orient_edit_turns"] = 1
            rank = n("orient_rank")
            counts["orient_rank_" + (ORIENT_RANKS[rank - 1] if 1 <= rank <= 4 else "absent")] = 1
    if not (n("files_read") or n("searches") or n("tree_listings") or n("find_files")):
        return counts
    counts["reading_turns"] = 1
    for source, key in (("files_read", "reading_files_read"), ("rereads", "reading_rereads"), ("searches", "reading_searches"), ("empty_searches", "reading_empty_searches"),
                        ("tree_listings", "reading_tree_listings"), ("find_files", "reading_find_files"), ("top_reads", "reading_top_reads"), ("window_reads", "reading_window_reads")):
        if n(source):
            counts[key] = n(source)
    if n("applied"):
        counts["reading_edit_turns"] = 1
        counts["reading_edit_turn_files_read"] = n("files_read")
        counts["reading_edit_turn_files_used"] = min(n("edited_and_read"), n("files_read"))
    return counts
