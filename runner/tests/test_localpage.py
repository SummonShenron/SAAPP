"""The rules for which pages the runner will open, and what it takes from a page (saapp_runner/localpage.py). No browser is started here."""
import json

import pytest

from saapp_runner import localpage as lp


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:5173/", ("localhost", 5173, "/")),
    ("http://localhost:5173", ("localhost", 5173, "/")),
    ("http://127.0.0.1:3000/app/page?x=1&y=two", ("127.0.0.1", 3000, "/app/page?x=1&y=two")),
    ("http://[::1]:8000/a", ("::1", 8000, "/a")),
    ("HTTP://LOCALHOST:80/Mixed/Case", ("localhost", 80, "/Mixed/Case")),
    ("http://localhost/", ("localhost", 80, "/")),
    ("http://localhost:5173/#/route", ("localhost", 5173, "/")),                       # a fragment never reaches the server, so it is dropped
])
def test_an_address_on_this_machine_is_read_into_host_port_and_path(url, expected):
    assert lp.parse_address(url) == expected


@pytest.mark.parametrize("url", [
    "", None, "   ", "localhost:5173", "https://localhost:5173/", "file:///C:/Windows/win.ini", "chrome://settings", "data:text/html,<script>1</script>", "javascript:alert(1)",
    "view-source:http://localhost:5173/", "ftp://localhost/", "ws://localhost:5173/",
    "http://example.com/", "http://localhost.evil.com:80/", "http://127.0.0.1.nip.io:80/", "http://evil.com/localhost", "http://192.168.1.5:3000/", "http://10.0.0.2/", "http://0.0.0.0:3000/",
    "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]:80/", "http://localhost@evil.com/", "http://user:pass@localhost:3000/", "http://localhost:3000@evil.com/",
    "http://evil.com\\@localhost:3000/", "http://localhost:99999/", "http://localhost:0/", "http://localhost:abc/", "http://localhost:3000/a b", "http://localhost:3000/\x00",
    "http://localhost:3000/a\\b", "http://localhost:3000/<script>", "http://localhost:3000/a?q=\"x\"", "http://localhost:3000/" + "a" * 500,
])
def test_anything_else_is_refused(url):
    with pytest.raises(lp.LookRefused):
        lp.parse_address(url)


def test_the_address_to_open_is_rebuilt_from_its_parts_so_nothing_odd_survives():
    assert lp.allowed_address("http://LOCALHOST:5173/x?y=1#frag") == "http://localhost:5173/x?y=1"
    assert lp.allowed_address("http://[::1]:8000/") == "http://[::1]:8000/"
    assert lp.build_address("127.0.0.1", 3000, "/") == "http://127.0.0.1:3000/"


def test_an_approval_that_covers_one_servers_port_refuses_every_other_port():
    assert lp.allowed_address("http://localhost:5173/a", 5173) == "http://localhost:5173/a"
    with pytest.raises(lp.LookRefused, match="port 5173"):
        lp.allowed_address("http://localhost:8000/", 5173)
    with pytest.raises(lp.LookRefused):
        lp.allowed_address("http://example.com:5173/", 5173)


def test_the_device_is_one_of_three_and_has_a_size():
    assert lp.clean_device("Phone") == "phone" and lp.clean_device(None) == "desktop" and lp.clean_device("") == "desktop"
    for bad in ("watch", "../phone", "phone; rm", 5):
        with pytest.raises(lp.LookRefused):
            lp.clean_device(bad)
    assert lp.DEVICES["phone"][:2] == (390, 844) and lp.DEVICES["tablet"][:2] == (820, 1180) and lp.DEVICES["desktop"][:2] == (1280, 800)
    assert lp.DEVICES["phone"][3] is True and lp.DEVICES["desktop"][3] is False


