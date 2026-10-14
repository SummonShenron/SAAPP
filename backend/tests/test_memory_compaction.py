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

    def find_one(self, filt, sort=None):
        matches = self.find(filt)
        if not matches:
            return None
        if sort:
            key, direction = sort[0]
            matches = sorted(matches, key=lambda d: d.get(key), reverse=(direction == -1))
        return matches[0]

    def count_documents(self, filt):
        return len(self.find(filt))

    def delete_many(self, filt):
        for d in self.find(filt):
            self.docs.remove(d)

    def update_many(self, filt, update):
        for d in self.find(filt):
            d.update(update.get("$set", {}))

    def insert_one(self, doc):
        self.docs.append(doc)

    def update_one(self, filt, update, upsert=False):
        matches = self.find(filt)
        if matches:
            matches[0].update(update.get("$set", {}))
        elif upsert:
            self.docs.append({**filt, **update.get("$setOnInsert", {}), **update.get("$set", {})})


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
    monkeypatch.setattr(mc, "save_user_fact", lambda username, fact, category, source, confidence=1.0, memory_vector_store=None: promoted_facts.append((username, fact, category, source)))

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed"
    assert result["clusters_formed"] == 1
    assert result["singletons_promoted"] == 0
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
async def test_dissimilar_singletons_are_promoted_not_left_behind_forever(monkeypatch):
    """The actual bug this fixes, reproduced: three genuinely distinct chunks (real production
    logs showed exactly this shape — a healthy memory full of diverse content forms zero
    clusters). Before the fix they'd stay counted as "uncompacted" forever, getting re-fetched
    and re-clustered (finding nothing, every time) on every future run. After the fix they're
    retagged and the backlog actually clears."""
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("Prefers dark mode", [1.0, 0.0, 0.0], _id="id-a"),
        _chunk("Works on a LangGraph agent project", [0.0, 1.0, 0.0], _id="id-b"),
        _chunk("Lives in Iowa", [0.0, 0.0, 1.0], _id="id-c"),
    ])

    llm_mock = AsyncMock()
    monkeypatch.setattr(mc.lite_llm, "ainvoke", llm_mock)

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed"
    assert result["clusters_formed"] == 0
    assert result["singletons_promoted"] == 3
    assert result["chunks_before"] == 3
    assert result["chunks_after"] == 3  # nothing deleted — genuinely distinct content is kept
    llm_mock.assert_not_called()  # no cluster ever formed, so no summarization call was made

    remaining = fake_db["user_memory_chunks"].find({"username": "jack"})
    assert len(remaining) == 3
    assert all(d["source_type"] == "compacted_summary" for d in remaining)
    assert {d["text"] for d in remaining} == {
        "Prefers dark mode", "Works on a LangGraph agent project", "Lives in Iowa",
    }

    # The real proof: the backlog is actually gone, not just hidden for one run.
    assert mc.count_uncompacted_chunks(fake_db, "jack") == 0
    second_run = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")
    assert second_run == {
        "status": "skipped", "reason": "not_enough_chunks", "chunks_before": 3, "tier": 1,
    }


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


# ---------------------------------------------------------------------------
# Pattern extraction cooldown
# ---------------------------------------------------------------------------

def _facts(n, category="preference"):
    return [SimpleNamespace(category=category, fact=f"Fact number {i}") for i in range(n)]


def _seed_pattern_extraction_event(fake_db, username, *, hours_ago: float, fact_count: int):
    import datetime as dt
    timestamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat()
    fake_db["memory_compaction_events"].insert_one({
        "username": username,
        "type": "pattern_extraction",
        "status": "completed",
        "fact_count": fact_count,
        "timestamp": timestamp,
    })


@run_async
async def test_pattern_extraction_cooldown_blocks_recent_rerun_with_few_new_facts(monkeypatch):
    fake_db = FakeDB()
    _seed_pattern_extraction_event(fake_db, "jack", hours_ago=1, fact_count=mc.MIN_FACTS_FOR_PATTERN_EXTRACTION)
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION + 1))
    llm_mock = AsyncMock()
    monkeypatch.setattr(mc.lite_llm, "ainvoke", llm_mock)

    result = await mc.extract_user_patterns(fake_db, object(), "jack")

    assert result == {"status": "skipped", "reason": "cooldown", "fact_count": mc.MIN_FACTS_FOR_PATTERN_EXTRACTION + 1}
    llm_mock.assert_not_called()


