"""Web search (backend/services/web_search.py): the search API first, the free engines as the fallback, a breaker so a dead search fails fast, spend limits, and nothing
the person searched for in a log."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.services import web_search as ws

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ws.reset()
    for name in ("TAVILY_API_KEY",):
        monkeypatch.delenv(name, raising=False)
    yield
    ws.reset()


# ---- fakes ---------------------------------------------------------------------------------------------------------------------------------

class Collection:
    def __init__(self):
        self.docs = {}

    def find_one(self, query):
        doc = self.docs.get(query["_id"])
        return dict(doc) if doc else None

    def update_one(self, query, update, upsert=False):
        doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]}) if upsert or query["_id"] in self.docs else None
        if doc is None:
            return
        for key, value in update.get("$inc", {}).items():
            doc[key] = doc.get(key, 0) + value
        doc.update(update.get("$set", {}))


class Db(dict):
    def __getitem__(self, name):
        return self.setdefault(name, Collection())


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"results": []}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def tavily_rows(n=3):
    return {"results": [{"title": f"Title {i}", "url": f"https://example.com/{i}", "content": f"Snippet {i}. " * 80, "score": 0.9} for i in range(n)]}


class Posts:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if isinstance(self.reply, Exception):
            raise self.reply
        if callable(self.reply):
            return self.reply()
        return self.reply


def with_tavily(monkeypatch, reply):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    posts = Posts(reply)
    monkeypatch.setattr(ws.requests, "post", posts)
    return posts


def free_engines(monkeypatch, rows=None, error=None, news_rows=None, delay=0.0):
    """Replaces the ddgs library's DDGS with one that records how it was called."""
    import ddgs

    calls = []

    class FakeDDGS:
        def __init__(self, timeout=5, **kw):
            calls.append({"init_timeout": timeout})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, **kw):
            calls.append({"kind": "text", "query": query, **kw})
            time.sleep(delay)
            if error:
                raise error
            return rows if rows is not None else []

        def news(self, query, **kw):
            calls.append({"kind": "news", "query": query, **kw})
            if error:
                raise error
            return news_rows if news_rows is not None else []

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    return calls


HITS = [{"title": "Free result", "href": "https://free.example/a", "body": "From the free engines."}]


# ---- the search API ------------------------------------------------------------------------------------------------------------------------

def test_a_search_goes_to_tavily_with_a_basic_search_and_comes_back_in_the_old_shape(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(200, tavily_rows(3)))
    rows = ws.search("how does python asyncio work", 3, db=Db())
    assert [set(r) for r in rows] == [{"title", "snippet", "link"}] * 3 and rows[0]["link"] == "https://example.com/0"
    sent = posts.calls[0]
    assert sent["url"] == ws.TAVILY_URL and sent["headers"]["Authorization"] == "Bearer tvly-test-key"
    assert sent["json"] == {"query": "how does python asyncio work", "max_results": 3, "search_depth": "basic", "topic": "general",
                            "include_answer": False, "include_raw_content": False}
    assert sent["timeout"] <= ws.TAVILY_TIMEOUT and all(len(r["snippet"]) <= ws.SNIPPET_CHARS for r in rows)


def test_a_news_question_uses_the_news_topic_and_other_questions_do_not(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(200, tavily_rows(1)))
    ws.search("Roger Federer latest news", db=Db())
    ws.search("latest version of numpy", db=Db())
    ws.search("anything", news=True, db=Db())
    assert [c["json"]["topic"] for c in posts.calls] == ["news", "general", "news"]
    assert ws.wants_news("breaking: storm") and ws.wants_news("tech headlines") and not ws.wants_news("how to bake bread")


def test_results_without_a_link_are_dropped_and_the_count_is_capped(monkeypatch):
    body = {"results": [{"title": "No link", "content": "x"}, "junk", *tavily_rows(20)["results"]]}
    with_tavily(monkeypatch, Reply(200, body))
    assert len(ws.search("q", 99, db=Db())) == ws.MAX_RESULTS_CAP
    assert ws.search("", 3, db=Db()) == [] and ws.search("   ", 3, db=Db()) == []


# ---- the free engines ----------------------------------------------------------------------------------------------------------------------

def test_without_a_key_the_free_engines_answer_with_a_real_region_and_never_the_libraries_default(monkeypatch):
    calls = free_engines(monkeypatch, HITS)
    rows = ws.search("how does python asyncio work", 3)
    assert rows == [{"title": "Free result", "snippet": "From the free engines.", "link": "https://free.example/a"}]
    text = next(c for c in calls if c.get("kind") == "text")
    assert text["region"] == "us-en" and text["region"] != "wt-wt" and text["max_results"] == 3 and text["backend"] == "auto"


