"""Did the tool loop stop reading because it got lost, or because it ran out of steps? (backend/utils/loop_progress.py, shown in the trace by native_loop.run)

The text below is made up. What matters: "new ground" is judged mechanically from the results (a file or window not read before, a search that named a new path, an applied edit), the last tool step is marked with what the work
looked like by then, a turn that used tools says how it ended, and nothing in it is a path, a term or a line of what was read."""
import asyncio
import pathlib

import pytest

from backend.services import native_loop as nl
from backend.tests.test_native_loop import TOOLS, ToolLLM, answers, asks, call
from backend.utils import loop_progress as lp

ROOT = pathlib.Path(__file__).resolve().parents[2]


def read(path, **args):
    return ("read_repo_file", {"path": path, **args})


# ---- what counts as new ground -----------------------------------------------------------------------------------------------------------

def test_a_file_or_window_not_read_before_is_new_ground_and_the_same_window_again_is_not():
    log = lp.ProgressLog()
    assert log.note(1, *read("a.py"), "def a(): ...") is True
    assert log.note(2, *read("a.py"), "def a(): ...") is False                                    # the same window again
    assert log.note(3, *read("a.py", start_line=200, line_count=40), "more of it") is True        # reading further is progress
    assert log.note(4, *read("b.py"), "def b(): ...") is True


def test_an_error_a_repeat_note_and_an_empty_result_are_never_ground():
    log = lp.ProgressLog()
    assert log.note(1, *read("a.py"), "ERROR: could not read a.py") is False
    assert log.note(2, *read("b.py"), "NOTE: you already made this exact call in step 1") is False
    assert log.note(3, "search_literal", {"term": "x"}, "No occurrences of 'x' found.") is False
    assert log.note(4, "search_literal", {"term": "y"}, "no matches for y") is False
    assert log.note(5, "search_literal", {"term": "z"}, "") is False and log.note(6, "web_search", {}, None) is False
    assert log.note(7, "search_literal", {"term": "w"}, "No matches found in src/brand_new_file.py or docs/guide.md") is False       # an empty result that happens to name paths is still empty


def test_a_search_is_new_ground_only_if_it_names_a_path_not_seen_in_this_turn():
    log = lp.ProgressLog()
    assert log.note(1, "search_literal", {"term": "greeting"}, "src/app.py:12: greeting = 'hi'\nsrc/util.py:3: def greeting()") is True
    assert log.note(2, "search_literal", {"term": "greeting("}, "src/app.py:12: greeting = 'hi'") is False                 # the same place again
    assert log.note(3, "find_file", {"name": "app"}, "src/app.py\nsrc/app_test.py") is True                                 # one new path
    assert log.note(4, *read("src/util.py"), "def greeting(): ...") is True                                               # a read of a file a search only named is still a first read
    assert log.note(5, "list_repo_tree", {}, "src/app.py\nsrc/app_test.py\nsrc/util.py") is False


def test_an_applied_edit_is_ground_a_declined_one_is_not_and_another_tool_counts_once_per_result():
    log = lp.ProgressLog()
    assert log.note(1, "propose_local_edits", {}, "Proposed 1 change: APPLIED: src/app.py") is True
    assert log.note(2, "propose_local_edits", {}, "DECLINED: too big") is False
    assert log.note(3, "web_search", {}, "A page about tides.") is True and log.note(4, "web_search", {}, "A  page about   TIDES.") is False


def test_a_step_gains_ground_if_any_of_its_calls_did():
    log = lp.ProgressLog()
    log.note(1, *read("a.py"), "x")
    log.note(2, *read("a.py"), "x")
    log.note(2, *read("c.py"), "y")                                  # two calls in one step: the step gained
    log.note(3, *read("a.py"), "x")
    assert log._gained == {1: True, 2: True, 3: False} and log.stalled_for() == 1


# ---- status, asked before a step runs ----------------------------------------------------------------------------------------------------

def test_the_status_is_unknown_early_progressing_while_anything_new_arrives_and_stalled_when_three_steps_in_a_row_found_nothing():
    log = lp.ProgressLog()
    assert log.status() == "unknown"
    log.note(1, *read("a.py"), "x")
    assert log.status() == "unknown"                                  # one step is too early to say
    log.note(2, *read("a.py"), "x")
    assert log.status() == "progressing"                              # step 1 gained and is inside the window
    log.note(3, *read("a.py"), "x")
    assert log.status() == "progressing"
    log.note(4, *read("a.py"), "x")
    assert log.status() == "stalled" and log.stalled_for() == 3        # the window is the last three steps: the early gain no longer counts
    log.note(5, *read("z.py"), "z")
    assert log.status() == "progressing" and log.stalled_for() == 0


# ---- how a turn ended --------------------------------------------------------------------------------------------------------------------

