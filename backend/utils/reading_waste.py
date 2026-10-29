"""How much of what a coding turn reads is the right material: counts, so the question "does it read the wrong things?" has an answer.

84% of Sonic's tool calls are repo reading (30 days of usage, 2026-10-29): about 3.4 file reads and 2.9 searches per turn, and a listing of the whole tree on a third of them. Reads almost never fail (3%), so it is
not asking for paths that do not exist; the open question is whether the files it opens are the ones that mattered. Nothing measured that. This follows one native-loop turn and keeps only counts:

- files READ (distinct), and how many of them were later EDITED in the same turn (the ones that mattered), in turns where an edit was applied: the share is how often what it opened was what it changed.
- RE-READS: a file read again in the same turn.
- SEARCHES, and how many found nothing (a guess that did not land); whole-tree LISTINGS and find_file calls (the "where do I start" moves).

No path, no term, no text is kept (the paths live in memory for the turn only, to answer "was this file edited?"), and nothing here logs. Pure."""
from typing import Any, Dict, Optional, Sequence

READ_TOOLS = ("read_repo_file",)
SEARCH_TOOLS = ("search_literal", "search_code")
EMPTY_PREFIXES = ("no occurrences", "no matches", "no results", "found no")


def _path(args: Any) -> str:
    return str((args or {}).get("path") or "").strip().replace("\\", "/") if isinstance(args, dict) else ""


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

    def note(self, name: str, args: Any, result: Any) -> None:
        text = str(result or "")
        failed = text.startswith("ERROR")
        if name in READ_TOOLS and not failed:
            path = _path(args)
            if path:
                self._read[path] = self._read.get(path, 0) + 1
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
        return {
            "files_read": len(read), "edited_and_read": len(read & self._edited), "files_edited": len(self._edited), "rereads": sum(count - 1 for count in self._read.values()),
            "searches": self.searches, "empty_searches": self.empty_searches, "tree_listings": self.tree_listings, "find_files": self.find_files, "applied": self.applied,
        }


def counts_from(summary: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """The counter increments for one turn (keys in self_counters.READING_KEYS). Empty when the turn read nothing at all. The "edit turn" counters count only turns where an edit was applied."""
    if not isinstance(summary, dict):
        return {}

    def n(key: str) -> int:
        value = summary.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    if not (n("files_read") or n("searches") or n("tree_listings") or n("find_files")):
        return {}
    counts: Dict[str, int] = {"reading_turns": 1}
    for source, key in (("files_read", "reading_files_read"), ("rereads", "reading_rereads"), ("searches", "reading_searches"), ("empty_searches", "reading_empty_searches"),
                        ("tree_listings", "reading_tree_listings"), ("find_files", "reading_find_files")):
        if n(source):
            counts[key] = n(source)
    if n("applied"):
        counts["reading_edit_turns"] = 1
        counts["reading_edit_turn_files_read"] = n("files_read")
        counts["reading_edit_turn_files_used"] = min(n("edited_and_read"), n("files_read"))
    return counts
