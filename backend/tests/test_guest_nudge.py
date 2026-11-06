"""The mid-chat nudge for the shared guest (backend/utils/guest_nudge.py; docs/guest-mode-plan.md step 4)."""
import re

import pytest

from backend.utils import guest_nudge as gn
from backend.utils.guest_limits import PORTFOLIO_GUEST
from backend.utils.self_counters import KEYS, new_turn_counts, tally


def ask(message, who=PORTFOLIO_GUEST):
    return gn.feature_for(who, message)


def test_each_thing_only_an_account_can_do_picks_its_card():
    assert ask("Can you run my tests and tell me what broke?") == "checks"
    assert ask("run our test suite on my machine") == "checks"
    assert ask("Please connect my project folder") == "fix"
    assert ask("Fix the bug in my code, it crashes on login") == "fix"
    assert ask("refactor the parser in my repo") == "fix"
    assert ask("Tidy up my messy Downloads folder") == "tidy"
    assert ask("organize my files by date") == "tidy"
    assert ask("Remember that I'm learning Rust") == "memory"
    assert ask("don't forget I prefer short answers") == "memory"
    assert ask("From now on, answer in bullet points") == "memory"


def test_the_most_specific_card_wins_when_a_message_asks_for_two():
    assert ask("Remember that I want you to run my tests first") == "checks"


def test_a_question_that_has_nothing_to_do_with_an_account_gets_no_card_so_the_nudge_is_never_noise():
    for text in (
        "What was changed in the last pull request?",
        "Where in the code does Sonic Assistant decide which documents a person is allowed to read?",
        "Run some Python to find the first ten prime numbers that are also Fibonacci numbers, and show me the output.",
        "Search the web for what changed in the latest Python release and give me five bullet points.",
        "Who is Jack Harper, and what has he built?",
        "How does Sonic Assistant keep one person's data separate from another's?",
        "Can you sort this list: 3, 1, 2?",                      # sorting something that is not their folder
        "I need to run the tests in that repo, how does CI do it?",
        "So what changed in the latest Python release?", "From now until Friday the demo is free to try.", "I will remember to say thanks.",
        "hi", "", None,
    ):
        assert ask(text) is None, text


def test_the_nudge_is_for_the_shared_guest_only():
    for who in ("jack@example.com", "guest_bty", "guest", "", None):
        assert gn.feature_for(who, "remember that I like tea") is None, who


def test_every_account_card_on_the_page_is_caught_by_the_word_it_is_keyed_by():
    """The page's own example texts (local/src/welcomeCards.ts), so a card cannot be reworded into one the server no longer recognizes."""
    source = open("local/src/welcomeCards.ts", encoding="utf-8").read()
    cards = re.findall(r"key: '(\w+)', label: '[^']+', action: 'signup', text: (?:\"([^\"]+)\"|'([^']+)')", source)
    assert sorted(k for k, _, _ in cards) == sorted(gn.FEATURES)
    for key, double, single in cards:
        assert ask(double or single) == key, (key, double or single)


def test_the_counter_group_covers_every_feature_and_counts_only_the_feature():
    counts = new_turn_counts()
    for feature in gn.FEATURES:
        assert f"guest_nudge_{feature}" in KEYS
        tally(counts, "guest_nudge", feature)
    assert {k: v for k, v in counts.items() if k.startswith("guest_nudge")} == {f"guest_nudge_{f}": 1 for f in gn.FEATURES}
    tally(counts, "guest_nudge", "anything else")                   # an unknown word is dropped, never recorded
    assert "guest_nudge_anything else" not in counts


def test_the_chat_stream_counts_the_nudge_with_the_turn_and_sends_it_after_the_reply():
    source = open("app.py", encoding="utf-8").read()
    head = source[source.index("nudge_feature = guest_nudge.feature_for(username, question)"):]
    assert head.index('tally(turn_counts, "guest_nudge", nudge_feature)') < head.index("record_turn_counts, turn_counts")
    assert "'event': 'account_nudge', 'feature': nudge_feature" in head
    assert head.index("record_turn_counts, turn_counts") < head.index("'event': 'account_nudge'")
