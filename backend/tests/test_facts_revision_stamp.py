"""The saved fact list carries a revision stamp, so keeping the in-memory copy fresh costs a few bytes instead of the whole list (backend/utils/memory_utils.py).

Why: the list is ~2 KB a fact and was re-read in full every FACTS_CACHE_FRESH_SECONDS whether or not anything had changed. With the cap raised from 400 to 1,500 that would have been
about 3 MB every ten minutes per active person. A write now stamps the stored document; a refresh reads only the stamp and re-reads the list when it differs."""
import time

import pytest

from backend.utils import memory_utils as mu


class Facts:
    """The facts collection: one document per user, a projection honoured, every full read counted."""

    def __init__(self):
        self.doc = None
        self.full_reads = 0
        self.stamp_reads = 0

    def find_one(self, filt, projection=None):
        if projection:
            self.stamp_reads += 1
            return {k: v for k, v in (self.doc or {}).items() if k in projection} if self.doc else None
        self.full_reads += 1
        return self.doc

    def update_one(self, filt, update, upsert=False):
        self.doc = {**(self.doc or {"username": filt["username"]}), **update["$set"]}
        return type("R", (), {"modified_count": 1})()

    def delete_one(self, filt):
        self.doc = None


class Vectors:
    def __init__(self):
        self.doc = None

    def find_one(self, filt, projection=None):
        return self.doc

    def update_one(self, filt, update, upsert=False):
        self.doc = {"username": filt["username"], **update["$set"]}

    def delete_one(self, filt):
        self.doc = None


class Db(dict):
    def __init__(self):
        super().__init__()
        self["user_memory_facts"] = Facts()
        self["user_memory_fact_embeddings"] = Vectors()


def fact(fid):
    now = "2026-10-23T00:00:00+00:00"
    return mu.UserFact(id=fid, username="jack", category="preference", fact=f"fact {fid}", created_at=now, updated_at=now)


@pytest.fixture
def db(monkeypatch):
    database = Db()
    monkeypatch.setattr(mu, "get_db", lambda: database)
    mu.clear_facts_cache()
    return database


def wait_for(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_every_write_stamps_the_stored_document_with_a_new_value(db):
    mu.save_user_facts("jack", [fact("a")])
    first = db["user_memory_facts"].doc["rev"]
    mu.save_user_facts("jack", [fact("a"), fact("b")])
    assert db["user_memory_facts"].doc["rev"] > first


def test_a_stale_copy_that_is_still_current_is_confirmed_with_the_stamp_alone(db, monkeypatch):
    mu.save_user_facts("jack", [fact("a"), fact("b")])
    mu.clear_facts_cache()
    assert {f.id for f in mu.load_user_facts("jack")} == {"a", "b"}          # a cold read: the one full read
    monkeypatch.setattr(mu, "FACTS_CACHE_FRESH_SECONDS", -1.0)                # everything counts as old
    before = db["user_memory_facts"].full_reads
    mu.load_user_facts("jack")
    assert wait_for(lambda: db["user_memory_facts"].stamp_reads >= 1)
    time.sleep(0.2)
    assert db["user_memory_facts"].full_reads == before, "nothing changed, so the whole list must not be read again"


def test_a_copy_that_another_instance_replaced_is_re_read_in_full(db, monkeypatch):
    mu.save_user_facts("jack", [fact("a")])
    mu.clear_facts_cache()
    mu.load_user_facts("jack")
    # another server instance writes (its own stamp), behind this one's back
    other = db["user_memory_facts"]
    other.doc = {**other.doc, "facts": [fact("elsewhere").model_dump(exclude={"embedding"})], "rev": time.time_ns() + 5}
    monkeypatch.setattr(mu, "FACTS_CACHE_FRESH_SECONDS", -1.0)
    mu.load_user_facts("jack")                                                # served at once from memory, refreshed in the background
    assert wait_for(lambda: "elsewhere" in {f.id for f in mu.load_user_facts("jack")})


def test_a_list_stored_before_stamps_existed_is_simply_re_read_so_nothing_breaks(db, monkeypatch):
    mu.save_user_facts("jack", [fact("a")])
    stored = db["user_memory_facts"]
    stored.doc.pop("rev")                                                     # an older document: no stamp
    mu.clear_facts_cache()
    mu.load_user_facts("jack")
    monkeypatch.setattr(mu, "FACTS_CACHE_FRESH_SECONDS", -1.0)
    before = stored.full_reads
    mu.load_user_facts("jack")
    assert wait_for(lambda: stored.full_reads > before)


def test_a_database_that_cannot_take_a_projection_falls_back_to_the_full_read(db, monkeypatch):
    mu.save_user_facts("jack", [fact("a")])
    mu.clear_facts_cache()
    mu.load_user_facts("jack")
    stored = db["user_memory_facts"]

    def no_projection(filt, projection=None):
        if projection:
            raise TypeError("no projection")
        stored.full_reads += 1
        return stored.doc

    monkeypatch.setattr(stored, "find_one", no_projection)
    monkeypatch.setattr(mu, "FACTS_CACHE_FRESH_SECONDS", -1.0)
    before = stored.full_reads
    mu.load_user_facts("jack")
    assert wait_for(lambda: stored.full_reads > before)


def test_the_cap_leaves_room_now_that_a_fact_costs_about_two_kilobytes():
    assert mu.MAX_STORED_FACTS == 1500
    # 1,500 facts at ~2 KB is ~3 MB: nowhere near the 16 MB document limit (about 8,000 facts)
    assert mu.MAX_STORED_FACTS * 2048 < 16 * 1024 * 1024 // 4


def test_saving_a_fact_from_the_chat_does_not_block_the_event_loop():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[2] / "backend/services/agent_workflow.py").read_text(encoding="utf-8")
    node = source[source.index("async def memory_save_node("):source.index("def memory_recall_node(")]
    assert "await asyncio.to_thread(\n            lambda: save_user_fact(" in node
    assert "await asyncio.to_thread(\n            lambda: embed_and_store_memory_chunk(" in node
