# CUI // SP-CTI
"""CDP session — request/response correlation + event demux (cdp-port-03).

This is the layer the spike (cdp-00 §4.3) insists lives ABOVE the frame codec:
``ws_client`` frames bytes and knows nothing about CDP; this maps a CDP command's
``id`` to its result and demultiplexes the unsolicited events that interleave with
responses. Keeping the codec free of CDP knowledge is what makes a later swap to
``--remote-debugging-pipe`` a single-file change.

CDP over a single page target is **single-flight** from one thread — a command is
sent, then messages are read until the matching ``id`` arrives; any event (a
message carrying ``method`` but not our ``id``) seen while waiting is buffered and
optionally dispatched to a listener, never dropped and never mistaken for the
response. That is the whole subtlety, and it is why correlation cannot live inside
the frame codec.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from tools.browser.cdp.ws_client import WebSocketClient, WebSocketTimeout
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.cdp.session")

EventListener = Callable[[str, Dict[str, Any]], None]


class CDPError(Exception):
    """A CDP command returned an ``error`` object.

    Carries the protocol ``code``/``message`` so callers can distinguish, e.g., a
    detached target from a bad parameter.
    """

    def __init__(self, method: str, code: Any, message: str, data: Any = None) -> None:
        self.method = method
        self.code = code
        self.cdp_message = message
        self.data = data
        super().__init__(f"CDP {method} failed [{code}]: {message}" + (f" ({data})" if data else ""))


class CDPSession:
    """Synchronous CDP command/event multiplexer over one WebSocket.

    Not thread-safe: one page target, one driving thread — matching selenium's own
    single-threaded-per-driver contract and the browser's per-target semantics.
    """

    def __init__(self, ws: WebSocketClient, *, default_timeout: Optional[float] = 30.0) -> None:
        self._ws = ws
        self._next_id = 0
        self.default_timeout = default_timeout
        # Events buffered while waiting for a response (or if no listener is set).
        self._events: List[Dict[str, Any]] = []
        self._listeners: List[EventListener] = []

    # -- events ---------------------------------------------------------------

    def add_listener(self, listener: EventListener) -> None:
        """Register a callback invoked as ``listener(method, params)`` for every
        event, including those already buffered."""
        self._listeners.append(listener)

    def drain_events(self, method: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return (and clear) buffered events, optionally filtered by method."""
        if method is None:
            out, self._events = self._events, []
            return out
        kept, out = [], []
        for ev in self._events:
            (out if ev.get("method") == method else kept).append(ev)
        self._events = kept
        return out

    def _dispatch_event(self, message: Dict[str, Any]) -> None:
        self._events.append(message)
        for listener in self._listeners:
            try:
                listener(message.get("method", ""), message.get("params", {}))
            except Exception as exc:  # noqa: BLE001 - a listener must never break the read loop
                logger.debug("[cdp session] event listener raised: %s", exc)

    # -- commands -------------------------------------------------------------

    def send(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a CDP command and return its ``result`` dict.

        Raises :class:`CDPError` on a protocol error, or a WebSocket error on
        transport failure/timeout. Events arriving before the matching response
        are buffered and dispatched, never confused with the result.
        """
        self._next_id += 1
        msg_id = self._next_id
        payload: Dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session_id is not None:
            payload["sessionId"] = session_id
        self._ws.send_text(json.dumps(payload))

        deadline = self.default_timeout if timeout is None else timeout
        while True:
            raw = self._ws.recv_text(timeout=deadline)
            try:
                message = json.loads(raw)
            except ValueError as exc:
                raise CDPError(method, "parse", f"non-JSON CDP frame: {exc}") from exc

            if message.get("id") == msg_id:
                if "error" in message:
                    err = message["error"] or {}
                    raise CDPError(method, err.get("code"), err.get("message", ""), err.get("data"))
                return message.get("result", {})

            # Not our response: an event (has "method") or another id's reply.
            if "method" in message:
                self._dispatch_event(message)
                continue
            # A stray reply for a different id (rare in single-flight) — ignore it
            # but log, so a genuine desync is visible rather than silently eaten.
            logger.debug("[cdp session] out-of-band reply id=%s while awaiting %s", message.get("id"), msg_id)

    def wait_for_event(self, method: str, *, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Block until an unsolicited event with ``method`` arrives; return it (or
        ``None`` on timeout).

        Reads frames off the socket, dispatching every other event as it goes, so a
        load wait genuinely waits for ``Page.loadEventFired`` instead of racing the
        DOM parse. Lenient by design: a timeout returns ``None`` (an SPA may never
        fire the event) rather than raising, matching Selenium's pageLoadStrategy.
        A buffered matching event is returned immediately.
        """
        buffered = self.drain_events(method)
        if buffered:
            return buffered[0]
        budget = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + (budget or 0.0)
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                raw = self._ws.recv_text(timeout=max(0.1, remaining))
            except WebSocketTimeout:
                return None
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if "method" in message:
                self._dispatch_event(message)
                if message.get("method") == method:
                    return message
        return None

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self._ws.close()

    def __enter__(self) -> "CDPSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
