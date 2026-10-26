"""What Sonic takes from its own recorded experiences, for itself (backend/utils/experience_reflections.py)."""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from backend.utils import experience_reflections as er
from backend.utils import experiences as ex

ROOT = pathlib.Path(__file__).resolve().parents[2]
FAILURE = {"id": "css_what_wins-1", "on": "2026-09", "kind": "failure", "what": "A hover selector for a banner was written without checking how the page was nested, and it did not apply."}
WORKED = {"id": "keep_to_the_ask-0", "on": "2026-10-10", "kind": "worked", "what": "Five small, specific edits to one component in a row each worked as proposed."}
GOOD_FAILURE = "For a system like me, a rule I have not checked against the real markup is a guess, so when a style change is asked for I read how the page is nested before I write the selector."
GOOD_WORKED = "Small, specific changes that name exactly what they touch are easy for a person to check, so I keep proposing edits that narrow."


def run(coro):
    return asyncio.run(coro)


# ---- the checks ------------------------------------------------------------------------------------------------------------------------

def test_a_reflection_on_a_failure_must_say_what_it_does_about_it():
    assert er.reflection_issue(GOOD_FAILURE, "failure") is None
    bare = "For a system like me, a rule written without checking the markup is a guess that sounds like a fact about the page and it did not apply there."
    assert er.reflection_issue(bare, "failure") == "says nothing it does about it"
    verdict = "I was careless with the selector and that is something a system like me should not be about the nesting of a page."
    assert er.reflection_issue(verdict, "failure") == "says nothing it does about it"


def test_a_reflection_on_something_that_worked_needs_no_habit():
    assert er.reflection_issue(GOOD_WORKED, "worked") is None
    plain = "Narrow, named changes are the kind a system like me can be checked on, and that is part of why they tend to go well for the person."
    assert er.reflection_issue(plain, "worked") is None


@pytest.mark.parametrize("text,reason", [
    ("For me, a selector I have not checked is a guess.", "not 12-100 words"),
    (GOOD_FAILURE.replace("a rule", "3 rules"), "contains a number"),
    (GOOD_FAILURE + " I feel bad about the rule that did not apply in that case.", "talks about feeling"),
    (GOOD_FAILURE + " Ever since then I have been more careful with every selector.", "claims time spent or a change in itself"),
    ("A system like it should read how the page is nested before it writes a selector, and so should anyone who works on a page like this one.", "not about itself"),
    (GOOD_FAILURE.replace("a system like me", "you"), "addresses someone"),
])
def test_the_shelfs_rules_still_apply(text, reason):
    assert er.reflection_issue(text, "failure") == reason


def test_the_prompt_asks_for_the_habit_on_a_failure_and_only_the_gist_on_a_success():
    failure, worked = er.build_prompt(FAILURE), er.build_prompt(WORKED)
    assert "FAILURE" in failure and "what you do about it" in failure and "A forced link is worse than none" in failure and "NOTHING" in failure
    assert "WORKED" in worked and "what you do about it" not in worked
    assert FAILURE["what"] in failure and "THINGS THAT ARE TRUE ABOUT YOU" in failure


# ---- generating ------------------------------------------------------------------------------------------------------------------------

def model_says(text):
    async def invoke(prompt):
        return SimpleNamespace(content=text)
    return invoke


def test_a_good_reflection_is_pending_and_a_bad_or_empty_one_is_nothing_and_a_failed_call_is_an_error():
    assert run(er.generate(model_says(GOOD_FAILURE), FAILURE)) == {"status": "pending", "text": GOOD_FAILURE}
    assert run(er.generate(model_says("NOTHING"), FAILURE)) == {"status": "none"}
    assert run(er.generate(model_says("Too short."), FAILURE)) == {"status": "none"}

    async def boom(prompt):
        raise RuntimeError("down")

    assert run(er.generate(boom, FAILURE)) == {"status": "error"}


# ---- storage and approval --------------------------------------------------------------------------------------------------------------

class Col:
    def __init__(self):
        self.docs = {}

    def find(self, flt=None):
        return [dict(d, _id=k) for k, d in self.docs.items()]

    def find_one(self, flt):
        key = flt["_id"]
        return dict(self.docs[key], _id=key) if key in self.docs else None

    def insert_one(self, doc):
        doc = dict(doc)
        self.docs[doc.pop("_id")] = doc

    def update_one(self, flt, update, upsert=False):
        if flt["_id"] in self.docs:
            self.docs[flt["_id"]].update(update["$set"])


class DB:
    def __init__(self):
        self.col = Col()

    def __getitem__(self, name):
        assert name == er.COLLECTION
        return self.col