@run_async
async def test_pattern_extraction_cooldown_allows_rerun_after_interval_expires(monkeypatch):
    fake_db = FakeDB()
    _seed_pattern_extraction_event(
        fake_db, "jack",
        hours_ago=mc.MIN_PATTERN_EXTRACTION_INTERVAL_HOURS + 1,
        fact_count=mc.MIN_FACTS_FOR_PATTERN_EXTRACTION,
    )
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"patterns": []})))
    )

    result = await mc.extract_user_patterns(fake_db, object(), "jack")

    assert result["status"] == "completed"


@run_async
async def test_pattern_extraction_cooldown_allows_rerun_with_enough_new_facts(monkeypatch):
    fake_db = FakeDB()
    _seed_pattern_extraction_event(fake_db, "jack", hours_ago=1, fact_count=mc.MIN_FACTS_FOR_PATTERN_EXTRACTION)
    monkeypatch.setattr(
        mc, "load_user_facts",
        lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION + mc.MIN_NEW_FACTS_FOR_PATTERN_EXTRACTION),
    )
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"patterns": []})))
    )

    result = await mc.extract_user_patterns(fake_db, object(), "jack")

    assert result["status"] == "completed"


@run_async
async def test_pattern_extraction_no_prior_run_has_no_cooldown(monkeypatch):
    fake_db = FakeDB()
    monkeypatch.setattr(mc, "load_user_facts", lambda username: _facts(mc.MIN_FACTS_FOR_PATTERN_EXTRACTION))
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"patterns": []})))
    )

    result = await mc.extract_user_patterns(fake_db, object(), "jack")

    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# a summary is dated by the notes it replaces, not by when compaction ran
# ---------------------------------------------------------------------------

def test_span_of_returns_the_oldest_and_newest_dates_of_a_cluster():
    cluster = [
        {"created_at": "2026-07-01T10:00:00+00:00"},
        {"created_at": "2026-09-20T10:00:00+00:00", "latest_at": "2026-10-05T10:00:00+00:00"},
        {"created_at": "2026-08-01T10:00:00+00:00"},
    ]
    assert mc._span_of(cluster) == {"created_at": "2026-07-01T10:00:00+00:00", "latest_at": "2026-10-05T10:00:00+00:00"}


def test_span_of_ignores_notes_with_no_usable_date_and_is_empty_when_none_has_one():
    assert mc._span_of([{"text": "no date"}, {"created_at": "garbage"}]) == {}
    assert mc._span_of([{"created_at": "garbage"}, {"created_at": "2026-07-01T10:00:00"}])["created_at"] == "2026-07-01T10:00:00+00:00"


@run_async
async def test_a_compacted_summary_keeps_the_oldest_date_of_what_it_replaced(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        {**_chunk("a1", [1.0, 0.0, 0.0], _id="id-a1"), "created_at": "2026-07-01T10:00:00+00:00"},
        {**_chunk("a2", [0.99, 0.01, 0.0], _id="id-a2"), "created_at": "2026-10-01T10:00:00+00:00"},
    ])
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "Consolidated.", "facts": []})))
    )
    stored = []

    def fake_embed(vector_store, username, text, source_type="manual", source_ref=None, created_at=None, latest_at=None):
        stored.append({"text": text, "created_at": created_at, "latest_at": latest_at})

    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", fake_embed)
    monkeypatch.setattr(mc, "save_user_fact", lambda *a, **k: None)

    await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert stored == [{"text": "Consolidated.", "created_at": "2026-07-01T10:00:00+00:00", "latest_at": "2026-10-01T10:00:00+00:00"}]


# ---------------------------------------------------------------------------
# originals are archived before they are deleted, so a merge can be undone
# ---------------------------------------------------------------------------

def _two_similar(fake_db, **extra):
    _seed_chunks(fake_db, "jack", [
        {**_chunk("a1", [1.0, 0.0, 0.0], source_ref="sess-1", _id="id-a1"), "created_at": "2026-07-01T10:00:00+00:00", **extra},
        {**_chunk("a2", [0.99, 0.01, 0.0], source_ref="sess-2", _id="id-a2"), "created_at": "2026-08-01T10:00:00+00:00"},
    ])


def _patch_llm_and_store(monkeypatch, fake_db, stored):
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "Consolidated.", "facts": []})))
    )

    def fake_embed(vector_store, username, text, source_type="manual", source_ref=None, created_at=None, latest_at=None):
        stored.append({"text": text, "source_ref": source_ref})
        fake_db["user_memory_chunks"].insert_one({"_id": "summary", "username": username, "text": text, "source_type": source_type, "source_ref": source_ref})

    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", fake_embed)
    monkeypatch.setattr(mc, "save_user_fact", lambda *a, **k: None)


