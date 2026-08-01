# CUI // SP-CTI
"""Browser backend selection (cdp-port-04).

Chooses and constructs the browser transport — CDP (Tier 1), Selenium (Tier 2),
or none (Tier 3) — from a **declared order, never a silent try/except cascade**
(spike cdp-00 §4.6):

* ``auto`` (the default) resolves **CDP → Tier 3**. It never silently picks
  Selenium; Tier 2 is opt-in only.
* an explicit ``cdp`` or ``selenium`` request **never degrades** — if it cannot be
  honoured it raises, so a caller that demanded a transport is never handed a
  different one.

The tier decision itself is delegated to ``cdp.preflight`` (which reads
``RemoteDebuggingAllowed`` and checks for a browser, without launching one), so
this module only maps a tier to a constructor and enforces the never-degrade rule.

**This wires selection only. It does not touch ``scope.py`` or the agent-browser
tests.** ``GuardedDriver`` wraps whatever driver object it is given, and both
``CDPDriver`` and the Selenium ``WebDriver`` are duck-type compatible at the
driver level, so the guard, the audit trail, and the 108 tests are unaffected.

Config: ``ICDEV_BROWSER_BACKEND`` = ``auto`` | ``cdp`` | ``selenium`` (default
``auto``); an explicit ``requested`` argument overrides the env var.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from tools.browser.cdp.preflight import preflight
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.backend")

BACKEND_CDP = "cdp"
BACKEND_SELENIUM = "selenium"
BACKEND_HTTP_ONLY = "http-only"  # Tier 3 — no browser transport

_VALID_REQUESTS = ("auto", "cdp", "selenium")
_ENV_VAR = "ICDEV_BROWSER_BACKEND"

# Named here so a Tier-3 refusal points at the surviving verification, not a dead end.
_TIER3_TOOLS = "tools/testing/route_smoke.py, api_contract_tester.py, fathomdesk_smoke.py"


class BackendUnavailable(RuntimeError):
    """The requested (or resolved) backend cannot be provided on this host.

    Raised — rather than silently degraded — when an explicit ``cdp``/``selenium``
    request cannot be honoured, or when ``auto`` lands on Tier 3.
    """


@dataclass
class BackendResolution:
    backend: str      # BACKEND_CDP | BACKEND_SELENIUM | BACKEND_HTTP_ONLY
    tier: int         # 1 | 2 | 3
    reason: str
    requested: str    # "auto" | "cdp" | "selenium"


def _requested(requested: Optional[str]) -> str:
    req = (requested or os.environ.get(_ENV_VAR, "auto")).strip().lower()
    if req not in _VALID_REQUESTS:
        raise BackendUnavailable(
            f"invalid {_ENV_VAR}={req!r}; expected one of {_VALID_REQUESTS}"
        )
    return req


def resolve_backend(requested: Optional[str] = None) -> BackendResolution:
    """Resolve which backend to use, enforcing the never-degrade rule for explicit
    requests. Does not construct anything."""
    req = _requested(requested)
    decision = preflight(requested=req)["decision"]
    backend = decision["name"]  # "cdp" | "selenium" | "http-only"

    # Explicit requests never degrade: if the chosen backend is not what was asked
    # for, that is a hard failure, not a quiet fallback.
    if req == "cdp" and backend != BACKEND_CDP:
        raise BackendUnavailable(f"cdp backend requested but unavailable: {decision['reason']}")
    if req == "selenium" and backend != BACKEND_SELENIUM:
        raise BackendUnavailable(f"selenium backend requested but unavailable: {decision['reason']}")

    logger.info("[backend] requested=%s -> %s (tier %d): %s", req, backend, decision["tier"], decision["reason"])
    return BackendResolution(backend=backend, tier=decision["tier"], reason=decision["reason"], requested=req)


def create_backend(
    requested: Optional[str] = None,
    *,
    headless: bool = True,
    window_size: Tuple[int, int] = (1920, 1080),
) -> Any:
    """Resolve and construct the browser driver.

    Returns a ``CDPDriver`` (Tier 1) or a Selenium ``WebDriver`` (Tier 2). Raises
    :class:`BackendUnavailable` at Tier 3, naming the surviving HTTP-only tools —
    the loud degradation the spike requires, never a silent None.
    """
    res = resolve_backend(requested)

    if res.backend == BACKEND_CDP:
        from tools.browser.cdp.driver import CDPDriver
        return CDPDriver.create(headless=headless, window_size=window_size)

    if res.backend == BACKEND_SELENIUM:
        from tools.browser.driver_manager import DriverManager
        return DriverManager.instance().create_driver(headless=headless, window_size=window_size)

    raise BackendUnavailable(
        f"no browser transport available (Tier 3): {res.reason} "
        f"Use browser-free HTTP verification — {_TIER3_TOOLS}."
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json
    import sys
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description="Browser backend resolver (cdp-port-04)")
    parser.add_argument("--requested", choices=_VALID_REQUESTS, default=None,
                        help=f"Override {_ENV_VAR} (default: env or 'auto')")
    parser.add_argument("--json", action="store_true", help="JSON output")
    ns = parser.parse_args()

    try:
        res = resolve_backend(ns.requested)
        print(json.dumps(asdict(res), indent=2))
    except BackendUnavailable as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        sys.exit(1)
