"""What the runner will open in a browser, and what it takes from the page. All of it is rules and data: no browser is started here (browser.py does that).

THE ADDRESS IS DECIDED HERE, ON THIS MACHINE, NEVER BY THE SERVER. A look names a page; the runner checks the address with these rules again, even when the server handed it out:
- only `http` (not file:, chrome:, data:, javascript:, view-source:, ftp: or anything else);
- only this machine: `localhost`, `127.0.0.1` or `[::1]`, exactly (not `localhost.evil.com`, not `127.0.0.1.nip.io`, not a LAN address like 192.168.1.5);
- no user name or password in the address, a real port from 1 to 65535, an ordinary path and query, no control characters, and a sane length.
When the person's approval covered a particular server (a preview session), the port must also be that server's.
"""
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
MAX_URL_CHARS = 400
_PATH_RE = re.compile(r"^/[A-Za-z0-9/_.~%:@!$&'()*+,;=\-]*$")
_QUERY_RE = re.compile(r"^[A-Za-z0-9/_.~%:@!$&'()*+,;=?\-]*$")

# (width, height, device scale factor, mobile): what the browser pretends to be. `phone` is a current mid-size phone, `tablet` a portrait tablet, `desktop` a laptop window.
DEVICES: Dict[str, Tuple[int, int, float, bool]] = {"phone": (390, 844, 2.0, True), "tablet": (820, 1180, 2.0, True), "desktop": (1280, 800, 1.0, False)}
MOBILE_USER_AGENT = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"

TEXT_CHARS = 6000
CONSOLE_LINES = 10
LINE_CHARS = 200
FAILED_LINES = 10
WIDE_ELEMENTS = 6
SCREENSHOT_B64_CAP = 900_000          # about 650 KB of image; a bigger one is taken again smaller
LOOK_SECONDS = 30.0                   # the whole look, start to finish


class LookRefused(Exception):
    """The address or the device is not one the runner will open."""


def clean_device(name: Any) -> str:
    device = str(name or "desktop").strip().lower()
    if device not in DEVICES:
        raise LookRefused(f"the device must be one of {', '.join(DEVICES)}.")
    return device


def parse_address(url: Any) -> Tuple[str, int, str]:
    """(host, port, path with query) for an address this runner may open, or a LookRefused saying why not. Pure."""
    text = str(url or "").strip()
    if not text or len(text) > MAX_URL_CHARS:
        raise LookRefused("the address is empty or too long.")
    if any(ord(ch) < 33 or ord(ch) == 127 for ch in text):
        raise LookRefused("the address has spaces or control characters in it.")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise LookRefused("the address could not be read.") from None
    if parts.scheme != "http":
        raise LookRefused("only http addresses are opened (not file:, chrome:, data:, javascript: or others).")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise LookRefused("an address with a user name or password in it is not opened.")
    host = (parts.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise LookRefused("only pages on this computer are opened: localhost, 127.0.0.1 or [::1].")
    if port is None:
        port = 80
    if not 1 <= port <= 65535:
        raise LookRefused("the port must be from 1 to 65535.")
    path = parts.path or "/"
    if not _PATH_RE.match(path) or not _QUERY_RE.match(parts.query or ""):
        raise LookRefused("the path has characters that are not allowed.")
    return host, port, path + (f"?{parts.query}" if parts.query else "")


def build_address(host: str, port: int, path: str) -> str:
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}{path}"


def allowed_address(url: Any, port: Optional[int] = None) -> str:
    """The address to open, rebuilt from its parts, or a LookRefused. With `port` (the managed server's), any other port is refused. Pure."""
    host, found, path = parse_address(url)
    if port is not None and found != port:
        raise LookRefused(f"the approval covers the server on port {port}, not port {found}.")
    return build_address(host, found, path)


def is_loopback_request(url: str) -> bool:
    """Whether a request the page makes stays on this machine: an http(s) or ws(s) address whose host is loopback, or a page's own data:, blob: or about: content. Anything else is failed. Pure."""
    text = str(url or "")
    lowered = text.lower()
    if lowered.startswith(("data:", "blob:", "about:")):
        return True
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return parts.scheme in ("http", "https", "ws", "wss") and host in LOOPBACK_HOSTS


def blocked_host(url: str) -> str:
    """The host of a request that was failed, for the report (just the host: no path, no query). Pure."""
    try:
        return (urlsplit(str(url or "")).hostname or "unknown")[:80]
    except ValueError:
        return "unknown"


def one_line(text: Any, limit: int = LINE_CHARS) -> str:
    flat = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


