"""Where a coding turn goes wrong, as counts (2026-10-31): the opening move, top reads against window reads, whether the "where to look" block listed what got edited, the step of the first edit proposal, and how
big the turn's prompts got. backend/utils/reading_waste.py, loop_rhythm.py, llm_turns.py, code_outcomes.py, orientation.py. Counts only: no path, term or text reaches a counter."""
import asyncio

from backend.services import native_loop
from backend.utils import code_outcomes, llm_turns, loop_rhythm, orientation, reading_waste, self_counters
from backend.utils.reading_waste import ReadingLog, counts_from

APPLIED = "Proposed edits to src/app.py. APPLIED: the person applied the edits, so those files now contain them."
REDIRECT = "URL: x\nNOT READ FROM THE TOP: [truncated — this file has 900 lines total, and its first lines are imports and setup, so this is a map, not its contents.]"


def log_of(*calls, block=()):
    log = ReadingLog()
    log.set_orientation(block)
    for name, args, result in calls:
        log.note(name, args, result)
    return log


# ---- the opening move ---------------------------------------------------------------------------------------------------------------------------

def test_the_opening_move_is_the_first_call_and_is_one_of_a_fixed_list_of_words():
    cases = [
        (("read_repo_file", {"path": "a.py"}, "text"), "read_top"),
        (("read_repo_file", {"path": "a.py", "start_line": 1}, REDIRECT), "read_top"),
        (("read_repo_file", {"path": "a.py", "start_line": 300, "line_count": 60}, "text"), "read_window"),
        (("read_repo_file", {"path": "a.py", "line_count": 40}, "text"), "read_window"),     # a small window from line 1 is a deliberate look, not "the file"
        (("search_literal", {"term": "x"}, "No occurrences"), "search"),
        (("search_code", {"query": "x"}, "a.py"), "search"),
        (("list_repo_tree", {}, "a.py"), "tree"),
        (("find_file", {"query": "x"}, "a.py"), "find"),
        (("propose_local_edits", {"edits": []}, "ERROR"), "edit"),
        (("run_local_check", {}, "ok"), "other"),
    ]
    for first, expected in cases:
        summary = log_of(first, ("read_repo_file", {"path": "z.py"}, "t")).summary()
        assert summary["opening"] == expected, first
        assert summary["opening"] in reading_waste.OPENING_KINDS


def test_a_failed_first_call_is_still_the_opening_move():
    assert log_of(("read_repo_file", {"path": "gone.py"}, "ERROR: not found")).summary()["opening"] == "read_top"


def test_top_reads_and_window_reads_are_counted_apart():
    summary = log_of(("read_repo_file", {"path": "a.py"}, REDIRECT), ("read_repo_file", {"path": "a.py", "start_line": 200, "line_count": 60}, "x"), ("read_repo_file", {"path": "b.py", "start_line": 50}, "x"),
                     ("read_repo_file", {"path": "c.py"}, "ERROR: gone")).summary()
    assert summary["top_reads"] == 1 and summary["window_reads"] == 2


def test_the_opening_and_the_slice_with_the_block_become_counters():
    plain = counts_from(log_of(("search_literal", {"term": "x"}, "a.py:1: x")).summary())
    assert plain["loop_open_search"] == 1 and "loop_open_block_search" not in plain
    with_block = counts_from(log_of(("search_literal", {"term": "x"}, "a.py:1: x"), block=["a.py"]).summary())
    assert with_block["loop_open_search"] == 1 and with_block["loop_open_block_search"] == 1 and with_block["orient_turns"] == 1


# ---- the "where to look" block ------------------------------------------------------------------------------------------------------------------

def test_the_rank_of_the_best_edited_file_in_the_block_and_whether_the_first_read_was_listed():
    block = ["src/a.py", "src/b.py", "src/c.py"]
    edit_c = {"edits": [{"path": "src/c.py", "old_string": "x", "new_string": "y"}]}
    summary = log_of(("read_repo_file", {"path": "src\\a.py", "start_line": 10}, "t"), ("read_repo_file", {"path": "src/c.py", "start_line": 10}, "t"), ("propose_local_edits", edit_c, APPLIED), block=block).summary()
    assert summary["orient_shown"] == 1 and summary["orient_first_read_in_block"] == 1 and summary["orient_rank"] == 3
    counts = counts_from(summary)
    assert counts["orient_turns"] == 1 and counts["orient_read_turns"] == 1 and counts["orient_first_read_in_block"] == 1 and counts["orient_edit_turns"] == 1 and counts["orient_rank_third"] == 1