def test_a_news_question_asks_the_free_engines_news_search(monkeypatch):
    calls = free_engines(monkeypatch, news_rows=[{"title": "T", "body": "B", "url": "https://news.example/1"}])
    assert ws.search("Federer news", 3) == [{"title": "T", "snippet": "B", "link": "https://news.example/1"}]
    assert any(c.get("kind") == "news" for c in calls) and not any(c.get("kind") == "text" for c in calls)


def test_a_search_that_found_nothing_is_an_empty_answer_not_a_failure_and_does_not_rest_the_engines(monkeypatch):
    from ddgs.exceptions import DDGSException

    free_engines(monkeypatch, error=DDGSException("No results found."))
    assert ws.search("zzzxqv nothing", 3) == [] and ws.resting() == {}


def test_engines_that_all_refuse_or_time_out_are_a_failure_and_rest(monkeypatch):
    from ddgs.exceptions import DDGSException, TimeoutException

    for error in (DDGSException("blocked"), TimeoutException("operation timed out")):
        ws.reset()
        free_engines(monkeypatch, error=error)
        with pytest.raises(ws.SearchUnavailable):
            ws.search("q", 3)
        assert ws.FREE in ws.resting()


# ---- fallback and the breaker -------------------------------------------------------------------------------------------------------------

def test_when_tavily_fails_the_free_engines_answer_the_same_call_and_tavily_rests_for_a_minute(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(500))
    calls = free_engines(monkeypatch, HITS)
    assert ws.search("q one", 3, db=Db())[0]["link"] == "https://free.example/a"
    assert 0 < ws.resting()[ws.TAVILY] <= ws.BREAKER_SECONDS
    ws.search("q two", 3, db=Db())
    assert len(posts.calls) == 1 and sum(1 for c in calls if c.get("kind") == "text") == 2        # the second search never touched Tavily


@pytest.mark.parametrize("status", [401, 403, 432, 433])
def test_a_key_or_plan_problem_rests_tavily_for_much_longer_than_a_blip(monkeypatch, status):
    with_tavily(monkeypatch, Reply(status))
    free_engines(monkeypatch, HITS)
    ws.search("q", 3, db=Db())
    assert ws.resting()[ws.TAVILY] > ws.BREAKER_SECONDS * 5


@pytest.mark.parametrize("reply", [Reply(200, ValueError("bad json")), ws.requests.Timeout("slow"), ws.requests.ConnectionError("down")])
def test_an_unreadable_reply_a_timeout_or_a_dropped_connection_all_fall_back(monkeypatch, reply):
    with_tavily(monkeypatch, reply)
    free_engines(monkeypatch, HITS)
    assert ws.search("q", 3, db=Db())[0]["link"] == "https://free.example/a" and ws.TAVILY in ws.resting()


def test_when_nothing_can_answer_the_next_search_fails_at_once_without_calling_anyone(monkeypatch):
    from ddgs.exceptions import DDGSException

    posts = with_tavily(monkeypatch, Reply(500))
    calls = free_engines(monkeypatch, error=DDGSException("blocked"))
    with pytest.raises(ws.SearchUnavailable):
        ws.search("q one", 3, db=Db())
    before = (len(posts.calls), len(calls))
    started = time.monotonic()
    with pytest.raises(ws.SearchUnavailable):
        ws.search("q two, reworded", 3, db=Db())
    assert (len(posts.calls), len(calls)) == before and time.monotonic() - started < 0.5


def test_a_resting_provider_is_tried_again_once_its_time_is_up(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(500))
    free_engines(monkeypatch, HITS)
    ws.search("q", 3, db=Db())
    monkeypatch.setitem(ws._resting_until, ws.TAVILY, time.monotonic() - 1)
    posts.reply = Reply(200, tavily_rows(1))
    assert ws.search("q", 3, db=Db())[0]["link"] == "https://example.com/0" and ws.TAVILY not in ws.resting()


def test_one_call_never_takes_longer_than_the_overall_limit_even_when_a_provider_hangs(monkeypatch):
    monkeypatch.setattr(ws, "OVERALL_TIMEOUT", 4.0)
    monkeypatch.setattr(ws, "TAVILY_TIMEOUT", 1.0)
    with_tavily(monkeypatch, lambda: time.sleep(5) or Reply(200, tavily_rows(1)))
    free_engines(monkeypatch, HITS, delay=5.0)
    started = time.monotonic()
    with pytest.raises(ws.SearchUnavailable):
        ws.search("q", 3, db=Db())
    assert time.monotonic() - started < 5.0                                        # the 4 second limit, plus a little for the threads to hand back
    assert {ws.TAVILY, ws.FREE} <= set(ws.resting())


