import os
from datetime import datetime, timezone

import pytest

from backend.utils import llm_turns, sonic_health
from backend.utils import user_usage as uu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime(2026, 10, 10, 15, 30, tzinfo=timezone.utc)       # 08:30 on the 10th in Pacific time
DAYS = ["2026-10-08", "2026-10-09", "2026-10-10"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    uu.reset()
    llm_turns.reset()
    monkeypatch.setattr(uu, "_daily_index_ready", False)
    yield
    uu.reset()
    llm_turns.reset()


class _Col:
    def __init__(self):
        self.rows, self.fail, self.indexes = [], False, []

    def update_one(self, flt, update, upsert=False):
        if self.fail:
            raise OSError("db down")
        row = next((r for r in self.rows if all(r.get(k) == v for k, v in flt.items())), None)
        if row is None:
            row = dict(flt)
            self.rows.append(row)
        for key, by in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + by

    def find(self, flt, projection=None):
        if "day" in flt:
            return [dict(r) for r in self.rows if r["day"] >= flt["day"]["$gte"]]
        return [dict(r) for r in self.rows if r["user"] == flt["user"] and r["minute"] >= flt["minute"]["$gte"]]

    def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))


class _DB:
    def __init__(self):
        self.minute, self.daily = _Col(), _Col()

    def __getitem__(self, name):
        return self.minute if name == uu.COLLECTION else self.daily


def _row(user, day, cost_micro, tokens=1000, turns=1, calls=2):
    return {"user": user, "day": day, "tokens": tokens, "calls": calls, "turns": turns, "cost_micro": cost_micro}


# ---------------------------------------------------------------------------------------------------------------
# Counting by day
# ---------------------------------------------------------------------------------------------------------------

def test_a_turn_is_also_counted_in_the_persons_billing_day():
    uu.add("jack", 900, 3, now=NOW, cost_micro=40_000)
    uu.add("jack", 100, 1, now=NOW, cost_micro=10_000)
    assert uu._pending_daily == {("jack", "2026-10-10"): {"tokens": 1_000, "calls": 4, "turns": 2, "cost_micro": 50_000}}


def test_the_day_is_the_billing_day_not_the_utc_day():
    late_utc = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)      # still the evening of the 10th in Pacific time
    uu.add("jack", 100, 1, now=late_utc)
    assert list(uu._pending_daily) == [("jack", "2026-10-10")]


def test_flush_saves_one_row_per_person_and_day_and_makes_them_expire():
    db = _DB()
    uu.add("jack", 900, 3, now=NOW, cost_micro=40_000)
    uu.add("alice", 500, 2, now=NOW, cost_micro=10_000)
    uu.flush(db)
    saved = {(r["user"], r["day"]): r for r in db.daily.rows}
    assert saved[("jack", "2026-10-10")]["cost_micro"] == 40_000 and saved[("alice", "2026-10-10")]["tokens"] == 500
    assert db.daily.indexes and db.daily.indexes[0][1] == {"expireAfterSeconds": uu.DAILY_KEEP_SECONDS}
    assert uu.DAILY_KEEP_SECONDS >= 100 * 24 * 3600, "a month of history has to survive"


def test_a_failed_write_keeps_the_daily_counts_too():
    db = _DB()
    db.daily.fail = True
    uu.add("jack", 900, 3, now=NOW, cost_micro=40_000)
    assert uu.flush(db) == 0
    assert uu._pending_daily[("jack", "2026-10-10")]["cost_micro"] == 40_000
    db.daily.fail = False
    uu.flush(db)
    assert db.daily.rows[0]["cost_micro"] == 40_000


def test_reading_back_includes_what_has_not_been_saved_yet_and_nothing_older_than_the_period():
    db = _DB()
    db.daily.rows = [_row("jack", "2026-10-09", 20_000), _row("jack", "2026-09-01", 99_000_000)]
    uu.add("jack", 100, 1, now=NOW, cost_micro=5_000)
    rows = uu.fetch_daily(db, 3, NOW)
    assert sorted((r["day"], r["cost_micro"]) for r in rows) == [("2026-10-09", 20_000), ("2026-10-10", 5_000)]


