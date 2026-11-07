"""The sandbox a `run_mongo_query` snippet (admin-only) and an approved Mongo write run in (backend/services/agent_workflow.py: mongo_query_globals, run_mongo_snippet, run_mongo_write_snippet).

THE NAMESPACE RULE (2026-11-07). A snippet runs in ONE namespace. With separate globals and locals, anything the snippet defines itself (`cutoff = ...`, a helper `def`, a variable a lambda uses) is a local that a generator
expression, a lambda or a nested function cannot see, so `sum(1 for d in db if d["n"] > cutoff)` raised NameError. These tests run the real helpers, not a copy of how they work."""
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.agent_workflow import mongo_query_globals, run_mongo_snippet, run_mongo_write_snippet

SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
BUILTINS = {"range": range, "len": len, "list": list, "min": min, "max": max, "sum": sum}  # a stand-in for the tool's real, equally small set
ROWS = [{"u": "jack", "n": 5}, {"u": "ann", "n": 1}, {"u": "jack", "n": 3}]


def run(code, db=None, username="jack", builtins=None):
    return run_mongo_snippet(code, db if db is not None else ROWS, username, dict(BUILTINS) if builtins is None else builtins)


def write(code, db=None, username="jack", builtins=None):
    return run_mongo_write_snippet(code, db if db is not None else ROWS, username, dict(BUILTINS) if builtins is None else builtins)


# ---- dates and the allowed names -------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_snippet_can_work_with_dates_without_importing_anything():
    code = 'result = datetime.fromisoformat("2026-10-14T05:00:00+00:00") - timedelta(days=2)'
    assert run(code) == datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
    assert run("result = datetime.now(timezone.utc).tzinfo") == timezone.utc


def test_the_date_types_are_visible_inside_comprehensions_and_lambdas_too():
    assert run("result = [datetime.fromisoformat(d).day for d in ['2026-10-12', '2026-10-14']]") == [12, 14]
    assert run("f = lambda n: timedelta(days=n).days\nresult = f(3)") == 3


def test_a_snippet_can_still_filter_on_the_iso_strings_the_conversations_store():
    docs = [{"updated_at": "2026-10-12T09:00:00+00:00"}, {"updated_at": "2026-10-14T05:00:00+00:00"}]
    code = (
        "cutoff = (datetime.fromisoformat('2026-10-14T12:00:00+00:00') - timedelta(days=1)).isoformat()\n"
        "result = [d for d in db if d['updated_at'] >= cutoff]"
    )
    assert run(code, db=docs) == [docs[1]]


# ---- the NameError the namespace rule fixes ---------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code, expected", [
    ('result = [d["n"] for d in db if d["u"] == username]', [5, 3]),                                                          # the original report: username inside a comprehension
    ('cutoff = 2\nresult = [d["n"] for d in db if d["n"] > cutoff]', [5, 3]),                                                 # a name the snippet defined, in a comprehension
    ('cutoff = 2\nresult = sum(1 for d in db if d["n"] > cutoff)', 2),                                                        # in a generator expression: this raised NameError before
    ('limit = 2\nbig = lambda d: d["n"] > limit\nresult = [d["n"] for d in db if big(d)]', [5, 3]),                           # in a lambda
    ('base = 1\ndef plus(d):\n    return d["n"] + base\nresult = [plus(d) for d in db]', [6, 2, 4]),                           # a nested function using another top-level name
    ('def mine(d):\n    return d["u"] == username\nresult = sum(1 for d in db if mine(d))', 2),                                 # db and username inside a nested function
    ('total = 0\nfor d in db:\n    total += d["n"]\nresult = total', 9),                                                        # plain statements, as before
    ('result = max(d["n"] for d in db if d["u"] == username) - min(d["n"] for d in db)', 4),                                  # two generator expressions
])
def test_a_name_visible_anywhere_in_the_snippet_is_visible_everywhere_in_it(code, expected):
    assert run(code) == expected


def test_db_and_username_are_what_the_tool_was_given():
    sentinel = object()
    assert run("result = db", db=sentinel) is sentinel
    assert run("result = username", username="ann") == "ann"


