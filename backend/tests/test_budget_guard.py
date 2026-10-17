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
       "LLM_BUDGET_NOTICE_AT", "LLM_BUDGET_BLOCK_AT", "LLM_PRICES", "LLM_BUDGET_5H_USD", "LLM_BUDGET_5H_TOKENS",
       "LLM_USER_BUDGET_5H_TOKENS", "LLM_USER_BUDGET_DAILY_TOKENS")


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
    assert notice < 1.0 <= block                         # a block line of exactly 1.0 is allowed now: it makes a budget a hard cap


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


def test_a_daily_budget_resets_at_the_next_pacific_midnight_and_a_monthly_one_on_the_first():
    day = bg.evaluate({"day_tokens": 120}, {"day_tokens": 100.0}, NOW)
    assert day["resets_at"] == "2026-10-11T00:00:00-07:00"  # Google's day turns over at midnight Pacific (PDT in October)
    month = bg.evaluate({"month_tokens": 120}, {"month_tokens": 100.0}, NOW)
    assert month["resets_at"] == "2026-11-01T00:00:00-07:00"
    december = bg.evaluate({"month_tokens": 120}, {"month_tokens": 100.0}, datetime(2026, 12, 20, 9, tzinfo=timezone.utc))
    assert december["resets_at"] == "2027-01-01T00:00:00-08:00"  # PST in winter


def test_the_budget_follows_the_billing_zone_when_it_is_overridden(monkeypatch):
    monkeypatch.setenv("LLM_BILLING_TIMEZONE", "UTC")
    assert bg.evaluate({"day_tokens": 120}, {"day_tokens": 100.0}, NOW)["resets_at"] == "2026-10-11T00:00:00+00:00"


def test_todays_spend_is_todays_in_the_billing_zone_not_in_utc():
    # 00:05 UTC on the 12th is still the 11th in Pacific time: rows keyed 2026-10-11 are today's, rows keyed 2026-10-12 are not yet.
    moment = datetime(2026, 10, 12, 0, 5, tzinfo=timezone.utc)
    rows = [
        {"day": "2026-10-11", "model": "m", "label": "x", "calls": 1, "input": 100, "output": 0, "reasoning": 0, "cached": 0},
        {"day": "2026-10-12", "model": "m", "label": "x", "calls": 1, "input": 7, "output": 0, "reasoning": 0, "cached": 0},
    ]
    totals = bg.spent(rows, moment, {})
    assert totals["day_tokens"] == 100 and totals["month_tokens"] == 107


def test_the_already_spent_offset_belongs_to_the_billing_month_not_the_utc_one(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_MONTH_ALREADY_USD", "2026-10:50.02")
    assert bg.month_already_spent(datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)) == 50.02  # still October in Pacific time
    assert bg.month_already_spent(datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)) == 0.0   # now November there


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


class _Other:
    """Any collection the guard reads besides the day rows: minute rows, a person's rows, admin extensions, saved window starts. Plain dicts."""

    def __init__(self, rows=(), fail=False):
        self.rows, self.fail, self.queries, self.updates = [dict(r) for r in rows], fail, [], []

    def _matches(self, flt, row):
        for key, want in flt.items():
            have = row.get(key)
            if isinstance(want, dict) and "$gte" in want:
                if have is None or have < want["$gte"]:
                    return False
            elif have != want:
                return False
        return True

    def find(self, flt, projection=None):
        self.queries.append(flt)
        if self.fail:
            raise OSError("db down")
        return [dict(r) for r in self.rows if self._matches(flt, r)]

    def find_one(self, flt, projection=None):
        found = self.find(flt, projection)
        return found[0] if found else None

    def update_one(self, flt, update, upsert=False):
        if self.fail:
            raise OSError("db down")
        row = next((r for r in self.rows if self._matches(flt, r)), None)
        if row is None:
            row = dict(flt)
            self.rows.append(row)
        row.update(update.get("$set", {}))
        for key, by in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + by
        self.updates.append((flt, update))


