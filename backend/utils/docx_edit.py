"""Editing an existing Word document, safely (see backend/services/local_edits.py, propose_local_edits).

The rules that make this safe, in the order they matter:

  1. THE ORIGINAL IS NEVER WRITTEN. An edit produces the bytes of a NEW copy; the browser saves it under a new name. Nothing here touches the original.
  2. EVERYTHING EXCEPT THE NAMED PARAGRAPHS IS UNCHANGED, AND IT IS CHECKED. Every part of the .docx zip other than word/document.xml is copied byte for byte. Inside
     document.xml, a check after the edit confirms that the document's structure (tables, section settings, everything that is not a paragraph) is identical, that every
     untouched paragraph is identical (canonical XML), and that each edited paragraph reads exactly as intended and kept its own paragraph formatting. If any of that
     fails, no bytes come back at all.
  3. EDITS ARE EXACT, LIKE CODE EDITS. Sonic names a paragraph by its number in the outline it read; text to replace must occur exactly once in that paragraph. Numbers
     refer to the document as it was read, so edits do not shift each other.
  4. ONLY PLAIN PARAGRAPHS CAN BE EDITED: only paragraph properties and ordinary runs of text, tabs and line breaks, outside tracked changes, fields, content controls,
     text boxes and the like. Anything else is shown in the outline (so Sonic can read it) but marked as not editable. Encrypted files, macro-enabled files, absurdly
     large or malformed ones, and anything with a DOCTYPE are refused before any editing.
  5. A paragraph inside the edited text keeps the formatting of the text it replaces (the first character replaced decides it); inserted paragraphs copy the paragraph
     properties of the one they follow.

The outline is what is kept on the server for Sonic to read (never the file itself): one line per paragraph, "[12] text", where "[12*]" cannot be edited and "[12~]" can
have its text changed but cannot be deleted. Pure functions; problems come back as DocxEditError with a message that is safe to show.
"""
import copy
import io
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
MAX_DOCX_BYTES = 3_000_000
MAX_MEMBERS = 3000
MAX_UNCOMPRESSED = 60_000_000
MAX_PARAGRAPHS = 3000
MAX_OPS = 40
MAX_TEXT = 5_000

# A paragraph may sit directly in the body, a table cell, and nothing else: anything wrapping it (a content control, a text box, a tracked insertion) is off limits.
_ALLOWED_PARENTS = {"body", "tbl", "tr", "tc"}
_ALLOWED_PARAGRAPH_CHILDREN = {"pPr", "r", "proofErr", "bookmarkStart", "bookmarkEnd"}
_ALLOWED_RUN_CHILDREN = {"rPr", "t", "tab", "br", "lastRenderedPageBreak"}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_OUTLINE_LINE = re.compile(r"^\[(\d+)([*~!]*)(?:\|([^\]]*))?\] ?(.*)$")
OUTLINE_HEADER = (
    "WORD DOCUMENT (an outline for editing; the file itself is not kept). Each paragraph is one line: [number] text. "
    "[number*] cannot be edited; [number~] can have its text changed but cannot be deleted; [number!] cannot have a paragraph inserted after it. Inside the text, \\n is a line break within the paragraph "
    "and \\\\ is a backslash. Headers, footers, text boxes, comments and images are not shown and are never changed. "
    "To change this document, propose word_edits using these paragraph numbers; the result is saved as a new copy and the original is never touched.\n---\n"
)


class DocxEditError(ValueError):
    """The document or the edits cannot be used; the message is safe to show to the person."""


