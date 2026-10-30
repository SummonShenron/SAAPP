"""The saved person a conversation has been about outranks a closer-sounding fact about someone who has not come up (backend/utils/conversation_people.py).
The real case: all evening about Yashu, then "I'm afraid I won't see her again" picked facts about Suzie and Tina."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.utils import conversation_people as cp
from backend.utils import memory_utils

QUESTION = "im so afraid i wont see her again"
SUZIE = "Jack has a relationship with Suzie that recently ended in a painful rejection."
YASHU = "Jack is interested in Yashu and has plans to see her on Friday."
VECTORS = {
    QUESTION: [1.0, 0.0],
    SUZIE: [0.95, 0.31],       # sounds closer to the question than the fact about Yashu
    YASHU: [0.8, 0.6],
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: VECTORS.get(text.strip(), [0.0, 0.1]))
    yield


def _msgs(*texts):
    return [SimpleNamespace(content=t) for t in texts]


def _store(*facts):
    for text, category in facts:
        memory_utils.save_user_fact("jack", text, category=category)
    # the user's own name is in many facts, so it is not "someone they talk about"
    for i in range(6):
        memory_utils.save_user_fact("jack", f"Jack likes hobby number {i}.", category="trait")


def _facts():
    return memory_utils.load_user_facts("jack")


def test_people_come_from_relationship_facts_in_the_middle_of_a_sentence():
    _store((SUZIE, "relationship"), (YASHU, "relationship"), ("Jack plays Elden Ring on Friday nights.", "preference"))
    names = cp.people_vocabulary(_facts())
    assert "Suzie" in names and "Yashu" in names
    assert "Jack" not in names          # in many facts: the person themselves
    assert "Friday" not in names        # a day, not a person
    assert "Elden" not in names         # not from a relationship fact


def test_presence_counts_recent_mentions_more_and_ignores_case():
    old = _msgs("tell me about suzie") + _msgs(*["something else"] * 60) + _msgs("yashu said hi", "ok")
    weights = cp.mention_weights(old, ["Suzie", "Yashu"])
    assert weights["Yashu"] > weights["Suzie"] > 0


def test_a_name_beyond_the_window_is_not_present():
    history = _msgs("suzie") + _msgs(*["filler"] * (cp.WINDOW_MESSAGES + 5))
    assert "Suzie" not in cp.mention_weights(history, ["Suzie"])


def test_the_person_the_conversation_is_about_wins_over_a_closer_sounding_old_fact():
    _store((SUZIE, "relationship"), (YASHU, "relationship"))
    without = memory_utils.fetch_relevant_user_facts("jack", QUESTION, limit=1)
    assert "Suzie" in without and "Yashu" not in without       # the old behaviour, still the default when no conversation is passed

    talk = _msgs("did yashu call?", "She said she would.", "Yashu mentioned feeling bad about cancelling", "ok", QUESTION)
    with_talk = memory_utils.fetch_relevant_user_facts("jack", QUESTION, limit=1, messages=talk)
    assert "Yashu" in with_talk and "Suzie" not in with_talk


def test_a_message_that_names_nobody_is_told_who_the_conversation_is_about():
    _store((SUZIE, "relationship"), (YASHU, "relationship"))
    talk = _msgs("Yashu cancelled", "Yashu said Sunday", QUESTION)
    block = memory_utils.fetch_relevant_user_facts("jack", QUESTION, limit=1, messages=talk)
    assert "PEOPLE THIS CONVERSATION HAS BEEN ABOUT: Yashu." in block
    assert "never guess a name" in block


def test_no_note_when_the_message_names_someone_or_nobody_is_in_the_conversation():
    _store((SUZIE, "relationship"), (YASHU, "relationship"))
    named = "i am afraid i wont see yashu again"
    talk = _msgs("Yashu cancelled", named)
    assert "PEOPLE THIS CONVERSATION" not in memory_utils.fetch_relevant_user_facts("jack", named, limit=1, messages=talk)
    assert "PEOPLE THIS CONVERSATION" not in memory_utils.fetch_relevant_user_facts("jack", QUESTION, limit=1, messages=_msgs("hello", QUESTION))
    assert cp.people_note([], QUESTION) == ""


def test_a_fact_about_nobody_named_is_not_boosted():
    assert cp.boost_for("Jack prefers dark mode.", [("Yashu", 1.0)]) == 1.0
    assert cp.boost_for("Jack is interested in Yashu.", [("Yashu", 1.0)]) == pytest.approx(1.0 + cp.BOOST)


def test_non_text_messages_are_skipped():
    assert cp.mention_weights([SimpleNamespace(content=[{"type": "image"}]), SimpleNamespace(content="Yashu")], ["Yashu"]).get("Yashu", 0) > 0


def test_the_module_logs_nothing():
    source = Path(cp.__file__).read_text(encoding="utf-8")
    assert "logging" not in source and "logger" not in source
