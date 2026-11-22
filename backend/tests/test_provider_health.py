"""A model provider refusing Sonic's API key is noticed, logged, counted and shown to an admin (backend/utils/provider_health.py). On 2026-11-17 the Anthropic key on Render expired; every Claude call answered 401, the app
quietly fell back to Gemini, and the work cost about four times as much for three days before a crash showed it. No model is called here: fakes stand in for the providers' clients."""
import asyncio
import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from backend.models import openai_llm as ol
from backend.models.claude_llm import ClaudeLLM
from backend.tests.test_claude_llm import FakeChat, FakeGemini
from backend.tests.test_openai_llm import FakeClient, reply
from backend.utils import provider_health as ph
from backend.utils import self_counters


class Rejected(Exception):
    status_code = 401


class Forbidden(Exception):
    status_code = 403


class Busy(Exception):
    status_code = 529


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ph.reset()
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    monkeypatch.setattr("backend.utils.llm_usage.record", lambda *a, **k: None)
    yield
    ph.reset()


@pytest.fixture()
def counted(monkeypatch):
    seen = []
    monkeypatch.setattr(ph, "_record", lambda key: seen.append(key))
    return seen


@pytest.fixture()
def logged():
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append((record.levelname, record.getMessage()))

    handler = Capture(level=logging.DEBUG)
    logger = logging.getLogger("SASS Logger")
    logger.addHandler(handler)
    yield lines
    logger.removeHandler(handler)


# ---- what counts as "the key was refused" ------------------------------------------------------------------------------------------------------------

def test_a_401_or_a_403_is_a_refused_key_and_nothing_else_is():
    assert ph.is_auth_failure(Rejected("x")) and ph.is_auth_failure(Forbidden("x"))
    for other in (Busy("overloaded"), RuntimeError("boom"), TimeoutError("slow"), ValueError("bad request"), Exception()):
        assert not ph.is_auth_failure(other), other
    assert ph.is_auth_failure(SimpleNamespace(response=SimpleNamespace(status_code=401))) is True
    assert ph.is_auth_failure(SimpleNamespace(response=SimpleNamespace(status_code=429))) is False
    assert ph.is_auth_failure(None) is False


def test_the_error_classes_the_providers_really_raise_are_recognised():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def raised(status, cls):
        return cls(message="m", response=httpx.Response(status, request=request), body=None)

    assert ph.is_auth_failure(raised(401, anthropic.AuthenticationError))
    assert ph.is_auth_failure(raised(403, anthropic.PermissionDeniedError))
    assert not ph.is_auth_failure(raised(429, anthropic.RateLimitError))
    assert not ph.is_auth_failure(raised(500, anthropic.InternalServerError))
    assert not ph.is_auth_failure(anthropic.APIConnectionError(request=request))
    assert ph.is_auth_failure(type("AuthenticationError", (Exception,), {})("no status attached")), "recognised by its class name when no status is attached"


# ---- the state ---------------------------------------------------------------------------------------------------------------------------------------

def test_a_refused_key_is_shown_with_what_it_means_and_what_to_do_and_nothing_else_is(counted):
    assert ph.alerts() == [] and not ph.degraded()
    assert ph.note_failure("claude", Busy("overloaded")) is False and ph.alerts() == [], "an overloaded service is not a bad key"
    assert ph.note_failure("claude", Rejected("invalid x-api-key")) is True
    [alert] = ph.alerts()
    assert alert["provider"] == "claude" and alert["label"] == "Claude (Anthropic)" and alert["failures"] == 1
    assert "falling back to Gemini" in alert["message"] and "costs more" in alert["message"] and "ANTHROPIC_API_KEY" in alert["message"] and "platform.claude.com/settings/keys" in alert["message"]
    assert ph.degraded()
    first = alert["since"]
    ph.note_failure("claude", Rejected("again"))
    [again] = ph.alerts()
    assert again["since"] == first and again["failures"] == 2, "it keeps when it began and how often since"