def test_write_missing_writes_pending_ones_once_and_skips_what_already_has_a_reflection():
    db = DB()
    out = run(er.write_missing(db, [FAILURE, WORKED], model_says(GOOD_FAILURE)))
    assert out == {"pending": 2, "nothing": 0, "errors": 0}                  # a sentence with a habit in it is also fine for a note that worked
    again = run(er.write_missing(db, [FAILURE, WORKED], model_says(GOOD_FAILURE)))
    assert again == {"pending": 0, "nothing": 0, "errors": 0}                 # nothing is written twice


def test_only_an_approved_reflection_is_ever_said_and_the_decisions_are_final_where_they_should_be():
    db = DB()
    run(er.write_missing(db, [FAILURE], model_says(GOOD_FAILURE)))
    er.forget_cached()
    assert er.approved_for(db, FAILURE["id"], now=1000.0) == ""                  # pending: never said
    assert er.decide(db, FAILURE["id"], "approve", "failure", by="jack") == {"ok": True}
    er.forget_cached()
    assert er.approved_for(db, FAILURE["id"], now=1000.0) == GOOD_FAILURE and db.col.docs[FAILURE["id"]]["approved_by"] == "jack"
    assert er.decide(db, FAILURE["id"], "edit", "failure", text="too short")["ok"] is False
    edited = GOOD_FAILURE.replace("a style change", "a stylesheet change")
    assert er.decide(db, FAILURE["id"], "edit", "failure", text=edited) == {"ok": True}
    er.forget_cached()
    assert er.approved_for(db, FAILURE["id"], now=1000.0) == ""                  # an edit goes back to pending
    assert er.decide(db, FAILURE["id"], "remove") == {"ok": True}
    assert er.decide(db, FAILURE["id"], "approve", "failure")["ok"] is False and er.decide(db, "missing", "approve")["ok"] is False


def test_an_edit_that_breaks_a_rule_cannot_be_approved():
    db = DB()
    run(er.write_missing(db, [FAILURE], model_says(GOOD_FAILURE)))
    outcome = er.decide(db, FAILURE["id"], "approve", "failure", text="I feel terrible about the selector and that stays with me whenever a page is nested badly in a style sheet.")
    assert outcome["ok"] is False and "cannot be said" in outcome["problem"]


# ---- how it reaches a reply ------------------------------------------------------------------------------------------------------------

def test_an_approved_reflection_becomes_one_optional_extra_sentence_when_the_note_is_cited():
    item = {**FAILURE, "what": FAILURE["what"]}
    plain = ex.build_block(item)
    with_it = ex.build_block(item, GOOD_FAILURE)
    assert "you may add one more sentence" not in plain and "you may add one more sentence on what you take from it" in with_it and GOOD_FAILURE in with_it
    assert "without adding to it" in with_it


class TwoCollections:
    """A database with the stored notes (none) and the reflections."""

    def __init__(self, reflections):
        self.reflections = reflections

    def __getitem__(self, name):
        if name == er.COLLECTION:
            return self.reflections
        return SimpleNamespace(find=lambda *a, **k: [], create_index=lambda *a, **k: None)


def test_the_dashboard_view_lists_every_hand_written_note_with_its_reflection_state():
    first = ex.entries()[0]
    db = TwoCollections(DB().col)
    run(er.write_missing(db, [{**FAILURE, "id": first["id"]}], model_says(GOOD_FAILURE)))
    view = ex.admin_view(db)
    assert set(view) == {"pending", "approved", "registry", "limits"} and view["pending"] == [] and view["approved"] == []
    by_id = {note["id"]: note for note in view["registry"]}
    assert by_id[first["id"]]["reflection"] == {"status": "pending", "text": GOOD_FAILURE}
    assert all(note["reflection"] == {"status": "", "text": ""} for note_id, note in by_id.items() if note_id != first["id"])
    assert view["limits"]["reflections_at_once"] == er.BACKFILL_LIMIT


def test_the_endpoints_are_admin_only_and_the_character_entry_point_passes_the_approved_line():
    source = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    for route in ('@app.post("/api/admin/experiences/reflections/write")', '@app.post("/api/admin/experiences/{experience_id}/reflection")'):
        assert "Depends(require_global_admin)" in source[source.index(route):source.index(route) + 500], route
    character = (ROOT / "backend" / "utils" / "character.py").read_text(encoding="utf-8")
    assert "experience_reflections.approved_for(db, experience[\"id\"])" in character and "experiences.build_block(experience, reflection)" in character