# ---- spend limits --------------------------------------------------------------------------------------------------------------------------

def test_the_monthly_ceiling_sends_searches_to_the_free_engines_before_tavilys_own_limit_is_reached(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(200, tavily_rows(1)))
    free_engines(monkeypatch, HITS)
    db = Db()
    monkeypatch.setattr(ws, "TAVILY_MONTHLY_LIMIT", 3)
    for n in range(5):
        ws.search(f"q{n}", 3, username=f"user{n}", db=db)
    assert len(posts.calls) == 3 and ws.usage_this_month(db) == 3


def test_one_person_cannot_use_up_the_months_searches_in_a_day(monkeypatch):
    posts = with_tavily(monkeypatch, Reply(200, tavily_rows(1)))
    free_engines(monkeypatch, HITS)
    db = Db()
    monkeypatch.setattr(ws, "TAVILY_USER_DAILY", 2)
    for n in range(4):
        ws.search(f"q{n}", 3, username="jack", db=db)
    ws.search("someone else", 3, username="lee", db=db)
    assert len(posts.calls) == 3                                    # two for jack, one for lee


def test_scheduled_jobs_count_under_system_and_a_person_is_not_stored_by_name(monkeypatch):
    with_tavily(monkeypatch, Reply(200, tavily_rows(1)))
    db = Db()
    ws.search("q", 3, db=db)
    ws.search("q", 3, username="jack@example.com", db=db)
    ids = list(db[ws.USAGE_COLLECTION].docs)
    assert not any("jack" in doc_id for doc_id in ids) and len(ids) == 3 and all(d["expires_at"] for d in db[ws.USAGE_COLLECTION].docs.values())


def test_a_failed_request_is_not_charged_to_a_second_allowance_and_a_broken_database_does_not_stop_search(monkeypatch):
    with_tavily(monkeypatch, Reply(200, tavily_rows(1)))

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("database down")

    assert ws.search("q", 3, db=Broken())[0]["link"] == "https://example.com/0"
    assert ws.reserve_tavily(None, "jack") is True


# ---- what is logged ------------------------------------------------------------------------------------------------------------------------

def test_the_search_text_never_reaches_a_log_and_the_libraries_that_print_urls_are_quieted(monkeypatch, caplog):
    secret = "my landlord Dana Quillfeather eviction"
    with_tavily(monkeypatch, Reply(500))
    free_engines(monkeypatch, error=RuntimeError(f"boom {secret}"))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ws.SearchUnavailable):
            ws.search(secret, 3, db=Db())
    assert "Quillfeather" not in caplog.text and "eviction" not in caplog.text
    for name in ("primp", "ddgs"):
        assert logging.getLogger(name).level >= logging.WARNING


# ---- the async entry point and the wiring --------------------------------------------------------------------------------------------------

def test_the_async_entry_point_runs_the_search_off_the_event_loop(monkeypatch):
    free_engines(monkeypatch, HITS)
    rows = asyncio.run(ws.search_async("how does python asyncio work", 3, username="jack"))
    assert rows[0]["link"] == "https://free.example/a"


def test_the_tool_agent_tells_the_model_once_that_search_is_down_and_the_old_wrapper_is_gone():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    branch = source[source.index('if tool_action == "web_search":'):source.index('if tool_action == "read_url":')]
    assert "run_web_search(query, max_results=3, username=username)" in branch and "except SearchUnavailable" in branch and "WEB_SEARCH_UNAVAILABLE" in branch
    assert "DuckDuckGoSearchAPIWrapper" not in source and "Do not call web_search again this turn" in ws.UNAVAILABLE_MESSAGE
    assert ws.UNAVAILABLE_MESSAGE.startswith("ERROR")                      # the memory layer and the loop already treat an ERROR string as a failed tool


def test_the_reading_shelf_and_user_tasks_search_through_the_same_layer(monkeypatch):
    from backend.services import shelf_refresh

    seen = {}
    monkeypatch.setattr(ws, "search", lambda query, max_results=3, **kw: seen.update(query=query, max_results=max_results, **kw) or [{"link": "https://a.example/x"}, "junk"])
    assert shelf_refresh.default_search("topic query") == [{"link": "https://a.example/x"}]
    assert seen == {"query": "topic query", "max_results": shelf_refresh.MAX_RESULTS, "news": False}