@run_async
async def test_merged_notes_are_copied_to_the_archive_with_their_text_embedding_and_dates(monkeypatch):
    fake_db = FakeDB()
    _two_similar(fake_db)
    stored = []
    _patch_llm_and_store(monkeypatch, fake_db, stored)

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    archived = fake_db[mc.ARCHIVE_COLLECTION].docs
    assert result["status"] == "completed" and result["clusters_archived"] == 1 and result["clusters_skipped"] == 0
    assert sorted(d["_id"] for d in archived) == ["id-a1", "id-a2"]
    first = next(d for d in archived if d["_id"] == "id-a1")
    assert first["text"] == "a1" and first["embedding"] == [1.0, 0.0, 0.0] and first["created_at"] == "2026-07-01T10:00:00+00:00"
    assert first["username"] == "jack" and first["compaction_tier"] == 1 and first["run_id"] == result["run_id"]
    assert first["archived_at"] and first["cluster_id"]
    # the live store holds only the summary now
    assert [d["_id"] for d in fake_db["user_memory_chunks"].docs] == ["summary"]


@run_async
async def test_the_summary_is_tied_to_its_archived_originals_and_provenance_does_not_nest(monkeypatch):
    fake_db = FakeDB()
    _two_similar(fake_db)
    stored = []
    _patch_llm_and_store(monkeypatch, fake_db, stored)

    await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    cluster_id = fake_db[mc.ARCHIVE_COLLECTION].docs[0]["cluster_id"]
    assert stored[0]["source_ref"] == f"compacted:2 chunks (sess-1, sess-2) #{cluster_id}"

    # a second-tier pass over a summary that already has provenance names the original sources, not the earlier merge
    fake_db2 = FakeDB()
    _seed_chunks(fake_db2, "jack", [
        _chunk("s1", [1.0, 0.0, 0.0], source_ref="compacted:2 chunks (a, b) #abc123", _id="s1"),
        _chunk("s2", [0.99, 0.01, 0.0], source_ref="sess-9", _id="s2"),
    ])
    for doc in fake_db2["user_memory_chunks"].docs:
        doc["source_type"] = "compacted_summary"
    stored2 = []
    _patch_llm_and_store(monkeypatch, fake_db2, stored2)
    await mc.compact_meta_memory(fake_db2, vector_store=object(), username="jack")
    assert stored2[0]["source_ref"].startswith("compacted:2 chunks (sess-9) #")
    assert "abc123" not in stored2[0]["source_ref"]


@run_async
async def test_a_cluster_that_cannot_be_archived_is_left_alone_and_nothing_is_deleted(monkeypatch):
    fake_db = FakeDB()
    _two_similar(fake_db)
    stored = []
    _patch_llm_and_store(monkeypatch, fake_db, stored)

    def broken(*args, **kwargs):
        raise RuntimeError("archive down")

    monkeypatch.setattr(mc, "_archive_cluster", broken)

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed" and result["clusters_skipped"] == 1 and result["clusters_archived"] == 0
    assert sorted(d["_id"] for d in fake_db["user_memory_chunks"].docs) == ["id-a1", "id-a2"]  # both originals still live
    assert stored == []  # and no summary was written for them


def test_archiving_twice_is_harmless_because_each_note_is_keyed_by_its_own_id():
    fake_db = FakeDB()
    cluster = [_chunk("a1", [1.0, 0.0, 0.0], _id="id-a1"), _chunk("a2", [0.9, 0.1, 0.0], _id="id-a2")]
    mc._archive_cluster(fake_db, "jack", cluster, tier=2, run_id="r1", cluster_id="c1")
    mc._archive_cluster(fake_db, "jack", cluster, tier=2, run_id="r1", cluster_id="c1")
    assert len(fake_db[mc.ARCHIVE_COLLECTION].docs) == 2


def test_the_stale_flag_travels_into_the_archive_so_a_restore_does_not_revive_a_superseded_note():
    fake_db = FakeDB()
    mc._archive_cluster(fake_db, "jack", [{**_chunk("old", [1.0, 0.0, 0.0], _id="id-old"), "stale": True}], tier=1, run_id="r", cluster_id="c")
    assert fake_db[mc.ARCHIVE_COLLECTION].docs[0]["stale"] is True


