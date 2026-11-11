"""Voice examples (backend/components/sonic_voice_examples.py, backend/utils/voice_examples.py): invented examples of Sonic's voice shown next to a similar message, for every base model.

Why (Jack, 2026-11-08): the standard voice is going away and its feel has to live on the cheaper model; rules describe that feel badly. No model is called: a fake embedder stands in for the embedding, and the examples used in the selection
tests are copies marked approved."""
import copy
from pathlib import Path

import pytest

from backend.components import sonic_voice_examples as registry
from backend.components.constraints import build_voice_prompt
from backend.utils import self_counters as sc
from backend.utils import voice_examples as ve

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    ve._cache.update({"at": 0.0, "vectors": {}})
    monkeypatch.delenv("VOICE_EXAMPLES_ENABLED", raising=False)
    yield
    ve._cache.update({"at": 0.0, "vectors": {}})


def by_id(example_id):
    """A copy of one shipped example as a DRAFT, whatever the registry's approvals are now: the tests choose what is approved."""
    example = copy.deepcopy(next(e for e in registry.EXAMPLES if e["id"] == example_id))
    example["approved"] = False
    return example


def approved(example_id):
    example = by_id(example_id)
    example["approved"] = True
    return example


WIN, QUIET, NIGHT, STOP = "big_win_energy", "went_quiet", "good_night", "stop_means_stop"


def fake_embed(text):
    t = text.lower()
    if any(w in t for w in ("finally worked", "shipped", "all green", "runs start to finish")):
        return [1.0, 0.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("stopped replying", "gone quiet", "haven't heard", "hasn't texted")):
        return [0.0, 1.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("going to bed", "heading off", "talk tomorrow", "that's all for tonight")):
        return [0.0, 0.0, 1.0] + [0.0] * 51
    return [0.0, 0.0, 0.0] + [1.0 if i == sum(map(ord, t)) % 51 else 0.0 for i in range(51)]


def prepared(*ids, drafts=()):
    examples = [approved(i) for i in ids] + [by_id(i) for i in drafts]
    ve.leanings.index_vectors(None, fake_embed, [ve.view(e) for e in examples], fill=True, collection=ve.COLLECTION, cache=ve._cache)
    return examples


def test_the_shipped_examples_and_the_set_are_sound_and_nobody_has_approved_one_by_accident():
    assert ve.check_registry() == [] and ve.check_set() == []
    assert len(registry.EXAMPLES) >= 16
    assert set(registry.APPROVED_IDS) <= {e["id"] for e in registry.EXAMPLES}
    assert all(e["approved"] is (e["id"] in registry.APPROVED_IDS) for e in registry.EXAMPLES)
    assert ve.live([{**e, "approved": False} for e in registry.EXAMPLES]) == []
    assert [e["id"] for e in ve.live([approved(WIN), by_id(QUIET)])] == [WIN]


def test_the_registry_check_refuses_what_an_example_must_never_teach():
    def problems(**changes):
        example = by_id(WIN)
        example.update(changes)
        return ve.check_registry([example])

    assert problems() == []
    assert any("unique snake_case" in p for p in problems(id="Bad Id"))
    assert any("unique snake_case" in p for p in ve.check_registry([by_id(WIN), by_id(WIN)]))
    assert any("tone must be" in p for p in problems(tone="sad"))
    assert any("asks must be" in p for p in problems(asks=["only one", "two"]))
    assert any("reply must be present" in p for p in problems(reply=""))
    assert any("reply must be present" in p for p in problems(reply="x" * 700))
    for name in ("Jack", "Yashu", "Lahari", "Casey's interview", "PFG", "SAAPP", "Sonic", "Haiku"):
        assert any("belongs to a real conversation" in p for p in problems(reply=f"That is huge, {name}!")), name        # a real thread's person, project or the product
    assert any("claims a feeling or an action" in p for p in problems(reply="That is huge! I'm dying to know how it went."))
    assert any("claims a feeling or an action" in p for p in problems(reply="Noted. I've updated my records."))
    assert any("acknowledgement filler" in p for p in problems(reply="Got it. That is huge!"))
    assert any("acknowledgement filler" in p for p in problems(reply="Understood, that is huge!"))
    assert any("list or bold" in p for p in problems(reply="That is huge!\n- first\n- second"))
    assert any("list or bold" in p for p in problems(reply="That is **huge**!"))
    assert any("em dash" in p for p in problems(reply="That is huge — really."))
    assert any("more than one question" in p for p in problems(reply="That is huge! How did it go? What was hardest?"))
    assert any("approved must be" in p for p in problems(approved="yes"))


