"""Situation facts (backend/components/sonic_situation_facts.py, backend/utils/situation_facts.py): reviewed facts about how a kind of situation works, shown to the reply only for a hurting person and only once a person approves them.

Why (2026-11-13): Haiku, left to find a fact itself, guessed and said the guess confidently; a fact about the world must come from a reviewed source. No model is called: a fake embedder stands in for the embedding, and the entries used in the
selection tests are made here (never relying on which shipped ones are approved)."""
from pathlib import Path

import pytest

from backend.components import sonic_situation_facts as registry
from backend.components.constraints import build_voice_prompt
from backend.utils import self_counters as sc
from backend.utils import situation_facts as sf

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    sf._cache.update({"at": 0.0, "vectors": {}})
    monkeypatch.delenv("SITUATION_FACTS_ENABLED", raising=False)
    yield
    sf._cache.update({"at": 0.0, "vectors": {}})


def entry(entry_id="interview_loop", approved=True, **changes):
    base = {"id": entry_id, "situation": "A step of an interview went badly and they fear the process is over.",
            "asks": ["the coding round went badly and i think i'm out", "i froze in the interview and now i'm spiraling", "bombed the technical screen"],
            "facts": ["At companies that run several rounds, a weak answer in one round is weighed with the notes from the others unless that round is a stated pass-or-fail gate."],
            "shows": "One round is one input.", "approved": approved}
    base.update(changes)
    return base


QUIET = lambda: entry("client_silence", situation="A client has gone quiet after a proposal.", asks=["my client hasn't replied since the proposal", "client went silent after the quote", "no word from the client in a week"],
                      facts=["At clients with budget approval steps, a proposal passes through other people before anyone replies."])


def fake_embed(text):
    t = text.lower()
    if any(w in t for w in ("coding round", "froze in the interview", "technical screen", "interview")):
        return [1.0, 0.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("client", "proposal", "quote")):
        return [0.0, 1.0, 0.0] + [0.0] * 51
    return [0.0, 0.0, 0.0] + [1.0 if i == sum(map(ord, t)) % 51 else 0.0 for i in range(51)]


def prepared(*entries):
    sf.leanings.index_vectors(None, fake_embed, [sf.view(e) for e in entries], fill=True, collection=sf.COLLECTION, cache=sf._cache)
    return list(entries)


def test_the_shipped_facts_are_sound_and_nobody_has_approved_one_by_accident():
    assert sf.check_registry() == []
    assert set(registry.APPROVED_IDS) <= {e["id"] for e in registry.FACTS}
    assert all(e["approved"] is (e["id"] in registry.APPROVED_IDS) for e in registry.FACTS)
    assert sf.live([{**e, "approved": False} for e in registry.FACTS]) == []
    assert [e["id"] for e in sf.live([entry(), QUIET() | {"approved": False}])] == ["interview_loop"]


def test_the_registry_check_refuses_what_a_fact_must_never_say():
    def problems(**changes):
        return sf.check_registry([entry(**changes)])

    assert problems() == []
    assert any("unique snake_case" in p for p in problems(id="Bad Id")) and any("unique snake_case" in p for p in sf.check_registry([entry(), entry()]))
    assert any("situation of at most" in p for p in problems(situation=""))
    assert any("asks must be" in p for p in problems(asks=["one", "two"]))
    assert any("one to three facts" in p for p in problems(facts=[])) and any("one to three facts" in p for p in problems(facts=["A.", "B.", "C.", "D."]))
    assert any("one sentence" in p for p in problems(facts=["First thing. Second thing."])) and any("one sentence" in p for p in problems(facts=["x" * 300]))
    assert any("often or usually" in p for p in problems(facts=["Hiring decisions often wait on every interviewer."]))        # the check a reply is held to
    assert any("often or usually" in p for p in problems(facts=["A delay can be about the budget cycle."]))
    assert any("statistic" in p for p in problems(facts=["Roughly 40% of candidates hear nothing for a week."])) and any("statistic" in p for p in problems(facts=["About 3 in 4 applicants are filtered by software."]))
    assert any("em dash" in p for p in problems(facts=["Each round is scored on its own — not together."])) and any("em dash" in p for p in problems(facts=["- Each round is scored on its own."]))
    for name in ("Jack", "Yashu", "Lahari", "Casey", "PFG", "SAAPP", "Sonic", "Haiku"):
        assert any("belongs to a real conversation" in p for p in problems(situation=f"{name} had a bad interview")), name
    assert any("identity check" in p for p in problems(facts=["I have been through that interview myself."]))
    assert any("approved must be" in p for p in problems(approved="yes"))


