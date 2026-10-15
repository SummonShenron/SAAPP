from types import SimpleNamespace

import pytest

from backend.utils import db_monitor as dm
from backend.utils import db_utils


class _Log:
    def __init__(self):
        self.lines = []

    def warning(self, message, *args):
        self.lines.append(message % args)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("DB_SLOW_COMMAND_SECONDS", raising=False)
    log = _Log()
    monkeypatch.setattr(dm, "logger", log)
    dm.reset()
    yield log
    dm.reset()


def started(request_id=1, name="find", collection="user_memory_facts", **extra):
    return SimpleNamespace(request_id=request_id, command_name=name, command={name: collection, **extra}, database_name="saapp_database")


def finished(request_id=1, name="find", seconds=0.01, reply=None):
    return SimpleNamespace(request_id=request_id, command_name=name, duration_micros=int(seconds * 1_000_000), reply=reply or {"ok": 1})


def test_a_slow_read_is_logged_with_its_command_collection_time_and_size(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(started())
    big = {"cursor": {"firstBatch": [{"blob": "x" * 1_000_000} for _ in range(4)]}}
    listener.succeeded(finished(seconds=38.2, reply=big))
    assert _clean.lines == ["[db-slow] find user_memory_facts took 38.2s (about 3.8 MB)"]


def test_a_fast_command_is_not_logged_and_costs_nothing_that_stays(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(started())
    listener.succeeded(finished(seconds=0.05))
    assert _clean.lines == [] and dm.slow_counts() == {} and listener._started == {}


def test_a_command_that_fails_slowly_is_logged_as_a_failure(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(started(name="update", collection="conversations"))
    listener.failed(finished(name="update", seconds=12.0))
    assert _clean.lines == ["[db-slow] update conversations failed after 12.0s"]


def test_nothing_that_was_asked_or_returned_reaches_the_log(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(started(filter={"username": "someone@example.com"}, projection={"messages": 1}))
    listener.succeeded(finished(seconds=5.0, reply={"cursor": {"firstBatch": [{"text": "PRIVATE-MESSAGE-TEXT"}]}}))
    assert len(_clean.lines) == 1
    assert "someone@example.com" not in _clean.lines[0] and "PRIVATE-MESSAGE-TEXT" not in _clean.lines[0]


def test_slow_commands_are_tallied_by_command_and_collection(_clean):
    listener = dm.SlowCommandLogger()
    for request_id, seconds in ((1, 3.0), (2, 4.5)):
        listener.started(started(request_id))
        listener.succeeded(finished(request_id, seconds=seconds))
    assert dm.slow_counts() == {"find user_memory_facts": {"count": 2, "seconds": 7.5}}


def test_the_threshold_is_two_seconds_unless_set_and_a_bad_setting_falls_back(monkeypatch):
    assert dm.slow_seconds() == 2.0
    monkeypatch.setenv("DB_SLOW_COMMAND_SECONDS", "0.5")
    assert dm.slow_seconds() == 0.5
    for bad in ("abc", "0", "-3", ""):
        monkeypatch.setenv("DB_SLOW_COMMAND_SECONDS", bad)
        assert dm.slow_seconds() == 2.0


def test_a_command_with_no_collection_is_named_by_its_database(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(SimpleNamespace(request_id=7, command_name="ping", command={"ping": 1}, database_name="admin"))
    listener.succeeded(finished(7, name="ping", seconds=9.0))
    assert _clean.lines[0].startswith("[db-slow] ping admin took 9.0s")


def test_commands_that_never_report_back_cannot_grow_the_table_without_end(monkeypatch):
    monkeypatch.setattr(dm, "MAX_IN_FLIGHT", 3)
    listener = dm.SlowCommandLogger()
    for request_id in range(10):
        listener.started(started(request_id))
    assert len(listener._started) == 3


def test_a_malformed_event_never_raises(_clean):
    listener = dm.SlowCommandLogger()
    listener.started(object())
    listener.succeeded(object())
    listener.failed(object())
    assert _clean.lines == []


def test_the_app_database_client_is_created_with_the_listener(monkeypatch):
    made = {}

    def fake_client(uri, **kwargs):
        made.update(kwargs)
        return {"saapp_database": object()}

    monkeypatch.setenv("USE_DB", "true")
    monkeypatch.setenv("MONGO_URI", "mongodb://example.invalid")
    monkeypatch.setattr(db_utils, "MongoClient", fake_client)
    monkeypatch.setattr(db_utils, "_client", None)
    db_utils.get_db()
    listeners = made["event_listeners"]
    assert len(listeners) == 1 and isinstance(listeners[0], dm.SlowCommandLogger)
    monkeypatch.setattr(db_utils, "_client", None)
