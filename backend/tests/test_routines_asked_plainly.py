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


# ---- "do you have a busy day today?": a question about Sonic's OWN day (found 2026-10-22: it was answered from the person's calendar, and "you do have a schedule" got the reading shelf) -----

@pytest.mark.parametrize("message", [
    "Do you have a busy day today?", "do you have a busy day", "are you busy today?", "Are you free later", "do you have a schedule?", "what does your day look like",
    "what's your day look like today", "how busy are you today", "do you have any plans tonight", "You do have a schedule tho.. I know you have stuff going today",
    "I know you have stuff going on today", "you have a schedule",
])
def test_a_question_about_its_own_day_or_schedule_reaches_the_routines_answer(message):
    assert rt.asks_about_routines(message), message


@pytest.mark.parametrize("message", [
    "do you have a plan for the migration?", "do you have a minute?", "can you check my calendar for today", "what's on my schedule today", "do you have access to my calendar",
    "do you have the schedule for the release", "do you have a schedule for the release?", "i have a busy day today", "are you able to run the tests", "do you have plans to refactor this",
])
def test_questions_about_the_persons_own_day_or_about_the_work_are_not_taken_for_it(message):
    assert not rt.asks_about_routines(message), message


def test_the_routines_answer_says_it_is_about_its_own_day_and_not_the_persons():
    from datetime import datetime, timezone

    class Db:
        def __getitem__(self, name):
            class Coll:
                def find(self, *a, **k):
                    class Cur:
                        def sort(self, *a): return self
                        def limit(self, *a): return self
                        def __iter__(self): return iter([])
                    return Cur()
            return Coll()

    block = rt.build_routines_context(Db(), "America/Chicago", is_admin=False, now=datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc))
    assert "YOUR day, not theirs" in block and "do not say you have no schedule" in block and "these jobs are your schedule" in block


def test_the_coordinator_sends_such_a_question_to_conversation_and_not_to_the_calendar_tool():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    assert "self_report.kind_of(_content_of(_messages[-1])) or routines.asks_about_routines(_content_of(_messages[-1]))" in source
