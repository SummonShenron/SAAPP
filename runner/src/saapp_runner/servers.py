"""Background servers: a project's own dev server (`npm run dev`), started on the person's say-so and kept running so a page can be looked at.

WHY THIS IS NOT A CHECK. A check has a time limit and is killed when it ends; a dev server never ends. So the runner keeps this process itself, one per project, and stops it on request, after it has sat
unused for a while, and when the runner stops.

THE COMMAND COMES FROM THE PROJECT'S OWN FILES, NEVER FROM THE SERVER. `detect` reads `package.json` and offers `npm run dev` (or `npm start`) by NAME; SAAPP can ask for that name and nothing else. It is the same
trust as running the project's tests: it runs the person's own scripts with their own privileges, which is why it is approved on a card first.

NOTHING HERE LISTENS. The runner opens no port. Whether the dev server itself is open to the person's network is the project's own configuration; when its output says so (a "Network:" address, or
0.0.0.0) the result says so too, so the card can warn.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import execute

IDLE_SECONDS = 30 * 60
PORT_WAIT_SECONDS = 60.0
TAIL_CHARS = 4000

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ADDRESS_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\]):(\d{2,5})", re.IGNORECASE)
_PORT_WORDS_RE = re.compile(r"(?:listening on|running on|started on|ready on|port)\D{0,12}(\d{4,5})", re.IGNORECASE)
_EXPOSED_RE = re.compile(r"Network:\s+https?://|0\.0\.0\.0:\d|on all (?:addresses|interfaces)", re.IGNORECASE)


def detect(root: Path) -> Dict[str, List[str]]:
    """The servers this project can start, {name: words}: `dev` for `npm run dev`, else `start` for `npm start`. Nothing is run to find them."""
    try:
        scripts = json.loads((Path(root) / "package.json").read_text(encoding="utf-8", errors="replace")).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(scripts, dict):
        return {}
    if "dev" in scripts:
        return {"dev": ["npm", "run", "dev"]}
    if "start" in scripts:
        return {"start": ["npm", "start"]}
    return {}


def find_port(text: str) -> Optional[int]:
    """The port a server's output says it is on ("Local: http://localhost:5173/", "Running on http://127.0.0.1:5000", "listening on port 3000"), or None. Pure."""
    clean = _ANSI_RE.sub("", text or "")
    match = _ADDRESS_RE.search(clean) or _PORT_WORDS_RE.search(clean)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def says_exposed(text: str) -> bool:
    """Whether the output says the server is reachable from other computers, not only this one. Pure."""
    return bool(_EXPOSED_RE.search(_ANSI_RE.sub("", text or "")))


def accepts_connections(port: int) -> bool:
    """Whether something on this machine accepts a connection on the port (a server may listen on IPv4 or IPv6 only)."""
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


class _Server:
    def __init__(self, name: str, proc: "subprocess.Popen[bytes]") -> None:
        self.name, self.proc, self.port, self.exposed = name, proc, None, False
        self.text, self.lock = "", threading.Lock()
        self.started = self.last_used = time.monotonic()

    def add(self, piece: str) -> None:
        with self.lock:
            self.text = (self.text + piece)[-TAIL_CHARS * 3:]

    def tail(self) -> str:
        with self.lock:
            return _ANSI_RE.sub("", self.text)[-TAIL_CHARS:]


