import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[2] / "app.py"


def test_the_login_insert_runs_off_the_event_loop():
    source = APP.read_text(encoding="utf-8")
    start = source.index('@app.post("/api/log-login")')
    handler = source[start: source.index("@app.get", start)]
    assert "await asyncio.to_thread(\n        record_login_event," in handler
    assert not re.search(r"^\s+record_login_event\(", handler, re.M)  # a bare call would block every request
