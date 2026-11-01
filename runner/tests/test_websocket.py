"""The small WebSocket client (saapp_runner/websocket.py), against a hand-made server."""
import base64
import hashlib
import socket
import struct
import threading

import pytest

from saapp_runner.websocket import WebSocket, WebSocketError

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _frame(opcode, payload, final=True):
    head = bytes([(0x80 if final else 0) | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([size])
    elif size < 65536:
        head += bytes([126]) + struct.pack(">H", size)
    else:
        head += bytes([127]) + struct.pack(">Q", size)
    return head + payload


def _read(conn, count):
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _read_client_frame(conn):
    first, second = _read(conn, 2)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack(">H", _read(conn, 2))[0]
    elif size == 127:
        size = struct.unpack(">Q", _read(conn, 8))[0]
    assert second & 0x80, "a client frame must be masked"
    mask = _read(conn, 4)
    body = bytes(b ^ mask[i % 4] for i, b in enumerate(_read(conn, size)))
    return first & 0x0F, body


class Server:
    """Accepts one connection, does the handshake, then runs `script(conn)`."""

    def __init__(self, script, accept_ok=True):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script, self.accept_ok, self.requests = script, accept_ok, []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        conn, _ = self.sock.accept()
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(1024)
            self.requests.append(head.decode("latin-1"))
            key = [line.split(": ", 1)[1] for line in head.decode("latin-1").split("\r\n") if line.lower().startswith("sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            if not self.accept_ok:
                accept = "wrong"
            conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
            self.script(conn)
        except (EOFError, OSError):
            pass
        finally:
            conn.close()
            self.sock.close()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/devtools/page/ABC?x=1"


def test_it_does_the_handshake_and_sends_masked_text_that_the_server_can_read():
    seen = []

    def script(conn):
        opcode, body = _read_client_frame(conn)
        seen.append((opcode, body))
        conn.sendall(_frame(1, b"pong " + body))

    server = Server(script)
    ws = WebSocket(server.url)
    ws.send('{"id":1,"method":"Page.enable"}')
    assert ws.receive(3) == 'pong {"id":1,"method":"Page.enable"}'
    ws.close()
    assert seen == [(1, b'{"id":1,"method":"Page.enable"}')]
    request = server.requests[0]
    assert request.startswith("GET /devtools/page/ABC?x=1 HTTP/1.1") and "Upgrade: websocket" in request and "Sec-WebSocket-Version: 13" in request


@pytest.mark.parametrize("size", [0, 5, 125, 126, 1000, 65535, 65536, 200_000])
def test_messages_of_every_size_class_go_both_ways(size):
    text = "é" * (size // 2) + "x" * (size % 2)
    got = []

    def script(conn):
        opcode, body = _read_client_frame(conn)
        got.append(body)
        conn.sendall(_frame(1, body))

    ws = WebSocket(Server(script).url)
    ws.send(text)
    assert ws.receive(5) == text and got[0].decode() == text
    ws.close()


def test_a_message_split_into_pieces_is_put_back_together_and_pings_are_answered():
    pongs = []

    def script(conn):
        conn.sendall(_frame(1, b"hel", final=False) + _frame(0, b"lo ", final=False) + _frame(0, b"world", final=True))
        conn.sendall(_frame(9, b"are you there"))
        pongs.append(_read_client_frame(conn))
        conn.sendall(_frame(1, b"after the ping"))

    ws = WebSocket(Server(script).url)
    assert ws.receive(3) == "hello world"
    assert ws.receive(3) == "after the ping"
    assert pongs == [(0xA, b"are you there")]
    ws.close()


def test_receive_returns_none_when_nothing_comes_and_the_connection_stays_usable():
    release = threading.Event()

    def script(conn):
        release.wait(5)
        conn.sendall(_frame(1, b"late"))

    ws = WebSocket(Server(script).url)
    assert ws.receive(0.3) is None
    release.set()
    assert ws.receive(3) == "late"
    ws.close()


def test_a_close_from_the_browser_ends_the_connection_with_an_error():
    def script(conn):
        conn.sendall(_frame(8, b""))

    ws = WebSocket(Server(script).url)
    with pytest.raises(WebSocketError, match="closed"):
        ws.receive(3)
    assert ws.closed is True


def test_a_wrong_handshake_answer_is_refused():
    with pytest.raises(WebSocketError, match="handshake"):
        WebSocket(Server(lambda conn: None, accept_ok=False).url)


def test_only_this_machine_is_ever_connected_to():
    for url in ("ws://example.com:9222/devtools/page/1", "ws://192.168.1.5:9222/x", "wss://127.0.0.1:9222/x", "http://127.0.0.1:9222/", "ws://127.0.0.1/x", "ws://localhost.evil.com:9222/x"):
        with pytest.raises(WebSocketError, match="127.0.0.1"):
            WebSocket(url)


def test_a_port_nothing_listens_on_is_a_plain_error():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(WebSocketError, match="could not connect"):
        WebSocket(f"ws://127.0.0.1:{port}/x", timeout=2)
