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

Anything **agent-driven** goes through ``scope.GuardedDriver``, which enforces
the domain allowlist, the per-run action cap, the per-step timeout, credential
placeholder substitution, and the audit trail.

Agents do not drive the guard directly — they use ``AgentBrowser``, the indexed
page representation built on top of it, so a model acts via ``click(14)``
rather than an invented CSS selector it cannot verify::

    from tools.browser import AgentBrowser

    with AgentBrowser(run_id="vv-001") as b:
        print(b.navigate("http://localhost:5050").to_text())
        b.click(14)

Every navigation is allowlist-gated and every action is budgeted and audited,
because ``AgentBrowser`` holds a ``GuardedDriver`` and has no unguarded path to
the raw session.
"""

from tools.browser.driver_manager import get_driver, DriverManager
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
from tools.browser.agent_browser import AgentBrowser, PageState, IndexedElement

__all__ = [
    "get_driver",
    "DriverManager",
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
    "AgentBrowser",
    "PageState",
    "IndexedElement",
]
