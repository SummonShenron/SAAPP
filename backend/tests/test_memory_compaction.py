import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.services import memory_compaction as mc


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# _cluster_chunks — pure function, no DB/LLM involved
# ---------------------------------------------------------------------------

def _chunk(text, embedding, source_ref=None, _id=None):
    return {"_id": _id or text, "text": text, "embedding": embedding, "source_ref": source_ref}


def test_cluster_chunks_groups_similar_embeddings_together():
    chunks = [
        _chunk("a", [1.0, 0.0, 0.0]),
        _chunk("b", [0.99, 0.01, 0.0]),
        _chunk("c", [0.98, 0.02, 0.0]),
    ]
    clusters = mc._cluster_chunks(chunks, threshold=0.9)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_cluster_chunks_keeps_dissimilar_chunks_as_singletons():
    chunks = [
        _chunk("a", [1.0, 0.0, 0.0]),
        _chunk("b", [0.0, 1.0, 0.0]),
        _chunk("c", [0.0, 0.0, 1.0]),
    ]
    clusters = mc._cluster_chunks(chunks, threshold=0.9)
    assert len(clusters) == 3
    assert all(len(c) == 1 for c in clusters)


def test_cluster_chunks_mixed_grouping():
    chunks = [
        _chunk("a1", [1.0, 0.0, 0.0]),
        _chunk("a2", [0.99, 0.01, 0.0]),
        _chunk("b1", [0.0, 1.0, 0.0]),
    ]
    clusters = mc._cluster_chunks(chunks, threshold=0.9)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


# ---------------------------------------------------------------------------
# compact_user_memory — using a tiny in-memory fake Mongo db
# ---------------------------------------------------------------------------

class FakeCollection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _matches(doc, filt):
        for key, cond in filt.items():
            if isinstance(cond, dict):
                if "$ne" in cond and doc.get(key) == cond["$ne"]:
                    return False
                if "$in" in cond and doc.get(key) not in cond["$in"]:
                    return False
                if "$nin" in cond and doc.get(key) in cond["$nin"]:
                    return False
            elif doc.get(key) != cond:
                return False
        return True

    def find(self, filt, projection=None):
        return [d for d in self.docs if self._matches(d, filt)]

    def count_documents(self, filt):
        return len(self.find(filt))

    def delete_many(self, filt):
        for d in self.find(filt):
            self.docs.remove(d)

    def insert_one(self, doc):
        self.docs.append(doc)


class FakeDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture(autouse=True)
def reset_compaction_guard():
    mc._compaction_in_progress.clear()
    yield
    mc._compaction_in_progress.clear()


def _seed_chunks(fake_db, username, chunks):
    for c in chunks:
        fake_db["user_memory_chunks"].insert_one({**c, "username": username})


@run_async
async def test_compact_user_memory_inserts_before_deleting_and_promotes_facts(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("a1", [1.0, 0.0, 0.0], _id="id-a1"),
        _chunk("a2", [0.99, 0.01, 0.0], _id="id-a2"),
    ])

    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({
            "summary": "Consolidated summary.",
            "facts": [{"category": "preference", "fact": "Prefers dark mode."}],
        })))
    )

    embed_calls = []

    def fake_embed(vector_store, username, text, source_type="manual", source_ref=None):
        embed_calls.append((username, text, source_type))
        fake_db["user_memory_chunks"].insert_one({
            "_id": "compacted-1", "username": username, "text": text,
            "embedding": [1.0, 0.0, 0.0], "source_type": source_type, "source_ref": source_ref,
        })

    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", fake_embed)

    promoted_facts = []
    monkeypatch.setattr(mc, "save_user_fact", lambda username, fact, category, source: promoted_facts.append((username, fact, category, source)))

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed"
    assert result["clusters_formed"] == 1
    assert result["facts_promoted"] == 1
    assert result["chunks_before"] == 2
    assert result["chunks_after"] == 1  # both originals removed, one compacted summary added

    remaining = fake_db["user_memory_chunks"].find({"username": "jack"})
    assert len(remaining) == 1
    assert remaining[0]["source_type"] == "compacted_summary"
    assert embed_calls == [("jack", "Consolidated summary.", "compacted_summary")]
    assert promoted_facts == [("jack", "Prefers dark mode.", "preference", "inferred")]

    # And the compacted summary is now excluded from future compaction runs.
    assert mc.count_uncompacted_chunks(fake_db, "jack") == 0


@run_async
async def test_compact_user_memory_skips_when_not_enough_chunks():
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [_chunk("a1", [1.0, 0.0, 0.0], _id="id-a1")])

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "skipped"
    assert result["reason"] == "not_enough_chunks"


@run_async
async def test_compact_user_memory_skips_when_db_is_none():
    result = await mc.compact_user_memory(None, vector_store=object(), username="jack")
    assert result == {"status": "skipped", "reason": "no_database"}


@run_async
async def test_compact_user_memory_respects_concurrency_guard(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("a1", [1.0, 0.0, 0.0], _id="id-a1"),
        _chunk("a2", [0.99, 0.01, 0.0], _id="id-a2"),
    ])
    mc._compaction_in_progress.add("jack")

    llm_mock = AsyncMock()
    monkeypatch.setattr(mc.lite_llm, "ainvoke", llm_mock)

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result == {"status": "skipped", "reason": "already_running"}
    llm_mock.assert_not_called()


