"""Spreadsheets for the ingestion pipeline (function_app.py): .csv, .tsv, .xlsx and .xlsm turned into readable text, one block per sheet, or refused in words.

Like text_files.py, this is deliberately free of `backend.*` imports and uses only the standard library (csv, zipfile, xml), so no new requirement is needed.

WHAT A FILE MUST SURVIVE
- ENCODING. Delimited files are read with text_files.decode_text (UTF-8, UTF-16 with BOM, Windows-1252).
- SIZE. A CSV/TSV over TEXT_INGEST_MAX_BYTES, or a workbook over DOCX_INGEST_MAX_BYTES, or one that unzips to more than MAX_WORKBOOK_UNZIPPED_BYTES, is refused.
- EMPTY. A file with no readable cells is refused.
- SHAPE. A cell outside the 16,384 columns Excel allows is refused (a crafted position would otherwise make the reader build a list of trillions of empty cells).

HOW A TABLE IS WRITTEN
Chunks are searched and read on their own, so a row far down a big table must still say which column each value belongs to. The header row (the first row with two or more cells, when none of them is a number)
is written once as it is, and every row after it is written as `Header: value | Header: value`, leaving out empty cells. Rows above the header (a title) and tables whose first row is not a header
(its cells include numbers) are written as plain `a | b | c` lines. Cells have their whitespace collapsed so a cell with line breaks stays on one line.

WHAT A WORKBOOK GIVES
- Shared strings and inline strings are both read. Hidden and very hidden sheets are skipped (they are often what the author did not mean to share); the count is logged.
- A cell whose number format is a date or time is written as a date or time (2023-07-14, 2023-07-14 09:30, 09:30), in either of Excel's date systems.
- Formulas are not evaluated; the value the file stored for them is used. A formula that has no stored value (many scripts write those) is left empty, and the count is logged.

Everything that is the FILE's fault raises TextIngestError, which the pipeline handles the way it handles a bad text or Word file. The messages are fixed words, never the file's content.
"""
import csv
import datetime
import io
import logging
import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

try:
    from . import text_files
except ImportError:  # run as a script, where this folder is on the path instead of being a package
    import text_files

logger = logging.getLogger(__name__)

TextIngestError = text_files.TextIngestError      # the same class the pipeline catches, whichever way this folder was imported

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
SPREADSHEET_EXTENSIONS = (".csv", ".tsv", ".xlsx", ".xlsm")
# A worksheet is XML, and parsing XML takes many times its size in memory (ElementTree builds the whole tree), so the limit on what a workbook may unzip to is much lower than a Word file's:
# the ingestion process shares the server's memory (2026-10-25: a sandbox run took the instance down for the same reason).
MAX_WORKBOOK_UNZIPPED_BYTES = int(os.getenv("XLSX_INGEST_MAX_UNZIPPED_BYTES", str(30 * 1024 * 1024)))
MAX_COLUMNS = 16384          # Excel's own limit: column XFD
MAX_LABEL_CHARS = 60         # a long header is cut where it is repeated on every row

_NUMBER = re.compile(r"^[-+$€£]?\d[\d,.]*%?$")
_BUILTIN_DATE_FORMATS = frozenset(list(range(14, 23)) + list(range(27, 37)) + list(range(45, 48)) + list(range(50, 59)))
_EPOCH_1900 = datetime.datetime(1899, 12, 30)      # Excel's day 1 is 1900-01-01 and it counts a 1900-02-29 that never existed, so from 1900-03-01 on this epoch is exact
_EPOCH_1904 = datetime.datetime(1904, 1, 1)
_MAX_SERIAL = 2958465                              # 9999-12-31


# ---- the table -> text step (shared by delimited files and every sheet) -----------------------------------------------------------------------------------

def _clean_rows(rows) -> list:
    out = []
    for row in rows:
        cells = [" ".join(cell.split()) for cell in row]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            out.append(cells)
    return out


def _header_index(rows: list):
    """Index of the header row: the first row with two or more cells, provided none of them is a number. None when there is no such row."""
    for index, cells in enumerate(rows):
        filled = [cell for cell in cells if cell]
        if len(filled) >= 2:
            return None if any(_NUMBER.match(cell) for cell in filled) else index
    return None


