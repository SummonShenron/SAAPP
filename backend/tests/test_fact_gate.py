"""The write-side gate on passing work status (backend/utils/fact_gate.py) and its place in compaction (backend/services/memory_compaction.py). Example wordings are the shapes
the real store held (2026-11-23 review), not invented ones. No database, no model."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.components.constraints import MEMORY_COMPACTION_PROMPT
from backend.services import memory_compaction as mc
from backend.utils import fact_gate, self_counters


@pytest.mark.parametrize("text", [
    "Jack is working on an error lambda.",
    "The user is working on a rules processor pipeline change.",
    "Jack is currently implementing previously discussed changes.",
    "The user is actively developing an AI project.",
    "Jack is preparing to begin testing a technical project.",
    "The user is currently working on a page redesign project.",
    "Jack is currently focused on performing code reviews.",
])
def test_passing_work_status_that_names_nothing_is_not_stored(text):
    assert fact_gate.is_passing_status(text, "project", "work")
    assert fact_gate.is_passing_status(text, "project", "own")


@pytest.mark.parametrize("text", [
    "The user is working on a branch named 'feat/notes'.",                                         # quoted
    "The user is currently refactoring backend/services/local_workspace.py to fix validation.",   # a path
    "Jack is working on errAgent's alert paths.",                                                  # camelCase
    "Jack is working on the PAG project with Casey's team.",                                       # a capitalised name
    "Jack is working on 3 interview tasks.",                                                       # a number
    "The user is developing a personal project called errAgent.",                                  # no status phrase: a lasting description
    "Jack is developing a custom AI assistant for personal grounding.",
    "Jack completed a four-round onsite interview.",
])
def test_a_fact_that_names_something_or_describes_a_project_is_kept(text):
    assert not fact_gate.is_passing_status(text, "project", "work")


@pytest.mark.parametrize("text, category, domain", [
    ("The user is working on managing alcohol cravings during periods of high stress.", "goal", "personal"),
    ("The user is currently employed as a contractor.", "career", "work"),
    ("Jack is currently focused on adjusting his sleep schedule.", "goal", "own"),
    ("Jack is working on organizing his living space.", "project", "personal"),
    ("Jack is working on an error lambda.", "project", None),
    ("Jack is working on an error lambda.", "project", "general"),
])
def test_goals_career_facts_and_personal_or_unknown_domains_are_never_gated(text, category, domain):
    assert not fact_gate.is_passing_status(text, category, domain)


def test_the_person_named_first_is_not_counted_as_a_name_but_a_name_later_is():
    assert not fact_gate.names_something("Jack is working on a loader.")
    assert fact_gate.names_something("Jack is working on a loader for Principal.")


def test_the_compaction_prompt_says_status_is_not_a_fact_and_a_possessive_name_is_an_organisation():
    assert "is not a fact" in MEMORY_COMPACTION_PROMPT
    assert "never turn it\ninto a person" in MEMORY_COMPACTION_PROMPT
    MEMORY_COMPACTION_PROMPT.format(chunk_texts="x")                                # the new lines added no stray braces


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, flt=None, projection=None):
        return [d for d in self.docs if all(d.get(k) == v for k, v in (flt or {}).items() if not isinstance(v, dict))]

    def count_documents(self, flt=None):
        return len(self.find(flt))

    def find_one(self, *a, **k):
        return None

    def delete_many(self, flt):
        ids = set(flt["_id"]["$in"])
        self.docs[:] = [d for d in self.docs if d["_id"] not in ids]

    def update_many(self, *a, **k):
        return None

    def insert_one(self, doc):
        self.docs.append(doc)

    def insert_many(self, docs):
        self.docs.extend(docs)

    def update_one(self, *a, **k):
        return None


class _DB(dict):
    def __missing__(self, name):
        self[name] = _Coll([])
        return self[name]


def test_compaction_stores_the_named_fact_skips_the_status_one_and_counts_it(monkeypatch):
    db = _DB()
    db["user_memory_chunks"] = _Coll([
        {"_id": "a", "username": "jack", "text": "a", "embedding": [1.0, 0.0], "source_type": "manual"},
        {"_id": "b", "username": "jack", "text": "b", "embedding": [0.99, 0.01], "source_type": "manual"},
    ])
    monkeypatch.setattr(mc, "_fetch_chunks", lambda db, username, flt: list(db["user_memory_chunks"].docs))
    monkeypatch.setattr(mc, "_count_all_chunks", lambda db, username: len(db["user_memory_chunks"].docs))
    monkeypatch.setattr(mc, "_archive_cluster", lambda *a, **k: None)
    monkeypatch.setattr(mc, "log_compaction_event", lambda *a, **k: None)
    monkeypatch.setattr(mc, "embed_and_store_memory_chunk", lambda *a, **k: None)
    monkeypatch.setattr(mc.lite_llm, "ainvoke", AsyncMock(return_value=SimpleNamespace(content=json.dumps({"summary": "S.", "facts": [
        {"category": "project", "domain": "work", "fact": "Jack is working on an error lambda."},
        {"category": "project", "domain": "work", "fact": "Jack is working on the PAG project at Principal."},
    ]}))))
    saved, counted = [], []
    monkeypatch.setattr(mc, "save_user_fact", lambda username, fact, **k: saved.append(fact))
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: counted.append(dict(counts)))
    result = asyncio.run(mc.compact_user_memory(db, vector_store=object(), username="jack"))
    assert result["status"] == "completed"
    assert saved == ["Jack is working on the PAG project at Principal."]
    assert (result["facts_promoted"], result["facts_skipped_vague"]) == (1, 1)
    assert counted == [{"vague_fact_skipped": 1}]
    assert "vague_fact_skipped" in self_counters.KEYS
