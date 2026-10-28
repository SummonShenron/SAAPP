import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def runner_home(tmp_path, monkeypatch):
    """Every test gets its own home folder for the runner's key and config, never the real one."""
    home = tmp_path / "runner-home"
    monkeypatch.setenv("SAAPP_RUNNER_HOME", str(home))
    return home


# ---- a small server to talk to ----------------------------------------------------------------------------------------------------------------

class Fake:
    """Answers each (method, path) with a (status, body) and remembers what it was sent."""

    def __init__(self):
        self.routes, self.seen = {}, []

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else None
                outer.seen.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"), "agent": self.headers.get("User-Agent"), "body": body})
                status, payload = outer.routes.get((self.command, self.path.split("?")[0]), (404, {"detail": "no route"}))
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _answer

            def log_message(self, *args):
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake():
    server = Fake().start()
    yield server
    server.stop()
