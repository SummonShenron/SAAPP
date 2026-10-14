"""Clear All must forget everything Sonic keeps about a user: facts, notes from past conversations, and the archived copies of both."""
import pathlib
from types import SimpleNamespace

import pytest

from backend.services import memory_compaction as mc
from backend.services import memory_search as ms
from backend.utils import memory_erase as me
from backend.utils import memory_maintenance as mm
from backend.utils import memory_utils


class Collection:
    def __init__(self, docs=None, fail=False):
        self.docs = list(docs or [])
        self.fail = fail

    def count_documents(self, filt):
        return len([d for d in self.docs if all(d.get(k) == v for k, v in filt.items())])

    def delete_many(self, filt):
        if self.fail:
            raise RuntimeError("database down")
        gone = [d for d in self.docs if all(d.get(k) == v for k, v in filt.items())]
        for d in gone:
            self.docs.remove(d)
        return SimpleNamespace(deleted_count=len(gone))


class DB:
    def __init__(self, **collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


def seeded_db(**overrides):
    rows = lambda n, who: [{"username": who, "text": f"{who} {i}"} for i in range(n)]  # noqa: E731
    collections = {name: Collection(rows(3, "jack") + rows(2, "sam")) for name in me.ERASED_COLLECTIONS}
    collections.update(overrides)
    return DB(**collections)


@pytest.fixture(autouse=True)
def facts(monkeypatch):
    state = SimpleNamespace(saved=["a", "b", "c", "d"], cleared=[])
    monkeypatch.setattr(me, "load_user_facts", lambda username: list(state.saved))
    monkeypatch.setattr(me, "delete_all_user_facts", lambda username: state.cleared.append(username))
    return state


def test_every_place_memory_is_kept_is_on_the_list_and_the_names_match_the_ones_the_app_writes_to():
    assert set(me.ERASED_COLLECTIONS) == {"user_memory_chunks", "user_memory_chunk_archive", "user_memory_fact_archive", "memory_compaction_events"}
    assert ms.USER_MEMORY_COLLECTION in me.ERASED_COLLECTIONS
    assert mc.ARCHIVE_COLLECTION == ms.CHUNK_ARCHIVE_COLLECTION == mm.ARCHIVE_COLLECTION
    assert memory_utils.FACT_ARCHIVE_COLLECTION in me.ERASED_COLLECTIONS
    assert me.COMPACTION_EVENTS_COLLECTION == "memory_compaction_events"


def test_the_event_log_name_is_the_one_compaction_really_writes_to():
    source = pathlib.Path(mc.__file__).read_text(encoding="utf-8")
    assert f'db["{me.COMPACTION_EVENTS_COLLECTION}"]' in source


def test_clearing_deletes_the_facts_the_notes_and_both_archives_for_that_user_only(facts):
    db = seeded_db()
    result = me.erase_user_memory(db, "jack")
    assert result["failed"] == [] and result["facts"] == 4 and facts.cleared == ["jack"]
    assert result["deleted"] == {name: 3 for name in me.ERASED_COLLECTIONS}
    for name in me.ERASED_COLLECTIONS:
        assert [d["username"] for d in db[name].docs] == ["sam", "sam"], name  # another user's memory is untouched


def test_a_failed_step_is_reported_and_the_other_steps_still_run():
    db = seeded_db(user_memory_chunk_archive=Collection([{"username": "jack"}], fail=True))
    result = me.erase_user_memory(db, "jack")
    assert result["failed"] == ["user_memory_chunk_archive"]
    assert db["user_memory_chunks"].count_documents({"username": "jack"}) == 0
    assert db["user_memory_fact_archive"].count_documents({"username": "jack"}) == 0
    assert db["user_memory_chunk_archive"].count_documents({"username": "jack"}) == 1  # the one that failed is still there, and said so


def test_a_failure_clearing_the_facts_is_reported_too(monkeypatch):
    def broken(username):
        raise RuntimeError("write failed")

    monkeypatch.setattr(me, "delete_all_user_facts", broken)
    result = me.erase_user_memory(seeded_db(), "jack")
    assert result["failed"] == ["facts"]
    assert result["deleted"]["user_memory_chunks"] == 3  # the rest were still cleared


def test_with_no_database_only_the_facts_are_cleared_and_nothing_fails(facts):
    result = me.erase_user_memory(None, "jack")
    assert result == {"deleted": {}, "facts": 4, "failed": []} and facts.cleared == ["jack"]


def test_the_counts_the_page_shows_are_the_notes_and_the_archived_copies_of_notes_and_facts():
    db = seeded_db()
    assert me.stored_counts(db, "jack") == {"notes": 3, "archived": 6}
    assert me.stored_counts(db, "nobody") == {"notes": 0, "archived": 0}
    assert me.stored_counts(None, "jack") == {"notes": 0, "archived": 0}


def test_counting_never_raises_even_if_the_database_does():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert me.stored_counts(Broken(), "jack") == {"notes": 0, "archived": 0}


def test_after_clearing_nothing_is_left_to_count():
    db = seeded_db()
    me.erase_user_memory(db, "jack")
    assert me.stored_counts(db, "jack") == {"notes": 0, "archived": 0}


# ---- the endpoints ----------------------------------------------------------------------------------------------------------------


def app_source():
    return (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")


def test_clear_all_erases_through_the_one_eraser_forgets_the_loops_and_never_reports_a_partial_wipe_as_success():
    source = app_source()
    clear = source[source.index("async def clear_memory"): source.index('@app.get("/api/open-loops")')]
    assert "memory_erase.erase_user_memory" in clear and "forget_all_loops" in clear
    assert 'result["failed"]' in clear and "HTTPException(status_code=500" in clear
    assert clear.index("raise HTTPException") < clear.index('return {"status": "cleared"')


def test_the_stored_counts_endpoint_requires_a_signed_in_user_and_asks_only_about_that_user():
    source = app_source()
    start = source.index('@app.get("/api/memory/stored")')
    block = source[start: start + 500]
    assert "Depends(get_current_user)" in block and 'current_user.get("sub")' in block and "memory_erase.stored_counts" in block


def test_the_page_enables_clear_all_when_only_notes_or_archives_exist_and_the_confirmation_names_what_goes():
    root = pathlib.Path(__file__).resolve().parents[2] / "local" / "src"
    page = (root / "pages" / "Memory.tsx").read_text(encoding="utf-8")
    assert "stored.notes === 0 && stored.archived === 0" in page
    assert "the notes from your past conversations" in page and "archived copies" in page
    assert "getMemoryStored" in (root / "api.ts").read_text(encoding="utf-8")
