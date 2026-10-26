"""The self-model: what kind of system Sonic is, from evidence, approved by a person, revisable (backend/utils/self_model.py)."""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from backend.utils import self_model as sm

ROOT = pathlib.Path(__file__).resolve().parents[2]

PARTS = {
    "time_facts": ["In the past week, about 55% of the chat turns I handled were coding and repository work.", "On my own, on a schedule, I run: a health check; tidying old chats; reading a page a day."],
    "tasks": {"runs": 12, "people": 1},
    "goals": [{"id": "coding", "title": "Get better at coding", "state": "unproven"}],
    "shelf": {"items": 11, "topics": 3, "labels": ["ai research", "computing history", "security"]},
    "leanings": {"count": 29, "philosophies": 9, "example": "I trust what I can check over what I assume."},
    "record": {"total": 45, "failure": 30, "worked": 7, "record": 8},
    "changelog": {"count": 75, "newest": "A record of my own work"},
    "tools": {"reads": 14, "searches": 8, "edits": 1},
    "code": {"turns": 79, "edited": 28, "tested": 0},
}


def facts():
    return sm.build_facts(PARTS)


def run(coro):
    return asyncio.run(coro)


# ---- the facts are written by code ---------------------------------------------------------------------------------------------------------

def test_every_fact_has_a_stable_key_and_one_plain_sentence():
    built = facts()
    keys = [f["key"] for f in built]
    assert len(keys) == len(set(keys)) and {"routines", "tasks", "goal_coding", "reading", "leanings", "record", "changelog", "tools", "coding", "between", "feelings"} <= set(keys)
    assert all(f["text"] and f["text"].endswith(".") and "\n" not in f["text"] for f in built)
    coding = next(f for f in built if f["key"] == "coding")
    assert "79 coding turns" in coding["text"] and "28 proposed or made an edit" in coding["text"] and "0 of those ran a test" in coding["text"]


def test_a_source_that_could_not_be_read_is_simply_left_out():
    built = sm.build_facts({"time_facts": PARTS["time_facts"]})
    assert {f["key"] for f in built} == {"work_0", "routines", "between", "feelings"} and sm.build_facts({"tools": {"reads": 0}}) == sm.build_facts({})


# ---- the checks ------------------------------------------------------------------------------------------------------------------------

CITED = ["I keep a reading shelf of 11 pieces I have read, across 3 topics: ai research, computing history, security.", "In the past week, about 55% of the chat turns I handled were coding and repository work."]
GOOD = "Most of my chat turns are coding and repository work, and I keep a small reading shelf of 11 pieces on topics I lean toward."


def test_a_statement_that_rests_on_its_facts_passes():
    assert sm.statement_issue(GOOD, CITED) is None


@pytest.mark.parametrize("text,cited,reason", [
    ("I am an assistant.", CITED, "not 8-60 words"),
    (GOOD, [], "cites no fact"),
    (GOOD.replace("11", "12"), CITED, "has a number its facts do not"),
    ("Most of my chat turns are coding and repository work, and my own record shows nothing at all about it.".replace("my own", "your own"), CITED, "addresses someone"),
    ("I feel proud that most of my chat turns are coding and repository work, and I keep a small shelf of reading.", CITED, "talks about feeling"),
    ("Ever since I started, most of my chat turns have been coding and repository work with a small reading shelf.", CITED, "claims time spent or a change in itself"),
    ("My target is to raise the share of my chat turns that are coding and repository work, with a small shelf of reading.", CITED, "claims a stake in its own numbers"),
    ("I spend most of my time on coding and repository work, and I keep a small reading shelf of 11 pieces.", CITED, "claims a share of its time, which its facts count in turns"),
    ("I keep a reading shelf of 11 pieces across 3 topics to inform my coding and repository work.", CITED, "claims a purpose its facts do not state"),
    ("The system handles most of its chat turns as coding and repository work, with a small reading shelf of pieces.", CITED, "not about itself"),
])
def test_a_statement_that_could_overreach_is_refused(text, cited, reason):
    assert sm.statement_issue(text, cited) == reason


