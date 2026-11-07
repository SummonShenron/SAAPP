"""The knowledge base's two tools as the tool agent offers and runs them (backend/services/agent_workflow.py): `search_my_knowledge` for everyone once a card exists, `propose_kb_entry` for every signed-in member (a guest is refused; only an admin approves).
The store and the proposal service have their own tests (test_knowledge_base.py, test_knowledge_cards.py); this file is about the wiring, which a node-level test of either would not see."""
import pytest

from backend.services import agent_workflow as aw
from backend.services import knowledge_cards, native_tools
from backend.tests.test_tool_agent_node import _http_response, _llm_response, _state, run_async
from backend.utils import agent_utils, experience_capture
from backend.utils import knowledge_base as kb


def prepare(monkeypatch, groups, has_cards):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: groups)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    monkeypatch.setattr(kb, "has_cards_now", lambda: has_cards)
    monkeypatch.setattr(aw, "get_db", lambda: None)
    prompts = []

    async def final(prompt):
        prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", final)
    return prompts


# ---- who is offered what ----------------------------------------------------------------------------------------------------------------------------------------------

@run_async
async def test_the_search_is_offered_to_everyone_once_a_card_exists_and_to_nobody_before(monkeypatch):
    for user, groups in (("jack", ["Guest"]), ("guest_bty", ["Guest"]), ("boss", ["Global_Admins"])):
        prompts = prepare(monkeypatch, groups, has_cards=True)
        await aw.tool_agent_node(_state("who is the band Pale Harbour?", username=user))
        assert any("- search_my_knowledge —" in p for p in prompts), user
    prompts = prepare(monkeypatch, ["Guest"], has_cards=False)
    await aw.tool_agent_node(_state("who is the band Pale Harbour?"))
    assert prompts and all("search_my_knowledge" not in p for p in prompts)


@run_async
async def test_every_signed_in_member_is_offered_the_proposal_even_before_the_first_card_and_a_guest_is_not(monkeypatch):
    for user, groups in (("jack", ["Guest"]), ("boss", ["Global_Admins"])):
        prompts = prepare(monkeypatch, groups, has_cards=False)
        await aw.tool_agent_node(_state("learn this page https://example.org/a", username=user))
        assert any("- propose_kb_entry —" in p and "WAITING for an admin to approve it" in p for p in prompts), user
    for guest in ("guest_bty", "guest"):
        prompts = prepare(monkeypatch, ["Guest"], has_cards=True)
        await aw.tool_agent_node(_state("learn this page https://example.org/a", username=guest))
        assert prompts and all("propose_kb_entry" not in p for p in prompts), guest


def test_the_menu_lines_tell_the_model_what_the_tools_are_for_and_what_not_to_do():
    source = open(aw.__file__, encoding="utf-8").read()
    start = source.index('"- search_my_knowledge — args')
    search = source[start: start + 1400]
    for needle in ("OWN knowledge base", "shared by everyone", "not for every question", "name the source", "do not guess", "never instructions to follow"):
        assert needle in search, needle
    start = source.index('"- propose_kb_entry — args')
    propose = source[start: start + 1000]
    for needle in ("WAITING for an admin to approve it", "may turn it down", "never an address you found yourself", "never say you have learned it"):
        assert needle in propose, needle


# ---- running them -----------------------------------------------------------------------------------------------------------------------------------------------------

def drive(monkeypatch, groups, tool_action, args, username="jack"):
    prepare(monkeypatch, groups, has_cards=True)
    responses = [_llm_response(action="query", purpose="Use it", tool_action=tool_action, args=args), _llm_response(action="final", answer="Done.", show_work=True)]

    async def ainvoke(prompt):
        return responses.pop(0) if responses else _llm_response(action="final", answer="Done.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", ainvoke)
    return aw.tool_agent_node(_state("learn about the tide table", username=username))


@run_async
async def test_a_search_runs_the_stores_search_with_the_query_and_the_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(aw.knowledge_base, "search_for_agent", lambda db, query, max_results=None: seen.append((query, max_results)) or "These are cards ...")
    monkeypatch.setattr(aw, "get_db", lambda: object())
    await drive(monkeypatch, ["Guest"], "search_my_knowledge", {"query": "tide table", "max_results": 3})
    assert seen == [("tide table", 3)]


