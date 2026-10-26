"""New notes for Sonic's record: captured from objective signals, abstract by construction, pending until a person approves them (backend/utils/experience_capture.py)."""
import asyncio
import copy
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.utils import experience_capture as xc
from backend.utils import experiences as ex

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
GOOD = "It wrote a style rule without checking how the page was nested, and the rule did not apply."


def run(coro):
    return asyncio.run(coro)


# ---- an in-memory collection ---------------------------------------------------------------------------------------------------------------

class Result:
    def __init__(self, modified=0, deleted=0):
        self.modified_count = self.matched_count = modified
        self.deleted_count = deleted


def matches(doc, flt):
    for key, want in flt.items():
        have = doc.get(key)
        if isinstance(want, dict):
            if "$in" in want and have not in want["$in"]:
                return False
            if "$gte" in want and not (str(have or "") >= want["$gte"]):
                return False
        elif have != want:
            return False
    return True


class Col:
    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        pass

    def find(self, flt=None, projection=None):
        rows = [copy.deepcopy(d) for d in self.docs if matches(d, flt or {})]
        if projection and any(v == 1 for v in projection.values()):
            rows = [{k: v for k, v in row.items() if k == "_id" or projection.get(k) == 1} for row in rows]
        return rows

    def count_documents(self, flt):
        return len([d for d in self.docs if matches(d, flt)])

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def update_one(self, flt, update, upsert=False):
        for doc in self.docs:
            if matches(doc, flt):
                doc.update(copy.deepcopy(update.get("$set", {})))
                for key in update.get("$unset", {}):
                    doc.pop(key, None)
                return Result(1)
        return Result(0)

    def delete_one(self, flt):
        for doc in self.docs:
            if matches(doc, flt):
                self.docs.remove(doc)
                return Result(deleted=1)
        return Result()

    def delete_many(self, flt):
        hit = [d for d in self.docs if matches(d, flt)]
        for d in hit:
            self.docs.remove(d)
        return Result(deleted=len(hit))


class DB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        return self.cols.setdefault(name, Col())


def model_says(text):
    async def invoke(prompt):
        return SimpleNamespace(content=text)
    return invoke


def embedder(table=None):
    table = table or {}
    return lambda text: table.get(text, [1.0, 0.0, 0.0])


CONTEXT = {"request": "make the banner glow on hover", "reply": "Here is the selector.", "followup": "that didn't work", "tools": ["read_repo_file"]}


# ---- every note is checked by code ---------------------------------------------------------------------------------------------------------

def test_a_plain_abstract_sentence_passes():
    assert xc.note_issue(GOOD) is None
    assert xc.note_issue("It made five small edits in a row to one component, and each worked as proposed.") is None      # a count in words is a general description, not a figure from a conversation
    assert xc.note_issue("It read the project's own stylesheet first, and the change that followed did not need a second try.") is None      # an apostrophe inside a word is fine


@pytest.mark.parametrize("text,reason", [
    ("It wrote a rule.", "not 6-40 words"),
    ("It wrote a style rule without checking the nesting. It did not apply.", "not one sentence"),
    ("The assistant wrote a style rule without checking how the page was nested, and it did not apply.", "not about itself in the third person"),
    ("It edited \"Chat.tsx\" without reading the stylesheet, and the rule did not apply on the page.", "contains a link, quote, path or identifier"),
    ("It edited Chat.tsx without reading the stylesheet, and the rule did not apply on the page.", "contains a link, quote, path or identifier"),
    ("It called handle_request without checking what it returned, and the answer was wrong anyway.", "contains a link, quote, path or identifier"),
    ("It read https://example.com and then wrote a rule that did not apply on the page at all.", "contains a link, quote, path or identifier"),
    ("It proposed 3 edits in a row and each one of them worked as expected on the page.", "contains a number"),
    ("It told you the rule would apply, and it did not apply on the page after the change.", "addresses or names someone"),
    ("It wrote a rule for the banner on the Sunday call and the rule did not apply to the page.", "contains a proper name"),
    ("It felt sure the rule would apply, and the rule did not apply on the page after the change.", "talks about feeling or intent"),
    ("It wanted the rule to apply, and wrote it without checking how the page was nested at all.", "talks about feeling or intent"),
])
def test_a_note_that_could_carry_anything_from_a_conversation_is_refused(text, reason):
    assert xc.note_issue(text) == reason


def test_the_prompt_gives_the_signal_the_excerpt_and_the_rules_and_tells_the_model_not_to_copy():
    prompt = xc.build_prompt("pushback", CONTEXT)
    assert "read as pushback" in prompt and "It counts as: failure" in prompt and "make the banner glow on hover" in prompt and "never copy from it" in prompt and "NOTHING" in prompt
    assert "read_repo_file" in prompt and "praised" in xc.build_prompt("praise", CONTEXT) and "It counts as: worked" in xc.build_prompt("praise", CONTEXT)
    long = xc.build_prompt("pushback", {**CONTEXT, "request": "x " * 2000})
    assert len(long) < 3500                                                             # an excerpt, never the whole turn


