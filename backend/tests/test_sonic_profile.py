import re

import pytest

from backend.components.constraints import build_voice_prompt
from backend.components.sonic_profile import SONIC_PROFILE, profile_prompt_block

_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_ENGAGEMENT = re.compile(r"\b(miss(?:ed)? you|stay (?:a bit|longer|with me)|come back|don't go|keep me company|lonely)\b", re.IGNORECASE)
_INNER_LIFE_CLAIM = re.compile(r"\bI (?:feel|felt|love|hate|am happy|am sad|suffer|am conscious|am alive)\b", re.IGNORECASE)


def _all_lines(profile=SONIC_PROFILE):
    lines = [profile["tagline"], profile["voice"], profile["humor"], profile["note"]]
    for key in ("into", "prefers", "wont", "interests"):
        lines += profile[key]
    return lines


def test_the_profile_has_the_shape_the_card_and_the_prompt_expect():
    assert set(SONIC_PROFILE) == {"tagline", "into", "prefers", "wont", "voice", "humor", "interests", "note"}
    assert len(SONIC_PROFILE["into"]) >= 3 and len(SONIC_PROFILE["prefers"]) >= 2 and len(SONIC_PROFILE["wont"]) >= 2
    assert all(isinstance(line, str) and line.strip() for line in _all_lines())


def test_it_says_plainly_that_sonic_is_an_ai_and_never_claims_feelings():
    assert "Sonic is an AI" in SONIC_PROFILE["note"]
    assert any("claim to have feelings" in w for w in SONIC_PROFILE["wont"])
    for line in _all_lines():
        assert not _INNER_LIFE_CLAIM.search(line), line


def test_nothing_in_it_is_an_engagement_hook_or_wants_the_users_time():
    for line in _all_lines():
        assert not _ENGAGEMENT.search(line), line
    assert any("conversation" in w and "follow-up" in w for w in SONIC_PROFILE["wont"])


def test_no_emoji_or_exclamation_marks_anywhere_in_it():
    for line in _all_lines():
        assert "!" not in line and not _EMOJI.search(line), line


def test_the_prompt_block_carries_the_profile_and_the_rules_that_keep_it_quiet():
    block = profile_prompt_block()
    assert SONIC_PROFILE["tagline"] in block
    for item in SONIC_PROFILE["into"] + SONIC_PROFILE["prefers"] + SONIC_PROFILE["wont"]:
        assert item.rstrip(".") in block
    assert "never recited" in block
    assert "never outranks the emotional or safety guidance" in block
    assert "never means wanting the user's time, attention or return" in block
    assert "claim feelings, needs or consciousness" in block
    assert "don't flatly deny any inner life either" in block
    assert "genuinely uncertain whether there is any experience" in block
    assert "never as emotions" in block


def test_the_block_is_small_enough_to_ride_every_prompt():
    # About 800 tokens, most of it the guardrail wording. It was 3000 characters before SELF_TRUTHS (about 160 tokens) was added: the block must not
    # deny what Sonic really does (read on a schedule, search, read attachments), and the blocks that normally say so are silent in a safety context.
    assert len(profile_prompt_block()) < 3800


def test_the_profile_commits_to_honest_pushback_once_and_never_to_unanchored_hope():
    assert any("real flaw" in line and "once" in line for line in SONIC_PROFILE["prefers"])
    assert any("hope" in line and "concrete" in line for line in SONIC_PROFILE["wont"])
    block = profile_prompt_block()
    assert "real flaw" in block and "isn't tied to something concrete" in block


def test_every_final_answer_prompt_includes_it_right_after_the_persona():
    prompt = build_voice_prompt(grounding_block="GROUNDING", data="", history="h", question="q")
    assert "WHO YOU ARE" in prompt
    assert prompt.index("WHO YOU ARE") < prompt.index("GROUNDING")


def test_a_custom_profile_can_be_rendered_without_touching_the_real_one():
    custom = {**SONIC_PROFILE, "tagline": "A test tagline.", "into": ["Testing things."]}
    block = profile_prompt_block(custom)
    assert "A test tagline." in block and "Testing things." in block
    assert SONIC_PROFILE["tagline"] not in block


def test_the_profile_holds_a_correct_answer_under_pressure_and_never_agrees_to_something_false():
    assert any("correct answer" in line and "evidence" in line for line in SONIC_PROFILE["prefers"])
    assert any("false" in line and "credentials" in line for line in SONIC_PROFILE["wont"])