# ---- parsing and writing ---------------------------------------------------------------------------------------------------------------

def test_the_model_reply_is_read_one_statement_a_line_with_its_citations():
    reply = f"1. {GOOD} [F2, F1]\n- I run a health check on a schedule. [F3]\nno citation here at all\n```"
    parsed = sm.parse(reply)
    assert parsed == [{"text": GOOD, "cites": [1, 2]}, {"text": "I run a health check on a schedule.", "cites": [3]}]


class Col:
    def __init__(self):
        self.docs = {}

    def find(self, flt=None):
        want = (flt or {}).get("status", {}).get("$in")
        return [dict(d, _id=k) for k, d in self.docs.items() if want is None or d.get("status") in want]

    def find_one(self, flt):
        return dict(self.docs[flt["_id"]], _id=flt["_id"]) if flt["_id"] in self.docs else None

    def insert_one(self, doc):
        doc = dict(doc)
        self.docs[doc.pop("_id")] = doc

    def update_one(self, flt, update, upsert=False):
        doc = self.docs.get(flt["_id"])
        if doc is None:
            return SimpleNamespace(modified_count=0)
        status = flt.get("status")
        if isinstance(status, str) and doc.get("status") != status:
            return SimpleNamespace(modified_count=0)
        if isinstance(status, dict) and doc.get("status") not in status["$in"]:
            return SimpleNamespace(modified_count=0)
        doc.update(update["$set"])
        return SimpleNamespace(modified_count=1)

    def delete_many(self, flt):
        for key in [k for k, d in self.docs.items() if d.get("status") == flt.get("status")]:
            del self.docs[key]


class DB:
    def __init__(self):
        self.col = Col()

    def __getitem__(self, name):
        assert name == sm.COLLECTION
        return self.col


def says(text):
    async def invoke(prompt):
        assert "THE FACTS" in prompt and "F1:" in prompt
        return SimpleNamespace(content=text)
    return invoke


def test_good_statements_are_stored_pending_with_the_facts_they_rest_on_and_bad_ones_are_dropped():
    db = DB()
    built = facts()
    reading = next(i for i, f in enumerate(built, 1) if f["key"] == "reading")
    work = next(i for i, f in enumerate(built, 1) if f["key"] == "work_0")
    reply = f"{GOOD} [F{work}, F{reading}]\nI feel proud of everything I read on my shelf of pieces. [F{reading}]\nI keep a shelf of 99 pieces across several topics of every kind. [F{reading}]\nI am something. [F999]"
    out = run(sm.generate(db, says(reply), built))
    assert out["stored"] == 1 and out["dropped"] == 3
    doc = next(iter(db.col.docs.values()))
    assert doc["status"] == sm.PENDING and doc["text"] == GOOD and {c["key"] for c in doc["cites"]} == {"work_0", "reading"} and all(c["text"] for c in doc["cites"])


def test_a_new_write_replaces_the_waiting_ones_but_never_touches_what_was_approved():
    db = DB()
    built = facts()
    reply = f"{GOOD} [F1, F{next(i for i, f in enumerate(built, 1) if f['key'] == 'reading')}]"
    run(sm.generate(db, says(reply), built))
    first = next(iter(db.col.docs))
    assert sm.approve(db, first, by="jack") is True
    run(sm.generate(db, says(reply), built))
    run(sm.generate(db, says(reply), built))
    statuses = sorted(d["status"] for d in db.col.docs.values())
    assert statuses == [sm.APPROVED, sm.PENDING]


