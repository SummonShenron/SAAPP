"""What Sonic takes from a piece for itself, pending an admin's approval (backend/utils/shelf_reflections.py)."""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from backend.tests.test_reading_shelf import NOW, Db, stock
from backend.utils import reading_shelf as shelf
from backend.utils import shelf_reflections as sr

ROOT = pathlib.Path(__file__).resolve().parents[2]
GOOD = ("For a system like me that uses language models and tools, this is a reminder that technical limits and mistakes are a real risk. Something I should hold myself to is "
        "not claiming I searched or saved something when I did not actually run those steps in the current exchange.")


def run(coro):
    return asyncio.run(coro)


class Reply:
    def __init__(self, content):
        self.content = content


def invoke_with(text):
    async def invoke(prompt):
        invoke.prompts.append(prompt)
        if isinstance(text, Exception):
            raise text
        return Reply(text)

    invoke.prompts = []
    return invoke


def item_in(db, **kw):
    return stock(db, url=kw.pop("url", "https://blog.example.com/winter"), topic="ai_research", title="AI Winter", summary="Cycles of inflated expectations and funding collapses in AI.", **kw)


# ---- the fixed checks ------------------------------------------------------------------------------------------------------------------------------

def test_a_reflection_about_the_system_itself_passes():
    assert sr.reflection_issue(GOOD) is None


@pytest.mark.parametrize("text,fragment", [
    ("Too short.", "words"),
    ("word " * 120, "words"),
    ("For a system like me this is a long enough sentence about myself and my tools, see https://example.com for more of it please.", "plain passage"),
    ("For a system like me this is a reminder that there were 3 winters and I should hold myself to checking my tools before I claim anything.", "number"),
    ("This is a long enough sentence about the history of the field and how funding collapsed after the early booms in research.", "about itself"),
    ("For a system like me this is a reminder, and you should check what I say before you rely on it in your own work.", "addresses someone"),
    ("For a system like me this is a reminder, and I felt it deeply, hoping to do better because I want to be trusted by people.", "feeling"),
    ("For a system like me this is a reminder that I should hold myself to checking my tools, and I thought about it for days since then.", "time spent"),
])
def test_a_reflection_that_breaks_a_rule_is_refused_and_says_which(text, fragment):
    issue = sr.reflection_issue(text)
    assert issue and fragment in issue, issue


def test_nothing_means_the_piece_has_nothing_for_a_system_like_sonic():
    assert sr.parse(Reply("NOTHING")) is None and sr.parse("nothing.") is None and sr.parse("") is None and sr.parse(Reply(f'"{GOOD}"')) == GOOD


def test_the_prompt_has_the_true_facts_the_summary_and_the_rules_and_no_conversation():
    prompt = sr.build_prompt({"title": "AI Winter", "summary": "Cycles of inflated expectations."})
    for expected in ("TRUE ABOUT YOU", "Title: AI Winter", "Summary: Cycles of inflated expectations.", "for a system like me", "Nothing about feelings", "reply with exactly NOTHING", "A forced link is worse than none", "at most 70 words"):
        assert expected in prompt, expected
    assert "\\n" not in prompt                                                                                         # real line breaks, not the two characters


def test_only_pieces_about_ai_and_software_get_a_reflection_and_the_others_cost_no_model_call():
    asked = invoke_with(GOOD)
    for topic in ("game_design", "game_lore", "tabletop", "scifi", "", None):
        assert run(sr.generate(asked, {"title": "Boss battles", "summary": "x", "topic": topic})) == {"status": "none"}
    assert asked.prompts == []
    assert set(sr.REFLECT_TOPICS) <= set(shelf.TOPIC_IDS) and "game_design" not in sr.REFLECT_TOPICS


# ---- writing them ----------------------------------------------------------------------------------------------------------------------------------

def test_a_good_reflection_is_pending_nothing_is_none_a_bad_one_is_none_and_a_failed_call_is_an_error():
    item = {"title": "AI Winter", "summary": "x", "topic": "ai_research"}
    assert run(sr.generate(invoke_with(GOOD), item)) == {"status": "pending", "text": GOOD}
    assert run(sr.generate(invoke_with("NOTHING"), item)) == {"status": "none"}
    assert run(sr.generate(invoke_with("I felt inspired and hope to do better because I want it so much."), item)) == {"status": "none"}
    assert run(sr.generate(invoke_with(RuntimeError("down")), item)) == {"status": "error"}