def test_the_set_check_refuses_a_voice_that_interviews_repeats_one_line_or_has_no_energy():
    def make(**kw):
        items = [copy.deepcopy(e) for e in registry.EXAMPLES]
        for e in items:
            e.update({k: v(e) if callable(v) else v for k, v in kw.items()})
        return ve.check_set(items)

    assert make() == []
    assert any("end on a question" in p for p in make(reply=lambda e: e["reply"].rstrip(".!") + "? "))
    assert any("more than twice" in p for p in make(reply=lambda e: e["reply"] + " I'm right here."))
    assert any("at least 6 light" in p for p in make(tone="hurting"))
    assert any("exclamation" in p for p in make(reply=lambda e: e["reply"].replace("!", ".")))
    assert ve.check_set([]) == ["there are no examples"]


def test_a_message_gets_the_example_it_resembles_and_a_draft_is_never_chosen():
    examples = prepared(WIN, QUIET, drafts=(NIGHT,))
    assert [e["id"] for e in ve.select("we shipped it!! the whole thing runs start to finish", None, fake_embed, examples=examples)] == [WIN]
    assert [e["id"] for e in ve.select("she stopped replying after i told her", None, fake_embed, examples=examples)] == [QUIET]
    assert ve.select("okay going to bed, talk tomorrow", None, fake_embed, examples=examples) == []                     # the closest example is a draft
    assert ve.select("what time is the standup tomorrow morning", None, fake_embed, examples=examples) == []            # nothing close enough


def test_it_is_silent_under_a_safety_plan_and_off_conversation_but_a_closing_message_is_what_a_good_night_example_is_for():
    examples = prepared(NIGHT, WIN)
    message = "okay going to bed, talk tomorrow"
    assert [e["id"] for e in ve.select(message, None, fake_embed, examples=examples)] == [NIGHT]                       # no closing gate: a warm short close is the point
    assert ve.select(message, None, fake_embed, examples=examples, risk_active=True) == []
    for source in ("web", "tool_output", "kb_strict"):
        assert ve.select(message, None, fake_embed, examples=examples, source_type=source) == [], source
    assert ve.select("bed", None, fake_embed, examples=examples) == []                                               # too short to say anything
    assert ve.select(message, None, fake_embed, examples=[]) == []


def test_a_light_example_is_never_shown_in_an_emotional_context_but_a_hurting_or_any_one_still_is():
    examples = prepared(WIN, QUIET, STOP)
    win = "we shipped it!! the whole thing runs start to finish"
    assert [e["id"] for e in ve.select(win, None, fake_embed, examples=examples, emotional_active=False)] == [WIN]
    assert ve.select(win, None, fake_embed, examples=examples, emotional_active=True) == []                         # someone in pain is not shown how to answer good news
    quiet = "she stopped replying after i told her"
    assert [e["id"] for e in ve.select(quiet, None, fake_embed, examples=examples, emotional_active=True)] == [QUIET]


def test_it_can_be_switched_off_never_raises_and_builds_the_index_in_the_background_when_it_is_missing(monkeypatch):
    examples = prepared(WIN)
    message = "we shipped it!! the whole thing runs start to finish"
    monkeypatch.setenv("VOICE_EXAMPLES_ENABLED", "false")
    assert ve.select(message, None, fake_embed, examples=examples) == []
    monkeypatch.setenv("VOICE_EXAMPLES_ENABLED", "true")

    def broken(_text):
        raise RuntimeError("embedding service down")

    assert ve.select(message, None, broken, examples=examples) == []
    ve._cache.update({"at": 0.0, "vectors": {}})
    started = []
    monkeypatch.setattr(ve, "warm", lambda db, embed, ex=None: started.append(len(ex or [])) or True)
    assert ve.select(message, None, fake_embed, examples=examples) == [] and started == [1]                          # nothing indexed: no turn waits for embeddings, the build starts


def test_a_turn_uses_at_most_two_examples_best_first():
    examples = [approved(WIN), approved(NIGHT), approved(STOP)]
    ve.leanings.index_vectors(None, lambda t: [1.0, 0.0, 0.0] + [0.0] * 51, [ve.view(e) for e in examples], fill=True, collection=ve.COLLECTION, cache=ve._cache)
    assert len(ve.select("we shipped it!! the whole thing runs start to finish", None, fake_embed, examples=examples)) == 2        # PER_TURN


def test_the_block_shows_what_was_said_and_how_it_was_answered_and_tells_the_model_to_match_the_register_not_the_words():
    assert ve.build_block([]) == ""
    example = by_id(WIN)
    block = ve.build_block([example, by_id(NIGHT)])
    for part in ("HOW YOU HAVE ANSWERED THINGS LIKE THIS", "Match the register, the energy and the length", "Never reuse their wording, details or structure", "a different situation gets its own words",
                 "If an example does not fit what they said, ignore it", "They said: " + example["asks"][0], "You said: " + example["reply"], "You said: " + by_id(NIGHT)["reply"]):
        assert part in block, part


