import asyncio
import functools
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bson import ObjectId

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

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeDeleteResult:
    def __init__(self, count):
        self.deleted_count = count


def _matches(doc, query):
    for key, cond in query.items():
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$in" in cond and value not in cond["$in"]:
                return False
            if "$lt" in cond and not (value < cond["$lt"]):
                return False
        elif value != cond:
            return False
    return True


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
        return _FakeCursor([d for d in self.docs if _matches(d, query)])

    def aggregate(self, pipeline, allowDiskUse=False):
        # Minimal interpreter for exactly the two stages prune_old_checkpoints actually issues
        # ({"$sort": {"_id": -1}} then a $group-with-$push by thread_id) — not a general Mongo
        # aggregation engine, just enough to prove the real pipeline's shape/order is correct.
        docs = list(self.docs)
        for stage in pipeline:
            if "$sort" in stage:
                (key, direction), = stage["$sort"].items()
                docs.sort(key=lambda d: d[key], reverse=(direction == -1))
            elif "$group" in stage:
                group_spec = stage["$group"]
                group_key_field = group_spec["_id"].lstrip("$")
                push_field_name, push_spec = next(
                    (k, v["$push"]) for k, v in group_spec.items() if k != "_id"
                )
                grouped = {}
                order = []
                for d in docs:
                    key = d[group_key_field]
                    if key not in grouped:
                        grouped[key] = []
                        order.append(key)
                    grouped[key].append({out_key: d[ref.lstrip("$")] for out_key, ref in push_spec.items()})
                docs = [{"_id": key, push_field_name: grouped[key]} for key in order]
        return docs

    def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, query)]
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

    # max_age_days=0 disables the separate age axis for this test — plain int _ids here aren't
    # real ObjectIds, and this test is only exercising the per-thread floor.
    summary = cr.prune_old_checkpoints(keep_per_thread=3, max_age_days=0)

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

    summary = cr.prune_old_checkpoints(keep_per_thread=2, max_age_days=0)

    assert summary["deleted_checkpoints"] == 3
    assert summary["deleted_checkpoint_writes"] == 3
    remaining_write_ids = sorted(w["checkpoint_id"] for w in writes.docs)
    assert remaining_write_ids == ["ckpt-3", "ckpt-4"]  # writes for the 2 kept checkpoints