# ---------------------------------------------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------------------------------------------

def test_people_are_ranked_by_what_they_cost_with_the_figures_a_price_needs():
    rows = [
        _row("alice", "2026-10-08", 100_000, turns=4), _row("alice", "2026-10-10", 300_000, turns=4),
        _row("jack", "2026-10-10", 600_000, turns=10),
        _row("bob", "2026-10-09", 0, tokens=50, turns=1),
    ]
    out = uu.summarize_users(rows, DAYS)
    assert [r["user"] for r in out["rows"]] == ["jack", "alice", "bob"]
    jack, alice, bob = out["rows"]
    assert jack["cost"] == 0.6 and jack["share"] == 0.6 and jack["cost_per_turn"] == 0.06 and jack["active_days"] == 1 and jack["cost_per_active_day"] == 0.6
    assert alice["cost"] == 0.4 and alice["turns"] == 8 and alice["active_days"] == 2 and alice["cost_per_active_day"] == 0.2
    assert alice["per_day"] == [0.1, 0.0, 0.3]
    assert out["people"] == 3 and out["cost"] == 1.0 and out["priced"] is True and out["days"] == DAYS
    assert bob["cost"] == 0.0


def test_days_outside_the_period_are_ignored():
    out = uu.summarize_users([_row("jack", "2026-10-10", 100_000), _row("jack", "2026-08-01", 5_000_000)], DAYS)
    assert out["cost"] == 0.1 and out["rows"][0]["active_days"] == 1


def test_only_the_biggest_are_listed_and_the_rest_are_added_up():
    rows = [_row(f"user{i}", "2026-10-10", (i + 1) * 1_000) for i in range(6)]
    out = uu.summarize_users(rows, DAYS, limit=4)
    assert len(out["rows"]) == 4 and out["rows"][0]["user"] == "user5"
    assert out["others"] == {"people": 2, "cost": 0.003, "tokens": 2000}
    assert uu.summarize_users(rows, DAYS, limit=10)["others"] is None


def test_without_prices_it_says_so_and_ranks_by_tokens():
    out = uu.summarize_users([_row("jack", "2026-10-10", 0, tokens=900), _row("alice", "2026-10-10", 0, tokens=100)], DAYS)
    assert out["priced"] is False and out["cost"] == 0
    assert [r["user"] for r in out["rows"]] == ["jack", "alice"]
    assert out["rows"][0]["share"] == 0.9, "with no dollars the share falls back to tokens"
    assert out["unattributed_cost"] is None


def test_what_is_not_tied_to_anyone_is_the_difference_from_the_pages_own_total():
    out = uu.summarize_users([_row("jack", "2026-10-10", 600_000)], DAYS, total_cost=1.0)
    assert out["unattributed_cost"] == 0.4
    assert uu.summarize_users([_row("jack", "2026-10-10", 600_000)], DAYS, total_cost=0.5)["unattributed_cost"] == 0.0
    assert uu.summarize_users([_row("jack", "2026-10-10", 600_000)], DAYS, total_cost=None)["unattributed_cost"] is None


def test_nothing_counted_is_an_empty_but_complete_summary():
    out = uu.summarize_users([], DAYS)
    assert out["rows"] == [] and out["people"] == 0 and out["priced"] is False and out["others"] is None


def test_the_summary_holds_counts_and_account_names_and_nothing_else():
    out = uu.summarize_users([_row("jack", "2026-10-10", 100_000)], DAYS, evals=_eval_days(10, 0.5))
    expected = {"user", "kind", "cost", "tokens", "turns", "calls", "cost_per_turn", "cost_per_active_day", "active_days", "share", "per_day"}
    assert all(set(row) == expected for row in out["rows"])
    assert set(out) == {"days", "people", "priced", "cost", "tokens", "rows", "others", "evals", "unattributed_cost"}


