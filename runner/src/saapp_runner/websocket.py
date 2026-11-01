"""A very small WebSocket client, only enough to talk to a browser's DevTools port on THIS machine (browser.py).

The standard library has no WebSocket, and the runner uses nothing else, so this is the few things the DevTools protocol needs and nothing more: the opening handshake, text frames in both directions
(the client's frames are masked, as the protocol requires), fragmented messages, ping and close. It only connects to 127.0.0.1 or localhost: a DevTools address is never anywhere else, so any other host
is refused before a socket is opened.
"""
import base64
import hashlib
import os
import socket
import struct
import time
from typing import Optional
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    """The connection failed, was refused, or broke."""


def _read_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    data = b""
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out")
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(count - len(data))
        except socket.timeout:
            raise TimeoutError("timed out") from None
        except OSError as problem:
            raise WebSocketError(f"the connection broke: {problem.__class__.__name__}") from None
        if not chunk:
            raise WebSocketError("the connection was closed")
        data += chunk
    return data


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parts = urlsplit(url)
        if parts.scheme != "ws" or parts.hostname not in ("127.0.0.1", "localhost") or not parts.port:
            raise WebSocketError("only ws://127.0.0.1:<port> addresses are allowed here.")
        self.url, self.closed = url, False
        try:
            self.sock = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        except OSError as problem:
            raise WebSocketError(f"could not connect: {problem.__class__.__name__}") from None
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        request = f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        self.sock.sendall(request.encode("ascii"))
        deadline = time.monotonic() + timeout
        head = b""
        while b"\r\n\r\n" not in head:
            head += _read_exact(self.sock, 1, deadline)
            if len(head) > 8192:
                raise WebSocketError("the answer to the handshake was too long.")
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WebSocketError(f"the browser refused the connection ({lines[0][:60]}).")
        headers = {line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip() for line in lines[1:] if ":" in line}
        expected = base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketError("the handshake answer was not the one expected.")

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        try:
            self.sock.sendall(bytes(header) + masked)
        except OSError as problem:
            raise WebSocketError(f"could not send: {problem.__class__.__name__}") from None

    def receive(self, timeout: float = 10.0) -> Optional[str]:
        """The next text message, or None when none arrived in `timeout` seconds. Pings are answered and ignored; a close ends the connection (WebSocketError)."""
        deadline = time.monotonic() + timeout
        message = b""
        while True:
            try:
                first, second = _read_exact(self.sock, 2, deadline)
            except TimeoutError:
                return None
            final, opcode, masked, size = bool(first & 0x80), first & 0x0F, bool(second & 0x80), second & 0x7F
            try:
                if size == 126:
                    size = struct.unpack(">H", _read_exact(self.sock, 2, deadline + 30))[0]
                elif size == 127:
                    size = struct.unpack(">Q", _read_exact(self.sock, 8, deadline + 30))[0]
                if size > 64 * 1024 * 1024:
                    raise WebSocketError("a message was far too large.")
                mask = _read_exact(self.sock, 4, deadline + 30) if masked else b""
                body = _read_exact(self.sock, size, deadline + 30) if size else b""
            except TimeoutError:
                raise WebSocketError("the browser stopped in the middle of a message.") from None
            if masked:
                body = bytes(byte ^ mask[index % 4] for index, byte in enumerate(body))
            if opcode == 0x8:
                self.closed = True
                raise WebSocketError("the browser closed the connection.")
            if opcode == 0x9:
                self._control(0xA, body)
                continue
            if opcode == 0xA:
                continue
            message += body
            if final:
                return message.decode("utf-8", errors="replace")

    def _control(self, opcode: int, body: bytes) -> None:
        mask = os.urandom(4)
        try:
            self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(body)]) + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(body)))
        except OSError:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._control(0x8, b"")
        finally:
            try:
                self.sock.close()
            except OSError:
                pass
