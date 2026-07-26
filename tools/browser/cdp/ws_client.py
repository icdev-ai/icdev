# CUI // SP-CTI
"""Stdlib-only RFC 6455 WebSocket client for loopback CDP (cdp-port-01).

Why hand-rolled instead of the usual optional-accelerator-with-fallback idiom:
the target is an air-gapped host, and neither ``websockets`` nor
``websocket-client`` is declared in requirements.txt. A "prefer third-party if
importable" design would take the fast path in development and the *untested*
stdlib path on the actual air-gapped target — the exact inversion of where test
effort should land. So this is the only path, and it is the one under test.

CDP over loopback strips almost everything hard out of RFC 6455:

* no TLS (``ws://127.0.0.1:port`` — the browser serves plaintext on loopback),
* no proxy,
* no ``permessage-deflate`` (we simply never offer the extension),
* server -> client frames arrive **unmasked** (RFC 6455 §5.1: a server MUST NOT
  mask), and client -> server masking is a 4-byte XOR.

**This module is a frame codec. It knows nothing about CDP.** Request/response
correlation (matching a CDP ``id`` to its result and demuxing unsolicited
``Runtime.consoleAPICalled``-style events) belongs one layer up and must never
leak into the codec — keeping the codec CDP-free is also what makes a later swap
to ``--remote-debugging-pipe`` a single-file change.

Two details that bite in practice and are covered by tests:

* a 1920x1080 PNG screenshot arrives base64-encoded at 1-3 MB, so the payload
  read loops on partial ``recv`` and the 64-bit length path is exercised for real;
* control frames (ping/pong/close) interleave with data frames and are handled
  transparently by ``recv_message`` without the caller seeing them.
"""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional
from urllib.parse import urlsplit

# RFC 6455 §1.3 — the magic GUID concatenated to the client key for the accept.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Read chunk for socket recv. Small enough to exercise the partial-read loop on
# multi-MB payloads, large enough not to syscall-thrash.
_RECV_CHUNK = 65536

# Guard against a malicious/confused server announcing an enormous frame. CDP
# payloads (even full-page screenshots) sit comfortably under this.
_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB


class WSOpcode(IntEnum):
    """RFC 6455 §5.2 opcodes."""

    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


class WebSocketError(Exception):
    """Protocol or connection error."""


class WebSocketTimeout(WebSocketError):
    """A read did not complete within the socket receive deadline.

    Distinct type so callers (a CDP session's per-request deadline) can tell a
    timeout apart from a hard protocol failure.
    """


@dataclass
class WebSocketFrame:
    """A single decoded frame. ``opcode`` is a :class:`WSOpcode`; ``payload`` is
    the (already-unmasked) bytes; ``fin`` marks the final fragment."""

    fin: bool
    opcode: WSOpcode
    payload: bytes


# ── Frame codec (pure functions — no socket, fully unit-testable) ─────────────


def encode_frame(opcode: WSOpcode, payload: bytes, *, mask: bool = True) -> bytes:
    """Encode one FINal frame. Client -> server frames MUST be masked (``mask``
    defaults True per RFC 6455 §5.1); the mask key is fresh per frame.

    The length field selects 7 / 16 / 64-bit form exactly per spec — the 64-bit
    branch is real, not theoretical, for base64 screenshot payloads.
    """
    if opcode > 0xF:
        raise WebSocketError(f"opcode out of range: {opcode}")
    fin_and_op = 0x80 | int(opcode)  # FIN=1, RSV=0
    header = bytearray([fin_and_op])

    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)

    if not mask:
        return bytes(header) + payload

    mask_key = os.urandom(4)
    header += mask_key
    masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
    return bytes(header) + masked


# ── The client ────────────────────────────────────────────────────────────────