def _eval_days(calls, cost, days=DAYS):
    """The usage page's daily rows (summarize_daily), with the eval part of each day filled in on the last day only."""
    rows = [{"day": day, "eval_calls": 0, "eval_tokens": 0, "eval_cost": 0.0} for day in days]
    rows[-1].update({"eval_calls": calls, "eval_tokens": calls * 1000, "eval_cost": cost})
    return rows


# ---------------------------------------------------------------------------------------------------------------
# Testing and evals, listed beside the people
# ---------------------------------------------------------------------------------------------------------------

def test_the_evals_get_their_own_entry_sorted_in_by_cost_and_marked_as_not_a_person():
    out = uu.summarize_users([_row("jack", "2026-10-10", 600_000), _row("alice", "2026-10-10", 100_000)], DAYS, evals=_eval_days(20, 0.3))
    assert [(r["user"], r["kind"]) for r in out["rows"]] == [("jack", "person"), (uu.EVALS_NAME, "evals"), ("alice", "person")]
    evals = out["rows"][1]
    assert evals["cost"] == 0.3 and evals["calls"] == 20 and evals["tokens"] == 20_000 and evals["active_days"] == 1
    assert evals["turns"] == 0 and evals["cost_per_turn"] is None and evals["per_day"] == [0.0, 0.0, 0.3]
    assert out["evals"] == {"cost": 0.3, "tokens": 20_000, "calls": 20}


def test_the_evals_are_not_counted_as_people_or_in_what_people_cost():
    out = uu.summarize_users([_row("jack", "2026-10-10", 600_000)], DAYS, evals=_eval_days(20, 0.4))
    assert out["people"] == 1 and out["cost"] == 0.6
    assert out["rows"][0]["share"] == 0.6, "shares are of everything counted: people and tests together (0.6 of 1.0)"
    assert [r for r in out["rows"] if r["kind"] == "evals"][0]["share"] == 0.4


def test_what_is_tied_to_neither_people_nor_evals_is_what_remains():
    out = uu.summarize_users([_row("jack", "2026-10-10", 600_000)], DAYS, total_cost=1.5, evals=_eval_days(20, 0.4))
    assert out["unattributed_cost"] == 0.5


def test_a_period_with_only_evals_still_shows_them_and_is_priced():
    out = uu.summarize_users([], DAYS, evals=_eval_days(20, 0.4))
    assert out["people"] == 0 and out["priced"] is True and [r["kind"] for r in out["rows"]] == ["evals"]


def test_no_evals_means_no_entry():
    assert uu.summarize_users([_row("jack", "2026-10-10", 100_000)], DAYS, evals=_eval_days(0, 0.0))["evals"] is None
    assert all(r["kind"] == "person" for r in uu.summarize_users([_row("jack", "2026-10-10", 100_000)], DAYS)["rows"])
    assert uu.summarize_users([_row("jack", "2026-10-10", 100_000)], DAYS, evals=None)["evals"] is None


def test_eval_days_outside_the_period_and_unpriced_evals_are_handled():
    outside = [{"day": "2026-01-01", "eval_calls": 5, "eval_tokens": 5000, "eval_cost": 9.0}]
    assert uu.summarize_users([], DAYS, evals=outside)["evals"] is None
    unpriced = [{"day": "2026-10-10", "eval_calls": 5, "eval_tokens": 5000, "eval_cost": None}]
    out = uu.summarize_users([], DAYS, evals=unpriced)
    assert out["evals"] == {"cost": 0.0, "tokens": 5000, "calls": 5} and out["priced"] is False


def test_evals_are_never_cut_by_the_list_limit():
    rows = [_row(f"user{i}", "2026-10-10", (i + 1) * 1_000) for i in range(6)]
    out = uu.summarize_users(rows, DAYS, limit=2, evals=_eval_days(5, 0.0001))
    assert len(out["rows"]) == 3 and any(r["kind"] == "evals" for r in out["rows"])


