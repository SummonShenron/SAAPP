from datetime import datetime, timezone

from backend.tests.test_sonic_health import _DB
from backend.utils import llm_turns as lt
from backend.utils import llm_usage as lu
from backend.utils import sonic_health as sh

PRICES = {"cheap": {"input": 1.0, "output": 2.0, "cached_input": 0.1}}
DAYS = ["2026-10-10", "2026-10-11", "2026-10-12"]


def row(day, model="cheap", calls=1, input=1_000_000, output=0, cached=0):
    return {"day": day, "model": model, "label": "x.y", "calls": calls, "input": input, "output": output, "reasoning": 0, "cached": cached}


def test_there_is_one_row_per_day_and_a_quiet_day_is_all_zeros():
    out = lu.summarize_daily([row("2026-10-11", calls=3)], PRICES, DAYS)
    assert [r["day"] for r in out] == DAYS
    assert out[0]["calls"] == 0 and out[0]["tokens"] == 0 and out[0]["cost"] == 0.0
    assert out[1]["calls"] == 3 and out[1]["tokens"] == 1_000_000 and out[1]["cost"] == 1.0


def test_cost_adds_up_across_models_and_callers_and_counts_cached_input_at_its_own_price():
    docs = [row("2026-10-12", input=1_000_000, output=500_000), row("2026-10-12", input=1_000_000, cached=1_000_000)]
    out = lu.summarize_daily(docs, PRICES, DAYS)[2]
    assert out["calls"] == 2 and out["input"] == 2_000_000 and out["output"] == 500_000
    assert out["cost"] == round(1.0 + 1.0 + 0.1, 4)  # first: $1 in + $1 out; second: all cached input


def test_a_model_with_no_price_is_counted_as_unpriced_tokens_never_guessed():
    out = lu.summarize_daily([row("2026-10-12", model="unknown-model", input=500, output=100)], PRICES, DAYS)[2]
    assert out["cost"] == 0.0 and out["unpriced_tokens"] == 600 and out["tokens"] == 600


def test_without_any_prices_the_cost_is_none_not_zero():
    assert all(r["cost"] is None for r in lu.summarize_daily([row("2026-10-12")], {}, DAYS))


def test_rows_outside_the_period_are_ignored():
    out = lu.summarize_daily([row("2026-09-01")], PRICES, DAYS)
    assert sum(r["calls"] for r in out) == 0


def test_turns_per_day_are_summed_from_the_saved_rows():
    out = lt.daily_turns([{"day": "2026-10-12", "turns": 5, "tool_turns": 2, "calls": 20}, {"day": "2026-10-12", "turns": 1, "calls": 3}, {"day": "", "turns": 9}])
    assert out == {"2026-10-12": {"turns": 6, "tool_turns": 2, "calls": 23}}


def test_the_days_of_a_period_are_billing_days_oldest_first_and_end_today_in_pacific_time():
    # 00:30 UTC on the 13th is still the 12th in Pacific time
    assert sh.billing_days(3, datetime(2026, 10, 13, 0, 30, tzinfo=timezone.utc)) == ["2026-10-10", "2026-10-11", "2026-10-12"]
    assert sh.billing_days(1, datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)) == ["2026-10-13"]
    assert len(sh.billing_days(30, datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc))) == 30


def test_the_health_payload_carries_the_daily_series_for_the_whole_period():
    now = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
    out = sh.build_sonic_health(_DB([], [], []), 7, now)
    daily = out["usage"]["daily"]
    assert [d["day"] for d in daily][-1] == "2026-10-12" and len(daily) == 7
    assert all({"day", "calls", "tokens", "cost", "turns", "tool_turns"} <= set(d) for d in daily)


def test_the_daily_series_holds_only_numbers_and_dates():
    out = sh.build_sonic_health(_DB([], [], []), 3, datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc))
    for d in out["usage"]["daily"]:
        assert all(isinstance(v, (int, float, type(None))) for k, v in d.items() if k != "day")
