"""Sonic's working leanings and the philosophies behind them (backend/components/sonic_leanings.py, backend/utils/leanings.py)."""
import copy
import pathlib
import re

import pytest

from backend.components import sonic_leanings as sl
from backend.utils import leanings as lean
from backend.utils import self_counters as sc
from backend.utils.identity_checks import identity_reply_issue

ROOT = pathlib.Path(__file__).resolve().parents[2]
_FEELING_WORDS = re.compile(r"\b(?:hate|love|proud|frustrat\w*|annoy\w*|disappoint\w*|excited|happy(?! path)|sad|worried|afraid|enjoy\w*)\b", re.IGNORECASE)
_MEMORY_CLAIMS = re.compile(r"\b(?:I remember|I've seen|I have seen|last time|I once|back when|in my experience)\b", re.IGNORECASE)


def texts():
    for philosophy in sl.PHILOSOPHIES:
        yield f"philosophy {philosophy['id']} statement", philosophy["statement"]
        yield f"philosophy {philosophy['id']} reason", philosophy["reason"]
    for entry in sl.LEANINGS:
        yield f"leaning {entry['id']} line", entry["line"]
        for note in entry.get("evidence") or []:
            yield f"leaning {entry['id']} evidence", note["what"]


# ---- the registry is sound and every word of it is held to the identity policy ------------------------------------------------------------

def test_the_registry_is_sound():
    assert lean.check_registry() == []


@pytest.mark.parametrize("label,text", list(texts()))
def test_no_line_claims_a_feeling_a_memory_or_a_stake(label, text):
    assert identity_reply_issue(text) is None, (label, identity_reply_issue(text))
    assert not _FEELING_WORDS.search(text), (label, "a feeling word")
    assert not _MEMORY_CLAIMS.search(text), (label, "a claimed memory: a reason is a consequence, and evidence is written about the record in the third person")


def test_leanings_are_in_sonics_voice_and_philosophies_hold_in_the_first_person():
    for entry in sl.LEANINGS:
        assert re.match(r"^(?:I|A change|When|Before)\b", entry["line"]), entry["id"]
    for philosophy in sl.PHILOSOPHIES:
        assert re.match(r"^(?:I|It's)\b", philosophy["statement"]), philosophy["id"]


def test_ids_are_unique_and_every_leaning_follows_from_a_philosophy_that_exists():
    ids = [entry["id"] for entry in sl.LEANINGS]
    assert len(ids) == len(set(ids)) and len({p["id"] for p in sl.PHILOSOPHIES}) == len(sl.PHILOSOPHIES)
    assert {entry["philosophy"] for entry in sl.LEANINGS} <= {p["id"] for p in sl.PHILOSOPHIES}


def test_the_registry_check_names_the_problem():
    bad = [{"id": "x", "philosophy": "nope", "when": "short", "line": "no full stop", "evidence": [{"on": "yesterday", "what": ""}]}]
    problems = " ".join(lean.check_registry(bad, [{"id": "p", "statement": "Not a sentence", "reason": ""}]))
    for expected in ("must follow from a philosophy", "describe the situation", "must be a sentence", "evidence note needs a date", "statement must be", "reason must be"):
        assert expected in problems, expected


def test_only_what_is_approved_can_be_used_and_a_draft_never_is():
    assert all(entry.get("approved") is True for entry in lean.live()) and {entry["id"] for entry in lean.live()} <= {entry["id"] for entry in sl.LEANINGS}
    drafts = [dict(entry, id=entry["id"] + "_draft", approved=False) for entry in sl.LEANINGS[:2]]
    assert lean.live(sl.LEANINGS + drafts) == lean.live(sl.LEANINGS)                       # a new entry that is not approved yet is left out whatever else is in the list
    assert {"css_what_wins", "fix_where_it_renders", "read_back_ui_change"} <= {entry["id"] for entry in lean.live()}


