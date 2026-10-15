import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.services.agent_workflow import mongo_query_globals

SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
BUILTINS = {"range": range, "len": len, "list": list, "min": min, "max": max, "sum": sum}  # a stand-in for the tool's real, equally small set


def run(code, **scope):
    local = {"result": None, **scope}
    exec(code, mongo_query_globals(BUILTINS), local)
    return local["result"]


def test_a_snippet_can_work_with_dates_without_importing_anything():
    code = 'result = datetime.fromisoformat("2026-10-14T05:00:00+00:00") - timedelta(days=2)'
    assert run(code) == datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
    assert run("result = datetime.now(timezone.utc).tzinfo") == timezone.utc


def test_the_date_types_are_visible_inside_comprehensions_and_lambdas_too():
    # A name that lived only in the local scope would raise NameError here: comprehension bodies look in the globals.
    assert run("result = [datetime.fromisoformat(d).day for d in ['2026-10-12', '2026-10-14']]") == [12, 14]
    assert run("f = lambda n: timedelta(days=n).days\nresult = f(3)") == 3


def test_a_snippet_can_still_filter_on_the_iso_strings_the_conversations_store():
    docs = [{"updated_at": "2026-10-12T09:00:00+00:00"}, {"updated_at": "2026-10-14T05:00:00+00:00"}]
    code = (
        "cutoff = (datetime.fromisoformat('2026-10-14T12:00:00+00:00') - timedelta(days=1)).isoformat()\n"
        "result = [d for d in docs if d['updated_at'] >= cutoff]"
    )
    assert run(code, docs=docs) == [docs[1]]


@pytest.mark.parametrize("code", ["import os", "import datetime", "from datetime import datetime", "__import__('os')"])
def test_imports_stay_blocked(code):
    with pytest.raises((ImportError, NameError)):  # an import statement fails inside `import`; calling __import__ by name finds no such built-in
        run(code)


@pytest.mark.parametrize("code", ["open('x')", "eval('1')", "exec('1')", "compile('1', 'x', 'eval')", "print(1)", "getattr(1, 'real')"])
def test_nothing_outside_the_allowed_built_ins_appears(code):
    with pytest.raises(NameError):
        run(code)


def test_the_globals_are_fresh_each_time_so_one_snippet_cannot_leave_something_for_the_next():
    first = mongo_query_globals(BUILTINS)
    first["leftover"] = 1
    assert "leftover" not in mongo_query_globals(BUILTINS)
    assert mongo_query_globals(BUILTINS)["__builtins__"] is BUILTINS


def test_the_read_only_query_uses_the_helper_and_the_write_path_is_not_widened():
    assert "exec(code, mongo_query_globals(exec_builtins), local_scope)" in SOURCE
    assert SOURCE.count('exec(code, {"__builtins__": exec_builtins}, local_scope)') == 1  # the approved-write path keeps its original, smaller globals


def test_the_tool_text_tells_the_model_what_is_available_and_how_timestamps_are_stored():
    start = SOURCE.index('"- run_mongo_query — args: code')
    text = SOURCE[start: start + 1200]
    for needle in ("imports are not allowed", "`datetime`", "`timedelta`", "`timezone`", "ISO-8601 strings", "'$gte': '2026-10-14'"):
        assert needle in text, needle
