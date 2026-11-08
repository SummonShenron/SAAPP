"""Where to look, found before the model takes its first step. Pure code, no model call, nothing logged.

WHY (Jack, 2026-10-31): Haiku writes the change well once it knows where to work, and spends its steps getting there: search, read the top, search again, read again (the coding day read 45 files, 22 of them
re-reads, and 24 turns in ten days ended at the step cap with no draft). Finding the place is mostly searching a handful of words from the request across the folder at once and ranking the files, which
code does in a fraction of a second and a model does one serial step at a time, each step re-sending the whole prompt.

WHAT. `where_to_look(root, paths, texts)` takes the words that are specific in the person's request (file names, identifiers, quoted strings, then the longer plain words), reads each text file in the synced
folder once, and ranks the files: a word in the file's NAME counts most, a word that appears in few files counts more than one that appears everywhere, a file that matches several different words counts more
than one that matches one word many times, and tests and docs count half. It returns a short block of the top files with the lines that matched, for the first step's prompt. It is a map, not proof: it says so,
and returns "" when nothing in the folder matches well enough to be worth the tokens.
"""
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

MAX_TERMS = 8
MIN_WORD_LENGTH = 4                 # shorter plain words match nearly everything; 4-letter ones ("plan", "mode") are kept but go last, and a word found in many files counts for little
MAX_TEXT_CHARS = 1500               # per message: a pasted transcript is not the request
MAX_FILES_SHOWN = 4
MAX_LINES_PER_FILE = 3
LINE_CHARS = 110
MAX_FILE_BYTES = 400_000
MAX_SCAN_BYTES = 30_000_000
DEADLINE_SECONDS = 3.0
RELATIVE_CUTOFF = 0.4               # a file scoring under this share of the best file's score is not worth showing next to it
MIN_SCORE = 3.0                     # below this the best file is a weak coincidence, not a lead
NAME_MATCH_WEIGHT = 6.0             # a word in the file's own NAME: the strongest sign there is, and not discounted for being common
DIRECTORY_MATCH_WEIGHT = 1.5
EXACT_FILENAME_WEIGHT = 20.0        # the request names the file ("Integrations.tsx") and the folder has it
MAX_BLOCK_CHARS = 1800
MAX_COUNTED_HITS = 4                # repeats of one word stop adding up here
TEST_OR_DOC_FACTOR = 0.5

_TEXT_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".html", ".vue", ".go", ".rs", ".java", ".cs", ".rb", ".php", ".sh", ".ps1", ".toml", ".yml", ".yaml", ".json", ".md", ".txt", ".sql",
)
_TEST_OR_DOC_DIRS = frozenset({"tests", "test", "__tests__", "spec", "docs", "doc", "eval_results", "source_docs", "fixtures", "examples"})

