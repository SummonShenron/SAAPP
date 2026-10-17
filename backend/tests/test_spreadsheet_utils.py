"""A spreadsheet or CSV attached to a chat (backend/utils/spreadsheet_utils.py): what the model is shown, and what it is told it cannot see."""
import base64
import io

import pytest
from openpyxl import Workbook

from backend.models.attachment import Attachment
from backend.utils import attachment_utils, spreadsheet_utils as su


def csv_bytes(rows, header="Date,Category,Amount"):
    return (header + "\n" + "\n".join(rows)).encode("utf-8")


def xlsx_bytes(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


SPENDING = [f"2026-01-{day:02d},{cat},{amount}" for day, cat, amount in
            [(1, "Groceries", "54.20"), (2, "Dining", "$31.50"), (3, "Groceries", "12.80"), (4, "Fuel", "40.00"), (5, "Dining", "22.00")]]


def test_a_small_csv_is_shown_whole_with_totals_and_how_often_each_category_appears():
    text = su.table_text("spending.csv", csv_bytes(SPENDING))
    assert 'Spreadsheet "spending.csv"' in text and "5 rows × 3 columns" in text and "All 5 rows:" in text
    assert "Date | Category | Amount" in text and "2026-01-02 | Dining | $31.50" in text
    assert "Amount: total 160.5, lowest 12.8, highest 54.2, average 32.1 (5 filled)" in text      # $ sign and commas do not stop it being a number column
    assert "Category (how often each appears): Groceries 2, Dining 2, Fuel 1" in text
    assert "Amount added up for each Category: Groceries 67, Dining 53.5, Fuel 40" in text         # the usual next question: how much per category
    assert "Date (how often" not in text and "for each Date" not in text                           # a date column does not repeat, so it is not a category


def test_a_big_sheet_shows_the_first_rows_totals_over_every_row_and_says_what_it_cannot_see():
    rows = [f"2026-01-01,Cat{n % 3},{n}" for n in range(1, 3001)]
    text = su.table_text("big.csv", csv_bytes(rows))
    assert "3,000 rows" in text and "First 30 of 3,000 rows:" in text
    assert "Amount: total 4,501,500, lowest 1, highest 3,000" in text                              # over ALL 3,000 rows, not just the 30 shown
    assert "Only the 30 rows above" in text and "rather than guessing" in text
    assert "Cat1 1,000" in text
    assert len(text) <= su.MAX_CHARS


def test_an_excel_workbook_shows_each_sheet_and_ignores_empty_ones():
    data = xlsx_bytes({"January": [["Item", "Cost"], ["Rent", 1200], ["Phone", 45.5]], "Blank": [[None, None]]})
    text = su.table_text("budget.xlsx", data)
    assert 'Sheet "January": 2 rows × 2 columns' in text and "Rent | 1200" in text
    assert "Cost: total 1,245.5" in text
    assert 'Sheet "Blank": no data.' in text


def test_a_wide_table_is_cut_to_the_column_limit_and_says_so():
    header = ",".join(f"c{n}" for n in range(40))
    text = su.table_text("wide.csv", csv_bytes([",".join(str(n) for n in range(40))], header=header))
    columns_line = next(line for line in text.splitlines() if line.startswith("Columns:"))
    assert "40 columns" in text and columns_line.endswith("c24 …") and "c25" not in columns_line


def test_a_file_that_cannot_be_read_never_raises_and_never_invents_data():
    assert su.table_text("empty.csv", b"") == ""
    assert su.table_text("broken.xlsx", b"this is not a zip file") == ""
    junk = su.table_text("junk.csv", b"\x00\x01\x02 not a table at all \xff\xfe")
    assert junk == "" or "no data" in junk


def test_text_with_accents_and_a_latin1_file_still_read():
    assert "Café" in su.table_text("a.csv", "Name,Score\nCafé,5\n".encode("utf-8"))
    assert "Café" in su.table_text("b.csv", "Name,Score\nCafé,5\n".encode("latin-1"))


@pytest.mark.parametrize("filename,expect", [("old.xls", "save it as .xlsx or .csv"), ("letter.doc", "save it as .docx or a PDF"),
                                             ("slides.pptx", "save it as a PDF"), ("IMG_1.HEIC", "Most Compatible")])
def test_formats_that_cannot_be_read_get_a_note_that_tells_the_person_what_to_do(filename, expect):
    note = su.unreadable_note(filename)
    assert expect in note and "cannot read" in note and "Tell the person plainly" in note


def test_readable_formats_get_no_unreadable_note_and_tables_are_recognised():
    for name in ("a.pdf", "a.docx", "a.csv", "a.xlsx", "a.txt", "photo.jpg", "noextension"):
        assert su.unreadable_note(name) == ""
    assert all(su.is_table_file(n) for n in ("a.csv", "A.XLSX", "a.tsv", "a.xlsm")) and not su.is_table_file("a.pdf")


def test_the_picture_of_a_sheet_stays_under_the_size_where_attachments_get_summarized():
    assert su.MAX_CHARS < attachment_utils.SHORT_DOCUMENT_CHARS


def test_an_attached_spreadsheet_is_read_by_the_table_reader_and_not_summarized(monkeypatch):
    from unittest.mock import Mock

    summarize = Mock()
    monkeypatch.setattr(attachment_utils.llm, "invoke", summarize)
    attachment = Attachment(filename="spending.csv", content=base64.b64encode(csv_bytes(SPENDING)).decode())

    text = attachment_utils.extract_text_from_attachment(attachment)
    assert "Category (how often each appears)" in text
    assert attachment_utils.process_user_attachment(attachment, raw_text=text) == text
    summarize.assert_not_called()


def test_an_unreadable_attachment_reaches_the_model_as_a_note_not_as_nothing():
    attachment = Attachment(filename="old.xls", content=base64.b64encode(b"\xd0\xcf\x11\xe0").decode())
    text = attachment_utils.extract_text_from_attachment(attachment)
    assert "cannot read" in text
    assert attachment_utils.process_user_attachment(attachment, raw_text=text) == text.strip()
