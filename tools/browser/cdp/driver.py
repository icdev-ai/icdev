# CUI // SP-CTI
"""CDP driver — the driver-level operation surface over a page target (cdp-port-03).

Sits on top of the three lower layers: ``launcher`` (start the browser, find its
port), ``ws_client`` (frame the bytes), ``session`` (correlate id -> result,
demux events). This exposes the ~10 DRIVER-level operations that
``AgentBrowser``/``scope.GuardedDriver`` reach for — ``get``, ``execute_script``,
screenshots, ``current_url``/``title``, ``quit``, and the timeout setters — with
Selenium-compatible names so a backend swap is transparent (cdp-port-04 wires it).

Two subtleties the spike (cdp-00 §4.2) flags, handled here:

* **``execute_script`` wraps the body as an IIFE.** ``_EXTRACT_JS`` ends in a
  *top-level* ``return``, which is a syntax error outside a function. It is wrapped
  ``(function(){ <script> }).apply(null, <args>)`` and run via ``Runtime.evaluate``
  with ``returnByValue`` — so the top-level return works and ``arguments[0]``
  resolves to the config object, exactly as Selenium's ``executeScript`` does.
  (Scripts that must pass *live element handles* need ``Runtime.callFunctionOn``
  with an ``objectId`` — that is the WebDriver-facade extension, cdp-wd-01, not
  this driver-level surface.)
* **Real trusted clicks recompute viewport coordinates** — but click emulation is
  part of the fuller element surface (cdp-wd-01); this driver deliberately stops at
  the driver-level operations so ``scope.py`` and the 108 tests stay untouched.

The CDP transport is an INTERNAL detail beneath the scope guard (§4.5): this object
is what a backend selector hands to ``GuardedDriver``; it is never a caller-reachable
escape hatch.
"""
from __future__ import annotations

import base64
import json
from typing import Any, List, Optional

from tools.browser.cdp.launcher import LaunchedBrowser, launch
from tools.browser.cdp.session import CDPError, CDPSession
from tools.browser.cdp.ws_client import connect
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.cdp.driver")


class CDPScriptError(RuntimeError):
    """A page script raised (``Runtime.evaluate`` returned exceptionDetails)."""


def wrap_script_as_iife(script: str, args: List[Any]) -> str:
    """Wrap a Selenium-style script body (which may end in a top-level ``return``)
    as an immediately-invoked function whose ``arguments`` are ``args``.

    This is the transformation that makes ``_EXTRACT_JS`` — which ends in
    ``return {...}`` — valid and gives it ``arguments[0]`` = the config object,
    matching Selenium ``executeScript`` semantics. Pure/deterministic so it is
    directly unit-testable.
    """
    args_json = json.dumps(args)
    return f"(function(){{\n{script}\n}}).apply(null, {args_json})"


class CDPDriver:
    """A single-page CDP driver with a Selenium-compatible operation surface.

    Build via :meth:`create` (which launches a browser) or wrap an existing
    :class:`CDPSession`/:class:`LaunchedBrowser` for tests.
    """

    def __init__(
        self,
        session: CDPSession,
        *,
        launched: Optional[LaunchedBrowser] = None,
        page_session_id: Optional[str] = None,
    ) -> None:
        self._session = session
        self._launched = launched
        self._page_sid = page_session_id
        # Selenium's setters are best-effort no-op-tolerant; store as deadlines.
        self._page_load_timeout: float = 30.0
        self._script_timeout: float = 30.0

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(cls, *, headless: bool = True, window_size=(1920, 1080)) -> "CDPDriver":
        """Launch a browser, attach to a fresh page target, enable Page/Runtime."""
        launched = launch(headless=headless, window_size=window_size)
        try:
            ws = connect(launched.browser_ws_url, timeout=30.0)
            session = CDPSession(ws)
            # Create a page target and attach flat, so page commands carry a sessionId.
            target = session.send("Target.createTarget", {"url": "about:blank"})
            target_id = target["targetId"]
            attach = session.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            sid = attach["sessionId"]
            session.send("Page.enable", session_id=sid)
            session.send("Runtime.enable", session_id=sid)
            return cls(session, launched=launched, page_session_id=sid)
        except Exception:
            launched.terminate()
            raise

    # -- navigation -----------------------------------------------------------

    def get(self, url: str) -> None:
        """Navigate and wait (best-effort) for the load event."""
        self._session.drain_events("Page.loadEventFired")
        self._cmd("Page.navigate", {"url": url}, timeout=self._page_load_timeout)
        # Best-effort load wait: poll for the loadEventFired event we enabled.
        # A timeout is not fatal (SPAs may never fire it) — mirrors Selenium's
        # pageLoadStrategy leniency.
        self._await_event("Page.loadEventFired", timeout=self._page_load_timeout)

    @property
    def current_url(self) -> str:
        return str(self.execute_script("return document.location.href;"))

    @property
    def title(self) -> str:
        return str(self.execute_script("return document.title;"))

    # -- scripting ------------------------------------------------------------

    def execute_script(self, script: str, *args: Any) -> Any:
        """Run a script body (Selenium semantics: may end in a top-level return,
        ``arguments[0..]`` bound to ``args``) and return its value by value."""
        expression = wrap_script_as_iife(script, list(args))
        result = self._cmd(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=self._script_timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text", "script error")
            raise CDPScriptError(text)
        return result.get("result", {}).get("value")

    # -- screenshots ----------------------------------------------------------

    def get_screenshot_as_png(self) -> bytes:
        result = self._cmd("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result["data"])

    def save_screenshot(self, path: str) -> bool:
        try:
            with open(path, "wb") as fh:
                fh.write(self.get_screenshot_as_png())
            return True
        except OSError as exc:  # pragma: no cover - disk fault path
            logger.debug("[cdp driver] save_screenshot failed: %s", exc)
            return False

    # -- timeouts (Selenium-compatible; stored as receive deadlines) ----------

    def set_page_load_timeout(self, seconds: float) -> None:
        self._page_load_timeout = float(seconds)

    def set_script_timeout(self, seconds: float) -> None:
        self._script_timeout = float(seconds)

    # -- lifecycle ------------------------------------------------------------

    def quit(self) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass
        if self._launched is not None:
            self._launched.terminate()

    def __enter__(self) -> "CDPDriver":
        return self

    def __exit__(self, *_exc) -> None:
        self.quit()

    # -- internals ------------------------------------------------------------

    def _cmd(self, method: str, params: Optional[dict] = None, *, timeout: Optional[float] = None) -> dict:
        """Send a command scoped to the page session (when attached)."""
        return self._session.send(method, params, timeout=timeout, session_id=self._page_sid)

    def _await_event(self, method: str, timeout: float) -> bool:
        """Return True if ``method`` has been (or is) seen within ``timeout``.

        Events already buffered by the session are checked first; otherwise a
        single lightweight command round-trip lets the read loop drain pending
        events. Never fatal — navigation without a load event is tolerated.
        """
        if self._session.drain_events(method):
            return True
        try:
            # A cheap command forces the read loop to process any buffered frames.
            self._cmd("Runtime.evaluate", {"expression": "1", "returnByValue": True}, timeout=timeout)
        except (CDPError, Exception):  # noqa: BLE001 - best-effort
            return False
        return bool(self._session.drain_events(method))