def test_each_provider_has_its_own_alert_and_an_unknown_provider_is_ignored():
    ph.note_failure("claude", Rejected("x"))
    ph.note_failure("openai", Forbidden("x"))
    assert [a["provider"] for a in ph.alerts()] == ["claude", "openai"]
    assert ph.note_failure("mystery", Rejected("x")) is False
    ph.note_ok("mystery")


def test_a_call_that_goes_through_clears_it_and_the_recovery_is_counted_once(counted):
    ph.note_failure("claude", Rejected("x"))
    ph.note_ok("claude")
    assert ph.alerts() == [] and not ph.degraded()
    assert counted == ["llm_key_rejected_claude", "llm_key_recovered_claude"]
    ph.note_ok("claude")
    ph.note_ok("openai")
    assert counted == ["llm_key_rejected_claude", "llm_key_recovered_claude"], "a success with nothing rejected counts nothing"


def test_the_entry_points_never_raise_into_a_reply():
    class Weird:
        @property
        def status_code(self):
            raise RuntimeError("no")

    assert ph.note_failure("claude", Weird()) is False
    ph.note_ok(None)
    ph.reset()
    assert ph.alerts() == []


# ---- logging and counting, kept from flooding --------------------------------------------------------------------------------------------------------

def test_it_logs_an_error_the_first_time_and_then_at_most_every_thirty_minutes_and_never_the_errors_own_words(logged):
    secret = "invalid x-api-key sk-ant-api03-SECRET-VALUE"
    for clock in (0, 100, 1500, 1900, 2000):
        ph.note_failure("claude", Rejected(secret), clock=clock)
    errors = [text for level, text in logged if level == "ERROR"]
    assert len(errors) == 2, errors
    assert "Claude (Anthropic)" in errors[0] and "ANTHROPIC_API_KEY" in errors[0]
    assert all("SECRET-VALUE" not in text and "x-api-key" not in text for _, text in logged)


def test_it_counts_a_refusal_at_most_once_a_minute_so_the_counter_is_minutes_with_a_refusal_not_calls(counted):
    for clock in (0, 10, 59, 61, 100, 125):
        ph.note_failure("claude", Rejected("x"), clock=clock)
    assert counted == ["llm_key_rejected_claude"] * 3, "at 0, 61 and 125"


def test_a_recovery_is_logged_and_the_next_refusal_logs_loudly_again(logged):
    ph.note_failure("claude", Rejected("x"), clock=0)
    ph.note_ok("claude")
    ph.note_failure("claude", Rejected("x"), clock=5)
    levels = [level for level, _ in logged]
    assert levels.count("ERROR") == 2 and levels.count("INFO") >= 1
    assert any("accepted the API key again" in text for _, text in logged)


def test_the_counts_go_to_sonic_health_under_keys_that_exist_and_off_the_calling_thread(monkeypatch):
    for provider in ph.PROVIDERS:
        assert f"llm_key_rejected_{provider}" in self_counters.KEYS and f"llm_key_recovered_{provider}" in self_counters.KEYS
    written, started = [], []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: written.append(counts))

    class Immediate:
        def __init__(self, target, daemon=False):
            self.target = target
            started.append(daemon)

        def start(self):
            self.target()

    monkeypatch.setattr(ph.threading, "Thread", Immediate)
    ph._record("llm_key_rejected_claude")
    assert written == [{"llm_key_rejected_claude": 1}] and started == [True], "a daemon thread: a slow database cannot hold up a reply"


# ---- through the real wrappers -----------------------------------------------------------------------------------------------------------------------

def claude(chat, fallback=None):
    llm = ClaudeLLM(fallback=fallback)
    llm._chat = chat
    return llm


def test_a_refused_claude_key_falls_back_as_before_and_is_now_shown_and_a_good_call_clears_it(counted):
    llm = claude(FakeChat(error=Rejected("invalid x-api-key")), fallback=FakeGemini())
    assert llm.invoke("hi").content == "from gemini" and asyncio.run(llm.ainvoke("hi")).content == "from gemini"
    assert [a["provider"] for a in ph.alerts()] == ["claude"] and ph.alerts()[0]["failures"] == 2
    llm._chat = FakeChat()
    assert llm.invoke("hi").content != "from gemini"
    assert ph.alerts() == [], "the first good answer after the fix clears it"