def test_a_stored_reflection_is_not_overwritten_and_a_missing_one_is_filled_a_few_at_a_time():
    db = Db()
    a = item_in(db, url="https://blog.example.com/a")
    b = item_in(db, url="https://blog.example.com/b")
    c = item_in(db, url="https://blog.example.com/c")
    assert sr.store_generated(db, shelf.COLLECTION, a["_id"], {"status": "pending", "text": GOOD}) is True
    assert sr.store_generated(db, shelf.COLLECTION, a["_id"], {"status": "pending", "text": "A different one that should not replace it."}) is False
    assert db[shelf.COLLECTION].find_one({"_id": a["_id"]})["reflection"]["text"] == GOOD
    out = run(sr.write_missing(db, shelf.COLLECTION, shelf.live_items(db, NOW), invoke_with(GOOD), limit=1))
    assert out == {"pending": 1, "nothing": 0, "errors": 0}
    states = {i["_id"]: sr.state_of(i)["status"] for i in shelf.live_items(db, NOW)}
    assert sorted(states.values()) == ["", "pending", "pending"]
    nothing = run(sr.write_missing(db, shelf.COLLECTION, shelf.live_items(db, NOW), invoke_with("NOTHING")))
    assert nothing == {"pending": 0, "nothing": 1, "errors": 0}
    assert run(sr.write_missing(db, shelf.COLLECTION, shelf.live_items(db, NOW), invoke_with(GOOD))) == {"pending": 0, "nothing": 0, "errors": 0}     # every item has a state now


# ---- approval --------------------------------------------------------------------------------------------------------------------------------------

def pending_item(db):
    item = item_in(db)
    sr.store_generated(db, shelf.COLLECTION, item["_id"], {"status": "pending", "text": GOOD})
    return item["_id"]


def current(db, key):
    return db[shelf.COLLECTION].find_one({"_id": key})


def test_only_an_approved_reflection_is_ever_said_and_approving_records_who_and_when():
    db = Db()
    key = pending_item(db)
    assert sr.approved_text(current(db, key)) == ""                                          # pending: not said
    assert sr.decide(db, shelf.COLLECTION, key, "approve", "jack@example.com", now=NOW) == {"ok": True}
    doc = current(db, key)
    assert sr.approved_text(doc) == GOOD and doc["reflection"]["approved_by"] == "jack@example.com" and doc["reflection"]["approved_at"]
    assert sr.decide(db, shelf.COLLECTION, key, "remove", "jack@example.com")["ok"] is True
    assert sr.approved_text(current(db, key)) == "" and sr.state_of(current(db, key))["status"] == "removed"


def test_an_edited_wording_is_checked_like_a_generated_one_and_an_edit_of_an_approved_one_goes_back_to_pending():
    db = Db()
    key = pending_item(db)
    refused = sr.decide(db, shelf.COLLECTION, key, "approve", "jack", text="I felt this deeply and hope to do better because I want to be trusted by people.")
    assert refused["ok"] is False and "feeling" in refused["problem"] and sr.state_of(current(db, key))["status"] == "pending"
    better = "For a system like me this is a reminder that I should hold myself to checking my tools before I say I did something, and to admitting when a result is missing."
    assert sr.decide(db, shelf.COLLECTION, key, "approve", "jack", text=better)["ok"] is True and sr.approved_text(current(db, key)) == better
    assert sr.decide(db, shelf.COLLECTION, key, "edit", "jack", text=GOOD)["ok"] is True
    assert sr.state_of(current(db, key)) == {"status": "pending", "text": GOOD}               # changed after approval: it needs approving again