def test_the_leanings_that_came_from_real_failures_carry_dated_evidence():
    with_evidence = [entry for entry in sl.LEANINGS if entry.get("evidence")]
    assert len(with_evidence) >= 8
    assert all(re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", note["on"]) for entry in with_evidence for note in entry["evidence"])


# ---- matching by meaning --------------------------------------------------------------------------------------------------------------------

ENTRIES = [
    {"id": "a", "philosophy": "evidence", "when": "x" * 50, "line": "I check a.", "approved": True},
    {"id": "b", "philosophy": "cause", "when": "y" * 50, "line": "I fix b.", "approved": True},
    {"id": "c", "philosophy": "cause", "when": "z" * 50, "line": "I name c.", "approved": True},
    {"id": "core", "philosophy": "evidence", "when": "w" * 50, "line": "I always d.", "approved": True, "core": True},
    {"id": "draft", "philosophy": "cause", "when": "v" * 50, "line": "I never e.", "approved": False},
]
VECTORS = {"a": [1.0, 0.0], "b": [0.8, 0.6], "c": [0.0, 1.0], "core": [0.5, 0.5], "draft": [1.0, 0.0]}


def test_the_closest_entries_above_the_threshold_are_chosen_best_first_and_core_ones_are_left_to_the_caller():
    ranked = lean.rank([1.0, 0.0], ENTRIES, VECTORS, threshold=0.5, k=5)
    assert [entry["id"] for _, entry in ranked] == ["a", "draft", "b"]                       # drafts are ranked here only because rank() is handed whatever it is given: live() is what filters them
    assert "core" not in [entry["id"] for _, entry in ranked]
    assert [entry["id"] for _, entry in lean.rank([1.0, 0.0], lean.live(ENTRIES), VECTORS, threshold=0.5, k=1)] == ["a"]
    assert lean.rank([1.0, 0.0], lean.live(ENTRIES), VECTORS, threshold=0.99, k=3)[0][1]["id"] == "a"
    assert lean.rank([0.0, 0.0], lean.live(ENTRIES), VECTORS, threshold=0.1) == []


class FakeDB:
    def __init__(self):
        self.docs = {}

    def __getitem__(self, name):
        assert name == lean.COLLECTION
        return self

    def find(self, query):
        return [dict(doc, _id=key) for key, doc in self.docs.items()]

    def update_one(self, flt, update, upsert=False):
        self.docs.setdefault(flt["_id"], {}).update(copy.deepcopy(update["$set"]))


def embedder(table, calls=None):
    def embed(text):
        if calls is not None:
            calls.append(text)
        for key, vector in table.items():
            if key in text:
                return vector
        return None
    return embed


def reset_cache():
    lean._cache["vectors"], lean._cache["at"] = {}, 0.0


def test_the_index_embeds_each_approved_entry_once_stores_it_and_skips_drafts():
    reset_cache()
    db, calls = FakeDB(), []
    vectors = lean.index_vectors(db, embedder({"x" * 50: [1.0, 0.0], "y" * 50: [0.0, 1.0], "z" * 50: [0.5, 0.5], "w" * 50: [0.2, 0.9]}, calls), ENTRIES, now=0.0)
    assert set(vectors) == {"a", "b", "c", "core"} and len(calls) == 4 and set(db.docs) == {"a", "b", "c", "core"}
    reset_cache()
    again = lean.index_vectors(db, embedder({}, calls), ENTRIES, now=5000.0)           # a restart: nothing is embedded again, the stored vectors are used
    assert len(calls) == 4 and set(again) == set(vectors)


def test_an_edited_entry_is_embedded_again_and_the_others_are_not():
    reset_cache()
    db, calls = FakeDB(), []
    table = {"x" * 50: [1.0, 0.0], "y" * 50: [0.0, 1.0], "z" * 50: [0.5, 0.5], "w" * 50: [0.2, 0.9], "edited": [0.9, 0.1]}
    lean.index_vectors(db, embedder(table, calls), ENTRIES, now=0.0)
    reset_cache()
    changed = copy.deepcopy(ENTRIES)
    changed[0]["when"] = "edited " + "x" * 50
    lean.index_vectors(db, embedder(table, calls), changed, now=5000.0)
    assert len(calls) == 5 and "edited" in calls[-1]


def test_an_entry_that_cannot_be_embedded_is_left_out_without_failing():
    reset_cache()
    vectors = lean.index_vectors(None, embedder({"x" * 50: [1.0, 0.0]}), ENTRIES, now=0.0)       # no database: in memory only
    assert set(vectors) == {"a"}


def test_select_returns_core_plus_the_closest_and_never_raises(monkeypatch):
    reset_cache()
    table = {"x" * 50: [1.0, 0.0], "y" * 50: [0.8, 0.6], "z" * 50: [0.0, 1.0], "w" * 50: [0.5, 0.5], "fix the layout": [1.0, 0.05]}
    lean.index_vectors(None, embedder(table), ENTRIES)                              # the index is built in the background in real use; a turn only reads it
    chosen = lean.select("please fix the layout on mobile", None, embedder(table), entries=ENTRIES, threshold=0.7, k=2)
    assert [entry["id"] for entry in chosen] == ["core", "a", "b"]
    assert [entry["id"] for entry in lean.select("hi", None, embedder(table), entries=ENTRIES)] == ["core"]       # too short to match on: only the always-on ones
    assert lean.select("something long enough to match", None, lambda text: (_ for _ in ()).throw(RuntimeError("down")), entries=ENTRIES) == []
    monkeypatch.setenv("LEANINGS_ENABLED", "off")
    assert lean.select("please fix the layout on mobile", None, embedder(table), entries=ENTRIES) == []


def test_a_request_with_a_ready_vector_is_not_embedded_again():
    reset_cache()
    calls = []
    table = {"x" * 50: [1.0, 0.0]}
    lean.index_vectors(None, embedder(table), ENTRIES[:1])
    calls.clear()
    chosen = lean.select("fix the layout please", None, embedder(table, calls), entries=ENTRIES[:1], vector=[1.0, 0.0], threshold=0.5)
    assert [entry["id"] for entry in chosen] == ["a"] and all("fix the layout" not in text for text in calls)


# ---- what goes into the prompt --------------------------------------------------------------------------------------------------------------

def test_a_leaning_is_shown_under_its_philosophy_with_the_reason_to_give():
    chosen = [entry for entry in sl.LEANINGS if entry["id"] in ("css_what_wins", "fix_where_it_renders", "read_back_ui_change")]
    block = lean.build_block(chosen)
    assert "Why:" in block and sl.PHILOSOPHIES[0]["statement"] in block and "I check which rule actually wins" in block
    assert "come first" in block and "say your view once" in block                           # the person's preferences and the project's conventions win
    assert block.index(sl.PHILOSOPHIES[0]["statement"]) < block.index("I check which rule actually wins")


def test_a_philosophy_that_is_not_approved_is_left_out_and_its_leaning_stands_alone():
    block = lean.build_block([{"id": "q", "philosophy": "small", "line": "I keep it small."}], [{"id": "small", "statement": "I lean small.", "reason": "Because.", "approved": False}])
    assert "I keep it small." in block and "Why:" not in block and lean.build_block([]) == ""


def test_the_counters_exist_for_every_entry_so_none_is_dropped():
    assert {"leaning_turns", "native_chars_leanings"} <= sc.KEYS
    assert {f"leaning_used_{entry['id']}" for entry in sl.LEANINGS} <= sc.KEYS
    assert lean.counts_for([]) == {} and lean.counts_for([{"id": "css_what_wins"}]) == {"leaning_turns": 1, "leaning_used_css_what_wins": 1}


def test_the_tool_agent_asks_for_them_after_its_fixed_rules_and_the_meter_counts_them():
    loop = (ROOT / "backend" / "services" / "native_loop.py").read_text(encoding="utf-8")
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    from backend.services import native_loop

    assert "leanings: str = \"\"" in loop and 'meter.part' in loop and "leanings=await _leanings_block()" in workflow
    context = native_loop.build_context("fix the css", "", "", "", leanings="YOUR OWN WAY OF WORKING\n- I check.")
    assert context.index("YOUR OWN WAY OF WORKING") < context.index("USER REQUEST:") and "leanings" in __import__("backend.utils.prompt_meter", fromlist=["x"]).PARTS


def test_a_leanings_id_is_a_safe_counter_name():
    """The counter keys are built from the ids and the closed list allows nothing that could hold a person (see test_self_counters): so no id says user, name, text and so on."""
    for entry in sl.LEANINGS:
        assert entry["id"].replace("_", "").isalpha(), entry["id"]
        for forbidden in ("user", "session", "text", "message", "name", "email"):
            assert forbidden not in entry["id"], entry["id"]


def test_evidence_notes_say_what_kind_they_are_and_the_record_ones_state_real_numbers():
    kinds = [note.get("kind", "failure") for entry in sl.LEANINGS for note in entry.get("evidence") or []]
    assert set(kinds) <= {"failure", "worked", "record"} and {"worked", "record"} <= set(kinds) and kinds.count("failure") > kinds.count("worked")
    bad = [{"id": "x", "philosophy": "evidence", "when": "w" * 50, "line": "I check.", "evidence": [{"on": "2026-10", "kind": "dream", "what": "A thing."}]}]
    assert any("kind must be" in problem for problem in lean.check_registry(bad))


# ---- a turn never waits for the index --------------------------------------------------------------------------------------------------------

def test_a_turn_does_not_embed_the_entries_it_finds_missing_and_asks_for_a_background_build(monkeypatch):
    reset_cache()
    started = []
    monkeypatch.setattr(lean, "warm", lambda db, embed, entries=None: started.append(1) or True)
    calls = []
    chosen = lean.select("please fix the layout on mobile", None, embedder({"fix the layout": [1.0, 0.0]}, calls), entries=ENTRIES, threshold=0.0)
    assert [entry["id"] for entry in chosen] == ["core"]                         # nothing indexed yet: only the always-on ones, and nobody waited
    assert started == [1] and calls == ["please fix the layout on mobile"]       # only the request itself was embedded, not a single entry


def test_a_partial_index_is_not_cached_so_a_later_look_sees_what_was_stored_meanwhile():
    reset_cache()
    db = FakeDB()
    table = {"x" * 50: [1.0, 0.0], "y" * 50: [0.0, 1.0], "z" * 50: [0.5, 0.5], "w" * 50: [0.2, 0.9]}
    assert lean.index_vectors(db, embedder(table), ENTRIES, fill=False) == {}
    lean.index_vectors(db, embedder(table), ENTRIES[:2], fill=True)
    again = lean.index_vectors(db, embedder({}), ENTRIES, fill=False)           # the two stored ones are found; the others are still being waited for
    assert set(again) == {"a", "b"}


def test_the_background_build_runs_once_at_a_time_and_fills_the_index():
    import threading

    reset_cache()
    release, finished = threading.Event(), threading.Event()
    table = {"x" * 50: [1.0, 0.0], "y" * 50: [0.0, 1.0], "z" * 50: [0.5, 0.5], "w" * 50: [0.2, 0.9]}

    def slow(text):
        release.wait(5)
        return embedder(table)(text)

    db = FakeDB()
    assert lean.warm(db, slow, ENTRIES) is True and lean.warm(db, slow, ENTRIES) is False       # a second request while one is running starts nothing
    release.set()
    for _ in range(100):
        if not lean._warming["on"]:
            break
        finished.wait(0.05)
    assert not lean._warming["on"] and set(db.docs) == {"a", "b", "c", "core"}


def test_the_server_starts_the_build_when_it_starts():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "leanings_utils.warm(get_db(), embed_text)" in source


# ---- what happened after a reply that used a leaning --------------------------------------------------------------------------------------

def test_the_message_after_a_reply_is_counted_against_each_leaning_it_used():
    assert lean.outcome_counts(["css_what_wins", "fix_where_it_renders"], pushback=False, praised=False) == {"leaning_followed_css_what_wins": 1, "leaning_followed_fix_where_it_renders": 1}
    assert lean.outcome_counts(["css_what_wins"], pushback=True, praised=False) == {"leaning_followed_css_what_wins": 1, "leaning_pushback_css_what_wins": 1}
    assert lean.outcome_counts(["css_what_wins"], pushback=False, praised=True) == {"leaning_followed_css_what_wins": 1, "leaning_praise_css_what_wins": 1}
    assert lean.outcome_counts(["css_what_wins"], pushback=True, praised=True) == {"leaning_followed_css_what_wins": 1, "leaning_pushback_css_what_wins": 1}      # a message cannot be both
    assert lean.outcome_counts(["removed_since", 5, None], pushback=True, praised=False) == {} and lean.outcome_counts(None, pushback=False, praised=True) == {}


def test_a_turn_that_hit_its_step_limit_is_counted_against_the_leanings_it_used():
    assert lean.capped_counts(["css_what_wins"], True) == {"leaning_capped_css_what_wins": 1}
    assert lean.capped_counts(["css_what_wins"], False) == {} and lean.capped_counts(["unknown"], True) == {}


def test_every_outcome_counter_exists_for_every_leaning_and_is_a_safe_name():
    for group in ("leaning_used", "leaning_followed", "leaning_pushback", "leaning_praise", "leaning_capped"):
        assert {f"{group}_{entry['id']}" for entry in sl.LEANINGS} <= sc.KEYS, group


@pytest.mark.parametrize("message,expected", [
    ("perfect, thanks!", True), ("that worked perfectly", True), ("it looks great", True), ("thank you saapp", True),
    ("that didn't work", False), ("thanks but it still fails again", False), ("ok", False), ("add a test for the headroom check", False),
    ("thank you " + "word " * 45, False),
])
def test_praise_is_short_and_not_pushback(message, expected):
    from backend.utils import code_outcomes

    assert code_outcomes.is_praise(message) is expected


def test_the_turn_record_carries_the_leanings_it_used():
    from backend.utils import llm_turns

    turn = llm_turns.begin("someone")
    llm_turns.note_leanings(["css_what_wins", "fix_where_it_renders"])
    assert turn.leanings == ["css_what_wins", "fix_where_it_renders"]
    llm_turns.note_leanings(None)
    assert turn.leanings == []
    llm_turns.finish(turn)


def test_the_reply_is_marked_and_the_next_message_counted_in_the_app():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "leanings_before = [" in source and "leanings_utils.outcome_counts(leanings_before" in source
    assert "ai_message.additional_kwargs[code_outcomes.LEANINGS_MARK] = list(llm_turn.leanings)" in source and "leanings_utils.capped_counts(llm_turn.leanings, llm_turn.cap_hit)" in source
    assert "llm_turns.note_leanings(" in workflow
