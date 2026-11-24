"""The undoable cleanup of stored facts and notes (backend/utils/fact_cleanup.py): the original is archived before anything changes, a rewrite gets a new embedding or is
skipped, a removal drops its links, notes are only flagged, and a restore puts everything back. Uses the real UserFact and the fake database; no model, no embeddings."""
import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import fact_cleanup
from backend.utils.memory_utils import UserFact

USER = "someone"
WHEN = "2026-10-20T10:00:00+00:00"


def _fact(fid, text, category="goal", related=None):
    return UserFact(id=fid, username=USER, category=category, fact=text, source="inferred", confidence=0.8, created_at=WHEN, updated_at=WHEN,
                    embedding=[0.1, 0.2], goal_status="active" if category in ("goal", "project") else None, related=related or [])


class Store:
    def __init__(self, facts):
        self.facts = facts
        self.saves = 0

    def load(self, _username):
        return [f.model_copy(deep=True) for f in self.facts]

    def save(self, _username, facts):
        self.saves += 1
        self.facts = [f.model_copy(deep=True) for f in facts]


def _run(store, db, changes, embed=lambda text: [0.9, 0.9], run_id="run-1"):
    return fact_cleanup.apply_changes(USER, changes, run_id, db=db, load=store.load, save=store.save, embed=embed)


def test_a_rewrite_archives_the_original_first_then_changes_the_wording_embedding_and_status():
    store, db = Store([_fact("a", "Jack has a scheduled interview on October 27, 2026."), _fact("b", "Keep me.")]), FakeDB()
    result = _run(store, db, [{"id": "a", "action": "rewrite", "text": "  Jack had an interview\non October 27, 2026. ", "goal_status": "achieved"}])
    assert result == {"rewritten": 1, "archived": 0, "missing": [], "skipped": []}
    a = next(f for f in store.facts if f.id == "a")
    assert (a.fact, a.embedding, a.goal_status) == ("Jack had an interview on October 27, 2026.", [0.9, 0.9], "achieved")
    assert a.updated_at != WHEN and a.source == "inferred"
    [saved] = db["user_memory_fact_archive"].find({"cleanup_run": "run-1"})
    assert (saved["fact"], saved["embedding"], saved["goal_status"], saved["cleanup_action"]) == ("Jack has a scheduled interview on October 27, 2026.", [0.1, 0.2], "active", "rewrite")
    assert [f.fact for f in store.facts if f.id == "b"] == ["Keep me."]


def test_a_goal_status_is_only_set_on_a_goal_or_project():
    store = Store([_fact("p", "Jack lives in Iowa.", category="identity")])
    _run(store, FakeDB(), [{"id": "p", "action": "rewrite", "text": "Jack lives in Des Moines.", "goal_status": "achieved"}])
    assert store.facts[0].goal_status is None


def test_a_removal_archives_the_fact_and_takes_its_links_off_the_others():
    store, db = Store([_fact("a", "Vague.", related=["b"]), _fact("b", "Specific, Nov 16.", related=["a", "c"]), _fact("c", "Other.", related=["b"])]), FakeDB()
    result = _run(store, db, [{"id": "a", "action": "archive"}])
    assert result["archived"] == 1
    assert [f.id for f in store.facts] == ["b", "c"]
    assert store.facts[0].related == ["c"]
    assert [d["id"] for d in db["user_memory_fact_archive"].find({"cleanup_action": "archive"})] == ["a"]


def test_a_rewrite_whose_embedding_fails_is_skipped_and_nothing_is_archived_or_saved_for_it():
    store, db = Store([_fact("a", "Old words.")]), FakeDB()
    result = _run(store, db, [{"id": "a", "action": "rewrite", "text": "New words."}], embed=lambda text: None)
    assert result["skipped"] == ["a"] and result["rewritten"] == 0
    assert store.facts[0].fact == "Old words." and store.saves == 0
    assert db["user_memory_fact_archive"].find() == []


def test_an_id_that_is_not_stored_is_reported_missing_and_changes_nothing():
    store, db = Store([_fact("a", "Stays.")]), FakeDB()
    result = _run(store, db, [{"id": "zzz", "action": "archive"}])
    assert result["missing"] == ["zzz"] and store.saves == 0 and db["user_memory_fact_archive"].find() == []


@pytest.mark.parametrize("changes", [
    [{"id": "a", "action": "delete"}],
    [{"action": "archive"}],
    [{"id": "a", "action": "archive"}, {"id": "a", "action": "rewrite", "text": "x"}],
    [{"id": "a", "action": "rewrite", "text": "   "}],
    [{"id": "a", "action": "rewrite", "text": "x", "goal_status": "finished"}],
])
def test_a_malformed_change_is_refused_before_anything_is_touched(changes):
    store, db = Store([_fact("a", "Stays.")]), FakeDB()
    with pytest.raises(ValueError):
        _run(store, db, changes)
    assert store.saves == 0 and db["user_memory_fact_archive"].find() == []


