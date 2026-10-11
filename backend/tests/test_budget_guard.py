import asyncio
import json
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.utils import budget_guard as bg
from backend.utils import initiation as ini
from backend.utils import llm_usage as lu
from backend.utils.safety_utils import CRISIS_RESOURCE_LINE

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
ENV = ("LLM_BUDGET_DAILY_USD", "LLM_BUDGET_MONTHLY_USD", "LLM_BUDGET_DAILY_TOKENS", "LLM_BUDGET_MONTHLY_TOKENS",
       "LLM_BUDGET_NOTICE_AT", "LLM_BUDGET_BLOCK_AT", "LLM_PRICES")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    bg._reset_cache()
    lu.reset()
    monkeypatch.setattr(lu, "flush_in_background", lambda: None)
    yield
    bg._reset_cache()
    lu.reset()


def row(model="m", day="2026-10-10", input_tokens=0, output_tokens=0, cached=0):
    return {"day": day, "model": model, "label": "x", "calls": 1, "input": input_tokens, "output": output_tokens, "reasoning": 0, "cached": cached}


# ---- configuration ------------------------------------------------------------------------------------------------------------

def test_it_is_off_until_a_budget_is_set(monkeypatch):
    assert bg.budgets() == {} and bg.current_state()["level"] == bg.OFF
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", "40")
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "2000000")
    assert bg.budgets() == {"month_usd": 40.0, "day_tokens": 2_000_000.0}


@pytest.mark.parametrize("value", ["", "abc", "0", "-5", "  "])
def test_a_blank_nonsense_or_non_positive_budget_is_ignored(monkeypatch, value):
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", value)
    assert bg.budgets() == {}


def test_the_thresholds_default_to_80_percent_and_125_percent_and_stay_in_order(monkeypatch):
    assert bg.thresholds() == (0.8, 1.25)
    monkeypatch.setenv("LLM_BUDGET_NOTICE_AT", "1.5")   # would sit above the throttle point
    monkeypatch.setenv("LLM_BUDGET_BLOCK_AT", "0.5")    # would sit below it
    notice, block = bg.thresholds()
    assert notice < 1.0 < block


# ---- judging --------------------------------------------------------------------------------------------------------------------

def level_at(fraction, key="month_usd", limit=100.0):
    return bg.evaluate({key: fraction * limit}, {key: limit}, NOW)["level"]


@pytest.mark.parametrize("fraction,expected", [
    (0.0, bg.OK), (0.5, bg.OK), (0.79, bg.OK), (0.8, bg.NOTICE), (0.99, bg.NOTICE),
    (1.0, bg.THROTTLED), (1.24, bg.THROTTLED), (1.25, bg.BLOCKED), (3.0, bg.BLOCKED),
])
def test_the_four_steps(fraction, expected):
    assert level_at(fraction) == expected


def test_with_several_budgets_the_one_furthest_along_decides():
    state = bg.evaluate({"day_usd": 3.5, "month_usd": 10.0}, {"day_usd": 3.0, "month_usd": 100.0}, NOW)
    assert state["level"] == bg.THROTTLED and state["scope"] == "day" and state["unit"] == "usd"
    assert [d["scope"] for d in state["detail"]] == ["day", "month"]


def test_a_daily_budget_resets_at_the_next_utc_midnight_and_a_monthly_one_on_the_first():
    day = bg.evaluate({"day_tokens": 120}, {"day_tokens": 100.0}, NOW)
    assert day["resets_at"] == "2026-10-11T00:00:00+00:00"
    month = bg.evaluate({"month_tokens": 120}, {"month_tokens": 100.0}, NOW)
    assert month["resets_at"] == "2026-11-01T00:00:00+00:00"
    december = bg.evaluate({"month_tokens": 120}, {"month_tokens": 100.0}, datetime(2026, 12, 20, 9, tzinfo=timezone.utc))
    assert december["resets_at"] == "2027-01-01T00:00:00+00:00"


def test_low_power_lists_what_is_paused_and_the_other_steps_pause_nothing():
    assert bg.evaluate({"month_usd": 120}, {"month_usd": 100.0}, NOW)["paused"] == bg.PAUSED_FEATURES
    assert bg.evaluate({"month_usd": 90}, {"month_usd": 100.0}, NOW)["paused"] == []


