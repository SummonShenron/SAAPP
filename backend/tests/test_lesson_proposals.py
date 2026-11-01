"""Lessons for the project's guidance file (2026-11-01): when a message follows a coding reply, Sonic is told it may propose ONE rule for .sonic/AGENTS.md if it was a correction a rule would have prevented.
The rule is an ordinary edit card that always asks, and the server refuses a change that would push the file past what is read."""
import pathlib
import re

import pytest

from backend.services import local_edits as le
from backend.utils import lesson_proposals as lp
from backend.utils import project_guidance as pg

ROOT = pathlib.Path(__file__).resolve().parents[2]
OURS = {"path": ".sonic/AGENTS.md", "text": "# Project\n- rule one\n- rule two", "cut": False}


# ---- when the note is added -------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("followup, guidance, expected", [
    ("followed", OURS, True),
    ("redo", OURS, True),
    (None, OURS, False),                                                                      # the message does not follow a coding reply
    ("", OURS, False),
    ("followed", None, False),                                                                 # no guidance file at all
    ("followed", {"path": "AGENTS.md", "text": "x", "cut": False}, False),                     # another tool's file is never edited for lessons
    ("followed", {"path": "CLAUDE.md", "text": "x", "cut": False}, False),
    ("followed", {**OURS, "cut": True}, False),                                                # a file already cut has no room to add to
    ("followed", {**OURS, "text": ""}, False),
])
def test_the_note_is_offered_only_after_a_coding_reply_and_only_for_our_own_complete_file(followup, guidance, expected):
    assert lp.should_offer(followup, guidance) is expected


def test_the_note_asks_for_one_rule_names_the_file_and_its_size_and_says_what_does_not_count():
    text = lp.note(OURS)
    assert ".sonic/AGENTS.md" in text and f"is {len(OURS['text'])} now" in text and f"up to {pg.MAX_CHARS} characters" in text
    for phrase in ("ONE new rule", "not a matter of taste, not a one-off slip", "Do not add a rule that is already in PROJECT GUIDANCE", "do not propose the same one again if they decline",
                   "propose nothing and do not mention this", "replace the least useful line"):
        assert phrase in text, phrase


def test_the_note_holds_nothing_the_person_wrote():
    assert lp.note({"path": ".sonic/AGENTS.md", "text": "SECRET-RULE-TEXT"}).count("SECRET-RULE-TEXT") == 0


def test_this_repositorys_own_guidance_file_qualifies_and_leaves_room():
    guidance = pg.read(ROOT)
    assert guidance and lp.should_offer("followed", guidance)
    assert guidance["path"] == lp.GUIDANCE_PATH and len(guidance["text"]) < pg.MAX_CHARS


# ---- the server will not let a rule be cut off ----------------------------------------------------------------------------------------------

def _propose(before, after_extra="", replace=None, path=".sonic/AGENTS.md"):
    files = {path: before}
    edit = {"path": path, "old_string": replace[0], "new_string": replace[1]} if replace else {"path": path, "old_string": before.rstrip().split("\n")[-1], "new_string": before.rstrip().split("\n")[-1] + after_extra}
    return le.validate_edit_proposal([edit], lambda p: files.get(p))


def test_a_rule_that_fits_is_accepted():
    result = _propose("# Rules\n- one\n- two\n", "\n- three: a new lesson (what went wrong)")
    assert result["ok"] and result["files"][0]["path"] == ".sonic/AGENTS.md"


def test_a_rule_that_would_push_the_file_past_what_is_read_is_refused_in_words():
    base = "# Rules\n" + "\n".join(f"- rule number {n} with some words to fill the line out" for n in range(88))
    assert pg.fits(base) and len(base) > pg.MAX_CHARS - 400
    result = _propose(base + "\n", "\n- " + "x" * 400)
    assert not result["ok"] and "only the first 5000 are ever read" in result["errors"][0] and "replace the least useful line" in result["errors"][0]


def test_replacing_a_line_with_a_shorter_one_is_fine_even_near_the_limit():
    base = "# Rules\n" + "\n".join(f"- rule number {n} with some words to fill the line out" for n in range(80)) + "\n"
    result = _propose(base, replace=("- rule number 5 with some words to fill the line out", "- rule 5, shortened"))
    assert result["ok"]


def test_a_file_that_is_already_too_long_may_be_shortened_but_not_lengthened():
    long = "# Rules\n" + "\n".join(f"- rule number {n} with some words to fill the line out" for n in range(120)) + "\n"
    assert not pg.fits(long)
    assert _propose(long, replace=("- rule number 5 with some words to fill the line out", "- rule 5"))["ok"]
    assert not _propose(long, "\n- more")["ok"]


def test_other_files_have_no_size_rule_here():
    base = "x = 1\n" * 2000 + "last = 0\n"                                                       # well over 5,000 characters, with a last line that is unique
    assert _propose(base, "\ny = 2", path="src/big.py")["ok"]
    assert _propose("# a\n" * 2000 + "# last\n", "\n# b", path="AGENTS.md")["ok"]                          # a root AGENTS.md is another tool's, not ours


# ---- wired in ---------------------------------------------------------------------------------------------------------------------------------

def test_the_app_passes_the_followup_and_the_tool_agent_adds_the_note_after_the_stable_guidance_and_counts_it():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    workflow = (ROOT / "backend/services/agent_workflow.py").read_text(encoding="utf-8")
    assert '"code_followup": code_followup,' in app
    assert "if lesson_proposals.should_offer(state.get(\"code_followup\"), guidance):" in workflow
    assert workflow.index("coding_preferences + project_guidance.block(guidance)") < workflow.index("coding_preferences = coding_preferences + lesson_proposals.note(guidance)")
    assert '{"lesson_offered": 1}' in workflow and '{"guidance_edit_proposed": 1}' in workflow


def test_the_counters_exist_and_the_guidance_file_never_applies_by_itself():
    from backend.utils.self_counters import KEYS

    assert {"lesson_offered", "guidance_edit_proposed"} <= KEYS
    approval = (ROOT / "local/src/editApproval.ts").read_text(encoding="utf-8")
    assert "export const GUIDANCE_FILE = '.sonic/AGENTS.md';" in approval and "always asks (it shapes every coding turn)" in approval
