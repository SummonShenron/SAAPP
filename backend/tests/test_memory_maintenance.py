import pytest

from backend.utils import memory_maintenance as mm


def rows():
    return [
        {"_id": 1, "text": "SUMMARY: Adopted a dog named Biscuit.", "source_type": "conversation_turn"},
        {"_id": 2, "text": "Adopted a dog named Biscuit.", "source_type": "conversation_turn"},
        {"_id": 3, "text": "**Summary:** Planning a trip.", "source_type": "compacted_summary"},
        {"_id": 4, "text": "SUMMARY:", "source_type": "conversation_turn"},
        {"_id": 5, "text": "Note that the rent is going up.", "source_type": "manual"},
    ]


def test_only_notes_that_start_with_a_label_and_have_text_left_are_planned():
    plan = mm.plan_label_cleanup(rows())
    assert [(c["_id"], c["text"]) for c in plan] == [(1, "Adopted a dog named Biscuit."), (3, "Planning a trip.")]
    assert [c["source_type"] for c in plan] == ["conversation_turn", "compacted_summary"]


class Collection:
    def __init__(self):
        self.updates = []

    def update_one(self, filt, update):
        self.updates.append((filt, update))


def test_applying_writes_the_cleaned_text_and_a_fresh_embedding_and_nothing_else():
    collection = Collection()
    result = mm.apply_label_cleanup(collection, mm.plan_label_cleanup(rows()), lambda text: [float(len(text))])
    assert result == {"changed": 2, "skipped": 0}
    assert collection.updates[0] == ({"_id": 1}, {"$set": {"text": "Adopted a dog named Biscuit.", "embedding": [28.0]}})
    assert set(collection.updates[0][1]["$set"]) == {"text", "embedding"}


def test_a_note_whose_embedding_fails_is_left_untouched_not_half_changed():
    collection = Collection()
    result = mm.apply_label_cleanup(collection, mm.plan_label_cleanup(rows()), lambda text: None if "trip" in text else [1.0])
    assert result == {"changed": 1, "skipped": 1}
    assert [filt for filt, _ in collection.updates] == [{"_id": 1}]


def test_nothing_is_planned_when_no_note_has_a_label():
    assert mm.plan_label_cleanup([{"_id": 1, "text": "Plain note."}]) == []


# ---- undoing a compaction run ----------------------------------------------------------------------------------------------

import re
from types import SimpleNamespace

from backend.services import memory_compaction as mc


class Coll:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    @staticmethod
    def _match(doc, filt):
        for key, cond in filt.items():
            if isinstance(cond, dict) and "$exists" in cond:
                if (key in doc) != cond["$exists"]:
                    return False
            elif isinstance(cond, dict) and "$regex" in cond:
                if not re.search(cond["$regex"], str(doc.get(key) or "")):
                    return False
            elif doc.get(key) != cond:
                return False
        return True

    def find(self, filt, projection=None):
        return [d for d in self.docs if self._match(d, filt)]

    def update_one(self, filt, update, upsert=False):
        hit = self.find(filt)
        if hit:
            hit[0].update(update.get("$set", {}))
        elif upsert:
            self.docs.append({**filt, **update.get("$setOnInsert", {})})

    def update_many(self, filt, update):
        for d in self.find(filt):
            d.update(update.get("$set", {}))

    def delete_many(self, filt):
        gone = self.find(filt)
        for d in gone:
            self.docs.remove(d)
        return SimpleNamespace(deleted_count=len(gone))


class DB:
    def __init__(self):
        self.c = {}

    def __getitem__(self, name):
        return self.c.setdefault(name, Coll())


def _archived(run="r1", cluster="c1", n=2, at="2026-10-13T10:00:00+00:00", tier=2, restored=False):
    docs = []
    for i in range(n):
        doc = {"_id": f"{cluster}-{i}", "username": "jack", "text": f"note {i}", "embedding": [float(i)], "source_type": "compacted_summary",
               "source_ref": "sess", "created_at": "2026-07-01T10:00:00+00:00", "archived_at": at, "compaction_tier": tier, "run_id": run, "cluster_id": cluster}
        if restored:
            doc["restored_at"] = "2026-10-14T00:00:00+00:00"
        docs.append(doc)
    return docs


def test_the_archive_name_matches_the_one_compaction_writes_to():
    assert mm.ARCHIVE_COLLECTION == mc.ARCHIVE_COLLECTION


def test_runs_are_listed_newest_first_with_their_note_and_group_counts():
    docs = _archived("old", "c1", 2, "2026-10-12T10:00:00+00:00") + _archived("new", "c2", 3, "2026-10-13T10:00:00+00:00") + _archived("new", "c3", 2, "2026-10-13T10:00:00+00:00")
    runs = mm.list_runs(docs)
    assert [r["run_id"] for r in runs] == ["new", "old"]
    assert (runs[0]["notes"], runs[0]["clusters"], runs[0]["tier"]) == (5, 2, 2)