@pytest.mark.parametrize("url, stays", [
    ("http://localhost:5173/src/main.tsx", True), ("http://127.0.0.1:8000/api", True), ("http://[::1]:3000/", True), ("ws://localhost:5173/?token=x", True), ("https://localhost:5173/", True),
    ("data:image/png;base64,AAAA", True), ("blob:http://localhost:5173/abc", True), ("about:blank", True),
    ("https://fonts.googleapis.com/css", False), ("http://192.168.1.5/", False), ("http://169.254.169.254/", False), ("wss://example.com/socket", False), ("http://localhost.evil.com/", False),
    ("http://127.0.0.1.nip.io/", False), ("file:///etc/passwd", False), ("ftp://localhost/", False), ("chrome://version", False), ("", False),
])
def test_a_request_the_page_makes_may_only_stay_on_this_machine(url, stays):
    assert lp.is_loopback_request(url) is stays


def test_a_blocked_request_is_reported_by_host_only():
    assert lp.blocked_host("https://fonts.googleapis.com/css?family=x&secret=1") == "fonts.googleapis.com"
    assert lp.blocked_host("") == "unknown" and len(lp.blocked_host("http://" + "a" * 200 + ".com/")) <= 80


def test_what_the_page_says_about_itself_is_bounded_and_odd_answers_become_empty():
    raw = json.dumps({"title": "T" * 500, "url": "http://localhost:5173/", "text": "line" + "\n" * 5 + "next " + "x" * 9000, "layoutWidth": 600, "scrollWidth": 600, "scrollHeight": 3000,
                      "wide": [{"el": "div#banner.a.b", "right": 600, "width": 580}] * 20 + ["junk", {"right": "x"}], "viewportMeta": True})
    facts = lp.shape_facts(raw, (390, 844))
    assert len(facts["title"]) <= 120 and len(facts["text"]) == lp.TEXT_CHARS and facts["text_cut"] is True and "\n" * 3 not in facts["text"]
    assert facts["viewport"] == [390, 844] and facts["layout_width"] == 600 and facts["scroll_width"] == 600 and facts["overflow_x"] is True
    assert len(facts["wide_elements"]) == lp.WIDE_ELEMENTS and facts["viewport_meta"] is True
    quiet = lp.shape_facts(json.dumps({"layoutWidth": 1280, "scrollWidth": 1280, "text": "hi"}), (1280, 800))
    assert quiet["overflow_x"] is False and quiet["text"] == "hi" and quiet["viewport_meta"] is False and quiet["viewport"] == [1280, 800]
    for junk in (None, "", "not json", "[]", "5", json.dumps({"layoutWidth": "wide", "scrollWidth": "x", "wide": "no"})):
        empty = lp.shape_facts(junk, (390, 844))
        assert empty["text"] == "" and empty["overflow_x"] is False and empty["wide_elements"] == []


def test_the_script_is_measured_against_the_devices_width_not_the_pages_grown_layout_width():
    assert "const w = 390," in lp.facts_script(390) and "__DEVICE_WIDTH__" not in lp.facts_script(1280)
    assert lp.facts_script(390).count("390") == 1, "only a whole number is ever put into the script"


def test_the_script_the_runner_runs_in_the_page_only_reads_it():
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "eval(", "localStorage", "document.cookie", ".click(", ".submit(", "innerHTML =", "location.href =", "window.open"):
        assert forbidden not in lp.FACTS_SCRIPT


@pytest.mark.parametrize("code, must, must_not", [
    ("net::ERR_CONNECTION_REFUSED", "nothing answered on that port", "fault in the look"),
    ("net::ERR_CONNECTION_RESET", "nothing answered on that port", "fault in the look"),
    ("net::ERR_NAME_NOT_RESOLVED", "fault in the look, not in the person's setup", "Is the server running"),
    ("net::ERR_BLOCKED_BY_CLIENT", "outside this computer", "Is the server running"),
    ("net::ERR_UNSAFE_PORT", "choose another port", "Is the server running"),
    ("net::ERR_CONNECTION_TIMED_OUT", "did not answer in time", "fault in the look"),
    ("net::ERR_SOMETHING_NEW", "may not be running on that port", "fault in the look"),
    (None, "may not be running on that port", "fault in the look"),
])
def test_each_way_a_page_fails_to_open_is_said_for_what_it_is_and_a_lookup_failure_is_not_blamed_on_the_persons_setup(code, must, must_not):
    message = lp.open_error(code)
    assert must in message and must_not not in message and message.startswith("The page could not be opened")
    assert len(message) < 400
