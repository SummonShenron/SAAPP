"""Every place a name appears in a folder, sorted into where it is DEFINED, where it is IMPORTED and where it is USED, with line numbers (trace_symbol on a connected folder).

WHY (2026-11-24 coding audit). With a folder connected, search_code and trace_symbol were withheld (they ask GitHub's index, which cannot see uncommitted code), leaving only search_literal: a
case-insensitive substring search with no word boundary and no idea which hit is the definition. On a multi-file change Haiku either missed a caller or spent steps opening hits one by one. This greps
the folder's own files, whole words only and case-sensitive (an identifier is), and says which hit is the definition. Pure: the caller hands in (path, text) pairs.
"""
import re
from typing import Dict, Iterable, List, Tuple

SEARCHED_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".html", ".json", ".yml", ".yaml", ".toml", ".sql", ".md", ".sh")
MAX_USES_PER_FILE = 4
MAX_FILES_SHOWN = 25
MAX_DEFINITIONS = 10
MAX_LINE_CHARS = 160

_IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s+(?:\*|\{[^}]*\})\s+from\b)|\brequire\s*\(")


def _word(symbol: str) -> "re.Pattern":
    """The symbol as a whole word: not part of a longer identifier on either side ($ and - count as part of one, for JS names and CSS classes)."""
    return re.compile(r"(?<![A-Za-z0-9_$-])" + re.escape(symbol) + r"(?![A-Za-z0-9_$-])")


def classify(symbol: str, line: str) -> str:
    """"define", "import" or "use" for a line that contains the symbol. Pure."""
    s = re.escape(symbol)
    if _IMPORT_RE.search(line):
        return "import"
    stripped = line.strip()
    definition = (
        rf"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?(?:def|class|function\*?|interface|type|enum)\s+{s}\b",       # def / class / function / interface / type / enum
        rf"^(?:export\s+)?(?:const|let|var)\s+{s}\b",                                                                            # const X = / let X
        rf"^(?:export\s+)?(?:const|let|var)\s+\{{[^}}]*\b{s}\b[^}}]*\}}\s*=",                                                     # const { X } = ...
        rf"^self\.{s}\s*(?::[^=]+)?=(?!=)",                                                                                      # self.X = ...
        rf"^(?:public\s+|private\s+|protected\s+|static\s+|readonly\s+|async\s+|get\s+|set\s+)*{s}\s*\([^;]*\)\s*(?::[^;={{]+)?\{{\s*$",  # a method: X(a, b) {
        rf"^\.{s}\s*\{{",                                                                                                        # a CSS rule: .X {
    )
    if any(re.search(pattern, stripped) for pattern in definition):
        return "define"
    if not line[:1].isspace() and re.match(rf"^{s}\s*(?::[^=]+)?=(?!=)", stripped):
        return "define"                                                                                                           # a module-level X = ...
    return "use"


def find_references(files: Iterable[Tuple[str, str]], symbol: str) -> Dict[str, object]:
    """{"definitions": [(path, line_no, text)], "imports": {path: [line_no]}, "uses": {path: [(line_no, text)]}, "files": n} for a whole-word symbol. Pure."""
    symbol = (symbol or "").strip()
    found = {"definitions": [], "imports": {}, "uses": {}, "files": 0}
    if not symbol:
        return found
    pattern = _word(symbol)
    for path, text in files:
        if not path.lower().endswith(SEARCHED_SUFFIXES) or symbol not in (text or ""):
            continue
        hit = False
        for number, line in enumerate(text.splitlines(), start=1):
            if not pattern.search(line):
                continue
            hit = True
            kind = classify(symbol, line)
            shown = " ".join(line.split())[:MAX_LINE_CHARS]
            if kind == "define":
                found["definitions"].append((path, number, shown))
            elif kind == "import":
                found["imports"].setdefault(path, []).append(number)
            else:
                found["uses"].setdefault(path, []).append((number, shown))
        found["files"] += hit
    return found


def format_references(found: Dict[str, object], symbol: str) -> str:
    """What the model reads: definitions first, then each file that uses it with its first lines, then the importers. Pure."""
    definitions: List[Tuple[str, int, str]] = found["definitions"]           # type: ignore[assignment]
    imports: Dict[str, List[int]] = found["imports"]                          # type: ignore[assignment]
    uses: Dict[str, List[Tuple[int, str]]] = found["uses"]                    # type: ignore[assignment]
    if not definitions and not imports and not uses:
        return (f"No whole-word matches for {symbol!r} in the folder (matching is exact and case-sensitive). For a colloquial name use find_file; for text that is part of a longer word, "
                "use search_literal.")
    parts = [f"{symbol!r} appears in {found['files']} file(s) of the connected folder (whole word, case-sensitive)."]
    if definitions:
        parts.append("DEFINED:\n" + "\n".join(f"  {p}:{n}: {t}" for p, n, t in definitions[:MAX_DEFINITIONS])
                     + (f"\n  ... and {len(definitions) - MAX_DEFINITIONS} more" if len(definitions) > MAX_DEFINITIONS else ""))
    else:
        parts.append("DEFINED: no definition found in the folder (it may come from a library, or be built dynamically).")
    if uses:
        ordered = sorted(uses.items(), key=lambda item: (-len(item[1]), item[0]))
        lines = []
        for path, hits in ordered[:MAX_FILES_SHOWN]:
            shown = "\n".join(f"    {n}: {t}" for n, t in hits[:MAX_USES_PER_FILE])
            more = f"\n    ... {len(hits) - MAX_USES_PER_FILE} more in this file" if len(hits) > MAX_USES_PER_FILE else ""
            lines.append(f"  {path} ({len(hits)} use{'s' if len(hits) != 1 else ''})\n{shown}{more}")
        extra = f"\n  ... and {len(ordered) - MAX_FILES_SHOWN} more file(s)" if len(ordered) > MAX_FILES_SHOWN else ""
        parts.append("USED (every file you change it in, or that calls it, may need an edit):\n" + "\n".join(lines) + extra)
    if imports:
        parts.append("IMPORTED IN:\n" + "\n".join(f"  {p}:{','.join(str(n) for n in ns[:3])}" for p, ns in sorted(imports.items())))
    return "\n\n".join(parts)
