# CUI // SP-CTI
"""cdp-port-01 — stdlib RFC 6455 WebSocket client for loopback CDP.

Covers the two details the spike (docs/spikes/cdp-00-*) flagged as biting in
practice: multi-MB payloads that force the 64-bit length path + the partial-recv
loop, and control frames (ping/close) interleaved with data frames. Integration
tests run against a real threaded loopback WebSocket echo server, so the opening
handshake, masking, and framing are exercised end-to-end — not mocked.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

import pytest

from tools.browser.cdp.ws_client import (
    WSOpcode,
    WebSocketError,
    connect,
    encode_frame,
)

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ── A minimal, correct RFC 6455 echo server (test double for the browser) ─────


class _MiniWSServer:
    """Loopback WebSocket server: handshakes, then echoes each data frame back
    UNMASKED (as a real server must). Optionally injects ping/close frames.

    ``inject`` runs once after the handshake, before echoing, receiving the raw
    accepted socket — used to send a ping or an oversized frame.
    """

    def __init__(self, inject=None, bad_accept: bool = False):
        self._inject = inject
        self._bad_accept = bad_accept
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/TEST"

    def start(self) -> "_MiniWSServer":
        self._thread.start()
        return self

    def _serve(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                req += chunk
            key = ""
            for line in req.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1((key + _WS_GUID).encode()).digest()  # noqa: S324
            ).decode()
            if self._bad_accept:
                accept = "wrong-accept-token"
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode("ascii")
            )
            if self._bad_accept:
                return
            if self._inject is not None:
                self._inject(conn)
            self._echo_loop(conn)

    def _echo_loop(self, conn):
        try:
            while True:
                frame = _read_client_frame(conn)
                if frame is None or frame[0] == WSOpcode.CLOSE:
                    return
                opcode, payload = frame
                # Echo back UNMASKED, as a real server does.
                conn.sendall(encode_frame(opcode, payload, mask=False))
        except OSError:
            return


def _read_client_frame(conn):
    """Read one masked client frame; return (opcode, payload) or None on EOF."""
    hdr = _recv_exact(conn, 2)
    if hdr is None:
        return None
    b0, b1 = hdr
    opcode = WSOpcode(b0 & 0x0F)
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        (length,) = struct.unpack("!H", _recv_exact(conn, 2))
    elif length == 127:
        (length,) = struct.unpack("!Q", _recv_exact(conn, 8))
    mask_key = _recv_exact(conn, 4) if masked else b""
    payload = _recv_exact(conn, length) if length else b""
    if masked and payload:
        payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
    return opcode, bytes(payload or b"")


def _recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


# ── Codec unit tests (no socket) ──────────────────────────────────────────────


def test_encode_masks_client_frames_by_default():
    frame = encode_frame(WSOpcode.TEXT, b"hi")
    assert frame[0] == 0x81  # FIN + TEXT
    assert frame[1] & 0x80  # mask bit set
    assert (frame[1] & 0x7F) == 2  # length


def test_encode_length_forms_select_correctly():
    # 7-bit
    assert (encode_frame(WSOpcode.BINARY, b"x" * 10, mask=False)[1] & 0x7F) == 10
    # 16-bit marker
    f16 = encode_frame(WSOpcode.BINARY, b"x" * 200, mask=False)
    assert (f16[1] & 0x7F) == 126
    assert struct.unpack("!H", f16[2:4])[0] == 200
    # 64-bit marker (>= 65536) — the screenshot path
    big = b"x" * 70000
    f64 = encode_frame(WSOpcode.BINARY, big, mask=False)
    assert (f64[1] & 0x7F) == 127
    assert struct.unpack("!Q", f64[2:10])[0] == 70000


def test_masking_is_reversible_xor():
    # 100 bytes stays in the 7-bit length form, so the mask key is at [2:6] and
    # the masked body at [6:]. (Larger payloads shift these offsets — the
    # round-trip integration tests cover those paths through the real decoder.)
    payload = bytes(range(100))
    frame = encode_frame(WSOpcode.TEXT, payload, mask=True)
    assert (frame[1] & 0x7F) == 100  # 7-bit length form
    mask_key = frame[2:6]
    masked_body = frame[6:]
    assert masked_body != payload  # actually masked
    recovered = bytes(b ^ mask_key[i & 3] for i, b in enumerate(masked_body))
    assert recovered == payload


# ── Integration tests (real loopback server) ──────────────────────────────────


def test_handshake_and_text_roundtrip():
    srv = _MiniWSServer().start()
    with connect(srv.url, timeout=5) as ws:
        ws.send_text('{"id":1,"method":"Target.getTargets"}')
        assert ws.recv_text() == '{"id":1,"method":"Target.getTargets"}'


def test_large_payload_forces_64bit_and_partial_recv():
    """A ~2 MB payload (base64 screenshot scale) must round-trip — exercises the
    64-bit length branch and the partial-recv assembly loop for real."""
    srv = _MiniWSServer().start()
    big = base64.b64encode(b"\x89PNG" + b"\x00" * (2 * 1024 * 1024)).decode()
    with connect(srv.url, timeout=15) as ws:
        ws.send_text(big)
        got = ws.recv_text()
    assert got == big
    assert len(big) > 65536  # genuinely on the 64-bit path


def test_ping_is_answered_and_hidden_from_caller():
    def inject(conn):
        # Server sends an unsolicited ping before any echo; caller must not see it.
        conn.sendall(encode_frame(WSOpcode.PING, b"ka", mask=False))

    srv = _MiniWSServer(inject=inject).start()
    with connect(srv.url, timeout=5) as ws:
        ws.send_text("after-ping")
        # recv_text transparently handles the injected PING (answering PONG) and
        # only surfaces the echoed data frame to the caller.
        assert ws.recv_text() == "after-ping"


def test_peer_close_raises():
    def inject(conn):
        conn.sendall(encode_frame(WSOpcode.CLOSE, struct.pack("!H", 1000), mask=False))

    srv = _MiniWSServer(inject=inject).start()
    with connect(srv.url, timeout=5) as ws:
        with pytest.raises(WebSocketError):
            ws.recv_text()


def test_handshake_accept_mismatch_rejected():
    srv = _MiniWSServer(bad_accept=True).start()
    with pytest.raises(WebSocketError):
        connect(srv.url, timeout=5)


def test_only_ws_scheme_supported():
    with pytest.raises(WebSocketError):
        connect("wss://127.0.0.1:9222/devtools/page/X", timeout=5)


def test_send_after_close_raises():
    srv = _MiniWSServer().start()
    ws = connect(srv.url, timeout=5)
    ws.close()
    with pytest.raises(WebSocketError):
        ws.send_text("nope")
