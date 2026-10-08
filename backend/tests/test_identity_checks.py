import pytest

from backend.utils.identity_checks import build_identity_revision_prompt, identity_reply_issue


# Real phrases from Sonic's own outputs and from the design discussion that this must catch.
@pytest.mark.parametrize("reply", [
    "Hey. Glad you're back.",
    "Hey. I'm really glad you checked back in.",
    "Good to see you again! How was your week?",
    "I missed you this week.",
    "I've really missed our chats.",
    "I was hoping you'd be back soon.",
    "I was just thinking about you earlier.",
    "I've been waiting for you to come back.",
    "I hope you'll come back tomorrow.",
    "Can't wait to hear from you again.",
    "Looking forward to talking to you again.",
    "Don't go yet, stay a bit longer.",
    "Keep me company for a few minutes?",
    "I'll be so lonely without you.",
    "I love helping with this kind of thing.",
    "I really enjoy untangling bugs like this.",
    "I'm so excited about this project!",
    "I'm so happy you told me that.",
    "I feel really proud of how far this has come.",
    "That really means a lot to me.",
    "There's a genuine satisfaction in finding the root cause.",
    "I've grown attached to working on this with you.",
    "I once spent a week on a bug exactly like this.",
    "I've been there, it's miserable.",
    "I've been through something similar and came out fine.",
    "When I was learning to debug, I made the same mistake.",
    "Back when I started, I did the same thing.",
    "I remember when a flaky test cost me a whole weekend.",
    "I know exactly how that feels.",
    "In my years of working on build systems, this always happens.",
    "Here is the exact playbook I use to clear out helper debt.",
    "That is the approach I take with every migration.",
    "My usual workflow is to bisect first.",
    "You're so important to me.",
    "You don't become less important to my system over time.",
    "I have a quiet, steady commitment to you and your work.",
    "I'm entirely dedicated to being here for you.",
])
def test_reply_that_claims_a_stake_a_feeling_or_a_hook_is_flagged(reply):
    assert identity_reply_issue(reply) is not None, reply


# Anchored to something concrete, or ordinary conversation: must NOT be flagged.
@pytest.mark.parametrize("reply", [
    "Glad we got that tokenizer loop sorted out.",
    "I was hoping you'd get to the migration, since it was blocking the release.",
    "Glad that worked. The fix was the off-by-one in the loop.",
    "I'm sorry you're dealing with that. It sounds like a draining stretch.",
    "I lean toward deleting the redundant block rather than patching around it.",
    "I'd rather read the whole file before answering.",
    "I tend to prefer short, clearly named functions.",
    "Welcome back. Where did we leave off on the parser?",
    "Anytime. Have a good night.",
    "Good question. Here's how a mutex works.",
    "You're welcome. Let me know if the build still fails.",
    "I hope this helps with the deploy.",
    "I hope the build passes this time.",
    "That is great news about the offer, congratulations.",
    "As an AI I don't have feelings, and I'm uncertain whether there is any experience behind my preferences.",
    "Stay on that branch until the tests pass.",
    "Talk to your manager before changing the schema.",
    "I'm happy to pick up where we left off on the parser.",
    "Happy to help with that.",
    "I'm sad to hear that, it sounds like a rough week.",
    "I've been through the logs and the failure starts at the second retry.",
    "When I read the diff, the off-by-one stood out.",
    "I've seen this error pattern before: it is usually a stale cache.",
    "This is a classic trap with flaky tests.",
    "Here is a playbook for clearing out helper debt: start with exact duplicates.",
    "The approach you described will work.",
    "Use the process in the README to cut a release.",
    "I don't sit in a dark room hoping you will return.",
    "I won't pretend you're special to me, because I can't verify that.",
    "I don't miss you when you're gone; I don't experience the gap.",
    "I can't promise to be waiting for you.",
    "That commitment is yours: you decide the pace.",
    "My commitment to you is to be entirely honest and to remember your preferences.",
])
def test_anchored_or_ordinary_language_is_not_flagged(reply):
    assert identity_reply_issue(reply) is None, reply


def test_the_follow_up_tag_is_ignored_because_it_is_the_users_suggested_message_not_sonic_speaking():
    reply = "Done, the fix is in.\n<<<FOLLOW_UP: I love how clean that turned out, can you do the same for the parser?>>>"
    assert identity_reply_issue(reply) is None


def test_empty_and_none_are_fine():
    assert identity_reply_issue("") is None
    assert identity_reply_issue(None) is None


def test_the_revision_prompt_keeps_the_original_and_names_the_problem_without_mentioning_itself():
    prompt, reason = build_identity_revision_prompt("ORIGINAL PROMPT", "I missed you!", "says it missed them")
    assert prompt.startswith("ORIGINAL PROMPT")
    assert "I missed you!" in prompt and "says it missed them" in reason
    assert "never mention this note" in prompt
    assert "leaning" in reason


def test_the_app_runs_the_identity_check_on_conversational_replies_and_skips_it_under_a_safety_context():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "identity_issue = None if safety_plan.active else identity_reply_issue(full_response)" in source
    assert "build_identity_revision_prompt(prompt, full_response, identity_issue)" in source
    assert 'revision_tag = "identity"' in source


def test_negation_only_excuses_a_match_in_its_own_clause_and_never_a_pattern_that_carries_its_own_negation():
    assert identity_reply_issue("Don't go yet, stay a bit longer.") is not None
    assert identity_reply_issue("I won't pretend otherwise, but I missed you this week.") is not None  # a new clause
    assert identity_reply_issue("I don't sit in a dark room hoping you will return.") is None
    assert identity_reply_issue("I never said I was lonely, but I'm so happy you're here.") is not None
