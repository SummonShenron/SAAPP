"""SAAPP as an MCP client: Sonic can use tools offered by an MCP server (the first one: Google's Maps tools, for places, distances and routes).

MCP (the Model Context Protocol) is JSON-RPC 2.0 between a client that hosts a model and a server that offers tools. This is the client side over the "Streamable HTTP" transport, written against the specification
with `requests` (already a dependency) so there is nothing new to install. The server side of the same protocol, over stdio, is runner/src/saapp_runner/mcp_server.py.

  POST <url>  initialize {protocolVersion, capabilities, clientInfo}   ->  {protocolVersion, capabilities, serverInfo}   (+ a Mcp-Session-Id header, when the server keeps a session)
  POST <url>  notifications/initialized                                ->  202 Accepted
  POST <url>  tools/list                                               ->  {tools: [{name, description, inputSchema, ...}]}
  POST <url>  tools/call {name, arguments}                             ->  {content: [{type: "text", text}], isError}
  Every POST sends `Accept: application/json, text/event-stream`: the answer is either one JSON body or a stream of server-sent events that carries it. A session that expired (404) is started again once.

WE DO NOT HAND OVER CONTROL OF THE PROMPT. A server decides what its tools are called, how long their descriptions are and what they return, and all of that would land in Sonic's prompt on every call. So:
- SERVERS ARE CONFIGURED BY US (McpServerConfig), never by a user or a model: a fixed https address (no redirects followed), a header for a key read from the environment, and an ALLOWLIST of tools.
- EACH ALLOWED TOOL IS CURATED (ToolPolicy): our own description, only the arguments we list (anything else the model sends is dropped), a cap on how much of the result is kept, a daily limit. What the server says about its own tools is read but never shown to the model.
- TOOLS ARE ATTACHED ONLY WHEN A TURN NEEDS THEM (backend/utils/place_questions.py decides), so no turn pays for tool descriptions it will not use.
- RESULTS ARE UNTRUSTED TEXT (reviews and summaries are written by other people): the tool agent treats them as data, and nothing here logs a request or a result.
The key never leaves this module except in the request header; errors name the kind of failure, never what was sent or received."""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit

import requests

logger = logging.getLogger("SASS Logger")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "sonic-assistant", "title": "Sonic Assistant", "version": "1.0"}
TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "20"))
MAX_BODY_BYTES = 1_000_000                    # how much of one answer is read at all
DEFAULT_RESULT_CHARS = 3000                   # how much of a result text a tool keeps unless its policy says otherwise


class McpError(Exception):
    """A problem talking to an MCP server, said in words that name the kind of failure and never what was sent or received."""


@dataclass
class ToolPolicy:
    """What Sonic may do with one tool of one server, in our words."""
    description: str                                   # shown to the model instead of the server's own
    arguments: Dict[str, Dict[str, Any]]               # the arguments the model may give: {name: JSON schema of that argument}; anything else is dropped
    required: Tuple[str, ...] = ()
    max_result_chars: int = DEFAULT_RESULT_CHARS
    daily_limit: int = 100                             # calls per person per day (counted in memory, per process: a safety valve, not billing)
    server_name: Optional[str] = None                  # the tool's name on the server, when it differs from the name Sonic uses
    # How OUR tool becomes the server's, and what comes back, so the model sees a small interface and a small result instead of the server's: `build` turns the (already checked) arguments into the
    # server's; `render` turns the result text into ours (when it fails the result is kept as it was, then capped); `execute` replaces the single server call altogether (for a tool that makes several,
    # with no model step in between).
    build: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    render: Optional[Callable[[str], str]] = None
    execute: Optional[Callable[["McpHttpClient", Dict[str, Any]], Tuple[str, bool]]] = None