@run_async
async def test_maybe_trigger_compaction_only_fires_over_threshold(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [_chunk(f"c{i}", [1.0, 0.0, 0.0], _id=f"id-{i}") for i in range(3)])

    calls = []

    async def fake_compact(db, vector_store, username):
        calls.append(username)
        return {"status": "completed"}

    monkeypatch.setattr(mc, "compact_user_memory", fake_compact)

    await mc.maybe_trigger_compaction(fake_db, object(), "jack", threshold=5)
    assert calls == []

    await mc.maybe_trigger_compaction(fake_db, object(), "jack", threshold=2)
    assert calls == ["jack"]


# ---------------------------------------------------------------------------
# Tier 2 (self-compacting meta tier)
# ---------------------------------------------------------------------------

def _fake_embed_into(fake_db):
    """Returns a fake embed_and_store_memory_chunk that actually writes into fake_db,
    so tier-2 pooling behavior (which reads back what tier-1/tier-2 wrote) is realistic."""
    counter = {"n": 0}

    def fake_embed(vector_store, username, text, source_type="manual", source_ref=None):
        counter["n"] += 1
        fake_db["user_memory_chunks"].insert_one({
            "_id": f"compacted-{counter['n']}", "username": username, "text": text,
            "embedding": [1.0, 0.0, 0.0], "source_type": source_type, "source_ref": source_ref,
        })
    return fake_embed


@run_async
async def test_compact_meta_memory_pools_tier1_summaries_and_tags_tier2(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("Tier-1 summary A", [1.0, 0.0, 0.0], _id="t1-a"),
        _chunk("Tier-1 summary B", [0.99, 0.01, 0.0], _id="t1-b"),
    ])
    for doc in fake_db["user_memory_chunks"].docs:
        doc["source_type"] = "compacted_summary"

    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "Meta summary.", "facts": []})))
    )
    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", _fake_embed_into(fake_db))
    monkeypatch.setattr(mc, "save_user_fact", lambda *a, **k: None)

    result = await mc.compact_meta_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed"
    assert result["tier"] == 2
    remaining = fake_db["user_memory_chunks"].find({"username": "jack"})
    assert len(remaining) == 1
    assert remaining[0]["source_type"] == "compacted_meta_summary"


@run_async
async def test_compact_meta_memory_replaces_prior_meta_summary_instead_of_accumulating(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("Old meta summary", [1.0, 0.0, 0.0], _id="meta-old"),
    ])
    fake_db["user_memory_chunks"].docs[0]["source_type"] = "compacted_meta_summary"
    _seed_chunks(fake_db, "jack", [
        _chunk("New tier-1 summary", [0.99, 0.01, 0.0], _id="t1-new"),
    ])
    fake_db["user_memory_chunks"].docs[1]["source_type"] = "compacted_summary"

    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "Refreshed meta summary.", "facts": []})))
    )
    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", _fake_embed_into(fake_db))
    monkeypatch.setattr(mc, "save_user_fact", lambda *a, **k: None)

    result = await mc.compact_meta_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed"
    remaining = fake_db["user_memory_chunks"].find({"username": "jack"})
    # The old meta-summary and the tier-1 summary are both gone, replaced by ONE fresh meta-summary.
    assert len(remaining) == 1
    assert remaining[0]["text"] == "Refreshed meta summary."
    assert remaining[0]["source_type"] == "compacted_meta_summary"


@run_async
async def test_maybe_trigger_meta_compaction_only_fires_over_threshold(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [_chunk(f"s{i}", [1.0, 0.0, 0.0], _id=f"s-id-{i}") for i in range(3)])
    for doc in fake_db["user_memory_chunks"].docs:
        doc["source_type"] = "compacted_summary"

    calls = []

    async def fake_compact_meta(db, vector_store, username):
        calls.append(username)
        return {"status": "completed"}

    monkeypatch.setattr(mc, "compact_meta_memory", fake_compact_meta)

    await mc.maybe_trigger_meta_compaction(fake_db, object(), "jack", threshold=5)
    assert calls == []

    await mc.maybe_trigger_meta_compaction(fake_db, object(), "jack", threshold=2)
    assert calls == ["jack"]


@run_async
async def test_maybe_trigger_compaction_cascades_into_meta_only_when_tier1_completed(monkeypatch):
    fake_db = FakeDB()

    meta_calls = []

    async def fake_meta_trigger(db, vector_store, username, threshold=None):
        meta_calls.append(username)

    monkeypatch.setattr(mc, "maybe_trigger_meta_compaction", fake_meta_trigger)

    async def fake_compact_skipped(db, vector_store, username):
        return {"status": "skipped", "reason": "not_enough_chunks"}

    monkeypatch.setattr(mc, "compact_user_memory", fake_compact_skipped)
    _seed_chunks(fake_db, "jack", [_chunk("only-one", [1.0, 0.0, 0.0], _id="only-one")])
    await mc.maybe_trigger_compaction(fake_db, object(), "jack", threshold=0)
    assert meta_calls == []  # tier-1 was skipped, so tier-2 should never be checked

    async def fake_compact_completed(db, vector_store, username):
        return {"status": "completed"}

    monkeypatch.setattr(mc, "compact_user_memory", fake_compact_completed)
    await mc.maybe_trigger_compaction(fake_db, object(), "jack", threshold=0)
    assert meta_calls == ["jack"]  # tier-1 completed, so tier-2 gets checked
