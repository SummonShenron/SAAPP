"""Headless-browser actions for tool_agent_node's ReAct loop, backed by browserless.io over CDP
(no local Chromium/Firefox/WebKit binaries — see requirements.txt for why that matters on Render).

Every public function here returns a plain string and never raises: failures come back as
"ERROR: ..." strings, matching _read_file/_search_code's convention in agent_workflow.py so
run_react_loop's existing retry-nudge tracking (_is_empty_observation, unretried_inconclusive_tools)
picks up a failed/hung browser step exactly like a failed GitHub call, with no new plumbing.

BrowserSession is deliberately dumb: one CDP connection, one context, one page, reused across
every browser_* action within a single tool_agent_node call and closed exactly once when that
call's ReAct loop ends (see agent_workflow.py's finally block around run_react_loop). It cannot be
checkpointed into paused_clarification — a clarification pause reconnects a fresh session on
resume rather than resuming the old page, which is an accepted limitation, not a bug.
"""
import base64
import logging
import re

from langchain_core.messages import HumanMessage
from playwright.async_api import async_playwright

from backend.components.constraints import BROWSER_SCREENSHOT_DESCRIBE_PROMPT

logger = logging.getLogger("SASS Logger")

BROWSER_ACTION_TIMEOUT_MS_DEFAULT = 20_000
_BROWSER_TEXT_CHAR_CAP = 6000

# Harmless on any site that isn't ours (nothing else reads these keys) — see
# backend/auth/isolation_auth.py's guest bypass and local/src/api.ts's getEffectivePrincipal().
# Added unconditionally rather than conditioned on the target origin, per design.
GUEST_SEED_INIT_SCRIPT = """
window.localStorage.setItem('principal', 'guest');
window.localStorage.setItem('guest_token', 'guest-sandbox-token');
"""


class BrowserSession:
    """One CDP connection, one context, one page — lazily created on first use, reused after
    that. Not safe for concurrent use from multiple tasks at once; tool_agent_node's ReAct loop
    is sequential (one step at a time), so this is never an issue in practice."""

    def __init__(self, ws_endpoint: str, action_timeout_ms: int = BROWSER_ACTION_TIMEOUT_MS_DEFAULT):
        self.ws_endpoint = ws_endpoint
        self.action_timeout_ms = action_timeout_ms
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None
        self.live_url: str | None = None
        self._live_url_announced = False

    async def _ensure_started(self) -> None:
        if self.page is not None:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(self.ws_endpoint)
        self._context = await self._browser.new_context()
        await self._context.add_init_script(GUEST_SEED_INIT_SCRIPT)
        self.page = await self._context.new_page()
        self.page.set_default_timeout(self.action_timeout_ms)
        await self._mint_live_url()

    async def _mint_live_url(self) -> None:
        """Best-effort: browserless.io's LiveURL feature returns a shareable link to watch this
        exact session in real time. interactable=False is deliberate, not the default — the
        model is actively driving this same page at the same time, so an interactive embed
        would let a human and the AI fight over control of one session; view-only just lets
        someone watch. Not every plan/token has LiveURL enabled, so a failure here is silent —
        the browser tool works fine either way, it just has nothing to offer a human to watch by."""
        try:
            cdp = await self._context.new_cdp_session(self.page)
            result = await cdp.send("Browserless.liveURL", {"interactable": False})
            self.live_url = result.get("liveURL")
        except Exception:
            logger.info("[browser_tool] LiveURL not available for this session — continuing without it.")
            self.live_url = None

    def pop_live_url_announcement(self) -> str:
        """A one-time human-readable line pointing at the live view — returned once (right
        after the first successful navigate) and empty forever after, so it doesn't clutter
        every subsequent observation this turn."""
        if self.live_url and not self._live_url_announced:
            self._live_url_announced = True
            return f"(You can watch this browser session live: {self.live_url})\n"
        return ""

    async def close(self) -> None:
        """Best-effort — each step is guarded so one failure doesn't skip the rest."""
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                logger.exception("[browser_tool] failed to close browser context cleanly.")
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.exception("[browser_tool] failed to close browser connection cleanly.")
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.exception("[browser_tool] failed to stop the Playwright driver cleanly.")


