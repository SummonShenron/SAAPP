"""Photos and files attached in a chat expire (backend/services/attachment_retention.py)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from bson import ObjectId

from backend.services import attachment_retention as ar


class _Files:
    def __init__(self, rows):
        self.rows, self.filter = rows, None

    def find(self, filt, projection=None):
        self.filter = filt
        return iter(self.rows)


class _Docs:
    def __init__(self):
        self.filter = None

    def delete_many(self, filt):
        self.filter = filt
        return SimpleNamespace(deleted_count=4)


class _Db(dict):
    pass


def _db(rows):
    return _Db({"fs.files": _Files(rows), "session_documents": _Docs()})


def test_only_old_chat_attachments_go_and_the_cutoff_is_the_retention_period(monkeypatch):
    db = _db([{"_id": "p1"}, {"_id": "p2"}])
    gridfs = Mock()
    monkeypatch.setattr(ar, "get_db", lambda: db)
    monkeypatch.setattr(ar, "GridFS", Mock(return_value=gridfs))

    before = datetime.now(timezone.utc)
    out = ar.prune_expired_attachments(30)

    assert out == {"skipped": False, "deleted_photos": 2, "deleted_file_texts": 4, "max_age_days": 30}
    assert [c.args[0] for c in gridfs.delete.call_args_list] == ["p1", "p2"]
    query = db["fs.files"].filter
    assert query["metadata.kind"] == "chat_attachment"                     # never a knowledge-base file
    assert before - timedelta(days=30, minutes=1) < query["uploadDate"]["$lt"] < before - timedelta(days=30) + timedelta(minutes=1)
    cutoff_id = db["session_documents"].filter["_id"]["$lt"]
    assert isinstance(cutoff_id, ObjectId) and abs((cutoff_id.generation_time - (before - timedelta(days=30))).total_seconds()) < 120


def test_zero_or_negative_days_turns_it_off_and_no_database_is_a_no_op(monkeypatch):
    called = Mock(side_effect=AssertionError("must not touch the database"))
    monkeypatch.setattr(ar, "get_db", called)
    assert ar.prune_expired_attachments(0)["skipped"] and ar.prune_expired_attachments(-5)["skipped"]
    monkeypatch.setattr(ar, "get_db", lambda: None)
    assert ar.prune_expired_attachments(30) == {"skipped": True, "reason": "USE_DB not enabled"}


def test_the_default_is_thirty_days_and_the_app_starts_and_stops_the_clean_up_loop():
    assert ar.DEFAULT_MAX_AGE_DAYS == 30
    source = open("app.py", encoding="utf-8").read()
    assert "spawn_background_task(run_attachment_retention_loop())" in source
    assert "attachment_retention_task.cancel()" in source


def test_the_privacy_page_and_account_page_say_how_long_attachments_are_kept():
    privacy = open("local/src/pages/Privacy.tsx", encoding="utf-8").read()
    account = open("local/src/pages/Account.tsx", encoding="utf-8").read()
    assert "30 days" in privacy and "30 days" in account
