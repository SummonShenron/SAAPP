import asyncio
import logging
import time

import pytest

from backend.utils import loop_watchdog as lw


@pytest.fixture(autouse=True)
def clean():
    lw._inflight.clear()
    yield
    lw._inflight.clear()


def test_requests_in_flight_are_listed_oldest_first_with_their_age():
    a = lw.request_started("POST", "/api/chat")
    b = lw.request_started("GET", "/api/budget")
    start_a, start_b = lw._inflight[a][2], lw._inflight[b][2]
    out = lw.describe_inflight(now=max(start_a, start_b) + 60)
    assert out.index("POST /api/chat") < out.index("GET /api/budget") and "(60s)" in out
    lw.request_finished(a)
    lw.request_finished(b)
    assert lw.describe_inflight() == "none"


def test_a_long_list_is_capped_and_says_how_many_more():
    for i in range(12):
        lw.request_started("GET", f"/api/x{i}")
    out = lw.describe_inflight()
    assert out.count("GET /api/x") == lw.MAX_LISTED and out.endswith("and 4 more")


def test_a_slow_request_is_logged_when_it_finishes_and_a_quick_one_is_not(caplog):
    slow = lw.request_started("POST", "/api/chat")
    quick = lw.request_started("GET", "/api/me")
    with caplog.at_level(logging.WARNING, logger="SASS Logger"):
        lw.request_finished(quick, now=lw._inflight[quick][2] + 0.2)
        lw.request_finished(slow, now=lw._inflight[slow][2] + 74.2)
    messages = [r.getMessage() for r in caplog.records]
    assert any("[slow-request] POST /api/chat took 74.2s" in m for m in messages) and not any("/api/me" in m for m in messages)


def test_finishing_a_request_twice_or_an_unknown_one_does_nothing():
    key = lw.request_started("GET", "/api/me")
    lw.request_finished(key)
    lw.request_finished(key)
    lw.request_finished(999_999)


def test_a_long_path_is_clipped_and_nothing_but_method_and_path_is_kept():
    key = lw.request_started("GET", "/api/" + "x" * 500)
    method, path, _ = lw._inflight[key]
    assert len(path) <= 120 and method == "GET"


def test_the_watchdog_notices_a_blocked_loop_and_names_the_request_that_was_waiting(caplog):
    async def scenario():
        key = lw.request_started("POST", "/api/chat")
        task = asyncio.get_running_loop().create_task(lw.watch(tick=0.05, threshold=0.2))
        await asyncio.sleep(0.2)        # the watchdog is ticking normally
        time.sleep(0.6)                 # something blocks the whole loop
        await asyncio.sleep(0.2)        # the watchdog gets to run and see how late it was
        task.cancel()
        lw.request_finished(key)

    with caplog.at_level(logging.WARNING, logger="SASS Logger"):
        asyncio.run(scenario())
    stalls = [r.getMessage() for r in caplog.records if "[loop-stall]" in r.getMessage()]
    assert stalls and "POST /api/chat" in stalls[0] and "blocked for about" in stalls[0]


def test_a_healthy_loop_logs_nothing(caplog):
    async def scenario():
        task = asyncio.get_running_loop().create_task(lw.watch(tick=0.05, threshold=0.2))
        await asyncio.sleep(0.5)
        task.cancel()

    with caplog.at_level(logging.WARNING, logger="SASS Logger"):
        asyncio.run(scenario())
    assert not [r for r in caplog.records if "[loop-stall]" in r.getMessage()]


def test_the_app_starts_the_watchdog_and_reports_every_request_by_path_only():
    source = open("app.py", encoding="utf-8").read()
    assert "watchdog_task = loop_watchdog.start()" in source and "watchdog_task.cancel()" in source
    assert "loop_watchdog.request_started(request.method, request.url.path)" in source  # path only: no query string, headers or body
    assert source.count("loop_watchdog.request_finished(in_flight)") == 2  # on success and on failure
