import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.components.sonic_changelog import CHANGELOG
from backend.utils import self_history_utils as sh
from backend.utils.identity_checks import identity_reply_issue

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
OPEN = dict(source_type="conversational", risk_active=False, energy_tier="open", closing=False, now=NOW)


def ai(marked=False):
    return SimpleNamespace(additional_kwargs={sh.SELF_HISTORY_MARK: True} if marked else {})


def pick(message, recent=(), **overrides):
    return sh.select_self_history(message, recent, **{**OPEN, **overrides})


# ---- the changelog itself is kept honest ---------------------------------------------------------------------------

def test_the_changelog_is_well_formed():
    ids = [e["id"] for e in CHANGELOG]
    assert len(ids) == len(set(ids)) and len(ids) >= 5
    for e in CHANGELOG:
        assert {"id", "month", "title", "line", "keywords", "volunteer"} <= set(e)
        assert len(e["month"]) == 7 and e["month"][4] == "-", e["id"]
        assert sh._age_days(e["month"], NOW) is not None, e["id"]
        assert e["line"].endswith(".") and len(e["line"]) < 260, e["id"]
        assert all(k == k.lower() for k in e["keywords"] + e["volunteer"]), e["id"]


def test_no_changelog_line_claims_a_feeling_a_past_or_a_stake():
    """Every line is what Sonic may say, so each must pass the same check as any reply."""
    for e in CHANGELOG:
        assert identity_reply_issue(e["line"]) is None, (e["id"], identity_reply_issue(e["line"]))
        lowered = e["line"].lower()
        for banned in ("i remember", "i used to", "i felt", "i was frustrated", "i missed", "i struggled", "i learned"):
            assert banned not in lowered, (e["id"], banned)


# ---- when it is asked ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "what's new with you?",
    "what has changed about you lately",
    "what can you do now?",
    "any updates on you?",
    "have you changed since last time",
    "how have you improved",
    "what have you been up to?",
    "what's your backstory",
    "how did you start out?",
    "where did you come from",
    "how were you built",
    "when were you made?",
    "how old are you",
    "what were you like before?",
    "are you better now than before",
    "what's new?",
    "hey, what's new",
    "what can you do",
    "what can you do now?",
    "anything new with you?",
])
def test_questions_about_what_is_new_or_different_are_recognized(message):
    assert sh.asks_about_history(message), message


@pytest.mark.parametrize("message", [
    "what's new in python 3.13",
    "what can you tell me about bcrypt",
    "fix the bug in my parser",
    "what changed in this commit",
    "what's new in the latest release of react",
    "can you do it now? the deploy is ready",
    "any updates on the ticket?",
    "what new features does postgres 17 have",
    "what can you do with a linked list",
    "how did you start the build?",
    "how did you get the token",
    "how do you start a docker container",
    "how are you doing today",
    "",
])
def test_ordinary_questions_are_not(message):
    assert not sh.asks_about_history(message), message


def test_an_ask_returns_at_most_three_entries_with_the_ones_it_points_at_first():
    out = pick("what's new with you, can you use my calendar now?")
    assert out["mode"] == "asked" and 1 <= len(out["entries"]) <= 3
    assert out["entries"][0]["id"] == "integrations"


def test_a_general_ask_returns_the_newest_entries():
    out = pick("what's new with you?")
    assert [e["month"] for e in out["entries"]] == ["2026-10"] * 3


def test_an_ask_is_answered_even_when_a_proactive_item_took_the_turn_or_after_a_recent_mention():
    out = pick("what's new with you?", recent=[ai(marked=True)], proactive_taken=True)
    assert out and out["mode"] == "asked"


@pytest.mark.parametrize("overrides", [{"risk_active": True}, {"closing": True}, {"source_type": "kb_strict"}, {"source_type": "web"}])
def test_it_is_silent_under_safety_a_closing_message_or_a_non_conversational_route_even_when_asked(overrides):
    assert pick("what's new with you?", **overrides) is None