@dataclass
class McpServerConfig:
    name: str                                          # short and fixed: it becomes part of the tool names Sonic sees (mcp__<name>__<tool>)
    url: str
    key_header: str = ""                               # the header the key goes in, for example X-Goog-Api-Key
    key_env: str = ""                                  # the environment variable the key is read from (never a key in code or in a log)
    tools: Dict[str, ToolPolicy] = field(default_factory=dict)

    def key(self) -> str:
        return os.getenv(self.key_env, "").strip() if self.key_env else ""

    def configured(self) -> bool:
        return bool(self.url) and bool(self.tools) and (not self.key_env or bool(self.key()))


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise McpError("An MCP server address must be https.")
    if not parts.hostname or parts.username or parts.password:
        raise McpError("That is not an address an MCP server can have.")


def _events(response: "requests.Response") -> Iterator[str]:
    """The `data` of each server-sent event in a streamed answer, joined across its lines. Reads at most MAX_BODY_BYTES."""
    seen, buffer = 0, []
    for raw in response.iter_lines(decode_unicode=True):
        seen += len(raw or "") + 1
        if seen > MAX_BODY_BYTES:
            raise McpError("The server's answer was too large.")
        line = raw if isinstance(raw, str) else (raw or b"").decode("utf-8", errors="replace")
        if line == "":
            if buffer:
                yield "\n".join(buffer)
                buffer = []
        elif line.startswith("data:"):
            buffer.append(line[5:].lstrip(" "))
    if buffer:
        yield "\n".join(buffer)


def _read_capped(response: "requests.Response") -> bytes:
    body = b""
    for chunk in response.iter_content(chunk_size=8192):
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise McpError("The server's answer was too large.")
    return body


class McpHttpClient:
    """One connection to one server. Thread safe enough for one call at a time (calls take a lock); a session is started on first use and again if the server says it expired."""

    def __init__(self, config: McpServerConfig, session: Optional["requests.Session"] = None) -> None:
        _check_url(config.url)
        self.config = config
        self._http = session or requests.Session()
        self._session_id: Optional[str] = None
        self._protocol = PROTOCOL_VERSION
        self._ready = False
        self._last_session_header: Optional[str] = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_tools: List[Dict[str, Any]] = []

    # ---- one round trip -----------------------------------------------------------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": self._protocol}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.config.key_header and self.config.key():
            headers[self.config.key_header] = self.config.key()
        return headers

    def _post(self, message: Dict[str, Any], expect_reply: bool) -> Optional[Dict[str, Any]]:
        try:
            response = self._http.post(self.config.url, data=json.dumps(message), headers=self._headers(), timeout=TIMEOUT_SECONDS, stream=True, allow_redirects=False)
        except requests.Timeout:
            raise McpError("The MCP server did not answer in time.") from None
        except requests.RequestException:
            raise McpError("Could not reach the MCP server.") from None
        try:
            if response.status_code == 404 and self._session_id:
                self._ready, self._session_id = False, None
                raise _SessionExpired()
            if 300 <= response.status_code < 400:
                raise McpError("The MCP server tried to redirect, which is not followed.")
            if response.status_code in (401, 403):
                raise McpError("The MCP server refused the key (it may be wrong, or the service is not enabled for it).")
            if response.status_code == 429:
                raise McpError("The MCP server is rate limiting.")
            if response.status_code >= 400:
                raise McpError(f"The MCP server answered an error ({response.status_code}).")
            self._last_session_header = response.headers.get("Mcp-Session-Id") or None
            if not expect_reply:
                return None
            kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if kind == "text/event-stream":
                for data in _events(response):
                    try:
                        parsed = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(parsed, dict) and parsed.get("id") == message.get("id") and ("result" in parsed or "error" in parsed):
                        return self._reply(parsed)
                raise McpError("The MCP server's stream ended without an answer.")
            try:
                parsed = json.loads(_read_capped(response))
            except ValueError:
                raise McpError("The MCP server did not send JSON.") from None
            return self._reply(parsed if isinstance(parsed, dict) else {})
        finally:
            response.close()

    @staticmethod
    def _reply(parsed: Dict[str, Any]) -> Dict[str, Any]:
        if "error" in parsed:
            error = parsed.get("error") if isinstance(parsed.get("error"), dict) else {}
            raise McpError(f"The MCP server answered an error (code {error.get('code')}).")
        result = parsed.get("result")
        if not isinstance(result, dict):
            raise McpError("The MCP server's answer had no result.")
        return result

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        message: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        return self._post(message, True) or {}

    # ---- the session --------------------------------------------------------------------------------------------------------------------------

    def _start(self) -> None:
        """Starts a session: initialize, remember the session id and the protocol version the server chose, then tell it we are ready."""
        self._ready, self._session_id, self._protocol = False, None, PROTOCOL_VERSION
        result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO})
        version = result.get("protocolVersion")
        if not isinstance(version, str) or not version:
            raise McpError("The MCP server did not say which protocol version it speaks.")
        self._session_id, self._protocol = self._last_session_header, version
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, False)
        self._ready = True
        logger.info("[mcp] %s: session started (protocol %s).", self.config.name, version)

    def _with_session(self, work):
        """Runs `work` with a started session; when the server says the session expired, starts a new one and runs it once more."""
        for attempt in (1, 2):
            try:
                if not self._ready:
                    self._start()                                  # inside the try: a server can also end the session it just began
                return work()
            except _SessionExpired:
                if attempt == 2:
                    raise McpError("The MCP server kept ending the session.") from None
        raise McpError("The MCP server could not be used.")

    # ---- what the server offers and what Sonic does with it ---------------------------------------------------------------------------------------

    def list_tools(self) -> List[Dict[str, Any]]:
        """The server's own list of its tools (read, never shown to the model: see ToolPolicy)."""
        with self._lock:
            result = self._with_session(lambda: self._request("tools/list"))
            tools = result.get("tools")
            self.server_tools = [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)] if isinstance(tools, list) else []
            return self.server_tools

    def call_tool(self, server_tool: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """Calls one tool and returns (text, is_error): the text parts of the result joined, and the server's own isError flag. Raises McpError when the call itself could not be made."""
        with self._lock:
            result = self._with_session(lambda: self._request("tools/call", {"name": server_tool, "arguments": arguments}))
        content = result.get("content")
        parts = [str(item.get("text")) for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)] if isinstance(content, list) else []
        return "\n".join(parts), bool(result.get("isError"))


