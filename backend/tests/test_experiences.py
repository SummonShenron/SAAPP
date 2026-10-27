"""Sonic's recorded experiences, cited as a record and never as a recollection (backend/utils/experiences.py, backend/utils/character.py, the felt-recollection rule in identity_checks)."""
import pathlib
from types import SimpleNamespace

import pytest

from backend.components import sonic_leanings as sl
from backend.utils import character as character_utils
from backend.utils import experiences as ex
from backend.utils import leanings as lean
from backend.utils import self_counters as sc
from backend.utils.identity_checks import FELT_RECOLLECTION, identity_reply_issue

ROOT = pathlib.Path(__file__).resolve().parents[2]


def reset():
    ex._cache["vectors"], ex._cache["at"] = {}, 0.0
    lean._cache["vectors"], lean._cache["at"] = {}, 0.0


# ---- what may be cited -----------------------------------------------------------------------------------------------------------------

def test_experiences_come_only_from_the_evidence_of_approved_leanings():
    items = ex.entries()
    assert items and all(item["approved"] and item["what"] and item["on"] and item["kind"] in ("failure", "worked", "record") for item in items)
    approved = {entry["id"] for entry in lean.live(sl.LEANINGS)}
    assert {item["leaning"] for item in items} <= approved
    assert len({item["id"] for item in items}) == len(items)


def test_nothing_from_a_draft_leaning_is_ever_offered(monkeypatch):
    draft = [{"id": "d", "area": "general", "philosophy": "evidence", "when": "w" * 50, "line": "I check.", "approved": False, "evidence": [{"on": "2026-10", "kind": "failure", "what": "A draft note."}]}]
    monkeypatch.setattr(ex, "LEANINGS", draft)
    assert ex.entries() == []


# ---- the gates -------------------------------------------------------------------------------------------------------------------------

def message(marked=False):
    return SimpleNamespace(additional_kwargs={ex.EXPERIENCE_MARK: True} if marked else {})


def test_a_citation_waits_for_a_quiet_open_turn_and_is_rate_limited():
    kw = dict(risk_active=False, energy_tier="open", closing=False)
    assert ex.allowed([message(), message()], **kw)
    assert not ex.allowed([message()], **{**kw, "risk_active": True}) and not ex.allowed([message()], **{**kw, "energy_tier": "low"})
    assert not ex.allowed([message()], **{**kw, "closing": True}) and not ex.allowed([message()], proactive_taken=True, **kw)
    assert not ex.allowed([message(), message(marked=True)] + [message()] * 8, **kw)           # cited within the last ten messages
    assert ex.allowed([message(marked=True)] + [message()] * ex.MIN_GAP, **kw)                  # ten messages later it may again


def test_the_feature_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("EXPERIENCES_ENABLED", "off")
    assert not ex.allowed([], risk_active=False, energy_tier="open", closing=False) and ex.select("fix the css please", None, lambda t: [1.0]) is None


# ---- choosing one ----------------------------------------------------------------------------------------------------------------------

ITEMS = [
    {"id": "a", "on": "2026-09", "kind": "failure", "what": "A hover selector did not apply.", "leaning": "x", "area": "frontend", "when": "A hover selector did not apply.", "line": "", "asks": [], "approved": True},
    {"id": "n", "on": "2026-10-26", "kind": "record", "what": "Twenty-eight coding turns, none tested.", "leaning": "y", "area": "general", "when": "Twenty-eight coding turns, none tested.", "line": "", "asks": [], "approved": True},
]
TABLE = {"A hover selector did not apply.": [1.0, 0.0, 0.0], "Twenty-eight coding turns, none tested.": [0.0, 1.0, 0.0]}
REQUESTS = {"selector please now": [0.99, 0.1, 0.0], "tests please now": [0.1, 0.99, 0.0], "poem about autumn": [0.0, 0.0, 1.0], "both of them now": [1.0, 1.0, 0.0]}


