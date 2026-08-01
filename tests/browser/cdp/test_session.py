# CUI // SP-CTI
"""cdp-port-03 — CDP session request/response correlation + event demux.

The subtlety the spike (cdp-00 §4.3) says cannot live in the frame codec: events
interleave with responses, and a command must return ITS response, never an event
seen while waiting. Tested against a scripted fake WebSocket so the correlation
logic is exercised deterministically.
"""
from __future__ import annotations

import json

import pytest

from tools.browser.cdp.session import CDPError, CDPSession


class FakeWS:
    """Duck-typed WebSocketClient: records sent frames, replays scripted recvs."""

    def __init__(self, script):
        self.sent = []
        self._script = list(script)  # list of str frames to return from recv_text
        self.closed = False

    def send_text(self, text):
        self.sent.append(text)

    def recv_text(self, timeout=None):
        if not self._script:
            raise AssertionError("recv_text called with no scripted frames left")
        item = self._script.pop(0)
        if isinstance(item, Exception):  # scripted transport error (e.g. timeout)
            raise item
        return item

    def close(self):
        self.closed = True


def _msg(**kw):
    return json.dumps(kw)


def test_send_returns_matching_result_skipping_events():
    ws = FakeWS([
        _msg(method="Runtime.consoleAPICalled", params={"type": "log"}),  # event first
        _msg(id=1, result={"targetInfos": []}),                            # then our response
    ])
    s = CDPSession(ws)
    result = s.send("Target.getTargets")
    assert result == {"targetInfos": []}
    # the interleaved event was buffered, not lost or mistaken for the result
    events = s.drain_events()
    assert len(events) == 1 and events[0]["method"] == "Runtime.consoleAPICalled"


def test_wait_for_event_returns_when_seen_dispatching_others():
    """cdp-vv-01 regression: the load wait must genuinely block until the event
    arrives, dispatching intervening events — an earlier best-effort version
    returned before Page.loadEventFired, racing the DOM parse."""
    ws = FakeWS([
        _msg(method="Page.frameStartedLoading", params={}),
        _msg(method="Page.loadEventFired", params={"timestamp": 2.0}),
    ])
    s = CDPSession(ws)
    ev = s.wait_for_event("Page.loadEventFired", timeout=5)
    assert ev is not None and ev["method"] == "Page.loadEventFired"


def test_wait_for_event_returns_buffered_event_immediately():
    ws = FakeWS([
        _msg(method="Page.loadEventFired", params={}),
        _msg(id=1, result={}),
    ])
    s = CDPSession(ws)
    s.send("Page.navigate", {"url": "x"})  # buffers the loadEventFired
    assert s.wait_for_event("Page.loadEventFired", timeout=5)["method"] == "Page.loadEventFired"


def test_wait_for_event_timeout_returns_none_not_raise():
    from tools.browser.cdp.ws_client import WebSocketTimeout
    ws = FakeWS([WebSocketTimeout("no frame")])
    s = CDPSession(ws)
    assert s.wait_for_event("Page.loadEventFired", timeout=1) is None


def test_command_id_increments_and_is_sent():
    ws = FakeWS([_msg(id=1, result={}), _msg(id=2, result={})])
    s = CDPSession(ws)
    s.send("Page.enable")
    s.send("Runtime.enable")
    ids = [json.loads(f)["id"] for f in ws.sent]
    assert ids == [1, 2]
    assert json.loads(ws.sent[0])["method"] == "Page.enable"


def test_error_response_raises_cdp_error():
    ws = FakeWS([_msg(id=1, error={"code": -32000, "message": "Cannot navigate"})])
    s = CDPSession(ws)
    with pytest.raises(CDPError) as exc:
        s.send("Page.navigate", {"url": "x"})
    assert exc.value.code == -32000
    assert "Cannot navigate" in exc.value.cdp_message


def test_session_id_is_threaded_into_payload():
    ws = FakeWS([_msg(id=1, result={})])
    s = CDPSession(ws)
    s.send("Page.enable", session_id="SID123")
    assert json.loads(ws.sent[0])["sessionId"] == "SID123"


def test_listener_is_invoked_for_events():
    seen = []
    ws = FakeWS([
        _msg(method="Page.loadEventFired", params={"timestamp": 1.0}),
        _msg(id=1, result={"ok": True}),
    ])
    s = CDPSession(ws)
    s.add_listener(lambda method, params: seen.append(method))
    s.send("Page.navigate", {"url": "x"})
    assert seen == ["Page.loadEventFired"]


def test_drain_events_by_method_filters():
    ws = FakeWS([
        _msg(method="A", params={}),
        _msg(method="B", params={}),
        _msg(id=1, result={}),
    ])
    s = CDPSession(ws)
    s.send("X")
    only_a = s.drain_events("A")
    assert len(only_a) == 1 and only_a[0]["method"] == "A"
    # B remains buffered
    assert [e["method"] for e in s.drain_events()] == ["B"]


def test_listener_exception_does_not_break_read_loop():
    ws = FakeWS([
        _msg(method="Boom", params={}),
        _msg(id=1, result={"ok": 1}),
    ])
    s = CDPSession(ws)
    s.add_listener(lambda m, p: (_ for _ in ()).throw(RuntimeError("listener boom")))
    # send must still return the result despite the listener raising on the event
    assert s.send("X") == {"ok": 1}
