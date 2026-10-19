"""Turns any JSON file into text chunks a knowledge base can search.

It works from the SHAPE of the file, not from any one layout:

- A list of objects (a database export, an array of tickets) becomes one chunk per record, or a few records per chunk when there are very many.
- An object with lists inside it (a weekly log with `standup_entries`, `notes`, `contacts` ...) becomes one chunk per record of each list, headed with the file name
  and the object's own plain top-level values (a week, an owner), so every chunk says what it belongs to.
- An object with no lists (a config file, a settings dump) becomes a few "key: value" chunks, nested keys written as `server.port: 8080`.
- JSON Lines (one object per line) and a bare list of values are handled too.

What is dropped, because it only adds noise to a search: empty values, ids that are bare UUIDs or Mongo ids, and the usual bookkeeping keys (`id`, `_id`, `uuid`,
`etag`, `generated_at`, `updated_at`, `modified_at`). What is masked, because a private library is still not the place for it: the value of any key named like a
password, secret, token or API key, and anything inside a string that looks like a known key format (AWS, GitHub, OpenAI, Google, Slack, a JWT, a private-key block).

Every chunk is kept under CHUNK_CHARS so the text splitter that runs afterwards leaves it whole, and a record that is longer than that is split on its own lines
with the same header, so no piece loses what it belongs to. Limits (size, number of chunks) turn into a clear JsonIngestError, never a half-indexed file.

This module is deliberately free of `backend.*` imports, like function_app.py: it runs as part of a standalone subprocess.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MAX_BYTES = int(os.getenv("JSON_INGEST_MAX_BYTES", str(10 * 1024 * 1024)))
MAX_CHUNKS = int(os.getenv("JSON_INGEST_MAX_CHUNKS", "1500"))
CHUNK_CHARS = 900                 # the splitter downstream keeps anything up to 1000 characters whole
MAX_LINE_CHARS = 600              # one value longer than this is continued over several lines
MAX_HEADER_CHARS = 220
MAX_DEPTH = 12
MAX_LINES_PER_RECORD = 400
MAX_LIST_ITEMS_INLINE = 50
PACK_ABOVE_RECORDS = 300          # up to this many records, each gets a chunk of its own; beyond it they are packed together to keep the cost sensible
REDACTED = "[redacted]"

NOISE_KEYS = {"id", "_id", "uuid", "guid", "etag", "__v", "generated_at", "updated_at", "modified_at"}
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_SECRET_KEY_RE = re.compile(
    r"(?:^|[_.\-\s])(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|bearer|private[_-]?key|credentials?|access[_-]?key|client[_-]?secret)(?:$|[_.\-\s])"
)
_SECRET_VALUE_RE = re.compile(
    r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)


class JsonIngestError(Exception):
    """Why a JSON file could not be indexed, worded to be shown. Never contains any of the file's own content."""


# ---------------------------------------------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------------------------------------------

def parse(raw: bytes) -> Any:
    """The file's content as Python values. Accepts a JSON document, or JSON Lines (one value per line)."""
    if raw is None or len(raw) == 0:
        raise JsonIngestError("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise JsonIngestError(f"The file is {len(raw) // (1024 * 1024)} MB, which is over the {MAX_BYTES // (1024 * 1024)} MB limit for JSON files.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise JsonIngestError("The file is not UTF-8 text, so it can't be read as JSON.")
    if not text.strip():
        raise JsonIngestError("The file is empty.")
    try:
        return json.loads(text)
    except ValueError as error:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(line) for line in lines]
            except ValueError:
                pass
        where = f" (line {error.lineno}, column {error.colno})" if hasattr(error, "lineno") else ""
        raise JsonIngestError(f"This is not valid JSON{where}.")


# ---------------------------------------------------------------------------------------------------------------
# Turning values into lines of text
# ---------------------------------------------------------------------------------------------------------------

def _snake(key: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key)).lower()


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(_snake(key)))


def _is_noise(key: str) -> bool:
    return _snake(key) in NOISE_KEYS