def test_nothing_assigned_to_result_gives_none_as_before_and_a_snippets_error_is_not_swallowed():
    assert run("x = 1") is None
    with pytest.raises(ZeroDivisionError):
        run("result = 1 / 0")                                                                                                    # the caller turns this into the tool's error text, as it always did


# ---- what stays blocked ---------------------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["import os", "import datetime", "from datetime import datetime", "__import__('os')"])
def test_imports_stay_blocked(code):
    with pytest.raises((ImportError, NameError)):  # an import statement fails inside `import`; calling __import__ by name finds no such built-in
        run(code)
    with pytest.raises((ImportError, NameError)):
        write(code)


@pytest.mark.parametrize("code", ["open('x')", "eval('1')", "exec('1')", "compile('1', 'x', 'eval')", "print(1)", "getattr(1, 'real')", "filter(None, [])"])
def test_nothing_outside_the_allowed_built_ins_appears(code):
    with pytest.raises(NameError):
        run(code)
    with pytest.raises(NameError):
        write(code)


def test_the_namespace_is_fresh_each_time_so_one_snippet_cannot_leave_something_for_the_next():
    run("leftover = 1")
    with pytest.raises(NameError):
        run("result = leftover")
    first = mongo_query_globals(BUILTINS)
    first["leftover"] = 1
    assert "leftover" not in mongo_query_globals(BUILTINS)
    assert mongo_query_globals(BUILTINS)["__builtins__"] is BUILTINS


def test_a_snippet_cannot_change_the_allowed_built_ins_for_the_next_one():
    shared = dict(BUILTINS)
    run_mongo_snippet('__builtins__["len"] = None', ROWS, "jack", shared)
    assert shared["len"] is len                                                                                                 # the built-ins are copied per call
    assert run_mongo_snippet("result = len(db)", ROWS, "jack", shared) == 3
    run_mongo_write_snippet('__builtins__["sum"] = None', ROWS, "jack", shared)
    assert shared["sum"] is sum


# ---- the approved-write path: the same rule, and no wider -----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code, expected", [
    ('result = [d["n"] for d in db if d["u"] == username]', [5, 3]),
    ('cutoff = 2\nresult = sum(1 for d in db if d["n"] > cutoff)', 2),
    ('limit = 2\nbig = lambda d: d["n"] > limit\nresult = len([d for d in db if big(d)])', 2),
    ('def mine(d):\n    return d["u"] == username\nresult = sum(1 for d in db if mine(d))', 2),
])
def test_an_approved_write_sees_every_name_everywhere_in_its_snippet_too(code, expected):
    assert write(code) == expected


def test_the_write_path_is_not_widened_it_still_has_no_date_types_and_returns_none_when_nothing_is_assigned():
    for name in ("datetime", "timedelta", "timezone"):
        with pytest.raises(NameError):
            write(f"result = {name}")
        assert run(f"result = {name}") is not None                                                                              # the read path has them, as before
    assert write("x = 1") is None


# ---- the tool is wired to the helpers, and its text ------------------------------------------------------------------------------------------------------------------------------

def test_both_tools_run_through_the_helpers_and_nothing_splits_the_namespace_again():
    assert "return run_mongo_snippet(code, db, username, exec_builtins)" in SOURCE
    assert "execution_result = run_mongo_write_snippet(code, db, username, exec_builtins)" in SOURCE
    assert SOURCE.count("exec(code, namespace)") == 2                                                                           # one inside each helper
    assert "local_scope" not in SOURCE                                                                                           # the split globals/locals that caused the NameError is gone
    assert not re.search(r"exec\(code,[^)\n]*,[^)\n]*\)", SOURCE)                                                               # no exec(code, globals, locals) anywhere in the agent


def test_the_tool_text_tells_the_model_what_is_available_and_how_timestamps_are_stored():
    start = SOURCE.index('"- run_mongo_query — args: code')
    text = SOURCE[start: start + 1200]
    for needle in ("imports are not allowed", "`datetime`", "`timedelta`", "`timezone`", "ISO-8601 strings", "'$gte': '2026-10-14'"):
        assert needle in text, needle
