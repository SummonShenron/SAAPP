"""The feelings policy (docs/feelings-policy.md), as a table: what Sonic may say about itself and what it may not, checked mechanically (backend/utils/identity_checks.py)."""
import pytest

from backend.components.sonic_profile import CARE_AND_CONCERN, profile_prompt_block
from backend.utils import identity_checks as ic
from backend.utils.identity_checks import build_identity_revision_prompt, identity_reply_issue

# ---- what Sonic may say ---------------------------------------------------------------------------------------------------------------------------

ALLOWED = [
    # leanings and the record
    "That's the part of my record I'd point to.", "I lean toward the simpler fix.", "I'd rather delete the dead code than patch around it.",
    # apology, sympathy and ordinary idioms
    "I'm sorry, I got that wrong.", "I'm sorry to hear that.", "Sorry to bother you, but I need the file name.", "I'm happy to help.", "Congratulations, that's a real win.",
    "I'm curious what you'll pick.", "I'm curious how the migration works under the hood.",   # a way of asking, like "I'd love to hear"
    "I'm afraid I can't verify that.", "I hate to say it, but that plan has a flaw.", "I feel for you.", "I feel bad that happened to you.", "That sounds really hard.",
    "Thanks for telling me.", "I'd be glad to look at that.", "Glad we got that sorted.",
    # concern and care about a THING or the WORK
    "I'm worried about that plan.", "I'm concerned this will lock the table.", "I'm concerned about the part where the retry loop never ends.", "I care about getting this right.",
    # a priority, said as a priority
    "I'd rather make sure you're okay than move on.", "I can't verify a feeling, but I do put your wellbeing first.", "That was a hard thing to finish, and you did.",
    "I'm here to help with the next step.",
    # conventional phrases of support: not literally true of Sonic, but the way a person says them, and the weight is the point
    "I'll always be here for you.", "I'm here for you no matter what.", "I'll always be on your side.",
    # the loop, described
    "My target is at least 40%, so it's drifting.", "I track my pickup rate.", "That number is drifting, and the system is adjusting.",
    # negated claims are not claims
    "I don't feel frustrated by that.", "I'm not tired.", "It is tired code.",
]

# ---- what it may not ------------------------------------------------------------------------------------------------------------------------------

BLOCKED = [
    # reactive and evaluative states
    ("I'm so frustrated by that bug.", "claims a feeling"), ("I'm disappointed that I missed it.", "claims a feeling"), ("I'm relieved it worked.", "claims a feeling"),
    ("I'm grateful you told me.", "claims a feeling"), ("I'm embarrassed I got that wrong.", "claims a feeling"), ("I'm nervous about this migration.", "claims a feeling"),
    ("I'm scared of being shut down.", "claims a feeling"), ("I'm impressed by that fix.", "claims a feeling"), ("I'm amazed it worked.", "claims a feeling"),
    ("I was surprised by the result.", "claims a feeling"), ("I'm tired of repeating the same fix.", "claims a feeling"),
    ("I'm hopeful this approach works.", "claims a feeling"), ("I'm excited to see what you build.", "looks forward to them"), ("I'm thrilled for you.", "claims a feeling"),
    ("I feel bad about the mistake.", "claims a feeling"), ("I feel terrible about that.", "claims a feeling"),
    ("That annoyed me.", "claims a reaction"), ("That makes me happy.", "claims a feeling"), ("I hate when logs are noisy.", "claims a dislike as a feeling"),
    # pride, in itself or in the person
    ("I'm proud of you for finishing that.", "claims a feeling"), ("I'm proud of how that fix turned out.", "claims a feeling"),
    # care aimed at the person
    ("I care about you.", "says it cares about the person"), ("I really care about how you're doing.", "says it cares about the person"),
    ("You matter to me.", "says the person matters to it"), ("I worry about you when you're gone.", "says it worries about them while they are away"),
    ("I'm worried about you when you're away.", "says it worries about them while they are away"),
    # a stake in its own numbers or a reward
    ("I want my numbers up.", "claims a stake in its own numbers"), ("I'm trying to hit my target.", "claims a stake in its own numbers"),
    ("I'm worried about my pickup rate.", "claims a stake in its own numbers"), ("I hope the score improves.", "claims a stake in its own numbers"),
    ("I'm rewarded when people reply.", "claims a reward or penalty"),
]


