import asyncio
import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from backend.services import browser_tool as bt


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _fake_locator(count=1):
    locator = MagicMock()
    locator.count = AsyncMock(return_value=count)
    locator.click = AsyncMock()
    locator.fill = AsyncMock()
    locator.press = AsyncMock()
    locator.first = locator
    return locator


def _fake_page(**overrides):
    page = MagicMock()
    page.url = overrides.get("url", "https://example.com/")
    page.goto = AsyncMock(return_value=SimpleNamespace(status=overrides.get("status", 200)))
    page.title = AsyncMock(return_value=overrides.get("title", "Example Domain"))
    page.inner_text = AsyncMock(return_value=overrides.get("text", "Hello world"))
    page.screenshot = AsyncMock(return_value=b"fake-png-bytes")
    page.wait_for_load_state = AsyncMock()
    page.set_default_timeout = MagicMock()
    # Nothing found by default — individual tests point one of these at a real locator.
    empty = _fake_locator(count=0)
    page.get_by_role = MagicMock(return_value=empty)
    page.get_by_label = MagicMock(return_value=empty)
    page.get_by_placeholder = MagicMock(return_value=empty)
    page.get_by_text = MagicMock(return_value=empty)
    return page


def _session_with_fake_page(fake_page):
    """Bypasses the real Playwright connection entirely — _ensure_started is a no-op and
    `page` is pre-seeded, exactly as if a prior action in the same turn had already connected."""
    session = bt.BrowserSession("wss://fake-endpoint")
    session._ensure_started = AsyncMock()
    session.page = fake_page
    return session


# ---------------------------------------------------------------------------
# browser_navigate
# ---------------------------------------------------------------------------

@run_async
async def test_browser_navigate_returns_title_and_url():
    page = _fake_page(url="https://example.com/", title="Example Domain", status=200)
    session = _session_with_fake_page(page)

    result = await bt.browser_navigate(session, "example.com")

    assert "https://example.com/" in result
    assert "Example Domain" in result
    assert "200" in result
    page.goto.assert_awaited_once()
    # A bare host with no scheme gets https:// prepended before being handed to Playwright.
    assert page.goto.call_args.args[0] == "https://example.com"


@run_async
async def test_browser_navigate_timeout_returns_error_observation():
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    page = _fake_page()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("timed out"))
    session = _session_with_fake_page(page)

    result = await bt.browser_navigate(session, "https://slow-site.example")

    assert result.startswith("ERROR")


@run_async
async def test_browser_navigate_no_url_is_error_without_touching_playwright():
    session = _session_with_fake_page(_fake_page())

    result = await bt.browser_navigate(session, None)

    assert result.startswith("ERROR")


# ---------------------------------------------------------------------------
# browser_read_text
# ---------------------------------------------------------------------------

@run_async
async def test_browser_read_text_requires_prior_navigate():
    session = bt.BrowserSession("wss://fake-endpoint")  # session.page is still None

    result = await bt.browser_read_text(session)

    assert "ERROR" in result
    assert "browser_navigate" in result


@run_async
async def test_browser_read_text_collapses_whitespace_and_truncates():
    long_text = "word " * 3000  # comfortably over _BROWSER_TEXT_CHAR_CAP once collapsed
    page = _fake_page(text=long_text)
    session = _session_with_fake_page(page)

    result = await bt.browser_read_text(session)

    assert result.startswith(f"URL: {page.url}")
    assert "  " not in result  # whitespace collapsed to single spaces
    assert "[truncated]" in result
    assert len(result) < len(long_text)


# ---------------------------------------------------------------------------
# browser_click / browser_type — visible-text locator fallback chain
# ---------------------------------------------------------------------------

@run_async
async def test_browser_click_finds_button_by_role_and_clicks_it():
    page = _fake_page()
    button = _fake_locator(count=1)
    page.get_by_role = MagicMock(side_effect=lambda role, name=None, exact=False: button if role == "button" else _fake_locator(0))
    session = _session_with_fake_page(page)

    result = await bt.browser_click(session, "Sign in")

    button.click.assert_awaited_once()
    assert "Sign in" in result
    assert "ERROR" not in result


@run_async
async def test_browser_click_reports_error_when_nothing_matches():
    session = _session_with_fake_page(_fake_page())  # every locator returns count=0

    result = await bt.browser_click(session, "Nonexistent Button")

    assert result.startswith("ERROR")


@run_async
async def test_browser_type_fills_field_found_by_label_and_submits():
    page = _fake_page()
    field = _fake_locator(count=1)
    page.get_by_label = MagicMock(return_value=field)
    session = _session_with_fake_page(page)

    result = await bt.browser_type(session, "Email", "jack@example.com", submit=True)

    field.fill.assert_awaited_once_with("jack@example.com", timeout=session.action_timeout_ms)
    field.press.assert_awaited_once_with("Enter")
    assert "submitted" in result


