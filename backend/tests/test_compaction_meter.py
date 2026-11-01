"""The memory compaction meter's numbers (2026-11-01): how many of a person's memory notes are waiting to be folded together, and the number at which they are. It replaced the context ring, which never
came close to filling because a reply is given only the last ten messages. A count and a limit: no note text."""
import pathlib

import pytest

from backend.services import memory_compaction as mc
from backend.utils import compaction_meter as cm

ROOT = pathlib.Path(__file__).resolve().parents[2]


class _Collection:
    def __init__(self, count):
        self.count, self.filters = count, []

    def count_documents(self, filt):
        self.filters.append(filt)
        return self.count


def _db(count):
    collection = _Collection(count)
    return {mc.USER_MEMORY_COLLECTION: collection}, collection


def test_the_meter_is_the_count_of_notes_not_yet_folded_together_against_the_threshold():
    db, collection = _db(37)
    assert cm.view(db, "jack") == {"uncompacted": 37, "threshold": mc.DEFAULT_COMPACTION_THRESHOLD}
    assert collection.filters[0]["username"] == "jack"                                              # only this person's notes
    assert collection.filters[0]["source_type"] == {"$nin": [mc.TIER1_SOURCE_TYPE, mc.TIER2_SOURCE_TYPE]} and collection.filters[0]["stale"] == {"$ne": True}


def test_it_uses_the_same_count_and_threshold_the_compaction_itself_uses():
    source = (ROOT / "backend/services/memory_compaction.py").read_text(encoding="utf-8")
    assert "uncompacted = await asyncio.to_thread(count_uncompacted_chunks, db, username)" in source and "if uncompacted > effective_threshold:" in source
    helper = (ROOT / "backend/utils/compaction_meter.py").read_text(encoding="utf-8")
    assert "count_uncompacted_chunks(db, username)" in helper and "DEFAULT_COMPACTION_THRESHOLD" in helper


def test_a_count_over_the_threshold_is_reported_as_it_is_and_the_page_clamps_the_line():
    db, _ = _db(130)
    assert cm.view(db, "jack")["uncompacted"] == 130


def test_a_guest_a_missing_person_or_no_datastore_is_an_empty_meter_never_an_error():
    threshold = mc.DEFAULT_COMPACTION_THRESHOLD
    db, collection = _db(50)
    assert cm.view(db, "guest_bty") == {"uncompacted": 0, "threshold": threshold} and collection.filters == []     # never even asked
    assert cm.view(db, "") == {"uncompacted": 0, "threshold": threshold} and cm.view(db, None) == {"uncompacted": 0, "threshold": threshold}
    assert cm.view(None, "jack") == {"uncompacted": 0, "threshold": threshold}


def test_a_count_that_cannot_be_read_is_an_empty_meter_and_a_warning_without_any_text(caplog):
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("datastore down with SECRET-NOTE-TEXT")

    with caplog.at_level("WARNING", logger="SASS Logger"):
        assert cm.view(Broken(), "jack") == {"uncompacted": 0, "threshold": mc.DEFAULT_COMPACTION_THRESHOLD}
    assert any("could not count the uncompacted notes" in record.message for record in caplog.records)
    assert not any("SECRET-NOTE-TEXT" in record.getMessage() for record in caplog.records)


def test_the_threshold_follows_the_environment_setting(monkeypatch):
    monkeypatch.setattr(cm, "DEFAULT_COMPACTION_THRESHOLD", 40)
    assert cm.view(_db(3)[0], "jack") == {"uncompacted": 3, "threshold": 40}


def test_the_route_is_behind_sign_in_off_the_event_loop_and_cannot_be_taken_for_a_note_id():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert '@app.get("/api/memory/compaction-meter")' in app
    assert "return await asyncio.to_thread(compaction_meter.view, get_db(), current_user.get(\"sub\"))" in app
    route = app[app.index('@app.get("/api/memory/compaction-meter")'):]
    assert "current_user = Depends(get_current_user)" in route.split("\n", 2)[1]
    assert '@app.get("/api/memory/{' not in app                                                       # no note-id GET that the path could be swallowed by
    assert "from backend.utils import compaction_meter" in app


def test_the_page_calls_the_route_the_server_serves():
    api = (ROOT / "local/src/api.ts").read_text(encoding="utf-8")
    assert "fetch(`${BASE_URL}/api/memory/compaction-meter`" in api and "  getCompactionMeter," in api
