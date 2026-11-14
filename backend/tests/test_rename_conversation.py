from types import SimpleNamespace

from backend.utils import app_utils


class FakeConversations:
    def __init__(self, matched_count):
        self.matched_count = matched_count
        self.calls = []

    def update_one(self, query, update):
        self.calls.append((query, update))
        return SimpleNamespace(matched_count=self.matched_count)


class FakeDB:
    def __init__(self, matched_count):
        self.conversations = FakeConversations(matched_count)

    def __getitem__(self, name):
        assert name == "conversations"
        return self.conversations


def test_rename_sets_the_title_on_the_owners_thread(monkeypatch):
    db = FakeDB(1)
    monkeypatch.setattr(app_utils, "get_db", lambda: db)

    assert app_utils.rename_user_conversation("jack", "conv-1", "New name") is True
    assert db.conversations.calls == [
        ({"username": "jack", "session_id": "conv-1"}, {"$set": {"title": "New name"}}),
    ]


def test_rename_returns_false_when_no_thread_matches(monkeypatch):
    db = FakeDB(0)
    monkeypatch.setattr(app_utils, "get_db", lambda: db)

    assert app_utils.rename_user_conversation("jack", "missing", "New name") is False


def test_rename_returns_false_without_a_database(monkeypatch):
    monkeypatch.setattr(app_utils, "get_db", lambda: None)

    assert app_utils.rename_user_conversation("jack", "conv-1", "New name") is False
