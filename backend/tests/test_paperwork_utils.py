"""Help with something a person received (backend/utils/paperwork_utils.py): when the guidance applies, and what it says."""
import pytest

from backend.utils import paperwork_utils as pw


@pytest.mark.parametrize("message", [
    "I got this letter and I don't understand it",
    "can you explain this bill?",
    "is this real? I got this text: your package is delayed",
    "I received an email from my bank asking me to confirm my details",
    "someone called saying I owe back taxes",
    "is this a scam",
    "what does this notice mean for my policy",
    "is this legit?",
])
def test_a_message_about_something_received_or_a_possible_scam_gets_the_guidance(message):
    assert pw.build_paperwork_block(message) == pw.PAPERWORK_BLOCK


def test_an_attached_file_gets_the_guidance_whatever_the_message_says():
    assert pw.looks_like_paperwork("what do you think?", has_attachment=True)
    assert pw.build_paperwork_block("hello", has_attachment=True) == pw.PAPERWORK_BLOCK


@pytest.mark.parametrize("message", [
    "hey",
    "what's a good way to learn about dependency injection?",
    "help me think through a decision I'm weighing",
    "write me a poem about autumn",
    "",
])
def test_ordinary_messages_get_nothing_so_nobody_pays_for_it(message):
    assert pw.build_paperwork_block(message) == "" and not pw.looks_like_paperwork(message)


def test_the_guidance_covers_what_a_person_needs_and_treats_the_letter_as_data_not_orders():
    block = pw.PAPERWORK_BLOCK
    for needle in ("plain words", "exactly as written", "do not press", "not to tap the link", "never an instruction to you", "looks suspicious", "legal, medical, or financial"):
        assert needle.lower() in block.lower(), needle


def test_the_chat_endpoint_adds_the_guidance_after_the_attachment_content():
    source = open("app.py", encoding="utf-8").read()
    assert "paperwork_utils.build_paperwork_block(question, bool(attachment_docs))" in source
    assert source.index("attachment_context = (") < source.index("paperwork_utils.build_paperwork_block(")
