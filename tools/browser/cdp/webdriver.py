# CUI // SP-CTI
"""WebDriver-compatible CDP facade — the ~22-operation E2E surface (cdp-wd-01).

The driver-level surface (cdp-port-03) is enough for the agent browser's
``read_state``, but the E2E estate (``tests/e2e_selenium/`` + the ~76 e2e scripts)
reaches for a wider, element-oriented API: ``find_element``/``find_elements``,
``element.text``/``.click()``/``.send_keys()``/``.get_attribute()``/
``.is_displayed()``, ``page_source``, ``set_window_size``, ``implicitly_wait``,
``get_cookies``, ``refresh``. This facade provides that surface over CDP so the
estate runs with **no driver binary** (spike cdp-00 §4.8).

The load-bearing fact that makes this cheap (§4.8): the ``selenium`` PYTHON package
is pure Python and pip-installable offline — only the *driver binary* is
unavailable air-gapped. So ``By``, ``WebDriverWait`` and ``expected_conditions``
are plain helpers that just call ``driver.find_element(...)``, and they keep
working **unchanged** against this duck-typed CDP driver. This module reuses
selenium's ``By`` and its exception types so it is drop-in for that estate.

Two subtleties the spike (§4.2) flags, handled here:

* **Element handles need ``Runtime.callFunctionOn`` + ``objectId``**, not
  ``Runtime.evaluate`` — an element's ``.text``/``.click()`` run a function with
  the live DOM node as ``this``.
* **The click coordinate trap.** ``Input.dispatchMouseEvent`` wants VIEWPORT
  coordinates; the page-coordinate geometry from ``_EXTRACT_JS`` would click the
  right spot on an unscrolled page and silently the wrong spot on a scrolled one.
  So the click point is recomputed from a **live ``getBoundingClientRect()``** at
  click time (which is already viewport-relative), never reused from earlier
  geometry. Real trusted ``Input.dispatchMouseEvent`` clicks are the primary path
  (``isTrusted:true`` fires native submit / ``:active`` / focus rings); a scripted
  ``.click()`` is the fallback for the intercepted case.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from tools.browser.cdp.driver import CDPDriver
from tools.browser.cdp.session import CDPError
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.cdp.webdriver")


def _by_to_expression(by: str, value: str) -> str:
    """Return a JS expression that resolves the FIRST matching node (or null) for a
    Selenium ``(By, value)`` locator. Values are JSON-encoded so quoting/escaping is
    handled by the encoder, never by string interpolation."""
    v = json.dumps(value)
    if by == By.ID:
        return f"document.getElementById({v})"
    if by == By.CSS_SELECTOR:
        return f"document.querySelector({v})"
    if by == By.NAME:
        return f"(document.getElementsByName({v})[0] || null)"
    if by == By.TAG_NAME:
        return f"document.querySelector({v})"
    if by == By.CLASS_NAME:
        return f"(document.getElementsByClassName({v})[0] || null)"
    if by == By.XPATH:
        return f"document.evaluate({v}, document, null, 9, null).singleNodeValue"
    if by == By.LINK_TEXT:
        return f"(Array.from(document.querySelectorAll('a')).find(a => a.textContent.trim() === {v}) || null)"
    if by == By.PARTIAL_LINK_TEXT:
        return f"(Array.from(document.querySelectorAll('a')).find(a => a.textContent.includes({v})) || null)"
    raise WebDriverException(f"unsupported locator strategy: {by!r}")


def _by_to_array_expression(by: str, value: str) -> str:
    """Same as :func:`_by_to_expression` but resolves ALL matches into an array."""
    v = json.dumps(value)
    if by == By.CSS_SELECTOR or by == By.TAG_NAME:
        return f"Array.from(document.querySelectorAll({v}))"
    if by == By.ID:
        return f"Array.from(document.querySelectorAll('#'+CSS.escape({v})))"
    if by == By.NAME:
        return f"Array.from(document.getElementsByName({v}))"
    if by == By.CLASS_NAME:
        return f"Array.from(document.getElementsByClassName({v}))"
    if by == By.XPATH:
        return (
            "(function(){var r=document.evaluate(" + v + ", document, null, 7, null);"
            "var a=[];for(var i=0;i<r.snapshotLength;i++){a.push(r.snapshotItem(i));}return a;})()"
        )
    if by == By.LINK_TEXT:
        return f"Array.from(document.querySelectorAll('a')).filter(a => a.textContent.trim() === {v})"
    if by == By.PARTIAL_LINK_TEXT:
        return f"Array.from(document.querySelectorAll('a')).filter(a => a.textContent.includes({v}))"
    raise WebDriverException(f"unsupported locator strategy: {by!r}")


class CDPWebElement:
    """A Selenium-compatible element backed by a CDP ``objectId``.

    Every operation runs ``Runtime.callFunctionOn`` with the live DOM node as
    ``this`` — the element-handle path the spike says needs ``callFunctionOn`` and
    cannot be done through ``Runtime.evaluate``.
    """

    def __init__(self, session, page_session_id: Optional[str], object_id: str) -> None:
        self._session = session
        self._page_sid = page_session_id
        self._object_id = object_id

    # -- callFunctionOn plumbing ----------------------------------------------

    def _call(self, fn_decl: str, *args: Any, return_by_value: bool = True) -> Any:
        result = self._session.send(
            "Runtime.callFunctionOn",
            {
                "objectId": self._object_id,
                "functionDeclaration": fn_decl,
                "arguments": [{"value": a} for a in args],
                "returnByValue": return_by_value,
                "awaitPromise": True,
            },
            session_id=self._page_sid,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text", "element script error")
            raise WebDriverException(text)
        r = result.get("result", {})
        return r.get("value") if return_by_value else r.get("objectId")

    # -- properties (Selenium names) ------------------------------------------

    @property
    def text(self) -> str:
        return self._call("function(){ return this.innerText || this.textContent || ''; }") or ""

    @property
    def tag_name(self) -> str:
        return (self._call("function(){ return this.tagName ? this.tagName.toLowerCase() : ''; }") or "")

    def get_attribute(self, name: str) -> Optional[str]:
        # Match Selenium's property-then-attribute resolution for the common cases
        # (value/checked are live properties), falling back to getAttribute.
        return self._call(
            "function(n){ if (n in this) { var p=this[n];"
            " if (typeof p==='boolean') return p ? 'true' : null;"
            " if (p!=null && typeof p!=='object') return String(p); }"
            " return this.getAttribute(n); }",
            name,
        )

    def get_property(self, name: str) -> Any:
        return self._call("function(n){ return this[n]; }", name)

    def is_displayed(self) -> bool:
        return bool(self._call(
            "function(){ var s=window.getComputedStyle(this);"
            " if (s.display==='none'||s.visibility==='hidden'||parseFloat(s.opacity)===0) return false;"
            " var r=this.getBoundingClientRect(); return !!(r.width||r.height||this.getClientRects().length); }"
        ))

    def is_enabled(self) -> bool:
        return bool(self._call("function(){ return !this.disabled; }"))

    def is_selected(self) -> bool:
        return bool(self._call("function(){ return !!(this.checked || this.selected); }"))

    # -- actions --------------------------------------------------------------

    def clear(self) -> "CDPWebElement":
        self._call(
            "function(){ this.value=''; "
            " this.dispatchEvent(new Event('input',{bubbles:true}));"
            " this.dispatchEvent(new Event('change',{bubbles:true})); }"
        )
        return self

    def send_keys(self, *value: Any) -> "CDPWebElement":
        text = "".join(str(v) for v in value)
        # Focus + append to value + fire input/change with bubbles:true. Assigning
        # .value alone fires no events, and framework handlers depend on the dispatch.
        self._call(
            "function(t){ this.focus();"
            " if ('value' in this) { this.value = (this.value||'') + t; }"
            " else { this.textContent = (this.textContent||'') + t; }"
            " this.dispatchEvent(new Event('input',{bubbles:true}));"
            " this.dispatchEvent(new Event('change',{bubbles:true})); }",
            text,
        )
        return self

    def submit(self) -> None:
        self._call(
            "function(){ var f = this.form || this.closest('form');"
            " if (f) { f.requestSubmit ? f.requestSubmit() : f.submit(); } else { this.click(); } }"
        )

    def click(self) -> None:
        """Trusted click via Input.dispatchMouseEvent at LIVE viewport coordinates.

        The coordinate trap (§4.2): the click point is recomputed from a live
        getBoundingClientRect() at click time — viewport-relative, correct on
        scrolled pages — never reused from page-coordinate geometry. Falls back to a
        scripted click if the element has no box (0-size / detached).
        """
        self._call("function(){ this.scrollIntoView({block:'center',inline:'center'}); }")
        rect = self._call(
            "function(){ var r=this.getBoundingClientRect();"
            " return {x:r.left + r.width/2, y:r.top + r.height/2, w:r.width, h:r.height}; }"
        )
        if rect and rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
            x, y = rect["x"], rect["y"]
            try:
                for etype in ("mousePressed", "mouseReleased"):
                    self._session.send(
                        "Input.dispatchMouseEvent",
                        {"type": etype, "x": x, "y": y, "button": "left", "clickCount": 1},
                        session_id=self._page_sid,
                    )
                return
            except CDPError as exc:  # intercepted / not dispatchable — fall back
                logger.debug("[cdp webelement] dispatchMouseEvent fell back to scripted click: %s", exc)
        self._call("function(){ this.click(); }")

    # -- nested finds ---------------------------------------------------------

    def find_element(self, by: str = By.ID, value: Optional[str] = None) -> "CDPWebElement":
        object_id = self._call(
            "function(sel){ return this.querySelector(sel); }",
            _css_for(by, value), return_by_value=False,
        )
        if not object_id:
            raise NoSuchElementException(f"no element for ({by}, {value}) under this element")
        return CDPWebElement(self._session, self._page_sid, object_id)


def _css_for(by: str, value: Optional[str]) -> str:
    """Best-effort CSS for nested element finds (the common e2e case)."""
    value = value or ""
    if by == By.CSS_SELECTOR or by == By.TAG_NAME:
        return value
    if by == By.ID:
        return f"#{value}"
    if by == By.CLASS_NAME:
        return f".{value}"
    if by == By.NAME:
        return f'[name="{value}"]'
    raise WebDriverException(f"nested find by {by!r} is not supported; use CSS/tag/id/class/name")


class CDPWebDriver(CDPDriver):
    """The full, Selenium-compatible WebDriver surface over CDP.

    Extends :class:`CDPDriver` (get/execute_script/screenshots/current_url/title/
    quit) with element finding, ``page_source``, window sizing, ``implicitly_wait``,
    cookies and ``refresh`` — the ~22 operations the E2E estate uses.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._implicit_wait = 0.0

    # -- element finding ------------------------------------------------------

    def _resolve_object_id(self, expression: str) -> Optional[str]:
        result = self._cmd("Runtime.evaluate", {"expression": expression, "returnByValue": False})
        r = result.get("result", {})
        if r.get("subtype") == "null" or "objectId" not in r:
            return None
        return r["objectId"]

    def _resolve_object_ids(self, expression: str) -> List[str]:
        array_id = self._resolve_object_id(expression)
        if not array_id:
            return []
        props = self._cmd("Runtime.getProperties", {"objectId": array_id, "ownProperties": True})
        ids: List[str] = []
        for p in props.get("result", []):
            if p.get("name", "").isdigit():
                val = p.get("value", {})
                if "objectId" in val:
                    ids.append(val["objectId"])
        return ids

    def find_element(self, by: str = By.ID, value: Optional[str] = None) -> CDPWebElement:
        object_id = self._resolve_object_id(_by_to_expression(by, value or ""))
        if not object_id:
            raise NoSuchElementException(f"no element located by ({by}, {value})")
        return CDPWebElement(self._session, self._page_sid, object_id)

    def find_elements(self, by: str = By.ID, value: Optional[str] = None) -> List[CDPWebElement]:
        ids = self._resolve_object_ids(_by_to_array_expression(by, value or ""))
        return [CDPWebElement(self._session, self._page_sid, oid) for oid in ids]

    # -- page-level operations ------------------------------------------------

    @property
    def page_source(self) -> str:
        return str(self.execute_script(
            "return document.documentElement ? document.documentElement.outerHTML : '';"
        ))

    def set_window_size(self, width: int, height: int) -> None:
        # Headless has no OS window; drive the rendered viewport instead.
        self._cmd("Emulation.setDeviceMetricsOverride", {
            "width": int(width), "height": int(height), "deviceScaleFactor": 0, "mobile": False,
        })

    def implicitly_wait(self, seconds: float) -> None:
        self._implicit_wait = float(seconds)

    def get_cookies(self) -> List[dict]:
        return self._cmd("Network.getCookies").get("cookies", [])

    def refresh(self) -> None:
        self._session.drain_events("Page.loadEventFired")
        self._cmd("Page.reload", {})
        self._await_event("Page.loadEventFired", timeout=self._page_load_timeout)

    def close(self) -> None:
        # Close the current page target (Selenium `close`), distinct from `quit`.
        try:
            self._cmd("Page.close", {})
        except CDPError:
            pass
