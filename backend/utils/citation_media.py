"""How a cited source file is served when someone opens its link (the file route in app.py): the type it is sent as, and whether the browser should show it or save it.

A citation link opens the stored file itself. Files used to be PDFs, so everything not JSON was sent as application/pdf; text, Markdown and Word files sent that way fail to open or arrive as
garbage. Now each kind is sent as what it is:
- JSON: application/json, shown in the browser.
- Plain text, logs, Markdown, and CSV/TSV tables: text/plain, shown in the browser (Markdown as plain text, because browsers save text/markdown instead of showing it).
- Word and Excel workbooks: their own types, saved (a browser cannot show them).
- Anything else keeps the type it has always had, application/pdf, shown in the browser.
"""
from typing import Tuple

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def media_for(filename: str) -> Tuple[str, str]:
    """(media type, "inline" or "attachment") for a stored file's name."""
    name = (filename or "").lower()
    if name.endswith((".json", ".jsonl", ".ndjson")):
        return "application/json", "inline"
    if name.endswith((".txt", ".log", ".md", ".markdown", ".csv", ".tsv")):
        return "text/plain; charset=utf-8", "inline"
    if name.endswith((".xlsx", ".xlsm")):
        return (XLSX_TYPE if name.endswith(".xlsx") else "application/vnd.ms-excel.sheet.macroEnabled.12"), "attachment"
    if name.endswith(".docx"):
        return DOCX_TYPE, "attachment"
    return "application/pdf", "inline"
