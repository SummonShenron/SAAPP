"""Relevant findings: a shelf item whose meaning overlaps what the person just said (backend/utils/relevant_findings.py, reading_shelf.best_match, curiosity.finding_slot_open)."""
import pathlib
from datetime import timedelta
from types import SimpleNamespace

import pytest

from backend.tests.test_reading_shelf import NOW, Db, stock
from backend.utils import curiosity
from backend.utils import offer_response
from backend.utils import reading_shelf as shelf
from backend.utils import relevant_findings as rf

ROOT = pathlib.Path(__file__).resolve().parents[2]
MESSAGE = "do you think the current hype around language models could fade the way earlier AI booms did"
CLOSE = [1.0, 0.0, 0.0]
FAR = [0.0, 1.0, 0.0]
MID = [0.6, 0.8, 0.0]        # cosine 0.6 with CLOSE


def msg(kind, text="x"):
    return SimpleNamespace(type=kind, content=text, additional_kwargs={})


CHAT = [msg("human", "first thing"), msg("ai", "an answer."), msg("human", MESSAGE)]


def embed_by_text(table):
    calls = []

    def embed(text):
        calls.append(text)
        return table.get(text, FAR)

    embed.calls = calls
    return embed


def stocked(db=None, vector=CLOSE, **kw):
    db = db if db is not None else Db()
    item = stock(db, url="https://blog.example.com/winter", topic="ai_research", title="AI Winter", summary="Why funding collapsed after the early AI booms.", **kw)
    db[shelf.COLLECTION].update_one({"_id": item["_id"]}, {"$set": {"embedding": vector}})
    return db, item


def select(db, **kw):
    args = dict(source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False, user_profile=None, embed=lambda text: CLOSE, vector=CLOSE, counts={}, now=NOW)
    args.update(kw)
    return rf.select(db, "jack", kw.pop("question", MESSAGE), kw.pop("messages", CHAT), **{k: v for k, v in args.items() if k not in ("question", "messages")})


@pytest.fixture(autouse=True)
def clear_pacing(monkeypatch):
    monkeypatch.setattr(offer_response, "pacing", lambda *a, **k: offer_response.CLEAR)


# ---- the meaning match ---------------------------------------------------------------------------------------------------------------------------------

def test_the_closest_item_is_chosen_only_when_it_clears_the_threshold_and_items_without_an_embedding_are_skipped():
    near = {"_id": "a", "embedding": CLOSE}
    middling = {"_id": "b", "embedding": MID}
    bare = {"_id": "c"}
    assert shelf.best_match([middling, near, bare], CLOSE)["item"]["_id"] == "a" and shelf.best_match([middling, near], CLOSE)["score"] == 1.0
    assert shelf.best_match([middling], CLOSE)["score"] == 0.6                                # 0.6 clears the default of 0.58 ...
    assert shelf.best_match([middling], CLOSE, threshold=0.7) is None and shelf.best_match([middling], CLOSE, threshold=0.5)["item"]["_id"] == "b"      # ... and not 0.7
    assert shelf.best_match([bare], CLOSE) is None and shelf.best_match([], CLOSE) is None and shelf.best_match([near], FAR) is None


def test_missing_embeddings_are_filled_in_a_few_at_a_time_and_a_failed_one_is_left_alone():
    db = Db()
    for n in range(4):
        stock(db, url=f"https://blog.example.com/{n}", title=f"T{n}", summary=f"S{n}")
    embed = embed_by_text({})
    assert shelf.ensure_embeddings(db, embed, limit=3) == 3 and len(embed.calls) == 3
    assert shelf.ensure_embeddings(db, embed) == 1 and sum(1 for i in shelf.live_items(db, NOW) if i.get("embedding")) == 4

    def broken(text):
        raise RuntimeError("down")

    db2 = Db()
    stock(db2, url="https://blog.example.com/x")
    assert shelf.ensure_embeddings(db2, broken) == 0 and shelf.live_items(db2, NOW)[0].get("embedding") is None


def test_offers_are_logged_so_the_daily_limit_can_count_them_and_old_ones_do_not_count():
    db = Db()
    for n in range(4):
        shelf.mark_offered(db, "jack", f"k{n}", NOW - timedelta(hours=30 if n == 0 else 2 * n))
    assert shelf.findings_in_last_day(db, "jack", NOW) == 3 and shelf.findings_in_last_day(db, "someone", NOW) == 0


# ---- the free gates ------------------------------------------------------------------------------------------------------------------------------------

def gate(text=MESSAGE, messages=None, **kw):
    args = dict(source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False)
    args.update(kw)
    return curiosity.finding_slot_open(text, messages if messages is not None else CHAT, **args)


def test_a_substantive_message_in_a_real_conversation_opens_the_gate_and_every_reason_for_quiet_closes_it():
    assert gate() is True
    assert gate(source_type="tool_output") is False and gate(risk_active=True) is False and gate(energy_tier="easing") is False and gate(proactive_taken=True) is False
    assert gate("ok") is False and gate("thanks so much, goodnight!") is False and gate("short message here") is False       # a lull, a closing, too little to match on
    assert gate("look at this ```python\nprint(1)\n``` and tell me what it does please") is False and gate("x " * 900) is False        # code and pastes
    assert gate(messages=[msg("human", MESSAGE)]) is False                                                                   # not yet a real exchange
    assert gate(messages=[msg("human", "a"), msg("ai", "Which file do you mean?"), msg("human", MESSAGE)]) is False         # it is an answer to Sonic's own question


