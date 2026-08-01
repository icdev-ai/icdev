# CUI // SP-CTI
"""cdp-wd-01 — WebDriver-compatible CDP facade.

Covers the two subtleties the spike (cdp-00 §4.2) flags: element operations run
via Runtime.callFunctionOn on an objectId (not Runtime.evaluate), and click uses
LIVE viewport coordinates from getBoundingClientRect (the coordinate trap), with a
scripted-click fallback. Also the By->JS locator mapping and find_element/-elements.
Hermetic — a fake CDP session routes commands by inspecting the function body.
"""
from __future__ import annotations

import pytest
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from tools.browser.cdp.webdriver import (
    CDPWebDriver,
    CDPWebElement,
    _by_to_array_expression,
    _by_to_expression,
)


class FakeSession:
    """Routes callFunctionOn by a substring match on the functionDeclaration, and
    other methods by name. Records every call for assertions."""

    def __init__(self):
        self.calls = []
        self._fn_rules = []   # (substr, value_or_objectid, is_object)
        self._method_rules = {}

    def on_fn(self, substr, *, value=None, object_id=None):
        self._fn_rules.append((substr, value, object_id))
        return self

    def on_method(self, method, result):
        self._method_rules[method] = result
        return self

    def send(self, method, params=None, *, timeout=None, session_id=None):
        params = params or {}
        self.calls.append((method, params, session_id))
        if method == "Runtime.callFunctionOn":
            decl = params.get("functionDeclaration", "")
            for substr, value, object_id in self._fn_rules:
                if substr in decl:
                    if object_id is not None:
                        return {"result": {"objectId": object_id}}
                    return {"result": {"value": value}}
            return {"result": {"value": None}}
        if method in self._method_rules:
            r = self._method_rules[method]
            return r(params) if callable(r) else r
        return {}


# ── By -> JS locator mapping (pure) ───────────────────────────────────────────


def test_by_expression_maps_each_strategy():
    assert "getElementById(\"foo\")" in _by_to_expression(By.ID, "foo")
    assert "querySelector(\".btn\")" in _by_to_expression(By.CSS_SELECTOR, ".btn")
    assert "document.evaluate" in _by_to_expression(By.XPATH, "//a")
    assert "getElementsByName(\"q\")" in _by_to_expression(By.NAME, "q")
    assert "textContent.trim() ===" in _by_to_expression(By.LINK_TEXT, "Home")
    assert "includes(" in _by_to_expression(By.PARTIAL_LINK_TEXT, "Ho")


def test_by_values_are_json_escaped_not_interpolated():
    # a value with a quote must not break out of the JS string
    expr = _by_to_expression(By.CSS_SELECTOR, '"]; alert(1); //')
    assert "alert(1)" in expr  # present only as a JSON-escaped literal
    assert expr.count("querySelector(") == 1


def test_array_expression_uses_querySelectorAll():
    assert "querySelectorAll(\".row\")" in _by_to_array_expression(By.CSS_SELECTOR, ".row")
    assert "snapshotLength" in _by_to_array_expression(By.XPATH, "//tr")


# ── element operations via callFunctionOn ─────────────────────────────────────


def _el(session, oid="OBJ1"):
    return CDPWebElement(session, "PAGE-SID", oid)


def test_text_uses_callfunctionon_with_object_id():
    s = FakeSession().on_fn("innerText", value="Hello")
    el = _el(s)
    assert el.text == "Hello"
    method, params, sid = s.calls[-1]
    assert method == "Runtime.callFunctionOn"
    assert params["objectId"] == "OBJ1"
    assert sid == "PAGE-SID"


def test_get_attribute_passes_name_as_argument():
    s = FakeSession().on_fn("getAttribute", value="submit")
    assert _el(s).get_attribute("type") == "submit"
    assert s.calls[-1][1]["arguments"] == [{"value": "type"}]


def test_send_keys_dispatches_input_and_change():
    s = FakeSession().on_fn("dispatchEvent", value=None)
    _el(s).send_keys("abc")
    decl = s.calls[-1][1]["functionDeclaration"]
    assert "input" in decl and "change" in decl
    assert s.calls[-1][1]["arguments"] == [{"value": "abc"}]