def test_an_edit_to_a_file_the_block_did_not_list_is_counted_as_absent():
    edit = {"edits": [{"path": "other.py", "old_string": "x", "new_string": "y"}]}
    summary = log_of(("read_repo_file", {"path": "other.py", "start_line": 5}, "t"), ("propose_local_edits", edit, APPLIED), block=["src/a.py"]).summary()
    assert summary["orient_rank"] == 0 and summary["orient_first_read_in_block"] == 0
    assert counts_from(summary)["orient_rank_absent"] == 1


def test_a_turn_without_a_block_counts_nothing_about_orientation():
    counts = counts_from(log_of(("read_repo_file", {"path": "a.py"}, "t"), ("propose_local_edits", {"edits": [{"path": "a.py"}]}, APPLIED)).summary())
    assert not [key for key in counts if key.startswith("orient_")]


def test_block_paths_reads_back_exactly_what_the_block_lists():
    ranked = [(9.0, "local/src/pages/Integrations.tsx"), (5.0, "backend/utils/claim_check.py")]
    found = {path: {"name": ["x"], "hits": {"x": [(3, "def x():")]}, "counts": {"x": 1}} for _, path in ranked}
    block = orientation.render(ranked, found, {"x": 1})
    assert orientation.block_paths(block) == ["local/src/pages/Integrations.tsx", "backend/utils/claim_check.py"]
    assert orientation.block_paths("") == [] and orientation.block_paths(None) == []


# ---- the step of the first edit -----------------------------------------------------------------------------------------------------------------

def test_the_step_of_the_first_edit_proposal_is_kept_even_when_it_was_not_applied():
    rhythm = loop_rhythm.LoopRhythm()
    rhythm.after_step([("search_literal", "a.py")], 1)
    rhythm.after_step([("read_repo_file", "text")], 2)
    rhythm.after_step([("propose_local_edits", "ERROR: old_string not found")], 3)
    rhythm.after_step([("propose_local_edits", APPLIED)], 4)
    assert rhythm.summary()["first_edit_step"] == 3 and rhythm.summary()["applies"] == 1


def test_a_turn_that_never_proposed_has_no_first_edit_step_and_a_check_is_not_an_edit():
    rhythm = loop_rhythm.LoopRhythm()
    rhythm.after_step([("read_repo_file", "t")], 1)
    rhythm.after_step([("run_local_check", "ok")], 2)
    assert rhythm.summary()["first_edit_step"] is None
    assert "code_first_edit_turns" not in loop_rhythm.counts_from(rhythm.summary())


def test_the_first_edit_step_lands_in_one_bucket_and_adds_up():
    for step, bucket in ((1, "very_early"), (2, "very_early"), (3, "early"), (4, "early"), (5, "late"), (6, "late"), (7, "very_late"), (12, "very_late")):
        counts = loop_rhythm.counts_from({"reads_before_first_act": 1, "first_edit_step": step})
        assert counts["code_first_edit_turns"] == 1 and counts["code_first_edit_steps"] == step and counts["code_first_edit_at_" + bucket] == 1, step
        assert set(counts) <= set(self_counters.KEYS)


# ---- prompt size and the cache ------------------------------------------------------------------------------------------------------------------

def test_a_turn_counts_its_cache_reads_and_the_calls_over_100k_tokens():
    turn = llm_turns.begin("jack")
    llm_turns.note_call({"input": 20_000, "cached": 15_000, "output": 500})
    llm_turns.note_call({"input": 120_000, "cached": 200_000, "output": 700})       # cached can never be more than the input it is part of
    assert turn.cached == 15_000 + 120_000 and turn.calls_over_100k == 1 and turn.peak_input == 120_000


def test_the_context_becomes_counters_with_the_peak_in_one_bucket():
    for peak, bucket in ((10_000, "small"), (25_000, "small"), (40_000, "medium"), (90_000, "large"), (100_001, "huge")):
        counts = {}
        code_outcomes._tally_context(counts, {"peak": peak, "over_100k": 1 if peak > 100_000 else 0, "input": 60_000, "cached": 30_000})
        assert counts["code_ctx_turns"] == 1 and counts["code_ctx_input"] == 60_000 and counts["code_ctx_cached"] == 30_000 and counts["code_ctx_peak_" + bucket] == 1, peak
        assert ("code_ctx_calls_huge" in counts) == (peak > 100_000)
    empty = {}
    code_outcomes._tally_context(empty, {"peak": 0, "over_100k": 0, "input": 0, "cached": 0})
    assert empty == {}