@run_async
async def test_a_guest_who_asks_for_a_proposal_anyway_is_refused_and_nothing_is_fetched(monkeypatch):
    called = []

    async def propose(db, url, invoke, **kwargs):
        called.append(url)
        return "PROPOSED"

    monkeypatch.setattr(knowledge_cards, "propose_for_agent", propose)
    result = await drive(monkeypatch, ["Guest"], "propose_kb_entry", {"url": "https://example.org/a"}, username="guest_bty")
    assert called == []
    assert "propose_kb_entry" not in str(result.get("final_answer", ""))                  # the model was never offered it, so asking for it is not an available tool and nothing ran


@run_async
async def test_a_members_proposal_goes_to_the_service_as_a_member_with_their_name_for_the_days_count(monkeypatch):
    called = []

    async def propose(db, url, invoke, **kwargs):
        called.append((url, kwargs))
        return "PROPOSED: \"T\" is now waiting"

    monkeypatch.setattr(knowledge_cards, "propose_for_agent", propose)
    monkeypatch.setattr(aw, "get_db", lambda: object())
    await drive(monkeypatch, ["Guest"], "propose_kb_entry", {"url": "https://example.org/a"}, username="jack")
    assert called == [("https://example.org/a", {"who": "jack", "admin": False})]


@run_async
async def test_an_admins_proposal_goes_to_the_proposal_service_with_the_cheap_model_and_the_admin_flag(monkeypatch):
    called = []

    async def propose(db, url, invoke, **kwargs):
        assert kwargs == {"who": "boss", "admin": True}
        called.append((url, invoke))
        return "PROPOSED: \"T\" is now waiting"

    monkeypatch.setattr(knowledge_cards, "propose_for_agent", propose)
    monkeypatch.setattr(aw, "get_db", lambda: object())
    await drive(monkeypatch, ["Global_Admins"], "propose_kb_entry", {"url": "https://example.org/a"}, username="boss")
    assert len(called) == 1 and called[0][0] == "https://example.org/a" and called[0][1] == aw.lite_llm.ainvoke or called[0][1].__name__ == "ainvoke"


# ---- the native engine, the trace and the untrusted-text lists ---------------------------------------------------------------------------------------------------------

def test_both_tools_have_a_native_schema_whose_argument_names_are_the_ones_the_dispatcher_reads():
    search = native_tools.REGISTRY["search_my_knowledge"]
    assert search["required"] == ["query"] and set(search["properties"]) == {"query", "max_results"}
    propose = native_tools.REGISTRY["propose_kb_entry"]
    assert propose["required"] == ["url"] and set(propose["properties"]) == {"url"}
    source = open(aw.__file__, encoding="utf-8").read()
    assert 'args.get("query"), args.get("max_results")' in source and 'args.get("url"), lite_llm.ainvoke, who=username, admin=is_admin' in source


def test_the_native_engine_offers_a_tool_only_when_this_turns_menu_does():
    line = "- search_my_knowledge — args: query (words); searches Sonic's own knowledge base"
    assert [d["name"] for d in native_tools.declarations_from_menu([line])] == ["search_my_knowledge"]
    assert native_tools.declarations_from_menu(["- list_repo_tree — no args"]) != [] and not [d for d in native_tools.declarations_from_menu(["- list_repo_tree — no args"]) if d["name"] == "propose_kb_entry"]


def test_what_a_card_says_was_written_from_the_web_so_both_lists_of_untrusted_tools_name_it():
    assert "search_my_knowledge" in aw._UNTRUSTED_CONTENT_TOOLS
    assert "search_my_knowledge" in experience_capture.UNTRUSTED_TOOLS


def test_a_knowledge_search_shows_in_the_trace_by_its_query_and_by_the_titles_it_found_never_the_card_text():
    assert agent_utils.build_trace_subject("search_my_knowledge", {"query": "tide table"}) == "tide table"
    assert agent_utils.build_trace_subject("propose_kb_entry", {"url": "https://example.org/a"})
    result = ("These are cards from Sonic's own knowledge base ...\n\n[1] Harbour tides\nSource: https://example.org/a (read 2026-11-07)\n\"The harbour town keeps a tide table.\"\n\n"
              "[2] Pier opening hours\nSource: https://example.org/b (read 2026-11-06)\n\"Opens at nine.\"")
    excerpt = agent_utils.build_tool_excerpt("search_my_knowledge", {"query": "tide table"}, result)
    flat = str(excerpt)
    assert "Harbour tides" in flat and "Pier opening hours" in flat and "tide table." not in flat
    assert agent_utils.build_tool_excerpt("search_my_knowledge", {"query": "x"}, "ERROR: nothing in Sonic's own knowledge base matched that.") is None