def _rows_to_text(rows) -> str:
    rows = _clean_rows(rows)
    header = _header_index(rows)
    labels = [cell[:MAX_LABEL_CHARS] for cell in rows[header]] if header is not None else []
    lines = []
    for index, cells in enumerate(rows):
        if header is None or index <= header:
            lines.append(" | ".join(cells))
        else:
            lines.append(" | ".join(f"{labels[i]}: {cell}" if i < len(labels) and labels[i] else cell for i, cell in enumerate(cells) if cell))
    return "\n".join(lines)


# ---- delimited files -------------------------------------------------------------------------------------------------------------------------------

def _delimited_text(raw: bytes, filename: str) -> str:
    if len(raw) > text_files.MAX_BYTES:
        raise TextIngestError("The file is too big to read as a table.")
    delimiter = "\t" if filename.endswith(".tsv") else ","
    try:
        rows = list(csv.reader(io.StringIO(text_files.decode_text(raw)), delimiter=delimiter))
    except csv.Error:
        raise TextIngestError("The file is not a readable table.")
    return _rows_to_text(rows)


# ---- workbooks -------------------------------------------------------------------------------------------------------------------------------------

def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    if letters and len(letters.group(0)) > 3:          # XFD is the last column; refuse before computing a number nobody could allocate
        raise TextIngestError("The workbook has cells outside the columns a spreadsheet can have.")
    index = 0
    for ch in (letters.group(0) if letters else "A"):
        index = index * 26 + (ord(ch) - ord("A") + 1)
    if index > MAX_COLUMNS:
        raise TextIngestError("The workbook has cells outside the columns a spreadsheet can have.")
    return index - 1


def _shared_strings(archive: zipfile.ZipFile) -> list:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(NS_MAIN + "t")) for si in root.iter(NS_MAIN + "si")]


def _is_date_code(code: str) -> bool:
    """True when a custom number format shows a date or time: a y, m, d, h or s outside quotes, [brackets] and escaped characters (so "General" and 0.00E+00 are not dates)."""
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code)
    return re.search(r"[ymdhsYMDHS]", code) is not None


def _date_styles(archive: zipfile.ZipFile) -> frozenset:
    """Indexes of the cell styles (the s= on a cell) whose number format is a date or time."""
    if "xl/styles.xml" not in archive.namelist():
        return frozenset()
    root = ET.fromstring(archive.read("xl/styles.xml"))
    custom = {}
    for fmt in root.iter(NS_MAIN + "numFmt"):
        if (fmt.get("numFmtId") or "").isdigit():
            custom[int(fmt.get("numFmtId"))] = fmt.get("formatCode", "")
    xfs = root.find(NS_MAIN + "cellXfs")
    found = set()
    for index, xf in enumerate(xfs.findall(NS_MAIN + "xf") if xfs is not None else []):
        fmt_id = xf.get("numFmtId") or "0"
        if fmt_id.isdigit() and (int(fmt_id) in _BUILTIN_DATE_FORMATS or _is_date_code(custom.get(int(fmt_id), ""))):
            found.add(index)
    return frozenset(found)


def _is_1904(book) -> bool:
    props = book.find(NS_MAIN + "workbookPr")
    return props is not None and (props.get("date1904") or "").lower() in ("1", "true")


def _serial_text(raw_value: str, epoch: datetime.datetime) -> str:
    """A date serial (days since the epoch, with the time of day as the fraction) as 2023-07-14, 2023-07-14 09:30 or 09:30; the number itself when it is not a plausible date."""
    try:
        serial = float(raw_value)
    except ValueError:
        return raw_value
    if not 0 <= serial <= _MAX_SERIAL:
        return raw_value
    seconds = round(serial * 86400)
    if epoch is _EPOCH_1900 and serial < 61:             # before the phantom 1900-02-29 the 1900 system counts from one day later
        seconds += 86400
    moment = epoch + datetime.timedelta(seconds=seconds)
    if serial < 1:
        return moment.strftime("%H:%M:%S" if moment.second else "%H:%M")
    if seconds % 86400 == 0:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M:%S" if moment.second else "%Y-%m-%d %H:%M")


