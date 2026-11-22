"""The pure part of Sonic's Google Docs changes (backend/utils/doc_changes.py): checking what the model asked for, reading a document as Google returns it, building the exact requests, and describing the card.
No network, no clock. The document below has the real Docs API shape, with start and end indexes computed from its text, so an insert is tested at the index Google would really use."""
import pytest

from backend.utils import doc_changes as dc
from backend.utils.doc_changes import DocChangeError


def make_doc(parts, revision="rev1"):
    """A document body from [(text, style)]: each part is one paragraph ending in a line break, indexes running on from 1 as Google's do. A part may also be ("TABLE", [[cell text, ...]])."""
    content, index = [{"startIndex": 0, "endIndex": 1, "sectionBreak": {}}], 1
    for text, style in parts:
        if text == "TABLE":
            rows = []
            for row in style:
                cells = []
                for cell in row:
                    cells.append({"content": [{"paragraph": {"elements": [{"textRun": {"content": cell + "\n"}}], "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}}]})
                rows.append({"tableCells": cells})
            content.append({"startIndex": index, "endIndex": index + 50, "table": {"tableRows": rows}})
            index += 50
            continue
        end = index + len(text) + 1
        content.append({"startIndex": index, "endIndex": end, "paragraph": {"elements": [{"startIndex": index, "endIndex": end, "textRun": {"content": text + "\n"}}], "paragraphStyle": {"namedStyleType": style}}})
        index = end
    return {"documentId": "doc1234567890", "title": "Team notes", "revisionId": revision, "body": {"content": content}}


NOTES = [("Team notes", "TITLE"), ("Intro line about Q3.", "NORMAL_TEXT"), ("Action Items", "HEADING_2"), ("Call Sam about Q3.", "NORMAL_TEXT"), ("Decisions", "HEADING_2"), ("Ship it.", "NORMAL_TEXT")]
LAST_IS_HEADING = [("Plan", "TITLE"), ("Body text.", "NORMAL_TEXT"), ("Open questions", "HEADING_1")]
DOC_ID = "1AbCdEfGhIjKlMnOp"


def change(**args):
    return dc.clean_args(args)


# ---- checking what was asked -----------------------------------------------------------------------------------------------------------------------

def test_each_action_is_read_with_only_the_fields_it_uses():
    c = change(action="create", title="  Trip   plan ", text="Day 1\r\nDay 2\n")
    assert (c["action"], c["title"], c["text"], c["doc_id"]) == ("create", "Trip plan", "Day 1\nDay 2", None)
    assert change(action="create", title="Empty")["text"] == ""
    a = change(action="append", doc_id=DOC_ID, text="New line")
    assert (a["action"], a["doc_id"], a["text"]) == ("append", DOC_ID, "New line")
    r = change(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")
    assert (r["find"], r["replace_with"]) == ("Q3", "Q4")
    assert change(action="replace", doc_id=DOC_ID, find="Q3", replace_with="")["replace_with"] == ""                   # an empty replacement removes the phrase
    h = change(action="insert_after_heading", doc_id=DOC_ID, heading="  Action   Items ", text="Item")
    assert (h["heading"], h["text"]) == ("Action Items", "Item")
    assert change(action="append", text="Goes to the default document")["doc_id"] is None


@pytest.mark.parametrize("args,fragment", [
    ({"action": "delete"}, "create, append, replace or insert_after_heading"),
    ({"action": ""}, "create, append, replace or insert_after_heading"),
    ({"action": "create"}, "needs a title"),
    ({"action": "create", "title": "   "}, "needs a title"),
    ({"action": "create", "title": "x", "doc_id": DOC_ID}, "no document id"),
    ({"action": "create", "title": "x" * 201}, "title is too long"),
    ({"action": "create", "title": "x", "text": "y" * 20001}, "text is too long"),
    ({"action": "append", "doc_id": DOC_ID}, "text to add"),
    ({"action": "append", "doc_id": DOC_ID, "text": ""}, "text to add"),
    ({"action": "append", "doc_id": DOC_ID, "text": "\n\n"}, "text to add"),
    ({"action": "append", "doc_id": "short", "text": "x"}, "not one Google would give"),
    ({"action": "append", "doc_id": "has spaces in it", "text": "x"}, "not one Google would give"),
    ({"action": "replace", "doc_id": DOC_ID, "replace_with": "x"}, "exact phrase"),
    ({"action": "replace", "doc_id": DOC_ID, "find": "x"}, "what to replace it with"),
    ({"action": "replace", "doc_id": DOC_ID, "find": "same", "replace_with": "same"}, "nothing would change"),
    ({"action": "replace", "doc_id": DOC_ID, "find": "x" * 501, "replace_with": "y"}, "too long"),
    ({"action": "insert_after_heading", "doc_id": DOC_ID, "text": "x"}, "exact heading"),
    ({"action": "insert_after_heading", "doc_id": DOC_ID, "heading": "H"}, "text to add"),
])
def test_what_cannot_be_done_is_refused_in_words_the_person_can_act_on(args, fragment):
    with pytest.raises(DocChangeError) as refused:
        change(**args)
    assert fragment in str(refused.value)


def test_control_characters_are_stripped_and_line_breaks_are_kept_in_text_but_not_in_names():
    c = change(action="create", title="a\x00b\nc", text="line one\x07\nline two")
    assert c["title"] == "ab c" and c["text"] == "line one\nline two"


# ---- reading a document ----------------------------------------------------------------------------------------------------------------------------

def test_the_text_of_a_document_includes_its_tables_and_a_phrase_is_counted_case_sensitively_without_overlap():
    doc = make_doc([("Budget", "TITLE"), ("TABLE", [["Q3 target", "10"], ["Q3 actual", "9"]]), ("See Q3 and q3.", "NORMAL_TEXT")])
    assert "Q3 target" in dc.doc_text(doc) and "Q3 actual" in dc.doc_text(doc)
    assert dc.count_matches(doc, "Q3") == 3 and dc.count_matches(doc, "q3") == 1 and dc.count_matches(doc, "Q4") == 0
    assert dc.count_matches(make_doc([("aaaa", "NORMAL_TEXT")]), "aa") == 2


def test_headings_are_the_title_and_heading_styles_in_order_never_ordinary_text():
    found = dc.headings(make_doc(NOTES))
    assert [(h["text"], h["style"]) for h in found] == [("Team notes", "TITLE"), ("Action Items", "HEADING_2"), ("Decisions", "HEADING_2")]
    assert dc.headings(make_doc([("Just text", "NORMAL_TEXT")])) == []
    assert dc.headings({}) == []


def test_a_heading_is_found_ignoring_case_and_spacing_and_its_end_index_is_after_its_own_line_break():
    doc = make_doc(NOTES)
    h = dc.find_heading(doc, "  action   ITEMS ")
    assert h["text"] == "Action Items" and h["end"] == h["start"] + len("Action Items") + 1


def test_a_heading_that_is_not_there_names_the_ones_that_are_and_duplicates_and_headingless_documents_are_refused_too():
    with pytest.raises(DocChangeError) as missing:
        dc.find_heading(make_doc(NOTES), "Next steps")
    assert "\"Team notes\", \"Action Items\", \"Decisions\"" in str(missing.value) and "Next steps" in str(missing.value)
    many = make_doc([(f"Section {n}", "HEADING_1") for n in range(10)])
    with pytest.raises(DocChangeError) as long_list:
        dc.find_heading(many, "Nope")
    assert "and more" in str(long_list.value) and "Section 7" in str(long_list.value) and "Section 8" not in str(long_list.value)
    with pytest.raises(DocChangeError) as twice:
        dc.find_heading(make_doc([("Notes", "HEADING_1"), ("a", "NORMAL_TEXT"), ("notes", "HEADING_2")]), "NOTES")
    assert "2 headings" in str(twice.value)
    with pytest.raises(DocChangeError) as none:
        dc.find_heading(make_doc([("Just text", "NORMAL_TEXT")]), "Anything")
    assert "no headings" in str(none.value)


# ---- the requests Google will get ------------------------------------------------------------------------------------------------------------------

def test_a_new_document_puts_its_starting_text_at_the_very_start_or_sends_nothing_when_there_is_none():
    assert dc.build_request(change(action="create", title="T", text="Hello"), None) == {"requests": [{"insertText": {"location": {"index": 1}, "text": "Hello"}}], "required_revision": None}
    assert dc.build_request(change(action="create", title="T"), None) == {"requests": [], "required_revision": None}


def test_an_append_goes_at_the_end_of_the_body_as_a_new_paragraph_and_needs_no_revision_guard():
    built = dc.build_request(change(action="append", doc_id=DOC_ID, text="New line"), None)
    assert built == {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "\nNew line"}}], "required_revision": None}


def test_a_replace_is_case_sensitive_reaches_every_place_and_is_guarded_by_the_revision_it_was_drawn_from():
    built = dc.build_request(change(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4"), make_doc(NOTES, revision="revA"))
    assert built == {"requests": [{"replaceAllText": {"containsText": {"text": "Q3", "matchCase": True}, "replaceText": "Q4"}}], "required_revision": "revA"}


def test_text_under_a_heading_goes_at_the_start_of_the_next_paragraph_and_is_made_plain_so_it_never_inherits_a_heading_look():
    doc = make_doc(NOTES, revision="revB")
    h = dc.find_heading(doc, "Action Items")
    built = dc.build_request(change(action="insert_after_heading", doc_id=DOC_ID, heading="Action Items", text="Email Kim"), doc)
    at = h["end"]
    assert built["required_revision"] == "revB"
    assert built["requests"] == [
        {"insertText": {"location": {"index": at}, "text": "Email Kim\n"}},
        {"updateParagraphStyle": {"range": {"startIndex": at, "endIndex": at + len("Email Kim") + 1}, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "namedStyleType"}},
    ]


def test_text_under_a_heading_that_is_the_last_line_of_the_document_starts_a_new_paragraph_instead():
    doc = make_doc(LAST_IS_HEADING)
    h = dc.find_heading(doc, "Open questions")
    built = dc.build_request(change(action="insert_after_heading", doc_id=DOC_ID, heading="Open questions", text="Who owns it?"), doc)
    at = h["end"] - 1                                                          # before the heading's own line break, which the new paragraph then follows
    assert built["requests"][0] == {"insertText": {"location": {"index": at}, "text": "\nWho owns it?"}}
    assert built["requests"][1]["updateParagraphStyle"]["range"] == {"startIndex": at + 1, "endIndex": at + 1 + len("Who owns it?") + 1}


def test_several_lines_under_a_heading_are_all_covered_by_the_plain_style():
    doc = make_doc(NOTES)
    text = "one\ntwo\nthree"
    built = dc.build_request(change(action="insert_after_heading", doc_id=DOC_ID, heading="Decisions", text=text), doc)
    insert, style = built["requests"]
    assert insert["insertText"]["text"] == text + "\n" and style["updateParagraphStyle"]["range"]["endIndex"] - style["updateParagraphStyle"]["range"]["startIndex"] == len(text) + 1


# ---- the card --------------------------------------------------------------------------------------------------------------------------------------

def rows(card):
    return {r["label"]: r["value"] for r in card["rows"]}


def test_the_card_for_a_new_document_says_where_it_goes_and_that_only_the_person_can_open_it():
    card = dc.card_data(change(action="create", title="Trip plan", text="Day 1\nDay 2"), "Trip plan", None)
    assert (card["action"], card["heading"], card["apply_label"], card["title"], card["destructive"]) == ("create", "Create a Google Doc", "Create doc", "Trip plan", False)
    assert rows(card) == {"Text": "Day 1\nDay 2", "Where": "your Google Drive (My Drive)"} and card["notice"] == "A new document that only you can open."
    assert rows(dc.card_data(change(action="create", title="T"), "T", None))["Text"] == "(an empty document)"


def test_the_card_for_an_append_names_the_document_and_says_whether_others_can_see_the_change():
    c = change(action="append", doc_id=DOC_ID, text="New line")
    shared = dc.card_data(c, "Team notes", True)
    assert (shared["heading"], shared["apply_label"], shared["title"], shared["destructive"]) == ("Add to a document", "Add text", "Team notes", False)
    assert rows(shared) == {"Adds": "New line", "Where": "at the end of the document"} and shared["notice"] == "This document is shared, so the people who can open it will see the change."
    assert dc.card_data(c, "Team notes", False)["notice"] == "Only you can open this document." and dc.card_data(c, "Team notes", None)["notice"] == ""


def test_the_card_for_a_replace_shows_what_is_found_and_changed_is_marked_careful_and_points_to_version_history():
    c = change(action="replace", doc_id=DOC_ID, find="Q3", replace_with="Q4")
    card = dc.card_data(c, "Team notes", False, matches=3)
    assert card["destructive"] is True and (card["heading"], card["apply_label"]) == ("Replace text in a document", "Replace text")
    assert rows(card) == {"Find": "Q3", "Replace with": "Q4", "Found": "3 places, all changed"}
    assert card["notice"] == "Only you can open this document. You can undo this from the document's version history."
    assert rows(dc.card_data(c, "T", None, matches=1))["Found"] == "1 place, all changed"
    removing = dc.card_data(change(action="replace", doc_id=DOC_ID, find="Q3", replace_with=""), "T", None, matches=2)
    assert rows(removing)["Replace with"] == "(nothing: the phrase is removed)"


def test_the_card_for_an_insert_under_a_heading_names_the_heading():
    card = dc.card_data(change(action="insert_after_heading", doc_id=DOC_ID, heading="Action Items", text="Email Kim"), "Team notes", True)
    assert (card["heading"], card["apply_label"]) == ("Add under a heading", "Add text") and rows(card) == {"Under the heading": "Action Items", "Adds": "Email Kim"}


def test_long_text_is_cut_on_the_card_with_a_note_of_how_much_more_there_is():
    long = "word " * 400
    shown = rows(dc.card_data(change(action="append", doc_id=DOC_ID, text=long), "T", None))["Adds"]
    assert len(shown) < 1300 and shown.endswith("more characters)") and "(and " in shown
    assert dc.preview("short") == "short" and dc.preview("\n\nhi\n") == "hi"


# ---- finding documents -----------------------------------------------------------------------------------------------------------------------------

def test_the_drive_query_is_for_the_persons_google_docs_only_and_the_words_cannot_change_what_it_means():
    assert dc.drive_query(None) == "mimeType='application/vnd.google-apps.document' and trashed=false"
    assert dc.drive_query("  meeting   notes ") == "mimeType='application/vnd.google-apps.document' and trashed=false and name contains 'meeting notes'"
    hostile = dc.drive_query("x' or name contains 'y")
    assert hostile.endswith("name contains 'x\\' or name contains \\'y'")                  # the quotes are escaped, so it stays one phrase
    assert dc.drive_query("a\\b").endswith("name contains 'a\\\\b'")
    assert len(dc.drive_query("z" * 500)) < len(dc.drive_query(None)) + 130


def test_each_document_is_listed_with_the_id_a_change_needs():
    assert dc.doc_line({"id": "abc123", "name": "Team notes", "modifiedTime": "2026-11-20T10:00:00.000Z"}) == "- Team notes [id: abc123] (edited 2026-11-20)"
    assert dc.doc_line({"id": "x"}) == "- Untitled document [id: x]"