def _q(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _local(element: Any) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


# ---- reading the file --------------------------------------------------------------------------------------------------------------------------

def _open(data: bytes) -> zipfile.ZipFile:
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise DocxEditError("That isn't a Word document.")
    if len(data) > MAX_DOCX_BYTES:
        raise DocxEditError(f"That document is too large to edit here (limit {MAX_DOCX_BYTES // 1_000_000} MB).")
    if bytes(data[:8]) == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise DocxEditError("That file is password-protected or an old .doc, which can't be edited here.")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise DocxEditError("That isn't a valid .docx file.")
    archive = zipfile.ZipFile(io.BytesIO(data))
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
        raise DocxEditError("That document is too large or too complex to edit here.")
    names = {info.filename for info in infos}
    if "word/document.xml" not in names:
        raise DocxEditError("That isn't a Word document (no document body).")
    if any(info.flag_bits & 0x1 for info in infos):
        raise DocxEditError("That document is encrypted, so it can't be edited here.")
    if any(name.lower().endswith("vbaproject.bin") for name in names):
        raise DocxEditError("That document contains macros, so it can't be edited here.")
    return archive


def _parse_document(archive: zipfile.ZipFile) -> Any:
    raw = archive.read("word/document.xml")
    if b"<!DOCTYPE" in raw[:4096] or b"<!ENTITY" in raw[:4096]:
        raise DocxEditError("That document's body uses features that can't be edited safely.")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, remove_comments=False, remove_pis=False)
    try:
        return etree.fromstring(raw, parser)
    except etree.XMLSyntaxError:
        raise DocxEditError("That document's body is damaged, so it can't be edited.")


def _body(root: Any) -> Any:
    body = root.find(_q("body"))
    if body is None:
        raise DocxEditError("That isn't a Word document (no document body).")
    return body


def _chain_is_plain(paragraph: Any, body: Any) -> bool:
    node = paragraph.getparent()
    while node is not None and node is not body:
        if _local(node) not in _ALLOWED_PARENTS:
            return False
        node = node.getparent()
    return node is body


def _run_is_plain(run: Any) -> bool:
    for child in run:
        name = _local(child)
        if name not in _ALLOWED_RUN_CHILDREN:
            return False
        if name == "br" and (child.get(_q("type")) not in (None, "textWrapping") or child.get(_q("clear")) is not None):
            return False
        if name == "rPr" and any(_local(sub) in ("rPrChange", "ins", "del", "moveFrom", "moveTo") for sub in child.iter()):
            return False
    return True


def _paragraph_properties(paragraph: Any) -> Optional[Any]:
    properties = paragraph.find(_q("pPr"))
    return properties


def _is_editable(paragraph: Any, body: Any) -> bool:
    if not _chain_is_plain(paragraph, body):
        return False
    for child in paragraph:
        name = _local(child)
        if name not in _ALLOWED_PARAGRAPH_CHILDREN:
            return False
        if name == "r" and not _run_is_plain(child):
            return False
        if name == "pPr" and any(_local(sub) in ("pPrChange", "ins", "del", "moveFrom", "moveTo", "rPrChange") for sub in child.iter()):
            return False
    return True


def _run_text(run: Any) -> str:
    parts: List[str] = []
    for child in run:
        name = _local(child)
        if name == "t":
            parts.append(child.text or "")
        elif name == "tab":
            parts.append("\t")
        elif name == "br":
            parts.append("\n")
    return "".join(parts)


def _plain_paragraph_text(paragraph: Any) -> str:
    return "".join(_run_text(run) for run in paragraph.findall(_q("r")))