class _DB:
    def __init__(self, rows=(), fail=False, minutes=(), people=(), extensions=(), windows=()):
        self.col = _Col(list(rows), fail)
        self.others = {
            lu.MINUTE_COLLECTION: _Other(minutes, fail), "llm_usage_user_minutely": _Other(people, fail),
            bg.EXTENSIONS_COLLECTION: _Other(extensions, fail), bg.WINDOWS_COLLECTION: _Other(windows, fail),
        }

    def __getitem__(self, name):
        if name == lu.COLLECTION:
            return self.col
        return self.others[name]


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


def test_audit_style_wording_gets_the_wide_step_budget_on_the_cheap_model_never_the_stronger_one(agent_calls):
    tool_turn("audit every file across the whole repo", False, bg.OK)
    assert agent_calls[0]["llm"] is aw.lite_llm                                              # not the dearer model
    assert agent_calls[0]["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS_DEEP             # but the room an audit needs
    assert agent_calls[0]["max_retry_nudges"] == aw.TOOL_AGENT_MAX_RETRY_NUDGES_DEEP
    tool_turn("audit every file across the whole repo", True, bg.OK)                        # the user's own setting still gets it
    assert agent_calls[1]["llm"] is aw.lite_llm_deep


def test_audit_style_wording_does_not_widen_the_budget_back_in_low_power_mode(agent_calls):
    tool_turn("audit every file across the whole repo", False, bg.THROTTLED)
    assert agent_calls[0]["llm"] is aw.lite_llm and agent_calls[0]["max_iterations"] == bg.THROTTLED_TOOL_STEPS


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
    state_at = source.index("budget_state = await asyncio.to_thread(budget_guard.current_state, None, None, budget_guard.limit_username(username, is_admin_user))")
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
    block = source[start: source.index('@app.post("/api/budget/extend")')]
    assert "Depends(get_current_user)" in block and "budget_guard.public_view(state, admin)" in block and "is_global_admin(" in block
    assert len(block.splitlines()) < 12


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


# ---- the five-hour window, per-person limits and extending a block ----------------------------------------------------------------

def at(hour, minute=0, day=10):
    """A moment on the test day (2026-10-10, UTC) by clock time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def minute_of(moment):
    return lu.minute_key(moment)


def minute_row(moment, input_tokens=0, output_tokens=0, model="m", calls=1):
    return {"minute": minute_of(moment), "model": model, "calls": calls, "input": input_tokens, "output": output_tokens, "reasoning": 0, "cached": 0}


def person_row(user, moment, tokens):
    return {"user": user, "minute": minute_of(moment), "tokens": tokens, "calls": 1, "turns": 1}


def test_the_five_hour_and_per_person_limits_are_read_from_their_own_settings(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_USD", "0.60")
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "500000")
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "300000")
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_TOKENS", "0")  # not a limit
    assert bg.budgets() == {"five_usd": 0.6, "five_tokens": 500_000.0}
    assert bg.user_budgets() == {"my_five_tokens": 300_000.0}


# -- when a window starts and ends (pure)

def test_the_first_use_starts_a_window_that_lasts_exactly_five_hours():
    assert bg.resolve_window(None, [at(14)], at(15)) == (at(14), at(19))
    assert bg.resolve_window(None, [at(14), at(14, 40), at(18, 59)], at(18, 59)) == (at(14), at(19))  # more use inside it does not move its end


def test_when_the_window_ends_usage_starts_again_from_nothing_whatever_happened_in_it():
    assert bg.resolve_window(at(14), [at(14), at(18, 59)], at(18, 59)) == (at(14), at(19))
    assert bg.resolve_window(at(14), [at(14), at(18, 59)], at(19)) is None  # the heavy use right up to the end counts for nothing now


def test_the_next_window_starts_at_the_first_use_after_the_last_one_ended():
    window = bg.resolve_window(at(10), [at(10), at(12), at(15, 30)], at(16))
    assert window == (at(15, 30), at(20, 30))  # not at 15:00, when the last one ended: nothing was used until 15:30


def test_use_after_the_window_ended_but_before_now_is_what_starts_the_next_one_and_later_use_is_ignored():
    assert bg.resolve_window(at(10), [at(10), at(15, 10)], at(15, 20)) == (at(15, 10), at(20, 10))
    assert bg.resolve_window(at(10), [at(10), at(15, 10)], at(15, 5)) is None  # that use has not happened yet


def test_several_idle_windows_are_walked_past_to_the_one_in_progress():
    assert bg.resolve_window(at(2), [at(2), at(7, 30), at(13)], at(14)) == (at(13), at(18))


def test_with_no_saved_start_only_use_in_the_last_five_hours_counts_as_a_window_in_progress():
    assert bg.resolve_window(None, [at(8)], at(15)) is None        # six hours ago: that window is over
    assert bg.resolve_window(None, [at(11)], at(15)) == (at(11), at(16))


def test_a_window_with_no_use_in_it_at_all_is_idle():
    assert bg.resolve_window(None, [], at(15)) is None and bg.resolve_window(at(10), [], at(12)) == (at(10), at(15))


# -- what it counts

def test_the_window_counts_only_use_since_it_began_and_not_the_one_before():
    rows = [minute_row(at(12), 9_000), minute_row(at(15, 10), 100, 20)]
    totals = bg.spent([], at(15, 30), {}, rows, anchor=at(10))  # the window that began at 10:00 ended at 15:00; the next began at 15:10
    assert totals["five_tokens"] == 120 and totals["five_start"] == at(15, 10) and totals["five_reset"] == at(20, 10)


def test_the_window_prices_its_usage_like_the_day_does():
    prices = {"m": {"input": 1.0, "output": 2.0, "cached_input": 1.0}}
    totals = bg.spent([], at(15), prices, [minute_row(at(14), 1_000_000, 500_000)])
    assert totals["five_usd"] == pytest.approx(2.0)


def test_an_empty_or_malformed_row_does_not_start_a_window():
    junk = [{"minute": "not a minute", "calls": 3}, {"calls": 3}, minute_row(at(14), 0, 0, calls=0)]
    totals = bg.spent([], at(15), {}, junk)
    assert totals["five_start"] is None and totals["five_tokens"] == 0


# -- the guard's decision

def test_a_burst_trips_the_five_hour_window_long_before_the_daily_budget_notices(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000000")
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 1_500, 0)], minutes=[minute_row(at(14), 1_500)])
    state = bg.current_state(db, at(15))
    assert state["level"] == bg.BLOCKED and state["scope"] == "five" and state["blocked_scopes"] == ["five"]
    assert state["resets_at"].startswith("2026-10-10T19:00")  # the window began at 14:00, so it ends at 19:00 exactly
    assert state["resets"]["five"] == state["resets_at"]


def test_someone_who_maxes_it_out_three_minutes_before_it_ends_can_use_it_again_in_three_minutes(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    db = _DB(minutes=[minute_row(at(14), 100), minute_row(at(18, 55), 5_000)])
    blocked = bg.current_state(db, at(18, 57))
    assert blocked["level"] == bg.BLOCKED and blocked["resets_at"].startswith("2026-10-10T19:00")
    bg._reset_cache()
    free = bg.current_state(db, at(19, 0))
    assert free["level"] == bg.OK and free["spent"] == 0  # back to 0%, with no waiting for the heavy use to age out


def test_the_first_use_after_a_window_ends_starts_a_new_one_and_it_is_remembered(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    db = _DB(minutes=[minute_row(at(14), 5_000)])
    bg.current_state(db, at(15))
    saved = db.others[bg.WINDOWS_COLLECTION].rows
    assert len(saved) == 1 and saved[0]["scope"] == "five" and saved[0]["user"] == "" and saved[0]["start"] == at(14).isoformat()
    # Idle after 19:00; then something is used at 19:10: that starts the next window, which counts only that.
    db.others[lu.MINUTE_COLLECTION].rows.append(minute_row(at(19, 10), 300))
    bg._reset_cache()
    state = bg.current_state(db, at(19, 20))
    assert state["spent"] == 300 and state["level"] == bg.OK and state["resets_at"].startswith("2026-10-11T00:10")
    assert db.others[bg.WINDOWS_COLLECTION].rows[0]["start"] == at(19, 10).isoformat()


def test_a_saved_start_that_is_too_old_is_not_followed(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    old = {"scope": "five", "user": "", "start": at(1, day=5).isoformat()}
    db = _DB(minutes=[minute_row(at(14), 5_000)], windows=[old])
    assert bg.current_state(db, at(15))["scope"] == "five"  # worked out afresh from the usage, not from a window days ago


def test_the_minute_rows_are_only_read_when_a_five_hour_limit_is_set(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 10, 0)])
    bg.current_state(db, NOW)
    assert db.others[lu.MINUTE_COLLECTION].queries == [] and db.others[bg.WINDOWS_COLLECTION].queries == []


def test_the_blocked_message_names_the_time_for_a_five_hour_window_and_the_day_for_a_day():
    five = bg.blocked_reply("2026-10-10T19:00:00+00:00", False, "five")
    assert "until 12:00 PM (Pacific time)" in five and "my usage limit" in five
    assert "until 2026-10-11 (Pacific time)" in bg.blocked_reply("2026-10-11T07:00:00+00:00", False, "day")
    own = bg.blocked_reply("2026-10-10T19:00:00+00:00", False, "my_five")
    assert "your own usage limit" in own and "12:00 PM" in own


# -- extending a block

def test_an_extension_raises_the_block_line_of_its_own_scope_only(monkeypatch):
    limits = {"five_tokens": 1000.0, "day_tokens": 1000.0}
    totals = {"five_tokens": 1_400, "day_tokens": 100, "five_reset": NOW}
    assert bg.evaluate(totals, limits, NOW)["level"] == bg.BLOCKED
    extended = bg.evaluate(totals, limits, NOW, {"five": 0.25})                  # 125% + 25%: the block line is now 150%
    assert extended["level"] == bg.THROTTLED and extended["paused"] and extended["extended_by"] == 0.25
    assert bg.evaluate({**totals, "five_tokens": 1_550}, limits, NOW, {"five": 0.25})["level"] == bg.BLOCKED
    other = bg.evaluate(totals, limits, NOW, {"day": 0.25})                      # extending the day does nothing for the window
    assert other["level"] == bg.BLOCKED and other["blocked_scopes"] == ["five"]


def test_an_extension_never_lowers_the_throttle_line_so_low_power_stays_on(monkeypatch):
    state = bg.evaluate({"day_tokens": 1_100}, {"day_tokens": 1000.0}, NOW, {"day": 0.25})
    assert state["level"] == bg.THROTTLED and bg.low_power(state)


def test_a_running_extension_saved_by_an_admin_is_honoured_and_an_expired_one_is_not(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    soon, past = "2026-10-10T20:00:00+00:00", "2026-10-10T10:00:00+00:00"
    extensions = [{"scope": "day", "extra": 0.25, "until": soon}, {"scope": "five", "extra": 0.5, "until": past}]
    db = _DB([row("m", "2026-10-10", 1_300, 0)], minutes=[minute_row(at(14), 1_300)], extensions=extensions)
    state = bg.current_state(db, NOW)
    assert dict((d["scope"], d["fraction"]) for d in state["detail"]) == {"day": 1.3, "five": 1.3}
    assert state["blocked_scopes"] == ["five"]  # the day's block line moved to 150%; the five-hour one expired, so it is back at 125%


def test_granting_an_extension_adds_a_step_records_who_and_when_and_ends_with_the_scope(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 1_300, 0)])
    state = bg.current_state(db, NOW)
    assert bg.extendable_scopes(state) == ["day"]
    first = bg.grant_extension(db, "admin@example.com", state, NOW)
    saved = db.others[bg.EXTENSIONS_COLLECTION].rows[0]
    assert first["extended"] == [{"scope": "day", "extra": 0.25, "until": bg.billing_time.next_day_start(NOW).isoformat()}]
    assert saved["extra"] == 0.25 and saved["grants"] == [{"by": "admin@example.com", "at": NOW.isoformat(), "extra": 0.25}]
    # The state, read again, is no longer blocked: 130% is under the new 150% line (but still throttled).
    assert bg.current_state(db, NOW)["level"] == bg.THROTTLED
    db.col.rows[0]["input"] = 1_600
    bg._reset_cache()
    again = bg.current_state(db, NOW)
    assert again["level"] == bg.BLOCKED
    bg.grant_extension(db, "admin@example.com", again, NOW)
    assert db.others[bg.EXTENSIONS_COLLECTION].rows[0]["extra"] == 0.5 and len(db.others[bg.EXTENSIONS_COLLECTION].rows[0]["grants"]) == 2


def test_a_five_hour_extension_ends_with_the_window_it_was_granted_in(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    db = _DB(minutes=[minute_row(at(14), 1_400)])
    state = bg.current_state(db, NOW)
    assert state["level"] == bg.BLOCKED
    granted = bg.grant_extension(db, "admin@example.com", state, NOW)
    assert granted["extended"][0]["until"] == at(19).isoformat()  # not five hours from the click: the window began at 14:00
    bg._reset_cache()
    assert bg.current_state(db, NOW)["level"] == bg.THROTTLED


def test_a_grant_after_the_last_one_ran_out_starts_again_from_one_step(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_5H_TOKENS", "1000")
    old = {"scope": "five", "extra": 0.75, "until": "2026-10-10T09:00:00+00:00", "grants": [{"by": "a", "at": "x", "extra": 0.75}]}
    db = _DB(minutes=[minute_row(at(14), 1_400)], extensions=[old])
    state = bg.current_state(db, NOW)
    assert state["level"] == bg.BLOCKED
    bg.grant_extension(db, "admin@example.com", state, NOW)
    saved = db.others[bg.EXTENSIONS_COLLECTION].rows[0]
    assert saved["extra"] == 0.25 and len(saved["grants"]) == 1
    assert saved["until"] == at(19).isoformat()


def test_nothing_blocking_means_nothing_is_granted(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    db = _DB([row("m", "2026-10-10", 100, 0)])
    assert bg.grant_extension(db, "admin@example.com", bg.current_state(db, NOW), NOW) == {"extended": []}
    assert db.others[bg.EXTENSIONS_COLLECTION].rows == []


# -- one person's own limits

def test_a_person_over_their_own_five_hour_limit_is_paused_until_their_window_ends_and_nobody_else_is(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    db = _DB(people=[person_row("jack", at(14), 1_300), person_row("sam", at(14), 100)])
    mine = bg.current_state(db, at(15), "jack")
    assert mine["level"] == bg.BLOCKED and mine["scope"] == "my_five" and mine["resets_at"].startswith("2026-10-10T19:00")
    assert bg.current_state(db, at(15), "sam")["level"] == bg.OK
    assert bg.current_state(db, at(15))["level"] == bg.OFF  # no person named (an admin, or anything that is not a turn): no per-person limit applies


def test_a_persons_window_starts_at_their_own_first_use_and_resets_to_zero_at_its_end(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    db = _DB(people=[person_row("jack", at(14), 400), person_row("jack", at(18, 56), 5_000)])
    assert bg.current_state(db, at(18, 58), "jack")["level"] == bg.BLOCKED
    free = bg.current_state(db, at(19, 1), "jack")
    assert free["level"] == bg.OK and free["spent"] == 0
    saved = [r for r in db.others[bg.WINDOWS_COLLECTION].rows if r["user"] == "jack"]
    assert saved and saved[0]["scope"] == "my_five" and saved[0]["start"] == at(14).isoformat()


def test_two_people_have_their_own_windows(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    db = _DB(people=[person_row("jack", at(9), 5_000), person_row("sam", at(14), 5_000)])
    assert bg.current_state(db, at(15), "jack")["level"] == bg.OK          # their window began at 09:00 and ended at 14:00
    assert bg.current_state(db, at(15), "sam")["level"] == bg.BLOCKED


def test_a_persons_own_daily_limit_counts_their_billing_day(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_DAILY_TOKENS", "1000")
    # Pacific midnight is 07:00 UTC: 06:00 UTC belongs to yesterday's billing day, 07:00 to today's.
    db = _DB(people=[person_row("jack", at(6), 5_000), person_row("jack", at(7), 600), person_row("jack", at(14), 500)])
    state = bg.current_state(db, NOW, "jack")
    assert state["scope"] == "my_day" and state["spent"] == 1_100 and state["level"] == bg.THROTTLED


def test_the_turn_that_just_finished_counts_toward_a_persons_limit_before_it_is_saved(monkeypatch):
    from backend.utils import user_usage

    user_usage.reset()
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    db = _DB(people=[person_row("jack", at(14), 600)])
    user_usage.add("jack", 500, 2, now=at(15))
    assert bg.current_state(db, at(15), "jack")["spent"] == 1_100
    user_usage.reset()


def test_a_persons_own_limit_is_not_something_an_admin_can_extend_and_it_reads_as_theirs(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    state = bg.current_state(_DB(people=[person_row("jack", at(14), 1_300)]), at(15), "jack")
    assert state["level"] == bg.BLOCKED and bg.extendable_scopes(state) == []
    seen = bg.public_view(state, False)
    assert seen["own_limit"] is True and "your usage limit" in seen["message"] and "spent" not in seen
    admin = bg.public_view(state, True)
    assert admin["can_extend"] is False


def test_an_admin_is_told_when_plus_25_percent_is_on_offer_and_how_far_it_has_gone(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_DAILY_TOKENS", "1000")
    state = bg.current_state(_DB([row("m", "2026-10-10", 1_300, 0)]), NOW)
    assert bg.public_view(state, True)["can_extend"] is True
    assert "can_extend" not in bg.public_view(state, False)  # nobody else is shown the lever, or any amount


def test_the_extend_endpoint_is_admin_only_refuses_when_nothing_is_blocking_and_saves_off_the_event_loop():
    source = _app_source()
    start = source.index('@app.post("/api/budget/extend")')
    block = source[start: source.index('@app.get("/api/settings/proactive-opening")')]
    assert block.index("is_global_admin(") < block.index("status_code=403") < block.index("extendable_scopes(") < block.index("status_code=409")
    assert "await asyncio.to_thread(budget_guard.grant_extension, get_db(), username, state)" in block
    assert block.index("grant_extension") < block.index("budget_guard.public_view(fresh, True)")
    assert username_not_logged(block)


def username_not_logged(block):
    """The log line about a grant names no one (the saved grant does)."""
    log = next(line for line in block.splitlines() if "logger.info" in line)
    return "username" not in log


def test_the_chat_turn_applies_a_persons_own_limits_to_everyone_admins_included_and_says_which_limit_it_was():
    source = _app_source()
    assert "budget_guard.current_state, None, None, budget_guard.limit_username(username, is_admin_user)" in source
    assert "budget_guard.blocked_reply(budget_state.get('resets_at'), crisis, budget_state.get('scope'))" in source


def test_a_persons_window_counts_only_their_window_not_the_rest_of_their_day(monkeypatch):
    monkeypatch.setenv("LLM_USER_BUDGET_5H_TOKENS", "1000")
    # 3,000 tokens this morning in an earlier window; the current window (from 14:00) has 400.
    db = _DB(people=[person_row("jack", at(8), 3_000), person_row("jack", at(14), 400)])
    state = bg.current_state(db, at(15), "jack")
    assert state["scope"] == "my_five" and state["spent"] == 400 and state["level"] == bg.OK


def test_once_the_window_has_ended_nothing_is_counted_and_no_window_is_in_progress():
    rows = [minute_row(at(14), 5_000)]
    totals = bg.spent([], at(19, 30), {}, rows, anchor=at(14))
    assert totals["five_tokens"] == 0 and totals["five_start"] is None
    person = bg.person_totals([person_row("jack", at(14), 5_000)], at(19, 30), at(14))
    assert person["my_five_tokens"] == 0 and person["my_five_start"] is None
