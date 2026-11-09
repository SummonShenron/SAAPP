import pathlib
from datetime import datetime, timezone

import pytest

from backend.utils import budget_guard as bg
from backend.utils import llm_usage as lu

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
CALL = {"input": 1_000, "output": 100, "reasoning": 0, "cached": 0}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(lu.SOURCE_ENV, raising=False)
    monkeypatch.setattr(lu, "flush_in_background", lambda: None)
    lu.reset()
    yield
    lu.reset()


def test_a_call_in_an_eval_run_is_counted_under_an_eval_label_and_not_in_the_minute_rows(monkeypatch):
    monkeypatch.setenv(lu.SOURCE_ENV, "eval")
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "eval:native_loop.run")]
    assert lu.minute_snapshot() == {}


def test_an_ordinary_call_is_counted_as_it_always_was(monkeypatch):
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "native_loop.run")]
    assert len(lu.minute_snapshot()) == 1


def test_an_eval_label_is_not_prefixed_twice(monkeypatch):
    monkeypatch.setenv(lu.SOURCE_ENV, "eval")
    lu.record("m", "eval:x", CALL, day="2026-10-10")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "eval:x")]


def test_the_guard_leaves_eval_spend_out_of_the_day_and_the_month():
    rows = [
        {"day": "2026-10-10", "model": "m", "label": "native_loop.run", "calls": 1, "input": 100, "output": 0, "reasoning": 0, "cached": 0},
        {"day": "2026-10-10", "model": "m", "label": "eval:native_loop.run", "calls": 1, "input": 900_000, "output": 0, "reasoning": 0, "cached": 0},
    ]
    totals = bg.spent(rows, NOW, {})
    assert totals["day_tokens"] == 100 and totals["month_tokens"] == 100


def test_the_three_evals_mark_their_command_line_runs_and_only_those():
    for name in ("behavior_eval", "character_eval", "memory_eval"):
        source = (ROOT / "backend" / "evals" / f"{name}.py").read_text(encoding="utf-8")
        main_block = source[source.index('if __name__ == "__main__":'):]
        assert "llm_usage.mark_as_eval()" in main_block and main_block.index("mark_as_eval") < main_block.index("SystemExit(main())"), name
        assert "mark_as_eval()" not in source[: source.index('if __name__ == "__main__":')], name  # importing an eval (the tests do) changes nothing


# ---- a script that never said it was an eval is told so, once (2026-11-08: the probe scripts were about $4 of a $4.71 day) ----------------------------------------------------------------------

def test_only_a_process_with_no_server_no_test_run_and_no_eval_mark_is_an_unlabelled_script():
    assert lu.is_unlabelled_script("", {"os": 1}) is True
    assert lu.is_unlabelled_script("eval", {"os": 1}) is False                  # it said so
    assert lu.is_unlabelled_script("", {"uvicorn": 1}) is False                 # the server
    assert lu.is_unlabelled_script("", {"pytest": 1}) is False                  # a test run
    assert lu.is_unlabelled_script(" EVAL ", {}) is False


def test_the_warning_is_said_once_on_stderr_and_never_changes_the_label_or_the_counts(monkeypatch, capsys):
    monkeypatch.setattr(lu, "is_unlabelled_script", lambda source, modules: True)
    monkeypatch.setattr(lu, "_warned_unlabelled_script", False)
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    err = capsys.readouterr().err
    assert err.count("WARNING:") == 1 and err.startswith("WARNING")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "native_loop.run")] and lu.snapshot()[("2026-10-10", "m", "native_loop.run")]["calls"] == 2
    assert len(lu.minute_snapshot()) == 1                                         # still counted for the guard: a wrong guess must never hide real spend


def test_an_ordinary_test_run_and_a_marked_eval_say_nothing(monkeypatch, capsys):
    monkeypatch.setattr(lu, "_warned_unlabelled_script", False)
    lu.record("m", "x", CALL, day="2026-10-10")
    monkeypatch.setenv(lu.SOURCE_ENV, "eval")
    lu.record("m", "x", CALL, day="2026-10-10")
    assert capsys.readouterr().err == ""


def test_a_real_script_process_warns_unless_it_marked_itself(tmp_path):
    import subprocess
    import sys

    code = ("import os, sys\nfrom backend.utils import llm_usage\n"
            "if os.environ.get('MARK') == '1': llm_usage.mark_as_eval()\n"
            "llm_usage.record('m', 'probe.run', {'input': 1, 'output': 1, 'reasoning': 0, 'cached': 0}, day='2026-10-10')\n")
    env = {**__import__("os").environ, "GOOGLE_API_KEY": "test-not-real", "PYTHONPATH": str(ROOT)}
    env.pop(lu.SOURCE_ENV, None)
    loud = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**env, "MARK": "0"}, timeout=120)
    quiet = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**env, "MARK": "1"}, timeout=120)
    assert "mark_as_eval()" in loud.stderr and "mark_as_eval()" not in quiet.stderr