class _SessionExpired(Exception):
    pass


# ---- what the tool agent uses ---------------------------------------------------------------------------------------------------------------------

def tool_name(server: McpServerConfig, tool: str) -> str:
    return f"mcp__{server.name}__{tool}"


def split_tool_name(name: str) -> Optional[Tuple[str, str]]:
    parts = str(name).split("__", 2)
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "mcp" and parts[1] and parts[2] else None


def declaration(server: McpServerConfig, tool: str) -> Dict[str, Any]:
    """The function declaration the model sees for a curated tool: OUR description and only the arguments we allow."""
    policy = server.tools[tool]
    schema: Dict[str, Any] = {"type": "object", "properties": dict(policy.arguments)}
    if policy.required:
        schema["required"] = list(policy.required)
    return {"name": tool_name(server, tool), "description": policy.description, "parameters": schema}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def clean_arguments(policy: ToolPolicy, arguments: Any) -> Dict[str, Any]:
    """Only the arguments the policy lists, each checked against its declared type (and enum, when it has one). Raises McpError naming what is wrong, so the model can fix the call."""
    if not isinstance(arguments, dict):
        raise McpError("The arguments must be an object.")
    clean: Dict[str, Any] = {}
    for name, schema in policy.arguments.items():
        if name not in arguments or arguments[name] is None:
            continue
        value = arguments[name]
        expected = _TYPES.get(str(schema.get("type")))
        if expected and (not isinstance(value, expected) or (isinstance(value, bool) and schema.get("type") in ("integer", "number"))):
            raise McpError(f"The argument {name} must be a {schema.get('type')}.")
        if "enum" in schema and value not in schema["enum"]:
            same = [option for option in schema["enum"] if isinstance(option, str) and isinstance(value, str) and option.lower() == value.strip().lower()]
            if not same:
                raise McpError(f"The argument {name} must be one of: {', '.join(map(str, schema['enum']))}.")
            value = same[0]                                                       # a model writes "walk" for "WALK": the same choice, in the spelling the service wants
        if isinstance(value, str) and len(value) > 500:
            raise McpError(f"The argument {name} is too long.")
        if isinstance(value, list):
            if len(value) > int(schema.get("maxItems", 20)):
                raise McpError(f"The argument {name} has too many items (at most {schema.get('maxItems', 20)}).")
            item_type = _TYPES.get(str((schema.get("items") or {}).get("type")))
            for item in value:
                if item_type and not isinstance(item, item_type):
                    raise McpError(f"The items of {name} must be {(schema.get('items') or {}).get('type')}s.")
                if isinstance(item, str) and len(item) > 300:
                    raise McpError(f"An item of {name} is too long.")
        clean[name] = value
    missing = [name for name in policy.required if name not in clean]
    if missing:
        raise McpError(f"Missing: {', '.join(missing)}.")
    return clean