# ---- what has been spent --------------------------------------------------------------------------------------------------------

PRICES = lu.load_prices(json.dumps({"big": {"input": 1.0, "output": 10.0, "cached_input": 0.1}}))


def test_tokens_and_dollars_are_added_up_for_today_and_for_the_month():
    rows = [row("big", "2026-10-10", 1_000_000, 100_000, cached=400_000), row("big", "2026-10-03", 2_000_000, 0), row("other", "2026-10-10", 500, 500)]
    totals = bg.spent(rows, NOW, PRICES)
    assert totals["day_tokens"] == 1_100_000 + 1_000 and totals["month_tokens"] == 1_100_000 + 2_000_000 + 1_000
    today_cost = (600_000 * 1.0 + 400_000 * 0.1 + 100_000 * 10.0) / 1_000_000
    assert totals["day_usd"] == pytest.approx(today_cost)
    assert totals["month_usd"] == pytest.approx(today_cost + 2.0)
    assert totals["unpriced_tokens"] == 1_000  # "other" has no price: counted in tokens, left out of dollars, and said so


class _Col:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.queries = rows, fail, []

    def find(self, flt, projection=None):
        self.queries.append(flt)
        if self.fail:
            raise OSError("db down")
        floor = flt["day"]["$gte"]
        return [dict(r) for r in self.rows if r["day"] >= floor]


class _DB:
    def __init__(self, rows=(), fail=False):
        self.col = _Col(list(rows), fail)

    def __getitem__(self, name):
        assert name == lu.COLLECTION
        return self.col


def test_the_state_comes_from_the_saved_rows_and_whatever_is_still_in_memory(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 500, 100), row("m", "2026-09-30", 999_999, 0)])  # last month's row is not read
    assert bg.current_state(db, NOW)["level"] == bg.OK                       # 600 of 1000
    lu.record("m", "x", {"input": 300, "output": 0, "reasoning": 0, "cached": 0}, day="2026-10-10")
    bg._reset_cache()
    state = bg.current_state(db, NOW)
    assert state["spent"] == 900 and state["level"] == bg.NOTICE             # the unsaved 300 counts at once... 900 of 1000


def test_the_saved_rows_are_read_once_a_minute_not_on_every_turn(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 10, 10)])
    for _ in range(5):
        bg.current_state(db, NOW)
    assert len(db.col.queries) == 1
    assert db.col.queries[0] == {"day": {"$gte": "2026-10-01"}}


def test_with_no_budget_the_database_is_never_touched():
    db = _DB([row()])
    assert bg.current_state(db, NOW)["level"] == bg.OFF and db.col.queries == []