class WebSocketClient:
    """A synchronous loopback WebSocket client.

    Not thread-safe: a single CDP session drives it from one thread, matching
    selenium's own single-threaded-per-driver contract. Use :func:`connect` to
    build one (it performs the opening handshake).
    """

    def __init__(self, sock: socket.socket, *, timeout: Optional[float] = None) -> None:
        self._sock = sock
        self._recv_buf = bytearray()
        self._closed = False
        self.default_timeout = timeout

    # -- lifecycle ------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, code: int = 1000) -> None:
        """Send a Close frame (best-effort) and shut the socket down."""
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.sendall(encode_frame(WSOpcode.CLOSE, struct.pack("!H", code)))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocketClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- send -----------------------------------------------------------------

    def send_text(self, text: str) -> None:
        """Send a masked text frame (CDP command JSON)."""
        self._send(WSOpcode.TEXT, text.encode("utf-8"))

    def send_bytes(self, data: bytes) -> None:
        self._send(WSOpcode.BINARY, data)

    def _send(self, opcode: WSOpcode, payload: bytes) -> None:
        if self._closed:
            raise WebSocketError("send on a closed WebSocket")
        try:
            self._sock.sendall(encode_frame(opcode, payload, mask=True))
        except OSError as exc:  # pragma: no cover - network fault path
            raise WebSocketError(f"send failed: {exc}") from exc

    # -- receive --------------------------------------------------------------

    def recv_message(self, timeout: Optional[float] = None) -> WebSocketFrame:
        """Return the next DATA message (text/binary), transparently handling
        control frames and reassembling fragments.

        Ping is answered with Pong; a Close from the peer raises
        :class:`WebSocketError` after marking the connection closed. The caller
        never sees control frames. ``timeout`` (seconds) is applied as the socket
        receive deadline for the whole message; ``None`` uses ``default_timeout``.
        """
        deadline = self.default_timeout if timeout is None else timeout
        self._sock.settimeout(deadline)

        data_opcode: Optional[WSOpcode] = None
        buffer = bytearray()

        while True:
            frame = self._read_frame()

            if frame.opcode in (WSOpcode.PING, WSOpcode.PONG, WSOpcode.CLOSE):
                if frame.opcode == WSOpcode.PING:
                    # Answer with the same application data, masked.
                    try:
                        self._sock.sendall(encode_frame(WSOpcode.PONG, frame.payload))
                    except OSError:
                        pass
                    continue
                if frame.opcode == WSOpcode.PONG:
                    continue
                # CLOSE
                self._closed = True
                try:
                    self._sock.close()
                except OSError:
                    pass
                raise WebSocketError("peer closed the WebSocket")

            # Data frame (TEXT / BINARY / CONTINUATION).
            if frame.opcode == WSOpcode.CONTINUATION:
                if data_opcode is None:
                    raise WebSocketError("continuation frame with no message to continue")
            else:
                if data_opcode is not None:
                    raise WebSocketError("new data frame began mid-fragment")
                data_opcode = frame.opcode

            buffer += frame.payload
            if frame.fin:
                assert data_opcode is not None
                return WebSocketFrame(fin=True, opcode=data_opcode, payload=bytes(buffer))

    def recv_text(self, timeout: Optional[float] = None) -> str:
        frame = self.recv_message(timeout=timeout)
        if frame.opcode != WSOpcode.TEXT:
            raise WebSocketError(f"expected text frame, got {frame.opcode!r}")
        return frame.payload.decode("utf-8")

    # -- frame reader ---------------------------------------------------------

    def _read_frame(self) -> WebSocketFrame:
        b0, b1 = self._read_exact(2)
        fin = bool(b0 & 0x80)
        opcode_val = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F

        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))

        if length > _MAX_PAYLOAD_BYTES:
            raise WebSocketError(f"frame payload too large: {length} bytes")

        mask_key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""

        if masked and payload:
            payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))

        try:
            opcode = WSOpcode(opcode_val)
        except ValueError as exc:
            raise WebSocketError(f"unknown opcode 0x{opcode_val:x}") from exc

        return WebSocketFrame(fin=fin, opcode=opcode, payload=bytes(payload))

    def _read_exact(self, n: int) -> bytes:
        """Read exactly ``n`` bytes, looping on partial recv.

        This is the load-bearing loop for multi-MB base64 screenshot payloads:
        a single ``recv`` returns at most one TCP segment, so a 2 MB frame is
        assembled across dozens of reads.
        """
        while len(self._recv_buf) < n:
            try:
                chunk = self._sock.recv(_RECV_CHUNK)
            except socket.timeout as exc:
                raise WebSocketTimeout("timed out waiting for frame data") from exc
            except OSError as exc:
                raise WebSocketError(f"recv failed: {exc}") from exc
            if not chunk:
                raise WebSocketError("connection closed mid-frame")
            self._recv_buf += chunk
        out = bytes(self._recv_buf[:n])
        del self._recv_buf[:n]
        return out


# ── Handshake / factory ───────────────────────────────────────────────────────


def _accept_for_key(key_b64: str) -> str:
    digest = hashlib.sha1((key_b64 + _WS_GUID).encode("ascii")).digest()  # noqa: S324 - RFC 6455 mandates SHA-1 here
    return base64.b64encode(digest).decode("ascii")


def connect(
    url: str,
    *,
    timeout: Optional[float] = 30.0,
    connect_timeout: Optional[float] = 10.0,
) -> WebSocketClient:
    """Open a WebSocket to a loopback CDP endpoint and perform the RFC 6455
    opening handshake.

    ``url`` is a CDP ``webSocketDebuggerUrl`` such as
    ``ws://127.0.0.1:9222/devtools/page/<id>``. Only ``ws://`` (plaintext
    loopback) is supported — CDP does not serve ``wss://`` locally and TLS is
    explicitly out of scope. Raises :class:`WebSocketError` on any handshake
    failure, with the server's status line when one is available.
    """
    parts = urlsplit(url)
    if parts.scheme != "ws":
        raise WebSocketError(f"only ws:// is supported (loopback CDP), got: {parts.scheme!r}")
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.settimeout(connect_timeout)

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    try:
        sock.sendall(request.encode("ascii"))
        response, leftover = _read_http_response(sock)
    except OSError as exc:
        sock.close()
        raise WebSocketError(f"handshake I/O failed: {exc}") from exc

    status_line = response.split("\r\n", 1)[0]
    if " 101 " not in f" {status_line} ":
        sock.close()
        raise WebSocketError(f"handshake rejected: {status_line!r}")

    headers = _parse_headers(response)
    accept = headers.get("sec-websocket-accept", "")
    if accept != _accept_for_key(key):
        sock.close()
        raise WebSocketError("handshake accept token mismatch")

    client = WebSocketClient(sock, timeout=timeout)
    # Any bytes the server pipelined after the handshake belong to the frame stream.
    if leftover:
        client._recv_buf += leftover
    return client


def _read_http_response(sock: socket.socket) -> tuple[str, bytes]:
    """Read up to and including the blank line ending the HTTP response header.
    Returns (header_text, leftover_body_bytes) — leftover is any frame bytes the
    server pipelined immediately after the 101 response."""
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(_RECV_CHUNK)
        if not chunk:
            raise WebSocketError("connection closed during handshake")
        buf += chunk
        if len(buf) > 64 * 1024:
            raise WebSocketError("handshake response header too large")
    header_bytes, _, leftover = bytes(buf).partition(b"\r\n\r\n")
    return header_bytes.decode("latin-1"), leftover


def _parse_headers(response: str) -> dict:
    headers = {}
    for line in response.split("\r\n")[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    return headers
