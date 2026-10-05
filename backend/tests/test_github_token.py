"""Per-user GitHub tokens (docs/coding-agent-roadmap.md, section 4).

The token used to be one global env var. A user can now supply their own, so Sonic acts on THEIR
repos as them. Because a bug here leaks one user's repo access to another, the cross-tenant cases
are tested explicitly, not just the happy path.
"""
import hashlib
import hmac
import re

import pytest
from cryptography.fernet import Fernet

from backend.services import agent_workflow as aw
from backend.services import github_service as gs
from backend.utils import github_audit, secret_utils
from backend.utils import user_settings_utils as uset
from backend.utils.webhook_utils import verify_github_signature

ALICE_TOKEN = "ghp_aliceAliceAliceAlice1234567890ab"
BOB_TOKEN = "ghp_bobBobBobBobBobBobBob1234567890cd"
SHARED_TOKEN = "ghp_sharedSharedSharedShared1234567890"


# ---------------------------------------------------------------------------------------------------
# Fakes: a user_settings collection that understands the queries this code actually makes.
# ---------------------------------------------------------------------------------------------------

class _FakeSettingsCollection:
    def __init__(self):
        self.docs = []
        self.projections = []

    def _matches(self, doc, filt):
        for key, cond in filt.items():
            value = doc.get(key)
            if isinstance(cond, dict):
                if "$regex" in cond:
                    flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                    if not isinstance(value, str) or not re.search(cond["$regex"], value, flags):
                        return False
                if "$exists" in cond and (key in doc) != cond["$exists"]:
                    return False
                if "$ne" in cond and value == cond["$ne"]:
                    return False
            elif value != cond:
                return False
        return True

    def _project(self, doc, projection):
        if not projection:
            return dict(doc)
        if any(v == 1 for k, v in projection.items() if k != "_id"):
            return {k: doc[k] for k, v in projection.items() if v == 1 and k in doc}
        return {k: v for k, v in doc.items() if projection.get(k) != 0}

    def find_one(self, filt, projection=None):
        self.projections.append(projection)
        for doc in self.docs:
            if self._matches(doc, filt):
                return self._project(doc, projection)
        return None

    def find(self, filt, projection=None):
        return [self._project(d, projection) for d in self.docs if self._matches(d, filt)]

    def update_one(self, filt, update, upsert=False):
        for doc in self.docs:
            if self._matches(doc, filt):
                doc.update(update["$set"])
                return
        if upsert:
            self.docs.append({**filt, **update["$set"]})


class _FakeDB:
    def __init__(self):
        self.user_settings = _FakeSettingsCollection()
        self.github_token_audit = _FakeSettingsCollection()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(uset, "DATA_DIR", str(tmp_path))


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(uset, "get_db", lambda: fake)
    monkeypatch.setattr(github_audit, "get_db", lambda: None)  # audit is checked via a spy below
    return fake


@pytest.fixture
def audits(monkeypatch):
    """Records every audit call (username, source, purpose, repo)."""
    recorded = []
    import backend.utils.github_audit as module
    monkeypatch.setattr(module, "audit_github_token_use", lambda *a, **k: recorded.append(a))
    return recorded


def _save(username, token, login=None):
    return uset.set_user_github_token(username, token, github_login=login)


# ---------------------------------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------------------------------

def test_secrets_round_trip_and_the_ciphertext_does_not_contain_the_secret():
    encrypted = secret_utils.encrypt_secret(ALICE_TOKEN)
    assert ALICE_TOKEN not in encrypted
    assert secret_utils.decrypt_secret(encrypted) == ALICE_TOKEN


def test_a_secret_encrypted_under_another_key_is_unreadable_not_a_crash(monkeypatch):
    encrypted = secret_utils.encrypt_secret(ALICE_TOKEN)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert secret_utils.decrypt_secret(encrypted) is None
    assert secret_utils.decrypt_secret("not even base64 ciphertext") is None


def test_nothing_is_stored_without_an_encryption_key(monkeypatch, db):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY")
    with pytest.raises(secret_utils.SecretStorageNotConfigured):
        _save("alice", ALICE_TOKEN)
    assert db.user_settings.docs == []


# ---------------------------------------------------------------------------------------------------
# Storage: encrypted at rest, never returned
# ---------------------------------------------------------------------------------------------------

def test_the_stored_document_never_contains_the_plaintext_token(db):
    _save("alice", ALICE_TOKEN, login="alice-gh")
    stored = db.user_settings.docs[0]
    assert ALICE_TOKEN not in repr(stored)
    assert stored["github_token_last4"] == ALICE_TOKEN[-4:]
    assert uset.get_user_github_token("alice") == ALICE_TOKEN