def test_the_verdict_tells_answered_from_ran_out_progressing_from_ran_out_lost_from_looped_and_says_nothing_without_a_tool_call():
    idle = lp.ProgressLog()
    assert lp.verdict(idle, "model_answered", False) == "" and lp.verdict(idle, "step_limit", True) == ""
    moving = lp.ProgressLog()
    for step in (1, 2, 3):
        moving.note(step, *read(f"f{step}.py"), "x")
    assert lp.verdict(moving, "model_answered", False) == "answered" and lp.verdict(moving, "", False) == "answered" and lp.verdict(moving, "checkpoint_answered", False) == "answered"
    assert lp.verdict(moving, "step_limit", True) == "ran_out_progress" and lp.verdict(moving, "hard_cap", True) == "ran_out_progress"
    assert lp.verdict(moving, "repeats", True) == "looped"
    circling = lp.ProgressLog()
    circling.note(1, *read("a.py"), "x")
    for step in (2, 3, 4):
        circling.note(step, "search_literal", {"term": str(step)}, "no matches")
    assert lp.verdict(circling, "step_limit", True) == "ran_out_lost"
    assert lp.verdict(circling, "model_answered", False) == "answered"                      # it chose to stop: not a limit, whatever the last steps found
    assert lp.verdict(moving, "deviation_allowance", False) == ""                           # the plan's own allowance on a turn that carries a plan out: not about reading


def test_the_trace_words_name_the_ending_and_carry_only_counts():
    assert lp.explain("answered", steps_left=3)["detail"] == "It answered on its own with 3 steps left; the step limit did not stop it."
    assert "with" not in lp.explain("answered", steps_left=0)["detail"]
    assert lp.explain("answered", steps_left=1)["detail"].count("1 step left") == 1
    assert "all 6 steps" in lp.explain("ran_out_progress", budget=6)["detail"] and "more steps would probably have helped" in lp.explain("ran_out_progress", budget=6)["detail"]
    lost = lp.explain("ran_out_lost", stalled=3)
    assert "last 3 steps" in lost["detail"] and "would probably not have helped" in lost["detail"] and "circles" in lost["title"]
    assert "repeating" in lp.explain("looped")["title"] and lp.explain("nonsense") == {"title": "", "detail": ""}
    assert lp.last_step_label("progressing") == "still finding new material" and lp.last_step_label("stalled") and lp.last_step_label("unknown") == ""


def test_the_counters_say_how_it_ended_and_what_the_final_step_header_said():
    assert lp.counts("ran_out_lost", "stalled") == {"turn_end_ran_out_lost": 1, "last_step_stalled": 1}
    assert lp.counts("answered", None) == {"turn_end_answered": 1} and lp.counts("", "progressing") == {"last_step_progressing": 1}
    assert lp.counts("", "unknown") == {} and lp.counts("nonsense", None) == {}
    from backend.utils import self_counters

    assert set(self_counters.PROGRESS_KEYS) == {f"turn_end_{k}" for k in lp.VERDICTS} | {"last_step_progressing", "last_step_stalled"} and set(self_counters.PROGRESS_KEYS) <= set(self_counters.KEYS)


# ---- through the loop --------------------------------------------------------------------------------------------------------------------

class Heard:
    """What the trace would be told: every step announced (with the marker, if any) and every ending."""

    def __init__(self):
        self.steps, self.counts = [], []

    async def emit_step(self, title, detail, step_id, tool=None, subject=None, last_step=None, turn_end=None):
        self.steps.append({"title": title, "detail": detail, "tool": tool, "last_step": last_step, "turn_end": turn_end})


def table_act(table, default="No matches found."):
    async def act(decision):
        args = decision["args"]
        return table.get((decision["tool_action"], args.get("path") or args.get("query") or args.get("term") or ""), default)

    return act


def drive(monkeypatch, responses, table, *, max_steps, heard=None, **kwargs):
    heard = heard or Heard()
    sent = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: sent.append(dict(counts)))
    result = asyncio.run(nl.run(llm=ToolLLM(*responses), tools=TOOLS, act=table_act(table), question="where is X?", max_steps=max_steps, emit_step=heard.emit_step, **kwargs))
    heard.counts = {k: v for batch in sent for k, v in batch.items() if k.startswith(("turn_end_", "last_step_"))}
    return result, heard


def search(term):
    return asks(call("search_code", {"query": term}, id=f"s_{term}"))


def read_file(path):
    return asks(call("read_repo_file", {"path": path}, id=f"r_{path}"))


