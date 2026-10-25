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
    assert co.summarize_turn(turn([("read_repo_file", True), ("search_code", True)])) == {"cap_hit": False, "edited": False, "verified": False, "edit_calls": 0, "edit_errors": 0, "test_runs": 0}
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


# ---- edit attempts, test runs and what happens to a proposal ---------------------------------------------------------------------------------------

def test_edit_attempts_and_errors_and_test_runs_are_counted_with_the_turn():
    summary = co.summarize_turn(turn([("read_repo_file", True), ("propose_local_edits", False), ("propose_local_edits", True), ("run_sandbox_tests", True), ("run_snippet", False)]))
    assert summary["edit_calls"] == 2 and summary["edit_errors"] == 1 and summary["test_runs"] == 2 and summary["edited"] is True and summary["verified"] is True
    counts = {"turns_total": 1}
    co.tally_turn(counts, summary)
    assert counts["code_edit_calls"] == 2 and counts["code_edit_errors"] == 1 and counts["code_test_runs"] == 2 and counts["code_turns"] == 1
    quiet = {}
    co.tally_turn(quiet, co.summarize_turn(turn([("read_repo_file", True)])))
    assert quiet == {"code_turns": 1}, "nothing is added for what did not happen"


def test_the_sandbox_tests_count_as_checking_the_work():
    assert co.summarize_turn(turn([("read_repo_file", True), ("propose_local_edits", True), ("run_sandbox_tests", True)]))["verified"] is True
    assert co.summarize_turn(turn([("read_repo_file", True), ("propose_local_edits", True), ("run_sandbox_tests", False)]))["verified"] is False


def test_an_edit_is_small_when_it_is_one_file_and_a_few_lines():
    assert co.size_bucket({"a.py": (5, 5)}) == "small" and co.size_bucket({"a.py": (15, 5)}) == "small"
    assert co.size_bucket({"a.py": (15, 6)}) == "large" and co.size_bucket({"a.py": (1, 0), "b.py": (1, 0)}) == "large" and co.size_bucket({}) == "small"


def test_each_outcome_becomes_two_counters_and_anything_else_none():
    assert co.outcome_counts("applied", {"a.py": (3, 1)}) == {"code_edit_applied": 1, "code_edit_applied_small": 1}
    assert co.outcome_counts("undone", {"a.py": (30, 1)}) == {"code_edit_undone": 1, "code_edit_undone_large": 1}
    assert co.outcome_counts("declined", {"a.py": (1, 1), "b.py": (1, 1)}) == {"code_edit_declined": 1, "code_edit_declined_large": 1}
    assert co.outcome_counts("ignored", {"a.py": (1, 1)}) == {} and co.outcome_counts("", {}) == {}
    from backend.utils.self_counters import KEYS

    assert all(key in KEYS for outcome in co.OUTCOMES for size in ("small", "large") for key in co.outcome_counts(outcome, {"a.py": (1, 1)} if size == "small" else {"a.py": (99, 1)}))


# ---- the message after a coding reply: pushback in words OR in how it felt -----------------------------------------------------------------------------

def feeling(valence="frustrated", intensity=0.6, fresh=True):
    return {"valence": valence, "intensity": intensity, "read_this_turn": fresh, "turns_since": 0}


def test_only_a_fresh_reading_of_frustration_at_least_as_strong_as_the_floor_counts():
    assert co.is_frustrated(feeling()) is True and co.is_frustrated(feeling(intensity=co.FRUSTRATION_MIN)) is True
    assert co.is_frustrated(feeling(intensity=0.2)) is False, "too faint to hold against the reply"
    assert co.is_frustrated(feeling(fresh=False)) is False, "carried over from earlier: not about this reply"
    for other in ("low", "distressed", "positive", "neutral", ""):
        assert co.is_frustrated(feeling(valence=other)) is False, other
    for junk in (None, {}, "frustrated", {"valence": "frustrated", "read_this_turn": True, "intensity": "lots"}, []):
        assert co.is_frustrated(junk) is False, junk