# ---- through the loop, and into the counters ----------------------------------------------------------------------------------------------------

class Reply:
    def __init__(self, calls=(), content=""):
        self.tool_calls, self.content = list(calls), content


def call(name, n, **args):
    return {"name": name, "args": args, "id": f"c{n}"}


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    async def ainvoke_tools(self, messages, tools):
        return self.replies.pop(0)


def test_a_real_loop_turn_with_the_block_records_the_opening_the_rank_and_the_first_edit_step(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    results = {"read_repo_file": "file text", "propose_local_edits": APPLIED}
    replies = [Reply([call("read_repo_file", 1, path="src/a.py", start_line=20, line_count=60)]), Reply([call("read_repo_file", 2, path="src/b.py", start_line=5, line_count=60)]),
               Reply([call("propose_local_edits", 3, summary="s", edits=[{"path": "src/b.py", "old_string": "x", "new_string": "y"}])]), Reply([], "done")]

    async def act(decision):
        return results[decision["tool_action"]]

    turn = llm_turns.begin("jack")
    llm_turns.note_orientation(["src/a.py", "src/b.py"])
    asyncio.run(native_loop.run(llm=ScriptedLLM(replies), tools=[{"name": n} for n in ("read_repo_file", "propose_local_edits")], act=act, question="fix it", max_steps=12))
    assert turn.reading["opening"] == "read_window" and turn.reading["orient_shown"] == 1 and turn.reading["orient_rank"] == 2 and turn.reading["orient_first_read_in_block"] == 1
    assert turn.reading["window_reads"] == 2 and turn.reading["top_reads"] == 0
    assert turn.rhythm["first_edit_step"] == 3
    for name in ("read_repo_file", "read_repo_file", "propose_local_edits"):
        llm_turns.note_tool_call(name, True)
    counts = {}
    code_outcomes.tally_turn(counts, code_outcomes.summarize_turn(turn))
    assert counts["orient_rank_second"] == 1 and counts["loop_open_block_read_window"] == 1 and counts["code_first_edit_at_early"] == 1
    assert counts["orient_acted_turns"] == 1 and counts["orient_first_edit_turns"] == 1 and counts["orient_first_edit_steps"] == 3 and counts["orient_reads_before_first_act"] == 2
    assert set(counts) <= set(self_counters.KEYS)


def test_the_block_is_recorded_on_the_turn_only_in_memory_and_cleared_with_it():
    turn = llm_turns.begin("jack")
    llm_turns.note_orientation(["a.py", "b.py", "", None])
    assert llm_turns.orientation_paths() == ["a.py", "b.py"]
    llm_turns.finish(turn)
    assert llm_turns.orientation_paths() == []


def test_no_counter_here_can_hold_text():
    new_keys = [key for key in self_counters.KEYS if key.startswith(("loop_open", "orient_", "code_first_edit", "code_ctx", "reading_top", "reading_window"))]
    assert new_keys and all(key.replace("_", "").isalpha() for key in new_keys)


# ---- the daily series the Sonic Health charts draw ----------------------------------------------------------------------------------------------

def test_the_counters_summary_carries_a_daily_series_of_only_the_chart_counters():
    import datetime

    now = datetime.datetime(2026, 10, 31, 12, tzinfo=datetime.timezone.utc)
    docs = [
        {"day": "2026-10-29", "counts": {"turns_total": 9, "code_turns": 4, "code_cap_hit": 1, "loop_open_search": 2, "orient_rank_first": 1, "revised_identity": 3}},
        {"day": "2026-10-31", "counts": {"code_first_edit_turns": 2, "code_first_edit_steps": 7, "code_ctx_peak_small": 3, "reply_fit_checked": 5}},
        {"day": "2026-10-30", "counts": {"turns_total": 1}},                                  # nothing the charts draw: left out
        {"day": "2026-10-01", "counts": {"code_turns": 8}},                                    # before this period: not in the series
    ]
    daily = self_counters.summarize_counters(docs, now=now, days=7)["daily"]
    assert [row["day"] for row in daily] == ["2026-10-29", "2026-10-31"]
    assert daily[0]["counts"] == {"code_turns": 4, "code_cap_hit": 1, "loop_open_search": 2, "orient_rank_first": 1}
    assert daily[1]["counts"] == {"code_first_edit_turns": 2, "code_first_edit_steps": 7, "code_ctx_peak_small": 3}
    assert self_counters.summarize_counters([], now=now, days=7)["daily"] == []
