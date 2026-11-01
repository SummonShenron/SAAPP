"""Deleting and moving files in an edit proposal (2026-11-01). Sonic could neither remove an unused component nor move code between files: its edits only changed text, created files and made Word
documents, and the runner refuses rm and del. Now {path, delete: true} and {path, rename_to} are proposal forms: the server checks them against its snapshot, the card shows what is about to go, the
browser applies them (never automatically) and Undo puts the text back. Text files only: a file whose bytes are not in the snapshot could not be restored."""
import pathlib

import pytest

from backend.services import local_edits as le

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILES = {
    "src/old.py": "def old():\n    return 1\n",
    "src/keep.py": "def keep():\n    return 2\n",
    "src/broken.py": "def oops(:\n    pass\n",
    "notes/Letter.docx": "[1] Dear Sam,",
}


def _read(path):
    return FILES.get(path)


def _propose(*edits):
    return le.validate_edit_proposal(list(edits), _read)


# ---- delete -----------------------------------------------------------------------------------------------------------------------------------

def test_a_delete_lists_every_line_as_removed_and_says_the_file_will_be_gone():
    result = _propose({"path": "src/old.py", "delete": True})
    assert result["ok"] and result["edits"] == [{"type": "delete", "path": "src/old.py"}]
    card = result["files"][0]
    assert card["kind"] == "delete" and card["path"] == "src/old.py" and card["additions"] == 0 and card["deletions"] == 2        # two lines, not three: the piece after the final newline is no line
    assert card["diff"].splitlines() == ["--- a/src/old.py", "+++ /dev/null", "-def old():", "-    return 1"]
    assert result["removed"] == ["src/old.py"] and "src/old.py" not in result["texts"]


def test_a_delete_of_a_file_that_is_not_there_is_refused_in_words():
    result = _propose({"path": "src/missing.py", "delete": True})
    assert not result["ok"] and "nothing to delete" in result["errors"][0] and "find_file" in result["errors"][0]


def test_a_word_document_is_never_deleted():
    result = _propose({"path": "notes/Letter.docx", "delete": True})
    assert not result["ok"] and "Word documents can't be deleted or moved" in result["errors"][0]


def test_a_protected_or_unsafe_path_cannot_be_deleted():
    assert not _propose({"path": "../outside.py", "delete": True})["ok"]
    assert not _propose({"path": ".env", "delete": True})["ok"]


def test_a_delete_stands_alone_in_its_edit():
    for extra in ({"old_string": "a", "new_string": "b"}, {"content": "x"}, {"rename_to": "src/z.py"}):
        result = _propose({"path": "src/old.py", "delete": True, **extra})
        assert not result["ok"] and "stands alone" in result["errors"][0], extra


def test_a_file_cannot_be_deleted_twice_or_a_file_the_proposal_creates():
    twice = _propose({"path": "src/old.py", "delete": True}, {"path": "src/old.py", "delete": True})
    assert not twice["ok"] and "already removes or moves" in twice["errors"][0]
    new = _propose({"path": "src/new.py", "content": "x = 1\n"}, {"path": "src/new.py", "delete": True})
    assert not new["ok"] and "nothing to delete" in new["errors"][0]


def test_a_delete_does_not_trip_the_syntax_check_even_for_a_broken_file():
    result = _propose({"path": "src/broken.py", "delete": True})
    assert result["ok"] and result["removed"] == ["src/broken.py"]


def test_a_file_can_be_deleted_and_recreated_in_one_proposal():
    result = _propose({"path": "src/old.py", "delete": True}, {"path": "src/old.py", "content": "def old():\n    return 9\n"})
    assert result["ok"] and result["removed"] == [] and result["texts"] == {"src/old.py": "def old():\n    return 9\n"}


# ---- move ---------------------------------------------------------------------------------------------------------------------------------------

