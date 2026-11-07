"""The admin side of Sonic's knowledge base: the endpoints an admin approves, rejects and removes cards with (app.py), and the rule that no upload can write to the shared library."""
import asyncio

import pytest
from fastapi import HTTPException

import app as app_module
from backend.tests.fake_kb_db import Db
from backend.utils import isolation_kb_utils
from backend.utils import knowledge_base as kb

ADMIN = {"sub": "boss"}
BODY = "The harbour town keeps a tide table on its council website."


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(monkeypatch):
    fake = Db()
    monkeypatch.setattr(app_module, "_admin_db", lambda: fake)
    monkeypatch.setattr(kb, "record_turn_counts", lambda batch, now=None: None)
    monkeypatch.setattr("backend.utils.embedding_utils.embed_text", lambda text: [0.1, 0.2])
    kb._look_again()
    yield fake
    kb._look_again()


def pending(db, url="https://example.org/a"):
    return kb.add_pending(db, url=url, card={"title": "Harbour tides", "kind": "article", "body": BODY}, digest="h-" + url)["_id"]


def test_every_knowledge_endpoint_is_behind_the_global_admin_check():
    source = open("app.py", encoding="utf-8").read()
    for route in ('@app.get("/api/admin/knowledge")', '@app.post("/api/admin/knowledge/{key}/approve")', '@app.post("/api/admin/knowledge/{key}/reject")', '@app.post("/api/admin/knowledge/{key}/remove")'):
        start = source.index(route)
        assert "Depends(require_global_admin)" in source[start:source.index("\n\n", start)], route


def test_the_view_lists_what_waits_and_what_is_live(db):
    key = pending(db)
    view = run(app_module.get_learned_knowledge(admin=ADMIN))
    assert [c["key"] for c in view["pending"]] == [key] and view["live"] == []


def test_approving_makes_the_card_live_embedded_and_signed_by_the_admin_and_returns_the_new_view(db):
    key = pending(db)
    view = run(app_module.approve_learned_knowledge(key, admin=ADMIN))
    assert view["pending"] == [] and [c["key"] for c in view["live"]] == [key]
    stored = db[kb.DOCS].docs[0]
    assert stored["embedding"] == [0.1, 0.2] and stored["kb"]["approved_by"] == "boss" and stored["affiliate"] == "Sonic_Global"


def test_a_card_that_cannot_be_embedded_is_a_400_and_stays_waiting(db, monkeypatch):
    key = pending(db)
    monkeypatch.setattr("backend.utils.embedding_utils.embed_text", lambda text: None)
    with pytest.raises(HTTPException) as caught:
        run(app_module.approve_learned_knowledge(key, admin=ADMIN))
    assert caught.value.status_code == 400 and "embedded" in caught.value.detail
    assert len(db[kb.PENDING].docs) == 1 and db[kb.DOCS].docs == []


def test_rejecting_and_removing_work_and_an_unknown_card_is_a_404(db):
    key = pending(db)
    assert run(app_module.reject_learned_knowledge(key, admin=ADMIN))["pending"] == []
    other = pending(db, "https://example.org/b")
    run(app_module.approve_learned_knowledge(other, admin=ADMIN))
    assert run(app_module.remove_learned_knowledge(other, admin=ADMIN))["live"] == []
    for call in (app_module.reject_learned_knowledge, app_module.remove_learned_knowledge, app_module.approve_learned_knowledge):
        with pytest.raises(HTTPException) as caught:
            run(call("nope", admin=ADMIN))
        assert caught.value.status_code in (400, 404)


def test_no_upload_can_write_to_the_shared_library_not_even_an_admins(monkeypatch):
    monkeypatch.setattr(isolation_kb_utils, "load_user_directory_groups", lambda username: ["Global_Admins"])
    assert isolation_kb_utils.verify_user_ingest_access("boss", "Affiliate_A") is True
    assert isolation_kb_utils.verify_user_ingest_access("boss", kb.AFFILIATE) is False
    monkeypatch.setattr(isolation_kb_utils, "load_user_directory_groups", lambda username: [f"{kb.AFFILIATE} Ingesters"])
    assert isolation_kb_utils.verify_user_ingest_access("someone", kb.AFFILIATE) is False


def test_the_upload_guard_names_the_same_library_the_store_writes_to():
    assert 'affiliate == "Sonic_Global"' in open(isolation_kb_utils.__file__, encoding="utf-8").read() and kb.AFFILIATE == "Sonic_Global"