def test_if_the_usage_cannot_be_read_the_guard_steps_aside_and_never_raises(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    state = bg.current_state(_DB([row()], fail=True), NOW)
    assert state["level"] == bg.OFF and state["error"] is True
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    assert bg.current_state(None, NOW)["level"] == bg.OFF


# ---- what it changes --------------------------------------------------------------------------------------------------------------

def test_only_low_power_levels_pause_the_extras_and_cut_the_tool_steps():
    for level, paused in ((bg.OFF, False), (bg.OK, False), (bg.NOTICE, False), (bg.THROTTLED, True), (bg.BLOCKED, True)):
        state = {"level": level}
        assert bg.low_power(state) is paused and bg.allows("open_loops", state) is (not paused)
        assert bg.tool_steps(7, state) == (4 if paused else 7)
    assert bg.tool_steps(3, {"level": bg.THROTTLED}) == 3          # never raises a cap that was already lower
    assert bg.low_power(None) is False and bg.blocked({"level": bg.BLOCKED}) and not bg.blocked({"level": bg.THROTTLED})


def test_the_blocked_reply_says_when_it_ends_and_that_nothing_was_lost():
    reply = bg.blocked_reply("2026-11-01T00:00:00+00:00")
    assert "2026-11-01" in reply and "nothing you sent was lost" in reply and CRISIS_RESOURCE_LINE not in reply
    assert "for now" in bg.blocked_reply(None)


def test_in_a_crisis_the_blocked_reply_is_the_human_line_and_not_a_notice_about_limits():
    reply = bg.blocked_reply("2026-11-01T00:00:00+00:00", crisis=True)
    assert CRISIS_RESOURCE_LINE in reply and "usage limit" not in reply and "2026-11-01" not in reply


def test_an_admin_sees_amounts_and_everyone_else_sees_only_what_changed():
    state = bg.evaluate({"month_usd": 85}, {"month_usd": 100.0}, NOW)
    admin, other = bg.public_view(state, True), bg.public_view(state, False)
    assert admin["level"] == bg.NOTICE and "85%" in admin["message"] and admin["spent"] == 85 and admin["limit"] == 100.0
    assert other["level"] == bg.OK and other["message"] == "" and "spent" not in other      # the early notice is for admins only
    throttled = bg.evaluate({"month_usd": 110}, {"month_usd": 100.0}, NOW)
    for who in (True, False):
        view = bg.public_view(throttled, who)
        assert view["level"] == bg.THROTTLED and "Low-power mode" in view["message"] and view["paused"] and view["resets_at"]
    assert "spent" not in bg.public_view(throttled, False) and "limit" not in bg.public_view(throttled, False)
    assert bg.public_view({"level": bg.OFF}, True)["level"] == bg.OFF and bg.public_view(None, False)["level"] == bg.OFF


# ---- the tool agent in low-power mode ---------------------------------------------------------------------------------------------

@pytest.fixture
def agent_calls(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"default_branch": "main"}, text=""))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    monkeypatch.setattr(aw, "get_db", lambda: SimpleNamespace(list_collection_names=lambda: ["incidents"]))
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    calls = []

    async def fake_loop(**kwargs):
        calls.append(kwargs)
        return {"final_answer": "ok", "attempts": [], "show_work": True}

    monkeypatch.setattr(aw, "run_react_loop", fake_loop)
    return calls


def tool_turn(message, deep, level):
    state = {"username": "u", "messages": [HumanMessage(content=message)], "documents": [], "deep_thinking": deep, "budget_level": level}
    return run(aw.tool_agent_node(state))


