"""A spreadsheet or CSV attached to a chat, turned into text the model can answer from.

A whole sheet does not fit in a prompt, and asking a model to summarize one loses exactly the numbers a person asks about. So what the model gets is
a fixed, honest picture: the size, the column names, the first rows as they are, and, for every number column, totals over ALL the rows (not just
the ones shown) and, for short-list columns, how often each value appears. It also says plainly that it is looking at part of the sheet, so it can
tell the person what it cannot see instead of guessing.

No model call is made here: the text is built with fixed rules, so it is cheap and the same file always reads the same way.
"""
import io
import logging
from typing import Dict, List

import pandas as pd

logger = logging.getLogger("SASS Logger")

TABLE_EXTENSIONS = {"csv", "tsv", "xlsx", "xlsm"}
# Formats that are common but that this app cannot read; the person is told what to do instead of getting a blank answer.
UNREADABLE_EXTENSIONS = {"xls", "doc", "ppt", "pptx", "pages", "numbers", "key", "heic", "heif"}

MAX_SHEETS = 5
MAX_COLUMNS = 25
MAX_PREVIEW_ROWS = 30
MAX_CELL_CHARS = 60
MAX_CATEGORY_VALUES = 6          # a column with more distinct values than this is not a "short list"
MAX_CHARS = 5500                 # stays under the size where an attachment is summarized instead of passed as it is (attachment_utils.SHORT_DOCUMENT_CHARS)
MIN_PREVIEW_ROWS = 5


TABLE_MARKER = 'Spreadsheet "'     # table_text always starts with this, so text read from an attachment can be told apart from a document's


def is_table_text(text: str) -> bool:
    return str(text or "").lstrip().startswith(TABLE_MARKER)


def extension_of(filename: str) -> str:
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


def is_table_file(filename: str) -> bool:
    return extension_of(filename) in TABLE_EXTENSIONS


def unreadable_note(filename: str) -> str:
    """What to tell the model (and so the person) about a format that cannot be read, or "" when the format is not one of those."""
    ext = extension_of(filename)
    if ext not in UNREADABLE_EXTENSIONS:
        return ""
    advice = {
        "xls": "save it as .xlsx or .csv",
        "doc": "save it as .docx or a PDF",
        "ppt": "save it as a PDF",
        "pptx": "save it as a PDF",
        "heic": "attach a screenshot of the photo instead, or set the iPhone camera to Most Compatible (Settings, Camera, Formats)",
        "heif": "attach a screenshot of the photo instead, or set the iPhone camera to Most Compatible (Settings, Camera, Formats)",
    }.get(ext, "export it as a PDF or a plain text file")
    return f'[The attached file "{filename}" is a .{ext} file, which this assistant cannot read yet. Tell the person plainly, and suggest they {advice} and attach that.]'


def _load(filename: str, raw: bytes) -> Dict[str, pd.DataFrame]:
    ext = extension_of(filename)
    if ext in ("xlsx", "xlsm"):
        return pd.read_excel(io.BytesIO(raw), sheet_name=None, engine="openpyxl", dtype=object)
    sep = "\t" if ext == "tsv" else None                      # None: let pandas work out a comma, semicolon or tab
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            frame = pd.read_csv(io.BytesIO(raw), sep=sep, engine="python", encoding=encoding, dtype=object)
            return {"": frame}
        except UnicodeError:
            continue
    raise ValueError("could not decode the file")


def _cell(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).replace("\n", " ").replace("|", "/").strip()
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def _numeric(series: pd.Series) -> pd.Series:
    """The column read as numbers, aligned with its rows (a cell that is not a number is NaN), or an empty series when most of its filled cells are
    not numbers. Money signs, commas and percent signs are allowed."""
    cleaned = series.astype(str).str.replace(r"[,$£€%\s]", "", regex=True).where(series.notna())
    numbers = pd.to_numeric(cleaned, errors="coerce")
    filled = series.notna().sum()
    if filled and numbers.notna().sum() >= 0.8 * filled:
        return numbers
    return pd.Series(dtype=float)