def _display_text(paragraph: Any) -> str:
    """Everything the paragraph reads as, including text inside links and fields (for the outline only)."""
    parts: List[str] = []
    for element in paragraph.iter():
        name = _local(element)
        if name == "t":
            parts.append(element.text or "")
        elif name == "tab" and _local(element.getparent()) == "r":
            parts.append("\t")
        elif name == "br" and _local(element.getparent()) == "r":
            parts.append("\n")
    return "".join(parts)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _unescape(text: str) -> str:
    out: List[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            out.append("\n" if nxt == "n" else nxt)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _table_context(paragraph: Any, tables: Dict[Any, int]) -> str:
    cell = paragraph.getparent()
    if cell is None or _local(cell) != "tc":
        return ""
    row = cell.getparent()
    table = row.getparent() if row is not None else None
    if row is None or table is None or _local(row) != "tr":
        return ""
    row_number = [r for r in table if _local(r) == "tr"].index(row) + 1
    column = [c for c in row if _local(c) == "tc"].index(cell) + 1
    return f"table {tables.get(table, 0)} row {row_number} col {column}"


def _paragraph_infos(body: Any) -> List[Dict[str, Any]]:
    paragraphs = list(body.iter(_q("p")))
    if len(paragraphs) > MAX_PARAGRAPHS:
        raise DocxEditError(f"That document has too many paragraphs to edit here (limit {MAX_PARAGRAPHS}).")
    tables = {table: number for number, table in enumerate(body.iter(_q("tbl")), start=1)}
    infos: List[Dict[str, Any]] = []
    for number, paragraph in enumerate(paragraphs, start=1):
        editable = _is_editable(paragraph, body)
        properties = _paragraph_properties(paragraph)
        has_section = properties is not None and properties.find(_q("sectPr")) is not None
        parent = paragraph.getparent()
        only_in_cell = _local(parent) == "tc" and len(parent.findall(_q("p"))) == 1
        infos.append({
            "id": number, "element": paragraph, "editable": editable,
            "deletable": editable and not has_section and not only_in_cell,
            "text": _plain_paragraph_text(paragraph) if editable else _display_text(paragraph),
            "context": _table_context(paragraph, tables),
            "insertable": _chain_is_plain(paragraph, body),
        })
    return infos


# ---- the outline kept for Sonic to read ---------------------------------------------------------------------------------------------------------

def outline_text(data: bytes) -> str:
    """The outline of a .docx (see the module docstring). Raises DocxEditError when the file can't be used."""
    archive = _open(data)
    body = _body(_parse_document(archive))
    lines = []
    for info in _paragraph_infos(body):
        flag = ("" if info["editable"] else "*") + ("~" if info["editable"] and not info["deletable"] else "") + ("" if info["insertable"] else "!")
        context = f"|{info['context']}" if info["context"] else ""
        lines.append(f"[{info['id']}{flag}{context}] {_escape(info['text'])}")
    return OUTLINE_HEADER + "\n".join(lines) + "\n"


def parse_outline(text: str) -> Dict[int, Dict[str, Any]]:
    """The paragraphs of an outline as {number: {"editable", "deletable", "text"}}; {} when it is not one."""
    if not isinstance(text, str) or not text.startswith("WORD DOCUMENT"):
        return {}
    found: Dict[int, Dict[str, Any]] = {}
    for line in text.split("\n"):
        match = _OUTLINE_LINE.match(line)
        if match:
            flag = match.group(2)
            found[int(match.group(1))] = {
                "editable": "*" not in flag, "deletable": flag == "", "insertable": "!" not in flag, "text": _unescape(match.group(4)),
            }
    return found


# ---- checking and planning the edits (the same rules on an outline and on the real file) ------------------------------------------------------------

def _scrub(value: Any, what: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise DocxEditError(f"{what} must be text")
    text = _CONTROL_CHARS.sub("", value.replace("\r\n", "\n"))
    if not text and not allow_empty:
        raise DocxEditError(f"{what} is empty")
    if len(text) > MAX_TEXT:
        raise DocxEditError(f"{what} is too long ({len(text)} characters; the limit is {MAX_TEXT})")
    return text


def plan_edits(paragraphs: Dict[int, Dict[str, Any]], ops: Any) -> Dict[str, Any]:
    """Checks `ops` against paragraphs shaped like {number: {"editable", "deletable", "text"}} and returns
    {"replace": {n: (old, new)}, "delete": {n}, "insert": {n: [text, ...]}, "clean": [normalised ops]}. Raises DocxEditError naming the first problem."""
    if not isinstance(ops, list) or not ops:
        raise DocxEditError("word_edits must be a non-empty list")
    if len(ops) > MAX_OPS:
        raise DocxEditError(f"too many word edits ({len(ops)}); propose at most {MAX_OPS} at once")
    replace: Dict[int, Tuple[str, str]] = {}
    delete: set = set()
    insert: Dict[int, List[str]] = {}
    clean: List[Dict[str, Any]] = []
    for index, op in enumerate(ops, start=1):
        label = f"word edit {index}"
        if not isinstance(op, dict) or op.get("op") not in ("replace", "insert_after", "delete"):
            raise DocxEditError(f"{label}: op must be replace, insert_after or delete")
        number = op.get("paragraph")
        if not isinstance(number, int) or isinstance(number, bool) or number not in paragraphs:
            raise DocxEditError(f"{label}: there is no paragraph {number!r} in the outline")
        target = paragraphs[number]
        kind = op["op"]
        if kind == "replace":
            if not target["editable"]:
                raise DocxEditError(f"{label}: paragraph {number} has content that can't be edited safely (links, fields, tracked changes, text boxes or similar)")
            if number in replace or number in delete:
                raise DocxEditError(f"{label}: paragraph {number} is already changed by another edit; put it all in one")
            old = _scrub(op.get("old"), f"{label}: old")
            new = _scrub(op.get("new"), f"{label}: new", allow_empty=True)
            occurrences = target["text"].count(old)
            if occurrences == 0:
                raise DocxEditError(f"{label}: the text to replace was not found in paragraph {number}. Copy it exactly from the outline.")
            if occurrences > 1:
                raise DocxEditError(f"{label}: the text to replace appears {occurrences} times in paragraph {number}; include more surrounding words so it appears once")
            if old == new:
                raise DocxEditError(f"{label}: old and new are identical")
            replace[number] = (old, new)
            clean.append({"op": "replace", "paragraph": number, "old": old, "new": new})
        elif kind == "delete":
            if not target["deletable"]:
                raise DocxEditError(f"{label}: paragraph {number} can't be deleted (it is not editable, ends a section, or is the only paragraph in its table cell)")
            if number in replace or number in delete:
                raise DocxEditError(f"{label}: paragraph {number} is already changed by another edit")
            delete.add(number)
            clean.append({"op": "delete", "paragraph": number})
        else:
            if not target.get("insertable", True):
                raise DocxEditError(f"{label}: a paragraph can't be inserted after paragraph {number} (it sits inside a text box, content control or tracked change)")
            text = _scrub(op.get("text"), f"{label}: text")
            insert.setdefault(number, []).append(text)
            clean.append({"op": "insert_after", "paragraph": number, "text": text})
    return {"replace": replace, "delete": delete, "insert": insert, "clean": clean}


def edited_texts(paragraphs: Dict[int, Dict[str, Any]], plan: Dict[str, Any]) -> List[str]:
    """The document's paragraphs as plain text lines after the plan, in order (what the person previews)."""
    out: List[str] = []
    for number in sorted(paragraphs):
        if number in plan["delete"]:
            pass
        elif number in plan["replace"]:
            old, new = plan["replace"][number]
            out.append(paragraphs[number]["text"].replace(old, new, 1))
        else:
            out.append(paragraphs[number]["text"])
        out.extend(plan["insert"].get(number, []))
    return out


def original_texts(paragraphs: Dict[int, Dict[str, Any]]) -> List[str]:
    return [paragraphs[number]["text"] for number in sorted(paragraphs)]


# ---- making the copy ----------------------------------------------------------------------------------------------------------------------------

def _make_runs_text(run: Any, text: str) -> None:
    """Sets the text a run holds, keeping its formatting (rPr)."""
    for child in list(run):
        if _local(child) != "rPr":
            run.remove(child)
    buffer: List[str] = []

    def flush() -> None:
        if buffer:
            node = etree.SubElement(run, _q("t"))
            node.text = "".join(buffer)
            node.set(f"{{{XML_NS}}}space", "preserve")
            buffer.clear()

    for char in text:
        if char == "\t":
            flush()
            etree.SubElement(run, _q("tab"))
        elif char == "\n":
            flush()
            etree.SubElement(run, _q("br"))
        else:
            buffer.append(char)
    flush()


def _replace_in_paragraph(paragraph: Any, old: str, new: str) -> None:
    runs = paragraph.findall(_q("r"))
    texts = [_run_text(run) for run in runs]
    full = "".join(texts)
    start = full.find(old)
    end = start + len(old)
    position = 0
    placed = False
    for run, text in zip(runs, texts):
        run_start, run_end = position, position + len(text)
        position = run_end
        if not text or run_end <= start or run_start >= end:
            continue
        before = text[:max(start - run_start, 0)]
        after = text[end - run_start:] if end < run_end else ""
        combined = before + (new if not placed else "") + after
        placed = True
        if combined:
            _make_runs_text(run, combined)
        else:
            paragraph.remove(run)
    if not placed and new:                              # defensive: the planner guarantees a match
        raise DocxEditError("the text to replace could not be located in the file")


def _new_paragraph_after(target: Any, text: str) -> Any:
    paragraph = etree.Element(_q("p"))
    properties = _paragraph_properties(target)
    if properties is not None:
        kept = copy.deepcopy(properties)
        for stale in kept.findall(_q("sectPr")):
            kept.remove(stale)
        paragraph.append(kept)
    run = etree.SubElement(paragraph, _q("r"))
    first_run = next(iter(target.findall(_q("r"))), None)
    if first_run is not None and first_run.find(_q("rPr")) is not None:
        run.append(copy.deepcopy(first_run.find(_q("rPr"))))
    _make_runs_text(run, text)
    return paragraph


def _c14n(element: Any) -> bytes:
    return etree.tostring(element, method="c14n")


def _skeleton(root: Any) -> bytes:
    clone = copy.deepcopy(root)
    for paragraph in list(clone.iter(_q("p"))):
        paragraph.getparent().remove(paragraph)
    return _c14n(clone)


def apply_edits(data: bytes, ops: Any) -> bytes:
    """The bytes of an edited COPY of the document, or DocxEditError. See the module docstring for what is guaranteed."""
    archive = _open(data)
    root = _parse_document(archive)
    body = _body(root)
    infos = _paragraph_infos(body)
    by_number = {info["id"]: info for info in infos}
    plan = plan_edits(
        {n: {"editable": i["editable"], "deletable": i["deletable"], "insertable": i["insertable"], "text": i["text"]} for n, i in by_number.items()}, ops,
    )

    original_skeleton = _skeleton(root)
    original_c14n = {n: _c14n(i["element"]) for n, i in by_number.items()}
    original_ppr = {n: (_c14n(_paragraph_properties(i["element"])) if _paragraph_properties(i["element"]) is not None else b"") for n, i in by_number.items()}

    expected: List[Tuple[str, Any, Any]] = []          # what the edited document's paragraphs must be, in order
    for number in sorted(by_number):
        element = by_number[number]["element"]
        inserted = plan["insert"].get(number, [])
        if number in plan["delete"]:
            pass
        elif number in plan["replace"]:
            old, new = plan["replace"][number]
            expected.append(("edited", by_number[number]["text"].replace(old, new, 1), original_ppr[number]))
        else:
            expected.append(("same", original_c14n[number], None))
        for text in inserted:
            expected.append(("new", text, None))

    for number in sorted(by_number):
        element = by_number[number]["element"]
        if number in plan["replace"]:
            _replace_in_paragraph(element, *plan["replace"][number])
        cursor = element
        for text in plan["insert"].get(number, []):
            fresh = _new_paragraph_after(element, text)
            cursor.addnext(fresh)
            cursor = fresh
        if number in plan["delete"]:
            element.getparent().remove(element)

    edited_xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as out:
        for info in archive.infolist():
            content = edited_xml if info.filename == "word/document.xml" else archive.read(info.filename)
            clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            out.writestr(clone, content)
    result = output.getvalue()
    _verify(data, result, original_skeleton, expected)
    return result


def _verify(original: bytes, edited: bytes, original_skeleton: bytes, expected: List[Tuple[str, Any, Any]]) -> None:
    """Re-reads the finished copy and confirms it is exactly the original plus the intended edits. Raises DocxEditError (so nothing is returned) otherwise."""
    problem = "The edited copy did not pass its safety check, so nothing was written."
    before_zip, after_zip = zipfile.ZipFile(io.BytesIO(original)), zipfile.ZipFile(io.BytesIO(edited))
    if [i.filename for i in before_zip.infolist()] != [i.filename for i in after_zip.infolist()]:
        raise DocxEditError(problem)
    for info in before_zip.infolist():
        if info.filename != "word/document.xml" and before_zip.read(info.filename) != after_zip.read(info.filename):
            raise DocxEditError(problem)
    new_root = _parse_document(after_zip)
    if _skeleton(new_root) != original_skeleton:
        raise DocxEditError(problem)
    actual = list(_body(new_root).iter(_q("p")))
    if len(actual) != len(expected):
        raise DocxEditError(problem)
    for paragraph, (kind, value, properties) in zip(actual, expected):
        if kind == "same":
            if _c14n(paragraph) != value:
                raise DocxEditError(problem)
        else:
            if _plain_paragraph_text(paragraph) != value:
                raise DocxEditError(problem)
            if kind == "edited":
                current = _paragraph_properties(paragraph)
                if (_c14n(current) if current is not None else b"") != properties:
                    raise DocxEditError(problem)