# ---- when it is volunteered ----------------------------------------------------------------------------------------

def test_a_message_exactly_about_an_entry_can_volunteer_it_once_in_a_while():
    out = pick("can you check my calendar for tomorrow")
    assert out["mode"] == "volunteer" and out["entries"][0]["id"] == "integrations"


def test_nothing_is_volunteered_for_an_unrelated_message():
    assert pick("how do i reverse a linked list") is None


@pytest.mark.parametrize("overrides", [
    {"energy_tier": "subdued"}, {"energy_tier": "easing"}, {"closing": True}, {"risk_active": True},
    {"proactive_taken": True}, {"source_type": "tool_output"},
])
def test_a_volunteered_mention_stays_quiet_under_every_quiet_gate(overrides):
    assert pick("can you check my calendar for tomorrow", **overrides) is None


def test_a_volunteered_mention_is_rate_limited_by_the_marker_on_earlier_replies():
    recent = [ai(marked=True)] + [ai() for _ in range(5)]
    assert pick("can you check my calendar for tomorrow", recent=recent) is None
    far_back = [ai(marked=True)] + [ai() for _ in range(sh.SELF_HISTORY_MIN_GAP)]
    assert pick("can you check my calendar for tomorrow", recent=far_back) is not None


def test_an_old_entry_is_not_volunteered():
    old = [{"id": "x", "month": "2025-01", "title": "t", "line": "I can do a thing.", "keywords": [], "volunteer": ["the thing"]}]
    assert sh.select_self_history("tell me about the thing", [], entries=old, **OPEN) is None


# ---- the prompt ----------------------------------------------------------------------------------------------------

def test_the_asked_block_lists_the_entries_with_their_month_and_forbids_embellishment_and_a_claimed_past():
    block = sh.build_self_history_block(pick("what's new with you?"))
    assert "ABOUT YOUR OWN RECENT CHANGES" in block and "(October 2026)" in block
    assert "Don't add abilities, dates or reasons that aren't listed" in block
    assert "never as something you remember going through or felt" in block
    assert "you don't exist between conversations" in block.lower()


def test_the_volunteer_block_is_one_optional_clause_never_a_story():
    block = sh.build_self_history_block(pick("can you check my calendar for tomorrow"))
    assert "SELF-HISTORY (optional)" in block and "one short clause" in block
    assert "never as a story or a feeling" in block and "sales pitch" in block


def test_nothing_selected_means_no_block():
    assert sh.build_self_history_block(None) == ""
    assert sh.build_self_history_block({"mode": "asked", "entries": []}) == ""


def test_month_labels():
    assert sh.month_label("2026-10") == "October 2026"
    assert sh.month_label("bad") == "bad"


# ---- wiring --------------------------------------------------------------------------------------------------------

def test_the_app_selects_offers_marks_and_counts_self_history():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "self_history = select_self_history(",
        "proactive_taken=bool(open_loop_entry or relatable_entry or encouragement_entry)",
        "self_history_context=build_self_history_block(self_history)",
        'tally(turn_counts, "self_history_offered")',
        'self_history["mode"] == "volunteer"',
        "ai_message.additional_kwargs[SELF_HISTORY_MARK] = True",
    ):
        assert needle in source, needle


def test_no_entry_uses_a_generic_ask_phrase_as_a_keyword_because_it_would_rank_first_for_every_ask():
    generic = {"what's new", "whats new", "what changed", "what has changed", "new features", "new feature", "updates",
               "what can you do", "anything new", "different", "new"}
    for entry in CHANGELOG:
        for keyword in entry["keywords"]:
            assert keyword not in generic, (entry["id"], keyword)


def test_a_specific_origin_question_ranks_the_matching_entry_first():
    out = pick("how were you built?")
    assert out["mode"] == "asked" and out["entries"][0]["id"] == "multi_step"
    assert pick("how did you start out?")["entries"][0]["id"] == "origin"
