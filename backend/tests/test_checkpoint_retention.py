import asyncio
import functools
from types import SimpleNamespace

from backend.services import checkpoint_retention as cr


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d[key], reverse=(direction == -1))
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeDeleteResult:
    def __init__(self, count):
        self.deleted_count = count


class _FakeCheckpointsCollection:
    def __init__(self, docs):
        self.docs = docs  # list of {"_id", "thread_id", "checkpoint_id"}

    def distinct(self, field):
        seen = []
        for d in self.docs:
            if d[field] not in seen:
                seen.append(d[field])
        return seen

    def find(self, query, projection=None):
        thread_id = query.get("thread_id")
        return _FakeCursor([d for d in self.docs if d.get("thread_id") == thread_id])

    def delete_many(self, query):
        ids = set(query["_id"]["$in"])
        before = len(self.docs)
        self.docs = [d for d in self.docs if d["_id"] not in ids]
        return _FakeDeleteResult(before - len(self.docs))


class _FakeWritesCollection:
    def __init__(self, docs):
        self.docs = docs  # list of {"checkpoint_id"}

    def delete_many(self, query):
        ids = set(query["checkpoint_id"]["$in"])
        before = len(self.docs)
        self.docs = [d for d in self.docs if d["checkpoint_id"] not in ids]
        return _FakeDeleteResult(before - len(self.docs))


def _fake_db(checkpoints_docs, writes_docs):
    checkpoints = _FakeCheckpointsCollection(checkpoints_docs)
    writes = _FakeWritesCollection(writes_docs)
    client = {
        cr.CHECKPOINT_DB_NAME: {
            cr.CHECKPOINTS_COLLECTION: checkpoints,
            cr.CHECKPOINT_WRITES_COLLECTION: writes,
        }
    }
    return SimpleNamespace(client=client), checkpoints, writes


def _ckpt(id_, thread_id, checkpoint_id):
    return {"_id": id_, "thread_id": thread_id, "checkpoint_id": checkpoint_id}


# ---------------------------------------------------------------------------
# prune_old_checkpoints
# ---------------------------------------------------------------------------

def test_prune_keeps_last_n_per_thread(monkeypatch):
    # thread "a" has 10 checkpoints (ids 0-9, higher id = more recent); thread "b" has 2.
    docs = [_ckpt(i, "a", f"a-ckpt-{i}") for i in range(10)]
    docs += [_ckpt(100 + i, "b", f"b-ckpt-{i}") for i in range(2)]
    db, checkpoints, writes = _fake_db(docs, [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=3)

    assert summary["skipped"] is False
    assert summary["threads_seen"] == 2
    assert summary["threads_pruned"] == 1  # only thread "a" had more than 3
    assert summary["deleted_checkpoints"] == 7
    # the 3 most recent (highest _id) for thread "a" survive: 7, 8, 9
    remaining_a = sorted(d["_id"] for d in checkpoints.docs if d["thread_id"] == "a")
    assert remaining_a == [7, 8, 9]
    # thread "b" untouched
    remaining_b = sorted(d["_id"] for d in checkpoints.docs if d["thread_id"] == "b")
    assert remaining_b == [100, 101]


def test_prune_deletes_matching_checkpoint_writes(monkeypatch):
    docs = [_ckpt(i, "a", f"ckpt-{i}") for i in range(5)]
    writes_docs = [{"checkpoint_id": f"ckpt-{i}", "payload": i} for i in range(5)]
    db, checkpoints, writes = _fake_db(docs, writes_docs)
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=2)

    assert summary["deleted_checkpoints"] == 3
    assert summary["deleted_checkpoint_writes"] == 3
    remaining_write_ids = sorted(w["checkpoint_id"] for w in writes.docs)
    assert remaining_write_ids == ["ckpt-3", "ckpt-4"]  # writes for the 2 kept checkpoints


def test_prune_thread_with_fewer_than_keep_is_untouched(monkeypatch):
    docs = [_ckpt(i, "a", f"ckpt-{i}") for i in range(2)]
    db, checkpoints, writes = _fake_db(docs, [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=3)

    assert summary["threads_pruned"] == 0
    assert summary["deleted_checkpoints"] == 0
    assert len(checkpoints.docs) == 2


def test_prune_returns_skipped_when_db_disabled(monkeypatch):
    monkeypatch.setattr(cr, "get_db", lambda: None)

    summary = cr.prune_old_checkpoints()

    assert summary == {"skipped": True, "reason": "USE_DB not enabled"}


# ---------------------------------------------------------------------------
# run_checkpoint_retention_loop
# ---------------------------------------------------------------------------

class _StopLoop(Exception):
    pass


@run_async
async def test_retention_loop_calls_prune_and_survives_a_failure(monkeypatch):
    """One iteration's failure must not kill the loop — the next scheduled run still
    gets a chance, same resilience as this app's other background tasks."""
    call_count = {"n": 0}

    def fake_prune(keep_per_thread):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient mongo error")
        return {"skipped": False}

    monkeypatch.setattr(cr, "prune_old_checkpoints", fake_prune)

    sleep_calls = {"n": 0}

    async def fake_sleep(seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 3:
            raise _StopLoop()

    monkeypatch.setattr(cr.asyncio, "sleep", fake_sleep)

    try:
        await cr.run_checkpoint_retention_loop(interval_seconds=0.01, keep_per_thread=3)
    except _StopLoop:
        pass

    # 3 real prune attempts happened (one failed, loop kept going) before we stopped it.
    assert call_count["n"] == 3