def test_the_local_settings_file_holds_only_the_encrypted_form(monkeypatch, tmp_path):
    monkeypatch.setattr(uset, "get_db", lambda: None)
    _save("alice", ALICE_TOKEN)
    on_disk = (tmp_path / "alice.json").read_text()
    assert ALICE_TOKEN not in on_disk
    assert uset.get_user_github_token("alice") == ALICE_TOKEN  # still readable through the store


def test_the_status_the_browser_sees_never_contains_the_token(db):
    _save("alice", ALICE_TOKEN, login="alice-gh")
    status = uset.get_github_token_status("alice")
    assert status == {"configured": True, "last4": ALICE_TOKEN[-4:], "github_login": "alice-gh"}
    assert ALICE_TOKEN not in repr(status)
    assert uset.get_github_token_status("nobody") == {"configured": False, "last4": None, "github_login": None}


def test_ordinary_settings_reads_never_load_the_encrypted_token(db):
    _save("alice", ALICE_TOKEN)
    db.user_settings.projections.clear()
    uset.get_user_settings_bundle("alice")
    uset.get_user_target_repo("alice")
    assert db.user_settings.projections, "expected the settings reads to hit the store"
    assert all(p == {"github_token_encrypted": 0} for p in db.user_settings.projections)
    assert ALICE_TOKEN not in repr(uset.get_user_settings_bundle("alice"))


def test_removing_a_token_clears_it_completely(db):
    _save("alice", ALICE_TOKEN, login="alice-gh")
    status = _save("alice", "")
    assert status["configured"] is False
    assert uset.get_user_github_token("alice") is None
    assert db.user_settings.docs[0]["github_token_encrypted"] is None


@pytest.mark.parametrize("junk", ["short", "has spaces inside the token value", "ghp_" + "x" * 300, "ghp_!!!!!!!!!!!!!!!!!!!!!!"])
def test_a_value_not_shaped_like_a_token_is_refused(db, junk):
    with pytest.raises(uset.GitHubTokenInvalid):
        _save("alice", junk)
    assert db.user_settings.docs == []


@pytest.mark.parametrize("shared_identity", ["guest", "guest_bty", "guest-recruiter@example.com"])
def test_shared_guest_identities_cannot_hold_a_token(db, shared_identity):
    with pytest.raises(uset.GitHubTokenNotAllowed):
        _save(shared_identity, ALICE_TOKEN)
    # even a planted record is ignored for them: it would be used by every visitor
    db.user_settings.docs.append({"username": shared_identity, "github_token_encrypted": secret_utils.encrypt_secret(ALICE_TOKEN)})
    assert uset.get_user_github_token(shared_identity) is None


# ---------------------------------------------------------------------------------------------------
# Cross-tenant: one user's token is never visible to, or used for, another
# ---------------------------------------------------------------------------------------------------

def test_each_user_gets_only_their_own_token_and_everyone_else_gets_the_shared_one(db, monkeypatch, audits):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    _save("alice", ALICE_TOKEN)
    _save("bob", BOB_TOKEN)

    assert uset.resolve_github_token("alice") == ALICE_TOKEN
    assert uset.resolve_github_token("bob") == BOB_TOKEN
    assert uset.resolve_github_token("carol") == SHARED_TOKEN  # no token of her own
    assert uset.resolve_github_token(None) == SHARED_TOKEN

    for user in ("bob", "carol"):
        assert uset.get_user_github_token(user) != ALICE_TOKEN
    assert uset.get_user_github_token("carol") is None


def test_removing_one_users_token_does_not_touch_another_users(db):
    _save("alice", ALICE_TOKEN)
    _save("bob", BOB_TOKEN)
    _save("alice", "")
    assert uset.get_user_github_token("alice") is None
    assert uset.get_user_github_token("bob") == BOB_TOKEN


def test_resolution_prefers_the_users_own_token_over_the_shared_one(db, monkeypatch, audits):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    _save("alice", ALICE_TOKEN)
    assert uset.resolve_github_token("alice", purpose="create_issue", repo="alice/app") == ALICE_TOKEN
    assert audits == [("alice", "user", "create_issue", "alice/app")]


def test_the_shared_token_is_the_fallback_and_is_audited_as_shared(db, monkeypatch, audits):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    assert uset.resolve_github_token("carol", purpose="tool_agent_turn", repo="x/y") == SHARED_TOKEN
    assert audits == [("carol", "shared", "tool_agent_turn", "x/y")]


def test_with_no_token_anywhere_nothing_is_returned_or_audited(db, audits):
    assert uset.resolve_github_token("carol") is None
    assert audits == []


def test_an_undecryptable_stored_token_falls_back_instead_of_breaking_the_request(db, monkeypatch, audits):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    _save("alice", ALICE_TOKEN)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())  # key rotated
    assert uset.resolve_github_token("alice") == SHARED_TOKEN


