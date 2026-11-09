"""People notes (backend/components/sonic_people_notes.py, backend/utils/people_notes.py, docs/people-understanding-plan.md): understanding of what people commonly go through, found by meaning and put in the reply prompt.

No model is called: a fake embedder stands in for the embedding, and the notes used in the selection tests are copies marked approved."""
import copy
from pathlib import Path

import pytest

from backend.components import sonic_people_notes as registry
from backend.components.constraints import build_voice_prompt
from backend.utils import people_notes as pn
from backend.utils import self_counters as sc

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    pn._cache.update({"at": 0.0, "vectors": {}})
    monkeypatch.delenv("PEOPLE_NOTES_ENABLED", raising=False)
    yield
    pn._cache.update({"at": 0.0, "vectors": {}})


def by_id(note_id):
    """A copy of one shipped note as a DRAFT, whatever the registry's approvals are now: the tests choose what is approved."""
    note = copy.deepcopy(next(n for n in registry.NOTES if n["id"] == note_id))
    note["approved"] = False
    return note


def approved(note_id):
    note = by_id(note_id)
    note["approved"] = True
    return note


QUIET, NUMB, WAIT = "went_quiet_after_something_vulnerable", "feeling_numb", "waiting_for_a_reply"


def fake_embed(text):
    """Three directions: going quiet / numbness / waiting, by a keyword in the text. Any other text gets its own direction, orthogonal to those three."""
    t = text.lower()
    if any(w in t for w in ("quiet", "silent", "stopped replying", "haven't heard", "texted since", "gone silent")):
        return [1.0, 0.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("numb", "flat", "nothing really feels")):
        return [0.0, 1.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("waiting", "no reply", "hear back", "checking my phone")):
        return [0.0, 0.0, 1.0] + [0.0] * 51
    return [0.0, 0.0, 0.0] + [1.0 if i == sum(map(ord, t)) % 51 else 0.0 for i in range(51)]          # any other text: its own direction, so two unrelated texts are orthogonal


def prepared(*note_ids, drafts=()):
    notes = [approved(i) for i in note_ids] + [by_id(i) for i in drafts]
    pn.leanings.index_vectors(None, fake_embed, [pn.view(n) for n in notes], fill=True, collection=pn.COLLECTION, cache=pn._cache)
    return notes


def test_the_shipped_notes_are_sound_and_twenty_drafts_nobody_has_approved_by_accident():
    assert pn.check_registry() == []
    assert len(registry.NOTES) == 20
    assert all(isinstance(n["approved"], bool) for n in registry.NOTES)
    assert set(registry.APPROVED_IDS) <= {n["id"] for n in registry.NOTES}                      # an id that is not a note approves nothing
    assert all(n["approved"] is (n["id"] in registry.APPROVED_IDS) for n in registry.NOTES)      # APPROVED_IDS is the one place a note is approved
    assert pn.live([{**n, "approved": False} for n in registry.NOTES]) == []                 # a draft is never live
    assert [n["id"] for n in pn.live([approved(QUIET), by_id(NUMB)])] == [QUIET]


def test_the_registry_check_refuses_what_a_note_must_never_say():
    def problems(**changes):
        note = by_id(QUIET)
        note.update(changes)
        return pn.check_registry([note])

    assert problems() == []
    assert any("unique snake_case" in p for p in problems(id="Bad Id"))
    assert any("unique snake_case" in p for p in pn.check_registry([by_id(QUIET), by_id(QUIET)]))
    assert any("pattern must be" in p for p in problems(pattern="People replay it"))                         # no full stop
    assert any("pattern must be" in p for p in problems(pattern="x" * 400 + "."))
    assert any("speaks as Sonic" in p for p in problems(pattern="I know how that feels, and people often replay it."))
    assert any("speaks as Sonic" in p for p in problems(helps="My advice is to take a walk."))
    assert any("carries a quotation" in p for p in problems(pattern='People often say "why me" and replay it.'))
    assert any("labels or diagnoses" in p for p in problems(pattern="People who are anxiously attached replay it."))
    assert any("labels or diagnoses" in p for p in problems(avoid="Telling them they have a disorder."))
    assert any("asks must be" in p for p in problems(asks=["only one"]))
    assert any("asks must be" in p for p in problems(asks=["a", "b", "c", "x" * 200]))
    assert any("at least one evidence" in p for p in problems(evidence=[]))
    assert any("kind must be" in p for p in problems(evidence=[{"kind": "memory", "on": "2026-11-08", "what": "From a chat."}]))
    assert any("needs a date" in p for p in problems(evidence=[{"kind": "hand", "on": "yesterday", "what": "Written."}]))
    assert any("at least 3 independent people" in p for p in problems(evidence=[{"kind": "record", "on": "2026-11-08", "what": "An aggregate.", "people": 2}]))
    assert problems(evidence=[{"kind": "record", "on": "2026-11-08", "what": "An aggregate.", "people": 3}]) == []
    assert any("approved must be" in p for p in problems(approved="yes"))


def test_a_message_gets_the_note_it_resembles_and_a_draft_is_never_chosen():
    notes = prepared(QUIET, NUMB, drafts=(WAIT,))
    assert [n["id"] for n in pn.select("she stopped replying after i told her how i feel", None, fake_embed, notes=notes)] == [QUIET]
    assert [n["id"] for n in pn.select("i just feel numb lately and don't know why", None, fake_embed, notes=notes)] == [NUMB]
    assert pn.select("it's been days and i'm still waiting to hear back", None, fake_embed, notes=notes) == []       # the closest note is a draft: not used
    assert pn.select("what time is the standup tomorrow, do you know", None, fake_embed, notes=notes) == []         # nothing close enough


