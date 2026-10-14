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
