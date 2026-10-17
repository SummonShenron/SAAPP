"""Per-person limits in dollars, a hard daily cap with a low-power step before it, and no free pass for admins
(backend/utils/budget_guard.py, user_usage.py, llm_turns.py)."""
import json
from datetime import datetime, timezone

import pytest

from backend.tests.test_budget_guard import _DB, at, minute_of
from backend.utils import budget_guard as bg
from backend.utils import llm_turns, llm_usage, user_usage

PRICES = json.dumps({"priced": {"input": 1.0, "output": 2.0, "cached_input": 0.5}})


def person_cost_row(user, moment, dollars, tokens=1000):
    return {"user": user, "minute": minute_of(moment), "tokens": tokens, "calls": 1, "turns": 1, "cost_micro": int(round(dollars * 1_000_000))}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("LLM_USER_BUDGET_DAILY_USD", "LLM_USER_BUDGET_5H_USD", "LLM_USER_BUDGET_DAILY_TOKENS", "LLM_USER_BUDGET_5H_TOKENS",
                 "LLM_BUDGET_DAILY_USD", "LLM_BUDGET_MONTHLY_USD", "LLM_BUDGET_MONTH_ALREADY_USD", "LLM_BUDGET_5H_USD", "LLM_BUDGET_5H_TOKENS",
                 "LLM_BUDGET_DAILY_TOKENS", "LLM_BUDGET_MONTHLY_TOKENS", "LLM_BUDGET_BLOCK_AT", "LLM_BUDGET_THROTTLE_AT", "LLM_BUDGET_NOTICE_AT", "LLM_USER_BUDGET_EXEMPT_ADMINS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PRICES", PRICES)
    llm_turns.reset()
    user_usage.reset()
    bg._reset_cache()
    yield
    llm_turns.reset()
    user_usage.reset()


# ---- counting a person's dollars as their turn runs -----------------------------------------------------------------------------------------

def test_a_turns_priced_calls_add_up_to_the_persons_dollars_when_it_finishes():
    turn = llm_turns.begin("jack")
    llm_usage.record("priced", "app.token_streamer", {"calls": 1, "input": 1_000_000, "output": 0, "reasoning": 0, "cached": 0})
    llm_usage.record("priced", "app.token_streamer", {"calls": 1, "input": 0, "output": 500_000, "reasoning": 0, "cached": 0})
    llm_turns.finish(turn)
    rows = user_usage.unsaved_rows("jack")
    assert sum(r["cost_micro"] for r in rows) == 2_000_000          # $1.00 of input plus $1.00 of output
    assert user_usage.cost_since(rows, "0000") == 2.0


def test_cached_input_is_priced_at_the_cached_rate_and_a_model_without_a_price_adds_nothing():
    turn = llm_turns.begin("jack")
    llm_usage.record("priced", "x", {"calls": 1, "input": 1_000_000, "output": 0, "reasoning": 0, "cached": 1_000_000})
    llm_usage.record("unlisted-model", "x", {"calls": 1, "input": 5_000_000, "output": 5_000_000, "reasoning": 0, "cached": 0})
    llm_turns.finish(turn)
    rows = user_usage.unsaved_rows("jack")
    assert sum(r["cost_micro"] for r in rows) == 500_000
    assert sum(r["tokens"] for r in rows) == 1_000_000 + 10_000_000    # tokens are still counted, so a token limit still sees it


def test_a_turn_for_nobody_and_a_blocked_turn_cost_no_one_anything():
    anonymous = llm_turns.begin()
    llm_usage.record("priced", "x", {"calls": 1, "input": 1_000_000, "output": 0, "reasoning": 0, "cached": 0})
    llm_turns.finish(anonymous)
    blocked = llm_turns.begin("jack")
    llm_usage.record("priced", "x", {"calls": 1, "input": 1_000_000, "output": 0, "reasoning": 0, "cached": 0})
    llm_turns.finish(blocked, "blocked")
    assert user_usage.snapshot() == {}


def test_each_person_has_their_own_dollars_and_the_rows_hold_only_counts():
    for who in ("jack", "dana"):
        turn = llm_turns.begin(who)
        llm_usage.record("priced", "x", {"calls": 1, "input": 1_000_000 if who == "jack" else 250_000, "output": 0, "reasoning": 0, "cached": 0})
        llm_turns.finish(turn)
    assert user_usage.cost_since(user_usage.unsaved_rows("jack"), "0000") == 1.0
    assert user_usage.cost_since(user_usage.unsaved_rows("dana"), "0000") == 0.25
    assert set(user_usage.FIELDS) == {"tokens", "calls", "turns", "cost_micro"}


