"""The look action against a REAL Chrome or Edge on this computer (saapp_runner/browser.py). Skipped where no browser is installed.

These are the tests that prove the safety claims, not just the plumbing: a page that tries to reach outside the machine is stopped, nothing is left behind, and the answer is bounded.
"""
import base64
import glob
import http.server
import os
import socket
import tempfile
import threading

import pytest

from saapp_runner import browser

FOUND = browser.find_browser()
pytestmark = pytest.mark.skipif(FOUND is None, reason="no Chrome or Edge on this computer")

PAGE = b"""<!doctype html><html><head><title>Look test page</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0"><h1>Hello from the page</h1>
<div id="wide" style="width:600px;height:20px;background:#fcc">a box wider than a phone</div>
<script>console.error("boom from the page"); fetch('/missing.json');</script></body></html>"""

NO_VIEWPORT = b"<!doctype html><html><head><title>No viewport</title></head><body><p>plain</p></body></html>"


class Outside:
    """A server on a non-loopback address of this machine that records every request: a stand-in for the rest of the person's network."""

    def __init__(self, host):
        self.hits = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"secret")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer((host, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def lan_address():
    """An address of this computer that is not loopback, or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            address = sock.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


@pytest.fixture()
def site():
    outside_host = lan_address()
    outside = Outside("0.0.0.0") if outside_host else None
    state = {"outside": outside, "host": outside_host}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/missing"):
                self.send_response(404)
                self.end_headers()
                return
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "http://example.com/")
                self.end_headers()
                return
            body = NO_VIEWPORT if self.path.startswith("/plain") else PAGE
            if self.path.startswith("/leaky") and outside_host:
                body = (f'<!doctype html><title>Leaky</title><body><img src="http://{outside_host}:{outside.port}/leak.png">'
                        f'<script>fetch("http://{outside_host}:{outside.port}/fetch").catch(() => {{}}); try {{ new WebSocket("ws://{outside_host}:{outside.port}/ws"); }} catch (e) {{}}</script>'
                        f'<p>after</p></body>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["port"] = server.server_address[1]
    yield state
    server.shutdown()
    server.server_close()


def test_it_looks_at_a_local_page_at_phone_size_and_reports_what_it_saw(site):
    result = browser.look(f"http://localhost:{site['port']}/", "phone")
    assert result["ok"] is True, result.get("error")
    assert result["title"] == "Look test page" and "Hello from the page" in result["text"] and result["device"] == "phone" and result["browser"] == FOUND["name"]
    assert result["viewport"] == [390, 844] and result["viewport_meta"] is True and result["layout_width"] >= 600
    assert result["overflow_x"] is True and result["wide_elements"] and result["wide_elements"][0]["el"].startswith("div#wide"), "the 600 px box sticks out of a 390 px window"
    assert any("boom from the page" in line for line in result["console"]) and "404 /missing.json" in result["failed"]
    shot = base64.b64decode(result["screenshot_b64"])
    assert shot[:3] == b"\xff\xd8\xff" and 1000 < len(shot) < 700_000 and result["screenshot_bytes"] == len(shot), "a real JPEG, not too big"


def test_the_same_page_at_desktop_size_does_not_overflow(site):
    result = browser.look(f"http://localhost:{site['port']}/", "desktop")
    assert result["ok"] is True and result["viewport"] == [1280, 800] and result["layout_width"] == 1280 and result["overflow_x"] is False and result["wide_elements"] == []


def test_a_mobile_page_with_no_viewport_tag_is_noticed(site):
    result = browser.look(f"http://localhost:{site['port']}/plain", "phone")
    assert result["ok"] is True and result["viewport_meta"] is False and result["layout_width"] > 390, "such a page is laid out at about 980 px and zoomed out"


@pytest.mark.skipif(lan_address() is None, reason="this computer has no non-loopback address to test against")
def test_a_page_cannot_reach_anything_outside_the_machine_by_any_route(site):
    result = browser.look(f"http://localhost:{site['port']}/leaky", "desktop")
    assert result["ok"] is True and "after" in result["text"]
    assert site["outside"].hits == [], "an image, a fetch and a WebSocket to another address of this computer all got nowhere"
    assert site["host"] in result["blocked"]


def test_a_page_that_redirects_off_the_machine_is_not_followed(site):
    result = browser.look(f"http://localhost:{site['port']}/redirect", "desktop")
    assert result["ok"] is False and "could not be opened" in result["error"]


def test_a_port_with_nothing_on_it_says_so_in_words():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    result = browser.look(f"http://localhost:{port}/", "phone")
    assert result["ok"] is False and "Is the server running on that port?" in result["error"] and "screenshot_b64" not in result


def test_an_address_off_the_machine_never_starts_a_browser():
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*")))
    for url in ("http://example.com/", "https://localhost:5173/", "file:///C:/Windows/win.ini"):
        result = browser.look(url, "desktop")
        assert result["ok"] is False and result["error"].startswith("The runner refused it:")
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*"))) == before


def test_nothing_is_left_behind_a_throwaway_profile_that_is_deleted_and_no_browser_running(site):
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*")))
    result = browser.look(f"http://localhost:{site['port']}/", "phone")
    assert result["ok"] is True
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*"))) == before


def test_cancelling_stops_the_look_and_cleans_up(site):
    cancel = threading.Event()
    cancel.set()
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*")))
    result = browser.look(f"http://localhost:{site['port']}/", "phone", cancel=cancel)
    assert result["ok"] is False and "Stopped" in result["error"]
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*"))) == before


def test_a_look_that_runs_out_of_time_is_stopped_and_cleaned_up(site):
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*")))
    result = browser.look(f"http://localhost:{site['port']}/", "phone", timeout=0.05)
    assert result["ok"] is False and result["error"]
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "saapp-look-*"))) == before


def test_no_browser_gives_a_plain_instruction(monkeypatch, site):
    monkeypatch.setattr(browser, "find_browser", lambda *a, **k: None)
    result = browser.look(f"http://localhost:{site['port']}/", "phone")
    assert result["ok"] is False and "No Chrome or Edge was found" in result["error"]
