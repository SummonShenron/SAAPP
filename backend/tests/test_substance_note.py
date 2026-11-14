import asyncio
import pathlib
from datetime import datetime, timezone

from backend.utils import self_counters as sc
from backend.utils import substance_note as sn

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)


def _state(valence="low", need="venting", **extra):
    """The shape emotion_utils builds for a message that was read this turn (valence, intensity, need, read_this_turn, with the clock fields the decay reads)."""
    return {"valence": valence, "intensity": 0.6, "need": need, "read_this_turn": True, "updated_at": NOW.isoformat(), "turns_since": 0, **extra}


def test_the_note_applies_exactly_where_the_fresh_low_block_is_used():
    assert sn.applies(_state(), NOW, safety_active=False, source_type="conversational") is True
    for need in ("venting", "reassurance", "none"):
        assert sn.applies(_state("distressed", need), NOW, safety_active=False, source_type="conversational"), need
    assert not sn.applies(_state(need="solving"), NOW, safety_active=False, source_type="conversational")          # they want concrete help
    assert not sn.applies(_state(), NOW, safety_active=True, source_type="conversational")                         # a safety plan has its own rules
    assert not sn.applies(_state(), NOW, safety_active=False, source_type="kb_strict")                             # a data-grounded reply
    assert not sn.applies(_state("frustrated"), NOW, safety_active=False, source_type="conversational")           # frustration has its own block
    assert not sn.applies(_state("positive"), NOW, safety_active=False, source_type="conversational")
    assert not sn.applies(_state(read_this_turn=False), NOW, safety_active=False, source_type="conversational")   # a carried feeling is not read this turn
    assert not sn.applies(_state(lift_this_turn=True), NOW, safety_active=False, source_type="conversational")
    assert not sn.applies(None, NOW, safety_active=False, source_type="conversational") and not sn.applies({}, NOW, safety_active=False, source_type="conversational")


def test_the_note_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("SUBSTANCE_NOTE", raising=False)
    assert sn.enabled() is False
    for value in ("on", "1", "TRUE", "yes"):
        monkeypatch.setenv("SUBSTANCE_NOTE", value)
        assert sn.enabled() is True, value
    for value in ("", "off", "0", "no"):
        monkeypatch.setenv("SUBSTANCE_NOTE", value)
        assert sn.enabled() is False, value


def test_the_prompt_carries_the_conversation_and_the_message_and_asks_for_no_fact_about_the_world():
    prompt = sn.build_prompt("User: one\nAssistant: two", "  three  ")
    assert "User: one\nAssistant: two" in prompt and prompt.rstrip().endswith("three")
    assert "(this is the first message)" in sn.build_prompt("", "hello")
    assert len(sn.build_prompt("x" * 10000, "hello")) < len(sn.NOTE_PROMPT) + sn.MAX_HISTORY_CHARS + 50      # only the tail of a long conversation
    assert '"fact"' not in sn.NOTE_PROMPT and "Never write anything you infer, assume, or know about the world" in sn.NOTE_PROMPT


HISTORY = "User: i have been applying to jobs for months\nAssistant: that is wearing\nUser: not even a rejection, just silence"


def test_parse_reads_a_note_and_keeps_only_what_is_grounded_and_specific_to_the_person():
    raw = '```json\n{"new": "  Even a no is missing now.  ", "earlier": "They said they had been applying for months.", "avoid": "the volume point"}\n```'
    assert sn.parse(raw, HISTORY) == {"new": "Even a no is missing now.", "earlier": "They said they had been applying for months.", "avoid": "the volume point"}
    assert sn.parse('Here is the note: {"new": "x", "earlier": "", "avoid": ""} done', HISTORY) == {"new": "x", "earlier": "", "avoid": ""}              # text around the object
    # an `earlier` that is not in the conversation is dropped (the first try's invention: a claim nobody made)
    assert sn.parse('{"new": "x", "earlier": "They were promoted last year and love their manager.", "avoid": ""}', HISTORY)["earlier"] == ""
    # and one that talks about people in general or how such things usually go
    for text in ("People often apply for months and hear nothing, and they said months.", "Most people who apply for months get silence.", "It is usually because they are applying for months."):
        assert sn.parse('{"new": "x", "earlier": "%s", "avoid": ""}' % text, HISTORY)["earlier"] == "", text
    assert len(sn.parse('{"new": "%s", "earlier": "", "avoid": ""}' % ("w " * 400), HISTORY)["new"]) <= sn.MAX_FIELD_CHARS