# What the runner asks the page for once it has loaded. A fixed script, never anything the server sent. It reads the page; it changes nothing.
FACTS_SCRIPT = """(() => {
  const w = __DEVICE_WIDTH__, layout = window.innerWidth, doc = document.documentElement, body = document.body || doc;
  const wide = [];
  for (const el of body.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.right > w + 1) {
      const name = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.classList.length ? '.' + Array.from(el.classList).slice(0, 2).join('.') : '');
      wide.push({ el: name.slice(0, 80), right: Math.round(r.right), width: Math.round(r.width) });
      if (wide.length >= 40) break;
    }
  }
  wide.sort((a, b) => b.right - a.right);
  return JSON.stringify({
    title: document.title || '', url: location.href, text: (body.innerText || '').slice(0, 20000),
    layoutWidth: layout, scrollWidth: Math.max(doc.scrollWidth, body.scrollWidth), scrollHeight: Math.max(doc.scrollHeight, body.scrollHeight), wide: wide.slice(0, 12),
    viewportMeta: !!document.querySelector('meta[name=viewport]'),
  });
})()"""


def facts_script(device_width: int) -> str:
    """FACTS_SCRIPT for a window `device_width` pixels wide. The width is the DEVICE's, not the page's `innerWidth`: a mobile browser grows its layout width to fit content that sticks out, so
    `innerWidth` would hide exactly the overflow being looked for. Pure."""
    return FACTS_SCRIPT.replace("__DEVICE_WIDTH__", str(int(device_width)))


def shape_facts(raw: Any, device_size: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """The page's own answer to FACTS_SCRIPT as a bounded dict. `viewport` is the emulated window (`device_size`); `layout_width` is what the page laid itself out at, which differs on a mobile browser
    for a page with no viewport tag (980) or one that sticks out. Anything unexpected becomes empty rather than an error. Pure."""
    import json

    try:
        data = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    width, height = device_size if device_size else (0, 0)
    layout = int(data["layoutWidth"]) if isinstance(data.get("layoutWidth"), (int, float)) else 0
    scroll_width = int(data.get("scrollWidth") or 0) if isinstance(data.get("scrollWidth"), (int, float)) else 0
    wide: List[Dict[str, Any]] = []
    for item in data.get("wide") or []:
        if isinstance(item, dict) and isinstance(item.get("right"), (int, float)):
            wide.append({"el": one_line(item.get("el"), 80), "right": int(item["right"]), "width": int(item.get("width") or 0)})
    text = re.sub(r"\n{3,}", "\n\n", str(data.get("text") or "")).strip()
    return {
        "title": one_line(data.get("title"), 120), "final_url": one_line(data.get("url"), MAX_URL_CHARS), "text": text[:TEXT_CHARS], "text_cut": len(text) > TEXT_CHARS,
        "viewport": [width, height], "layout_width": layout, "scroll_width": scroll_width,
        "scroll_height": int(data.get("scrollHeight") or 0) if isinstance(data.get("scrollHeight"), (int, float)) else 0,
        "overflow_x": bool(width and scroll_width > width + 1), "wide_elements": wide[:WIDE_ELEMENTS], "viewport_meta": data.get("viewportMeta") is True,
    }


def open_error(error_text: Any) -> str:
    """What to tell the person (and the model) when the browser could not open the page, by the browser's own error code. Each reason is said as what it is: a closed port is the person's server, but a failed name
    lookup or a blocked redirect is the look's own doing and must not be blamed on their setup (an early version told everyone "is the server running?" even for a lookup failure the runner itself caused). Pure."""
    code = one_line(error_text, 60)
    if "ERR_CONNECTION_REFUSED" in code or "ERR_CONNECTION_RESET" in code or "ERR_EMPTY_RESPONSE" in code:
        return f"The page could not be opened: nothing answered on that port ({code}). Is the server running there?"
    if "ERR_NAME_NOT_RESOLVED" in code or "ERR_ADDRESS_INVALID" in code:
        return f"The page could not be opened: the look's own browser could not look the address up ({code}). That is a fault in the look, not in the person's setup; do not blame their machine, and say so plainly."
    if "ERR_BLOCKED_BY_CLIENT" in code:
        return f"The page could not be opened ({code}): it sent the browser somewhere outside this computer, which a look never follows."
    if "ERR_UNSAFE_PORT" in code:
        return f"The page could not be opened ({code}): browsers refuse that port number outright, so choose another port for the server."
    if "ERR_TIMED_OUT" in code or "ERR_CONNECTION_TIMED_OUT" in code:
        return f"The page could not be opened ({code}): the server did not answer in time."
    return f"The page could not be opened ({code}). The server may not be running on that port, or the address may be wrong."