def test_the_tool_step_path_is_covered_too():
    llm = claude(FakeChat(error=Rejected("x")), fallback=FakeGemini())
    assert asyncio.run(llm.ainvoke_tools([AIMessage(content="x")], [{"name": "t", "description": "d"}])).content == "from gemini"
    assert [a["provider"] for a in ph.alerts()] == ["claude"]


def test_an_overloaded_claude_still_falls_back_but_is_not_called_a_bad_key():
    llm = claude(FakeChat(error=Busy("overloaded")), fallback=FakeGemini())
    assert llm.invoke("hi").content == "from gemini"
    assert ph.alerts() == []


class Streaming:
    def __init__(self, error=None, chunks=("a", "b")):
        self.error, self.chunks = error, chunks

    async def astream(self, messages, *args, **kwargs):
        if self.error:
            raise self.error
        for text in self.chunks:
            yield AIMessage(content=text)


async def drain(stream):
    return [chunk async for chunk in stream]


def test_a_stream_that_is_refused_is_noticed_with_a_fallback_and_without_one_and_a_stream_that_works_clears_it():
    fallback = SimpleNamespace(astream=lambda prompt, *a, **k: _gen(["from gemini"]))
    llm = claude(Streaming(error=Rejected("x")), fallback=fallback)
    assert [c.content for c in asyncio.run(drain(llm.astream("hi")))] == ["from gemini"]
    assert [a["provider"] for a in ph.alerts()] == ["claude"]
    llm._chat = Streaming()
    assert [c.content for c in asyncio.run(drain(llm.astream("hi")))] == ["a", "b"]
    assert ph.alerts() == []
    bare = claude(Streaming(error=Rejected("x")), fallback=None)
    with pytest.raises(Rejected):
        asyncio.run(drain(bare.astream("hi")))
    assert [a["provider"] for a in ph.alerts()] == ["claude"], "nothing to fall back to, and it is still noticed"


async def _gen(items):
    for item in items:
        yield AIMessage(content=item)


def test_the_openai_wrapper_reports_and_clears_in_the_same_way():
    llm = ol.OpenAILLM(fallback=FakeGemini())
    llm._chat = FakeClient(error=Forbidden("no access"))
    assert llm.invoke("hi").content == "from gemini"
    assert [a["provider"] for a in ph.alerts()] == ["openai"]
    llm._chat = FakeClient(answer=reply("fine"))
    assert llm.invoke("hi").content == "fine"
    assert ph.alerts() == []


# ---- what the admin and the health check are told ----------------------------------------------------------------------------------------------------

def test_an_admin_is_told_in_the_budget_view_whatever_the_budget_says_and_nobody_else_is():
    from backend.utils import budget_guard as bg

    ph.note_failure("claude", Rejected("x"))
    for state in (None, {"level": bg.OFF}, {"level": bg.OK}):
        admin = bg.public_view(state, True)
        assert [a["provider"] for a in admin["provider_alerts"]] == ["claude"], state
        assert "provider_alerts" not in bg.public_view(state, False), "never sent to an ordinary user"
    ph.reset()
    assert bg.public_view(None, True)["provider_alerts"] == []


def test_the_health_check_says_degraded_while_a_key_is_refused_without_saying_which(monkeypatch):
    from backend.tests.import_app import import_app

    app_module = import_app()
    monkeypatch.setattr(app_module, "test_connection", lambda: False)     # the health check pings the database; tests never touch the real one
    ok = app_module.saapp_health_check()
    assert ok["llm"] == "ok"
    ph.note_failure("claude", Rejected("x"))
    bad = app_module.saapp_health_check()
    assert bad["llm"] == "degraded" and bad["status"] == "degraded"
    assert "claude" not in str(bad).lower() and "anthropic" not in str(bad).lower(), "the unauthenticated health check does not name the provider"
