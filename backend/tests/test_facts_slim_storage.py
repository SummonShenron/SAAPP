"""The saved facts are stored slim: the text in one small document and the embeddings, packed as 16-bit floats, in another (backend/utils/memory_utils.py).

The old single document carried every fact's embedding as 768 doubles (~4 MB for 345 facts) and took ~41 s to read cold on the slow database link."""
import json
import time
import uuid

import numpy as np
import pytest

from backend.utils import memory_utils as mu
from backend.utils.embedding_utils import cosine_similarity


class Coll:
    def __init__(self):
        self.doc = None
        self.reads = 0
        self.writes = 0

    def find_one(self, filt):
        self.reads += 1
        return self.doc

    def update_one(self, filt, update, upsert=False):
        self.writes += 1
        guard = filt.get("embeddings_split")
        if guard == {"$ne": True} and (self.doc or {}).get("embeddings_split") is True:
            return type("R", (), {"modified_count": 0})()  # the document is already in the new layout: the conversion must not touch it
        if self.doc is None and not upsert:
            return type("R", (), {"modified_count": 0})()
        self.doc = {**(self.doc or {"username": filt["username"]}), **update["$set"]}
        return type("R", (), {"modified_count": 1})()

    def delete_one(self, filt):
        self.doc = None


class Db(dict):
    def __getitem__(self, name):
        return self.setdefault(name, Coll())


def embedding(seed, size=768):
    rng = np.random.default_rng(seed)
    v = rng.normal(0, 0.05, size)
    return (v / np.linalg.norm(v) * 0.9).tolist()


def fact(fid, with_embedding=True, seed=None):
    now = "2026-10-16T00:00:00+00:00"
    return mu.UserFact(id=fid, username="jack", category="preference", fact=f"fact {fid}", created_at=now, updated_at=now,
                       embedding=embedding(seed if seed is not None else hash(fid) % 1000) if with_embedding else None)


@pytest.fixture
def db(monkeypatch):
    database = Db()
    monkeypatch.setattr(mu, "get_db", lambda: database)
    mu.clear_facts_cache()
    return database


def test_a_packed_embedding_is_a_quarter_the_size_and_barely_changes_a_similarity():
    a, b = embedding(1), embedding(2)
    packed = mu.pack_embedding(a)
    assert len(packed) == 768 * 2 and len(json.dumps(a)) > 4 * len(packed)
    restored = mu.unpack_embedding(packed)
    assert len(restored) == 768
    assert abs(cosine_similarity(a, b) - cosine_similarity(restored, b)) < 1e-3
    assert cosine_similarity(a, restored) > 0.99999


def test_a_save_puts_the_text_and_the_embeddings_in_separate_documents(db):
    mu.save_user_facts("jack", [fact("a", seed=1), fact("b", seed=2), fact("none", with_embedding=False)])
    stored = db["user_memory_facts"].doc
    assert stored["embeddings_split"] is True and len(stored["facts"]) == 3
    assert all("embedding" not in f for f in stored["facts"])
    vectors = db["user_memory_fact_embeddings"].doc["vectors"]
    assert {v["id"] for v in vectors} == {"a", "b"} and all(isinstance(v["v"], bytes) and len(v["v"]) == 1536 for v in vectors)   # a fact with no embedding has no vector
    assert len(json.dumps(stored)) < 2000                                                                          # the facts document is small now


def test_a_cold_load_puts_each_embedding_back_on_its_fact_and_reads_two_documents(db):
    originals = [fact("a", seed=1), fact("b", seed=2), fact("none", with_embedding=False)]
    mu.save_user_facts("jack", originals)
    mu.clear_facts_cache()                      # a restarted server: nothing in memory
    for coll in db.values():
        coll.reads = 0
    loaded = {f.id: f for f in mu.load_user_facts("jack")}
    assert db["user_memory_facts"].reads == 1 and db["user_memory_fact_embeddings"].reads == 1
    assert cosine_similarity(loaded["a"].embedding, originals[0].embedding) > 0.99999
    assert cosine_similarity(loaded["b"].embedding, originals[1].embedding) > 0.99999
    assert loaded["none"].embedding is None and loaded["a"].fact == "fact a"


def test_a_fact_list_without_any_embeddings_costs_no_second_read_to_cache_once(db):
    mu.save_user_facts("jack", [fact("a", with_embedding=False)])
    mu.clear_facts_cache()
    for _ in range(3):
        mu.load_user_facts("jack")
    assert db["user_memory_facts"].reads == 1                                  # still cached after the first read


def test_a_document_in_the_old_layout_still_loads_and_is_converted_in_the_background(db):
    old = [fact("a", seed=1), fact("b", seed=2)]
    db["user_memory_facts"].doc = {"username": "jack", "facts": [f.model_dump() for f in old]}   # embeddings inline, no marker
    loaded = {f.id: f for f in mu.load_user_facts("jack")}
    assert cosine_similarity(loaded["a"].embedding, old[0].embedding) > 0.99999                 # served exactly as before
    deadline = time.time() + 5
    while time.time() < deadline and not db["user_memory_facts"].doc.get("embeddings_split"):
        time.sleep(0.02)
    stored = db["user_memory_facts"].doc
    assert stored["embeddings_split"] is True and all("embedding" not in f for f in stored["facts"])
    assert {v["id"] for v in db["user_memory_fact_embeddings"].doc["vectors"]} == {"a", "b"}
    mu.clear_facts_cache()
    assert cosine_similarity({f.id: f for f in mu.load_user_facts("jack")}["b"].embedding, old[1].embedding) > 0.99999


def test_the_conversion_never_overwrites_a_newer_list_saved_in_the_new_layout_meanwhile(db):
    mu.save_user_facts("jack", [fact("newer", seed=3)])                                          # another instance already saved slim
    stale = [fact("old", seed=4)]
    assert mu._write_facts(db, "jack", stale, only_if_unsplit=True) is False
    assert [f["id"] for f in db["user_memory_facts"].doc["facts"]] == ["newer"]


def test_a_fact_id_with_dots_or_a_dollar_sign_is_safe_because_ids_are_values_not_keys(db):
    mu.save_user_facts("jack", [fact("a.b", seed=1), fact("$odd", seed=2)])
    mu.clear_facts_cache()
    assert {f.id: f.embedding is not None for f in mu.load_user_facts("jack")} == {"a.b": True, "$odd": True}


def test_forgetting_every_fact_leaves_no_vectors_behind(db):
    mu.save_user_facts("jack", [fact("a", seed=1)])
    assert db["user_memory_fact_embeddings"].doc is not None
    mu.delete_all_user_facts("jack")
    assert db["user_memory_fact_embeddings"].doc is None and db["user_memory_facts"].doc["facts"] == []


def test_the_local_json_fallback_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(mu, "get_db", lambda: None)
    monkeypatch.setattr(mu, "DATA_DIR", str(tmp_path))
    mu.clear_facts_cache()
    mu.save_user_facts("jack", [fact("a", seed=1)])
    saved = json.loads((tmp_path / "jack.json").read_text())
    assert len(saved[0]["embedding"]) == 768                                                      # the file keeps its embeddings inline, as before
    assert mu.load_user_facts("jack")[0].embedding == saved[0]["embedding"]
