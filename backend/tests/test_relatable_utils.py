import pathlib
from types import SimpleNamespace

import pytest

from backend.components.sonic_profile import RELATABLE, SONIC_PROFILE
from backend.utils import relatable_utils as ru
from backend.utils.identity_checks import identity_reply_issue

OPEN = dict(source_type="conversational", risk_active=False, energy_tier="open", closing=False)


def pick(message, recent=(), **overrides):
    return ru.select_relatable(message, list(recent), **{**OPEN, **overrides})


def ai(offered=False):
    return SimpleNamespace(additional_kwargs={ru.RELATABLE_MARK: True} if offered else {})


def human():
    return SimpleNamespace(additional_kwargs={})


def test_a_message_that_overlaps_with_a_profile_habit_gets_that_entry():
    entry = pick("this flaky test keeps failing randomly and it's driving me up the wall")
    assert entry and entry["id"] == "intermittent_failures"
    assert pick("any idea how to bisect this, which commit broke it?")["id"] == "find_the_commit"
    assert pick("what should i call this variable name")["id"] == "naming"


def test_an_unrelated_message_gets_nothing():
    assert pick("what's the capital of France") is None
    assert pick("") is None


def test_triggers_match_whole_words_not_fragments():
    assert pick("the cleanup crew arrived") is not None  # "cleanup" is a trigger on its own
    assert pick("unusedness is not a word i use") is None  # "unused" inside a longer word does not match
    assert pick("a bisection of the angle") is None


def test_it_is_silent_under_a_safety_context_a_held_down_emotional_context_a_closing_message_and_grounded_answers():
    message = "this flaky test is eating my whole afternoon"
    assert pick(message) is not None
    assert pick(message, risk_active=True) is None
    assert pick(message, energy_tier="subdued") is None
    assert pick(message, energy_tier="easing") is None
    assert pick(message, closing=True) is None
    assert pick(message, source_type="kb_strict") is None
    assert pick(message, source_type="kb_open") is None
    assert pick(message, source_type="tool_output") is not None
    assert pick(message, source_type="web") is not None


def test_it_stays_occasional_one_offer_then_a_gap_of_eight_messages():
    message = "this flaky test again"
    assert pick(message, [human(), ai(offered=True)] + [human(), ai()] * 2) is None  # 6 messages since
    recent = [ai(offered=True)] + [human(), ai()] * 4  # 9 messages since: the offer is out of the window
    assert pick(message, recent) is not None
    assert pick(message, [ai(offered=True), human()]) is None


def test_recently_offered_only_reads_the_marker_and_the_last_few_messages():
    assert not ru.recently_offered([])
    assert not ru.recently_offered([human(), ai()])
    assert ru.recently_offered([ai(offered=True), human()])
    assert not ru.recently_offered([ai(offered=True)] + [human()] * 8)


def test_the_best_match_wins_when_several_overlap():
    entry = pick("this flaky intermittent test and a redundant helper")
    assert entry["id"] == "intermittent_failures"


def test_the_prompt_block_offers_one_clause_that_may_be_skipped_and_is_never_a_story():
    block = ru.build_relatable_block(RELATABLE[0])
    assert RELATABLE[0]["line"] in block
    assert "if, and only if, it fits naturally" in block and "ONE short clause" in block
    assert "Skip it" in block and "never present it as something you went through" in block
    assert ru.build_relatable_block(None) == ""


def test_the_profile_entries_are_well_formed_and_restate_things_the_profile_already_says():
    ids = [e["id"] for e in RELATABLE]
    assert len(ids) == len(set(ids)) and len(ids) >= 4
    for entry in RELATABLE:
        assert entry["triggers"] and all(t == t.lower() for t in entry["triggers"])
        assert entry["line"].endswith(".") and "!" not in entry["line"]
        # leaning language, no feeling words, and nothing that would trip the identity check itself
        assert identity_reply_issue(entry["line"]) is None, entry["line"]
        for feeling in ("love", "enjoy", "excited", "favorite", "satisf"):
            assert feeling not in entry["line"].lower()
    profile_text = " ".join(SONIC_PROFILE["into"] + SONIC_PROFILE["prefers"]).lower()
    for anchor in ("commit", "contradictory sentence", "variable names", "redundant code"):
        assert anchor in profile_text


@pytest.mark.parametrize("reply,expected", [
    ("I'd rather delete it than patch around it.", True),
    ("I lean toward bisecting first.", True),
    ("I tend to check what a green test actually covers.", True),
    ("Intermittent failures are the kind of issue I trust the least.", True),
    ("Reading closely is what I lean on most.", True),
    ("I'd least trust a failure that vanishes on re-run.", True),
    ("Here is the fix for the loop.", False),
    ("", False),
])
def test_mentions_itself_spots_stated_leanings(reply, expected):
    assert ru.mentions_itself(reply) is expected


def test_the_app_selects_the_line_offers_it_marks_the_reply_and_counts_it():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "relatable_entry = select_relatable(",
        "relatable_context=build_relatable_block(relatable_entry)",
        "ai_message.additional_kwargs[RELATABLE_MARK] = True",
        'tally(turn_counts, "relatable_offered")',
        'tally(turn_counts, "self_mention")',
        "closing=is_closing_message(question)",
        "risk_active=risk_level != RISK_NONE",
    ):
        assert needle in source, needle
