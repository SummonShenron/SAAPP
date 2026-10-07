import pathlib

import pytest

from backend.components.constraints import (
    FOLLOW_UP_CONSTRAINT, GROUNDING_BLOCKS, SONIC_ASSISTANT_PERSONA, build_voice_prompt,
)
from backend.utils import wrapup_utils as wu


# ---------------------------------------------------------------------------
# What counts as a closing message: only an unambiguous sign-off with nothing else in it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "thanks", "Thanks!", "thank you", "thank you so much", "thx", "ty", "ok thanks", "okay, thanks!", "lol thanks",
    "perfect, that fixed it, thanks!", "that worked, thanks", "that's all I needed, thanks", "that's all", "all good, thanks",
    "goodnight saapp", "goodnight saapp..", "good night", "gn", "night night", "bye", "see you later", "talk later",
    "appreciate it, talk later", "got it, thanks", "makes sense, thank you", "sounds good", "awesome thanks for the help",
    "cheers", "you're the best", "great, thanks again", "ok that's all I needed, thanks", "thanks 🙏",
    # announcing the end of the session (the real exchange this was extended for)
    "oh its okay im done with work for the day lol its 6:30 pm", "im done for the day, thanks", "calling it a night, thanks for the help",
    "heading out for the day, talk tomorrow", "logging off, gn", "i'm done with work for today", "going to bed, goodnight",
])
def test_unambiguous_sign_offs_are_closing_messages(message):
    assert wu.is_closing_message(message) is True


@pytest.mark.parametrize("message", [
    "thanks! and how many cups in a quart?",               # asks something
    "thanks, can you also fix the second function",         # a new request
    "perfect, now do the same for the tests",
    "great, i'll try that tomorrow and see how it goes",    # new information
    "ok", "okay", "yes", "sure", "no", "yeah",              # ambiguous: may mean "continue"
    "thanks but that didn't work",                          # a problem, not a closing
    "goodnight? are you still there",
    "good morning",
    "my boss humiliated me in front of the team today",
    "what's the weather",
    "",
    None,
    "thanks " * 20,                                         # too long to be just a sign-off
    "im done with this bug",                                # done with a task, not leaving
    "im done for the day, can you remind me tomorrow to push the fix",   # a request
    "i'm done with work, what should i make for dinner",
    "heading to the standup now, can you summarize the thread",
    "off to fix the deploy, back in a bit",
])
def test_anything_else_is_not_a_closing_message(message):
    assert wu.is_closing_message(message) is False


# ---------------------------------------------------------------------------
# Does a reply to a closing message still fish?
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "Awesome, glad that did the trick! <<<FOLLOW_UP: Want me to check your other functions too? >>>",
    "You bet! Anything else I can help with?",
    "You bet! Let me know if you need any other quick conversions.",
    "Glad it worked. Feel free to reach out whenever.",
    "Goodnight! I'm here if you need anything tonight.",
    "Happy to help. Don't hesitate to ask if something else comes up.",
    "Nice. If you have any other questions, I'm around.",
    "Great! What's next on the list?",
    "Sleep well! Will you be deploying tomorrow?",
])
def test_replies_that_keep_the_conversation_going_are_flagged(reply):
    assert wu.closing_reply_issue(reply) is not None


@pytest.mark.parametrize("reply", [
    "Awesome, glad that did the trick! Happy coding!",
    "Goodnight! Sleep well, you definitely earned it after getting that deploy through.",
    "You got it. Enjoy the dinner!",
    "Anytime! Catch you later.",
    "You're welcome. Talk soon!",
    "",
])
def test_a_clean_close_is_not_flagged(reply):
    assert wu.closing_reply_issue(reply) is None


def test_the_reason_names_what_went_wrong():
    assert "follow-up suggestion" in wu.closing_reply_issue("Done. <<<FOLLOW_UP: more? >>>")
    assert "a question" in wu.closing_reply_issue("Done, how did it go?")
    assert "invitation to continue" in wu.closing_reply_issue("Done. Let me know if you need more.")


def test_a_question_inside_the_follow_up_tag_is_reported_as_the_tag_not_the_body():
    reply = "Glad that worked. <<<FOLLOW_UP: Want to look at the other file? >>>"
    assert wu.closing_reply_issue(reply) == "a follow-up suggestion"


def test_the_revision_prompt_keeps_the_original_includes_the_draft_and_asks_for_a_clean_close():
    prompt, reason = wu.build_closing_revision_prompt("ORIGINAL", "You bet! Anything else?", "an invitation to continue")
    assert prompt.startswith("ORIGINAL") and "You bet! Anything else?" in prompt
    assert "never mention this note" in prompt
    assert "clean close" in reason and "no question" in reason


# ---------------------------------------------------------------------------
# The rule itself is written down, in every response path, so an unrelated edit can't quietly drop it
# ---------------------------------------------------------------------------

def test_the_persona_states_the_wrap_up_rule_explicitly():
    persona = SONIC_ASSISTANT_PERSONA
    assert "KNOW WHEN TO STOP" in persona
    for banned in ('"anything else?"', '"let me know if you need more"', "tacked-on question"):
        assert banned in persona
    # ...and it is scoped to endings, so it can't be read as "wrap up early"
    assert "endings only" in persona and "never rush them toward the door" in persona


@pytest.mark.parametrize("source_type", sorted(GROUNDING_BLOCKS))
def test_every_response_path_carries_the_rule(source_type):
    prompt = build_voice_prompt(
        grounding_block=GROUNDING_BLOCKS[source_type], data="", history="", question="thanks!",
    )
    assert "KNOW WHEN TO STOP" in prompt


def test_the_follow_up_rule_says_a_follow_up_is_never_a_way_to_keep_talking():
    assert "never a way to keep the conversation going" in FOLLOW_UP_CONSTRAINT
    assert "farewells" in FOLLOW_UP_CONSTRAINT  # the pre-existing narrow rule is still there


def test_the_closing_directive_asks_for_a_short_finished_close():
    directive = wu.CLOSING_DIRECTIVE
    for required in ("No question", "no <<<FOLLOW_UP>>> tag", '"anything else?"', "say goodnight", "Let them go"):
        assert required in directive


def test_the_app_applies_the_directive_and_the_check_to_conversational_replies_only():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert 'closing_turn = source_type == "conversational" and risk_level == RISK_NONE and is_closing_message(question)' in source
    assert "prompt = prompt + CLOSING_DIRECTIVE" in source
    assert "closing_reply_issue(full_response)" in source and "build_closing_revision_prompt" in source
