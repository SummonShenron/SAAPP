"""Voice review (backend/utils/voice_review.py, the /api/admin/voice-review endpoints): a person's approvals of the voice examples and situation facts, kept in the database on top of the registries' own settings, with how often each was used.

Why (2026-11-13): approving meant editing a Python tuple and nothing showed whether an approved item was ever matched. No model is called; the database is the repo's fake and the embedder a stand-in."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.components import sonic_situation_facts as fact_registry
from backend.components import sonic_voice_examples as example_registry
from backend.tests.fake_runner_db import FakeDB
from backend.utils import self_counters as sc
from backend.utils import situation_facts as sf
from backend.utils import voice_examples as ve
from backend.utils import voice_review as vr

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
EXAMPLE_ID = "big_win_energy"                                     # a shipped example, approved in the registry
DRAFT_FACT = fact_registry.FACTS[0]["id"]                         # a shipped fact: a draft until a person approves it


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    vr.forget_cached()
    ve._cache.update({"at": 0.0, "vectors": {}})
    sf._cache.update({"at": 0.0, "vectors": {}})
    yield
    vr.forget_cached()


def counters(db, **days):
    for day, counts in days.items():
        db[sc.COUNTERS_COLLECTION].insert_one({"_id": day, "day": day, "counts": counts})
    return db


def test_the_registry_decides_until_a_person_does_and_a_decision_wins():
    example = next(e for e in example_registry.EXAMPLES if e["id"] == EXAMPLE_ID)
    draft = fact_registry.FACTS[0]
    assert example["approved"] is True and draft["approved"] is False
    assert vr.effective_approved("example", example, {}) is True and vr.effective_approved("fact", draft, {}) is False
    assert vr.effective_approved("example", example, {("example", EXAMPLE_ID): "removed"}) is False
    assert vr.effective_approved("fact", draft, {("fact", draft["id"]): "approved"}) is True
    assert vr.effective_approved("example", example, {("fact", EXAMPLE_ID): "removed"}) is True              # a decision on the other kind does not apply
    assert vr.effective_approved("fact", {"id": "x"}, {}) is False


def test_apply_changes_only_what_was_decided_and_never_the_registry_itself():
    entries = [{"id": "a", "approved": True}, {"id": "b", "approved": False}, {"id": "c", "approved": True}]
    assert vr.apply(entries, "example", {}) is entries                                                         # nothing decided: the same list
    changed = vr.apply(entries, "example", {("example", "a"): "removed", ("example", "b"): "approved"})
    assert [e["approved"] for e in changed] == [False, True, True] and changed[2] is entries[2]
    assert [e["approved"] for e in entries] == [True, False, True]                                             # the input was not touched
    before = [e["approved"] for e in example_registry.EXAMPLES]
    vr.apply(example_registry.EXAMPLES, "example", {("example", EXAMPLE_ID): "removed"})
    assert [e["approved"] for e in example_registry.EXAMPLES] == before


def test_a_decision_is_stored_for_a_known_item_only_and_reset_goes_back_to_the_registry():
    db = FakeDB()
    assert vr.set_state(db, "example", EXAMPLE_ID, "removed", "jack@example.com", NOW) is True
    assert db[vr.COLLECTION].find_one({"_id": f"example:{EXAMPLE_ID}"})["state"] == "removed"
    assert vr.set_state(db, "example", "no_such_example", "removed") is False and vr.set_state(db, "nonsense", EXAMPLE_ID, "removed") is False
    assert vr.set_state(db, "example", EXAMPLE_ID, "maybe") is False and vr.set_state(None, "example", EXAMPLE_ID, "removed") is False
    assert vr.overrides(db) == {("example", EXAMPLE_ID): "removed"}
    assert vr.set_state(db, "example", EXAMPLE_ID, "approved", "jack@example.com", NOW) is True and vr.overrides(db) == {("example", EXAMPLE_ID): "approved"}      # one document per item
    assert len(db[vr.COLLECTION].find({})) == 1
    assert vr.reset(db, "example", EXAMPLE_ID) is True and vr.overrides(db) == {} and vr.reset(db, "example", "no_such_example") is False


def test_the_overrides_are_cached_a_failed_read_keeps_the_last_answer_and_no_database_means_the_registry():
    db = FakeDB()
    vr.set_state(db, "fact", DRAFT_FACT, "approved")
    assert vr.overrides(db, now=1000.0) == {("fact", DRAFT_FACT): "approved"}
    db[vr.COLLECTION].insert_one({"_id": "example:" + EXAMPLE_ID, "state": "removed"})                          # written behind the cache's back
    assert ("example", EXAMPLE_ID) not in vr.overrides(db, now=1000.0 + vr.OVERRIDE_TTL_SECONDS - 1)         # still the cached answer
    assert vr.overrides(db, now=1000.0 + vr.OVERRIDE_TTL_SECONDS + 1)[("example", EXAMPLE_ID)] == "removed"    # re-read after the TTL

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("database down")

    assert vr.overrides(Broken(), now=5000.0) == {("example", EXAMPLE_ID): "removed", ("fact", DRAFT_FACT): "approved"}       # the last good answer
    vr.forget_cached()
    assert vr.overrides(Broken(), now=9000.0) == {} and vr.overrides(None) == {}
    db[vr.COLLECTION].insert_one({"_id": "garbage", "state": "removed"})
    db[vr.COLLECTION].insert_one({"_id": "example:x", "state": "weird"})
    vr.forget_cached()
    assert set(vr.overrides(db)) == {("example", EXAMPLE_ID), ("fact", DRAFT_FACT)}                            # unknown kinds and states are ignored


def test_the_view_lists_every_item_with_what_it_says_whether_it_may_be_used_and_how_often_it_was():
    db = counters(FakeDB(), **{
        "2026-11-12": {f"voice_example_used_{EXAMPLE_ID}": 3, f"situation_fact_used_{DRAFT_FACT}": 1},
        "2026-11-01": {f"voice_example_used_{EXAMPLE_ID}": 2},
        "2026-09-01": {f"voice_example_used_{EXAMPLE_ID}": 50},                                           # outside the 30 days
    })
    vr.set_state(db, "fact", DRAFT_FACT, "approved", "jack@example.com", NOW)
    shown = vr.view(db, NOW)
    assert shown["days"] == 30 and len(shown["items"]) == len(example_registry.EXAMPLES) + len(fact_registry.FACTS)
    example = next(i for i in shown["items"] if i["kind"] == "example" and i["id"] == EXAMPLE_ID)
    assert example["approved"] is True and example["default"] is True and example["decision"] is None and example["used"] == 5
    assert example["text"] and example["tone"] == "light" and len(example["asks"]) == 1                      # the reply, the tone and the ONE message shown beside it
    fact = next(i for i in shown["items"] if i["kind"] == "fact" and i["id"] == DRAFT_FACT)
    assert fact["approved"] is True and fact["default"] is False and fact["decision"] == "approved" and fact["used"] == 1
    assert fact["text"] == fact_registry.FACTS[0]["facts"] and fact["tone"] == ""
    assert vr.view(None, NOW)["items"][0]["used"] == 0 and vr.view(None, NOW)["items"][0]["decision"] is None      # no database: the registry's settings, no counts


def test_a_removed_example_is_never_chosen_and_an_approved_draft_is():
    def embed(text):
        return [1.0, 0.0, 0.0] + [0.0] * 51 if "shipped" in text.lower() else [0.0, 0.0, 0.0] + [1.0] + [0.0] * 50

    example = {**next(e for e in example_registry.EXAMPLES if e["id"] == EXAMPLE_ID), "asks": ["we shipped it!! it works", "shipped it", "we shipped the thing"], "approved": True}
    ve.leanings.index_vectors(None, embed, [ve.view(example)], fill=True, collection=ve.COLLECTION, cache=ve._cache)
    db = FakeDB()
    pick = lambda: [e["id"] for e in ve.select("we shipped it!! the whole thing works", db, embed, examples=[example])]
    assert pick() == [EXAMPLE_ID]
    vr.set_state(db, "example", EXAMPLE_ID, "removed")
    assert pick() == []                                                                                       # removed on the dashboard
    vr.reset(db, "example", EXAMPLE_ID)
    draft = {**example, "approved": False}
    assert [e["id"] for e in ve.select("we shipped it!! the whole thing works", db, embed, examples=[draft])] == []
    vr.set_state(db, "example", EXAMPLE_ID, "approved")
    assert [e["id"] for e in ve.select("we shipped it!! the whole thing works", db, embed, examples=[draft])] == [EXAMPLE_ID]      # approved on the dashboard


def test_a_situation_fact_a_person_approved_is_chosen_for_a_hurting_person_and_a_removed_one_is_not():
    def embed(text):
        return [1.0, 0.0, 0.0] + [0.0] * 51 if "interview" in text.lower() else [0.0, 0.0, 0.0] + [1.0] + [0.0] * 50

    draft = {**fact_registry.FACTS[0], "asks": ["the interview went badly", "bombed my interview", "interview disaster"], "approved": False}
    sf.leanings.index_vectors(None, embed, [sf.view({**draft, "approved": True})], fill=True, collection=sf.COLLECTION, cache=sf._cache)
    db = FakeDB()
    ask = lambda: [e["id"] for e in sf.select("my interview went badly today", db, embed, entries=[draft], emotional_active=True)]
    assert ask() == []                                                                                         # a draft is never used
    vr.set_state(db, "fact", draft["id"], "approved")
    assert ask() == [draft["id"]]
    vr.set_state(db, "fact", draft["id"], "removed")
    assert ask() == []


def test_the_endpoints_are_admin_only_and_decide_one_item_at_a_time():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    get = source[source.index('@app.get("/api/admin/voice-review")'):]
    get = get[:get.index("@app.post")]
    assert "Depends(require_global_admin)" in get and "voice_review.view, _admin_db()" in get
    post = source[source.index('@app.post("/api/admin/voice-review/{kind}/{entry_id}/{action}")'):]
    post = post[:post.index("class ReflectionDecision")]
    assert "Depends(require_global_admin)" in post and 'action == "reset"' in post and 'action in ("approve", "remove")' in post
    assert 'status_code=404, detail="No such action."' in post and 'status_code=404, detail="No such example or fact."' in post
    assert "voice_review.set_state, db, kind, entry_id" in post and 'admin.get("sub")' in post
