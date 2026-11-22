"""Google Docs changes Sonic can propose: create a document, add to the end of one, replace an exact phrase, add text under a heading. The pure part: checking what the model asked for, reading a document's structure, building the
exact requests Google will get, and describing the card the person approves. No network, no database, no clock (backend/tests/test_doc_changes.py). The part that talks to Google and keeps the proposal until it is approved is
backend/services/doc_changes_service.py; the Apply / Decline card is the shared one in backend/services/change_cards.py.

WHY THIS REPLACED "THE TARGET DOCUMENT". Sonic could append text to ONE document, the one pasted into Integrations, and the approval was a paragraph to type "approve" to. Now the model FINDS a document by name
(find_google_docs, which returns ids), proposes a change to one exact document id, and the card names the document as Google has it. The Integrations setting survives only as an optional default: "add this to my doc"
with no name uses it. Nothing here needs a new Google permission: the connection already carries Drive and Docs.

WHAT A CHANGE CAN BE (every field optional except what the action needs):
  create                 title (required), text (optional starting text): a new document in the person's My Drive
  append                 doc_id, text: added as new paragraphs at the end
  replace                doc_id, find (an exact phrase, case-sensitive), replace_with (may be empty, which removes the phrase): every place it appears
  insert_after_heading   doc_id, heading (the exact heading text), text: added as plain paragraphs right under that heading

The two index-based actions (replace, insert_after_heading) are made against the revision the card was drawn from, so Google itself refuses them if the document was edited since. Anything outside the limits is refused with a
sentence the person can act on (DocChangeError), never silently trimmed.
"""
import re
from typing import Any, Dict, List, Optional

from backend.utils.change_error import ChangeError

