"""Looks at ONE page on this computer in a hidden browser: the person's own Chrome or Edge, started headless with a throwaway profile, driven over its DevTools port.

WHAT KEEPS IT ON THIS MACHINE (localpage.py has the address rules; this file enforces the rest):
- the browser gets a fresh, empty profile in a temp folder (no cookies, logins, extensions or history) that is deleted afterwards, and no visible window;
- EVERY request the page makes is intercepted (the DevTools Fetch domain) and failed unless it goes to this machine, including redirects, scripts, fonts, images and analytics. As a second wall the browser
  is started with a proxy that goes nowhere and with name lookups turned off, so even a protocol the interception cannot see (a WebSocket to an outside address) has no way out;
- the whole look has a time limit, and the browser and everything it started is killed when it ends, is cancelled or runs out of time;
- what comes back is capped: a JPEG screenshot, the title, the visible text, a few console lines, failed local requests and layout facts.
It reads the page and changes nothing: no clicking, typing or form submitting.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import execute, localpage
from .websocket import WebSocket, WebSocketError

_BROWSER_ENV = "SAAPP_RUNNER_BROWSER"       # the person's own choice of browser program (a Chromium-based one: Brave, Vivaldi), set in their own environment


def find_browser(environ: Optional[Dict[str, str]] = None, exists: Callable[[str], bool] = os.path.isfile, which: Callable[[str], Optional[str]] = shutil.which, system: Optional[str] = None) -> Optional[Dict[str, str]]:
    """The browser the runner would use, {"name": "chrome" | "edge" | "chromium" | "custom", "path"}, or None. Looks at the usual install places (and the PATH on Linux), Chrome first. Pure apart from `exists`/`which`."""
    env = os.environ if environ is None else environ
    platform = system or ("windows" if os.name == "nt" else "darwin" if sys.platform == "darwin" else "linux")
    own = env.get(_BROWSER_ENV, "").strip()
    if own and exists(own):
        return {"name": "custom", "path": own}
    found: List[Dict[str, str]] = []
    if platform == "windows":
        roots = [env.get(key, "") for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        for name, tail in (("chrome", "Google\\Chrome\\Application\\chrome.exe"), ("edge", "Microsoft\\Edge\\Application\\msedge.exe"), ("chromium", "Chromium\\Application\\chrome.exe")):
            found += [{"name": name, "path": os.path.join(root, tail)} for root in roots if root]
    elif platform == "darwin":
        found = [{"name": "chrome", "path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}, {"name": "edge", "path": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"},
                 {"name": "chromium", "path": "/Applications/Chromium.app/Contents/MacOS/Chromium"}]
    else:
        for name, programs in (("chrome", ("google-chrome", "google-chrome-stable")), ("chromium", ("chromium", "chromium-browser")), ("edge", ("microsoft-edge", "microsoft-edge-stable"))):
            found += [{"name": name, "path": path} for path in (which(program) for program in programs) if path]
    return next((item for item in found if exists(item["path"])), None)


class _Page:
    """One DevTools connection to one page, and what it has seen so far."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws, self.counter = ws, 0
        self.console: List[str] = []
        self.failed: List[str] = []
        self.blocked: List[str] = []
        self.loaded = False
        self._urls: Dict[str, str] = {}

    def _send(self, method: str, params: Optional[Dict[str, Any]] = None) -> int:
        self.counter += 1
        self.ws.send(json.dumps({"id": self.counter, "method": method, "params": params or {}}))
        return self.counter

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0, cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        """Sends a command and waits for its answer, handling events that arrive meanwhile. Raises WebSocketError for an error answer or silence."""
        wanted = self._send(method, params)
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                raise WebSocketError("stopped")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WebSocketError(f"the browser did not answer {method} in time.")
            raw = self.ws.receive(min(remaining, 1.0))
            if raw is None:
                continue
            message = json.loads(raw)
            if message.get("id") == wanted:
                if "error" in message:
                    raise WebSocketError(f"{method} failed: {localpage.one_line(message['error'].get('message'), 80)}")
                return message.get("result") or {}
            self.handle(message)

    def pump(self, seconds: float, cancel: Optional[threading.Event] = None, until: Optional[Callable[[], bool]] = None) -> None:
        """Handles events for up to `seconds` (or until `until()` is true)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and not (until and until()):
            if cancel is not None and cancel.is_set():
                raise WebSocketError("stopped")
            raw = self.ws.receive(min(0.25, max(end - time.monotonic(), 0.01)))
            if raw is not None:
                self.handle(json.loads(raw))

    def handle(self, message: Dict[str, Any]) -> None:
        method, params = message.get("method"), message.get("params") or {}
        if method == "Fetch.requestPaused":
            url, request_id = str((params.get("request") or {}).get("url") or ""), params.get("requestId")
            if not request_id:
                return
            if localpage.is_loopback_request(url):
                self._send("Fetch.continueRequest", {"requestId": request_id})
            else:
                self._send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
                host = localpage.blocked_host(url)
                if host not in self.blocked and len(self.blocked) < 8:
                    self.blocked.append(host)
        elif method == "Page.loadEventFired":
            self.loaded = True
        elif method == "Network.requestWillBeSent":
            self._urls[str(params.get("requestId"))] = str((params.get("request") or {}).get("url") or "")
        elif method == "Network.responseReceived":
            response = params.get("response") or {}
            status = response.get("status")
            if isinstance(status, (int, float)) and status >= 400 and localpage.is_loopback_request(str(response.get("url") or "")) and len(self.failed) < localpage.FAILED_LINES:
                self.failed.append(f"{int(status)} {self._path(str(response.get('url') or ''))}")
        elif method == "Network.loadingFailed":
            error = str(params.get("errorText") or "")
            if not error.startswith(("net::ERR_BLOCKED_BY_CLIENT", "net::ERR_ABORTED")) and len(self.failed) < localpage.FAILED_LINES:       # blocked ones are ours (reported as `blocked`); an abort is the page moving on
                self.failed.append(f"failed {self._path(self._urls.get(str(params.get('requestId')), ''))}: {localpage.one_line(params.get('errorText'), 60)}")
        elif method == "Runtime.consoleAPICalled":
            if params.get("type") in ("error", "warning", "assert"):
                text = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in params.get("args") or [] if isinstance(arg, dict))
                self._console(f"{params.get('type')}: {text}")
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            self._console("error: " + str(((details.get("exception") or {}).get("description") or details.get("text") or "an exception")).split("\n")[0])
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") in ("error", "warning") and "ERR_BLOCKED_BY_CLIENT" not in str(entry.get("text")):
                self._console(f"{entry.get('level')}: {entry.get('text')}")

    @staticmethod
    def _path(url: str) -> str:
        try:
            from urllib.parse import urlsplit

            parts = urlsplit(url)
            return localpage.one_line(parts.path or "/", 100)
        except ValueError:
            return "(a request)"

    def _console(self, text: str) -> None:
        if len(self.console) < localpage.CONSOLE_LINES:
            self.console.append(localpage.one_line(text))


def _launch(path: str, profile: Path) -> "subprocess.Popen[bytes]":
    flags = [
        "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--disable-background-networking", "--disable-component-update",
        "--disable-default-apps", "--mute-audio", "--hide-scrollbars", "--disable-breakpad", f"--user-data-dir={profile}", "--remote-debugging-port=0",
        "--proxy-server=socks5://127.0.0.1:9", "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost",      # a second wall: nothing but this machine can be reached, by any protocol
        "about:blank",
    ]
    env = execute.clean_env()
    env.update({key: os.environ[key] for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME") if key in os.environ})
    kwargs: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    kwargs.update(execute.no_window())
    return subprocess.Popen([path, *flags], **kwargs)


def _devtools_port(profile: Path, proc: "subprocess.Popen[bytes]", deadline: float) -> int:
    marker = profile / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise WebSocketError("the browser closed straight away.")
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].isdigit():
                return int(lines[0])
        except OSError:
            pass
        time.sleep(0.1)
    raise WebSocketError("the browser did not start in time.")


def _page_socket(port: int, deadline: float) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))          # never through a proxy from this machine's settings
    while time.monotonic() < deadline:
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=3) as response:
                pages = json.loads(response.read() or b"[]")
            for page in pages:
                if page.get("type") == "page" and str(page.get("webSocketDebuggerUrl") or "").startswith("ws://127.0.0.1:"):
                    return str(page["webSocketDebuggerUrl"])
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise WebSocketError("the browser did not offer a page in time.")


def look(address: str, device: str = "desktop", browser: Optional[Dict[str, str]] = None, wait_ms: int = 800, timeout: float = localpage.LOOK_SECONDS,
         cancel: Optional[threading.Event] = None, port: Optional[int] = None) -> Dict[str, Any]:
    """Opens `address` (checked here with the runner's own rules, and against `port` when the approval covered one server) in a hidden browser at the size of `device`, and returns what it saw:
    {"ok": True, "title", "final_url", "text", "console", "failed", "blocked", "viewport", "scroll_width", "overflow_x", "wide_elements", "screenshot_b64", ...} or {"ok": False, "error"}.
    Never raises."""
    started = time.monotonic()
    result: Dict[str, Any] = {"ok": False, "error": "", "device": "", "browser": ""}
    proc: Optional["subprocess.Popen[bytes]"] = None
    profile = Path(tempfile.mkdtemp(prefix="saapp-look-"))
    page: Optional[_Page] = None
    try:
        try:
            url = localpage.allowed_address(address, port)
            device = localpage.clean_device(device)
        except localpage.LookRefused as problem:
            result["error"] = f"The runner refused it: {problem}"
            return result
        result["device"] = device
        browser = browser or find_browser()
        if not browser:
            result["error"] = "No Chrome or Edge was found on this computer. Install one (or set SAAPP_RUNNER_BROWSER to its program) to let Sonic look at local pages."
            return result
        result["browser"] = browser["name"]
        deadline = started + timeout
        proc = _launch(browser["path"], profile)
        debug_port = _devtools_port(profile, proc, deadline)
        page = _Page(WebSocket(_page_socket(debug_port, deadline), timeout=10))
        width, height, scale, mobile = localpage.DEVICES[device]
        for method, params in (("Page.enable", {}), ("Network.enable", {}), ("Runtime.enable", {}), ("Log.enable", {}), ("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}),
                               ("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": mobile, "screenWidth": width, "screenHeight": height})):
            page.call(method, params, 10, cancel)
        if mobile:
            page.call("Emulation.setUserAgentOverride", {"userAgent": localpage.MOBILE_USER_AGENT}, 10, cancel)
            page.call("Emulation.setTouchEmulationEnabled", {"enabled": True}, 10, cancel)
        navigated = page.call("Page.navigate", {"url": url}, 15, cancel)
        if navigated.get("errorText"):
            result["error"] = f"The page could not be opened ({localpage.one_line(navigated['errorText'], 60)}). Is the server running on that port?"
            return result
        page.pump(max(min(deadline - time.monotonic(), 15.0), 1.0), cancel, until=lambda: page.loaded)
        page.pump(max(min(wait_ms / 1000.0, 5.0), 0.2), cancel)
        evaluated = page.call("Runtime.evaluate", {"expression": localpage.facts_script(width), "returnByValue": True}, 10, cancel)
        facts = localpage.shape_facts((evaluated.get("result") or {}).get("value"), (width, height))
        shot = ""
        for quality in (70, 40):
            shot = str(page.call("Page.captureScreenshot", {"format": "jpeg", "quality": quality}, 15, cancel).get("data") or "")
            if shot and len(shot) <= localpage.SCREENSHOT_B64_CAP:
                break
        if not shot or len(shot) > localpage.SCREENSHOT_B64_CAP:
            result["error"] = "The screenshot could not be made small enough to send."
            return result
        page.pump(0.2, cancel)
        result.update(facts, ok=True, error="", console=list(page.console), failed=list(page.failed), blocked=list(page.blocked), screenshot_b64=shot,
                      screenshot_bytes=len(base64.b64decode(shot)))
    except WebSocketError as problem:
        result["error"] = "Stopped before it finished." if str(problem) == "stopped" else f"The browser could not be driven: {localpage.one_line(problem, 120)}"
    except TimeoutError:
        result["error"] = "The look took too long and was stopped."
    except Exception as problem:                              # never raises into the runner's loop
        result["error"] = f"The look failed ({problem.__class__.__name__})."
    finally:
        if page is not None:
            page.ws.close()
        if proc is not None:
            execute.kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        for _ in range(5):                                    # Windows holds the profile's files for a moment after the browser exits
            shutil.rmtree(profile, ignore_errors=True)
            if not profile.exists():
                break
            time.sleep(0.3)
        result["duration"] = round(time.monotonic() - started, 2)
    return result