def test_it_stays_silent_under_a_safety_plan_on_a_closing_message_off_conversation_and_for_a_short_message():
    notes = prepared(QUIET)
    message = "she stopped replying after i told her how i feel"
    assert [n["id"] for n in pn.select(message, None, fake_embed, notes=notes)] == [QUIET]
    assert pn.select(message, None, fake_embed, notes=notes, risk_active=True) == []
    assert pn.select(message, None, fake_embed, notes=notes, closing=True) == []
    for source in ("web", "tool_output", "kb_strict"):
        assert pn.select(message, None, fake_embed, notes=notes, source_type=source) == [], source
    assert pn.select("quiet", None, fake_embed, notes=notes) == []                                                    # too short to say anything
    assert pn.select(message, None, fake_embed, notes=[]) == []


def test_it_can_be_switched_off_and_never_raises(monkeypatch):
    notes = prepared(QUIET)
    message = "she stopped replying after i told her how i feel"
    monkeypatch.setenv("PEOPLE_NOTES_ENABLED", "false")
    assert pn.select(message, None, fake_embed, notes=notes) == []
    monkeypatch.setenv("PEOPLE_NOTES_ENABLED", "true")

    def broken(_text):
        raise RuntimeError("embedding service down")

    assert pn.select(message, None, broken, notes=notes) == []                                                       # a failed embedding leaves the note out; the turn goes on


def test_a_turn_uses_at_most_two_notes_best_first_and_builds_the_index_in_the_background_when_it_is_missing(monkeypatch):
    notes = [approved(QUIET), approved(WAIT), approved("blocked_or_cut_off")]
    pn.leanings.index_vectors(None, lambda t: [1.0, 0.0, 0.0] + [0.0] * 51, [pn.view(n) for n in notes], fill=True, collection=pn.COLLECTION, cache=pn._cache)
    assert len(pn.select("she went quiet on me after i opened up", None, fake_embed, notes=notes)) == 2              # PER_TURN
    pn._cache.update({"at": 0.0, "vectors": {}})
    started = []
    monkeypatch.setattr(pn, "warm", lambda db, embed, n=None: started.append(len(n or [])) or True)
    assert pn.select("she went quiet on me after i opened up", None, fake_embed, notes=notes) == []                  # nothing indexed yet: no turn waits for embeddings
    assert started == [3]                                                                                            # ...but the build is started


def test_the_block_tells_the_model_how_to_use_a_note_and_carries_its_four_parts():
    assert pn.build_block([]) == ""
    note = by_id(QUIET)
    block = pn.build_block([note])
    for part in ("WHAT PEOPLE IN THIS SPOT OFTEN GO THROUGH", "a read, never your own experience", "to say something that shows you understand", "not to ask a question",
                 "Never say you know how it feels", "never label the person", "never state what another person feels or means as fact", "skip a note that does not fit what they said",
                 "When: " + note["situation"], "Often: " + note["pattern"], "Tends to help: " + note["helps"], "Avoid: " + note["avoid"]):
        assert part in block, part
    many = [by_id(i) for i in (QUIET, "blocked_or_cut_off", WAIT, NUMB)]
    capped = pn.build_block(many)
    assert capped.count("- When:") >= 1 and len(capped) <= pn.MAX_BLOCK_CHARS + 600                                  # a note that would pass the cap is left out, never cut in half
    assert pn.build_block(many[:1]) in capped


def test_counters_are_registered_recorded_and_ignore_what_is_not_registered():
    assert "people_notes_offered" in sc.KEYS
    assert all(f"people_note_used_{n['id']}" in sc.KEYS for n in registry.NOTES)
    counts = sc.new_turn_counts()
    pn.tally_turn(counts, [by_id(QUIET), by_id(NUMB)], sc.tally)
    assert counts["people_notes_offered"] == 1 and counts[f"people_note_used_{QUIET}"] == 1 and counts[f"people_note_used_{NUMB}"] == 1
    empty = sc.new_turn_counts()
    pn.tally_turn(empty, [], sc.tally)
    assert "people_notes_offered" not in empty
    stray = sc.new_turn_counts()
    pn.tally_turn(stray, [{"id": "not_a_registered_note"}], sc.tally)
    assert stray.get("people_notes_offered") == 1 and not any(k.startswith("people_note_used_") for k in stray)       # an unknown note adds no counter of its own


def test_the_reply_prompt_carries_the_block_just_before_the_emotional_context_and_only_when_there_is_one():
    with_block = build_voice_prompt(grounding_block="G", data="", history="", question="q", emotional_context="EMOTIONAL-SECTION", people_notes_context="PEOPLE-BLOCK")
    assert with_block.index("PEOPLE-BLOCK") < with_block.index("EMOTIONAL-SECTION") < with_block.index("CONVERSATION HISTORY")
    without = build_voice_prompt(grounding_block="G", data="", history="", question="q", emotional_context="EMOTIONAL-SECTION")
    assert "PEOPLE-BLOCK" not in without and "WHAT PEOPLE IN THIS SPOT" not in without


def test_the_app_selects_notes_for_every_base_model_with_the_safety_and_closing_gates_and_builds_the_index_at_startup():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "people_notes_utils.select, final_state.get(\"original_question\", question), get_db(), embed_text," in source
    assert "risk_active=risk_level != RISK_NONE, closing=is_closing_message(question), vector=question_embedding" in source
    assert "people_notes_context=people_notes_utils.build_block(people_note_entries)," in source
    assert "people_notes_utils.tally_turn(turn_counts, people_note_entries, tally)" in source
    assert "people_notes_utils.warm(get_db(), embed_text)" in source
    assert "from backend.utils import people_notes as people_notes_utils" in source