def test_a_move_is_one_card_with_where_from_and_to_and_the_new_text_and_the_old_path_removed():
    result = _propose({"path": "src/old.py", "rename_to": "lib/renamed.py"})
    assert result["ok"] and result["edits"] == [{"type": "rename", "path": "src/old.py", "to": "lib/renamed.py"}]
    assert len(result["files"]) == 1
    card = result["files"][0]
    assert card["kind"] == "rename" and card["path"] == "lib/renamed.py" and card["source"] == "src/old.py"
    assert card["diff"] == "rename from src/old.py\nrename to lib/renamed.py" and card["additions"] == 0 and card["deletions"] == 0
    assert result["texts"] == {"lib/renamed.py": FILES["src/old.py"]} and result["removed"] == ["src/old.py"]


def test_a_move_that_also_changes_the_file_shows_the_changes_too():
    result = _propose({"path": "src/old.py", "rename_to": "lib/renamed.py"}, {"path": "lib/renamed.py", "old_string": "return 1", "new_string": "return 5"})
    assert result["ok"] and len(result["files"]) == 1
    card = result["files"][0]
    assert card["diff"].startswith("rename from src/old.py\nrename to lib/renamed.py\n") and "+    return 5" in card["diff"] and card["additions"] == 1 and card["deletions"] == 1
    assert result["texts"]["lib/renamed.py"] == "def old():\n    return 5\n"


def test_a_move_onto_a_file_that_exists_or_onto_itself_is_refused():
    onto = _propose({"path": "src/old.py", "rename_to": "src/keep.py"})
    assert not onto["ok"] and "never overwrites a file by moving another onto it" in onto["errors"][0]
    same = _propose({"path": "src/old.py", "rename_to": "src/old.py"})
    assert not same["ok"] and "different path" in same["errors"][0]


def test_a_move_of_a_missing_or_word_file_or_to_a_bad_place_is_refused():
    assert "nothing to move" in _propose({"path": "src/missing.py", "rename_to": "src/x.py"})["errors"][0]
    assert "Word documents can't be deleted or moved" in _propose({"path": "notes/Letter.docx", "rename_to": "notes/L.docx"})["errors"][0]
    assert not _propose({"path": "src/old.py", "rename_to": "../x.py"})["ok"]
    assert not _propose({"path": "src/old.py", "rename_to": ".env"})["ok"]
    assert "different path, and not a Word document" in _propose({"path": "src/old.py", "rename_to": "src/old.docx"})["errors"][0]


def test_moving_a_broken_file_unchanged_is_not_this_proposals_fault():
    assert _propose({"path": "src/broken.py", "rename_to": "src/still_broken.py"})["ok"]


def test_the_edits_after_a_move_are_checked_like_any_others():
    result = _propose({"path": "src/old.py", "rename_to": "lib/r.py"}, {"path": "lib/r.py", "old_string": "def old():", "new_string": "def old(:"})
    assert not result["ok"] and "syntax error" in result["errors"][0]


# ---- how it reaches the page and the folder --------------------------------------------------------------------------------------------------

def test_the_applied_result_drops_removed_files_from_the_servers_copy_and_the_turns_cache_and_counts_for_the_plan():
    source = (ROOT / "backend/services/agent_workflow.py").read_text(encoding="utf-8")
    assert 'removed = result.get("removed") or []' in source and "_turn_file_cache.pop(path, None)" in source
    assert '[{"path": path, "content": text} for path, text in texts.items()], list(removed),' in source
    assert "[*texts, *removed])" in source                                                 # a plan step that deletes a file is done when it is deleted
    assert 'or f.get("kind") == "delete"}' in source


def test_sonic_is_told_the_forms_and_to_fix_what_refers_to_the_file_in_the_same_call():
    tools = (ROOT / "backend/services/native_tools.py").read_text(encoding="utf-8")
    assert '"delete": {"type": "boolean"' in tools and '"rename_to": {"type": "string"' in tools
    assert "{path, delete: true} DELETES an existing text file" in tools and "never one of the two without the other" in tools


def test_a_delete_or_move_never_applies_by_itself():
    approval = (ROOT / "local/src/editApproval.ts").read_text(encoding="utf-8")
    assert "deleting or moving a file always asks" in approval