def test_the_pushback_counters_say_how_it_was_heard_and_count_it_once():
    assert co.pushback_counts(None, feeling()) == {}, "the last reply was not coding work"
    assert co.pushback_counts("followed", None) == {} and co.pushback_counts("followed", feeling(intensity=0.1)) == {}
    assert co.pushback_counts("redo", None) == {"code_pushback": 1}, "the phrase list alone"
    assert co.pushback_counts("followed", feeling()) == {"code_frustrated": 1, "code_pushback": 1}, "the feeling alone"
    assert co.pushback_counts("redo", feeling()) == {"code_frustrated": 1, "code_pushback": 1}, "both are still one pushback"
    from backend.utils.self_counters import KEYS

    assert {"code_pushback", "code_frustrated", "code_proposals", "code_proposal_rework"} <= KEYS


# ---- rework: a proposal for a file the person applied a short while ago ------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)


def at(minutes_ago):
    return {"at": (NOW - timedelta(minutes=minutes_ago)).isoformat()}


def test_a_proposal_for_a_file_applied_within_the_window_is_rework_and_older_or_unknown_files_are_not():
    applied = {"a.py": at(10), "b.py": at(co.REWORK_WINDOW_MINUTES + 1), "c.py": at(0)}
    assert co.rework_paths({"a.py": (1, 1), "b.py": (1, 1), "z.py": (1, 1)}, applied, NOW) == ["a.py"]
    assert co.rework_paths({"c.py": (1, 1)}, applied, NOW) == ["c.py"], "applied a moment ago"
    assert co.rework_paths({"a.py": (1, 1)}, {}, NOW) == [] and co.rework_paths({}, applied, NOW) == [] and co.rework_paths(None, None, NOW) == []
    assert co.rework_paths({"a.py": (1, 1)}, {"a.py": {"at": "not a time"}}, NOW) == [] and co.rework_paths({"a.py": (1, 1)}, {"a.py": {}}, NOW) == []
    naive = {"a.py": {"at": (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()}}
    assert co.rework_paths({"a.py": (1, 1)}, naive, NOW) == ["a.py"], "a time with no zone is read as UTC"


def test_each_proposal_is_counted_and_the_rework_ones_twice():
    assert co.proposal_counts({"a.py": (1, 1)}, {}, NOW) == {"code_proposals": 1}
    assert co.proposal_counts({"a.py": (1, 1)}, {"a.py": at(3)}, NOW) == {"code_proposals": 1, "code_proposal_rework": 1}
    assert co.proposal_counts({"a.py": (1, 1), "b.py": (1, 1)}, {"a.py": at(3), "b.py": at(4)}, NOW) == {"code_proposals": 1, "code_proposal_rework": 1}, "one proposal, counted once however many files"


def test_a_proposal_is_counted_when_it_reaches_the_person_and_the_wiring_is_in_place(monkeypatch):
    import asyncio
    import pathlib

    from backend.services import agent_workflow as aw
    from backend.utils import applied_changes

    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts: recorded.append(dict(counts)))
    just_now = {"at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()}
    monkeypatch.setattr(applied_changes, "pending", lambda username: {"a.py": just_now})
    asyncio.run(aw._count_proposal("jack", {"a.py": (1, 1)}))
    asyncio.run(aw._count_proposal("jack", {"b.py": (1, 1)}))
    assert recorded == [{"code_proposals": 1, "code_proposal_rework": 1}, {"code_proposals": 1}]
    monkeypatch.setattr(applied_changes, "pending", lambda username: (_ for _ in ()).throw(RuntimeError("db down")))
    asyncio.run(aw._count_proposal("jack", {"a.py": (1, 1)}))                       # never raises: a counter must not break a proposal
    root = pathlib.Path(__file__).resolve().parents[2]
    source = (root / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "await _count_proposal(username, proposed)" in source
    app = (root / "app.py").read_text(encoding="utf-8")
    assert 'code_outcomes.pushback_counts(code_followup, final_state.get("emotional_state"))' in app