def _with_scheme(url: str) -> str:
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return f"https://{url}"
    return url


async def browser_navigate(session: BrowserSession, url: str | None) -> str:
    if not url:
        return "ERROR: no url given"
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        await session._ensure_started()
        response = await session.page.goto(
            _with_scheme(url), wait_until="domcontentloaded", timeout=session.action_timeout_ms
        )
        title = await session.page.title()
        status = response.status if response is not None else "unknown"
        return f"Navigated to {session.page.url} (status {status}). Page title: '{title}'"
    except PlaywrightTimeoutError:
        return f"ERROR: timed out loading {url}"
    except Exception as e:
        return f"ERROR: could not load {url}: {e}"


async def browser_read_text(session: BrowserSession) -> str:
    if session.page is None:
        return "ERROR: no page loaded yet — use browser_navigate first"
    try:
        text = await session.page.inner_text("body")
        collapsed = re.sub(r"\s+", " ", text).strip()
        snippet = collapsed[:_BROWSER_TEXT_CHAR_CAP] + (
            "\n... [truncated]" if len(collapsed) > _BROWSER_TEXT_CHAR_CAP else ""
        )
        return f"URL: {session.page.url}\n{snippet}"
    except Exception as e:
        return f"ERROR: could not read page text: {e}"


async def _locate(page, text: str):
    """A model can only ever see rendered text (from browser_read_text), never a DOM/selector —
    so it can only reasonably supply visible text/labels. Tries the common ways a user would
    identify something by eye, falls through to the next if the first finds nothing."""
    for locator_fn in (
        lambda: page.get_by_role("button", name=text, exact=False),
        lambda: page.get_by_role("link", name=text, exact=False),
        lambda: page.get_by_label(text, exact=False),
        lambda: page.get_by_placeholder(text, exact=False),
        lambda: page.get_by_text(text, exact=False),
    ):
        locator = locator_fn()
        if await locator.count() > 0:
            return locator.first
    return None


async def browser_click(session: BrowserSession, target: str | None) -> str:
    if session.page is None:
        return "ERROR: no page loaded yet — use browser_navigate first"
    if not target:
        return "ERROR: no target text given"
    try:
        locator = await _locate(session.page, target)
        if locator is None:
            return f"ERROR: could not find anything matching '{target}' to click"
        await locator.click(timeout=session.action_timeout_ms)
        await session.page.wait_for_load_state("domcontentloaded", timeout=session.action_timeout_ms)
        title = await session.page.title()
        return f"Clicked '{target}'. Now at {session.page.url} — page title: '{title}'"
    except Exception as e:
        return f"ERROR: could not click '{target}': {e}"


async def browser_type(session: BrowserSession, label: str | None, value: str | None, submit: bool = False) -> str:
    if session.page is None:
        return "ERROR: no page loaded yet — use browser_navigate first"
    if not label or value is None:
        return "ERROR: both label and value are required"
    try:
        locator = await _locate(session.page, label)
        if locator is None:
            return f"ERROR: could not find an input matching '{label}'"
        await locator.fill(value, timeout=session.action_timeout_ms)
        if submit:
            await locator.press("Enter")
            await session.page.wait_for_load_state("domcontentloaded", timeout=session.action_timeout_ms)
        return f"Typed into '{label}'" + (f" and submitted — now at {session.page.url}" if submit else ".")
    except Exception as e:
        return f"ERROR: could not type into '{label}': {e}"


async def browser_screenshot(session: BrowserSession, llm) -> str:
    if session.page is None:
        return "ERROR: no page loaded yet — use browser_navigate first"
    try:
        png_bytes = await session.page.screenshot(type="png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        response = await llm.ainvoke([
            HumanMessage(content=[
                {"type": "text", "text": BROWSER_SCREENSHOT_DESCRIBE_PROMPT},
                {"type": "image_url", "image_url": f"data:image/png;base64,{b64}"},
            ])
        ])
        description = response.content if hasattr(response, "content") else str(response)
        return f"URL: {session.page.url}\n{description}"
    except Exception as e:
        return f"ERROR: could not capture/describe screenshot: {e}"
