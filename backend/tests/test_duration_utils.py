import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import duration_utils as du
from backend.utils.identity_checks import identity_reply_issue

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
TZ = "America/Chicago"


# ---- recognizing the question ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "how long have we been talking?",
    "how long have we known each other",
    "how long have we been chatting, like a few months?",
    "when did we first talk?",
    "when did we start talking",
    "when did we meet lol",
    "when did i start using you",
    "how long have i been using you",
    "when was our first conversation",
    "how long have you known me",
    "hey, random question: how long have you and i been talking",
])
def test_questions_about_how_long_they_have_talked_are_recognized(message):
    assert du.asks_how_long(message), message


@pytest.mark.parametrize("message", [
    "how long have we been talking about this bug?",
    "how long have we been talking about the migration",
    "when did we start talking about the parser?",
    "how long does the build take",
    "how long have you been running this job",
    "when did the deploy start",
    "how long have i been using this regex wrong",
    "how are we doing",
    "",
])
def test_ordinary_questions_and_questions_about_a_topic_are_not(message):
    assert not du.asks_how_long(message), message


# ---- reading the facts ----------------------------------------------------------------------------------------------

class _Col:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, flt, projection=None, sort=None):
        hits = [d for d in self.docs if d["username"] == flt["username"] and d.get("created_at")]
        if sort:
            hits.sort(key=lambda d: d["created_at"])
        return {"created_at": hits[0]["created_at"]} if hits else None

    def count_documents(self, flt):
        return sum(1 for d in self.docs if d["username"] == flt["username"])


class _DB:
    def __init__(self, docs):
        self.col = _Col(docs)

    def __getitem__(self, name):
        assert name == "conversations"
        return self.col


def test_the_facts_are_the_earliest_saved_conversation_and_how_many_are_saved():
    db = _DB([
        {"username": "u", "created_at": "2026-08-01T10:00:00+00:00"},
        {"username": "u", "created_at": "2026-06-14T09:00:00+00:00"},
        {"username": "u", "created_at": "2026-09-20T09:00:00+00:00"},
        {"username": "other", "created_at": "2026-01-01T00:00:00+00:00"},
    ])
    facts = du.conversation_facts_from_db(db, "u")
    assert facts == {"first_at": datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc), "count": 3}


def test_a_user_with_nothing_saved_has_no_first_date_and_a_count_of_zero():
    assert du.conversation_facts_from_db(_DB([]), "u") == {"first_at": None, "count": 0}


def test_an_unreadable_history_is_none_not_a_guess(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    assert du.conversation_facts("u") is None


# ---- what Sonic is told ---------------------------------------------------------------------------------------------

def test_the_block_states_the_earliest_saved_conversation_with_month_year_and_how_long_ago():
    facts = {"first_at": datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc), "count": 38}
    block = du.build_duration_context(facts, NOW, TZ)
    assert "earliest saved conversation" in block and "June 2026" in block and "about 4 months ago" in block
    assert "38 conversations are saved" in block


def test_the_block_never_asks_for_a_feeling_a_milestone_or_a_count_of_days_and_admits_what_it_cannot_see():
    block = du.build_duration_context({"first_at": datetime(2026, 6, 14, tzinfo=timezone.utc), "count": 2}, NOW, TZ)
    for rule in ("Give it no feeling and no meaning", "don't call it a milestone or an anniversary", "don't count days",
                 "don't add a promise about the future", "anything they deleted is gone",
                 "Don't describe what you talked about in it"):
        assert rule in block, rule
    assert 'say "earliest saved conversation", not "since we met"' in block


def test_one_conversation_is_singular_and_a_conversation_from_today_says_today():
    block = du.build_duration_context({"first_at": NOW - timedelta(hours=3), "count": 1}, NOW, TZ)
    assert "(today)" in block and "and 1 conversation is saved in total" in block and "1 conversation are" not in block


def test_nothing_saved_says_so_and_a_failed_lookup_says_it_cannot_check_instead_of_guessing():
    none_saved = du.build_duration_context({"first_at": None, "count": 0}, NOW, TZ)
    assert "no earlier saved conversations" in none_saved and "Give it no feeling" in none_saved
    failed = du.build_duration_context(None, NOW, TZ)
    assert "can't read the conversation history" in failed and "don't guess a date or a number" in failed


def test_the_month_is_in_the_users_own_timezone_so_a_late_evening_conversation_is_not_pushed_into_next_month():
    late = datetime(2026, 7, 1, 3, 30, tzinfo=timezone.utc)  # still June 30 evening in Chicago
    assert du.month_year(late, TZ) == "June 2026"
    assert du.month_year(late, "UTC") == "July 2026"


# ---- the wording backstop -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "It's been a joy getting to know you over these months.",
    "These past few months of talking with you have been wonderful.",
    "Happy anniversary!",
    "Congratulations on our milestone.",
    "I've treasured our conversations since June.",
    "What a meaningful stretch of our conversations all this time.",
])
def test_putting_a_feeling_or_a_milestone_on_how_long_they_have_talked_is_flagged(reply):
    assert identity_reply_issue(reply) is not None, reply


@pytest.mark.parametrize("reply", [
    "Your earliest saved conversation is from June 2026, about four months ago, and 38 conversations are saved.",
    "The earliest conversation I can see is from June 2026. Anything you deleted wouldn't show up.",
    "I can only see saved conversations, and the earliest is from June.",
    "It's been about four months by the saved record.",
])
def test_stating_the_duration_as_a_plain_fact_is_fine(reply):
    assert identity_reply_issue(reply) is None, reply


# ---- wiring ---------------------------------------------------------------------------------------------------------

def test_the_app_adds_the_fact_only_when_asked_in_plain_conversation_for_a_signed_in_user_and_counts_it():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    start = source.index("duration_context = \"\"")
    window = source[start: start + 700]
    for needle in ('source_type == "conversational"', "risk_level == RISK_NONE", "not is_guest_username(username)",
                   "duration_utils.asks_how_long(", "duration_utils.conversation_facts, username",
                   'tally(turn_counts, "duration_offered")'):
        assert needle in window, needle
    assert "duration_context=duration_context," in source


def test_the_voice_prompt_carries_the_block_only_when_given_and_the_counter_exists():
    from backend.components.constraints import build_voice_prompt
    from backend.utils.self_counters import KEYS

    assert "duration_offered" in KEYS
    assert "HOW LONG YOU HAVE TALKED" in build_voice_prompt("G", "d", "h", "q", duration_context="\nHOW LONG YOU HAVE TALKED: x\n")
    assert "HOW LONG YOU HAVE TALKED" not in build_voice_prompt("G", "d", "h", "q")
