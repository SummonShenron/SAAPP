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


def test_a_call_in_an_eval_run_is_counted_under_an_eval_label_and_not_in_the_hour_rows(monkeypatch):
    monkeypatch.setenv(lu.SOURCE_ENV, "eval")
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "eval:native_loop.run")]
    assert lu.hourly_snapshot() == {}


def test_an_ordinary_call_is_counted_as_it_always_was(monkeypatch):
    lu.record("m", "native_loop.run", CALL, day="2026-10-10")
    assert list(lu.snapshot()) == [("2026-10-10", "m", "native_loop.run")]
    assert len(lu.hourly_snapshot()) == 1


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
