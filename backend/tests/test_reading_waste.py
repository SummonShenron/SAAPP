"""Measuring whether a coding turn reads the right material (backend/utils/reading_waste.py, native_loop.py, llm_turns.py, code_outcomes.py): counts only, nothing about what was read is kept."""
import asyncio

from backend.services import native_loop
from backend.utils import code_outcomes, llm_turns, reading_waste, self_counters
from backend.utils.reading_waste import ReadingLog, counts_from

APPLIED = "Proposed edits to src/app.py. APPLIED: the person applied the edits, so those files now contain them."


def log_of(*calls):
    log = ReadingLog()
    for name, args, result in calls:
        log.note(name, args, result)
    return log


def test_files_read_are_counted_once_each_and_a_second_read_is_a_reread():
    summary = log_of(("read_repo_file", {"path": "a.py"}, "text"), ("read_repo_file", {"path": "b.py"}, "text"), ("read_repo_file", {"path": "a.py"}, "text"), ("read_repo_file", {"path": "a\\b.py"}, "text"),
                     ("read_repo_file", {"path": "a/b.py"}, "text")).summary()
    assert summary["files_read"] == 3 and summary["rereads"] == 2                 # a.py twice, a\b.py and a/b.py are the same file


def test_a_read_that_failed_or_named_no_file_is_not_a_read():
    summary = log_of(("read_repo_file", {"path": "gone.py"}, "ERROR: not found"), ("read_repo_file", {}, "text"), ("read_repo_file", None, "text"), ("read_repo_file", {"path": ""}, "text")).summary()
    assert summary["files_read"] == 0 and summary["rereads"] == 0


def test_searches_count_and_the_ones_that_found_nothing_are_told_apart():
    summary = log_of(("search_literal", {"term": "x"}, "Found 3 matches\na.py:1: x"), ("search_literal", {"term": "y"}, "No occurrences of 'y' in 700 files scanned."), ("search_code", {"query": "z"}, "No matches."),
                     ("search_code", {"query": "w"}, "a.py\nb.py"), ("search_literal", {"term": "e"}, "ERROR: no term given")).summary()
    assert summary["searches"] == 4 and summary["empty_searches"] == 2                # the error is not a search


def test_tree_listings_and_find_file_calls_are_counted():
    summary = log_of(("list_repo_tree", {}, "a.py\nb.py"), ("list_repo_tree", {}, "ERROR: x"), ("find_file", {"query": "q"}, "a.py"), ("find_file", {"query": "q2"}, "a.py")).summary()
    assert summary["tree_listings"] == 1 and summary["find_files"] == 2


def test_the_files_edited_are_the_files_read_only_when_the_edit_was_applied():
    reads = [("read_repo_file", {"path": "a.py"}, "t"), ("read_repo_file", {"path": "b.py"}, "t"), ("read_repo_file", {"path": "c.py"}, "t")]
    edit = {"summary": "s", "edits": [{"path": "a.py", "old_string": "x", "new_string": "y"}, {"path": "new.py", "content": "z"}]}
    applied = log_of(*reads, ("propose_local_edits", edit, APPLIED)).summary()
    assert applied["applied"] == 1 and applied["files_edited"] == 2 and applied["edited_and_read"] == 1 and applied["files_read"] == 3        # new.py was created, not read first
    declined = log_of(*reads, ("propose_local_edits", edit, "DECLINED: not that")).summary()
    assert declined["applied"] == 0 and declined["files_edited"] == 0 and declined["edited_and_read"] == 0
    failed = log_of(*reads, ("propose_local_edits", edit, "ERROR: old_string not found")).summary()
    assert failed["applied"] == 0


def test_an_edit_that_changes_a_file_read_in_an_earlier_step_still_counts_as_used():
    log = log_of(("read_repo_file", {"path": "a.py"}, "t"), ("propose_local_edits", {"edits": [{"path": "a.py"}]}, APPLIED), ("read_repo_file", {"path": "b.py"}, "t"), ("propose_local_edits", {"edits": [{"path": "b.py"}]}, APPLIED))
    assert log.summary()["edited_and_read"] == 2 and log.summary()["applied"] == 2


