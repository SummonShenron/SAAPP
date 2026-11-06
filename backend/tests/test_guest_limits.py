"""Limits on the shared portfolio guest, per visitor (backend/utils/guest_limits.py; docs/guest-mode-plan.md step 2)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import billing_time, budget_guard
from backend.utils import guest_limits as gl

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)
VISITOR = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
ADDRESS = "203.0.113.7"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GUEST_VISITOR_DAILY_TURNS", "GUEST_IP_DAILY_TURNS", "GUEST_DAILY_BUDGET_USD", "GUEST_NUDGE_AT_TURN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gl, "_index_ready", False)


def turn(db, visitor=VISITOR, address=ADDRESS, now=NOW):
    return gl.check_and_count(db, visitor, address, now)


# ---- the numbers are environment settings -------------------------------------------------------------------------------------------------------

def test_the_defaults_are_the_ones_chosen_and_every_one_changes_with_a_setting_and_no_code(monkeypatch):
    assert (gl.visitor_turns(), gl.ip_turns(), gl.daily_budget_usd(), gl.nudge_at()) == (8, 40, 1.0, 5)
    monkeypatch.setenv("GUEST_VISITOR_DAILY_TURNS", "3")
    monkeypatch.setenv("GUEST_IP_DAILY_TURNS", "12")
    monkeypatch.setenv("GUEST_DAILY_BUDGET_USD", "2.5")
    monkeypatch.setenv("GUEST_NUDGE_AT_TURN", "2")
    assert (gl.visitor_turns(), gl.ip_turns(), gl.daily_budget_usd(), gl.nudge_at()) == (3, 12, 2.5, 2)


def test_a_setting_that_is_not_a_number_or_is_negative_falls_back_to_the_default_and_zero_switches_a_limit_off(monkeypatch):
    for bad in ("many", "-4", ""):
        monkeypatch.setenv("GUEST_VISITOR_DAILY_TURNS", bad)
        assert gl.visitor_turns() == 8
    monkeypatch.setenv("GUEST_VISITOR_DAILY_TURNS", "0")
    assert gl.visitor_turns() == 0
    db = FakeDB()
    assert all(turn(db)["allowed"] for _ in range(30))                           # off means off: no cap on a visitor (the address limit is still there behind it)


def test_changing_a_limit_takes_effect_on_the_next_turn_without_a_restart(monkeypatch):
    db = FakeDB()
    assert turn(db)["allowed"] and turn(db)["allowed"]
    monkeypatch.setenv("GUEST_VISITOR_DAILY_TURNS", "2")
    verdict = turn(db)
    assert not verdict["allowed"] and verdict["reason"] == "visitor"
    monkeypatch.setenv("GUEST_VISITOR_DAILY_TURNS", "5")
    assert turn(db)["allowed"]


# ---- the pure parts -----------------------------------------------------------------------------------------------------------------------------

def test_a_visitor_id_is_only_accepted_in_the_shape_a_browser_makes():
    assert gl.clean_visitor_id(VISITOR) == VISITOR
    for bad in (None, "", "short", "x" * 65, "has spaces in it 123456789", "<script>alert(1)</script>"):
        assert gl.clean_visitor_id(bad) is None


def test_the_address_is_the_first_forwarded_one_and_falls_back_to_the_direct_peer():
    assert gl.client_address("198.51.100.4, 10.0.0.1", "10.0.0.9") == "198.51.100.4"
    assert gl.client_address(None, "10.0.0.9") == "10.0.0.9"
    assert gl.client_address("", None) == "unknown"


def test_the_stored_key_depends_on_the_day_and_the_kind_and_never_contains_the_value():
    key = gl.hashed("visitor", VISITOR, "2026-11-05")
    assert key != gl.hashed("visitor", VISITOR, "2026-11-06") and key != gl.hashed("ip", VISITOR, "2026-11-05")
    assert VISITOR not in key and len(key) == 24


def test_the_decision_checks_the_spend_ceiling_first_then_the_visitor_then_the_address_and_a_zero_cap_is_off():
    base = dict(visitor_cap=8, ip_cap=40, budget_usd=1.0)
    assert gl.decide(0, 0, 0.0, **base) == {"allowed": True, "reason": "", "remaining": 8}
    assert gl.decide(8, 0, 0.0, **base)["reason"] == "visitor"
    assert gl.decide(0, 40, 0.0, **base)["reason"] == "ip"
    assert gl.decide(8, 40, 1.0, **base)["reason"] == "budget"
    assert gl.decide(100, 100, 5.0, visitor_cap=0, ip_cap=0, budget_usd=0)["allowed"] is True


# ---- counting per visitor -----------------------------------------------------------------------------------------------------------------------

def test_a_visitor_gets_their_own_allowance_and_one_visitor_using_theirs_up_does_not_block_another(monkeypatch):
    db = FakeDB()
    seen = [turn(db) for _ in range(8)]
    assert all(v["allowed"] for v in seen) and [v["remaining"] for v in seen] == [7, 6, 5, 4, 3, 2, 1, 0]
    ninth = turn(db)
    assert not ninth["allowed"] and ninth["reason"] == "visitor" and "Sign up" in ninth["message"]
    other = turn(db, visitor=OTHER, address="203.0.113.99")
    assert other["allowed"] and other["remaining"] == 7                          # a different visitor on a different address is untouched


def test_the_address_limit_stands_behind_the_visitor_limit_for_a_browser_that_clears_its_storage(monkeypatch):
    monkeypatch.setenv("GUEST_IP_DAILY_TURNS", "5")
    db = FakeDB()
    ids = [f"{n:08d}-2222-3333-4444-555555555555" for n in range(6)]
    results = [turn(db, visitor=i) for i in ids]                                 # six "new" visitors, one address
    assert [r["allowed"] for r in results] == [True] * 5 + [False] and results[-1]["reason"] == "ip"


def test_a_visitor_with_no_usable_id_is_counted_by_address_so_a_script_cannot_get_an_unlimited_allowance():
    db = FakeDB()
    results = [turn(db, visitor=None) for _ in range(9)]
    assert [r["allowed"] for r in results] == [True] * 8 + [False]


def test_all_guests_together_have_a_spend_ceiling_that_blocks_even_a_new_visitor(monkeypatch):
    db = FakeDB()
    day = billing_time.day(NOW)
    db["llm_usage_user_daily"].insert_one({"_id": "a", "user": gl.PORTFOLIO_GUEST, "day": day, "cost_micro": 1_200_000})
    fresh = turn(db, visitor=OTHER, address="198.51.100.20")
    assert not fresh["allowed"] and fresh["reason"] == "budget" and "come back tomorrow" in fresh["message"]
    monkeypatch.setenv("GUEST_DAILY_BUDGET_USD", "5")                             # raising the ceiling is one setting
    assert turn(db, visitor=OTHER, address="198.51.100.20")["allowed"]
    db["llm_usage_user_daily"].insert_one({"_id": "b", "user": "someone@example.com", "day": day, "cost_micro": 9_000_000})
    assert gl.spend_today(db, day) == pytest.approx(1.2)                           # only the guest identities' spend counts


def test_the_allowance_starts_again_each_day():
    db = FakeDB()
    for _ in range(8):
        turn(db)
    assert not turn(db)["allowed"]
    assert turn(db, now=NOW + timedelta(days=1))["allowed"]


def test_what_is_stored_is_only_hashed_counts_and_the_day():
    db = FakeDB()
    turn(db)
    stored = json.dumps(db[gl.COLLECTION].find({}), default=str)
    assert VISITOR not in stored and ADDRESS not in stored
    for doc in db[gl.COLLECTION].find({}):
        assert set(doc) <= {"_id", "turns", "day", "day_dt"}


def test_a_database_failure_lets_the_turn_through_and_so_does_having_none():
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert gl.check_and_count(Broken(), VISITOR, ADDRESS, NOW)["allowed"] is True
    assert gl.check_and_count(None, VISITOR, ADDRESS, NOW)["allowed"] is True
    assert gl.allowance(Broken(), VISITOR, ADDRESS, NOW)["limit"] == 0


def test_the_allowance_for_the_page_reads_without_counting_and_says_where_the_nudge_starts():
    db = FakeDB()
    for _ in range(3):
        turn(db)
    seen = gl.allowance(db, VISITOR, ADDRESS, NOW)
    assert seen == {"used": 3, "limit": 8, "remaining": 5, "nudge_at": 5, "blocked": ""}
    assert gl.allowance(db, VISITOR, ADDRESS, NOW)["used"] == 3                   # looking does not use a turn


# ---- wired in -----------------------------------------------------------------------------------------------------------------------------------

def test_only_the_portfolio_guest_is_limited_and_it_has_no_shared_per_person_budget_any_more():
    assert gl.is_portfolio_guest("guest-recruiter@example.com") and gl.is_portfolio_guest(" Guest-Recruiter@example.com ")
    assert not gl.is_portfolio_guest("guest_bty") and not gl.is_portfolio_guest("jack@example.com") and not gl.is_portfolio_guest(None)
    assert budget_guard.limit_username("guest-recruiter@example.com", False) is None
    assert budget_guard.limit_username("jack@example.com", False) == "jack@example.com"


def test_the_chat_endpoint_counts_the_guest_before_doing_any_work_and_the_page_can_read_the_allowance():
    source = open("app.py", encoding="utf-8").read()
    head = source[source.index("async def secure_chat("):]
    assert head.index("guest_limits.check_and_count") < head.index("get_stream_llm(username)")
    assert '"code": "guest_limit"' in source and 'status_code=429' in source
    assert '@app.get("/api/guest/allowance")' in source