def test_is_displayed_returns_bool():
    s = FakeSession().on_fn("getComputedStyle", value=True)
    assert _el(s).is_displayed() is True


# ── the click coordinate trap ─────────────────────────────────────────────────


def test_click_uses_live_viewport_coordinates():
    s = (FakeSession()
         .on_fn("scrollIntoView", value=None)
         .on_fn("getBoundingClientRect", value={"x": 100, "y": 200, "w": 50, "h": 20})
         .on_method("Input.dispatchMouseEvent", {}))
    _el(s).click()

    mouse = [c for c in s.calls if c[0] == "Input.dispatchMouseEvent"]
    assert len(mouse) == 2  # pressed + released
    assert [m[1]["type"] for m in mouse] == ["mousePressed", "mouseReleased"]
    # coordinates are the LIVE viewport-relative center, not page geometry
    for m in mouse:
        assert m[1]["x"] == 100 and m[1]["y"] == 200
        assert m[1]["button"] == "left"


def test_click_falls_back_to_scripted_when_no_box():
    s = (FakeSession()
         .on_fn("scrollIntoView", value=None)
         .on_fn("getBoundingClientRect", value={"x": 0, "y": 0, "w": 0, "h": 0})  # 0-size
         .on_fn("this.click()", value=None))
    _el(s).click()
    # no trusted dispatch on a 0-box element; scripted click used instead
    assert not [c for c in s.calls if c[0] == "Input.dispatchMouseEvent"]
    assert any("this.click()" in c[1].get("functionDeclaration", "")
               for c in s.calls if c[0] == "Runtime.callFunctionOn")


# ── driver-level element finding ──────────────────────────────────────────────


def _driver(session, sid="PAGE-SID"):
    return CDPWebDriver(session, page_session_id=sid)


def test_find_element_returns_webelement():
    s = FakeSession().on_method(
        "Runtime.evaluate", lambda p: {"result": {"objectId": "EL42"}}
    )
    el = _driver(s).find_element(By.CSS_SELECTOR, ".x")
    assert isinstance(el, CDPWebElement)
    assert el._object_id == "EL42"


def test_find_element_raises_when_absent():
    s = FakeSession().on_method("Runtime.evaluate", {"result": {"subtype": "null"}})
    with pytest.raises(NoSuchElementException):
        _driver(s).find_element(By.ID, "missing")


def test_find_elements_expands_array_via_getproperties():
    s = FakeSession()
    s.on_method("Runtime.evaluate", {"result": {"objectId": "ARR"}})
    s.on_method("Runtime.getProperties", {"result": [
        {"name": "0", "value": {"objectId": "E0"}},
        {"name": "1", "value": {"objectId": "E1"}},
        {"name": "length", "value": {"value": 2}},  # non-numeric name is skipped
    ]})
    els = _driver(s).find_elements(By.CSS_SELECTOR, ".row")
    assert [e._object_id for e in els] == ["E0", "E1"]


def test_find_elements_empty_when_no_array():
    s = FakeSession().on_method("Runtime.evaluate", {"result": {"subtype": "null"}})
    assert _driver(s).find_elements(By.CSS_SELECTOR, ".none") == []


# ── page-level operations ─────────────────────────────────────────────────────


def test_get_cookies_reads_network_domain():
    s = FakeSession().on_method("Network.getCookies", {"cookies": [{"name": "sid", "value": "x"}]})
    assert _driver(s).get_cookies() == [{"name": "sid", "value": "x"}]


def test_set_window_size_uses_emulation():
    s = FakeSession().on_method("Emulation.setDeviceMetricsOverride", {})
    _driver(s).set_window_size(1280, 720)
    call = [c for c in s.calls if c[0] == "Emulation.setDeviceMetricsOverride"][0]
    assert call[1]["width"] == 1280 and call[1]["height"] == 720


def test_implicitly_wait_stores_value():
    d = _driver(FakeSession())
    d.implicitly_wait(5)
    assert d._implicit_wait == 5.0


def test_page_source_reads_outer_html():
    s = FakeSession().on_method("Runtime.evaluate", {"result": {"value": "<html>x</html>"}})
    assert _driver(s).page_source == "<html>x</html>"
