"""Pages Sonic read on its own, kept as candidates for its knowledge base (backend/utils/kb_candidates.py), and the admin's Learn / Dismiss (backend/services/knowledge_cards.py learn_candidate, app.py).

The text of pages and addresses below is made up. What matters: capture is generous and free (an address, nothing fetched), it keeps nothing about who read it, it refuses what cannot be a public page, and Learn goes through the
same proposal as everything else so the card still needs its own approval."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app as app_module
from backend.services import agent_workflow as aw
from backend.services import knowledge_cards as cards
from backend.tests.fake_kb_db import Db
from backend.tests.test_knowledge_cards import BODY, PAGE, URL, Model, page
from backend.utils import kb_candidates as kc
from backend.utils import knowledge_base as kb

NOW = datetime.now(timezone.utc)
ADMIN = {"sub": "boss"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def counts(monkeypatch):
    seen = {}
    monkeypatch.setattr(kb, "record_turn_counts", lambda batch, now=None: [seen.__setitem__(k, seen.get(k, 0) + v) for k, v in batch.items()])
    return seen


@pytest.fixture
def db():
    return Db()


# ---- which addresses can be candidates -------------------------------------------------------------------------------------------------------

def test_a_public_page_is_kept_without_its_query_fragment_port_or_trailing_slash():
    assert kc.clean_url("https://Tides.Example.org:8443/harbour/?id=7&token=abc#top") == "https://tides.example.org/harbour"
    assert kc.clean_url("tides.example.org/harbour/") == "https://tides.example.org/harbour"          # a missing scheme is read as https, as read_url does
    assert kc.clean_url("https://tides.example.org") == "https://tides.example.org"


def test_what_cannot_be_a_public_page_is_dropped():
    for url in (
        "http://tides.example.org/harbour",                                    # not https
        "https://user:pass@tides.example.org/harbour",                         # a name and password in the address
        "https://localhost/harbour", "https://printer.local/status", "https://wiki.internal/page", "https://intranet/home",
        "https://192.168.1.5/admin", "https://10.0.0.2/x", "https://[::1]/x", "https://8.8.8.8/x",
        "https://docs.google.com/document/d/abc/edit", "https://drive.google.com/file/d/abc", "https://team.atlassian.net/wiki",
        "https://tides.example.org/login", "https://tides.example.org/oauth/callback?code=1", "https://tides.example.org/account/settings",
        "https://tides.example.org/share/" + "a" * 40,                         # a long token in the path
        "", None, "not a url at all", "https://tides.example.org/" + "a" * (kb.MAX_URL_CHARS + 10),
    ):
        assert kc.clean_url(url) is None, url


# ---- noting a read ---------------------------------------------------------------------------------------------------------------------------

def test_a_first_read_is_new_a_second_is_a_repeat_and_nothing_but_an_address_a_domain_and_times_is_kept(db, counts):
    assert kc.record(db, "https://tides.example.org/harbour?utm_source=x", NOW) == "new"
    assert kc.record(db, "https://tides.example.org/harbour/", NOW + timedelta(hours=1)) == "repeat"
    (doc,) = db[kc.COLLECTION].docs
    assert set(doc) == {"_id", "url", "domain", "status", "seen", "first_seen", "last_seen"}                  # no person, no question, no page text
    assert doc["url"] == "https://tides.example.org/harbour" and doc["domain"] == "tides.example.org" and doc["seen"] == 2 and doc["status"] == "open"
    assert counts == {"kb_candidate_seen": 1, "kb_candidate_repeat": 1}


def test_an_address_that_cannot_be_a_page_or_that_the_base_already_has_is_skipped_and_counted(db, counts):
    assert kc.record(db, "https://docs.google.com/document/d/abc/edit") == "skipped"
    assert kc.record(db, "https://www.reddit.com/r/x/comments/1") == "skipped"                              # a source the base never reads
    kb.add_pending(db, url="https://tides.example.org/waiting", card={"title": "t", "kind": "article", "body": BODY}, digest="h1")
    assert kc.record(db, "https://tides.example.org/waiting") == "skipped"                                  # already waiting for approval
    kb._tombstone(db, kb.card_key("https://tides.example.org/gone"), "https://tides.example.org/gone", "h2", kb.REJECTED, NOW)
    assert kc.record(db, "https://tides.example.org/gone") == "skipped"                                     # an admin turned it away
    assert kc.record(None, "https://tides.example.org/x") == "skipped" and db[kc.COLLECTION].docs == []
    assert counts == {"kb_candidate_skipped": 4}


def test_a_page_the_base_holds_is_not_suggested(db, counts):
    key = kb.card_key("https://tides.example.org/live")
    db[kb.DOCS].insert_one({"_id": "d1", "affiliate": kb.AFFILIATE, "kb": {"key": key, "url": "https://tides.example.org/live", "status": kb.LIVE}})
    assert kc.record(db, "https://tides.example.org/live") == "skipped"


def test_a_dismissed_page_is_not_suggested_again_and_its_count_does_not_grow(db, counts):
    kc.record(db, "https://tides.example.org/harbour", NOW)
    key = kc.open_list(db)[0]["key"]
    assert kc.dismiss(db, key, NOW) is True and kc.dismiss(db, key) is False                                   # only an open one can be dismissed
    assert kc.record(db, "https://tides.example.org/harbour", NOW + timedelta(days=1)) == "dismissed"
    assert db[kc.COLLECTION].docs[0]["seen"] == 1 and kc.open_list(db) == [] and counts["kb_candidate_dismissed"] == 1


def test_an_unread_candidate_expires_a_dismissal_lasts_longer_and_a_full_list_drops_its_weakest(db, counts, monkeypatch):
    kc.record(db, "https://tides.example.org/old", NOW - timedelta(days=kc.CANDIDATE_DAYS + 1))
    kc.record(db, "https://tides.example.org/fresh", NOW)
    assert [c["url"] for c in kc.open_list(db, NOW)] == ["https://tides.example.org/fresh"]                  # the stale one was pruned by the listing
    long_ago = NOW - timedelta(days=kc.CANDIDATE_DAYS + 5)
    kc.record(db, "https://tides.example.org/dismissed", long_ago)
    kc.dismiss(db, kb.card_key("https://tides.example.org/dismissed"), long_ago)
    assert kc.prune(db, NOW) == 0                                                                           # a dismissal is kept for DISMISSED_DAYS, longer than an open candidate
    assert kc.prune(db, NOW + timedelta(days=kc.DISMISSED_DAYS)) >= 1
    monkeypatch.setattr(kc, "MAX_CANDIDATES", 3)
    fresh = Db()
    for n, reads in enumerate((1, 3, 1)):
        for _ in range(reads):
            kc.record(fresh, f"https://tides.example.org/p{n}", NOW + timedelta(minutes=n))
    kc.record(fresh, "https://tides.example.org/p9", NOW + timedelta(hours=1))                              # a fourth: the least-read, oldest one (p0) makes room
    assert sorted(c["url"][-2:] for c in kc.open_list(fresh, NOW + timedelta(hours=1))) == ["p1", "p2", "p9"]


def test_the_list_is_most_read_first_then_most_recent(db):
    for path, reads, minute in (("a", 1, 0), ("b", 3, 1), ("c", 1, 5), ("d", 3, 0)):
        for _ in range(reads):
            kc.record(db, f"https://tides.example.org/{path}", NOW + timedelta(minutes=minute))
    assert [c["url"][-1] for c in kc.open_list(db, NOW + timedelta(hours=1))] == ["b", "d", "c", "a"]


# ---- Learn -----------------------------------------------------------------------------------------------------------------------------------

def seeded(db):
    kc.record(db, URL, NOW)
    return kc.open_list(db)[0]["key"]


def learn(db, key, model, **kwargs):
    return run(cards.learn_candidate(db, key, model, admin_name="boss", **kwargs))


def test_learn_runs_the_same_proposal_as_an_admin_and_the_card_waits_for_its_own_approval(db, counts):
    key = seeded(db)
    model = Model()
    outcome = learn(db, key, model, fetch=page(), now=NOW)
    assert outcome["ok"] is True and "waiting for your approval" in outcome["message"]
    assert kc.open_list(db) == [] and counts["kb_candidate_learned"] == 1 and counts["kb_proposed"] == 1
    (waiting,) = db[kb.PENDING].docs
    assert waiting["by"] == "admin" and db[kb.DOCS].docs == []                                              # pending only: nothing is readable until the second approval
    assert len(model.prompts) == 2                                                                           # the card, then the support check: nothing more


def test_a_page_that_will_not_read_stays_with_the_reason_and_costs_no_model_call(db, counts):
    key = seeded(db)
    model = Model()
    outcome = learn(db, key, model, fetch=page(text="Too short."), now=NOW)
    assert outcome["ok"] is False and "too little readable text" in outcome["message"] and model.prompts == []
    (row,) = kc.open_list(db)
    assert "too little readable text" in row["problem"] and "kb_candidate_learned" not in counts


def test_a_card_the_page_does_not_support_stays_with_the_reason(db, counts):
    key = seeded(db)
    outcome = learn(db, key, Model(supported=False), fetch=page(), now=NOW)
    assert outcome["ok"] is False and "did not support" in outcome["message"] and db[kb.PENDING].docs == [] and "did not support" in kc.open_list(db)[0]["problem"]


def test_a_page_that_became_known_or_was_turned_away_is_removed_not_kept(db, counts):
    key = seeded(db)
    kb._tombstone(db, kb.card_key(URL), URL, "h", kb.REJECTED, NOW)                                         # an admin turned it away after it was noted
    outcome = learn(db, key, Model(), fetch=page(), now=NOW)
    assert outcome["ok"] is False and "turned that page away" in outcome["message"] and kc.open_list(db) == []


def test_an_unknown_or_already_dismissed_candidate_cannot_be_learned(db):
    key = seeded(db)
    assert learn(db, "nope", Model(), fetch=page())["message"] == "No such candidate page."
    kc.dismiss(db, key)
    assert learn(db, key, Model(), fetch=page())["ok"] is False and learn(None, key, Model())["ok"] is False


def test_an_admin_is_not_held_to_the_daily_proposal_caps(db, counts):
    for n in range(kb.DAILY_PROPOSALS + 2):
        url = f"https://tides.example.org/p{n}"
        kc.record(db, url, NOW)
        key = kb.card_key(url)
        kb_key = kc.open_list(db)[0]["key"] if n == 0 else key
        text = PAGE + f" distinct page number {n} " * 5
        assert learn(db, kb_key, Model(), fetch=lambda u, t=text, f=url: page(text=t, final_url=f)(u), now=NOW)["ok"] is True, n


# ---- the endpoints ---------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def admin_db(monkeypatch):
    fake = Db()
    monkeypatch.setattr(app_module, "_admin_db", lambda: fake)
    monkeypatch.setattr(kb, "record_turn_counts", lambda batch, now=None: None)
    kb._look_again()
    yield fake
    kb._look_again()


def test_both_candidate_endpoints_are_behind_the_global_admin_check():
    source = open("app.py", encoding="utf-8").read()
    for route in ('@app.post("/api/admin/knowledge/candidates/{key}/learn")', '@app.post("/api/admin/knowledge/candidates/{key}/dismiss")'):
        start = source.index(route)
        assert "Depends(require_global_admin)" in source[start:source.index("\n\n", start)], route


def test_the_admin_view_lists_the_candidates_and_dismiss_returns_the_new_view(admin_db):
    kc.record(admin_db, "https://tides.example.org/harbour", NOW)
    view = run(app_module.get_learned_knowledge(admin=ADMIN))
    assert [c["url"] for c in view["candidates"]] == ["https://tides.example.org/harbour"] and view["candidates"][0]["seen"] == 1
    after = run(app_module.dismiss_knowledge_candidate(view["candidates"][0]["key"], admin=ADMIN))
    assert after["candidates"] == []
    with pytest.raises(HTTPException) as caught:
        run(app_module.dismiss_knowledge_candidate("nope", admin=ADMIN))
    assert caught.value.status_code == 404


def test_learn_through_the_endpoint_is_a_400_with_the_reason_when_it_cannot_and_the_new_view_when_it_can(admin_db, monkeypatch):
    kc.record(admin_db, URL, NOW)
    key = kc.open_list(admin_db)[0]["key"]
    model = Model()
    monkeypatch.setattr("backend.models.models.lite_llm", type("L", (), {"ainvoke": staticmethod(model)})())
    monkeypatch.setattr("backend.services.url_reader.try_lightweight_fetch", page(text="Too short."))          # default_fetch imports this when it runs
    with pytest.raises(HTTPException) as caught:
        run(app_module.learn_knowledge_candidate(key, admin=ADMIN))
    assert caught.value.status_code == 400 and "too little readable text" in caught.value.detail
    monkeypatch.setattr("backend.services.url_reader.try_lightweight_fetch", page())
    view = run(app_module.learn_knowledge_candidate(key, admin=ADMIN))
    assert view["candidates"] == [] and len(view["pending"]) == 1 and view["live"] == []


# ---- the capture: what the tool agent does when it reads a page --------------------------------------------------------------------------------

async def _read(monkeypatch, *, username="jack", risk="none", result="Some page text that is long enough.", tool="read_url", url="https://tides.example.org/harbour", boom=False):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state
    from backend.services import repo_checkout as rc

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    monkeypatch.setattr(aw, "read_url", AsyncMock(return_value=result))
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value=result))
    monkeypatch.setattr(aw, "_get_browser_session", AsyncMock(), raising=False)
    monkeypatch.setattr(aw, "get_db", lambda: "the-db")
    monkeypatch.setattr(aw, "effective_risk", lambda *a, **k: risk)
    noted = []
    def fake_record(database, address, *a, **k):
        if boom:
            raise RuntimeError("the database is down")
        noted.append((database, address))
        return "new"

    monkeypatch.setattr(aw.kb_candidates, "record", fake_record)

    async def fake_native_run(**kwargs):
        seen["observation"] = await kwargs["act"]({"tool_action": tool, "args": {"url": url}})
        return {"final_answer": "done", "attempts": [], "show_work": False}

    seen = {}
    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    await aw.tool_agent_node({**_state("read this page", username=username)})
    return noted, seen


def test_a_page_read_for_a_signed_in_member_is_noted_with_nothing_but_its_address(monkeypatch):
    noted, seen = run(_read(monkeypatch))
    assert noted == [("the-db", "https://tides.example.org/harbour")] and seen["observation"] == "Some page text that is long enough."      # the model still gets the page


def test_the_browser_tool_notes_its_page_too(monkeypatch):
    noted, _ = run(_read(monkeypatch, tool="browser_navigate"))
    assert noted == [("the-db", "https://tides.example.org/harbour")]


def test_a_failed_read_a_guest_and_a_turn_with_a_safety_concern_note_nothing(monkeypatch):
    assert run(_read(monkeypatch, result="ERROR: could not read https://tides.example.org/harbour: timed out"))[0] == []
    assert run(_read(monkeypatch, username="guest_bty"))[0] == []
    assert run(_read(monkeypatch, risk="elevated"))[0] == []
    assert run(_read(monkeypatch, url=""))[0] == []


def test_a_noting_that_fails_never_breaks_the_read(monkeypatch):
    noted, seen = run(_read(monkeypatch, boom=True))
    assert noted == [] and seen["observation"] == "Some page text that is long enough."      # the store raised; the model still got its page


# ---- the wiring and what is logged -----------------------------------------------------------------------------------------------------------

def test_every_candidate_counter_is_one_the_tally_accepts_and_each_is_recorded():
    from backend.utils import self_counters

    keys = {"kb_candidate_seen", "kb_candidate_repeat", "kb_candidate_skipped", "kb_candidate_learned", "kb_candidate_dismissed"}
    assert keys <= set(self_counters.KB_KEYS) and keys <= set(self_counters.KEYS)
    source = "".join(open(path, encoding="utf-8").read() for path in ("backend/utils/kb_candidates.py", "backend/services/knowledge_cards.py"))
    for key in keys:
        assert f'"{key}"' in source, f"{key} is never recorded"


def test_the_candidate_code_logs_no_address_and_the_frontend_calls_both_endpoints():
    import ast

    for path in ("backend/utils/kb_candidates.py",):
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant), "a log line that formats something in"
    api = open("local/src/api.ts", encoding="utf-8").read()
    assert "/api/admin/knowledge/candidates/${" in api and "candidates: CandidatePage[]" in api
    tab = open("local/src/pages/SonicHealthLearned.tsx", encoding="utf-8").read()
    assert "api.decideCandidate(" in tab and "'learn'" in tab and "'dismiss'" in tab