def test_restoring_a_run_puts_the_notes_back_with_their_ids_and_dates_removes_the_summary_and_marks_the_archive():
    db = DB()
    db["user_memory_chunk_archive"].docs = _archived()
    db["user_memory_chunks"].docs = [
        {"_id": "summary", "username": "jack", "text": "Merged.", "source_ref": "compacted:2 chunks (sess) #c1"},
        {"_id": "other", "username": "jack", "text": "Untouched.", "source_ref": "compacted:2 chunks (sess) #zzz"},
    ]
    result = mm.restore_run(db, "r1", "2026-10-14T00:00:00+00:00")
    assert result == {"restored": 2, "summaries_removed": 1, "summaries_missing": 0}
    live = {d["_id"]: d for d in db["user_memory_chunks"].docs}
    assert set(live) == {"other", "c1-0", "c1-1"}
    assert live["c1-0"]["text"] == "note 0" and live["c1-0"]["embedding"] == [0.0] and live["c1-0"]["created_at"] == "2026-07-01T10:00:00+00:00"
    assert not set(live["c1-0"]) & set(mm.ARCHIVE_ONLY_FIELDS)
    assert all(d.get("restored_at") for d in db["user_memory_chunk_archive"].docs)


def test_restoring_twice_does_nothing_the_second_time():
    db = DB()
    db["user_memory_chunk_archive"].docs = _archived()
    mm.restore_run(db, "r1", "2026-10-14T00:00:00+00:00")
    again = mm.restore_run(db, "r1", "2026-10-15T00:00:00+00:00")
    assert again == {"restored": 0, "summaries_removed": 0, "summaries_missing": 0}
    assert len(db["user_memory_chunks"].docs) == 2


def test_a_summary_that_a_later_run_already_merged_is_reported_missing_but_the_notes_still_come_back():
    db = DB()
    db["user_memory_chunk_archive"].docs = _archived()
    result = mm.restore_run(db, "r1", "2026-10-14T00:00:00+00:00")
    assert result == {"restored": 2, "summaries_removed": 0, "summaries_missing": 1}


# ---- bringing back facts that pruning evicted --------------------------------------------------------------------------------------

from backend.utils import memory_utils as mu


def vec_sim(a, b):
    """Cosine for the tiny test vectors."""
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def live_fact(fact_id, text, embedding=None, source="explicit"):
    return mu.UserFact(id=fact_id, username="jack", category="preference", fact=text, source=source, confidence=1.0,
                       created_at="2026-09-01T00:00:00+00:00", updated_at="2026-09-01T00:00:00+00:00", embedding=embedding)


def archived_fact(fact_id, text, embedding, updated="2026-09-10T00:00:00+00:00", source="explicit", **extra):
    return {"_id": f"arch-{fact_id}", "id": fact_id, "username": "jack", "category": "preference", "fact": text, "source": source,
            "confidence": 1.0, "created_at": "2026-09-01T00:00:00+00:00", "updated_at": updated, "active": True, "embedding": embedding,
            "goal_status": None, "last_nudged_at": None, "archived_at": "2026-10-01T00:00:00+00:00", **extra}


def ids(plan):
    return [doc["id"] for doc, _ in plan["restore"]]


def test_an_archived_fact_comes_back_unless_it_is_already_live_or_the_same_as_a_live_fact():
    live = [live_fact("L1", "Lives in Denver", [1.0, 0.0, 0.0])]
    archive = [
        archived_fact("A", "Plays cello", [0.0, 1.0, 0.0]),
        archived_fact("B", "Resides in Denver, Colorado", [0.97, 0.05, 0.0]),  # a near-duplicate of a live fact
        archived_fact("L1", "Lives in Denver", [1.0, 0.0, 0.0]),  # the same id is live already
        archived_fact("C", "lives in denver.", None),  # no embedding, but the same words as a live fact
    ]
    plan = mm.plan_fact_restore(archive, live, 0.80, vec_sim)
    assert ids(plan) == ["A"]
    assert plan["skipped"] == {"already live": 1, "same as a live fact": 2, "same as another restored fact": 0}


def test_two_archived_versions_of_the_same_fact_restore_only_the_newest():
    archive = [
        archived_fact("old", "Works at Acme", [1.0, 0.0, 0.0], updated="2026-08-01T00:00:00+00:00"),
        archived_fact("new", "Works at Acme as an engineer", [0.98, 0.1, 0.0], updated="2026-09-15T00:00:00+00:00"),
        archived_fact("other", "Has a dog", [0.0, 0.0, 1.0]),
    ]
    plan = mm.plan_fact_restore(archive, [], 0.80, vec_sim)
    assert sorted(ids(plan)) == ["new", "other"] and plan["skipped"]["same as another restored fact"] == 1