def test_the_model_reply_is_read_with_its_area_and_nothing_means_nothing():
    assert xc.parse("NOTHING") is None and xc.parse("") is None and xc.parse(SimpleNamespace(content="nothing at all")) is None
    assert xc.parse(f"{GOOD}\nAREA: frontend") == {"what": GOOD, "area": "frontend"}
    assert xc.parse(f"\"{GOOD}\"\nAREA: nonsense") == {"what": GOOD, "area": "general"}


# ---- when it may run -------------------------------------------------------------------------------------------------------------------

def test_the_gates_keep_it_out_of_guests_safety_contexts_heavy_moods_and_untrusted_text():
    ok = dict(guest=False, risk_active=False, energy_tier="open")
    assert xc.eligible(**ok, tools_used=["read_repo_file", "propose_local_edits"])
    assert not xc.eligible(**{**ok, "guest": True}) and not xc.eligible(**{**ok, "risk_active": True}) and not xc.eligible(**{**ok, "energy_tier": "low"})
    assert not xc.eligible(**ok, tools_used=["read_repo_file", "web_search"]) and not xc.eligible(**ok, tools_used=["read_gmail_message"])


def test_a_turn_that_read_untrusted_text_never_reaches_the_model():
    called = []

    async def invoke(prompt):
        called.append(prompt)
        return SimpleNamespace(content=GOOD)

    outcome = run(xc.run_capture("pushback", CONTEXT, guest=False, risk_active=False, energy_tier="open", untrusted=True))
    assert outcome == {"result": "skipped", "why": "not eligible"} and called == []


# ---- storing, merging, limits ------------------------------------------------------------------------------------------------------------

def test_a_good_note_is_stored_pending_with_a_date_a_kind_and_the_signal_and_nothing_else_from_the_turn():
    db = DB()
    out = run(xc.capture(db, "pushback", CONTEXT, model_says(f"{GOOD}\nAREA: frontend"), embedder(), now=NOW))
    assert out["result"] == "stored"
    doc = db[xc.COLLECTION].docs[0]
    assert doc["status"] == xc.PENDING and doc["on"] == "2026-10-26" and doc["kind"] == "failure" and doc["signal"] == "pushback" and doc["area"] == "frontend" and doc["count"] == 1
    assert doc["expires_at"] and "request" not in doc and "reply" not in doc and "followup" not in doc
    assert set(doc) == {"_id", "on", "kind", "signal", "area", "what", "status", "count", "embedding", "created_at", "updated_at", "expires_at"}


def test_praise_is_something_that_worked_and_the_kind_comes_from_the_signal_not_the_model():
    db = DB()
    run(xc.capture(db, "praise", CONTEXT, model_says("It read the exact values from the project's logo file before it wrote the new file, and the result was accepted as it was."), embedder(), now=NOW))
    assert db[xc.COLLECTION].docs[0]["kind"] == "worked"


@pytest.mark.parametrize("reply,result", [("NOTHING", "none"), ("It said so.", "invalid"), ("It wrote Chat.tsx and nothing else of any real use in the end.", "invalid")])
def test_nothing_or_an_unacceptable_note_stores_nothing(reply, result):
    db = DB()
    assert run(xc.capture(db, "pushback", CONTEXT, model_says(reply), embedder(), now=NOW))["result"] == result and db[xc.COLLECTION].docs == []


def test_a_note_close_to_one_already_stored_adds_to_its_count_instead():
    db = DB()
    run(xc.capture(db, "pushback", CONTEXT, model_says(GOOD), embedder({GOOD: [1.0, 0.0, 0.0]}), now=NOW))
    again = "It wrote a style rule without first checking the page's nesting, and that rule did not apply either."
    out = run(xc.capture(db, "pushback", CONTEXT, model_says(again), embedder({again: [0.99, 0.05, 0.0]}), now=NOW))
    assert out["result"] == "merged" and len(db[xc.COLLECTION].docs) == 1 and db[xc.COLLECTION].docs[0]["count"] == 2


def test_a_different_note_is_a_new_one():
    db = DB()
    run(xc.capture(db, "pushback", CONTEXT, model_says(GOOD), embedder({GOOD: [1.0, 0.0, 0.0]}), now=NOW))
    other = "It said a change was made when no tool had made it, and the file stayed exactly as it was."
    assert run(xc.capture(db, "answer_check", CONTEXT, model_says(other), embedder({other: [0.0, 1.0, 0.0]}), now=NOW))["result"] == "stored" and len(db[xc.COLLECTION].docs) == 2