class ServerManager:
    def __init__(self, idle_seconds: float = IDLE_SECONDS, port_wait: float = PORT_WAIT_SECONDS, ready: Callable[[int], bool] = accepts_connections) -> None:
        self.idle_seconds, self.port_wait, self._ready = idle_seconds, port_wait, ready
        self._servers: Dict[str, _Server] = {}
        self._lock = threading.Lock()

    # ---- starting ------------------------------------------------------------------------------------------------------------------------------

    def start(self, project_id: str, root: Path, name: str, words: List[str], env: Optional[Dict[str, str]] = None, cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Starts the server and waits (up to `port_wait` seconds) until it says its port and accepts connections. Returns {"status": "running", "port", "name", "exposed", "output_tail"},
        or {"status": "exited", "exit_code", "output_tail"} when it stopped by itself, {"status": "no_port", "output_tail"} when it never said where it listens (it is stopped), or {"status": "cancelled"}.
        One server per project: a different one already running is stopped first; the same one is returned as it is. Never raises."""
        try:
            with self._lock:
                running = self._servers.get(project_id)
            if running is not None and running.proc.poll() is None:
                if running.name == name and running.port:
                    running.last_used = time.monotonic()
                    return self._running(running)
                self.stop(project_id)
            merged = execute.clean_env(env)
            merged.pop("CI", None)                                   # a dev server run with CI set can treat warnings as errors
            merged.update({"BROWSER": "none", "FORCE_COLOR": "0"})   # and must not open a window of its own
            program = shutil.which(words[0], path=merged.get("PATH")) or words[0]
            kwargs: Dict[str, Any] = {"cwd": str(root), "env": merged, "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            kwargs.update(execute.no_window())
            try:
                proc = subprocess.Popen([program, *words[1:]], **kwargs)
            except FileNotFoundError:
                return {"status": "exited", "exit_code": None, "output_tail": f"command not found: {words[0]}"}
            except OSError as problem:
                return {"status": "exited", "exit_code": None, "output_tail": f"could not start it: {problem.__class__.__name__}"}
            server = _Server(name, proc)

            def read() -> None:
                import codecs

                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    server.add(decoder.decode(chunk))

            threading.Thread(target=read, daemon=True).start()
            with self._lock:
                self._servers[project_id] = server
            deadline = time.monotonic() + self.port_wait
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    self.stop(project_id)
                    return {"status": "cancelled"}
                if proc.poll() is not None:
                    time.sleep(0.2)                                    # let the reader take the last of the output
                    with self._lock:
                        self._servers.pop(project_id, None)
                    return {"status": "exited", "exit_code": proc.returncode, "output_tail": server.tail()}
                port = find_port(server.tail())
                if port and self._ready(port):
                    server.port, server.exposed = port, says_exposed(server.tail())
                    return self._running(server)
                time.sleep(0.25)
            output = server.tail()
            self.stop(project_id)
            return {"status": "no_port", "output_tail": output}
        except Exception as problem:
            return {"status": "exited", "exit_code": None, "output_tail": f"could not start it ({problem.__class__.__name__})"}

    @staticmethod
    def _running(server: _Server) -> Dict[str, Any]:
        return {"status": "running", "name": server.name, "port": server.port, "exposed": server.exposed, "output_tail": server.tail()}

    # ---- asking, using, stopping ---------------------------------------------------------------------------------------------------------------

    def status(self, project_id: str) -> Optional[Dict[str, Any]]:
        """What is running for the project, in the shape `start` answers with, or None. A server that has exited since is forgotten."""
        with self._lock:
            server = self._servers.get(project_id)
        if server is None:
            return None
        if server.proc.poll() is not None:
            with self._lock:
                self._servers.pop(project_id, None)
            return None
        return self._running(server) if server.port else None

    def port_of(self, project_id: str) -> Optional[int]:
        info = self.status(project_id)
        return int(info["port"]) if info else None

    def touch(self, project_id: str) -> None:
        with self._lock:
            server = self._servers.get(project_id)
        if server is not None:
            server.last_used = time.monotonic()

    def stop(self, project_id: str) -> bool:
        """Stops the project's server and everything it started. True when there was one."""
        with self._lock:
            server = self._servers.pop(project_id, None)
        if server is None:
            return False
        execute.kill_tree(server.proc)
        try:
            server.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            execute.kill_tree(server.proc)
        return True

    def stop_all(self) -> int:
        with self._lock:
            ids = list(self._servers)
        return sum(1 for project_id in ids if self.stop(project_id))

    def sweep(self, now: Optional[float] = None) -> List[str]:
        """Stops servers nobody has used for the idle limit, and forgets ones that exited. Returns the project ids it stopped."""
        moment = time.monotonic() if now is None else now
        stopped: List[str] = []
        with self._lock:
            items = list(self._servers.items())
        for project_id, server in items:
            if server.proc.poll() is not None:
                with self._lock:
                    self._servers.pop(project_id, None)
            elif moment - server.last_used > self.idle_seconds:
                if self.stop(project_id):
                    stopped.append(project_id)
        return stopped
