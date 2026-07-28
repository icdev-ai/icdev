# CUI // SP-CTI
"""cdp-port-03 — CDP driver operation surface.

The delicate, testable logic without a live browser: the IIFE wrapping that makes
_EXTRACT_JS (which ends in a top-level `return`) valid and gives it arguments[0],
plus the driver operations exercised against a scripted fake CDPSession.
"""
from __future__ import annotations

import base64
import json

import pytest

from tools.browser.cdp.driver import CDPDriver, CDPScriptError, wrap_script_as_iife


# ── the IIFE wrapping (pure) ──────────────────────────────────────────────────


def test_wrap_makes_top_level_return_valid_and_binds_arguments():
    wrapped = wrap_script_as_iife("return arguments[0].max_elements;", [{"max_elements": 200}])
    # body is inside a function (top-level return now legal), applied with the args
    assert wrapped.startswith("(function(){")
    assert "return arguments[0].max_elements;" in wrapped
    assert wrapped.endswith(f".apply(null, {json.dumps([{'max_elements': 200}])})")


def test_wrap_no_args_applies_empty_list():
    assert wrap_script_as_iife("return 1;", []).endswith(".apply(null, [])")


# ── driver operations via a fake session ──────────────────────────────────────


class FakeSession:
    """Scripted CDPSession: returns a canned result per method (last-registered
    wins), recording the calls for assertions."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []
        self.closed = False

    def send(self, method, params=None, *, timeout=None, session_id=None):
        self.calls.append((method, params, session_id))
        r = self._responses.get(method)
        return r(params) if callable(r) else (r or {})

    def drain_events(self, method=None):
        return []

    def close(self):
        self.closed = True


def _driver(responses, sid="SID"):
    return CDPDriver(FakeSession(responses), page_session_id=sid)


def test_execute_script_wraps_and_returns_value():
    captured = {}

    def evaluate(params):
        captured["expr"] = params["expression"]
        assert params["returnByValue"] is True
        return {"result": {"value": 42}}

    d = _driver({"Runtime.evaluate": evaluate})
    out = d.execute_script("return arguments[0] + 2;", 40)
    assert out == 42
    assert captured["expr"].startswith("(function(){")
    assert ".apply(null, [40])" in captured["expr"]


def test_execute_script_raises_on_exception_details():
    d = _driver({"Runtime.evaluate": {"exceptionDetails": {"text": "ReferenceError: x"}}})
    with pytest.raises(CDPScriptError):
        d.execute_script("return x;")


def test_current_url_and_title_use_evaluate():
    d = _driver({"Runtime.evaluate": lambda p: {"result": {"value": "http://localhost:5050/"}}})
    assert d.current_url == "http://localhost:5050/"


def test_screenshot_decodes_base64():
    png = b"\x89PNG\r\n\x1a\n" + b"body"
    d = _driver({"Page.captureScreenshot": {"data": base64.b64encode(png).decode()}})
    assert d.get_screenshot_as_png() == png


def test_save_screenshot_writes_file(tmp_path):
    png = b"\x89PNGdata"
    d = _driver({"Page.captureScreenshot": {"data": base64.b64encode(png).decode()}})
    dest = tmp_path / "shot.png"
    assert d.save_screenshot(str(dest)) is True
    assert dest.read_bytes() == png


def test_commands_carry_the_page_session_id():
    d = _driver({"Runtime.evaluate": {"result": {"value": "x"}}}, sid="PAGE-SID")
    _ = d.current_url
    method, _params, sid = d._session.calls[-1]
    assert method == "Runtime.evaluate"
    assert sid == "PAGE-SID"


def test_timeout_setters_store_values():
    d = _driver({})
    d.set_page_load_timeout(12)
    d.set_script_timeout(7)
    assert d._page_load_timeout == 12.0
    assert d._script_timeout == 7.0


def test_quit_closes_session():
    d = _driver({})
    d.quit()
    assert d._session.closed is True