def _number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") if abs(value) < 1e15 else str(value)


def _sheet_text(label: str, frame: pd.DataFrame, preview_rows: int) -> str:
    frame = frame.dropna(how="all").dropna(axis=1, how="all")
    rows, cols = frame.shape
    columns = [str(c) for c in frame.columns][:MAX_COLUMNS]
    head = f'{label}: {rows:,} rows × {cols} columns.' if rows else f"{label}: no data."
    if not rows:
        return head
    lines = [head, "Columns: " + ", ".join(columns) + (" …" if cols > MAX_COLUMNS else "")]
    shown = frame.iloc[:preview_rows, :MAX_COLUMNS]
    lines.append(f"First {len(shown):,} of {rows:,} rows:" if rows > len(shown) else f"All {rows:,} rows:")
    lines.append(" | ".join(columns))
    for _, row in shown.iterrows():
        lines.append(" | ".join(_cell(v) for v in row))

    facts: List[str] = []
    number_columns = []                                   # (name, numbers) for every column that is mostly numbers
    short_lists = []                                      # (name, values) for every column with only a few different values
    for name in frame.columns[:MAX_COLUMNS]:
        series = frame[name]
        numbers = _numeric(series)
        if len(numbers):
            filled = numbers.dropna()
            number_columns.append((name, numbers))
            facts.append(
                f"{name}: total {_number(filled.sum())}, lowest {_number(filled.min())}, highest {_number(filled.max())}, "
                f"average {_number(filled.mean())} ({len(filled):,} filled)"
            )
            continue
        values = series.astype(str).str.strip().where(series.notna())
        counts = values.value_counts()
        if 1 < len(counts) <= MAX_CATEGORY_VALUES and len(counts) < 0.7 * counts.sum():       # a real list: its values repeat (dates and ids do not)
            short_lists.append((name, values))
            facts.append(f"{name} (how often each appears): " + ", ".join(f"{_cell(v)} {n:,}" for v, n in counts.items()))
    # The usual next question about a sheet: how much per category. Totals over every row, for the first couple of number columns.
    for number_name, numbers in number_columns[:2]:
        for list_name, values in short_lists[:2]:
            totals = numbers.groupby(values).sum().sort_values(ascending=False)
            facts.append(f"{number_name} added up for each {list_name}: " + ", ".join(f"{_cell(k)} {_number(v)}" for k, v in totals.items()))
    if facts:
        lines.append(f"Across all {rows:,} rows:")
        lines.extend(f"- {fact}" for fact in facts)
    if rows > len(shown):
        lines.append(
            f"[Only the {len(shown):,} rows above and the totals are visible. For anything else about the other rows, say what you cannot see "
            "rather than guessing a figure.]"
        )
    return "\n".join(lines)


def table_text(filename: str, raw: bytes) -> str:
    """The attached CSV or spreadsheet as text for the model, or "" when it cannot be read (the caller says so). Never raises."""
    try:
        sheets = _load(filename, raw)
    except Exception:
        logger.warning("Could not read the attached spreadsheet.", exc_info=True)
        return ""
    names = list(sheets)
    preview_rows = MAX_PREVIEW_ROWS
    while True:
        parts = [f'Spreadsheet "{filename}".']
        for name in names[:MAX_SHEETS]:
            label = f'Sheet "{name}"' if name != "" else "Table"
            parts.append(_sheet_text(label, sheets[name], preview_rows))
        if len(names) > MAX_SHEETS:
            parts.append(f"[{len(names) - MAX_SHEETS} more sheets are not shown.]")
        text = "\n\n".join(parts)
        if len(text) <= MAX_CHARS or preview_rows <= MIN_PREVIEW_ROWS:
            return text[:MAX_CHARS] if len(text) > MAX_CHARS else text
        preview_rows = max(MIN_PREVIEW_ROWS, preview_rows - 5)