def test_the_audit_trail_records_who_what_and_where_but_never_the_token(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(github_audit, "get_db", lambda: fake)
    fake.github_token_audit.create_index = lambda *a, **k: None
    fake.github_token_audit.insert_one = lambda doc: fake.github_token_audit.docs.append(doc)
    monkeypatch.setattr(github_audit, "_index_ready", False)

    github_audit.audit_github_token_use("alice", "user", "create_pr", "alice/app")

    entry = fake.github_token_audit.docs[0]
    assert (entry["username"], entry["source"], entry["purpose"], entry["repo"]) == ("alice", "user", "create_pr", "alice/app")
    assert "at" in entry
    assert ALICE_TOKEN not in repr(entry)


def test_a_failing_audit_store_never_breaks_a_request(monkeypatch):
    def boom():
        raise RuntimeError("mongo down")
    monkeypatch.setattr(github_audit, "get_db", boom)
    github_audit.audit_github_token_use("alice", "user", "create_pr", "alice/app")  # must not raise


# ---------------------------------------------------------------------------------------------------
# Webhook: pick a token only from users who pinned the repo AND supplied one
# ---------------------------------------------------------------------------------------------------

def _pin(db, username, repo):
    uset.set_user_target_repo(username, repo)


def test_the_webhook_uses_the_token_of_a_user_who_pinned_that_repo(db):
    _save("alice", ALICE_TOKEN, login="alice-gh")
    _pin(db, "alice", "alice-gh/app")
    assert uset.find_github_token_for_repo("alice-gh/app") == ("alice", ALICE_TOKEN)


def test_the_repo_match_ignores_case(db):
    _save("alice", ALICE_TOKEN)
    _pin(db, "alice", "Alice-GH/App")
    assert uset.find_github_token_for_repo("alice-gh/app")[0] == "alice"


def test_a_user_who_pinned_the_repo_without_a_token_is_not_used(db):
    _pin(db, "alice", "alice-gh/app")  # pinned, but never supplied a token
    assert uset.find_github_token_for_repo("alice-gh/app") == (None, None)


def test_another_users_token_is_never_used_for_a_repo_they_did_not_pin(db):
    _save("alice", ALICE_TOKEN)
    _pin(db, "alice", "alice-gh/app")
    _save("bob", BOB_TOKEN)
    _pin(db, "bob", "bob-gh/other")
    assert uset.find_github_token_for_repo("bob-gh/other") == ("bob", BOB_TOKEN)
    assert uset.find_github_token_for_repo("somebody/else") == (None, None)


def test_when_several_users_pinned_the_repo_the_owner_wins(db):
    _save("zed", BOB_TOKEN, login="someone-else")
    _pin(db, "zed", "acme/app")
    _save("amy", ALICE_TOKEN, login="acme")
    _pin(db, "amy", "acme/app")
    assert uset.find_github_token_for_repo("acme/app")[0] == "amy"


def test_the_shared_env_token_is_never_returned_by_the_webhook_lookup(db, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    assert uset.find_github_token_for_repo("nobody/pinned-this") == (None, None)


def test_an_unreadable_token_is_skipped_for_the_next_candidate(db, monkeypatch):
    _save("amy", ALICE_TOKEN, login="acme")
    _pin(db, "amy", "acme/app")
    db.user_settings.docs[0]["github_token_encrypted"] = "garbage"
    _save("bea", BOB_TOKEN, login="someone")
    _pin(db, "bea", "acme/app")
    assert uset.find_github_token_for_repo("acme/app") == ("bea", BOB_TOKEN)


def test_webhook_signatures_are_checked_in_constant_time_form():
    body = b'{"action":"opened","number":7}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_github_signature("s3cret", body, good) is True
    assert verify_github_signature("wrong", body, good) is False
    assert verify_github_signature("s3cret", body + b" ", good) is False  # tampered body
    assert verify_github_signature("s3cret", body, None) is False
    assert verify_github_signature("s3cret", body, good.replace("sha256=", "sha1=")) is False
    assert verify_github_signature("", body, good) is False


# ---------------------------------------------------------------------------------------------------
# Saving: verified with GitHub before it is ever stored
# ---------------------------------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def _github_says(monkeypatch, status, payload=None, raises=None):
    def fake_get(url, headers=None, timeout=None):
        if raises:
            raise raises
        assert url == "https://api.github.com/user"
        assert headers["Authorization"].startswith("Bearer ")
        return _Resp(status, payload)
    monkeypatch.setattr(gs.requests, "get", fake_get)


def test_a_valid_token_is_stored_with_the_github_login(db, monkeypatch):
    _github_says(monkeypatch, 200, {"login": "alice-gh"})
    status = gs.verify_and_store_github_token("alice", ALICE_TOKEN)
    assert status == {"configured": True, "last4": ALICE_TOKEN[-4:], "github_login": "alice-gh"}
    assert uset.get_user_github_token("alice") == ALICE_TOKEN


def test_a_token_github_rejects_is_not_stored(db, monkeypatch):
    _github_says(monkeypatch, 401)
    with pytest.raises(gs.GitHubTokenRejected):
        gs.verify_and_store_github_token("alice", ALICE_TOKEN)
    assert db.user_settings.docs == []


@pytest.mark.parametrize("outcome", [dict(status=500), dict(status=403), dict(status=0, raises=gs.requests.ConnectionError("down"))])
def test_when_github_cannot_confirm_the_token_it_is_not_stored_and_the_user_can_retry(db, monkeypatch, outcome):
    _github_says(monkeypatch, **outcome)
    with pytest.raises(gs.GitHubUnreachable):
        gs.verify_and_store_github_token("alice", ALICE_TOKEN)
    assert db.user_settings.docs == []


def test_a_malformed_token_never_reaches_github(db, monkeypatch):
    calls = []
    monkeypatch.setattr(gs.requests, "get", lambda *a, **k: calls.append(1))
    with pytest.raises(uset.GitHubTokenInvalid):
        gs.verify_and_store_github_token("alice", "nope")
    assert calls == []


def test_a_shared_guest_identity_is_refused_before_anything_is_sent_to_github(db, monkeypatch):
    calls = []
    monkeypatch.setattr(gs.requests, "get", lambda *a, **k: calls.append(1))
    with pytest.raises(uset.GitHubTokenNotAllowed):
        gs.verify_and_store_github_token("guest", ALICE_TOKEN)
    assert calls == []


def test_an_empty_token_removes_the_saved_one_without_calling_github(db, monkeypatch):
    _github_says(monkeypatch, 200, {"login": "alice-gh"})
    gs.verify_and_store_github_token("alice", ALICE_TOKEN)
    calls = []
    monkeypatch.setattr(gs.requests, "get", lambda *a, **k: calls.append(1))
    assert gs.verify_and_store_github_token("alice", "  ")["configured"] is False
    assert calls == []


# ---------------------------------------------------------------------------------------------------
# The agent: every flow that talks to GitHub uses the right user's token
# ---------------------------------------------------------------------------------------------------

def test_creating_an_issue_uses_the_requesting_users_own_token(db, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    _save("alice", ALICE_TOKEN)
    _save("bob", BOB_TOKEN)
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(headers["Authorization"])
        return _Resp(201, {"number": 1, "html_url": "https://github.com/x/y/issues/1"})

    monkeypatch.setattr(aw.requests, "post", fake_post)
    details = {"repo": "x/y", "title": "t", "body": "b"}

    aw._execute_create_issue("alice", details)
    aw._execute_create_issue("bob", details)
    aw._execute_create_issue("carol", details)  # no token of her own

    assert sent == [f"Bearer {ALICE_TOKEN}", f"Bearer {BOB_TOKEN}", f"Bearer {SHARED_TOKEN}"]


def test_creating_a_pull_request_uses_the_requesting_users_own_token(db, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    _save("alice", ALICE_TOKEN)
    sent = []

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append(headers["Authorization"])
        return _Resp(201, {"number": 2, "html_url": "https://github.com/x/y/pull/2"})

    monkeypatch.setattr(aw.requests, "post", fake_post)
    aw._execute_create_pr("alice", {"repo": "x/y", "title": "t", "body": "b", "head_branch": "feat", "base_branch": "main"})

    assert sent == [f"Bearer {ALICE_TOKEN}"]


def test_the_branch_diff_uses_the_token_it_is_given_and_only_falls_back_to_the_env_var(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", SHARED_TOKEN)
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        return _Resp(404)

    monkeypatch.setattr(aw.requests, "get", fake_get)
    aw.fetch_branch_diff_summary("x/y", "main", "feat", token=ALICE_TOKEN)
    aw.fetch_branch_diff_summary("x/y", "main", "feat")

    assert seen == [f"Bearer {ALICE_TOKEN}", f"Bearer {SHARED_TOKEN}"]


def test_no_agent_flow_reads_the_global_env_token_directly_any_more():
    import pathlib
    source = pathlib.Path(aw.__file__).read_text(encoding="utf-8")
    direct_reads = [line.strip() for line in source.splitlines() if 'os.getenv("GITHUB_TOKEN")' in line]
    # The one deliberate fallback is fetch_branch_diff_summary's default for callers that pass no token.
    assert direct_reads == ['token = token or os.getenv("GITHUB_TOKEN")']