def test_the_health_page_passes_the_evals_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(sonic_health.user_usage, "summarize_users", lambda rows, days, total_cost=None, evals=None, **k: seen.update(evals=list(evals or [])) or {})
    monkeypatch.setattr(sonic_health.user_usage, "fetch_daily", lambda db, days, now: [])
    for name in ("fetch_usage_docs", "fetch_turn_docs", "fetch_recent_events", "fetch_counter_docs", "fetch_route_docs"):
        monkeypatch.setattr(sonic_health, name, lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "summarize_safety_events", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "summarize_counters", lambda *a, **k: {"totals": {}})
    monkeypatch.setattr(sonic_health, "fetch_corrections", lambda db: [])
    monkeypatch.setattr(sonic_health, "summarize_feedback", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "summarize_graph", lambda db: {})
    monkeypatch.setattr(sonic_health.routines_utils, "routines_view", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "list_observations", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "budget_state", lambda db, now: {})
    monkeypatch.setattr(sonic_health, "budget_view", lambda state, admin: {})
    sonic_health.build_sonic_health(_PageDB(), 3, NOW)
    assert [row["day"] for row in seen["evals"]] == DAYS and "eval_cost" in seen["evals"][0]


# ---------------------------------------------------------------------------------------------------------------
# On the page
# ---------------------------------------------------------------------------------------------------------------

class _PageDB:
    def __getitem__(self, name):
        raise RuntimeError("not needed")


def test_the_health_payload_carries_the_per_person_section(monkeypatch):
    monkeypatch.setattr(sonic_health, "fetch_usage_docs", lambda db, days, now: [])
    monkeypatch.setattr(sonic_health, "fetch_turn_docs", lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "summarize_safety_events", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "fetch_recent_events", lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "fetch_counter_docs", lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "summarize_counters", lambda *a, **k: {"totals": {}})
    monkeypatch.setattr(sonic_health, "fetch_corrections", lambda db: [])
    monkeypatch.setattr(sonic_health, "summarize_feedback", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "summarize_graph", lambda db: {})
    monkeypatch.setattr(sonic_health.routines_utils, "routines_view", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "list_observations", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "fetch_route_docs", lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "budget_state", lambda db, now: {})
    monkeypatch.setattr(sonic_health, "budget_view", lambda state, admin: {})
    monkeypatch.setattr(sonic_health.user_usage, "fetch_daily", lambda db, days, now: [_row("jack", "2026-10-10", 250_000)])
    out = sonic_health.build_sonic_health(_PageDB(), 3, NOW)
    assert out["users"]["rows"][0]["user"] == "jack" and out["users"]["rows"][0]["cost"] == 0.25
    assert out["users"]["days"] == DAYS


def test_a_problem_reading_the_per_person_rows_does_not_take_the_page_down(monkeypatch):
    for name in ("fetch_usage_docs", "fetch_turn_docs", "fetch_recent_events", "fetch_counter_docs", "fetch_route_docs"):
        monkeypatch.setattr(sonic_health, name, lambda *a, **k: [])
    monkeypatch.setattr(sonic_health, "summarize_safety_events", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "summarize_counters", lambda *a, **k: {"totals": {}})
    monkeypatch.setattr(sonic_health, "fetch_corrections", lambda db: [])
    monkeypatch.setattr(sonic_health, "summarize_feedback", lambda *a, **k: {})
    monkeypatch.setattr(sonic_health, "summarize_graph", lambda db: {})
    monkeypatch.setattr(sonic_health.routines_utils, "routines_view", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "list_observations", lambda db, now: [])
    monkeypatch.setattr(sonic_health, "budget_state", lambda db, now: {})
    monkeypatch.setattr(sonic_health, "budget_view", lambda state, admin: {})

    def broken(db, days, now):
        raise OSError("db down")

    monkeypatch.setattr(sonic_health.user_usage, "fetch_daily", broken)
    out = sonic_health.build_sonic_health(_PageDB(), 3, NOW)
    assert out["users"] is None and "usage" in out


def test_the_usage_tab_shows_the_per_person_card():
    page = open(os.path.join(REPO, "local", "src", "pages", "SonicHealth.tsx"), encoding="utf-8").read()
    assert "<UsersCard users={health.users} />" in page and "from './SonicHealthUsers'" in page