def test_a_run_without_an_id_is_refused():
    with pytest.raises(ValueError):
        _run(Store([_fact("a", "x")]), FakeDB(), [{"id": "a", "action": "archive"}], run_id="")


def test_notes_are_only_flagged_stale_under_the_run_and_only_their_own():
    db = FakeDB()
    db["user_memory_chunks"].insert_one({"_id": "n1", "username": USER, "text": "wrong date"})
    db["user_memory_chunks"].insert_one({"_id": "n2", "username": "other", "text": "not theirs"})
    assert fact_cleanup.flag_notes(db, USER, ["n1", "n2"], "run-1") == 1
    assert db["user_memory_chunks"].find_one({"_id": "n1"})["stale"] is True
    assert db["user_memory_chunks"].find_one({"_id": "n1"})["text"] == "wrong date"
    assert "stale" not in db["user_memory_chunks"].find_one({"_id": "n2"})


def test_restore_puts_every_fact_of_the_run_back_as_it_was_and_unflags_its_notes():
    original = [_fact("a", "Jack has a scheduled interview.", related=["b"]), _fact("b", "Vague.", related=["a"]), _fact("c", "Untouched.")]
    store, db = Store(original), FakeDB()
    db["user_memory_chunks"].insert_one({"_id": "n1", "username": USER, "text": "wrong date"})
    db["user_memory_chunks"].insert_one({"_id": "n0", "username": USER, "text": "stale before", "stale": True, "stale_reason": "fact_superseded"})
    _run(store, db, [{"id": "a", "action": "rewrite", "text": "Jack had an interview.", "goal_status": "achieved"}, {"id": "b", "action": "archive"}])
    fact_cleanup.flag_notes(db, USER, ["n1"], "run-1")
    back = fact_cleanup.restore(USER, "run-1", db=db, load=store.load, save=store.save, make_fact=lambda fields: UserFact(**fields))
    assert back == {"facts": 2, "notes": 1}
    assert sorted((f.id, f.fact, f.goal_status, f.embedding) for f in store.facts) == sorted((f.id, f.fact, f.goal_status, f.embedding) for f in original)
    assert db["user_memory_chunks"].find_one({"_id": "n1"})["stale"] is False
    assert db["user_memory_chunks"].find_one({"_id": "n0"})["stale"] is True             # a note stale for another reason stays stale
    saves = store.saves
    assert fact_cleanup.restore(USER, "run-1", db=db, load=store.load, save=store.save, make_fact=lambda fields: UserFact(**fields))["facts"] == 0
    assert store.saves == saves                                                            # a second restore finds nothing left to put back


def test_restore_only_touches_its_own_run():
    store, db = Store([_fact("a", "One."), _fact("b", "Two.")]), FakeDB()
    _run(store, db, [{"id": "a", "action": "archive"}], run_id="run-1")
    _run(store, db, [{"id": "b", "action": "archive"}], run_id="run-2")
    fact_cleanup.restore(USER, "run-1", db=db, load=store.load, save=store.save, make_fact=lambda fields: UserFact(**fields))
    assert [f.id for f in store.facts] == ["a"]


def test_the_maintenance_command_turns_a_stored_note_id_back_into_an_object_id_and_leaves_other_ids_alone():
    from bson import ObjectId

    from backend.utils.memory_maintenance import note_id
    assert note_id("6b0425e571fd1f14cb783a0a") == ObjectId("6b0425e571fd1f14cb783a0a")
    assert note_id("n1") == "n1"


def test_reembed_missing_embeds_only_facts_without_one_and_saves_once_or_not_at_all():
    from backend.utils.memory_maintenance import reembed_missing

    store = Store([_fact("a", "Has one."), _fact("b", "Missing."), _fact("c", "Missing too.")])
    store.facts[1].embedding = None
    store.facts[2].embedding = None
    embedded = []
    result = reembed_missing(USER, load=store.load, save=store.save, embed=lambda text: embedded.append(text) or ([0.5, 0.5] if text == "Missing." else None))
    assert result == {"embedded": 1, "failed": 1} and embedded == ["Missing.", "Missing too."] and store.saves == 1
    assert [(f.id, f.embedding, f.fact) for f in store.facts] == [("a", [0.1, 0.2], "Has one."), ("b", [0.5, 0.5], "Missing."), ("c", None, "Missing too.")]
    saves = store.saves
    assert reembed_missing(USER, load=store.load, save=store.save, embed=lambda text: None) == {"embedded": 0, "failed": 1} and store.saves == saves