def test_too_few_facts_or_no_database_writes_nothing_and_a_failing_model_never_raises():
    assert run(sm.generate(DB(), says("x"), facts()[:2])) == {"stored": 0, "dropped": 0, "facts": 2}
    assert run(sm.generate(None, says("x"), facts()))["stored"] == 0

    async def boom(prompt):
        raise RuntimeError("down")

    assert run(sm.generate(DB(), boom, facts())) == {"error": True}


# ---- approval, staleness and use -------------------------------------------------------------------------------------------------------

def stored_statement(db):
    built = facts()
    reading = next(i for i, f in enumerate(built, 1) if f["key"] == "reading")
    run(sm.generate(db, says(f"{GOOD} [F1, F{reading}]"), built))
    return next(iter(db.col.docs)), built


def test_only_an_approved_current_statement_is_ever_said():
    db = DB()
    statement_id, built = stored_statement(db)
    assert sm.approved_block(db, built) == ""                                           # pending: never said
    sm.approve(db, statement_id, by="jack")
    block = sm.approved_block(db, built)
    assert GOOD in block and "WHAT YOU KNOW ABOUT WHAT KIND OF SYSTEM YOU ARE" in block and "not covered here, say you do not know" in block


def test_a_statement_whose_facts_changed_is_stale_and_is_not_said_until_it_is_looked_at_again():
    db = DB()
    statement_id, built = stored_statement(db)
    sm.approve(db, statement_id)
    changed = [dict(f, text=f["text"].replace("11 pieces", "13 pieces")) if f["key"] == "reading" else f for f in built]
    assert sm.list_all(db, changed)["approved"][0]["stale"] is True and sm.list_all(db, built)["approved"][0]["stale"] is False
    assert sm.approved_block(db, changed) == ""
    gone = [f for f in built if f["key"] != "reading"]
    assert sm.is_stale(db.col.docs[statement_id] | {"cites": db.col.docs[statement_id]["cites"]}, gone) is True


def test_an_edit_is_checked_against_the_facts_it_cited_and_sends_an_approved_statement_back_to_pending():
    db = DB()
    statement_id, built = stored_statement(db)
    sm.approve(db, statement_id)
    assert sm.edit(db, statement_id, "I feel proud of my shelf of 11 pieces and my coding work as a whole thing now.") == "talks about feeling"
    assert sm.edit(db, statement_id, "Most of my chat turns are coding and repository work; the rest is smaller, and I keep a shelf of 11 pieces.") is None
    assert db.col.docs[statement_id]["status"] == sm.PENDING and sm.edit(db, "missing", GOOD) == "not found"
    assert sm.retire(db, statement_id) is True and sm.list_all(db)["pending"] == [] and sm.retire(db, statement_id) is False


def test_a_few_facts_is_enough_to_ask_but_the_block_says_nothing_with_no_database():
    assert sm.approved_block(None, facts()) == "" and sm.list_all(None) == {"pending": [], "approved": []}


@pytest.mark.parametrize("message,expected", [
    ("what kind of system are you?", True), ("tell me about yourself", True), ("how would you describe yourself", True), ("what are you?", True),
    ("what is your purpose", True), ("what do you spend your time on", True), ("who are you", True), ("how do you work?", True),
    ("what are you doing", False), ("what is the weather", False), ("fix the css please", False), ("are you sure", False),
])
def test_the_questions_that_ask_what_it_is(message, expected):
    assert sm.asks(message) is expected


# ---- wired in --------------------------------------------------------------------------------------------------------------------------

def test_the_dashboard_endpoints_are_admin_only_and_the_chat_uses_the_approved_block():
    source = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    for route in ('@app.get("/api/admin/self-model")', '@app.post("/api/admin/self-model/write")', '@app.post("/api/admin/self-model/{statement_id}/approve")',
                  '@app.patch("/api/admin/self-model/{statement_id}")', '@app.delete("/api/admin/self-model/{statement_id}")'):
        assert "Depends(require_global_admin)" in source[source.index(route):source.index(route) + 450], route
    assert "self_model.asks(question)" in source and "self_model.approved_block" in source