def test_a_message_gets_the_fact_it_resembles_and_a_draft_is_never_chosen():
    entries = prepared(entry(), entry("client_silence", approved=False, **{k: v for k, v in QUIET().items() if k not in ("id", "approved")}))
    emotional = dict(emotional_active=True, entries=entries)
    assert [e["id"] for e in sf.select("the technical screen went badly and i'm spiraling", None, fake_embed, **emotional)] == ["interview_loop"]
    assert sf.select("my client hasn't replied since the proposal", None, fake_embed, **emotional) == []                  # the closest entry is a draft
    assert sf.select("what time is the standup tomorrow morning", None, fake_embed, **emotional) == []                    # nothing close enough


def test_it_is_for_a_hurting_person_on_a_conversational_reply_and_silent_under_a_safety_plan():
    entries = prepared(entry())
    message = "the technical screen went badly and i'm spiraling"
    assert [e["id"] for e in sf.select(message, None, fake_embed, entries=entries, emotional_active=True)] == ["interview_loop"]
    assert sf.select(message, None, fake_embed, entries=entries) == []                                                        # no mood was read: a casual question
    assert sf.select(message, None, fake_embed, entries=entries, emotional_active=True, risk_active=True) == []
    for source in ("web", "tool_output", "kb_strict"):
        assert sf.select(message, None, fake_embed, entries=entries, emotional_active=True, source_type=source) == [], source
    assert sf.select("bed", None, fake_embed, entries=entries, emotional_active=True) == []                               # too short
    assert sf.select(message, None, fake_embed, entries=[], emotional_active=True) == []


def test_the_feature_can_be_switched_off(monkeypatch):
    entries = prepared(entry())
    monkeypatch.setenv("SITUATION_FACTS_ENABLED", "off")
    assert sf.select("the technical screen went badly and i'm spiraling", None, fake_embed, entries=entries, emotional_active=True) == []


def test_at_most_one_fact_a_turn_by_default_and_a_failed_embedding_gives_nothing():
    entries = prepared(entry(), entry("interview_two", asks=["the coding round went badly", "froze in the interview", "technical screen disaster"]))
    assert len(sf.select("the technical screen went badly", None, fake_embed, entries=entries, emotional_active=True)) == sf.PER_TURN == 1
    assert sf.select("the technical screen went badly", None, lambda t: None, entries=prepared(entry()), emotional_active=True) == []
    def boom(text):
        raise RuntimeError("provider down")
    assert sf.select("the technical screen went badly", None, boom, entries=entries, emotional_active=True) == []


def test_the_block_says_the_facts_are_general_and_adds_no_claim_and_sits_inside_the_voice_prompt():
    block = sf.build_block([entry(), QUIET()])
    assert block.startswith("\n" + sf.BLOCK_HEAD) and "Use at most one" in block and "not as a promise about their case" in block and "Do not add any other claim about their situation" in block
    assert "- At companies that run several rounds" in block and "- At clients with budget approval steps" in block
    assert sf.build_block([]) == ""
    prompt = build_voice_prompt(emotional_context="\nEMOTIONAL CONTEXT: low.\n" + block, question="the technical screen went badly", history="", grounding_block="", data="")
    assert "FACTS ABOUT HOW THIS KIND OF SITUATION WORKS" in prompt and "pass-or-fail gate" in prompt


def test_the_counters_are_registered_for_every_fact_and_tallied_per_turn():
    assert "situation_facts_offered" in sc.KEYS
    assert all(f"situation_fact_used_{e['id']}" in sc.KEYS for e in registry.FACTS)
    counts = sc.new_turn_counts()
    sf.tally_turn(counts, [], sc.tally)
    assert counts == {"turns_total": 1}
    first = registry.FACTS[0]
    sf.tally_turn(counts, [first], sc.tally)
    assert counts["situation_facts_offered"] == 1 and counts[f"situation_fact_used_{first['id']}"] == 1


def test_the_app_selects_facts_for_a_hurting_person_only_and_puts_them_in_the_emotional_context():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    call = source[source.index("situation_fact_entries = await asyncio.to_thread("):]
    call = call[:call.index("emotional_context += situation_facts_utils.build_block(situation_fact_entries)")]
    assert "emotional_active=bool(emotional_context)" in call and "risk_active=risk_level != RISK_NONE" in call and "source_type=source_type" in call
    assert "situation_facts_utils.tally_turn(turn_counts, situation_fact_entries, tally)" in call
    assert source.index("situation_fact_entries = await") < source.index("emotional_context=emotional_context")           # before the prompt is built
