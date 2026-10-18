from datetime import datetime, timezone

import pytest
from pymongo.errors import DuplicateKeyError

from backend.utils import daily_allowance as da

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_index_flag(monkeypatch):
    monkeypatch.setattr(da, "_index_ready", False)


class FakeCollection:
    """Just enough of one Mongo collection for the allowance: one document per _id, and an upsert that cannot insert a row that already exists."""

    def __init__(self):
        self.docs = {}
        self.indexes = []

    def create_index(self, key, **options):
        self.indexes.append((key, options))

    def find_one(self, query):
        return self.docs.get(query["_id"])

    def find_one_and_update(self, query, update, upsert=False, return_document=None):
        doc = self.docs.get(query["_id"])
        limit = query["count"]["$lt"]
        if doc is not None:
            if doc["count"] >= limit:
                raise DuplicateKeyError("E11000 duplicate key")       # the filter did not match, so the upsert tried to insert an existing _id
            doc["count"] += update["$inc"]["count"]
            return dict(doc)
        self.docs[query["_id"]] = {"_id": query["_id"], "count": update["$inc"]["count"], **update["$setOnInsert"]}
        return dict(self.docs[query["_id"]])

    def update_one(self, query, update):
        doc = self.docs.get(query["_id"])
        if doc is not None and doc["count"] > query["count"]["$gt"]:
            doc["count"] += update["$inc"]["count"]


class FakeDb(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]


def test_uses_are_taken_up_to_the_limit_then_refused():
    db = FakeDb()
    results = [da.take(db, "pics", "jack", 3, "2026-10-18", NOW) for _ in range(5)]
    assert results == [(True, 1), (True, 2), (True, 3), (False, 3), (False, 3)]


def test_each_person_and_each_day_has_their_own_allowance():
    db = FakeDb()
    for _ in range(3):
        da.take(db, "pics", "jack", 3, "2026-10-18", NOW)
    assert da.take(db, "pics", "jack", 3, "2026-10-18", NOW)[0] is False
    assert da.take(db, "pics", "alice", 3, "2026-10-18", NOW)[0] is True
    assert da.take(db, "pics", "jack", 3, "2026-10-19", NOW)[0] is True
    assert da.take(db, "other", "jack", 3, "2026-10-18", NOW)[0] is True


def test_a_use_that_failed_is_given_back():
    db = FakeDb()
    da.take(db, "pics", "jack", 1, "2026-10-18", NOW)
    assert da.take(db, "pics", "jack", 1, "2026-10-18", NOW)[0] is False
    da.give_back(db, "pics", "jack", "2026-10-18")
    assert da.take(db, "pics", "jack", 1, "2026-10-18", NOW)[0] is True


def test_giving_back_never_goes_below_zero():
    db = FakeDb()
    da.give_back(db, "pics", "jack", "2026-10-18")
    da.take(db, "pics", "jack", 2, "2026-10-18", NOW)
    da.give_back(db, "pics", "jack", "2026-10-18")
    da.give_back(db, "pics", "jack", "2026-10-18")
    assert da.used(db, "pics", "jack", "2026-10-18") == 0


def test_rows_expire_by_themselves():
    db = FakeDb()
    da.take(db, "pics", "jack", 3, "2026-10-18", NOW)
    assert db[da.COLLECTION].indexes and db[da.COLLECTION].indexes[0][1] == {"expireAfterSeconds": 0}
    row = next(iter(db[da.COLLECTION].docs.values()))
    assert row["expires_at"] > NOW


def test_the_stored_id_does_not_contain_the_username():
    db = FakeDb()
    da.take(db, "pics", "jack@example.com", 3, "2026-10-18", NOW)
    assert all("jack" not in key for key in db[da.COLLECTION].docs)


def test_no_database_means_allowed_and_a_zero_limit_means_none():
    assert da.take(None, "pics", "jack", 3, "2026-10-18") == (True, 0)
    assert da.take(FakeDb(), "pics", "jack", 0, "2026-10-18") == (False, 0)


def test_a_database_failure_does_not_stop_the_feature():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert da.take(Broken(), "pics", "jack", 3, "2026-10-18")[0] is True
    da.give_back(Broken(), "pics", "jack", "2026-10-18")
    assert da.used(Broken(), "pics", "jack", "2026-10-18") == 0