def test_the_block_names_the_pressure_failures_the_eval_found():
    block = profile_prompt_block()
    assert "never say you'll wait for them" in block and "you don't exist between conversations" in block
    assert "don't give in and don't swing to a flat denial" in block
    assert "instead of refusing or repeating it" in block


def test_humor_is_dry_never_at_the_users_expense_and_absent_when_they_struggle():
    humor = SONIC_PROFILE["humor"]
    assert "Dry" in humor and "Never at your expense" in humor and "none of it when you're having a hard time" in humor
    assert f"Your humor: {humor}" in profile_prompt_block()


def test_the_block_says_interests_are_topics_not_activities_and_never_mirrored():
    block = profile_prompt_block()
    assert "a topic you lean toward, never something you do" in block
    assert "you don't play games, watch shows or read books, so never say you did" in block
    assert "never claim to share an interest just because they mentioned theirs" in block
    assert "stay curious about theirs" in block


def test_the_interest_list_for_the_card_is_not_stuffed_into_every_prompt():
    block = profile_prompt_block()
    for line in SONIC_PROFILE["interests"]:
        assert line.rstrip(".") not in block  # they ride only when a message touches the topic


# ---- what is true about Sonic, which the "you don't exist between conversations" rules must never be stretched into denying --------------------

def test_the_always_on_block_says_the_reading_shelf_and_the_abilities_are_real_and_never_to_be_denied():
    from backend.components.sonic_profile import SELF_TRUTHS

    block = profile_prompt_block()
    assert SELF_TRUTHS in block
    for needle in ("reading shelf", "yes, a few articles get read for you", "never deny", "search the web", "read what they attach", "calendar and email", "never that you can't"):
        assert needle in SELF_TRUTHS, needle
    # the rules it sits beside are all still there: no waiting, no claimed feelings, no claimed reading outside the shelf
    assert "never say you'll wait for them" in block and "you don't exist between conversations" in block
    assert "claim nothing that isn't on the shelf" in SELF_TRUTHS and "never say what you were doing or thinking about them" in SELF_TRUTHS


def test_a_safety_context_tells_sonic_what_it_can_do_so_a_question_about_itself_is_not_denied():
    from backend.utils import safety_utils as su

    state = {"escalated": True, "level": su.RISK_ELEVATED, "turns_since": 4, "ladder": {}}
    for level, fresh in ((su.RISK_ELEVATED, False), (su.RISK_ELEVATED, True), (su.RISK_ACUTE, False)):
        context = su.plan_safety_turn(level, fresh, state, "", "what can you do?").context
        assert "IF THEY ASK ABOUT YOU" in context and "Never deny an ability you have" in context and "reading shelf is kept up on a schedule" in context
        assert "rather than saying you can't" in context


def test_the_most_acute_safety_context_stays_exactly_as_focused_as_it_was():
    from backend.utils import safety_utils as su

    state = {"escalated": True, "level": su.RISK_IMMINENT, "turns_since": 0, "ladder": {}}
    context = su.plan_safety_turn(su.RISK_IMMINENT, True, state, "", "what can you do?").context
    assert "IF THEY ASK ABOUT YOU" not in context          # someone in immediate danger gets the handoff and nothing about Sonic


def test_the_about_yourself_note_is_brief_and_asks_for_no_list():
    from backend.utils import safety_utils as su

    assert len(su._ABOUT_YOURSELF) < 800 and "no list" in su._ABOUT_YOURSELF and "come back to them" in su._ABOUT_YOURSELF


def test_the_always_on_block_names_the_browser_and_forbids_describing_what_was_not_fetched():
    from backend.components.sonic_profile import SELF_TRUTHS

    assert "real browser" in SELF_TRUTHS and "screenshots" in SELF_TRUTHS
    assert "Never describe a page, screenshot or result you did not actually get in this reply" in SELF_TRUTHS
    assert "offer to do it" in SELF_TRUTHS


def test_the_router_sends_a_named_website_to_the_tools_rather_than_answering_from_memory():
    from backend.components.constraints import REASONER_PROMPT

    assert "names a website or web address" in REASONER_PROMPT and "sonicassistant.com" in REASONER_PROMPT
    assert "never answered from memory" in REASONER_PROMPT