# ---- the selector --------------------------------------------------------------------------------------------------------------------------------------

def test_a_close_item_is_offered_as_a_finding_with_its_score_and_the_turns_own_embedding_is_reused():
    db, item = stocked()
    embed = embed_by_text({})
    counts = {}
    offer = select(db, embed=embed, counts=counts)
    assert offer["kind"] == "finding" and offer["item"]["_id"] == item["_id"] and offer["score"] == 1.0 and counts == {"finding_selected": 1}
    assert embed.calls == []                                                                                                # no second embedding of the message


def test_a_far_item_a_seen_item_and_a_shelf_with_nothing_fresh_give_nothing():
    db, item = stocked(vector=FAR)
    assert select(db) is None
    db, item = stocked()
    shelf.mark_offered(db, "jack", item["_id"], NOW - timedelta(days=3))
    assert select(db) is None
    assert select(Db()) is None


def test_the_daily_limit_the_per_person_gap_and_the_persons_own_switches_hold_it_back(monkeypatch):
    db, item = stocked()
    for hours in (3, 6, 9):
        shelf.mark_offered(db, "jack", f"older{hours}", NOW - timedelta(hours=hours))
    assert select(db) is None                                                                                               # three in the last day already
    db, item = stocked()
    monkeypatch.setattr(offer_response, "pacing", lambda *a, **k: offer_response.HELD)
    assert select(db) is None                                                                                               # the usual gap since the last offer of any kind
    monkeypatch.setattr(offer_response, "pacing", lambda *a, **k: offer_response.CLEAR)
    assert select(db, user_profile={"offers": False}) is None                                                              # the person's own "no offers" switch
    assert select(db, user_profile={"avoid": ["AI winter"]}) is None                                                       # a subject they asked not to hear about
    assert select(db, user_profile={"avoid": ["gardening"]}) is not None                                                   # an unrelated avoided subject changes nothing


def test_a_guest_a_database_failure_and_a_missing_database_never_raise():
    assert rf.select(None, "jack", MESSAGE, CHAT, source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False, user_profile=None, embed=lambda t: CLOSE) is None

    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("database went away")

    assert rf.select(Broken(), "jack", MESSAGE, CHAT, source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False, user_profile=None, embed=lambda t: CLOSE) is None


def test_an_item_that_has_no_embedding_yet_is_embedded_on_the_way_and_the_message_is_embedded_only_if_the_turn_gave_no_vector():
    db = Db()
    item = stock(db, url="https://blog.example.com/new", title="AI Winter", summary="Why funding collapsed.")
    table = {shelf.embedding_source(item): CLOSE, MESSAGE: CLOSE}
    embed = embed_by_text(table)
    offer = select(db, embed=embed, vector=None)
    assert offer and offer["item"]["_id"] == item["_id"] and MESSAGE in embed.calls and shelf.embedding_source(item) in embed.calls


# ---- what the reply is told ---------------------------------------------------------------------------------------------------------------------------

def test_the_finding_block_answers_first_names_the_overlap_allows_leaving_it_out_and_keeps_the_rules_about_what_was_read():
    db, item = stocked()
    block = shelf.build_shelf_block({"kind": "finding", "item": item, "score": 0.7}).replace("\n", " ")
    for expected in ("RELEVANT READING (this turn only)", "Answer what they said or asked first, and fully", "say what the piece covers and why that is close to what they said", "a stretched link is worse than none", "no lesson, advice or \"good reminder\" of your own",
                     "one specific, easy question", "Title: 'AI Winter'", "Do not tell them to read it", "no \"I loved it\"", "Include the link once"):
        assert expected in block, expected


# ---- the wiring ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_turn_selects_a_finding_before_the_memory_callback_and_the_slot_is_shared():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert source.index("relevant_findings.select") < source.index("callback_slot_ok =")
    assert "vector=question_embedding" in source and "not shelf_utils.asks_what_reading(question)" in source
    assert "callback_slot_ok = (not finding_offer) and (" in source and "or callback_offered or goal_nudge_context or finding_offer" in source
    assert "elif finding_offer:" in source and "shelf_offer = finding_offer" in source


def test_the_shelf_reads_about_agents_and_language_model_systems_and_its_counters_and_labels_exist():
    assert "agent_design" in shelf.TOPIC_IDS and "llm_systems" in shelf.TOPIC_IDS
    for topic in shelf.TOPICS:
        if topic["id"] in ("agent_design", "llm_systems"):
            assert all(len(t.split()) >= 1 for t in topic["triggers"]) and "agent" not in topic["triggers"] and "agents" not in topic["triggers"]          # narrow on purpose
    from backend.utils import self_counters
    assert "finding_selected" in self_counters.KEYS
    labels = (ROOT / "local" / "src" / "pages" / "sonicHealthFormat.ts").read_text(encoding="utf-8")
    assert "agent_design:" in labels and "llm_systems:" in labels


def test_the_module_logs_only_a_similarity_number():
    import ast
    tree = ast.parse((ROOT / "backend" / "utils" / "relevant_findings.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
            assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1])