ACTIONS = ("create", "append", "replace", "insert_after_heading")
MAX_TEXT = 20000
MAX_TITLE = 200
MAX_FIND = 500
MAX_HEADING = 200
MAX_MATCHES = 50
PREVIEW_CHARS = 1200
DOC_MIME = "application/vnd.google-apps.document"
_DOC_ID = re.compile(r"^[A-Za-z0-9_\-]{10,200}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_STYLES = ("TITLE", "SUBTITLE", "HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6")


class DocChangeError(ChangeError):
    """Something the person (or the model) can fix: the message is written to be shown as it is."""


# ---- checking what was asked -----------------------------------------------------------------------------------------------------------------------

def _clean(value: Any, field: str, limit: int, single_line: bool = False) -> Optional[str]:
    if value is None:
        return None
    text = _CONTROL.sub("", str(value).replace("\r\n", "\n").replace("\r", "\n"))
    text = " ".join(text.split()) if single_line else text.strip("\n")
    if len(text) > limit:
        raise DocChangeError(f"The {field} is too long (the limit is {limit} characters). Split it into several changes.")
    return text


def clean_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """The model's arguments, checked and normalised into one plain dict. Raises DocChangeError for anything the person can fix."""
    args = args or {}
    action = str(args.get("action") or "").strip().lower()
    if action not in ACTIONS:
        raise DocChangeError("The action must be create, append, replace or insert_after_heading.")
    doc_id = str(args.get("doc_id") or "").strip() or None
    if doc_id and not _DOC_ID.match(doc_id):
        raise DocChangeError("That document id is not one Google would give me. Look the document up first (find_google_docs) and use its id.")
    if action == "create" and doc_id:
        raise DocChangeError("A new document has no document id.")
    change: Dict[str, Any] = {"action": action, "doc_id": doc_id, "title": None, "text": None, "find": None, "replace_with": None, "heading": None}
    if action == "create":
        change["title"] = _clean(args.get("title"), "title", MAX_TITLE, True)
        if not change["title"]:
            raise DocChangeError("A new document needs a title.")
        change["text"] = _clean(args.get("text"), "text", MAX_TEXT) or ""
        return change
    if action in ("append", "insert_after_heading"):
        change["text"] = _clean(args.get("text"), "text", MAX_TEXT)
        if not change["text"]:
            raise DocChangeError("Give the text to add.")
    if action == "insert_after_heading":
        change["heading"] = _clean(args.get("heading"), "heading", MAX_HEADING, True)
        if not change["heading"]:
            raise DocChangeError("Give the exact heading to add the text under.")
    if action == "replace":
        change["find"] = _clean(args.get("find"), "phrase to find", MAX_FIND)
        if not change["find"]:
            raise DocChangeError("Give the exact phrase to replace.")
        if args.get("replace_with") is None:
            raise DocChangeError("Give what to replace it with (an empty string removes the phrase).")
        change["replace_with"] = _clean(args.get("replace_with"), "replacement", MAX_TEXT) or ""
        if change["replace_with"] == change["find"]:
            raise DocChangeError("The replacement is the same as the phrase, so nothing would change.")
    return change


# ---- reading a document as Google returns it ------------------------------------------------------------------------------------------------------

def _paragraph_text(paragraph: Dict[str, Any]) -> str:
    return "".join((element.get("textRun") or {}).get("content", "") for element in paragraph.get("elements") or [])


def _walk(content: List[Dict[str, Any]], out: List[str]) -> None:
    for item in content or []:
        if "paragraph" in item:
            out.append(_paragraph_text(item["paragraph"]))
        elif "table" in item:
            for row in item["table"].get("tableRows") or []:
                for cell in row.get("tableCells") or []:
                    _walk(cell.get("content") or [], out)
        elif "tableOfContents" in item:
            _walk(item["tableOfContents"].get("content") or [], out)


def doc_text(doc: Dict[str, Any]) -> str:
    """All of a document's text in reading order, table cells included (a replace reaches them too). Pure."""
    parts: List[str] = []
    _walk((doc.get("body") or {}).get("content") or [], parts)
    return "".join(parts)


def count_matches(doc: Dict[str, Any], find: str) -> int:
    """How many times `find` appears (case-sensitive, not overlapping), as replaceAllText would count them. Pure."""
    return doc_text(doc).count(find)


def _body_end(doc: Dict[str, Any]) -> int:
    content = (doc.get("body") or {}).get("content") or []
    return int(content[-1].get("endIndex", 0)) if content else 0


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def headings(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The top-level heading paragraphs, in order: {start, end, text, style}. `end` is the index just after the heading's own line break."""
    found: List[Dict[str, Any]] = []
    for item in (doc.get("body") or {}).get("content") or []:
        paragraph = item.get("paragraph")
        if not paragraph:
            continue
        style = (paragraph.get("paragraphStyle") or {}).get("namedStyleType", "")
        text = _paragraph_text(paragraph).strip("\n").strip()
        if style in _HEADING_STYLES and text:
            found.append({"start": int(item.get("startIndex", 0)), "end": int(item.get("endIndex", 0)), "text": text, "style": style})
    return found


def find_heading(doc: Dict[str, Any], wanted: str) -> Dict[str, Any]:
    """The one heading whose text equals `wanted` (ignoring case and spacing). Raises DocChangeError naming the headings that do exist when there is none, and asking for a closer name when several match."""
    found = headings(doc)
    matches = [h for h in found if _norm(h["text"]) == _norm(wanted)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise DocChangeError(f"There are {len(matches)} headings called \"{wanted}\" in that document, so I can't tell which one you mean. Tell me which, or add to the end instead.")
    if not found:
        raise DocChangeError("That document has no headings to add text under. I can add to the end instead.")
    names = ", ".join(f"\"{h['text']}\"" for h in found[:8]) + (" and more" if len(found) > 8 else "")
    raise DocChangeError(f"I can't find a heading called \"{wanted}\" in that document. Its headings are: {names}.")


# ---- the requests Google will get ------------------------------------------------------------------------------------------------------------------

def build_request(change: Dict[str, Any], doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{"requests": [...], "required_revision": the revision the change is guarded by, or None}. `doc` is the document as Google returned it (None for create and append, which do not need it)."""
    action = change["action"]
    if action == "create":
        # The document itself is made first (documents.create), then this puts the starting text at its very start.
        requests = [{"insertText": {"location": {"index": 1}, "text": change["text"]}}] if change["text"] else []
        return {"requests": requests, "required_revision": None}
    if action == "append":
        return {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "\n" + change["text"]}}], "required_revision": None}
    revision = (doc or {}).get("revisionId")
    if action == "replace":
        return {"requests": [{"replaceAllText": {"containsText": {"text": change["find"], "matchCase": True}, "replaceText": change["replace_with"]}}], "required_revision": revision}
    heading = find_heading(doc or {}, change["heading"])
    text = change["text"]
    if heading["end"] >= _body_end(doc or {}):
        # The heading is the last line of the document: there is no next paragraph to put the text in front of, so the line break goes first and the new paragraph is made plain.
        at = heading["end"] - 1
        requests = [{"insertText": {"location": {"index": at}, "text": "\n" + text}},
                    {"updateParagraphStyle": {"range": {"startIndex": at + 1, "endIndex": at + 1 + len(text) + 1}, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "namedStyleType"}}]
    else:
        at = heading["end"]
        requests = [{"insertText": {"location": {"index": at}, "text": text + "\n"}},
                    {"updateParagraphStyle": {"range": {"startIndex": at, "endIndex": at + len(text) + 1}, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "namedStyleType"}}]
    return {"requests": requests, "required_revision": revision}


# ---- the card --------------------------------------------------------------------------------------------------------------------------------------

_WORDS = {
    "create": ("Create a Google Doc", "Create doc"),
    "append": ("Add to a document", "Add text"),
    "replace": ("Replace text in a document", "Replace text"),
    "insert_after_heading": ("Add under a heading", "Add text"),
}


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    """The text as the card shows it: line breaks kept, cut with a note when long. Pure."""
    text = (text or "").strip("\n")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + f"… (and {len(text) - limit + 1} more characters)"


def _places(n: int) -> str:
    return "1 place" if n == 1 else f"{n} places"


def card_data(change: Dict[str, Any], name: str, shared: Optional[bool], matches: int = 0) -> Dict[str, Any]:
    """What the page draws: {action, heading, apply_label, title, rows: [{label, value}], notice, destructive}. `name` is the document's name as Google has it (the new title for a create); `shared` whether other people can
    open it (None when unknown); `matches` how many places a replace reaches. Plain data, so the page needs no parsing of text."""
    action = change["action"]
    rows: List[Dict[str, str]] = []

    def add(label: str, value: str) -> None:
        if value:
            rows.append({"label": label, "value": value})

    if action == "create":
        add("Text", preview(change["text"]) or "(an empty document)")
        add("Where", "your Google Drive (My Drive)")
        notice = "A new document that only you can open."
    elif action == "append":
        add("Adds", preview(change["text"]))
        add("Where", "at the end of the document")
        notice = ""
    elif action == "insert_after_heading":
        add("Under the heading", change["heading"])
        add("Adds", preview(change["text"]))
        notice = ""
    else:
        add("Find", preview(change["find"], 300))
        add("Replace with", preview(change["replace_with"], 300) or "(nothing: the phrase is removed)")
        add("Found", _places(matches) + ", all changed")
        notice = "You can undo this from the document's version history."
    if action != "create":
        share = "This document is shared, so the people who can open it will see the change." if shared else ("Only you can open this document." if shared is False else "")
        notice = (share + " " + notice).strip()
    heading, apply_label = _WORDS[action]
    return {"action": action, "heading": heading, "apply_label": apply_label, "title": name, "rows": rows, "notice": notice, "destructive": action == "replace"}


# ---- finding documents -----------------------------------------------------------------------------------------------------------------------------

def drive_query(words: Optional[str]) -> str:
    """The Drive query for the person's own Google Docs, optionally whose name contains `words`. Quotes and backslashes in the words are escaped, so they cannot change what the query means. Pure."""
    query = f"mimeType='{DOC_MIME}' and trashed=false"
    cleaned = " ".join(str(words or "").split())[:100]
    if cleaned:
        escaped = cleaned.replace("\\", "\\\\").replace("'", "\\'")
        query += f" and name contains '{escaped}'"
    return query


def doc_line(item: Dict[str, Any]) -> str:
    """One line per document for the tool's result: its name and the id a change needs, then when it was last edited. Pure."""
    edited = str(item.get("modifiedTime") or "")[:10]
    return f"- {item.get('name') or 'Untitled document'} [id: {item.get('id')}]" + (f" (edited {edited})" if edited else "")