def _scalar(value: Any, key: str = "") -> Optional[str]:
    """One value as text, or None when it should not appear (empty, or an id)."""
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)           # a number is never a secret, so a count like `token_count: 5` stays
    text = str(value).strip()
    if not text or _UUID_RE.match(text) or _OBJECT_ID_RE.match(text):
        return None
    if key and _is_secret_key(key) and len(text) >= 8:
        return REDACTED
    stamp = _ISO_TIME_RE.match(text)
    if stamp:
        return f"{stamp.group(1)} {stamp.group(2)}"      # seconds, fractions and the zone are noise for a search
    return _SECRET_VALUE_RE.sub(REDACTED, text)


def _wrap(label: str, text: str) -> List[str]:
    """`label: text` as one line. A value with several lines keeps them as the lines that follow the label (a bullet list stays a bullet list); a single line that
    is too long is cut at a space and the rest says `label (continued)`."""
    lines: List[str] = []
    first = True
    for paragraph in text.split("\n"):
        paragraph = paragraph.rstrip()
        if not paragraph.strip():
            continue
        cut_long = False
        while len(paragraph) > MAX_LINE_CHARS:
            cut = paragraph.rfind(" ", 0, MAX_LINE_CHARS)
            cut = cut if cut > MAX_LINE_CHARS // 2 else MAX_LINE_CHARS
            lines.append(f"{label}: {paragraph[:cut].strip()}" if first else (f"{label} (continued): " if cut_long else "") + paragraph[:cut].strip())
            first, cut_long, paragraph = False, True, paragraph[cut:].strip()
        if paragraph:
            lines.append(f"{label}: {paragraph}" if first else (f"{label} (continued): {paragraph}" if cut_long else paragraph))
        first = False
    return lines


_ISO_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def _flatten(value: Any, prefix: str, out: List[str], depth: int = 0, lists_found: Optional[List[Tuple[str, List[Any]]]] = None) -> None:
    """Appends the lines for `value` (a dict, list or scalar) under `prefix`. With `lists_found`, a list of objects is NOT written out but handed back as
    (path, items) to become sections of its own; without it (inside a record) such a list is written inline as `key[1].name: ...`."""
    if len(out) >= MAX_LINES_PER_RECORD or depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if _is_noise(key):
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, list) and lists_found is not None and child and all(isinstance(item, dict) for item in child):
                lists_found.append((path, child))
            elif isinstance(child, (dict, list)):
                _flatten(child, path, out, depth + 1, lists_found)
            else:
                text = _scalar(child, str(key))
                if text is not None:
                    out.extend(_wrap(path, text))
    elif isinstance(value, list):
        if not value:
            return
        if all(not isinstance(item, (dict, list)) for item in value):
            parts = [text for text in (_scalar(item, prefix) for item in value) if text is not None]
            joined = ", ".join(parts)
            if joined:
                out.extend(_wrap(prefix or "values", joined))
            return
        for index, item in enumerate(value[:MAX_LIST_ITEMS_INLINE], 1):
            _flatten(item, f"{prefix}[{index}]", out, depth + 1)
        if len(value) > MAX_LIST_ITEMS_INLINE:
            out.append(f"{prefix}: (and {len(value) - MAX_LIST_ITEMS_INLINE} more)")
    else:
        text = _scalar(value, prefix)
        if text is not None:
            out.extend(_wrap(prefix or "value", text))


# ---------------------------------------------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------------------------------------------

def _pieces(lines: List[str], room: int) -> List[List[str]]:
    """The lines grouped so that each group is at most `room` characters (a single line is never cut here: _wrap already bounded it)."""
    groups: List[List[str]] = [[]]
    size = 0
    for line in lines:
        if groups[-1] and size + len(line) + 1 > room:
            groups.append([])
            size = 0
        groups[-1].append(line)
        size += len(line) + 1
    return [group for group in groups if group]


def _header(filename: str, scalars: List[str]) -> str:
    summary = ", ".join(scalars)
    if len(summary) > MAX_HEADER_CHARS:
        summary = summary[:MAX_HEADER_CHARS].rsplit(",", 1)[0]
    return f"{filename} | {summary}" if summary else filename


