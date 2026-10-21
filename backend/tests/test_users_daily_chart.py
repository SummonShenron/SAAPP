"""Who used each day and what each cost against the day's whole total: the graph on the Sonic Health dashboard and the full table on the Usage tab
(backend/utils/user_usage.py day_breakdowns)."""
import os

from backend.tests.test_user_cost_summary import DAYS, NOW, _row, sonic_health
from backend.utils import user_usage as uu

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _daily(totals, evals=None):
    """The usage page's own per-day rows: the day's total estimate and the part of it that came from eval runs."""
    evals = evals or [0.0] * len(DAYS)
    return [{"day": day, "cost": total, "eval_cost": eval_cost, "eval_calls": 1 if eval_cost else 0, "eval_tokens": 10 if eval_cost else 0}
            for day, total, eval_cost in zip(DAYS, totals, evals)]


def _breakdown(rows, totals, viewer="jack", evals=None):
    return uu.day_breakdowns(rows, _daily(totals, evals), DAYS, viewer)


def _day(out, day="2026-10-10"):
    return next(d for d in out["by_day"] if d["day"] == day)


def test_a_day_lists_everyone_who_used_it_biggest_first_with_a_share_of_the_whole_day():
    rows = [_row("jack", "2026-10-10", 570_000, turns=5), _row("sam", "2026-10-10", 40_000, turns=2), _row("sam", "2026-10-09", 100_000, turns=3)]
    out = _breakdown(rows, [0.0, 0.1, 0.64])
    today = _day(out)
    assert [p["user"] for p in today["people"]] == ["jack", "sam"]
    jack, sam = today["people"]
    assert jack["you"] is True and sam["you"] is False
    assert jack["cost"] == 0.57 and jack["turns"] == 5 and jack["share"] == round(0.57 / 0.64, 4)
    assert sam["cost"] == 0.04 and sam["share"] == round(0.04 / 0.64, 4)
    assert today["total"] == 0.64 and out["viewer"] == "jack" and out["days"] == DAYS
    assert [p["user"] for p in _day(out, "2026-10-09")["people"]] == ["sam"]       # each day lists only the people who used THAT day


def test_what_no_account_owns_is_the_remainder_of_the_days_total():
    rows = [_row("jack", "2026-10-10", 570_000, turns=5), _row("sam", "2026-10-10", 40_000, turns=2)]
    today = _day(_breakdown(rows, [0.0, 0.0, 0.64]))
    assert today["background"] == {"cost": 0.03, "share": round(0.03 / 0.64, 4)}
    assert round(sum(p["cost"] for p in today["people"]) + today["background"]["cost"], 4) == today["total"]


def test_the_evals_are_their_own_entry_and_are_not_a_person():
    today = _day(_breakdown([_row("jack", "2026-10-10", 500_000, turns=4)], [0.0, 0.0, 0.8], evals=[0.0, 0.0, 0.2]))
    assert today["evals"]["cost"] == 0.2 and today["evals"]["calls"] == 1 and [p["user"] for p in today["people"]] == ["jack"]
    assert today["background"]["cost"] == 0.1                       # 0.8 - 0.5 - 0.2


def test_background_is_never_negative_when_the_parts_exceed_the_total():
    today = _day(_breakdown([_row("jack", "2026-10-10", 900_000, turns=1)], [0.0, 0.0, 0.5]))      # a price change or a late write can do this
    assert today["background"] is None


def test_a_day_nobody_used_is_empty_but_present():
    out = _breakdown([_row("jack", "2026-10-10", 500_000, turns=4)], [0.0, 0.0, 0.5])
    quiet = _day(out, "2026-10-08")
    assert quiet["people"] == [] and quiet["total"] == 0.0 and quiet["evals"] is None and quiet["background"] is None


def test_the_viewer_is_marked_only_when_they_used_that_day_and_an_unknown_viewer_marks_no_one():
    rows = [_row("jack", "2026-10-10", 500_000, turns=4), _row("sam", "2026-10-10", 100_000, turns=1)]
    assert [p["you"] for p in _day(_breakdown(rows, [0.0, 0.0, 0.6], viewer="nobody"))["people"]] == [False, False]
    assert [p["you"] for p in _day(_breakdown(rows, [0.0, 0.0, 0.6], viewer=None))["people"]] == [False, False]


def test_a_very_busy_day_lists_a_bounded_number_and_says_how_many_more():
    rows = [_row(f"u{n:03d}", "2026-10-10", 1_000 + n, turns=1) for n in range(uu.MAX_PEOPLE_PER_DAY + 7)]
    today = _day(_breakdown(rows, [0.0, 0.0, 1.0]))
    assert len(today["people"]) == uu.MAX_PEOPLE_PER_DAY and today["more_people"] == 7
    assert today["people"][0]["cost"] >= today["people"][-1]["cost"]


def test_nothing_priced_means_no_breakdown():
    assert uu.day_breakdowns([_row("jack", "2026-10-10", 0, tokens=50, turns=1)], _daily([0.0, 0.0, 0.0]), DAYS, "jack") is None
    assert uu.day_breakdowns([], [], DAYS, "jack") is None


def test_the_breakdown_holds_account_names_and_counts_and_nothing_else():
    today = _day(_breakdown([_row("jack", "2026-10-10", 500_000, turns=4)], [0.0, 0.0, 0.5]))
    assert set(today) == {"day", "total", "people", "more_people", "evals", "background"}
    assert set(today["people"][0]) == {"user", "you", "cost", "tokens", "calls", "turns", "share"}


def test_the_health_payload_carries_the_breakdown_and_the_endpoint_names_the_viewer(monkeypatch):
    seen = {}
    monkeypatch.setattr(sonic_health.user_usage, "fetch_daily", lambda db, days, now: [])
    monkeypatch.setattr(sonic_health.user_usage, "day_breakdowns", lambda rows, daily, period, viewer=None: seen.update(viewer=viewer) or {"marker": True})
    out = sonic_health._users_section(object(), 3, NOW, {"estimated_cost": 0.0}, [], "jack")
    assert out["daily_people"] == {"marker": True} and seen["viewer"] == "jack"
    app_source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert 'build_sonic_health, _admin_db(), days, None, admin.get("sub")' in app_source