def test_the_daily_limit_and_the_waiting_limit_hold():
    db = DB()
    for number in range(xc.MAX_PER_DAY):
        db[xc.COLLECTION].docs.append({"_id": str(number), "status": xc.PENDING, "created_at": NOW.isoformat(), "embedding": []})
    out = run(xc.capture(db, "pushback", CONTEXT, model_says(GOOD), embedder(), now=NOW))
    assert out == {"result": "skipped", "why": "daily limit"}
    full = DB()
    for number in range(xc.MAX_PENDING):
        full[xc.COLLECTION].docs.append({"_id": str(number), "status": xc.PENDING, "created_at": "2026-09-01T00:00:00+00:00", "embedding": []})
    assert run(xc.capture(full, "pushback", CONTEXT, model_says(GOOD), embedder(), now=NOW)) == {"result": "skipped", "why": "too many waiting"}


def test_a_failing_model_or_database_never_raises():
    async def boom(prompt):
        raise RuntimeError("down")

    assert run(xc.capture(DB(), "pushback", CONTEXT, boom, embedder(), now=NOW))["result"] == "error"
    assert run(xc.capture(None, "pushback", CONTEXT, model_says(GOOD), embedder(), now=NOW))["result"] == "skipped"
    assert run(xc.capture(DB(), "nonsense", CONTEXT, model_says(GOOD), embedder(), now=NOW))["result"] == "skipped"


# ---- approval --------------------------------------------------------------------------------------------------------------------------

def stored_one(db):
    run(xc.capture(db, "pushback", CONTEXT, model_says(GOOD), embedder(), now=NOW))
    return db[xc.COLLECTION].docs[0]["_id"]


def test_a_pending_note_is_never_citable_until_approved_and_then_it_is_without_an_expiry():
    db = DB()
    note_id = stored_one(db)
    assert xc.approved_entries(db) == [] and ex.entries(db) == ex.entries()
    assert xc.approve(db, note_id, by="jack@example.com", now=NOW) is True and xc.approve(db, note_id) is False
    doc = db[xc.COLLECTION].docs[0]
    assert doc["status"] == xc.APPROVED and doc["approved_by"] == "jack@example.com" and "expires_at" not in doc
    cited = xc.approved_entries(db)
    assert len(cited) == 1 and cited[0]["id"] == f"db-{note_id}" and cited[0]["what"] == GOOD and cited[0]["approved"] is True and cited[0]["when"] == GOOD
    ex.forget_cached()
    assert any(item["id"] == f"db-{note_id}" for item in ex.entries(db))


def test_an_edit_must_pass_the_same_checks_and_removal_is_final():
    db = DB()
    note_id = stored_one(db)
    assert xc.edit(db, note_id, "It said so.") == "not 6-40 words" and db[xc.COLLECTION].docs[0]["what"] == GOOD
    better = "It checked the real nesting of the page before it wrote the style rule, and the rule applied first time."
    assert xc.edit(db, note_id, better) is None and db[xc.COLLECTION].docs[0]["what"] == better and db[xc.COLLECTION].docs[0]["embedding"] == []
    assert xc.edit(db, "missing", better) == "not found"
    assert xc.delete(db, note_id) is True and xc.list_all(db) == [] and xc.delete(db, note_id) is False


def test_the_list_shows_pending_and_approved_newest_first_without_any_embedding():
    db = DB()
    first = stored_one(db)
    xc.approve(db, first, now=NOW)
    run(xc.capture(db, "answer_check", CONTEXT, model_says("It said a change was made when no tool had made it, and the file stayed as it was."), embedder({"It said a change was made when no tool had made it, and the file stayed as it was.": [0.0, 1.0, 0.0]}), now=NOW.replace(hour=16)))
    rows = xc.list_all(db)
    assert [r["status"] for r in rows] == [xc.PENDING, xc.APPROVED] and all("embedding" not in r and r["id"] for r in rows)


# ---- the excerpt a note is written from ------------------------------------------------------------------------------------------------

def test_the_excerpt_is_the_previous_request_the_last_reply_and_the_new_message():
    history = [SimpleNamespace(type="human", content="first ask"), SimpleNamespace(type="ai", content="first answer"), SimpleNamespace(type="human", content="that didn't work")]
    context = xc.context_from_history(history, question="that didn't work", reason="why")
    assert context == {"request": "first ask", "reply": "first answer", "followup": "that didn't work", "reason": "why"}
    assert xc.context_from_history([], question="hi")["request"] == ""


# ---- wired in --------------------------------------------------------------------------------------------------------------------------

def test_the_four_signals_the_untrusted_mark_and_the_admin_endpoints_are_wired_in_the_app():
    source = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    for signal in ('"pushback"', '"praise"', '"step_limit"', '"answer_check"'):
        assert signal in source
    assert "experience_capture.run_capture(" in source and "experience_capture.UNTRUSTED_MARK" in source
    for route in ('@app.get("/api/admin/experiences")', '@app.post("/api/admin/experiences/{experience_id}/approve")', '@app.patch("/api/admin/experiences/{experience_id}")', '@app.delete("/api/admin/experiences/{experience_id}")'):
        block = source[source.index(route):source.index(route) + 400]
        assert "Depends(require_global_admin)" in block, route


def test_the_counters_exist():
    from backend.utils import self_counters as sc

    assert {"experience_captured", "experience_capture_merged", "experience_capture_invalid"} <= sc.KEYS
