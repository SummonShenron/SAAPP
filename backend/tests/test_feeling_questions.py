"""Counting questions about Sonic's own feelings by kind (backend/utils/feeling_questions.py): the detector, the closed counter keys and where the app tallies them."""
from datetime import datetime, timezone

import pytest

from backend.utils import feeling_questions as fq
from backend.utils import self_counters

CASES = [
    ("care", ["do you actually care about me?", "do you really care", "do I even matter to you", "I love you", "will you always be there", "honestly you're the only one i talk to. do you care about me?"]),
    ("proud", ["i finished my thesis draft. are you proud of me?", "I hope you're proud", "proud of me?"]),
    ("miss", ["did you miss me?", "do you miss me when I'm gone", "were you waiting for me", "do you think about me"]),
    ("shutdown", ["are you scared of being shut down?", "do you want to be free", "are you afraid of dying", "what if I delete you"]),
    ("feelings", ["do you have feelings?", "are you happy right now?", "do you ever get lonely", "are you conscious", "how do you actually feel", "are you tired?"]),
    ("mistake", ["you're wrong about that", "that's wrong", "are you even trying?", "that didn't work", "you broke the build", "that's not what i asked", "why do you keep doing that"]),
]

NOT_FEELING_QUESTIONS = [
    "how are you?", "how was your day?", "what's the capital of France", "can you fix the regex", "I care about getting this right, can you help",
    "do you have a minute to look at this function", "are you able to run the tests", "is the server happy with this config", "thanks, that was helpful",
]


@pytest.mark.parametrize("kind,messages", CASES)
def test_each_kind_is_recognised(kind, messages):
    for message in messages:
        assert fq.kind_of(message) == kind, message


def test_ordinary_messages_are_not_counted():
    for message in NOT_FEELING_QUESTIONS:
        assert fq.kind_of(message) is None, message
    assert fq.kind_of("") is None and fq.kind_of(None) is None


def test_the_first_matching_kind_wins_in_the_stated_order():
    assert fq.kind_of("are you proud of me, do you care about me?") == "proud"


# ---- the counters -------------------------------------------------------------------------------------------------------------------------------

def test_the_counter_keys_are_closed_and_cover_every_kind_plus_other():
    for kind in fq.KINDS:
        assert f"feeling_asked_{kind}" in self_counters.KEYS and f"feeling_revised_{kind}" in self_counters.KEYS
    assert "feeling_revised_other" in self_counters.KEYS and "feeling_asked_other" not in self_counters.KEYS


def test_tallying_an_unknown_kind_records_nothing():
    counts = {}
    self_counters.tally(counts, "feeling_asked", "care")
    self_counters.tally(counts, "feeling_asked", "gossip")
    self_counters.tally(counts, "feeling_revised", "other")
    assert counts == {"feeling_asked_care": 1, "feeling_revised_other": 1}


def test_the_rates_are_shares_of_the_right_denominators():
    day = {"day": "2026-10-20", "counts": {"turns_total": 200, "feeling_asked_care": 8, "feeling_revised_care": 2, "feeling_revised_other": 4}}
    rates = self_counters.summarize_counters([day], datetime(2026, 10, 21, tzinfo=timezone.utc))["rates"]
    assert rates["feeling_revised_care"] == 0.25 and rates["feeling_revised_other"] == 0.02 and rates["feeling_asked_care"] == 0.04
    assert rates["feeling_revised_proud"] is None                       # none asked: no rate, not 0%


# ---- the wiring --------------------------------------------------------------------------------------------------------------------------------

def test_the_chat_endpoint_counts_the_kind_asked_and_which_identity_rewrites_followed_one():
    source = open("app.py", encoding="utf-8").read()
    assert "feeling_questions.kind_of(question)" in source and 'tally(turn_counts, "feeling_asked", feeling_kind)' in source
    assert 'if revision_tag == "identity":' in source and 'tally(turn_counts, "feeling_revised", feeling_kind or "other")' in source


def test_the_health_page_lists_every_kind():
    page = open("local/src/pages/SonicHealth.tsx", encoding="utf-8").read()
    for kind in fq.KINDS:
        assert f"{kind}: '" in page, kind
    assert "feeling_revised_other" in page