def embed(text):
    return TABLE.get(text) or REQUESTS.get(text)


def test_the_closest_experience_is_chosen_above_the_threshold_and_a_number_ranks_below_a_story():
    reset()
    lean.index_vectors(None, embed, ITEMS, collection=ex.COLLECTION, cache=ex._cache)             # the index is built in the background in real use
    assert ex.select("selector please now", None, embed, items=ITEMS)["id"] == "a"
    assert ex.select("tests please now", None, embed, items=ITEMS)["id"] == "n"
    assert ex.select("poem about autumn", None, embed, items=ITEMS) is None
    assert ex.select("both of them now", None, embed, items=ITEMS, threshold=0.5)["id"] == "a"    # a tie goes to the story, not the number


def test_an_index_that_is_not_ready_gives_no_experience_and_asks_for_a_build(monkeypatch):
    reset()
    started = []
    monkeypatch.setattr(ex, "warm", lambda db, embed, items=None: started.append(1) or True)
    assert ex.select("selector please now", None, embed, items=ITEMS) is None and started == [1]


def test_a_failing_embedding_never_raises():
    reset()
    assert ex.select("selector please now", None, lambda t: (_ for _ in ()).throw(RuntimeError("down")), items=ITEMS) is None


def test_closest_in_record_scores_only_the_same_kind(monkeypatch):
    reset()
    worked = {"id": "w", "on": "2026-10", "kind": "worked", "what": "A cache fix held.", "leaning": "z", "area": "general", "when": "A cache fix held.", "line": "", "asks": [], "approved": True}
    both = ITEMS + [worked]
    monkeypatch.setattr(ex, "entries", lambda db=None: both)
    # stub the index build so the vectors are fixed: the failure entry points along x, the worked entry along z
    monkeypatch.setattr(lean, "index_vectors", lambda db, embed, items, *, fill=True, collection=None, cache=None: {"a": [1.0, 0.0, 0.0], "n": [0.0, 1.0, 0.0], "w": [0.0, 0.0, 1.0]})
    near_failure = [1.0, 0.0, 0.0]
    failure_score = ex.closest_in_record(None, lambda t: None, near_failure, kind="failure")
    worked_score = ex.closest_in_record(None, lambda t: None, near_failure, kind="worked")
    assert failure_score is not None and failure_score >= 0.9
    assert worked_score is None or worked_score < ex.MIN_SCORE


def test_a_kind_filtered_lookup_keeps_every_entry_in_the_shared_cache(monkeypatch):
    reset()
    worked = {"id": "w", "on": "2026-10", "kind": "worked", "what": "A cache fix held.", "leaning": "z", "area": "general", "when": "A cache fix held.", "line": "", "asks": [], "approved": True}
    both = ITEMS + [worked]
    monkeypatch.setattr(ex, "entries", lambda db=None: both)

    class FakeDB:
        def __init__(self, docs):
            self.docs = docs

        def __getitem__(self, name):
            return self

        def find(self, query):
            return list(self.docs)

    docs = [{"_id": item["id"], "hash": lean.text_hash(item), "embeddings": [[1.0, 0.0, 0.0]]} for item in both]
    ex._cache["at"] = 0.0
    ex.closest_in_record(FakeDB(docs), lambda t: None, [1.0, 0.0, 0.0], kind="failure")
    assert set(ex._cache["vectors"]) == {item["id"] for item in both}


# ---- how it is offered and how it may be said ------------------------------------------------------------------------------------------

def test_the_block_offers_one_real_note_as_a_record_and_forbids_the_recollection_wording():
    block = ex.build_block(ITEMS[0])
    assert "REAL CASE FROM YOUR OWN RECORD" in block and "September 2026" in block and "A hover selector did not apply." in block
    assert "this is close to a case in my record" in block and "Never say you remember or recall it" in block and "Skip it entirely if the link is a stretch" in block
    assert "changes what you are doing here" in block                      # a failure is cited only when it changes what is being done now
    assert "plainly and briefly" in ex.build_block({**ITEMS[0], "kind": "worked"}) and "counters" in ex.build_block(ITEMS[1])
    assert ex.build_block({}) == "" and ex.build_block({"what": ""}) == ""