_calls: Dict[Tuple[str, str, str], int] = {}
_calls_lock = threading.Lock()


def _day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def within_limit(username: str, name: str, limit: int) -> bool:
    """Counts one call by this person to this tool today and says whether it is still within the daily limit. In memory (a safety valve against a loop or a bad day, not billing)."""
    key = (_day(), username, name)
    with _calls_lock:
        for old in [k for k in _calls if k[0] != key[0]]:
            _calls.pop(old, None)
        _calls[key] = _calls.get(key, 0) + 1
        return _calls[key] <= limit


def forget_counts() -> None:
    with _calls_lock:
        _calls.clear()


_clients: Dict[str, McpHttpClient] = {}


def client_for(server: McpServerConfig) -> McpHttpClient:
    if server.name not in _clients or _clients[server.name].config is not server:
        _clients[server.name] = McpHttpClient(server)
    return _clients[server.name]


def call(server: McpServerConfig, tool: str, arguments: Any, username: str = "") -> str:
    """One curated tool call, for the tool agent: checks the tool and its arguments, applies the daily limit, calls the server, and returns the result text capped at the policy's size. Failures come back as text
    that begins with ERROR (the same convention as every other tool), never raised: a failed call is something the model reads and reports."""
    policy = server.tools.get(tool)
    if policy is None:
        return f"ERROR: {tool} is not an available tool."
    if not server.configured():
        return "ERROR: that service is not set up on this server."
    try:
        clean = clean_arguments(policy, arguments)
    except McpError as problem:
        return f"ERROR: {problem}"
    if not within_limit(username or "?", tool_name(server, tool), policy.daily_limit):
        logger.info("[mcp] %s: %s reached its daily limit for a person.", server.name, tool)
        return f"ERROR: the daily limit for {tool} was reached; try again tomorrow."
    try:
        client = client_for(server)
        if policy.execute is not None:
            text, failed = policy.execute(client, clean)
        else:
            text, failed = client.call_tool(policy.server_name or tool, policy.build(clean) if policy.build else clean)
    except McpError as problem:
        logger.info("[mcp] %s: %s did not complete: %s", server.name, tool, problem)           # our own sentence naming the kind of failure, never what was sent or received
        return f"ERROR: {problem}"
    except Exception:
        logger.warning("[mcp] a call to %s failed unexpectedly.", server.name)
        return "ERROR: the service could not be used right now."
    text = text.strip()
    if failed:
        return "ERROR: " + (text[:300] or "the service reported a problem.")
    if policy.render is not None and text:
        try:
            text = policy.render(text).strip() or text
        except Exception:
            pass                                                                   # keep what the server said (it is capped below) rather than lose the answer
    if not text:
        return "The service returned nothing for that."
    return text if len(text) <= policy.max_result_chars else text[: policy.max_result_chars].rstrip() + f"\n[cut: only the first {policy.max_result_chars} characters are kept]"
