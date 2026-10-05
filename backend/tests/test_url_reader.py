import asyncio
import functools
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import requests

from backend.services import url_reader as ur


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


PUBLIC_IP = "93.184.216.34"
ARTICLE_WORDS = "Retries with exponential backoff keep a flaky dependency from taking the whole service down. " * 12
ARTICLE_HTML = (
    "<html><head><title>Backoff guide</title></head><body>"
    "<nav><a href='/'>Home</a> <a href='/docs'>Docs</a></nav>"
    f"<article><h1>Backoff guide</h1><p>{ARTICLE_WORDS}</p><p>{ARTICLE_WORDS}</p></article>"
    "<footer>Copyright nobody</footer></body></html>"
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Every hostname resolves to one public address unless a test overrides it, so nothing here
    touches the real network. IP-literal URLs resolve to themselves, as in real life."""
    def fake_getaddrinfo(host, port, *args, **kwargs):
        try:
            import ipaddress
            ipaddress.ip_address(host)
            address = host
        except ValueError:
            address = PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
    monkeypatch.setattr(ur.socket, "getaddrinfo", fake_getaddrinfo)


class FakeResponse:
    def __init__(self, status=200, body=b"", content_type="text/html; charset=utf-8", headers=None, encoding="utf-8"):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.encoding = encoding
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        self.closed = True


def serve(monkeypatch, responses):
    """Maps URL -> FakeResponse (or an exception to raise); records every URL actually requested."""
    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        assert kwargs.get("allow_redirects") is False, "redirects must be followed by hand so each hop is checked"
        result = responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ur.requests, "get", fake_get)
    return requested


# ---------------------------------------------------------------------------
# check_url_allowed: this server fetches a URL the model chose, so it must not be steerable into
# reading its own network.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.0.0.1:8000/api/me", "http://[::1]/", "http://10.1.2.3/",
    "http://192.168.0.10/admin", "http://172.16.5.5/", "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0/", "http://100.64.0.1/", "http://[fd00::1]/",
])
def test_non_public_addresses_are_refused(url):
    assert ur.check_url_allowed(url) is not None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "javascript:alert(1)", "http://"])
def test_non_http_schemes_and_hostless_urls_are_refused(url):
    assert ur.check_url_allowed(url) is not None


def test_a_public_host_is_allowed():
    assert ur.check_url_allowed("https://example.com/docs") is None
    assert ur.check_url_allowed(f"http://{PUBLIC_IP}/") is None


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(ur.socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, 1, 6, "", ("10.0.0.7", port))])
    assert ur.check_url_allowed("https://innocent-looking.example.com/") is not None


def test_a_name_with_any_private_address_among_its_records_is_refused(monkeypatch):
    monkeypatch.setattr(ur.socket, "getaddrinfo", lambda host, port, *a, **k: [
        (socket.AF_INET, 1, 6, "", (PUBLIC_IP, port)), (socket.AF_INET, 1, 6, "", ("127.0.0.1", port)),
    ])
    assert ur.check_url_allowed("https://rebind.example.com/") is not None


def test_an_unresolvable_host_is_refused(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(ur.socket, "getaddrinfo", boom)
    assert "resolve" in ur.check_url_allowed("https://does-not-exist.invalid/")


# ---------------------------------------------------------------------------
# try_lightweight_fetch
# ---------------------------------------------------------------------------

def test_lightweight_fetch_extracts_the_main_text_and_drops_page_chrome(monkeypatch):
    serve(monkeypatch, {"https://example.com/guide": FakeResponse(body=ARTICLE_HTML)})
    result = ur.try_lightweight_fetch("https://example.com/guide")
    assert result.error is None
    assert "exponential backoff" in result.text
    assert "Copyright nobody" not in result.text
    assert result.final_url == "https://example.com/guide"


def test_utf8_pages_without_a_declared_charset_are_not_turned_into_mojibake(monkeypatch):
    # requests reports ISO-8859-1 for text/html with no charset; the page's real bytes are UTF-8.
    html = ARTICLE_HTML.replace("Backoff guide</h1>", "Backoff guide¶ café</h1>").replace("Retries", "Rétries", 1)
    serve(monkeypatch, {"https://example.com/u": FakeResponse(body=html.encode("utf-8"), content_type="text/html", encoding="ISO-8859-1")})
    text = ur.try_lightweight_fetch("https://example.com/u").text
    assert "¶" in text and "café" in text
    assert "Â" not in text


def test_a_declared_charset_is_honoured(monkeypatch):
    body = "<html><body><article><p>" + ("Café au lait est une boisson chaude. " * 20) + "</p></article></body></html>"
    serve(monkeypatch, {"https://example.com/l": FakeResponse(body=body.encode("latin-1"), content_type="text/html; charset=iso-8859-1")})
    assert "Café au lait" in ur.try_lightweight_fetch("https://example.com/l").text


def test_an_unknown_declared_charset_does_not_crash(monkeypatch):
    serve(monkeypatch, {"https://example.com/z": FakeResponse(body=ARTICLE_HTML, content_type="text/html; charset=martian-9")})
    assert "exponential backoff" in ur.try_lightweight_fetch("https://example.com/z").text


def test_lightweight_fetch_follows_a_redirect_and_reports_the_final_url(monkeypatch):
    serve(monkeypatch, {
        "https://example.com/old": FakeResponse(301, headers={"Location": "/new"}),
        "https://example.com/new": FakeResponse(body=ARTICLE_HTML),
    })
    result = ur.try_lightweight_fetch("https://example.com/old")
    assert result.final_url == "https://example.com/new"
    assert "exponential backoff" in result.text


def test_a_redirect_into_a_private_address_is_refused_and_never_requested(monkeypatch):
    requested = serve(monkeypatch, {
        "https://example.com/go": FakeResponse(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}),
    })
    result = ur.try_lightweight_fetch("https://example.com/go")
    assert result.text is None and result.definitive
    assert requested == ["https://example.com/go"]


def test_a_redirect_loop_gives_up(monkeypatch):
    serve(monkeypatch, {"https://example.com/loop": FakeResponse(302, headers={"Location": "https://example.com/loop"})})
    result = ur.try_lightweight_fetch("https://example.com/loop")
    assert result.text is None and result.definitive and "redirect" in result.error


def test_a_404_is_definitive_but_other_failures_are_worth_a_browser_try(monkeypatch):
    serve(monkeypatch, {
        "https://example.com/gone": FakeResponse(404),
        "https://example.com/blocked": FakeResponse(403),
        "https://example.com/boom": requests.ConnectionError("reset"),
    })
    assert ur.try_lightweight_fetch("https://example.com/gone").definitive is True
    forbidden = ur.try_lightweight_fetch("https://example.com/blocked")
    assert forbidden.text is None and forbidden.definitive is False and "403" in forbidden.error
    failed = ur.try_lightweight_fetch("https://example.com/boom")
    assert failed.text is None and failed.definitive is False


def test_a_non_text_file_is_reported_not_decoded(monkeypatch):
    serve(monkeypatch, {"https://example.com/x.pdf": FakeResponse(body=b"%PDF-1.7", content_type="application/pdf")})
    result = ur.try_lightweight_fetch("https://example.com/x.pdf")
    assert result.text is None and result.definitive and "application/pdf" in result.error


def test_plain_text_and_json_are_returned_as_they_are(monkeypatch):
    serve(monkeypatch, {
        "https://example.com/data.json": FakeResponse(body='{"a": 1}', content_type="application/json"),
        "https://example.com/notes.txt": FakeResponse(body="  plain notes  ", content_type="text/plain"),
    })
    assert ur.try_lightweight_fetch("https://example.com/data.json").text == '{"a": 1}'
    assert ur.try_lightweight_fetch("https://example.com/notes.txt").text == "plain notes"


def test_the_download_is_capped(monkeypatch):
    class Endless(FakeResponse):
        def iter_content(self, chunk_size=1):
            while True:
                yield b"a" * chunk_size

    serve(monkeypatch, {"https://example.com/big": Endless(content_type="text/plain")})
    result = ur.try_lightweight_fetch("https://example.com/big")
    assert len(result.text) == ur._MAX_DOWNLOAD_BYTES


def test_responses_are_closed_even_when_followed_or_rejected(monkeypatch):
    first = FakeResponse(302, headers={"Location": "/b"})
    second = FakeResponse(404)
    serve(monkeypatch, {"https://example.com/a": first, "https://example.com/b": second})
    ur.try_lightweight_fetch("https://example.com/a")
    assert first.closed and second.closed


def test_without_trafilatura_the_lightweight_path_yields_no_text_instead_of_crashing(monkeypatch):
    monkeypatch.setitem(sys.modules, "trafilatura", None)  # makes `import trafilatura` raise ImportError
    serve(monkeypatch, {"https://example.com/guide": FakeResponse(body=ARTICLE_HTML)})
    result = ur.try_lightweight_fetch("https://example.com/guide")
    assert result.text == "" and result.definitive is False


# ---------------------------------------------------------------------------
# read_url
# ---------------------------------------------------------------------------

def _browser(monkeypatch, *, navigate="Navigated to https://example.com/app (status 200). Page title: 'App'",
             read="URL: https://example.com/app\nRendered text of the app"):
    nav = AsyncMock(return_value=navigate)
    rd = AsyncMock(return_value=read)
    monkeypatch.setattr(ur, "browser_navigate", nav)
    monkeypatch.setattr(ur, "browser_read_text", rd)
    return nav, rd


@run_async
async def test_read_url_fast_path_never_touches_the_browser(monkeypatch):
    serve(monkeypatch, {"https://example.com/guide": FakeResponse(body=ARTICLE_HTML)})
    nav, rd = _browser(monkeypatch)
    get_session = AsyncMock()

    result = await ur.read_url("https://example.com/guide", get_session, asyncio.Lock())

    assert "exponential backoff" in result
    assert result.startswith("URL: https://example.com/guide\n")
    assert "untrusted text from the web" in result
    get_session.assert_not_awaited()
    nav.assert_not_awaited()


@run_async
async def test_read_url_adds_a_scheme_when_none_is_given(monkeypatch):
    requested = serve(monkeypatch, {"https://example.com/guide": FakeResponse(body=ARTICLE_HTML)})
    _browser(monkeypatch)
    await ur.read_url("example.com/guide", AsyncMock(), asyncio.Lock())
    assert requested == ["https://example.com/guide"]


@run_async
async def test_read_url_without_a_url_is_an_error(monkeypatch):
    assert (await ur.read_url(None, AsyncMock(), asyncio.Lock())).startswith("ERROR")
    assert (await ur.read_url("   ", AsyncMock(), asyncio.Lock())).startswith("ERROR")


@run_async
async def test_read_url_refuses_an_internal_address_without_any_fetch_or_browser(monkeypatch):
    requested = serve(monkeypatch, {})
    nav, _ = _browser(monkeypatch)
    get_session = AsyncMock()

    result = await ur.read_url("http://169.254.169.254/latest/meta-data/", get_session, asyncio.Lock())

    assert result.startswith("ERROR")
    assert requested == []
    get_session.assert_not_awaited()
    nav.assert_not_awaited()


@run_async
async def test_a_thin_page_falls_back_to_a_real_browser_session(monkeypatch):
    serve(monkeypatch, {"https://example.com/app": FakeResponse(body="<html><body><div id='root'></div></body></html>")})
    nav, rd = _browser(monkeypatch)
    session = SimpleNamespace(live_url=None)
    get_session = AsyncMock(return_value=session)

    result = await ur.read_url("https://example.com/app", get_session, asyncio.Lock())

    nav.assert_awaited_once_with(session, "https://example.com/app")
    rd.assert_awaited_once_with(session)
    assert "Rendered text of the app" in result
    assert result.startswith("URL: https://example.com/app\n")
    assert "untrusted text from the web" in result


@run_async
async def test_the_browser_fallback_announces_the_live_view_hook_once_it_is_ready(monkeypatch):
    serve(monkeypatch, {"https://example.com/app": FakeResponse(body="<html></html>")})
    _browser(monkeypatch)
    session = SimpleNamespace(live_url="https://live.example/xyz")
    ready = AsyncMock()

    await ur.read_url("https://example.com/app", AsyncMock(return_value=session), asyncio.Lock(), ready)

    ready.assert_awaited_once_with(session)


@run_async
async def test_a_blocked_page_is_retried_in_a_browser(monkeypatch):
    serve(monkeypatch, {"https://example.com/app": FakeResponse(403)})
    nav, _ = _browser(monkeypatch)
    result = await ur.read_url("https://example.com/app", AsyncMock(return_value=SimpleNamespace(live_url=None)), asyncio.Lock())
    nav.assert_awaited_once()
    assert "Rendered text of the app" in result


@run_async
async def test_a_404_is_reported_without_spending_a_browser_session(monkeypatch):
    serve(monkeypatch, {"https://example.com/gone": FakeResponse(404)})
    nav, _ = _browser(monkeypatch)
    get_session = AsyncMock()

    result = await ur.read_url("https://example.com/gone", get_session, asyncio.Lock())

    assert result.startswith("ERROR") and "404" in result
    get_session.assert_not_awaited()
    nav.assert_not_awaited()


@run_async
async def test_with_no_browser_configured_a_short_real_page_is_still_returned(monkeypatch):
    short = "<html><body><article><p>Opening hours: 9 to 5, Monday to Friday. Closed on public holidays.</p></article></body></html>"
    serve(monkeypatch, {"https://example.com/hours": FakeResponse(body=short)})
    _browser(monkeypatch)
    get_session = AsyncMock(side_effect=RuntimeError("BROWSERLESS_WS_ENDPOINT is not configured"))

    result = await ur.read_url("https://example.com/hours", get_session, asyncio.Lock())

    assert "Opening hours" in result
    assert not result.startswith("ERROR")
    assert "short amount of text" in result


@run_async
async def test_with_no_browser_and_no_text_the_result_is_an_error(monkeypatch):
    serve(monkeypatch, {"https://example.com/app": FakeResponse(body="<html><body></body></html>")})
    _browser(monkeypatch)
    get_session = AsyncMock(side_effect=RuntimeError("not configured"))

    result = await ur.read_url("https://example.com/app", get_session, asyncio.Lock())

    assert result.startswith("ERROR: could not read https://example.com/app")


@run_async
async def test_a_failed_browser_navigation_is_an_error_not_a_read_of_a_stale_page(monkeypatch):
    serve(monkeypatch, {"https://example.com/app": FakeResponse(body="<html></html>")})
    nav, rd = _browser(monkeypatch, navigate="ERROR: timed out loading https://example.com/app")

    result = await ur.read_url("https://example.com/app", AsyncMock(return_value=SimpleNamespace(live_url=None)), asyncio.Lock())

    assert result.startswith("ERROR")
    rd.assert_not_awaited()


# ---------------------------------------------------------------------------
# The concurrency decision: read_url is batchable. Plain fetches are stateless and must stay
# parallel; only the browser fallback — the one shared, stateful session — is serialized.
# ---------------------------------------------------------------------------

@run_async
async def test_concurrent_browser_fallbacks_take_turns_and_never_read_each_others_pages(monkeypatch):
    serve(monkeypatch, {
        "https://example.com/a": FakeResponse(body="<html></html>"),
        "https://example.com/b": FakeResponse(body="<html></html>"),
        "https://example.com/c": FakeResponse(body="<html></html>"),
    })
    state = {"page": None, "in_flight": 0, "max_in_flight": 0, "events": []}

    async def fake_navigate(session, url):
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        await asyncio.sleep(0.01)
        state["page"] = url
        state["events"].append(("navigate", url))
        return f"Navigated to {url} (status 200)."

    async def fake_read(session):
        await asyncio.sleep(0.01)
        page = state["page"]
        state["events"].append(("read", page))
        state["in_flight"] -= 1
        return f"URL: {page}\ncontent of {page}"

    monkeypatch.setattr(ur, "browser_navigate", fake_navigate)
    monkeypatch.setattr(ur, "browser_read_text", fake_read)
    session = SimpleNamespace(live_url=None)
    lock = asyncio.Lock()

    results = await asyncio.gather(*(
        ur.read_url(f"https://example.com/{name}", AsyncMock(return_value=session), lock) for name in "abc"
    ))

    assert state["max_in_flight"] == 1
    for name, result in zip("abc", results):
        assert f"content of https://example.com/{name}" in result
    # Each navigate is immediately followed by its own read: the pair is one unit under the lock.
    assert [e[0] for e in state["events"]] == ["navigate", "read"] * 3
    for i in range(0, 6, 2):
        assert state["events"][i][1] == state["events"][i + 1][1]


@run_async
async def test_fast_path_reads_do_not_wait_on_the_browser_lock(monkeypatch):
    serve(monkeypatch, {
        f"https://example.com/{n}": FakeResponse(body=ARTICLE_HTML) for n in "abc"
    })
    _browser(monkeypatch)
    lock = asyncio.Lock()
    await lock.acquire()  # a browser fallback is mid-flight elsewhere

    results = await asyncio.wait_for(
        asyncio.gather(*(ur.read_url(f"https://example.com/{n}", AsyncMock(), lock) for n in "abc")),
        timeout=5,
    )

    assert all("exponential backoff" in r for r in results)
    assert lock.locked()


@run_async
async def test_a_browser_crash_is_an_error_observation_and_releases_the_lock(monkeypatch):
    serve(monkeypatch, {"https://example.com/a": FakeResponse(body="<html></html>")})
    monkeypatch.setattr(ur, "browser_navigate", AsyncMock(side_effect=ValueError("driver crashed")))
    lock = asyncio.Lock()

    result = await ur.read_url("https://example.com/a", AsyncMock(return_value=SimpleNamespace(live_url=None)), lock)

    assert result.startswith("ERROR")
    assert not lock.locked()


# ---------------------------------------------------------------------------
# A real failure, seen in production logs: a client-rendered job page (Eightfold-style) hides a ~115k
# character JSON theme config in a <code> element and ships its real job posting only as JSON-LD.
# trafilatura returned the config, which cleared the length check, so the agent was handed a theme
# file instead of the job description, and then fell through to a browser step.
# ---------------------------------------------------------------------------

import json as _json

JOB_DESCRIPTION_HTML = (
    "<p>Join Acme &mdash; powering the future of widgets.</p><p>You will build AI tools that help our "
    "analysts and own the quality of our data.</p><ul><li>3+ years of Python</li><li>Experience with "
    "LLM applications</li></ul>"
)


def _spa_job_page(blob_entries=4000):
    config = {"themeOptions": {"name": "Default", "customTheme": {"varTheme": {f"color-{i}": f"#{i:06x}" for i in range(blob_entries)}}}}
    posting = {
        "@context": "http://schema.org", "@type": "JobPosting", "title": "AI Solutions Developer",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Des Moines", "addressRegion": "IA", "addressCountry": "US"}}],
        "employmentType": "FULL_TIME", "datePosted": "2026-10-02", "description": JOB_DESCRIPTION_HTML,
    }
    return (
        "<html><head><title>Job</title>"
        f"<script type=\"application/ld+json\">{_json.dumps(posting)}</script></head>"
        f"<body><div id=\"app\"></div><code id=\"branding-data\" style=\"display: none;\">{_json.dumps(config)}</code></body></html>"
    )


def test_a_json_config_blob_is_not_prose_but_real_pages_are():
    assert ur.looks_like_prose(ARTICLE_WORDS) is True
    assert ur.looks_like_prose(_json.dumps({"a": [1, 2, 3], "b": {"c": "d"}} | {f"k{i}": f"v{i}" for i in range(500)})) is False
    assert ur.looks_like_prose("[1, 2, 3]") is False
    assert ur.looks_like_prose("   ") is False
    # Code-heavy documentation must not be mistaken for a data blob.
    docs = ("The json module encodes Python objects. For example, json.dumps({'a': [1, 2]}) returns a string, "
            "and json.loads('[1, 2, 3]') parses one back. Use indent=2 for readable output. " * 8)
    assert ur.looks_like_prose(docs) is True


def test_the_job_posting_is_read_from_json_ld_instead_of_the_hidden_config(monkeypatch):
    serve(monkeypatch, {"https://jobs.example.com/1": FakeResponse(body=_spa_job_page())})
    text = ur.try_lightweight_fetch("https://jobs.example.com/1").text
    assert "themeOptions" not in text and "color-" not in text
    assert "Title: AI Solutions Developer" in text
    assert "Organization: Acme" in text
    assert "Location: Des Moines, IA, US" in text
    assert "Employment type: FULL_TIME" in text
    assert "Join Acme \u2014 powering the future of widgets." in text  # entities decoded
    assert "- 3+ years of Python" in text and "- Experience with LLM applications" in text  # list kept readable
    assert "<p>" not in text and "<li>" not in text


@run_async
async def test_read_url_returns_the_job_posting_without_opening_a_browser(monkeypatch):
    serve(monkeypatch, {"https://jobs.example.com/1": FakeResponse(body=_spa_job_page())})
    nav, _ = _browser(monkeypatch)
    get_session = AsyncMock()

    result = await ur.read_url("https://jobs.example.com/1", get_session, asyncio.Lock())

    assert "AI Solutions Developer" in result and "3+ years of Python" in result
    assert "themeOptions" not in result
    get_session.assert_not_awaited()
    nav.assert_not_awaited()


def test_a_page_whose_only_text_is_a_data_blob_reads_as_empty_so_a_browser_gets_its_turn(monkeypatch):
    config_only = f"<html><body><code>{_json.dumps({f'k{i}': f'v{i}' for i in range(2000)})}</code></body></html>"
    serve(monkeypatch, {"https://jobs.example.com/2": FakeResponse(body=config_only)})
    assert ur.try_lightweight_fetch("https://jobs.example.com/2").text == ""


def test_json_ld_handles_graphs_lists_escaped_markup_and_ignores_junk():
    body = "Escaped markup is common in JSON-LD descriptions. " * 4
    page = (
        "<script type='application/ld+json'>{not valid json</script>"
        "<script type='application/ld+json'>" + _json.dumps({"@graph": [
            {"@type": "WebSite", "name": "Site"},  # no body text: ignored
            {"@type": "Article", "headline": "Guide", "author": {"name": "Pat"}, "articleBody": "&lt;p&gt;" + body + "&lt;/p&gt;"},
        ]}) + "</script>"
        "<script type='application/ld+json'>" + _json.dumps([{"@type": "Product", "name": "Widget", "description": "tiny"}]) + "</script>"
    )
    text = ur.structured_data_text(page)
    assert "Title: Guide" in text and "Organization: Pat" in text
    assert "&lt;" not in text and "<p>" not in text and body.strip() in text
    assert "Widget" not in text and "Site" not in text


def test_structured_data_does_not_replace_a_long_real_article(monkeypatch):
    ld = {"@type": "Article", "headline": "Short summary", "description": "A short summary of the article that is long enough to count as a body of text here."}
    page = ARTICLE_HTML.replace("</head>", f"<script type=\"application/ld+json\">{_json.dumps(ld)}</script></head>")
    serve(monkeypatch, {"https://example.com/g": FakeResponse(body=page)})
    text = ur.try_lightweight_fetch("https://example.com/g").text
    assert "exponential backoff" in text and "Short summary" not in text