def test_a_fact_with_no_embedding_and_new_words_is_restored():
    plan = mm.plan_fact_restore([archived_fact("N", "Has a dog", None)], [live_fact("L", "Lives in Denver", [1.0, 0.0, 0.0])], 0.80, vec_sim)
    assert ids(plan) == ["N"]


class ArchiveColl:
    def __init__(self, docs):
        self.docs = docs

    def find(self, filt, projection=None):
        out = []
        for d in self.docs:
            if d.get("username") != filt["username"] or d.get("source") != filt["source"]:
                continue
            if "restored_at" in filt and (("restored_at" in d) != filt["restored_at"]["$exists"]):
                continue
            out.append(d)
        return out

    def update_many(self, filt, update):
        for d in self.docs:
            if d["_id"] in filt["_id"]["$in"]:
                d.update(update["$set"])


@pytest.fixture
def fact_store(monkeypatch):
    state = SimpleNamespace(live=[live_fact("L1", "Lives in Denver", [1.0, 0.0, 0.0]), live_fact("M1", "Model-inferred thing", [0.0, 0.0, 1.0], source="inferred")],
                            archived_by_prune=[])
    monkeypatch.setattr(mu, "load_user_facts", lambda username: list(state.live))
    monkeypatch.setattr(mu, "save_user_facts", lambda username, facts: setattr(state, "live", list(facts)))
    monkeypatch.setattr(mu, "_archive_dropped_facts", lambda dropped: state.archived_by_prune.extend(dropped))
    return state


def test_restoring_brings_the_facts_back_marks_them_and_lets_the_normal_pruning_evict_the_inferred_ones(monkeypatch, fact_store):
    monkeypatch.setattr(mu, "MAX_STORED_FACTS", 3)
    db = DB()
    db[mu.FACT_ARCHIVE_COLLECTION].docs = []
    arch = ArchiveColl([archived_fact("A", "Plays cello", [0.0, 1.0, 0.0]), archived_fact("B", "Has a dog", [0.5, 0.5, 0.5])])
    db.c[mu.FACT_ARCHIVE_COLLECTION] = arch

    result = mm.restore_pruned_facts(db, "jack", "explicit", "2026-10-14T00:00:00+00:00")

    assert result["restored"] == 2 and result["evicted_to_stay_under_cap"] == 1
    kept = {f.id: f for f in fact_store.live}
    assert set(kept) == {"L1", "A", "B"}  # the inferred fact made room
    assert [f.id for f in fact_store.archived_by_prune] == ["M1"]  # and was archived, not lost
    assert kept["A"].fact == "Plays cello" and kept["A"].created_at == "2026-09-01T00:00:00+00:00" and kept["A"].source == "explicit"
    assert all(d.get("restored_at") for d in arch.docs)


def test_restoring_twice_does_nothing_the_second_time_and_only_the_asked_source_is_touched(monkeypatch, fact_store):
    monkeypatch.setattr(mu, "MAX_STORED_FACTS", 400)
    db = DB()
    db.c[mu.FACT_ARCHIVE_COLLECTION] = ArchiveColl([archived_fact("A", "Plays cello", [0.0, 1.0, 0.0]), archived_fact("X", "Inferred thing two", [0.3, 0.3, 0.9], source="inferred")])
    first = mm.restore_pruned_facts(db, "jack", "explicit", "2026-10-14T00:00:00+00:00")
    second = mm.restore_pruned_facts(db, "jack", "explicit", "2026-10-15T00:00:00+00:00")
    assert first["restored"] == 1 and second["restored"] == 0
    assert "X" not in {f.id for f in fact_store.live}  # the inferred archive row was not asked for
    assert len([f for f in fact_store.live if f.id == "A"]) == 1


def test_the_dry_run_lists_counts_and_categories_but_not_the_text_unless_asked(monkeypatch, fact_store, capsys):
    db = DB()
    db.c[mu.FACT_ARCHIVE_COLLECTION] = ArchiveColl([archived_fact("A", "Plays the cello on Wednesdays", [0.0, 1.0, 0.0])])
    assert mm._restore_facts_command(db, "jack", "explicit", False, False) == 0
    quiet = capsys.readouterr().out
    assert "1 would come back" in quiet and "[preference]" in quiet and "cello" not in quiet and "Dry run" in quiet
    mm._restore_facts_command(db, "jack", "explicit", True, False)
    assert "cello" in capsys.readouterr().out
    assert [f.id for f in fact_store.live] == ["L1", "M1"]  # nothing was written


def test_the_fact_archive_name_is_the_one_pruning_writes_to():
    assert mu.FACT_ARCHIVE_COLLECTION == "user_memory_fact_archive"
    assert "restored_at" in mm.FACT_ARCHIVE_ONLY_FIELDS and "_id" in mm.FACT_ARCHIVE_ONLY_FIELDS