def test_deep_thinking_uses_the_bigger_model_and_more_steps_when_the_budget_is_fine(agent_calls):
    tool_turn("look at the repo", True, bg.OK)
    assert agent_calls[0]["llm"] is aw.lite_llm_deep and agent_calls[0]["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS_DEEP


@pytest.mark.parametrize("level", [bg.THROTTLED, bg.BLOCKED])
def test_in_low_power_mode_deep_thinking_is_off_whatever_was_asked_and_the_steps_are_capped(agent_calls, level):
    tool_turn("look at the repo", True, level)
    assert agent_calls[0]["llm"] is aw.lite_llm and agent_calls[0]["max_iterations"] == bg.THROTTLED_TOOL_STEPS


def test_an_audit_style_message_does_not_switch_deep_mode_back_on_in_low_power_mode(agent_calls):
    tool_turn("audit every file across the whole repo", False, bg.OK)
    assert agent_calls[0]["llm"] is aw.lite_llm_deep   # without the guard that wording turns deep mode on by itself
    tool_turn("audit every file across the whole repo", False, bg.THROTTLED)
    assert agent_calls[1]["llm"] is aw.lite_llm and agent_calls[1]["max_iterations"] == bg.THROTTLED_TOOL_STEPS


def test_an_ordinary_turn_with_no_budget_is_unchanged(agent_calls):
    tool_turn("what does this function do", False, None)
    assert agent_calls[0]["llm"] is aw.lite_llm and agent_calls[0]["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS


# ---- openers -------------------------------------------------------------------------------------------------------------------------

def test_openers_are_paused_in_low_power_mode_and_run_otherwise(monkeypatch):
    opened = []

    async def fake_open(*args, **kwargs):
        opened.append(1)
        return {"content": "hello"}

    monkeypatch.setattr(ini, "maybe_open_conversation", fake_open)
    monkeypatch.setattr(ini, "get_enabled", lambda username: True)
    monkeypatch.setattr("backend.utils.ops_digest.get_enabled", lambda username: False)

    def go(level):
        monkeypatch.setattr(bg, "current_state", lambda *a, **k: {"level": level})
        sessions = {"u::s1": [HumanMessage(content="hi")]}
        return run(ini.open_for_request(
            "u", "s1", sessions, load=lambda u, s: [], sync=lambda *a: None, get_timezone=lambda u: "UTC",
            get_llm=lambda u: SimpleNamespace(ainvoke=None), save=lambda *a: None,
        ))

    assert go(bg.THROTTLED) is None and opened == []
    assert go(bg.BLOCKED) is None and opened == []
    assert go(bg.OK) == {"content": "hello"} and opened == [1]


# ---- the chat turn and the endpoint ---------------------------------------------------------------------------------------------------

def _app_source():
    return (ROOT / "app.py").read_text(encoding="utf-8")


def test_the_chat_turn_decides_the_budget_first_and_pauses_the_extras_and_the_model_when_told_to():
    source = _app_source()
    state_at = source.index("budget_state = await asyncio.to_thread(budget_guard.current_state)")
    assert state_at < source.index('"budget_level": budget_state.get("level")') < source.index("async def token_streamer():")
    assert "effective_deep_thinking = False" in source[state_at: state_at + 300]
    for gate in ('budget_guard.allows("answer_recheck", budget_state)', 'budget_guard.allows("memory_summaries", budget_state)',
                 'budget_guard.allows("open_loops", budget_state)'):
        assert source.count(gate) == 1, gate
    streamer = source[source.index("async def token_streamer():"): source.index("async def steerable_stream():")]
    blocked_at = streamer.index("if budget_guard.blocked(budget_state):")
    assert blocked_at < streamer.index("workflow.ainvoke")           # the stop is before any model-calling work
    block = streamer[blocked_at: streamer.index("        try:\n", blocked_at)]
    assert "in_crisis(" in block and "blocked_reply(" in block and "return" in block and "astream" not in block and "ainvoke" not in block
    assert "'event': 'budget'" in streamer


def test_the_budget_endpoint_is_thin_and_everyone_signed_in_may_ask_but_only_admins_get_amounts():
    source = _app_source()
    start = source.index('@app.get("/api/budget")')
    block = source[start: source.index('@app.get("/api/settings/proactive-opening")')]
    assert "Depends(get_current_user)" in block and "budget_guard.public_view(state, is_global_admin(" in block and len(block.splitlines()) < 12


def test_the_health_page_reports_the_budget_to_admins():
    health = (ROOT / "backend" / "utils" / "sonic_health.py").read_text(encoding="utf-8")
    assert '"budget": budget_view(budget_state(db, now), True)' in health


# ---- spend from before the meter existed --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-10:50.02", 50.02), ("2026-09:12,2026-10:50", 50.0), (" 2026-10 : 7 ", 0.0), ("2026-10:abc", 0.0),
    ("2026-10:-5", 0.0), ("2026-11:40", 0.0), ("", 0.0), ("nonsense", 0.0), ("2026-10:3,2026-10:4", 7.0),
])
def test_money_already_spent_this_month_counts_only_for_that_month_and_junk_is_ignored(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_BUDGET_MONTH_ALREADY_USD", raw)
    assert bg.month_already_spent(NOW) == pytest.approx(expected)


def test_the_already_spent_amount_moves_the_monthly_dollar_budget_and_not_the_daily_one(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", "60")
    monkeypatch.setenv("LLM_BUDGET_DAILY_USD", "3")
    monkeypatch.setenv("LLM_BUDGET_MONTH_ALREADY_USD", "2026-10:50.02")
    state = bg.current_state(_DB([]), NOW)
    detail = {d["scope"]: d for d in state["detail"]}
    assert detail["month"]["spent"] == pytest.approx(50.02) and detail["day"]["spent"] == 0
    assert state["level"] == bg.NOTICE and state["scope"] == "month"      # 83% of the month before a single call is counted
    assert bg.current_state(_DB([]), datetime(2026, 11, 2, tzinfo=timezone.utc))["detail"][0]["spent"] == 0  # and gone in November