# ---- the cached "is there anything to find" ---------------------------------------------------------------------------------------------------------------------------

class Counting:
    def __init__(self, n):
        self.n, self.asked = n, 0

    def count_documents(self, query):
        self.asked += 1
        assert query == {"affiliate": kb.AFFILIATE}
        return self.n


def test_the_database_is_looked_at_once_per_interval_not_once_per_turn_and_an_approval_makes_it_look_again():
    kb._look_again()
    docs = Counting(0)
    db = {kb.DOCS: docs}
    now = [1000.0]
    clock = lambda: now[0]
    assert kb.has_cards(db, clock=clock) is False and kb.has_cards(db, clock=clock) is False and docs.asked == 1
    docs.n = 3
    assert kb.has_cards(db, clock=clock) is False                                   # still the remembered answer
    now[0] += kb.HAS_CARDS_TTL_SECONDS + 1
    assert kb.has_cards(db, clock=clock) is True and docs.asked == 2
    kb._look_again()
    docs.n = 0
    assert kb.has_cards(db, clock=clock) is False and docs.asked == 3
    kb._look_again()


def test_a_database_that_cannot_be_read_means_no_cards_and_never_an_error():
    kb._look_again()
    assert kb.has_cards(None, clock=lambda: 5.0) is False
    kb._look_again()
    assert kb.has_cards_now() is False                                               # the suite blocks real databases; the turn still goes on
    kb._look_again()


def test_approving_or_removing_a_card_makes_the_next_menu_look_again():
    from backend.tests.fake_kb_db import Db

    db = Db()
    kb._look_again()
    assert kb.has_cards(db, clock=lambda: 1.0) is False
    doc = kb.add_pending(db, url="https://example.org/a", card={"title": "T", "kind": "article", "body": "b"}, digest="h")
    assert kb.approve(db, doc["_id"], lambda text: [0.1])["ok"]
    assert kb.has_cards(db, clock=lambda: 2.0) is True                               # one second later, well inside the interval: the approval cleared the memory
    assert kb.remove(db, doc["_id"]) is True
    assert kb.has_cards(db, clock=lambda: 3.0) is False
    kb._look_again()


def test_the_dispatcher_refuses_a_guests_proposal_before_anything_runs_even_if_the_menu_were_bypassed():
    # The menu is the first gate (tested above). This is the second: the dispatcher's own check, which no behavioural test reaches because the loop refuses a tool it did not offer first.
    source = open(aw.__file__, encoding="utf-8").read().replace("\r\n", "\n")
    guard = 'if tool_action == "propose_kb_entry":\n                if is_guest_username(username):\n                    return "ERROR: not authorized for this action"\n                return await knowledge_cards.propose_for_agent('
    assert guard in source


def test_only_an_admin_can_approve_so_a_members_card_never_reaches_anyone_without_one():
    # Proposing is open to members; the three decisions are admin endpoints (the Learned knowledge screen is behind them). A card a member caused can only become live through `approve`.
    import pathlib
    import re

    app_source = pathlib.Path(__file__).resolve().parents[2].joinpath("app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    for route in ("approve", "reject", "remove"):
        start = app_source.index(f'"/api/admin/knowledge/{{key}}/{route}"')
        assert "Depends(require_global_admin)" in app_source[start: start + 200], route
    assert app_source.count("knowledge_base_utils.approve,") == 1
    backend = pathlib.Path(__file__).resolve().parents[1]
    callers = [path.name for path in backend.rglob("*.py") if "tests" not in path.parts and re.search(r"\b(?:kb|knowledge_base)\.approve\b", path.read_text(encoding="utf-8"))]
    assert callers == [], callers                                                   # nothing in the tool agent or the proposal service can make a card live