def test_a_turn_that_ran_out_while_every_step_found_a_new_file_is_marked_and_ended_as_ran_out_progressing(monkeypatch):
    files = {("read_repo_file", f"src/f{n}.py"): f"content of file {n}" for n in range(1, 5)}
    result, heard = drive(monkeypatch, [read_file("src/f1.py"), read_file("src/f2.py"), read_file("src/f3.py"), read_file("src/f4.py"), answers("final words")], files, max_steps=4)
    marked = [s for s in heard.steps if s["last_step"]]
    assert [s["last_step"] for s in marked] == ["progressing"] and marked[0]["title"] == "Running read_repo_file..."            # only the last tool step, and it says the work was moving
    ending = heard.steps[-1]
    assert ending["turn_end"] == "ran_out_progress" and ending["title"] == "Stopped: ran out of steps" and "still finding new material" in ending["detail"]
    assert heard.counts == {"turn_end_ran_out_progress": 1, "last_step_progressing": 1} and result["final_answer"] == "final words"


def test_a_turn_that_ran_out_going_in_circles_is_marked_stalled_and_ended_as_ran_out_lost(monkeypatch):
    result, heard = drive(monkeypatch, [search("alpha"), search("beta"), search("gamma"), search("delta"), answers("I could not find it")], {}, max_steps=4)
    assert [s["last_step"] for s in heard.steps if s["last_step"]] == ["stalled"]
    ending = heard.steps[-1]
    assert ending["turn_end"] == "ran_out_lost" and "going in circles" in ending["title"] and "last 4 steps" in ending["detail"]
    assert heard.counts == {"turn_end_ran_out_lost": 1, "last_step_stalled": 1}


def test_a_turn_that_answers_on_its_own_says_so_and_has_no_final_step_header(monkeypatch):
    result, heard = drive(monkeypatch, [read_file("src/f1.py"), read_file("src/f2.py"), answers("found it")], {("read_repo_file", "src/f1.py"): "a", ("read_repo_file", "src/f2.py"): "b"}, max_steps=6)
    assert not [s for s in heard.steps if s["last_step"]]
    ending = heard.steps[-1]
    assert ending["turn_end"] == "answered" and "chose to answer" in ending["title"] and "with 4 steps left" in ending["detail"] and result["final_answer"] == "found it"
    assert heard.counts == {"turn_end_answered": 1}


def test_a_turn_that_makes_the_same_call_over_and_over_ends_as_looped(monkeypatch):
    result, heard = drive(monkeypatch, [search("same")] * 6 + [answers("stuck")], {}, max_steps=10)
    assert heard.steps[-1]["turn_end"] == "looped" and heard.counts == {"turn_end_looped": 1}


def test_a_turn_with_no_tool_call_says_nothing_about_how_it_ended(monkeypatch):
    result, heard = drive(monkeypatch, [answers("just words")], {}, max_steps=4)
    assert heard.steps == [] and heard.counts == {}


def test_an_announcer_that_does_not_know_the_markers_still_gets_every_step(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: None)
    seen = []

    async def old_announcer(title, detail, step_id, tool=None, subject=None):
        seen.append((title, tool))

    asyncio.run(nl.run(llm=ToolLLM(search("a"), search("b"), search("c"), answers("done")), tools=TOOLS, act=table_act({}), question="q", max_steps=3, emit_step=old_announcer))
    assert seen[:3] == [("Running search_code...", "search_code")] * 3 and len(seen) == 4 and seen[3][0].startswith("Stopped") and seen[3][1] is None      # every step, the last one without its marker, then the ending without its marker


def test_the_ending_is_not_sent_to_the_plain_announcer_only_to_the_step_announcer(monkeypatch):
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: None)
    plain = []

    async def emit(title, detail):
        plain.append(title)

    asyncio.run(nl.run(llm=ToolLLM(search("a"), answers("done")), tools=TOOLS, act=table_act({}), question="q", max_steps=3, emit=emit))
    assert plain == ["Running search_code..."]


# ---- the wiring, and what is stored -----------------------------------------------------------------------------------------------------

def test_the_markers_travel_from_the_loop_through_the_server_to_the_page_as_fixed_words_only():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert 'async def _announce_step(title, detail, step_id, tool=None, subject=None, last_step=None, turn_end=None):' in workflow
    assert 'payload["last_step"] = last_step' in workflow and 'payload["turn_end"] = turn_end' in workflow
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'if data.get("last_step") in ("progressing", "stalled", "unknown"):' in app_source and 'node_progress_payload["last_step"] = data["last_step"]' in app_source
    assert 'if data.get("turn_end") in ("answered", "ran_out_progress", "ran_out_lost", "looped"):' in app_source and 'node_progress_payload["turn_end"] = data["turn_end"]' in app_source
    assert tuple(lp.VERDICTS) == ("answered", "ran_out_progress", "ran_out_lost", "looped")


def test_the_progress_module_logs_and_stores_nothing():
    import ast

    source = (ROOT / "backend" / "utils" / "loop_progress.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "error", "exception", "debug")]
    assert "logging" not in source.split('"""', 2)[2] and "get_db" not in source and "open(" not in source
