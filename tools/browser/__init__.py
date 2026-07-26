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

For agent-facing use — an indexed representation of the page's interactive
elements, so a model can act via ``click(14)`` instead of guessing a selector::

    from tools.browser import AgentBrowser

    with AgentBrowser() as ab:
        state = ab.navigate("http://localhost:5050/kanban")
"""

from tools.browser.driver_manager import get_driver, DriverManager
from tools.browser.agent_browser import AgentBrowser

__all__ = ["get_driver", "DriverManager", "AgentBrowser"]