def test_prune_thread_with_fewer_than_keep_is_untouched(monkeypatch):
    docs = [_ckpt(i, "a", f"ckpt-{i}") for i in range(2)]
    db, checkpoints, writes = _fake_db(docs, [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=3, max_age_days=0)

    assert summary["threads_pruned"] == 0
    assert summary["deleted_checkpoints"] == 0
    assert len(checkpoints.docs) == 2


def test_prune_returns_skipped_when_db_disabled(monkeypatch):
    monkeypatch.setattr(cr, "get_db", lambda: None)

    summary = cr.prune_old_checkpoints()

    assert summary == {"skipped": True, "reason": "USE_DB not enabled"}


# ---------------------------------------------------------------------------
# max_age_days — the second axis. Bounds total storage against all-time thread COUNT, which
# the per-thread floor alone never does (a thread abandoned long ago keeps its floor forever).
# ---------------------------------------------------------------------------

def test_prune_deletes_aged_checkpoints_even_within_the_per_thread_floor(monkeypatch):
    now = datetime.now(timezone.utc)
    old_id = ObjectId.from_datetime(now - timedelta(days=200))
    recent_id = ObjectId.from_datetime(now - timedelta(days=1))
    docs = [
        _ckpt(old_id, "abandoned-thread", "old-ckpt"),
        _ckpt(recent_id, "active-thread", "recent-ckpt"),
    ]
    writes_docs = [{"checkpoint_id": "old-ckpt"}, {"checkpoint_id": "recent-ckpt"}]
    db, checkpoints, writes = _fake_db(docs, writes_docs)
    monkeypatch.setattr(cr, "get_db", lambda: db)

    # keep_per_thread=5 means the per-thread floor alone would leave BOTH untouched (neither
    # thread has more than 5 checkpoints) — only the age axis should remove the old one.
    summary = cr.prune_old_checkpoints(keep_per_thread=5, max_age_days=90)

    assert summary["threads_pruned"] == 0  # per-thread floor did nothing
    assert summary["deleted_aged_checkpoints"] == 1
    assert summary["deleted_aged_checkpoint_writes"] == 1
    remaining_ids = [d["checkpoint_id"] for d in checkpoints.docs]
    assert remaining_ids == ["recent-ckpt"]
    remaining_write_ids = [w["checkpoint_id"] for w in writes.docs]
    assert remaining_write_ids == ["recent-ckpt"]


def test_prune_age_axis_disabled_when_max_age_days_is_zero(monkeypatch):
    old_id = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=365))
    docs = [_ckpt(old_id, "abandoned-thread", "old-ckpt")]
    db, checkpoints, writes = _fake_db(docs, [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=5, max_age_days=0)

    assert summary["deleted_aged_checkpoints"] == 0
    assert len(checkpoints.docs) == 1


def test_prune_age_axis_keeps_checkpoints_newer_than_cutoff(monkeypatch):
    recent_id = ObjectId.from_datetime(datetime.now(timezone.utc) - timedelta(days=5))
    docs = [_ckpt(recent_id, "active-thread", "recent-ckpt")]
    db, checkpoints, writes = _fake_db(docs, [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_old_checkpoints(keep_per_thread=5, max_age_days=90)

    assert summary["deleted_aged_checkpoints"] == 0
    assert len(checkpoints.docs) == 1


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

    def fake_prune(keep_per_thread, max_age_days):
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


# ---------------------------------------------------------------------------
# prune_thread_checkpoints — runs after every chat turn. Observed: the daily/startup pass alone let
# one busy thread write 353 checkpoints (~480MB with their writes) between passes and fill the quota.
# ---------------------------------------------------------------------------

def test_per_turn_prune_keeps_only_the_newest_n_of_that_thread_and_leaves_other_threads_alone(monkeypatch):
    docs = [_ckpt(i, "a", f"a-{i}") for i in range(10)] + [_ckpt(100 + i, "b", f"b-{i}") for i in range(8)]
    writes_docs = [{"checkpoint_id": f"a-{i}", "thread_id": "a"} for i in range(10)]
    db, checkpoints, writes = _fake_db(docs, writes_docs)
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = cr.prune_thread_checkpoints("a", keep_per_thread=3)

    assert summary["deleted_checkpoints"] == 7
    assert summary["deleted_checkpoint_writes"] == 7
    assert sorted(d["_id"] for d in checkpoints.docs if d["thread_id"] == "a") == [7, 8, 9]
    assert len([d for d in checkpoints.docs if d["thread_id"] == "b"]) == 8  # untouched, even though > 3
    assert sorted(w["checkpoint_id"] for w in writes.docs) == ["a-7", "a-8", "a-9"]


def test_per_turn_prune_is_a_no_op_for_a_thread_within_its_floor(monkeypatch):
    db, checkpoints, _ = _fake_db([_ckpt(i, "a", f"a-{i}") for i in range(3)], [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    assert cr.prune_thread_checkpoints("a", keep_per_thread=3)["deleted_checkpoints"] == 0
    assert len(checkpoints.docs) == 3


def test_per_turn_prune_skips_without_a_database_and_never_raises(monkeypatch):
    monkeypatch.setattr(cr, "get_db", lambda: None)
    assert cr.prune_thread_checkpoints("a")["skipped"] is True

    def boom():
        raise RuntimeError("mongo down")

    monkeypatch.setattr(cr, "get_db", boom)
    assert cr.prune_thread_checkpoints("a") == {"skipped": True, "reason": "error"}


@run_async
async def test_per_turn_prune_async_wrapper_runs_the_same_prune(monkeypatch):
    db, checkpoints, _ = _fake_db([_ckpt(i, "a", f"a-{i}") for i in range(6)], [])
    monkeypatch.setattr(cr, "get_db", lambda: db)

    summary = await cr.prune_thread_checkpoints_async("a", keep_per_thread=2)

    assert summary["deleted_checkpoints"] == 4
