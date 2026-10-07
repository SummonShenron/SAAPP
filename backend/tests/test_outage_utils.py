import logging

import pytest

from backend.utils import outage_utils as ou
from backend.utils.safety_utils import has_crisis_resource


class _ResourceExhausted(Exception):
    pass


@pytest.mark.parametrize("exc", [
    RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota"),
    _ResourceExhausted("Quota exceeded for metric generate_content_requests"),
    RuntimeError("403 billing account disabled"),
    RuntimeError("rate limit reached"),
])
def test_spent_quota_or_cap_is_classified_as_quota(exc):
    assert ou.classify_llm_failure(exc) == ou.KIND_QUOTA


@pytest.mark.parametrize("exc", [
    RuntimeError("503 UNAVAILABLE"),
    RuntimeError("504 Gateway Timeout"),
    TimeoutError(),
    ConnectionError("reset"),
])
def test_overload_and_timeouts_are_classified_as_overloaded(exc):
    assert ou.classify_llm_failure(exc) == ou.KIND_OVERLOADED


def test_anything_else_is_other_and_a_number_that_merely_contains_429_is_not_quota():
    assert ou.classify_llm_failure(ValueError("bad json")) == ou.KIND_OTHER
    assert ou.classify_llm_failure(RuntimeError("took 14290ms")) == ou.KIND_OTHER
    assert ou.classify_llm_failure(None) == ou.KIND_OTHER


@pytest.mark.parametrize("kind", [ou.KIND_QUOTA, ou.KIND_OVERLOADED, ou.KIND_OTHER])
def test_plain_replies_never_leak_internals_or_name_a_hotline(kind):
    reply = ou.failure_reply(kind)
    assert reply
    assert not has_crisis_resource(reply)
    for leak in ("Traceback", "RESOURCE_EXHAUSTED", "429", "api key", "GOOGLE"):
        assert leak.lower() not in reply.lower()


def test_quota_reply_says_it_is_not_the_users_fault_and_nothing_was_lost():
    reply = ou.failure_reply(ou.KIND_QUOTA).lower()
    assert "not something you did" in reply
    assert "nothing you sent was lost" in reply


@pytest.mark.parametrize("kind", [ou.KIND_QUOTA, ou.KIND_OVERLOADED, ou.KIND_OTHER])
def test_a_crisis_turn_always_gets_a_real_human_line_whatever_the_failure(kind):
    reply = ou.failure_reply(kind, crisis=True)
    assert has_crisis_resource(reply)
    assert "988" in reply
    assert "don't wait on me" in reply.lower()


def test_in_crisis_uses_pattern_detection_over_recent_messages():
    assert ou.in_crisis(["fine", "i've been thinking about killing myself"])
    assert not ou.in_crisis(["ugh this deploy is killing me lol", "can you check the build"])
    assert not ou.in_crisis([])
    assert not ou.in_crisis([None, ""])


def test_log_model_outage_reports_kind_without_message_text(caplog):
    with caplog.at_level(logging.ERROR, logger=ou.logger.name):
        ou.log_model_outage(ou.KIND_QUOTA, "jack", True, RuntimeError("429 secret-key-123 my private message"))
    rec = caplog.records[-1]
    assert "quota" in rec.getMessage().lower()
    assert "secret-key-123" not in rec.getMessage()
    assert "private message" not in rec.getMessage()
    ctx = rec.erragent_context
    assert ctx["kind"] == ou.KIND_QUOTA
    assert ctx["crisisTurn"] is True
    assert "secret-key-123" not in str(ctx)


def test_handle_failed_turn_quota_for_an_ordinary_user_is_plain_and_logged(caplog):
    with caplog.at_level(logging.ERROR, logger=ou.logger.name):
        out = ou.handle_failed_turn(RuntimeError("429 RESOURCE_EXHAUSTED"), "jack", ["can you check the build"])
    assert (out.kind, out.crisis) == (ou.KIND_QUOTA, False)
    assert not has_crisis_resource(out.reply)
    assert "RESOURCE_EXHAUSTED" not in out.reply
    assert any("quota" in r.getMessage().lower() for r in caplog.records)


def test_handle_failed_turn_gives_a_human_line_when_recent_messages_signal_risk():
    out = ou.handle_failed_turn(
        RuntimeError("429 quota"), "jack", ["hey", "i don't see the point anymore, i want to kill myself"]
    )
    assert out.crisis
    assert has_crisis_resource(out.reply)


def test_handle_failed_turn_uses_the_conversations_safety_state_too():
    from datetime import datetime, timezone
    state = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0}
    out = ou.handle_failed_turn(RuntimeError("503 UNAVAILABLE"), "jack", ["ok"], state)
    assert out.crisis and has_crisis_resource(out.reply)


def test_a_model_outage_on_a_risk_turn_is_counted_for_the_safety_stats(monkeypatch):
    seen = []
    monkeypatch.setattr(ou, "log_safety_event", lambda *a, **k: seen.append((a, k)))
    ou.handle_failed_turn(RuntimeError("429 quota"), "jack", ["i want to kill myself"])
    assert seen == [(("jack", None, "quota", "outage_fallback"), {})]


def test_an_outage_on_an_ordinary_turn_adds_nothing_to_the_safety_stats(monkeypatch):
    seen = []
    monkeypatch.setattr(ou, "log_safety_event", lambda *a, **k: seen.append(a))
    ou.handle_failed_turn(RuntimeError("429 quota"), "jack", ["can you check the build"])
    assert seen == []


def test_handle_failed_turn_never_raises_even_on_odd_input():
    out = ou.handle_failed_turn(ValueError("x"), "jack", [None, 5, ["list"]], safety_state="garbage")
    assert out.reply