def test_rows_saved_before_dollars_were_counted_have_none_and_the_save_adds_the_new_field():
    assert user_usage.cost_since([{"minute": "2026-10-17T10:00", "tokens": 5000}], "0000") == 0.0
    saved = []

    class Collection:
        def update_one(self, query, update, upsert=False):
            saved.append(update["$inc"])

        def create_index(self, *a, **k):
            pass

    class Db(dict):
        def __getitem__(self, name):
            return Collection()

    user_usage.add("jack", 100, 1, cost_micro=1234)
    assert user_usage.flush(Db()) == 1
    assert saved and saved[0]["cost_micro"] == 1234


# ---- the limits and what they say ------------------------------------------------------------------------------------------------------------

def test_the_per_person_limits_can_be_set_in_dollars(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_USD", "1")
    monkeypatch.setenv("LLM_USER_BUDGET_5H_USD", "0.50")
    assert bg.user_budgets() == {"my_day_usd": 1.0, "my_five_usd": 0.5}


def test_a_persons_dollars_are_counted_in_their_day_and_in_their_own_five_hour_window():
    rows = [person_cost_row("jack", at(8), 0.40), person_cost_row("jack", at(14), 0.10)]
    totals = bg.person_totals(rows, at(15), at(14))
    assert totals["my_day_usd"] == pytest.approx(0.50) and totals["my_five_usd"] == pytest.approx(0.10)


def test_a_dollar_a_day_per_person_judged_end_to_end(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_USD", "1")
    mine = lambda dollars: bg.current_state(_DB(people=[person_cost_row("jack", at(9), dollars)]), at(15), "jack")
    assert mine(0.5)["level"] == bg.OK
    assert mine(0.85)["level"] == bg.NOTICE
    state = mine(1.0)
    assert state["level"] == bg.THROTTLED and state["scope"] == "my_day" and state["unit"] == "usd"      # default: low power at the limit, blocked at 125%
    assert mine(1.3)["level"] == bg.BLOCKED


def test_someone_elses_spend_never_moves_my_meter(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_USD", "1")
    db = _DB(people=[person_cost_row("dana", at(9), 5.0), person_cost_row("jack", at(9), 0.2)])
    assert bg.current_state(db, at(15), "jack")["level"] == bg.OK
    assert bg.current_state(db, at(15), "dana")["level"] == bg.BLOCKED


def test_the_per_person_five_hour_dollar_limit_counts_only_that_window(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_USD", "0.50")
    db = _DB(people=[person_cost_row("jack", at(8), 3.0), person_cost_row("jack", at(14), 0.2)])
    state = bg.current_state(db, at(15), "jack")
    assert state["scope"] == "my_five" and state["spent"] == pytest.approx(0.2) and state["level"] == bg.OK


def test_a_person_who_has_hit_their_day_limit_is_blocked_while_the_shared_budget_still_has_room(monkeypatch):
    totals = {"day_usd": 2.0, "my_day_usd": 1.0}
    state = bg.evaluate(totals, {"day_usd": 5.0, "my_day_usd": 1.0}, at(15))
    assert state["level"] == bg.THROTTLED and state["scope"] == "my_day"
    monkeypatch.setenv("LLM_BUDGET_BLOCK_AT", "1")
    state = bg.evaluate(totals, {"day_usd": 5.0, "my_day_usd": 1.0}, at(15))
    assert state["level"] == bg.BLOCKED and state["blocked_scopes"] == ["my_day"]
    assert bg.extendable_scopes(state) == []                    # a person's own block is not something an admin's +25% lifts


# ---- a hard daily cap, with a low-power step before it -------------------------------------------------------------------------------------------

def test_a_budget_can_be_a_hard_cap_with_a_throttle_line_before_it(monkeypatch):
    assert bg.thresholds() == (0.8, 1.25) and bg.throttle_at() == 1.0                      # as it always was
    monkeypatch.setenv("LLM_BUDGET_BLOCK_AT", "1")
    monkeypatch.setenv("LLM_BUDGET_THROTTLE_AT", "0.8")
    monkeypatch.setenv("LLM_BUDGET_NOTICE_AT", "0.6")
    assert bg.thresholds() == (0.6, 1.0) and bg.throttle_at() == 0.8
    level = lambda spent: bg.evaluate({"day_usd": spent}, {"day_usd": 5.0}, at(15))["level"]
    assert [level(s) for s in (2.0, 3.2, 4.2, 4.99, 5.0, 6.0)] == [bg.OK, bg.NOTICE, bg.THROTTLED, bg.THROTTLED, bg.BLOCKED, bg.BLOCKED]


def test_a_throttle_line_above_the_block_line_or_a_notice_line_above_it_is_pulled_into_order(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_BLOCK_AT", "1")
    monkeypatch.setenv("LLM_BUDGET_THROTTLE_AT", "2")
    assert bg.throttle_at() == 1.0
    monkeypatch.setenv("LLM_BUDGET_THROTTLE_AT", "0.5")
    monkeypatch.setenv("LLM_BUDGET_NOTICE_AT", "0.9")
    state = bg.evaluate({"day_usd": 2.0}, {"day_usd": 5.0}, at(15))
    assert state["level"] == bg.OK                                # 40%: under both lines
    assert bg.evaluate({"day_usd": 2.45}, {"day_usd": 5.0}, at(15))["level"] == bg.NOTICE      # notice was pulled below the throttle line


# ---- admins are people too -------------------------------------------------------------------------------------------------------------------

def test_an_admin_meets_the_per_person_limits_so_their_testing_cannot_use_up_everyone_elses_share(monkeypatch):
    assert bg.limit_username("jack", is_admin=True) == "jack" and bg.limit_username("dana", is_admin=False) == "dana"
    monkeypatch.setenv("LLM_USER_BUDGET_EXEMPT_ADMINS", "true")
    assert bg.limit_username("jack", is_admin=True) is None and bg.limit_username("dana", is_admin=False) == "dana"


def test_both_places_that_judge_a_person_use_the_same_rule():
    source = open("app.py", encoding="utf-8").read()
    assert source.count("budget_guard.limit_username(") == 2 and "None if admin else username" not in source and "None if is_admin_user else username" not in source


# ---- turns recorded before dollars were counted ----------------------------------------------------------------------------------------------

def old_row(user, moment, tokens):
    """A row saved before dollars were counted: it has no cost_micro key at all."""
    return {"user": user, "minute": minute_of(moment), "tokens": tokens, "calls": 1, "turns": 1}


def test_rows_from_before_dollars_were_counted_are_estimated_from_the_days_average_price_so_the_meter_does_not_start_at_zero():
    rows = [old_row("jack", at(9), 1_000_000), person_cost_row("jack", at(10), 0.10), old_row("jack", at(11), 500_000)]
    assert user_usage.cost_since(rows, "0000") == pytest.approx(0.10)                                   # no rate: old rows count as nothing
    assert user_usage.cost_since(rows, "0000", 0.0000004) == pytest.approx(0.10 + 1_500_000 * 0.0000004)
    zero = {"minute": minute_of(at(9)), "tokens": 5_000_000, "cost_micro": 0}
    assert user_usage.cost_since([zero], "0000", 0.0000004) == 0.0           # a row saved with dollars on it is believed, even when they are 0


def test_a_person_with_only_old_rows_today_sees_an_estimate_on_their_meter_end_to_end(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_USD", "1")
    monkeypatch.setenv("LLM_BUDGET_DAILY_USD", "5")
    # the whole day: 2,000,000 tokens for $0.80, all of it Jack's turns plus 500k of background work that is nobody's
    day_rows = [{"day": "2026-10-10", "model": "priced", "label": "x", "calls": 10, "input": 1_500_000, "output": 500_000, "reasoning": 0, "cached": 0}]
    monkeypatch.setenv("LLM_PRICES", json.dumps({"priced": {"input": 0.4, "output": 0.4}}))
    db = _DB(rows=day_rows, people=[old_row("jack", at(9), 1_500_000)])
    state = bg.current_state(db, at(15), "jack")
    mine = next(item for item in state["detail"] if item["scope"] == "my_day")
    assert mine["spent"] == pytest.approx(1_500_000 * 0.0000004, rel=0.02) and mine["unit"] == "usd"
