"""The most natural way to ask what Sonic has been doing reaches the routines answer, and the self-history answer defers to it (found from a real reply that said "I don't exist
between chats" and then described background jobs and a finished task in the same breath)."""
import pytest

from backend.utils import routines as rt
from backend.utils import self_history_utils as sh


@pytest.mark.parametrize("message", [
    "good morning saapp what have you been up to?",
    "what have you been up to?",
    "what've you been up to",
    "what have you been doing",
    "what are you up to",
    "whatcha been up to",
    "what have you been up to since yesterday",
])
def test_asking_plainly_what_it_has_been_up_to_reaches_the_routines_answer(message):
    assert rt.asks_about_routines(message)


@pytest.mark.parametrize("message", [
    "how was your night",
    "what do you do all day",
    "what are you thinking about",
    "what are you doing in that file",
    "can you check what the server has been up to",
    "good morning",
])
def test_other_questions_are_left_alone(message):
    assert not rt.asks_about_routines(message)


def test_the_self_history_answer_never_flatly_denies_existing_and_defers_to_the_routines_section():
    entries = [{"id": "x", "month": "2026-10", "title": "t", "line": "I can now do a thing.", "keywords": [], "volunteer": []}]
    block = sh.build_self_history_block({"mode": "asked", "entries": entries})
    assert "never a flat \"I don't exist\"" in block and "say what it lists first" in block and "a few jobs do run on their own" in block