# ---------------------------------------------------------------------------
# the tier-2 threshold and the cap on a merge group
# ---------------------------------------------------------------------------

def test_tier_two_merges_at_085_like_tier_one_because_090_merged_nothing_on_real_notes():
    import inspect

    # the default in the code (the live value can be overridden by MEMORY_META_COMPACTION_SIMILARITY in the environment)
    assert 'MEMORY_META_COMPACTION_SIMILARITY", "0.85"' in inspect.getsource(mc)
    assert 'MEMORY_COMPACTION_SIMILARITY", "0.85"' in inspect.getsource(mc)


def test_a_merge_group_never_exceeds_the_cap_and_the_rest_seed_later_groups():
    chunks = [_chunk(f"n{i}", [1.0, 0.001 * i, 0.0]) for i in range(30)]
    clusters = mc._cluster_chunks(chunks, threshold=0.9, max_size=12)
    assert max(len(c) for c in clusters) <= 12
    assert sum(len(c) for c in clusters) == 30  # nothing lost or duplicated
    assert [len(c) for c in clusters] == [12, 12, 6]
    assert mc.MAX_CLUSTER_SIZE == 12


# ---------------------------------------------------------------------------
# a note flagged stale (superseded) is never folded into a new summary
# ---------------------------------------------------------------------------

@run_async
async def test_a_stale_note_is_left_out_of_compaction_and_cannot_come_back_inside_a_fresh_summary(monkeypatch):
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        _chunk("Works at Principal", [1.0, 0.0, 0.0], _id="old"),
        _chunk("Works at Principal as an engineer", [0.99, 0.01, 0.0], _id="fresh-a"),
        _chunk("Works at Principal on the payments team", [0.98, 0.02, 0.0], _id="fresh-b"),
    ])
    fake_db["user_memory_chunks"].docs[0]["stale"] = True
    stored = []
    _patch_llm_and_store(monkeypatch, fake_db, stored)
    monkeypatch.setattr(mc, "_summarize_cluster", AsyncMock(return_value={"summary": "Merged.", "facts": []}))

    result = await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert result["status"] == "completed" and result["clusters_formed"] == 1
    assert sorted(d["_id"] for d in fake_db[mc.ARCHIVE_COLLECTION].docs) == ["fresh-a", "fresh-b"]  # the stale one was not archived either
    left = {d["_id"]: d for d in fake_db["user_memory_chunks"].docs}
    assert "old" in left and left["old"]["stale"] is True  # still there, still hidden, untouched
    assert "fresh-a" not in left and "fresh-b" not in left


def test_stale_notes_do_not_count_toward_either_compaction_trigger():
    fake_db = FakeDB()
    _seed_chunks(fake_db, "jack", [
        {**_chunk("a", [1.0, 0.0, 0.0], _id="a"), "source_type": "conversation_turn"},
        {**_chunk("b", [0.0, 1.0, 0.0], _id="b"), "source_type": "conversation_turn", "stale": True},
        {**_chunk("c", [0.0, 0.0, 1.0], _id="c"), "source_type": "compacted_summary"},
        {**_chunk("d", [0.5, 0.5, 0.0], _id="d"), "source_type": "compacted_summary", "stale": True},
    ])
    assert mc.count_uncompacted_chunks(fake_db, "jack") == 1
    assert mc._count_tier2_pool(fake_db, "jack") == 1


# ---------------------------------------------------------------------------
# facts a model infers from a summary start less trusted than ones the user stated
# ---------------------------------------------------------------------------

@run_async
async def test_facts_promoted_from_a_compacted_cluster_start_at_the_lower_inferred_confidence(monkeypatch):
    fake_db = FakeDB()
    _two_similar(fake_db)
    monkeypatch.setattr(
        mc.lite_llm, "ainvoke",
        AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "Consolidated.", "facts": [{"category": "preference", "fact": "Prefers dark mode."}]})))
    )
    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", lambda *a, **k: None)
    saved = []
    monkeypatch.setattr(mc, "save_user_fact", lambda username, fact, category, source, confidence=1.0, memory_vector_store=None: saved.append((source, confidence)))

    await mc.compact_user_memory(fake_db, vector_store=object(), username="jack")

    assert saved == [("inferred", mc.COMPACTION_FACT_CONFIDENCE)]
    assert 0 < mc.COMPACTION_FACT_CONFIDENCE < 1.0


def test_the_inferred_confidence_default_is_07():
    import inspect

    assert 'MEMORY_COMPACTION_FACT_CONFIDENCE", "0.7"' in inspect.getsource(mc)
