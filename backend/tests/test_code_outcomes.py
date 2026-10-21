"""How coding work ends, as aggregate counters (backend/utils/code_outcomes.py)."""
import pytest

from backend.utils import code_outcomes as co
from backend.utils import llm_turns, self_counters


@pytest.fixture(autouse=True)
def _clean():
    llm_turns.reset()
    yield
    llm_turns.reset()


def turn(tools=(), steps=3, cap=False, loop=True):
    """A finished turn as the app sees it: the tools the loop called ((name, ok) pairs) and how the loop ended."""
    t = llm_turns.begin("sam")
    for name, ok in tools:
        llm_turns.note_tool_call(name, ok)
    if loop:
        llm_turns.note_tool_loop(steps, cap)
    return t


# ---- which turns are coding turns -------------------------------------------------------------------------------------------------------------

def test_a_turn_that_read_the_repo_is_a_coding_turn_and_one_that_searched_the_web_is_not():
    assert co.summarize_turn(turn([("read_repo_file", True), ("search_code", True)])) == {"cap_hit": False, "edited": False, "verified": False}
    assert co.summarize_turn(turn([("web_search", True), ("read_url", True)])) is None


def test_a_turn_without_a_tool_loop_is_not_a_coding_turn_even_if_a_tool_name_was_noted():
    assert co.summarize_turn(turn([("read_repo_file", True)], loop=False)) is None
    assert co.summarize_turn(None) is None


def test_hitting_the_step_cap_is_recorded():
    assert co.summarize_turn(turn([("search_code", True)], steps=8, cap=True))["cap_hit"] is True


def test_an_edit_counts_only_when_it_did_not_come_back_as_an_error():
    assert co.summarize_turn(turn([("read_repo_file", True), ("propose_local_edits", False)]))["edited"] is False
    assert co.summarize_turn(turn([("read_repo_file", True), ("propose_local_edits", True)]))["edited"] is True


def test_an_edit_is_verified_only_when_tests_or_a_snippet_ran_successfully_in_the_same_turn():
    edit = ("propose_local_edits", True)
    assert co.summarize_turn(turn([edit, ("run_repo_tests", True)]))["verified"] is True
    assert co.summarize_turn(turn([edit, ("run_snippet", True)]))["verified"] is True
    assert co.summarize_turn(turn([edit, ("run_repo_tests", False)]))["verified"] is False
    assert co.summarize_turn(turn([edit]))["verified"] is False
    assert co.summarize_turn(turn([("read_repo_file", True), ("run_repo_tests", True)]))["verified"] is False       # tests with no edit: nothing was verified


# ---- tallying ------------------------------------------------------------------------------------------------------------------------------------

def test_a_coding_turn_adds_to_the_closed_counter_keys_only():
    counts = self_counters.new_turn_counts()
    co.tally_turn(counts, {"cap_hit": True, "edited": True, "verified": False})
    assert counts == {"turns_total": 1, "code_turns": 1, "code_cap_hit": 1, "code_edited": 1}
    co.tally_turn(counts, None)
    assert counts["code_turns"] == 1
    assert {"code_turns", "code_cap_hit", "code_edited", "code_verified", "code_followed", "code_redo"} <= self_counters.KEYS


# ---- the next message --------------------------------------------------------------------------------------------------------------------------

MARKED = {co.CODE_MARK: True}


def test_a_message_after_coding_work_is_read_as_redo_when_it_says_it_did_not_work():
    for text in ("that didn't work", "it still fails with the same error", "tests are still failing", "nope, same error again", "that's wrong, it should return a list",
                 "now it doesn't compile", "you broke the build", "still broken"):
        assert co.followup(MARKED, text) == "redo", text


def test_other_messages_after_coding_work_are_just_followed():
    for text in ("thanks, that works", "great, now add a test for the empty case", "can you also rename the helper?", "perfect"):
        assert co.followup(MARKED, text) == "followed", text


def test_nothing_is_counted_when_the_previous_reply_was_not_coding_work():
    assert co.followup({}, "that didn't work") is None
    assert co.followup(None, "that didn't work") is None


def test_followups_are_tallied_with_redos_counted_in_both():
    counts = {}
    co.tally_followup(counts, "followed")
    co.tally_followup(counts, "redo")
    co.tally_followup(counts, None)
    assert counts == {"code_followed": 2, "code_redo": 1}


# ---- rates and the wiring -------------------------------------------------------------------------------------------------------------------------

def test_the_rates_are_shares_of_the_right_denominators():
    day = {"day": "2026-10-20", "counts": {"turns_total": 100, "code_turns": 20, "code_cap_hit": 5, "code_edited": 8, "code_verified": 2, "code_followed": 16, "code_redo": 4}}
    from datetime import datetime, timezone

    rates = self_counters.summarize_counters([day], datetime(2026, 10, 21, tzinfo=timezone.utc))["rates"]
    assert rates["code_cap_hit"] == 0.25 and rates["code_verified"] == 0.25 and rates["code_redo"] == 0.25


def test_the_chat_endpoint_marks_and_counts_coding_turns_and_reads_the_next_message():
    source = open("app.py", encoding="utf-8").read()
    assert "code_outcomes.followup(previous_marks, question)" in source and "code_outcomes.tally_followup(turn_counts, code_followup)" in source
    assert "code_outcomes.summarize_turn(llm_turn)" in source and "ai_message.additional_kwargs[code_outcomes.CODE_MARK] = True" in source and "code_outcomes.tally_turn(turn_counts, code_summary)" in source


def test_the_health_page_shows_the_coding_rows():
    page = open("local/src/pages/SonicHealth.tsx", encoding="utf-8").read()
    assert "rates.code_cap_hit" in page and "rates.code_verified" in page and "rates.code_redo" in page


def test_the_repo_tools_match_the_ones_the_trace_announces():
    from backend.services import agent_workflow

    assert co.REPO_TOOLS == agent_workflow._REPO_SOURCE_TOOLS