def _section_chunks(header: str, path: str, records: List[List[str]]) -> List[Tuple[str, str]]:
    """(text, section label) for each chunk of one list of records."""
    total = len(records)
    room = CHUNK_CHARS - len(header) - len(path) - 60
    chunks: List[Tuple[str, str]] = []
    if total <= PACK_ABOVE_RECORDS:
        for number, lines in enumerate(records, 1):
            parts = _pieces(lines, room)
            for part_number, part in enumerate(parts, 1):
                suffix = f", part {part_number} of {len(parts)}" if len(parts) > 1 else ""
                chunks.append((f"{header}\nsection: {path} (entry {number} of {total}{suffix})\n" + "\n".join(part), path))
        return chunks
    pending: List[str] = []
    first_number, size = 1, 0
    for number, lines in enumerate(records, 1):
        for part in _pieces(lines, room):
            block = "\n".join(part)
            if pending and size + len(block) + 2 > room:
                chunks.append((f"{header}\nsection: {path} (entries {first_number} to {number - 1} of {total})\n" + "\n\n".join(pending), path))
                pending, first_number, size = [], number, 0
            pending.append(block)
            size += len(block) + 2
    if pending:
        chunks.append((f"{header}\nsection: {path} (entries {first_number} to {total} of {total})\n" + "\n\n".join(pending), path))
    return chunks


def chunk_texts(data: Any, filename: str) -> List[Tuple[str, str]]:
    """(text, section path) for every chunk of the file, in order. Raises JsonIngestError for a file that is too big or has nothing to index."""
    lines: List[str] = []
    lists_found: List[Tuple[str, List[Any]]] = []
    if isinstance(data, dict):
        _flatten(data, "", lines, 0, lists_found)
    elif isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        lists_found.append(("items", data))
    elif isinstance(data, list):
        _flatten(data, "values", lines, 0, lists_found)
    else:
        _flatten(data, "value", lines, 0, lists_found)

    chunks: List[Tuple[str, str]] = []
    has_sections = bool(lists_found)
    # The plain top-level values head every chunk of a file that has record lists; in a file with none they ARE the content.
    header = _header(filename, [line for line in lines if ": " in line and "\n" not in line][:6]) if has_sections else filename
    for path, items in lists_found:
        records: List[List[str]] = []
        for item in items:
            record: List[str] = []
            _flatten(item, "", record, 0)
            if record:
                records.append(record)
        if records:
            chunks.extend(_section_chunks(header, path, records))
    if lines and not has_sections:
        room = CHUNK_CHARS - len(header) - 40
        parts = _pieces(lines, room)
        for number, part in enumerate(parts, 1):
            chunks.append((f"{header}\n" + (f"(part {number} of {len(parts)})\n" if len(parts) > 1 else "") + "\n".join(part), "properties"))
    if not chunks:
        raise JsonIngestError("The file has no content to index (every value is empty).")
    if len(chunks) > MAX_CHUNKS:
        raise JsonIngestError(f"The file would make {len(chunks)} chunks, over the limit of {MAX_CHUNKS}. Split it into smaller files.")
    return chunks


def build_documents(raw: bytes, filename: str, affiliate: str, document_class: Any, ingest_id: str) -> List[Any]:
    """The Documents to embed for one uploaded JSON file. `document_class` is langchain's Document, passed in so this module needs no imports of its own.
    Each carries the same metadata a PDF page does (affiliate, source, page) plus its kind, its section of the file, and `ingest_id`, which marks which
    upload it came from so a re-upload can replace the earlier chunks only after the new ones are safely in."""
    chunks = chunk_texts(parse(raw), filename)
    return [
        document_class(
            page_content=text,
            metadata={"affiliate": affiliate, "source": filename, "page": number, "doc_type": "json", "json_section": path, "ingest_id": ingest_id},
        )
        for number, (text, path) in enumerate(chunks, 1)
    ]
