# CUI // SP-CTI
"""ICDEV™ Browser automation layer.

Provides a unified WebDriver factory backed by vendored drivers.
No runtime downloads — all drivers must be pre-installed via
``tools/airgap/driver_vendor.py``.

Usage::

    from tools.browser import get_driver

    driver = get_driver()          # headless Edge or Chrome
    driver.get("http://localhost:5050")
    driver.quit()

Anything **agent-driven** goes through ``scope.GuardedDriver`` instead, which
enforces the domain allowlist, the per-run action cap, the per-step timeout,
credential placeholder substitution, and the audit trail::

    from tools.browser import get_driver, GuardedDriver

    driver = get_driver()
    try:
        session = GuardedDriver(driver, run_id="vv-001")
        session.navigate("http://localhost:5050/")   # allowlisted
    finally:
        driver.quit()

For agent-facing use — an indexed representation of the page's interactive
elements, so a model can act via ``click(14)`` instead of guessing a selector.
``AgentBrowser`` runs its own scheme allowlist and defers every http(s) target
to ``scope.check_navigation``, so it can only ever be narrower than the
canonical policy::

    from tools.browser import AgentBrowser

    with AgentBrowser() as ab:
        state = ab.navigate("http://localhost:5050/kanban")
"""

from tools.browser.driver_manager import get_driver, DriverManager
from tools.browser.agent_browser import AgentBrowser
from tools.browser.scope import (
    ActionBudget,
    ActionBudgetExceeded,
    BrowserScopeConfig,
    GuardedDriver,
    NavigationDenied,
    ScopeDecision,
    ScopeViolation,
    SecretResolutionError,
    SensitiveDataResolver,
    StepTimeout,
    check_navigation,
    load_scope_config,
)

__all__ = [
    "get_driver",
    "DriverManager",
    "AgentBrowser",
    "GuardedDriver",
    "BrowserScopeConfig",
    "ScopeDecision",
    "ActionBudget",
    "SensitiveDataResolver",
    "ScopeViolation",
    "NavigationDenied",
    "ActionBudgetExceeded",
    "StepTimeout",
    "SecretResolutionError",
    "check_navigation",
    "load_scope_config",
]