def test_the_summary_keeps_only_counts_never_a_path_or_a_term():
    summary = log_of(("read_repo_file", {"path": "secret/plan.py"}, "t"), ("search_literal", {"term": "SECRET_TERM"}, "No occurrences")).summary()
    assert all(isinstance(value, int) for value in summary.values()) and "secret" not in str(summary) and "SECRET_TERM" not in str(summary)


def test_the_counters_for_a_turn_that_read_nothing_are_empty_and_edit_turn_ones_need_an_applied_edit():
    assert counts_from(None) == {} and counts_from("x") == {} and counts_from(ReadingLog().summary()) == {}
    read_only = counts_from(log_of(("read_repo_file", {"path": "a.py"}, "t"), ("search_literal", {"term": "x"}, "No occurrences")).summary())
    assert read_only == {"reading_turns": 1, "reading_files_read": 1, "reading_searches": 1, "reading_empty_searches": 1}
    edited = counts_from(log_of(("read_repo_file", {"path": "a.py"}, "t"), ("read_repo_file", {"path": "b.py"}, "t"), ("list_repo_tree", {}, "x"), ("propose_local_edits", {"edits": [{"path": "a.py"}]}, APPLIED)).summary())
    assert edited["reading_edit_turns"] == 1 and edited["reading_edit_turn_files_read"] == 2 and edited["reading_edit_turn_files_used"] == 1 and edited["reading_tree_listings"] == 1
    assert set(edited) <= set(self_counters.KEYS) and set(self_counters.READING_KEYS) <= self_counters.KEYS


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


def test_the_native_loop_records_what_it_read_and_it_becomes_counters_for_the_turn(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    results = {"list_repo_tree": "a.py\nb.py\nc.py", "find_file": "a.py", "search_literal": "No occurrences of 'zzz' in 3 files scanned.", "read_repo_file": "file text", "propose_local_edits": APPLIED}
    replies = [Reply([call("list_repo_tree", 1)]), Reply([call("search_literal", 2, term="zzz"), call("find_file", 3, query="a")]), Reply([call("read_repo_file", 4, path="a.py"), call("read_repo_file", 5, path="b.py")]),
               Reply([call("propose_local_edits", 6, summary="s", edits=[{"path": "a.py", "old_string": "x", "new_string": "y"}])]), Reply([], "done")]

    async def act(decision):
        return results[decision["tool_action"]]

    turn = llm_turns.begin("jack")
    asyncio.run(native_loop.run(llm=ScriptedLLM(replies), tools=[{"name": n} for n in ("list_repo_tree", "find_file", "search_literal", "read_repo_file", "propose_local_edits")], act=act, question="fix it", max_steps=12))
    assert turn.reading == {"files_read": 2, "edited_and_read": 1, "files_edited": 1, "rereads": 0, "searches": 1, "empty_searches": 1, "tree_listings": 1, "find_files": 1, "applied": 1}
    for name in ("list_repo_tree", "search_literal", "find_file", "read_repo_file", "read_repo_file", "propose_local_edits"):
        llm_turns.note_tool_call(name, True)                       # the tool agent's dispatcher counts each call by name; the loop itself does not
    counts = {}
    code_outcomes.tally_turn(counts, code_outcomes.summarize_turn(turn))
    assert counts["reading_files_read"] == 2 and counts["reading_edit_turn_files_used"] == 1 and counts["reading_empty_searches"] == 1 and counts["reading_edit_turns"] == 1


def test_the_old_loop_is_not_measured_and_nothing_here_logs():
    import ast
    import inspect

    for fn in (ReadingLog.note, ReadingLog.summary, reading_waste.counts_from):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"logger", "log"}], fn.__name__
    assert reading_waste.READ_TOOLS == ("read_repo_file",)
