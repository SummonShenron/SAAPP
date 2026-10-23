"""A polite "can you apply the changes?" after a reply that gave the change is an edit request (2026-10-23).

With a connected folder the tool agent only treats a turn as an EDIT turn (propose_local_edits required, a "final" that only describes the change rejected) when the message reads as
an edit request. "can you apply the changes?" started with "can you", which the confirmation pattern did not allow, and the reply before it (the change as a code block, with "paste that
into index.css") did not read as an offer either. The turn then ran as an ordinary question and the model answered "I only have read access in this session" with the tool in its menu.
"""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils.agent_utils import looks_like_edit_request

GAVE_CODE = "Set these in local/src/index.css:\n```css\n--bg-main: #101112;\n```\nTwo small things to keep in mind when you paste that into the file."


def thread(*pairs):
    messages = []
    for index, text in enumerate(pairs):
        messages.append((HumanMessage if index % 2 == 0 else AIMessage)(content=text))
    return messages


def test_a_polite_confirmation_after_a_reply_that_gave_the_change_as_code_is_an_edit_request():
    assert looks_like_edit_request(thread("can you make the dark theme a bit lighter?", GAVE_CODE, "can you apply the changes?")) is True


@pytest.mark.parametrize("message", [
    "can you apply the changes?", "could you please just do it", "go ahead and apply that", "can you apply those changes", "yes please make those changes", "would you apply them",
])
def test_the_ways_people_ask_for_it_to_be_done_count_after_an_offer(message):
    assert looks_like_edit_request(thread("change the header in Chat.tsx", "Want me to apply it?", message)) is True


def test_an_explicit_request_earlier_still_counts():
    assert looks_like_edit_request(thread("change the dark theme tokens in index.css", "Here is the change", "can you apply the changes?")) is True


def test_a_confirmation_with_nothing_offered_or_asked_before_it_is_not_an_edit_request():
    assert looks_like_edit_request(thread("how does the theme work?", "It uses CSS variables defined in index.css.", "can you apply the changes?")) is False


def test_a_question_about_the_changes_is_not_an_edit_request():
    assert looks_like_edit_request(thread("thoughts on gray?", GAVE_CODE, "can you explain the changes?")) is False
