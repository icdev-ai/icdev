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

For agents, use the indexed page representation instead of raw Selenium —
a model acts via ``click(14)``, not an invented CSS selector::

    from tools.browser import AgentBrowser

    with AgentBrowser() as b:
        print(b.navigate("http://localhost:5050").to_text())
        b.click(14)
"""

from tools.browser.driver_manager import get_driver, DriverManager
from tools.browser.agent_browser import AgentBrowser, PageState, IndexedElement

__all__ = [
    "get_driver",
    "DriverManager",
    "AgentBrowser",
    "PageState",
    "IndexedElement",
]