def test_parse_gives_nothing_for_an_unusable_answer():
    for raw in ("", "not json", "[1, 2]", '{"new": "", "earlier": "", "avoid": ""}', '{"new": 5, "earlier": null, "avoid": []}', None, 7):
        assert sn.parse(raw, HISTORY) is None, raw


def test_the_block_lists_only_the_fields_that_have_words_and_says_to_never_mention_it():
    block = sn.build_block({"new": "A harsher conclusion.", "earlier": "", "avoid": "The volume point."})
    assert block.startswith(sn.BLOCK_HEAD) and "never mention them" in block
    assert "- New in this message: A harsher conclusion.\n" in block and "- You have already said, so do not say it again: The volume point.\n" in block
    assert "Something they told you earlier" not in block
    assert sn.build_block(None) == "" and sn.build_block({}) == "" and sn.build_block({"new": "", "earlier": "", "avoid": ""}) == ""


class _Reply:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.prompts = content, error, []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return _Reply(self.content)


def test_write_note_asks_the_model_once_and_reads_text_or_blocks_and_never_raises():
    good = '{"new": "x", "earlier": "", "avoid": ""}'
    fake = _FakeLLM(good)
    assert asyncio.run(sn.write_note(fake, HISTORY, "hello")) == {"new": "x", "earlier": "", "avoid": ""}
    assert len(fake.prompts) == 1 and "hello" in fake.prompts[0]
    # a reply made of blocks (thinking first, then text), as the Claude wrapper can return
    blocks = _FakeLLM([{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": good}])
    assert asyncio.run(sn.write_note(blocks, HISTORY, "hello")) == {"new": "x", "earlier": "", "avoid": ""}
    assert asyncio.run(sn.write_note(_FakeLLM(error=RuntimeError("provider down")), HISTORY, "hello")) is None
    assert asyncio.run(sn.write_note(_FakeLLM("not json"), HISTORY, "hello")) is None


def test_the_note_counters_are_registered_and_tallied_by_what_happened_to_the_note():
    assert set(sc.SUBSTANCE_KEYS) <= sc.KEYS
    counts = sc.new_turn_counts()
    sn.tally_turn(counts, None, sc.tally)
    assert counts["substance_note_asked"] == 1 and counts["substance_note_empty"] == 1 and "substance_note_used" not in counts
    counts = sc.new_turn_counts()
    sn.tally_turn(counts, {"new": "x", "earlier": "", "avoid": ""}, sc.tally)
    assert counts["substance_note_used"] == 1 and "substance_note_earlier" not in counts and "substance_note_empty" not in counts
    counts = sc.new_turn_counts()
    sn.tally_turn(counts, {"new": "x", "earlier": "y", "avoid": ""}, sc.tally)
    assert counts["substance_note_used"] == 1 and counts["substance_note_earlier"] == 1


def test_the_app_writes_the_note_only_when_switched_on_applicable_and_the_reasoner_model_is_haiku():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    block = source[source.index("if substance_note.enabled() and substance_note.applies("):]
    block = block[:block.index("user_tz = await")]
    assert "safety_active=safety_plan.active" in block and "source_type=source_type" in block
    assert "reasoner_llm_for(reply_choice)" in block and 'if _note_kind == "haiku":' in block      # a person on another model never has it written by a provider they did not pick
    assert "substance_note.tally_turn(turn_counts, _note, tally)" in block and "emotional_context += substance_note.build_block(_note)" in block