def test_there_is_nothing_to_decide_on_a_missing_none_or_removed_reflection_or_a_removed_item():
    db = Db()
    key = item_in(db)["_id"]
    assert sr.decide(db, shelf.COLLECTION, key, "approve", "jack")["ok"] is False                 # no reflection yet
    sr.store_generated(db, shelf.COLLECTION, key, {"status": "none"})
    assert sr.decide(db, shelf.COLLECTION, key, "approve", "jack")["ok"] is False
    assert sr.decide(db, shelf.COLLECTION, "no-such-key", "approve", "jack")["problem"] == "No such item."
    assert sr.decide(db, shelf.COLLECTION, pending_item(Db()), "bogus", "jack")["ok"] is False


# ---- where an approved one is used and where a pending one never is ------------------------------------------------------------------------------------------------

def test_the_shelf_block_and_the_listing_carry_an_approved_takeaway_and_never_a_pending_one():
    db = Db()
    key = pending_item(db)
    pending_doc = current(db, key)
    for block in (shelf.build_shelf_block({"kind": "topic", "item": pending_doc}), shelf.build_listing_block({"kind": "listing", "items": [pending_doc]})):
        assert GOOD not in block and "Your approved takeaway" not in block
    sr.decide(db, shelf.COLLECTION, key, "approve", "jack")
    approved_doc = current(db, key)
    block = shelf.build_shelf_block({"kind": "finding", "item": approved_doc, "score": 0.7})
    assert GOOD in block and "It is the one conclusion you may add" in block and "do not say it moved or changed you" in block
    listing = shelf.build_listing_block({"kind": "listing", "items": [approved_doc]})
    assert f"Your approved takeaway: {GOOD}" in listing and "say it in those terms if they ask what you took from it" in listing.replace("\n", " ")


def test_the_public_page_gets_only_approved_takeaways_and_the_admin_view_counts_pending_and_missing_ones():
    db = Db()
    approved_key = pending_item(db)
    sr.decide(db, shelf.COLLECTION, approved_key, "approve", "jack")
    pending = item_in(db, url="https://blog.example.com/p")
    sr.store_generated(db, shelf.COLLECTION, pending["_id"], {"status": "pending", "text": GOOD})
    item_in(db, url="https://blog.example.com/m")                                              # no reflection yet
    public = {i["url"]: i["reflection"] for i in shelf.public_view(db, NOW)["items"]}
    assert list(public.values()).count(GOOD) == 1 and list(public.values()).count("") == 2
    view = shelf.admin_view(db, NOW)
    assert view["reflections_pending"] == 1 and view["reflections_missing"] == 1
    assert {i["key"]: i["reflection"]["status"] for i in view["items"]}[approved_key] == "approved"


def test_a_tombstone_keeps_no_reflection_and_no_embedding():
    assert "reflection" in shelf._TOMBSTONE_DROPS and "embedding" in shelf._TOMBSTONE_DROPS


# ---- the wiring ----------------------------------------------------------------------------------------------------------------------------------

def test_a_reflection_is_written_when_a_piece_is_read_and_the_admin_endpoints_and_screen_exist():
    refresh = (ROOT / "backend" / "services" / "shelf_refresh.py").read_text(encoding="utf-8")
    assert "shelf_reflections.generate(_reflection_invoke or invoke, doc)" in refresh and "def set_reflection_model" in refresh and "shelf_reflections.store_generated" in refresh
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "shelf_refresh.set_reflection_model(shelf_reflection_llm.ainvoke)" in app_source and "write_missing(db, shelf_utils.COLLECTION, items, shelf_reflection_llm.ainvoke)" in app_source
    for route in ('@app.post("/api/admin/reading-shelf/reflect")', '@app.post("/api/admin/reading-shelf/{key}/reflection")'):
        block = app_source[app_source.index(route):app_source.index(route) + 900]
        assert "Depends(require_global_admin)" in block, route
    screen = (ROOT / "local" / "src" / "pages" / "SonicHealthShelf.tsx").read_text(encoding="utf-8")
    assert "pending: not said to anyone until you approve it" in screen and "Approve this wording" in screen and "Write takeaways" in screen
    page = (ROOT / "local" / "src" / "pages" / "Reading.tsx").read_text(encoding="utf-8")
    assert "What I take from it" in page


def test_the_module_logs_only_fixed_words():
    import ast
    tree = ast.parse((ROOT / "backend" / "utils" / "shelf_reflections.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1]) and len(node.args) <= 2