def _sheet_targets(archive: zipfile.ZipFile) -> list:
    """(sheet name, path inside the archive, hidden) for each sheet, in workbook order; and whether the workbook counts dates from 1904."""
    rels = {}
    rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for rel in rel_root.iter(NS_PKG_REL + "Relationship"):
        target = rel.get("Target", "")
        path = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
        rels[rel.get("Id")] = posixpath.normpath(path)
    book = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = []
    for sheet in book.iter(NS_MAIN + "sheet"):
        path = rels.get(sheet.get(NS_REL_ID))
        if path:
            sheets.append((sheet.get("name", "Sheet"), path, (sheet.get("state") or "visible") != "visible"))
    return sheets, _is_1904(book)


def _sheet_rows(xml_bytes: bytes, strings: list, date_styles: frozenset = frozenset(), epoch: datetime.datetime = _EPOCH_1900) -> tuple:
    """(rows, formulas without a stored value). A cell with no position (r=) goes in the next column of its row, as Excel reads it."""
    root = ET.fromstring(xml_bytes)
    rows, unvalued = [], 0
    for row in root.iter(NS_MAIN + "row"):
        cells = []
        for cell in row.findall(NS_MAIN + "c"):
            index = _column_index(cell.get("r")) if cell.get("r") else len(cells)
            if index >= MAX_COLUMNS:
                raise TextIngestError("The workbook has cells outside the columns a spreadsheet can have.")
            while len(cells) <= index:
                cells.append("")
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(NS_MAIN + "t"))
            else:
                raw_value = cell.findtext(NS_MAIN + "v") or ""
                if kind == "s" and raw_value.isdigit() and int(raw_value) < len(strings):
                    value = strings[int(raw_value)]
                elif kind == "b":
                    value = "TRUE" if raw_value == "1" else "FALSE"
                elif kind in (None, "n") and raw_value and (cell.get("s") or "").isdigit() and int(cell.get("s")) in date_styles:
                    value = _serial_text(raw_value, epoch)
                else:
                    value = raw_value
                if not raw_value and cell.find(NS_MAIN + "f") is not None:
                    unvalued += 1
            cells[index] = value
        rows.append(cells)
    return rows, unvalued


def _workbook_text(raw: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise TextIngestError("The file is not a readable Excel workbook.")
    try:
        if sum(info.file_size for info in archive.infolist()) > MAX_WORKBOOK_UNZIPPED_BYTES:
            raise TextIngestError("The workbook unzips to more than the size limit.")
        strings = _shared_strings(archive)
        date_styles = _date_styles(archive)
        sheets, is_1904 = _sheet_targets(archive)
        epoch = _EPOCH_1904 if is_1904 else _EPOCH_1900
        blocks, hidden, unvalued = [], 0, 0
        for name, path, is_hidden in sheets:
            if is_hidden:
                hidden += 1
                continue
            rows, missing = _sheet_rows(archive.read(path), strings, date_styles, epoch)
            unvalued += missing
            text = _rows_to_text(rows)
            if text:
                blocks.append(f"Sheet: {name}\n{text}")
        if hidden or unvalued:
            logger.info("   Workbook: skipped %d hidden sheet(s); %d formula cell(s) had no stored value and were left empty.", hidden, unvalued)
        return "\n\n".join(blocks)
    except TextIngestError:
        raise
    except (KeyError, ET.ParseError, zipfile.BadZipFile, ValueError):
        raise TextIngestError("The file is not a readable Excel workbook.")
    finally:
        archive.close()


def spreadsheet_text(raw: bytes, filename: str) -> str:
    """The readable text of a .csv, .tsv, .xlsx or .xlsm file, or TextIngestError."""
    name = filename.lower()
    if name.endswith((".csv", ".tsv")):
        text = _delimited_text(raw, name)
    elif name.endswith((".xlsx", ".xlsm")):
        if len(raw) > text_files.MAX_DOCX_BYTES:
            raise TextIngestError("The workbook is too big to read.")
        text = _workbook_text(raw)
    else:
        raise TextIngestError("Not a spreadsheet this pipeline reads.")
    if not text.strip():
        raise TextIngestError("The spreadsheet has no readable cells in it.")
    return text
