# CUI // SP-CTI
"""ICDEV CDP transport — drive a Chromium-family browser over the Chrome
DevTools Protocol instead of a version-matched WebDriver binary.

The point of this package is air-gap survivability: CDP is served by the browser
itself over a loopback WebSocket when it is launched with
``--remote-debugging-port``. There is no separate driver binary to version-match,
pre-stage, or re-vendor when the browser auto-updates — which is the maintenance
coupling that makes the vendored-driver path unserviceable on an air-gapped host
(see docs/spikes/cdp-00-browser-automation-airgap-adaptation.md).

Layering (bottom-up), so each piece stays independently testable:

* ``ws_client`` — a stdlib-only RFC 6455 WebSocket client for loopback. Frame
  codec only; it knows nothing about CDP. This is cdp-port-01.
* ``preflight`` — reads the ``RemoteDebuggingAllowed`` policy and picks the usable
  tier (CDP / Selenium / HTTP-only) deterministically, without launching a
  browser. This is cdp-port-06.
* ``session`` — a CDP session that correlates request ids to responses and demuxes
  unsolicited events, ABOVE the frame codec (cdp-port-03).
* ``launcher`` — starts the browser with an ephemeral loopback debug port + a
  mandatory temp profile, reads ``DevToolsActivePort``, tears it down (cdp-port-03).
* ``driver`` — the ~10-operation, Selenium-compatible driver surface over a page
  target (cdp-port-03).
* ``webdriver`` — the full ~22-operation WebDriver-compatible facade (find_element,
  element handles, clicks) for the E2E estate (cdp-wd-01). It is NOT re-exported
  here on purpose: it imports ``selenium`` (for ``By``/exception compatibility),
  and keeping it out of the package ``__init__`` lets ``preflight``/``backend`` be
  imported and probe on a host where the ``selenium`` package is absent. Import it
  directly: ``from tools.browser.cdp.webdriver import CDPWebDriver``.

Zero new *required* runtime dependencies for the transport: CDP over loopback needs
no TLS, no proxy, and no ``permessage-deflate``, so a stdlib ``socket`` client
suffices. (The ``webdriver`` facade layer additionally uses the ``selenium`` python
package, which is pure-Python and offline-installable — only the driver *binary* is
unavailable air-gapped.)
"""

from tools.browser.cdp.driver import CDPDriver, CDPScriptError, wrap_script_as_iife
from tools.browser.cdp.launcher import (
    CDPLaunchError,
    LaunchedBrowser,
    build_launch_args,
    launch,
)
from tools.browser.cdp.preflight import (
    PolicyResult,
    TierDecision,
    preflight,
    read_remote_debugging_policy,
    select_tier,
)
from tools.browser.cdp.session import CDPError, CDPSession
from tools.browser.cdp.ws_client import (
    WebSocketError,
    WebSocketFrame,
    WebSocketTimeout,
    WSOpcode,
    connect,
)

__all__ = [
    "WSOpcode",
    "WebSocketFrame",
    "WebSocketError",
    "WebSocketTimeout",
    "connect",
    "PolicyResult",
    "TierDecision",
    "preflight",
    "read_remote_debugging_policy",
    "select_tier",
    "CDPSession",
    "CDPError",
    "CDPLaunchError",
    "LaunchedBrowser",
    "build_launch_args",
    "launch",
    "CDPDriver",
    "CDPScriptError",
    "wrap_script_as_iife",
]
