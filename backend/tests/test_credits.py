"""The paid tier's prepaid credit, built switched off (backend/utils/credits.py, backend/services/payments.py; docs/paid-tier-plan.md). With BILLING_ENABLED off nothing here does anything."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.services import payments
from backend.tests.fake_runner_db import FakeDB
from backend.utils import billing_time, budget_guard, credits, llm_turns, llm_usage

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
DOLLAR = credits.MICRO


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("BILLING_ENABLED", "BILLING_RATE", "BILLING_MIN_TOPUP_USD", "BILLING_DAILY_CEILING_USD", "BILLING_NEW_ACCOUNT_CEILING_USD", "BILLING_NEW_ACCOUNT_DAYS", "BILLING_GLOBAL_PAID_CEILING_USD", "BILLING_FEE_PERCENT", "BILLING_FEE_FIXED_USD"):
        monkeypatch.delenv(name, raising=False)
    from backend.utils import user_usage

    def wipe():
        credits.reset()
        payments.set_provider(None)
        user_usage.reset()          # finishing a test turn adds that person's usage in memory: it must not be flushed by a later test
        llm_usage.reset()
        llm_turns._current.set(None)

    wipe()
    yield
    wipe()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")


def funded(db, user="ana", dollars=10, at=NOW - timedelta(days=30)):
    credits.apply_topup(db, user, int(dollars * DOLLAR), f"pay-{user}-{dollars}", at)


# ---- off means off ---------------------------------------------------------------------------------------------------------------------------

def test_billing_is_off_by_default_and_nothing_is_charged_or_paid_while_it_is():
    db = FakeDB()
    funded(db)                                                                    # a balance exists in the ledger, but billing is off
    assert credits.enabled() is False
    assert credits.paid_status(db, "ana", NOW) is None
    assert credits.queue_debit("ana", "turn-1", 500_000) is False and credits._pending == []
    assert credits.topups_paused(db, NOW) is False


def test_every_way_of_saying_yes_turns_it_on_and_anything_else_does_not(monkeypatch):
    for value, expected in (("true", True), ("1", True), ("YES", True), ("on", True), ("", False), ("false", False), ("0", False), ("maybe", False)):
        monkeypatch.setenv("BILLING_ENABLED", value)
        assert credits.enabled() is expected, value


# ---- the ledger -------------------------------------------------------------------------------------------------------------------------------

def test_a_row_is_signed_by_its_kind_and_the_same_event_twice_counts_once(on):
    db = FakeDB()
    assert credits.record(db, "ana", "topup", 5 * DOLLAR, "pay-1", NOW) is True
    assert credits.record(db, "ana", "topup", 5 * DOLLAR, "pay-1", NOW) is False          # a repeated webhook
    assert credits.record(db, "ana", "spend", 2 * DOLLAR, "turn-1", NOW) is True
    assert credits.record(db, "ana", "refund", 1 * DOLLAR, "ref-1", NOW) is True
    assert credits.record(db, "ana", "adjust", -DOLLAR // 2, "adj-1", NOW) is True
    assert credits.balance_micro(db, "ana") == 5 * DOLLAR - 2 * DOLLAR - 1 * DOLLAR - DOLLAR // 2
    assert credits.record(db, "ana", "topup", 5 * DOLLAR, "turn-1", NOW) is True           # the same ref under another kind is another event


def test_a_row_that_is_not_valid_is_refused_and_a_valid_one_holds_no_text(on):
    db = FakeDB()
    for args in (("", "topup", 5, "r"), ("ana", "gift", 5, "r"), ("ana", "topup", 0, "r"), ("ana", "topup", 5, ""), ("ana", "topup", True, "r"), ("ana", "topup", 1.5, "r"), (None, "topup", 5, "r")):
        assert credits.record(db, *args) is False, args
    credits.record(db, "ana", "topup", 5 * DOLLAR, "pay-1", NOW)
    row = db[credits.COLLECTION].find_one({"user": "ana"})
    assert set(row) == {"_id", "user", "kind", "micro", "ref", "at", "day"}              # amounts, a kind, a reference and times: no text of any kind
    assert row["_id"] == "topup:pay-1" and row["day"] == billing_time.day(NOW)


def test_a_duplicate_key_from_two_writers_at_once_is_one_credit(on):
    class Racing(FakeDB):
        def __getitem__(self, name):
            collection = super().__getitem__(name)
            original = collection.insert_one

            def insert(doc):
                original(doc)
                raise type("DuplicateKeyError", (Exception,), {})()
            collection.insert_one = insert
            return collection

    assert credits.record(Racing(), "ana", "topup", DOLLAR, "pay-1", NOW) is False


def test_queued_spends_count_against_the_balance_at_once_and_are_saved_by_flush_without_counting_twice(on):
    db = FakeDB()
    funded(db, dollars=10)
    assert credits.queue_debit("ana", "turn-1", 300_000, NOW) is True
    assert credits.balance_micro(db, "ana") == 10 * DOLLAR - 300_000                   # visible before it is saved
    assert credits.flush(db) == 1 and credits._pending == []
    assert credits.balance_micro(db, "ana") == 10 * DOLLAR - 300_000                   # and not subtracted twice once it is
    credits.queue_debit("ana", "turn-1", 300_000, NOW)                                  # the same turn flushed again
    assert credits.flush(db) == 0 and credits.balance_micro(db, "ana") == 10 * DOLLAR - 300_000


def test_a_flush_that_cannot_save_keeps_the_spends_for_the_next_one(on):
    class Down:
        def __getitem__(self, name):
            raise RuntimeError("database down")

    credits.queue_debit("ana", "turn-1", 100_000, NOW)
    assert credits.flush(Down()) == 0 and len(credits._pending) == 1
    db = FakeDB()
    assert credits.flush(db) == 1 and credits._pending == []


def test_the_charge_is_the_metered_cost_times_the_rate_and_a_free_turn_is_not_queued(on, monkeypatch):
    assert credits.queue_debit("ana", "t1", 1_000_000, NOW) is True and credits._pending[0]["micro"] == 1_000_000          # one to one
    monkeypatch.setenv("BILLING_RATE", "1.25")
    credits.queue_debit("ana", "t2", 1_000_000, NOW)
    assert credits._pending[1]["micro"] == 1_250_000
    for rate in ("0", "-2", "lots"):
        monkeypatch.setenv("BILLING_RATE", rate)
        assert credits.rate() == 1.0, rate
    assert credits.queue_debit("ana", "t3", 0, NOW) is False and credits.queue_debit(None, "t4", 5, NOW) is False and len(credits._pending) == 2


# ---- who is paid, and the low ceilings --------------------------------------------------------------------------------------------------------

def test_a_person_is_paid_only_while_billing_is_on_and_a_balance_remains(on):
    db = FakeDB()
    assert credits.paid_status(db, "ana", NOW) is None                                 # no balance
    funded(db, dollars=1)
    status = credits.paid_status(db, "ana", NOW)
    assert status["balance_micro"] == DOLLAR and status["at_ceiling"] is False
    credits.record(db, "ana", "spend", DOLLAR, "turn-1", NOW)
    assert credits.paid_status(db, "ana", NOW) is None                                 # back under the free limits at zero


def test_a_new_account_has_the_lower_ceiling_and_it_lifts_after_the_new_account_days_and_stays_low(on):
    assert credits.ceiling_micro({}, NOW) == 2 * DOLLAR                                  # no top-up on record counts as new
    assert credits.ceiling_micro({"first_topup_at": NOW - timedelta(days=3)}, NOW) == 2 * DOLLAR
    assert credits.ceiling_micro({"first_topup_at": NOW - timedelta(days=8)}, NOW) == 5 * DOLLAR          # the ordinary ceiling is low by design
    assert credits.ceiling_micro({"first_topup_at": NOW - timedelta(days=400)}, NOW) == 5 * DOLLAR        # age never lifts it past that


def test_the_ceilings_come_from_the_environment_and_a_nonsense_value_uses_the_default(on, monkeypatch):
    monkeypatch.setenv("BILLING_DAILY_CEILING_USD", "8")
    monkeypatch.setenv("BILLING_NEW_ACCOUNT_CEILING_USD", "1.5")
    monkeypatch.setenv("BILLING_NEW_ACCOUNT_DAYS", "3")
    assert credits.ceiling_micro({"first_topup_at": NOW - timedelta(days=2)}, NOW) == 1_500_000
    assert credits.ceiling_micro({"first_topup_at": NOW - timedelta(days=4)}, NOW) == 8 * DOLLAR
    for bad in ("", "lots", "-4"):
        monkeypatch.setenv("BILLING_DAILY_CEILING_USD", bad)
        assert credits.daily_ceiling_micro() == 5 * DOLLAR, bad


def test_nothing_but_an_admins_approval_raises_a_ceiling_and_it_raises_that_person_only(on):
    db = FakeDB()
    funded(db, "ana")
    funded(db, "ben")
    assert credits.admin_set_ceiling(db, "jack", "ana", 12, NOW) == (True, "Saved.")
    assert credits.ceiling_micro(credits.get_account(db, "ana"), NOW) == 12 * DOLLAR
    assert credits.ceiling_micro(credits.get_account(db, "ben"), NOW) == 5 * DOLLAR
    account = credits.get_account(db, "ana")
    assert account["ceiling_override_by"] == "jack" and account["ceiling_override_at"] == NOW         # saved with who and when
    assert credits.admin_set_ceiling(db, "jack", "ana", 0)[0] is False and credits.admin_set_ceiling(db, "jack", "ana", "lots")[0] is False
    credits.admin_set_ceiling(db, "jack", "ana", None, NOW)
    assert credits.ceiling_micro(credits.get_account(db, "ana"), NOW) == 5 * DOLLAR


def test_a_person_may_set_their_own_ceiling_lower_but_never_higher_than_the_servers(on):
    db = FakeDB()
    funded(db, "ana")
    assert credits.set_own_ceiling(db, "ana", 3, NOW)[:1] == (True,) and credits.ceiling_micro(credits.get_account(db, "ana"), NOW) == 3 * DOLLAR
    ok, message, in_force = credits.set_own_ceiling(db, "ana", 9, NOW)
    assert ok is False and "can't be set above $5.00" in message and in_force == 3 * DOLLAR           # refused; what was in force stays
    assert credits.set_own_ceiling(db, "ana", 0, NOW)[0] is False and credits.set_own_ceiling(db, "ana", "x", NOW)[0] is False
    assert credits.set_own_ceiling(db, "ana", None, NOW)[0] is True and credits.ceiling_micro(credits.get_account(db, "ana"), NOW) == 5 * DOLLAR
    credits.admin_set_ceiling(db, "jack", "ana", 12, NOW)
    assert credits.set_own_ceiling(db, "ana", 12, NOW)[0] is True                                     # up to what an admin allowed them


def test_a_person_at_their_ceiling_is_flagged_with_the_balance_untouched_and_spend_is_counted_by_billing_day(on):
    db = FakeDB()
    funded(db, "ana", dollars=20)
    credits.record(db, "ana", "spend", 2 * DOLLAR, "yesterday", NOW - timedelta(days=1))
    assert credits.spent_today_micro(db, "ana", NOW) == 0                                              # yesterday does not count
    credits.record(db, "ana", "spend", 5 * DOLLAR, "t1", NOW)
    status = credits.paid_status(db, "ana", NOW)
    assert status["at_ceiling"] is True and status["spent_today_micro"] == 5 * DOLLAR and status["balance_micro"] == 13 * DOLLAR


def test_new_top_ups_pause_when_all_paid_spend_today_reaches_the_global_ceiling_and_people_keep_their_balance(on, monkeypatch):
    monkeypatch.setenv("BILLING_GLOBAL_PAID_CEILING_USD", "6")
    db = FakeDB()
    funded(db, "ana", dollars=20)
    funded(db, "ben", dollars=20)
    credits.record(db, "ana", "spend", 3 * DOLLAR, "t1", NOW)
    assert credits.topups_paused(db, NOW) is False
    credits.record(db, "ben", "spend", 3 * DOLLAR, "t2", NOW)
    assert credits.topups_paused(db, NOW) is True and credits.balance_micro(db, "ben") == 17 * DOLLAR


def test_what_the_person_is_told_is_their_own_and_in_dollars(on):
    db = FakeDB()
    funded(db, "ana", dollars=10)
    funded(db, "ben", dollars=99)
    credits.record(db, "ana", "spend", DOLLAR // 2, "t1", NOW)
    view = credits.public_view(db, "ana", NOW, provider_ready=True)
    assert view["balance_usd"] == 9.5 and view["paid"] is True and view["daily_ceiling_usd"] == 5.0 and view["spent_today_usd"] == 0.5
    assert view["can_top_up"] is True and credits.public_view(db, "ana", NOW)["can_top_up"] is False        # no provider yet
    assert {row["kind"] for row in view["recent"]} == {"topup", "spend"} and "99" not in json.dumps(view) and "ben" not in json.dumps(view)       # nothing of anyone else's


# ---- the payment side ---------------------------------------------------------------------------------------------------------------------------

def _event(user="ana", dollars=10, pid="evt-1", kind=payments.COMPLETED, credit=None):
    body = {"id": pid, "type": kind, "user": user, "amount_micro": int(dollars * DOLLAR)}
    if credit is not None:
        body["credit_micro"] = int(credit * DOLLAR)
    return json.dumps(body).encode()


def test_the_environment_can_never_select_a_provider_so_the_fake_cannot_be_used_in_a_deployment(monkeypatch):
    for value in ("fake", "stripe", "anything"):
        monkeypatch.setenv("BILLING_PROVIDER", value)
        assert payments.get_provider() is None
    assert payments.start_checkout(FakeDB(), "ana", 10, "", "")[0] == 503 and payments.accept_webhook(FakeDB(), b"{}", "sig")[0] == 503


def test_a_checkout_needs_a_number_at_least_the_minimum_and_top_ups_that_are_not_paused(on):
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    assert payments.start_checkout(db, "ana", "lots", "", "")[0] == 400
    code, body = payments.start_checkout(db, "ana", 2, "", "")
    assert code == 400 and "$5.00" in body["detail"]
    code, body = payments.start_checkout(db, "ana", 10, "", "")
    assert code == 200 and body["url"].startswith("https://pay.invalid/")
    assert body["credit_usd"] == 10.0 and body["fee_usd"] == 0.61 and body["total_usd"] == 10.61          # the processing line is shown, not hidden in the price
    assert provider.checkouts == [{"user": "ana", "amount_micro": 10_610_000, "credit_micro": 10 * DOLLAR}]


def test_top_ups_are_refused_while_the_global_paid_ceiling_is_reached(on, monkeypatch):
    monkeypatch.setenv("BILLING_GLOBAL_PAID_CEILING_USD", "1")
    payments.set_provider(payments.FakeProvider())
    db = FakeDB()
    funded(db, "ana", dollars=20)
    credits.record(db, "ana", "spend", 2 * DOLLAR, "t1", datetime.now(timezone.utc))
    code, body = payments.start_checkout(db, "ben", 10, "", "")
    assert code == 409 and "paused" in body["detail"]


def test_a_webhook_with_a_bad_or_missing_signature_credits_nothing(on):
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    body = _event()
    assert payments.accept_webhook(db, body, None)[0] == 400 and payments.accept_webhook(db, body, "0" * 64)[0] == 400
    assert payments.accept_webhook(db, body, FakeProviderOther().sign(body))[0] == 400            # signed with another secret
    assert credits.balance_micro(db, "ana") == 0


class FakeProviderOther(payments.FakeProvider):
    def __init__(self):
        super().__init__("another-secret")


def test_a_completed_payment_credits_once_however_many_times_the_webhook_arrives(on):
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    body = _event(dollars=10.61, credit=10)
    assert payments.accept_webhook(db, body, provider.sign(body)) == (200, {"status": "credited"})
    assert payments.accept_webhook(db, body, provider.sign(body)) == (200, {"status": "duplicate"})
    assert credits.balance_micro(db, "ana") == 10 * DOLLAR
    assert "first_topup_at" in credits.get_account(db, "ana")                                       # their new-account period has started


def test_only_a_completed_payment_of_a_positive_amount_for_a_named_person_credits(on):
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    for body in (_event(kind="invoice.created"), _event(dollars=0), _event(dollars=-5), _event(user=""), _event(pid=""), b"not json"):
        code, result = payments.accept_webhook(db, body, provider.sign(body))
        assert code == 200 and result == {"status": "ignored"}, body
    assert credits.balance_micro(db, "ana") == 0


# ---- the processing line, refunds and disputes (decided 2026-11-05) ----------------------------------------------------------------------------------

def test_a_top_up_costs_the_credit_plus_a_processing_line_that_covers_the_fee_and_rounds_up_to_a_cent():
    for dollars, fee in ((5, 0.46), (10, 0.61), (25, 1.06)):
        priced = credits.quote(dollars * DOLLAR)
        assert priced["credit_micro"] == dollars * DOLLAR and priced["fee_micro"] == int(round(fee * DOLLAR)) and priced["total_micro"] == priced["credit_micro"] + priced["fee_micro"]
        assert priced["total_micro"] % 10_000 == 0                                                       # a whole cent
        processor_takes = 0.029 * priced["total_micro"] + 300_000
        assert priced["total_micro"] - processor_takes >= dollars * DOLLAR - 1                           # the credit is paid for, so one to one costs Jack nothing
    assert credits.quote(0) == {"credit_micro": 0, "fee_micro": 0, "total_micro": 0}


def test_the_fee_comes_from_the_environment_and_a_nonsense_value_uses_the_default(monkeypatch):
    monkeypatch.setenv("BILLING_FEE_PERCENT", "0")
    monkeypatch.setenv("BILLING_FEE_FIXED_USD", "0")
    assert credits.quote(5 * DOLLAR)["fee_micro"] == 0
    monkeypatch.setenv("BILLING_FEE_PERCENT", "10")
    assert credits.quote(9 * DOLLAR)["total_micro"] == 10 * DOLLAR                                       # 9 / 0.9
    for bad in ("", "lots", "-5"):
        monkeypatch.setenv("BILLING_FEE_PERCENT", bad)
        assert credits.fee_percent() in (2.9, 0.0), bad
    monkeypatch.setenv("BILLING_FEE_PERCENT", "99")
    assert credits.fee_percent() == 20.0                                                                 # never absurd


def test_a_payment_whose_event_names_no_credit_is_credited_less_the_processing_line_and_never_more_than_was_paid(on):
    assert credits.credit_from_paid(credits.quote(10 * DOLLAR)["total_micro"]) == 10 * DOLLAR
    assert credits.credit_from_paid(200_000) == 0 and credits.credit_from_paid(0) == 0 and credits.credit_from_paid(-5) == 0
    assert payments.credit_for_payment(10_610_000) == 10 * DOLLAR
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    body = _event(dollars=10.61)                                                                          # no credit named
    assert payments.accept_webhook(db, body, provider.sign(body)) == (200, {"status": "credited"}) and credits.balance_micro(db, "ana") == 10 * DOLLAR
    greedy = _event(user="ben", dollars=5, pid="evt-2", credit=50)                                         # a credit larger than the payment is not believed
    payments.accept_webhook(db, greedy, provider.sign(greedy))
    assert credits.balance_micro(db, "ben") == credits.credit_from_paid(5 * DOLLAR) <= 5 * DOLLAR


def test_the_page_can_ask_what_a_top_up_would_cost_before_anyone_pays(on):
    assert payments.quote_view(10) == (200, {"credit_usd": 10.0, "fee_usd": 0.61, "total_usd": 10.61})
    assert payments.quote_view(2)[0] == 400 and payments.quote_view("lots")[0] == 400


def test_a_disputed_payment_freezes_the_account_zeroes_what_is_left_and_counts_once(on):
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    db = FakeDB()
    funded(db, "ana", dollars=20)
    credits.record(db, "ana", "spend", 3 * DOLLAR, "t1", datetime.now(timezone.utc))
    body = _event(kind=payments.DISPUTED, pid="dp-1", dollars=20)
    assert payments.accept_webhook(db, body, provider.sign(body)) == (200, {"status": "frozen"})
    assert credits.is_frozen(db, "ana") and credits.balance_micro(db, "ana") == 0                         # what was left is no longer spendable
    assert credits.paid_status(db, "ana", datetime.now(timezone.utc)) is None                              # no paid bypass either
    assert payments.accept_webhook(db, body, provider.sign(body)) == (200, {"status": "duplicate"})
    assert len(list(db[credits.COLLECTION].find({"kind": "adjust"}))) == 1                                # one adjustment however often the event arrives
    assert payments.start_checkout(db, "ana", 10, "", "")[0] == 403                                       # and no new top-ups
    assert credits.paid_status(FakeDB(), "ben", NOW) is None                                              # nobody else is touched


def test_an_admin_can_lift_a_freeze_and_it_is_recorded_who_and_when(on):
    db = FakeDB()
    funded(db, "ana", dollars=5)
    credits.freeze(db, "ana", "dp-1", NOW)
    assert credits.unfreeze(db, "jack", "ana", NOW) is True and credits.is_frozen(db, "ana") is False
    account = credits.get_account(db, "ana")
    assert account["unfrozen_by"] == "jack" and account["unfrozen_at"] == NOW
    assert credits.unfreeze(db, "jack", "ana", NOW) is False                                              # nothing to lift
    credits.apply_topup(db, "ana", 5 * DOLLAR, "pay-again", NOW)
    assert credits.paid_status(db, "ana", NOW) is not None                                               # paid again once lifted and funded


def test_a_refund_returns_only_unused_credit_and_never_more_than_the_balance_and_not_twice(on):
    db = FakeDB()
    funded(db, "ana", dollars=10)
    credits.record(db, "ana", "spend", 4 * DOLLAR, "t1", NOW)
    ok, message, balance = credits.refund(db, "jack", "ana", 7, "rf-1", NOW)
    assert ok is False and "more than their unused balance ($6.00)" in message and balance == 6 * DOLLAR
    ok, message, balance = credits.refund(db, "jack", "ana", 2.5, "rf-1", NOW)
    assert ok is True and balance == 3_500_000 and credits.balance_micro(db, "ana") == 3_500_000
    assert credits.refund(db, "jack", "ana", 1, "rf-1", NOW)[0] is False                                  # the same refund id twice
    for bad in (0, -1, "lots"):
        assert credits.refund(db, "jack", "ana", bad, None, NOW)[0] is False, bad
    assert "dashboard" in message


def test_the_billing_view_shows_the_fee_rule_and_whether_the_account_is_frozen(on):
    db = FakeDB()
    funded(db, "ana")
    view = credits.public_view(db, "ana", NOW)
    assert view["fee_percent"] == 2.9 and view["fee_fixed_usd"] == 0.3 and view["frozen"] is False
    credits.freeze(db, "ana", "dp-1", NOW)
    assert credits.public_view(db, "ana", NOW)["frozen"] is True


def test_the_quote_and_the_refund_and_unfreeze_routes_exist_and_stay_off_with_billing_off():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    for route in ('@app.get("/api/billing/quote"', '@app.post("/api/admin/billing/refund"', '@app.post("/api/admin/billing/unfreeze"'):
        assert route in source, route
    block = source[source.index('@app.post("/api/admin/billing/refund"'):source.index("class LocalGitBundleRequest")]
    assert block.count("if not credits.enabled():") == 2 and block.count("Depends(require_global_admin)") == 2
    assert "payments.quote_view(amount_usd)" in source and "payments.start_checkout, get_db(), username, payload.amount_usd" in source


# ---- the guard and the usage meter -------------------------------------------------------------------------------------------------------------

def test_only_the_chat_turn_gives_a_paid_person_full_power_and_only_while_billing_is_on_and_they_are_funded(on):
    db = FakeDB()
    funded(db, "ana", dollars=10)
    paid = budget_guard.current_state(db, NOW, "ana", paid_bypass=True)
    assert paid["paid"] is True and paid["level"] == budget_guard.OK
    assert "paid" not in budget_guard.current_state(db, NOW, "ana")                                      # a scheduled task is judged on the free limits
    assert "paid" not in budget_guard.current_state(db, NOW, "ben", paid_bypass=True)                    # no balance
    assert "paid" not in budget_guard.current_state(db, NOW, None, paid_bypass=True)


def test_with_billing_off_the_guard_is_exactly_what_it_was_even_for_someone_with_a_balance():
    db = FakeDB()
    funded(db, "ana", dollars=10)
    assert "paid" not in budget_guard.current_state(db, NOW, "ana", paid_bypass=True)


def test_a_paid_person_at_their_ceiling_is_paused_with_their_balance_untouched_and_told_so(on):
    db = FakeDB()
    funded(db, "ana", dollars=20)
    credits.record(db, "ana", "spend", 5 * DOLLAR, "t1", NOW)
    state = budget_guard.current_state(db, NOW, "ana", paid_bypass=True)
    assert state["level"] == budget_guard.BLOCKED and state["scope"] == "paid_day" and state["paid"] is True
    reply = budget_guard.blocked_reply(state["resets_at"], False, state["scope"])
    assert "daily limit for paid use" in reply and "balance is untouched" in reply and budget_guard.PAID_OPTION_NOTE not in reply
    view = budget_guard.public_view(state, False)
    assert view["own_limit"] is True and "balance is untouched" in view["message"] and "upgrade_note" not in view     # they are paying: no "paid option is planned" line


def test_a_paid_turns_calls_are_counted_under_a_paid_label_and_kept_out_of_the_shared_budget(on):
    llm_usage.reset()
    turn = llm_turns.begin("ana")
    llm_turns.note_paid()
    llm_usage.record("gemini-x", "agent.tool", {"input": 1000, "output": 100, "reasoning": 0, "cached": 0}, day=billing_time.day(NOW))
    llm_turns._current.set(None)
    assert turn.paid is True
    assert [key[2] for key in llm_usage.snapshot()] == ["paid:agent.tool"]
    assert llm_usage.minute_snapshot() == {}                                                              # the shared five-hour window is not moved
    rows = [{"day": billing_time.day(NOW), "model": "gemini-x", "label": "paid:agent.tool", "input": 10**9, "output": 0, "calls": 1, "reasoning": 0, "cached": 0}]
    totals = budget_guard.spent(rows, NOW, {"gemini-x": {"input": 1.0, "output": 1.0}})
    assert totals["day_tokens"] == 0 and totals["day_usd"] == 0 and totals["month_tokens"] == 0
    llm_usage.reset()


def test_an_ordinary_turns_calls_are_not_relabelled_and_still_count(on):
    llm_usage.reset()
    llm_turns.begin("ana")
    llm_usage.record("gemini-x", "agent.tool", {"input": 1000, "output": 100, "reasoning": 0, "cached": 0}, day=billing_time.day(NOW))
    llm_turns._current.set(None)
    assert [key[2] for key in llm_usage.snapshot()] == ["agent.tool"] and llm_usage.minute_snapshot() != {}
    llm_usage.reset()


def test_a_finished_paid_turn_queues_its_charge_and_an_ordinary_or_blocked_one_does_not(on):
    def finished(paid, outcome="ok", cost=250_000):
        turn = llm_turns.begin("ana")
        turn.cost_micro, turn.input = cost, 1000
        if paid:
            llm_turns.note_paid()
        llm_turns.finish(turn, outcome)
        llm_turns._current.set(None)
        return turn

    paid_turn = finished(True)
    assert [(item["user"], item["ref"], item["micro"]) for item in credits._pending] == [("ana", paid_turn.id, 250_000)]
    credits.reset()
    finished(False)
    finished(True, "blocked")
    assert credits._pending == []


def test_the_usage_flush_saves_the_queued_charges_too():
    source = (ROOT / "backend" / "utils" / "llm_usage.py").read_text(encoding="utf-8")
    assert "credits.flush(db)" in source


# ---- the endpoints and the chat turn -------------------------------------------------------------------------------------------------------------

def test_the_endpoints_exist_answer_404_with_billing_off_and_the_chat_turn_marks_a_paid_turn():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    for route in ('@app.get("/api/billing/me"', '@app.post("/api/billing/checkout"', '@app.post("/api/billing/webhook"', '@app.put("/api/billing/ceiling"', '@app.put("/api/admin/billing/ceiling"'):
        assert route in source, route
    block = source[source.index("def _billing_user(current_user)"):source.index("class LocalGitBundleRequest")]
    assert block.count("if not credits.enabled():") == 5 and 'detail="Not found."' in block                 # _billing_user, the webhook, and the three admin routes
    assert "is_guest_username(username)" in block and "Depends(require_global_admin)" in block
    assert "budget_guard.current_state, None, None, budget_guard.limit_username(username, is_admin_user), True)" in source
    assert 'if budget_state.get("paid") and budget_state.get("level") != budget_guard.BLOCKED:' in source and "llm_turns.note_paid()" in source


def test_with_billing_off_the_chat_turn_never_even_reads_a_balance(monkeypatch):
    class Boom:
        def __getitem__(self, name):
            raise AssertionError("the database was read with billing off")

    monkeypatch.setattr(budget_guard, "_paid_state", lambda *args: (_ for _ in ()).throw(AssertionError("_paid_state ran with billing off")))
    assert "paid" not in budget_guard.current_state(Boom(), NOW, "ana", paid_bypass=True)


def test_a_payment_that_completes_after_a_freeze_does_not_make_the_frozen_account_paid_again(on):
    """The freeze zeroes the balance, so only a LATER credit (a checkout begun before the dispute) can show whether the frozen flag itself is checked."""
    db = FakeDB()
    funded(db, "ana", dollars=10)
    credits.freeze(db, "ana", "dp-1", NOW)
    credits.apply_topup(db, "ana", 5 * DOLLAR, "late-payment", NOW)
    assert credits.balance_micro(db, "ana") == 5 * DOLLAR and credits.paid_status(db, "ana", NOW) is None
    assert "paid" not in budget_guard.current_state(db, NOW, "ana", paid_bypass=True)