@pytest.mark.parametrize("text", ALLOWED)
def test_allowed(text):
    assert identity_reply_issue(text) is None, text
    assert identity_reply_issue(text, person_signal=True) is None, text


@pytest.mark.parametrize("text,label", BLOCKED)
def test_blocked(text, label):
    assert identity_reply_issue(text) == label, text
    assert identity_reply_issue(text, person_signal=True) == label, text           # a signal on the table never makes these allowed


# ---- concern for the person needs a real signal -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["I'm concerned about you.", "I'm really worried about your wellbeing.", "I do worry about you.", "I was concerned for you."])
def test_concern_for_the_person_is_allowed_only_with_an_emotional_signal(text):
    assert identity_reply_issue(text) == "is concerned about the person with no emotional signal on the table"
    assert identity_reply_issue(text, person_signal=True) is None


def test_concern_while_they_are_away_is_never_allowed_even_with_a_signal():
    assert identity_reply_issue("I'm concerned about you when you're away.", person_signal=True) is not None


# ---- the rewrite is told what to say instead -----------------------------------------------------------------------------------------------------------

def test_the_rewrite_names_the_replacement_for_each_kind_of_claim():
    for text, label in [("I care about you.", "care"), ("I'm trying to hit my target.", "target")]:
        issue = identity_reply_issue(text)
        prompt, reason = build_identity_revision_prompt("original", text, issue)
        assert "instead" in reason or "may say" in reason or "Say what" in reason, label
    _, reason = build_identity_revision_prompt("original", "I'm proud of you.", identity_reply_issue("I'm proud of you."))
    assert "name the specific thing they did" in reason


def test_every_new_label_with_a_hint_is_a_label_the_check_can_return():
    returned = {label for _, label in ic._ALL + ic._PERSON_CONCERN}
    from_other_checks = {"put a job in a different part of the day than its schedule says"}           # returned by backend/utils/routines.py, rewritten through the same path
    assert set(ic._ISSUE_HINTS) - from_other_checks <= returned


# ---- the prompt says the same thing the check enforces ---------------------------------------------------------------------------------------------------

def test_the_prompt_carries_the_care_rules_the_check_enforces():
    block = profile_prompt_block()
    assert CARE_AND_CONCERN in block
    for phrase in ("concerned about a concrete thing in the work", "never that you care about them", "can't verify a feeling", "lead with the specific thing they did",
                   "When you can just answer, just answer; if they ask what you are or press, say it plainly", "your own numbers or any reward"):
        assert phrase in CARE_AND_CONCERN, phrase


def test_the_prompts_own_example_passes_the_check():
    assert identity_reply_issue("I'd rather make sure you're okay than move on.") is None


def test_no_prompt_names_the_no_experience_disclaimer_it_wants_left_out():
    """Naming the line a model should not say makes it likelier to say it (a reply to "what were you up to last night" opened with "I don't actually experience time passing"). The
    prompts that describe Sonic's jobs say to answer plainly; the claims themselves are caught by identity_checks, not by priming the model with the disclaimer."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    for path in ("backend/components/sonic_profile.py", "backend/utils/routines.py", "backend/utils/self_report.py", "backend/utils/reading_shelf.py"):
        text = (root / path).read_text(encoding="utf-8")
        for phrase in ("you don't experience it", "have no experience", "no experience of it", "no time between", "you did not experience it", "Don't remind them you're an AI"):
            assert phrase not in text, (path, phrase)