@run_async
async def test_browser_type_without_locating_field_is_error():
    session = _session_with_fake_page(_fake_page())

    result = await bt.browser_type(session, "Nonexistent Field", "value")

    assert result.startswith("ERROR")


# ---------------------------------------------------------------------------
# browser_screenshot — the one action that makes its own vision LLM call
# ---------------------------------------------------------------------------

@run_async
async def test_browser_screenshot_calls_vision_llm_and_returns_description():
    page = _fake_page(url="https://example.com/")
    session = _session_with_fake_page(page)
    fake_llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="A plain page with a heading.")))

    result = await bt.browser_screenshot(session, fake_llm)

    fake_llm.ainvoke.assert_awaited_once()
    (messages,), _ = fake_llm.ainvoke.call_args
    content_blocks = messages[0].content
    assert any(block.get("type") == "image_url" for block in content_blocks)
    assert any(block.get("type") == "text" for block in content_blocks)
    assert "A plain page with a heading." in result
    assert "https://example.com/" in result


@run_async
async def test_browser_screenshot_requires_prior_navigate():
    session = bt.BrowserSession("wss://fake-endpoint")
    fake_llm = SimpleNamespace(ainvoke=AsyncMock())

    result = await bt.browser_screenshot(session, fake_llm)

    assert result.startswith("ERROR")
    fake_llm.ainvoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# BrowserSession._ensure_started — the guest-auth seeding + idempotency
# ---------------------------------------------------------------------------

@run_async
async def test_ensure_started_seeds_guest_auth_and_connects_once(monkeypatch):
    fake_page = _fake_page()
    fake_cdp_session = MagicMock()
    fake_cdp_session.send = AsyncMock(return_value={"liveURL": "https://production-sfo.browserless.io/live/index.html?i=abc"})

    fake_context = MagicMock()
    fake_context.add_init_script = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.new_cdp_session = AsyncMock(return_value=fake_cdp_session)
    fake_context.close = AsyncMock()

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_chromium = SimpleNamespace(connect_over_cdp=AsyncMock(return_value=fake_browser))
    fake_playwright_driver = SimpleNamespace(chromium=fake_chromium, stop=AsyncMock())

    fake_playwright_ctx = MagicMock()
    fake_playwright_ctx.start = AsyncMock(return_value=fake_playwright_driver)

    monkeypatch.setattr(bt, "async_playwright", lambda: fake_playwright_ctx)

    session = bt.BrowserSession("wss://fake-endpoint?token=abc")
    await session._ensure_started()
    await session._ensure_started()  # second call must be a no-op — only one connection made

    fake_chromium.connect_over_cdp.assert_awaited_once_with("wss://fake-endpoint?token=abc")
    fake_context.add_init_script.assert_awaited_once()
    seeded_script = fake_context.add_init_script.call_args.args[0]
    assert "guest-sandbox-token" in seeded_script
    assert session.page is fake_page

    # LiveURL is minted view-only — the model is driving this same page at the same time, so
    # letting a viewer also click/type into it would mean the two fighting over one session.
    fake_cdp_session.send.assert_awaited_once_with("Browserless.liveURL", {"interactable": False})
    assert session.live_url == "https://production-sfo.browserless.io/live/index.html?i=abc"

    await session.close()
    fake_context.close.assert_awaited_once()
    fake_browser.close.assert_awaited_once()
    fake_playwright_driver.stop.assert_awaited_once()


@run_async
async def test_ensure_started_tolerates_live_url_not_being_available(monkeypatch):
    """Not every browserless plan/token has LiveURL enabled — that must never break the
    browser tool itself, just leave nothing to watch by."""
    fake_page = _fake_page()
    fake_context = MagicMock()
    fake_context.add_init_script = AsyncMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.new_cdp_session = AsyncMock(side_effect=RuntimeError("not enabled for this token"))
    fake_context.close = AsyncMock()

    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)
    fake_browser.close = AsyncMock()

    fake_chromium = SimpleNamespace(connect_over_cdp=AsyncMock(return_value=fake_browser))
    fake_playwright_driver = SimpleNamespace(chromium=fake_chromium, stop=AsyncMock())
    fake_playwright_ctx = MagicMock()
    fake_playwright_ctx.start = AsyncMock(return_value=fake_playwright_driver)
    monkeypatch.setattr(bt, "async_playwright", lambda: fake_playwright_ctx)

    session = bt.BrowserSession("wss://fake-endpoint?token=abc")
    await session._ensure_started()

    assert session.live_url is None
    assert session.page is fake_page  # the session itself still came up fine
