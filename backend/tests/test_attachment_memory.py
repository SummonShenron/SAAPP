"""A photo or file attached earlier in a conversation is still known on later turns (2026-11-01: the same screenshot had to be explained four times, and each time Sonic said it did not remember what it
showed). The text read out of an attachment is kept in session_documents; it used to reach the reply only on the turn it came with. Now a later turn gets it, newest first, within a size cap."""
import pathlib

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils
from backend.utils import attachment_utils as au

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Cursor(list):
    def sort(self, key, direction):
        return _Cursor(sorted(self, key=lambda row: row["_order"], reverse=direction == -1))

    def limit(self, count):
        return _Cursor(self[:count])


class _Docs:
    def __init__(self, rows):
        self.rows, self.filter = rows, None

    def find(self, filt, projection=None):
        self.filter = filt
        return _Cursor(dict(row) for row in self.rows)


def _db(monkeypatch, rows):
    docs = _Docs(rows)
    monkeypatch.setattr(au, "get_db", lambda: {"session_documents": docs})
    return docs


def _row(name, text, order):
    return {"filename": name, "text": text, "_order": order}


# ---- knowing there is something to look up -----------------------------------------------------------------------------------------------------

def _human(text, **extra):
    return HumanMessage(content=text, additional_kwargs=extra)


def test_an_earlier_message_with_an_attachment_is_found_and_the_newest_message_does_not_count():
    history = [_human("look at this", attachment_names=["screenshot.png"]), AIMessage(content="ok"), _human("what did she say in it?")]
    assert au.attachments_earlier_in(history) is True
    assert au.attachments_earlier_in([_human("hi"), AIMessage(content="hello"), _human("here", attachment_names=["a.png"])]) is False      # this turn's own file is not "earlier"
    assert au.attachments_earlier_in([_human("hi"), AIMessage(content="hello"), _human("and?")]) is False
    assert au.attachments_earlier_in([]) is False and au.attachments_earlier_in(None) is False


def test_a_message_that_only_kept_its_photo_reference_counts_too_and_an_assistant_message_never_does():
    assert au.attachments_earlier_in([_human("photo", attachments=[{"filename": "a.png", "gridfs_id": "1"}]), _human("next")]) is True
    assert au.attachments_earlier_in([AIMessage(content="x", additional_kwargs={"attachment_names": ["a.png"]}), _human("next")]) is False


# ---- what is looked up -----------------------------------------------------------------------------------------------------------------------

def test_earlier_attachments_come_back_newest_first_without_this_turns_files_and_empty_ones(monkeypatch):
    docs = _db(monkeypatch, [_row("old.png", "old text", 1), _row("new.png", "new text", 3), _row("blank.png", "   ", 4), _row("now.png", "this turn", 5), _row("mid.pdf", "mid text", 2)])
    got = au.earlier_attachments("jack", "s1", exclude_names=["now.png"])
    assert got == [("new.png", "new text"), ("mid.pdf", "mid text"), ("old.png", "old text")]
    assert docs.filter == {"username": "jack", "session_id": "s1"}                               # only this person's, only this conversation's


def test_only_the_newest_few_are_kept(monkeypatch):
    _db(monkeypatch, [_row(f"f{n}.png", f"text {n}", n) for n in range(10)])
    assert [name for name, _ in au.earlier_attachments("jack", "s1")] == ["f9.png", "f8.png", "f7.png"]


def test_without_a_database_or_a_conversation_or_when_it_fails_there_is_nothing_and_nothing_raises(monkeypatch):
    monkeypatch.setattr(au, "get_db", lambda: None)
    assert au.earlier_attachments("jack", "s1") == [] and au.earlier_attachments_block("jack", "s1") == ""
    docs = _db(monkeypatch, [])
    assert au.earlier_attachments("", "s1") == [] and au.earlier_attachments("jack", "") == []

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(au, "get_db", lambda: Broken())
    assert au.earlier_attachments("jack", "s1") == [] and au.earlier_attachments_block("jack", "s1") == ""


# ---- the block --------------------------------------------------------------------------------------------------------------------------------

def test_the_block_names_each_file_and_tells_the_model_to_use_it_and_never_deny_it():
    block = au.build_earlier_attachments_block([("screenshot.png", "Yashu: I need some space.\nJack: ok"), ("bill.pdf", "Due March 1: $42.10")])
    assert block.startswith("\n\nATTACHMENTS EARLIER IN THIS CONVERSATION")
    assert "[screenshot.png]\nYashu: I need some space.\nJack: ok" in block and "[bill.pdf]\nDue March 1: $42.10" in block
    assert block.index("screenshot.png") < block.index("bill.pdf")                              # the order given (newest first)
    assert "Never say you can't see it or don't remember it" in block and "ask them to send it again" in block


def test_a_long_attachment_is_cut_with_a_note_and_the_whole_block_has_a_ceiling(monkeypatch):
    monkeypatch.setattr(au, "ATTACHMENT_MEMORY_DOC_CHARS", 100)
    monkeypatch.setattr(au, "ATTACHMENT_MEMORY_TOTAL_CHARS", 180)
    block = au.build_earlier_attachments_block([("a.png", "x" * 500), ("b.png", "y" * 500), ("c.png", "z" * 500)])
    assert "[a.png]\n" + "x" * 100 + " [cut: the rest of this one is not shown]" in block
    assert "zzz" not in block                                                                    # the ceiling stopped it before the third
    assert block.count("[cut:") == 2


def test_nothing_to_add_is_an_empty_string_not_a_bare_header():
    assert au.build_earlier_attachments_block([]) == ""


# ---- surviving a restart ------------------------------------------------------------------------------------------------------------------

def test_the_names_are_saved_with_the_message_and_come_back_after_a_reload():
    saved = app_utils._serialize_messages([_human("look", attachment_names=["screenshot.png"]), AIMessage(content="ok")])
    assert saved[0]["attachment_names"] == ["screenshot.png"] and "attachment_names" not in saved[1]
    restored = app_utils._deserialize_messages(saved)
    assert restored[0].additional_kwargs["attachment_names"] == ["screenshot.png"]
    assert au.attachments_earlier_in([restored[0], restored[1], _human("what did it say?")]) is True


def test_a_message_saved_before_names_were_kept_gets_them_from_its_photo_reference():
    restored = app_utils._deserialize_messages([{"type": "human", "content": "look", "attachments": [{"filename": "old.png", "gridfs_id": "9"}]}])
    assert restored[0].additional_kwargs["attachment_names"] == ["old.png"]


def test_a_plain_message_gets_no_names():
    assert "attachment_names" not in app_utils._deserialize_messages([{"type": "human", "content": "hi"}])[0].additional_kwargs


# ---- wired in ----------------------------------------------------------------------------------------------------------------------------------

def test_the_chat_turn_records_the_names_and_adds_the_block_off_the_event_loop_and_never_for_a_guest():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'chat_sessions[history_key][-1].additional_kwargs["attachment_names"] = [a.filename for a in request.attachments][:10]' in source
    assert "await asyncio.to_thread(earlier_attachments_block, username, session_id, [a.filename for a in (request.attachments or [])])" in source
    assert "if not is_guest_username(username) and attachments_earlier_in(chat_sessions[history_key])" in source
    assert source.index("earlier_attachments_block, username") < source.index("paperwork_utils.build_paperwork_block(")      # before the paperwork guidance reads the turn's attachments