_FILENAME = re.compile(r"[\w.-]*\w\.(?:py|tsx?|jsx?|mjs|css|scss|json|md|html|ya?ml|toml|sh|ps1)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"`“]([^\"`”\n]{4,60})[\"`”]")
_IDENTIFIER = re.compile(
    r"--[a-z][\w-]*"                                              # a css custom property
    r"|\b[A-Za-z_][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"               # snake_case
    r"|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b"                             # camelCase
    r"|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"                     # PascalCase
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_STOPWORDS = frozenset("""
    about above after again against also always another anything because before being below between both build cannot change changes check could create does doing done dont down during each either else enough even
    ever every everything fix from further have having here hold into itself just keep know last later less like look looks lets made make makes making many maybe might more most much must need needs never next none
    nothing only onto other ought over please really right same should since some something sometimes still such take than that thats their theirs them then there these they thing things think this those though
    through today together under until update updates upon using very want wants well were what whats when where whether which while whole will with within without would write your yours yourself file files code
    sonic haiku sonnet gemini claude model models step steps turn turns
    redesign redesigns restyle restructure reorganize reorganise declutter simplify collapse improve improves remove delete deleted rename refactor implement adjust tweak insert replace modify
    stop stay show shows said says goes gets give gave tell told them then when with will would could been over onto from into than also each such very just only
""".split())


def _clean(texts: Iterable[Any]) -> List[str]:
    return [str(text or "")[:MAX_TEXT_CHARS] for text in texts if str(text or "").strip()]


def search_terms(texts: Iterable[Any]) -> List[str]:
    """The specific words in the request, most specific first, at most MAX_TERMS, no repeats (case-insensitive). The latest message comes first in `texts`. Pure."""
    chosen: List[str] = []
    seen = set()

    def add(term: str) -> None:
        cleaned = term.strip().strip(".,;:!?()[]{}")
        key = cleaned.lower()
        if len(cleaned) >= 4 and key not in seen and len(chosen) < MAX_TERMS:
            seen.add(key)
            chosen.append(cleaned)

    cleaned_texts = _clean(texts)
    for pattern in (_FILENAME, _QUOTED, _IDENTIFIER):
        for text in cleaned_texts:
            for found in pattern.finditer(text):
                add(found.group(1) if pattern is _QUOTED else found.group(0))
    plain: List[str] = []
    for text in cleaned_texts:
        for found in _WORD.finditer(text):
            word = found.group(0)
            if len(word) >= MIN_WORD_LENGTH and word.lower() not in _STOPWORDS and word.lower() not in seen and word.lower() not in (p.lower() for p in plain):
                plain.append(word)
    for word in sorted(plain, key=lambda w: -len(w)):
        add(word)
    return chosen


_COMMENT_OR_IMPORT = re.compile(r"^\s*(?:#|//|/\*|\*|<!--|\"\"\"|'''|import\s|from\s+\S+\s+import\b|export\s*\{|using\s|use\s)")


def _line_value(line: str) -> int:
    """How useful a matching line is as a place to read a window around: a declaration (2) over code (1) over a comment, docstring line or import (0). Pure."""
    if not line.strip() or _COMMENT_OR_IMPORT.match(line):
        return 0
    return 2 if not line[:1].isspace() else 1


def _is_test_or_doc(path: str) -> bool:
    parts = path.lower().split("/")
    name = parts[-1]
    return any(part in _TEST_OR_DOC_DIRS for part in parts[:-1]) or name.startswith("test_") or ".test." in name or ".spec." in name or name.endswith("_test.py") or name.endswith(".md")


def _scan(root: Path, paths: Sequence[str], terms: Sequence[str]) -> Tuple[Dict[str, Dict[str, Any]], Counter]:
    """Every file's matches: {path: {"name": terms in the path, "hits": {term: [(line number, line)]}, "counts": {term: n}}}, and how many files each term appears in."""
    lowered = [term.lower() for term in terms]
    found: Dict[str, Dict[str, Any]] = {}
    files_with: Counter = Counter()
    started = time.monotonic()
    scanned = 0
    for path in paths:
        if time.monotonic() - started > DEADLINE_SECONDS or scanned > MAX_SCAN_BYTES:
            break
        lower_path = path.lower()
        in_name = [term for term in lowered if term in lower_path]
        hits: Dict[str, List[Tuple[int, str]]] = {}
        counts: Dict[str, int] = {}
        if lower_path.endswith(_TEXT_EXTENSIONS):
            try:
                target = root / path
                if target.stat().st_size <= MAX_FILE_BYTES:
                    text = target.read_text(encoding="utf-8", errors="ignore")
                    scanned += len(text)
                    low_text = text.lower()
                    present = [term for term in lowered if term in low_text]
                    if present:
                        for number, line in enumerate(text.splitlines(), start=1):
                            low_line = line.lower()
                            for term in present:
                                if term in low_line:
                                    counts[term] = counts.get(term, 0) + 1
                                    kept = hits.setdefault(term, [])
                                    if len(kept) < 4:
                                        kept.append((number, line.strip()))
                                    else:
                                        weakest = min(range(len(kept)), key=lambda i: _line_value(kept[i][1]))
                                        if _line_value(line) > _line_value(kept[weakest][1]):
                                            kept[weakest] = (number, line.strip())
            except (OSError, ValueError):
                continue
        if in_name or hits:
            found[path] = {"name": in_name, "hits": hits, "counts": counts}
            for term in set(in_name) | set(hits):
                files_with[term] += 1
    return found, files_with


def rank(found: Dict[str, Dict[str, Any]], files_with: Counter) -> List[Tuple[float, str]]:
    """Files best first as (score, path). Pure."""
    scored: List[Tuple[float, str]] = []
    for path, info in found.items():
        score = 0.0
        matched = set(info["name"]) | set(info["hits"])
        basename = path.lower().rsplit("/", 1)[-1]
        for term in matched:
            weight = 1.0 / max(1.0, files_with[term] / 4.0)           # a word found in a handful of files points somewhere; one found in a hundred points nowhere
            if term == basename:
                score += EXACT_FILENAME_WEIGHT
            elif term in basename:
                score += NAME_MATCH_WEIGHT
            elif term in info["name"]:
                score += DIRECTORY_MATCH_WEIGHT * weight
            score += min(info["counts"].get(term, 0), MAX_COUNTED_HITS) * weight
        score *= 1.0 + 0.5 * (len(matched) - 1)                       # several different words beat one word repeated
        if _is_test_or_doc(path):
            score *= TEST_OR_DOC_FACTOR
        scored.append((score, path))
    return sorted(scored, key=lambda pair: (-pair[0], pair[1]))


def render(ranked: Sequence[Tuple[float, str]], found: Dict[str, Dict[str, Any]], files_with: Counter) -> str:
    """The prompt block for the best files, or "" when none is a real lead. Pure."""
    best = [(score, path) for score, path in ranked if score >= MIN_SCORE][:MAX_FILES_SHOWN]
    if not best:
        return ""
    best = [(score, path) for score, path in best if score >= best[0][0] * RELATIVE_CUTOFF]
    lines = [
        "WHERE TO LOOK (found by searching the folder for words from the request before your first step: a starting map, not proof). Go straight to the best match: read a window around the line you want "
        "(read_repo_file with start_line and line_count), then make the change. Search again only if none of these is the place."
    ]
    for _, path in best:
        info = found[path]
        why = []
        if info["name"]:
            why.append("name matches " + ", ".join(f'"{term}"' for term in sorted(info["name"])[:2]))
        if info["hits"]:
            why.append("contains " + ", ".join(f'"{term}"' for term in sorted(info["hits"], key=lambda t: files_with[t])[:3]))
        lines.append(f"- {path} ({'; '.join(why)})")
        candidates: Dict[int, str] = {}
        for term in sorted(info["hits"], key=lambda t: files_with[t]):
            for number, text in info["hits"][term]:
                candidates.setdefault(number, text)
        chosen = sorted(candidates, key=lambda n: (-_line_value(candidates[n]), n))[:MAX_LINES_PER_FILE]
        for number in sorted(chosen):
            lines.append(f"    line {number}: {candidates[number][:LINE_CHARS]}")
    return "\n".join(lines)[:MAX_BLOCK_CHARS]


def block_paths(block: str) -> List[str]:
    """The file paths a "where to look" block lists, best first. Parses the block render() wrote, so it is the one reader of that format. Pure."""
    paths: List[str] = []
    for line in str(block or "").splitlines():
        if line.startswith("- "):
            path = line[2:].split(" (", 1)[0].strip()
            if path:
                paths.append(path)
    return paths


def where_to_look(root: Any, paths: Sequence[str], texts: Iterable[Any]) -> str:
    """The "where to look" block for a request in a synced folder, or "" (no specific words, nothing found, or a folder that cannot be read). Blocking disk reads, bounded by DEADLINE_SECONDS; call it off the
    event loop. Never raises: a failure here must cost the turn nothing."""
    try:
        terms = search_terms(texts)
        if not terms or not paths:
            return ""
        found, files_with = _scan(Path(str(root)), list(paths), terms)
        return render(rank(found, files_with), found, files_with)
    except Exception:
        return ""