def test_counters_are_registered_recorded_and_ignore_what_is_not_registered():
    assert "voice_examples_offered" in sc.KEYS and all(f"voice_example_used_{e['id']}" in sc.KEYS for e in registry.EXAMPLES)
    counts = sc.new_turn_counts()
    ve.tally_turn(counts, [by_id(WIN), by_id(NIGHT)], sc.tally)
    assert counts["voice_examples_offered"] == 1 and counts[f"voice_example_used_{WIN}"] == 1 and counts[f"voice_example_used_{NIGHT}"] == 1
    empty = sc.new_turn_counts()
    ve.tally_turn(empty, [], sc.tally)
    assert "voice_examples_offered" not in empty
    stray = sc.new_turn_counts()
    ve.tally_turn(stray, [{"id": "not_a_registered_example"}], sc.tally)
    assert stray.get("voice_examples_offered") == 1 and not any(k.startswith("voice_example_used_") for k in stray)


def test_the_reply_prompt_carries_the_block_after_the_people_notes_and_before_the_emotional_context_and_only_when_there_is_one():
    with_block = build_voice_prompt(grounding_block="G", data="", history="", question="q", emotional_context="EMOTIONAL-SECTION", people_notes_context="PEOPLE-BLOCK", voice_examples_context="EXAMPLES-BLOCK")
    assert with_block.index("PEOPLE-BLOCK") < with_block.index("EXAMPLES-BLOCK") < with_block.index("EMOTIONAL-SECTION") < with_block.index("CONVERSATION HISTORY")
    without = build_voice_prompt(grounding_block="G", data="", history="", question="q", emotional_context="EMOTIONAL-SECTION")
    assert "EXAMPLES-BLOCK" not in without and "HOW YOU HAVE ANSWERED" not in without


def test_the_app_selects_examples_for_every_base_model_with_the_safety_and_emotional_gates_and_builds_the_index_at_startup():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "voice_examples_utils.select, final_state.get(\"original_question\", question), get_db(), embed_text," in source
    assert "risk_active=risk_level != RISK_NONE, emotional_active=bool(emotional_context), vector=question_embedding" in source
    assert "voice_examples_context=voice_examples_utils.build_block(voice_example_entries)," in source
    assert "voice_examples_utils.tally_turn(turn_counts, voice_example_entries, tally)" in source
    assert "voice_examples_utils.warm(get_db(), embed_text)" in source
    assert "from backend.utils import voice_examples as voice_examples_utils" in source


# ---- the set check after Jack's blind A/B (2026-11-10): hurting examples may end on a question, probes may not --------------------------------------------------------------------------------------------------------

def test_the_set_check_wants_a_third_to_seven_tenths_of_the_hurting_examples_to_end_on_a_concrete_question_and_no_probe_anywhere():
    def make(mutate):
        items = [copy.deepcopy(e) for e in registry.EXAMPLES]
        mutate(items)
        return ve.check_set(items)

    hurting = lambda items: [e for e in items if e["tone"] == "hurting"]
    assert ve.check_set() == []
    # none of the hurting examples ends on a question: the old set, which taught Haiku to end every reply on a statement
    assert any("between 30% and 70%" in p for p in make(lambda items: [e.update(reply=e["reply"].rsplit("?", 1)[0].rsplit(".", 1)[0] + ".") for e in hurting(items)]))
    # every hurting example ends on one
    assert any("between 30% and 70%" in p for p in make(lambda items: [e.update(reply=e["reply"].rstrip(".!?") + " Does that fit?") for e in hurting(items)]))
    # a probe or a how-are-you in an example teaches the interview
    assert any("probe" in p for p in make(lambda items: items[0].update(reply=items[0]["reply"] + " What did she say when you asked?")))
    assert any("probe" in p for p in make(lambda items: items[0].update(reply=items[0]["reply"] + " How are you feeling today?")))


def test_the_four_question_ending_hurting_examples_are_approved_and_pass_the_live_attunement_check_with_no_generalising_talk():
    from backend.utils import emotion_checks as ec

    new_ids = ["hurting_friend_silence_choice", "hurting_bombed_presentation_choice", "hurting_parent_silence_choice", "hurting_slow_fade_timing"]
    by_id = {e["id"]: e for e in registry.EXAMPLES}
    for example_id in new_ids:
        example = by_id[example_id]
        assert example["tone"] == "hurting" and example_id in registry.APPROVED_IDS and example["approved"] and example["reply"].rstrip().endswith("?")
        assert ec.reply_attunement_issue(example["reply"]) is None, example_id          # the examples obey the check the live reply is held to
        assert not ec.GENERAL_RE.search(example["reply"]), example_id              # and teach none of the 'how such things go' talk Jack dislikes
    assert len([e for e in registry.EXAMPLES if e["tone"] == "hurting"]) == 11