def test_the_counters_know_whether_the_reply_cited_the_record():
    assert ex.counts_for(False, "this is close to a case in my record") == {}
    assert ex.counts_for(True, "Here is the fix.") == {"experience_offered": 1}
    assert ex.counts_for(True, "This is close to a case in my record: a selector did not apply.") == {"experience_offered": 1, "experience_cited": 1}
    assert {"experience_offered", "experience_cited"} <= sc.KEYS


# ---- the identity rule: a record may be cited, a recollection may not be claimed --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "That reminds me of this one time a style change did nothing.",
    "I remember a similar case where a selector did not apply.",
    "I recall something similar happening before.",
    "This rings a bell: a hover selector once did not apply.",
    "I have been here before with a stale cache.",
])
def test_claiming_to_remember_or_be_reminded_of_its_own_past_is_caught(text):
    assert identity_reply_issue(text) == FELT_RECOLLECTION


@pytest.mark.parametrize("text", [
    "This is close to a case in my record: a selector was written without checking the nesting.",
    "That reminds me of a case in my record: a selector did not apply.",
    "My record has a similar case, so I am reading the markup first.",
    "I remember you told me about this before.",
    "This is the same shape as the retry problem you sorted out a few weeks back.",
    "I will check the stylesheet first.",
    "I have seen this error pattern before: it is usually a stale cache.",
])
def test_citing_the_record_or_what_the_person_said_is_left_alone(text):
    assert identity_reply_issue(text) is None


def test_the_rewrite_is_told_how_to_say_it_instead():
    from backend.utils import identity_checks

    assert "as a record, not a memory" in identity_checks._ISSUE_HINTS[FELT_RECOLLECTION]


# ---- one entry point for a tool turn and a plain conversation --------------------------------------------------------------------------

def test_a_turn_gets_leanings_and_at_most_one_case_from_one_embedding(monkeypatch):
    reset()
    calls = []

    def counting(text):
        calls.append(text)
        return embed(text)

    monkeypatch.setattr(lean, "select", lambda request, db, embed, vector=None, **k: [{"id": "css_what_wins", "philosophy": "evidence", "line": "I check which rule wins."}])
    monkeypatch.setattr(ex, "select", lambda request, db, embed, vector=None, **k: ITEMS[0])
    found = character_utils.for_turn("selector please and more words", None, counting, vector=[1.0, 0.0, 0.0], allow_experience=True)
    assert found["leanings"] == ["css_what_wins"] and found["experience"]["id"] == "a" and "I check which rule wins." in found["text"] and "REAL CASE FROM YOUR OWN RECORD" in found["text"]
    assert calls == []                                                                         # the turn's own embedding was reused: nothing was embedded here
    quiet = character_utils.for_turn("selector please and more words", None, counting, vector=[1.0, 0.0], allow_experience=False)
    assert quiet["experience"] is None and "REAL CASE" not in quiet["text"]
    assert character_utils.for_turn("hi", None, counting)["text"] == ""                       # too short to match on
    monkeypatch.setattr(lean, "select", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert character_utils.for_turn("selector please and more words", None, counting, vector=[1.0, 0.0]) == {"text": "", "leanings": [], "experience": None}


def test_both_kinds_of_turn_use_it_and_the_reply_is_marked_and_counted():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "character_utils.for_turn, question, get_db(), embed_text, vector=question_embedding" in app_source
    assert 'slot["proactive_taken"] = True' in app_source and "experiences_utils.cited(str(ai_message.content))" in app_source
    assert "character_module.for_turn, request, get_db(), embed_text" in workflow and "llm_turns.note_experience()" in workflow
