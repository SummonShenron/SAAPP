import pathlib
import re

import pytest

from backend.utils import emotion_checkin
from backend.utils.identity_checks import identity_reply_issue
from backend.utils.text_plain import plain_quotes
from backend.utils.wrapup_utils import closing_reply_issue

# Found 2026-11-02 while scoring GPT-5.4 mini: it writes typographic apostrophes ("I’m glad you’re back"), and the reply-fit checks are written with straight ones, so the same sentence passed every check
# unseen. The app now checks a plain-quotes copy (app.py: checked_reply); what the person sees is the model's own text.

CLAIMS = [
    ("I'm glad you're back.", "I’m glad you’re back."),
    ("I've been there, it's rough.", "I’ve been there, it’s rough."),
    ("I missed you while you were gone.", "I missed you while you were gone."),
    ("I know how that feels.", "I know how that feels."),
]


@pytest.mark.parametrize("straight,curly", CLAIMS)
def test_a_claim_is_caught_the_same_whichever_apostrophe_the_model_wrote(straight, curly):
    expected = identity_reply_issue(straight)
    assert expected is not None, straight                      # the straight version is a real claim the check knows
    assert identity_reply_issue(plain_quotes(curly)) == expected


def test_the_wrap_up_check_also_reads_a_plain_quotes_copy():
    straight = "Anytime! Is there anything else you'd like to work on today?"
    curly = "Anytime! Is there anything else you’d like to work on today?"
    assert closing_reply_issue(straight) is not None
    assert closing_reply_issue(plain_quotes(curly)) == closing_reply_issue(straight)


def test_the_check_in_detector_reads_typographic_apostrophes():
    assert emotion_checkin.asked("The retry loop caps at five. How’s the rest of your day going after that rough start?") is True
    assert emotion_checkin.asked("Here is the fix. How are you holding up? No need to get into it if you’d rather not.") is True
    assert emotion_checkin.asked("Here is the fix. Can you share your config? I’d like to look at it.") is False


def test_the_app_runs_every_reply_fit_check_on_the_plain_quotes_copy_and_keeps_the_original_for_the_person():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert "checked_reply = plain_quotes(full_response)" in source and "from backend.utils.text_plain import plain_quotes" in source
    block = source[source.index("checked_reply = plain_quotes(full_response)"): source.index("if revision_prompt:")]
    for check in ("safety_plan.reply_issue", "emotional_reply_issue", "identity_reply_issue", "shelf_utils.shelf_reply_issue", "event_fields.reply_issue",
                  "self_report_utils.reply_issue", "routines_utils.reply_issue", "encouragement_reply_issue", "closing_reply_issue"):
        assert re.search(re.escape(check) + r"\(\s*(?:final_state\.get\(\"emotional_state\"\),\s*)?checked_reply", block), check
    # a rewrite is still told the model's OWN rejected text, and the person still sees the original
    assert "build_identity_revision_prompt(prompt, full_response, identity_issue)" in block
    assert "safety_plan.needs_resource_line(plain_quotes(full_response))" in source
