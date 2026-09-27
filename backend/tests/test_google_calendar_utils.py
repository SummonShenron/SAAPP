from types import SimpleNamespace

import pytest

from backend.utils import google_calendar_utils as gcu


class _FakeCollection:
    """Minimal in-memory stand-in for a pymongo Collection — just enough of find_one/
    update_one($set/$setOnInsert/upsert)/delete_one/create_index for these tests."""

    def __init__(self):
        self.docs: dict[tuple, dict] = {}
        self.indexes = []

    def _key(self, query: dict):
        return tuple(sorted(query.items()))

    def find_one(self, query: dict):
        return self.docs.get(self._key(query))

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        key = self._key(query)
        is_new = key not in self.docs
        if is_new:
            if not upsert:
                return SimpleNamespace(matched_count=0)
            self.docs[key] = dict(query)
        doc = self.docs[key]
        doc.update(update.get("$set", {}))
        if is_new:
            for k, v in update.get("$setOnInsert", {}).items():
                doc.setdefault(k, v)
        return SimpleNamespace(matched_count=1)

    def delete_one(self, query: dict):
        key = self._key(query)
        existed = key in self.docs
        self.docs.pop(key, None)
        return SimpleNamespace(deleted_count=1 if existed else 0)

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))


class _FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(gcu, "get_db", lambda: db)
    return db


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(gcu, "get_db", lambda: None)


# ---------------------------------------------------------------------------
# ensure_indexes
# ---------------------------------------------------------------------------

def test_ensure_indexes_creates_expected_indexes(fake_db):
    gcu.ensure_indexes()
    connections = fake_db.collections["google_calendar_connections"]
    pending = fake_db.collections["google_calendar_oauth_pending"]
    assert connections.indexes == [(("username",), {"unique": True})]
    assert pending.indexes == [
        (("state",), {"unique": True}),
        (("created_at",), {"expireAfterSeconds": gcu._PENDING_TTL_SECONDS}),
    ]


def test_ensure_indexes_is_a_noop_without_a_db(no_db):
    gcu.ensure_indexes()  # must not raise


# ---------------------------------------------------------------------------
# connection status / save / update / delete
# ---------------------------------------------------------------------------

def test_get_connection_status_defaults_to_not_connected(fake_db):
    status = gcu.get_connection_status("jack")
    assert status == {"connected": False, "account_email": None, "scopes": [], "connected_at": None}


def test_save_and_get_connection_round_trip(fake_db):
    gcu.save_connection(
        "jack", account_email="jack@example.com", scopes=["calendar.events"],
        expires_at="2026-01-01", access_token_encrypted="enc-a", refresh_token_encrypted="enc-r",
    )
    status = gcu.get_connection_status("jack")
    assert status["connected"] is True
    assert status["account_email"] == "jack@example.com"
    assert status["scopes"] == ["calendar.events"]
    assert status["connected_at"] is not None


def test_save_connection_refuses_locked_identity(fake_db):
    gcu.save_connection(
        "guest_bty", account_email="x@example.com", scopes=[], expires_at=None,
        access_token_encrypted="enc-a", refresh_token_encrypted=None,
    )
    assert gcu.get_connection_status("guest_bty")["connected"] is False


def test_get_connection_status_locked_identity_never_touches_db(fake_db):
    # Even if a connection somehow exists for this key, locked identities must never see it.
    gcu._connections_collection().update_one(
        {"username": "guest-recruiter@example.com"}, {"$set": {"account_email": "leaked@example.com"}}, upsert=True,
    )
    assert gcu.get_connection_status("guest-recruiter@example.com")["connected"] is False


def test_get_encrypted_tokens_returns_none_for_locked_identity(fake_db):
    gcu._connections_collection().update_one(
        {"username": "guest_bty"}, {"$set": {"access_token_encrypted": "enc"}}, upsert=True,
    )
    assert gcu.get_encrypted_tokens("guest_bty") is None


def test_update_tokens_only_overwrites_refresh_token_when_given(fake_db):
    gcu.save_connection(
        "jack", account_email="jack@example.com", scopes=["calendar.events"],
        expires_at="2026-01-01", access_token_encrypted="enc-a-old", refresh_token_encrypted="enc-r-old",
    )
    gcu.update_tokens("jack", access_token_encrypted="enc-a-new", expires_at="2026-02-01")
    doc = gcu.get_encrypted_tokens("jack")
    assert doc["access_token_encrypted"] == "enc-a-new"
    assert doc["refresh_token_encrypted"] == "enc-r-old"  # untouched

    gcu.update_tokens("jack", access_token_encrypted="enc-a-newer", expires_at="2026-03-01", refresh_token_encrypted="enc-r-new")
    doc = gcu.get_encrypted_tokens("jack")
    assert doc["refresh_token_encrypted"] == "enc-r-new"


def test_delete_connection(fake_db):
    gcu.save_connection(
        "jack", account_email="jack@example.com", scopes=[], expires_at=None,
        access_token_encrypted="enc-a", refresh_token_encrypted=None,
    )
    assert gcu.delete_connection("jack") is True
    assert gcu.get_connection_status("jack")["connected"] is False
    assert gcu.delete_connection("jack") is False  # already gone


# ---------------------------------------------------------------------------
# pending OAuth state — the mechanism that survives the redirect
# ---------------------------------------------------------------------------

def test_pending_create_get_consume_round_trip(fake_db):
    gcu.create_pending("state-123", "jack", "verifier-abc", "https://app.example.com/#/integrations")
    pending = gcu.get_pending("state-123")
    assert pending["username"] == "jack"
    assert pending["code_verifier"] == "verifier-abc"
    assert pending["return_to"] == "https://app.example.com/#/integrations"

    gcu.consume_pending("state-123")
    assert gcu.get_pending("state-123") is None


def test_pending_lookup_for_unknown_state_returns_none(fake_db):
    assert gcu.get_pending("never-created") is None
