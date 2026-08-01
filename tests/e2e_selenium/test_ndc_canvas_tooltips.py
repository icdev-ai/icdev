# CUI // SP-CTI
"""E2E Test: NDC canvas tooltips + orphan-edge filter.

Regression for two bugs triaged 2026-04-18:
  1. tools/dashboard/static/js/canvas-tooltips.js was referenced from
     network/canvas.html but never existed → tooltips failed silently.
  2. loadGraphJSON / createLink accepted edges whose source/target IDs
     referred to missing nodes → JointJS rendered invisible links.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - A new (empty) topology can be created via /network/canvas
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.mark.e2e_selenium
def test_canvas_tooltips_script_loads(driver):
    """canvas-tooltips.js must resolve (no 404) and expose initEnhancedTooltips."""
    driver.get(f"{BASE_URL}/network/canvas/new")
    # Wait for JointJS paper init (network-canvas.js polls until `paper` defined)
    exposed = driver.execute_async_script(
        """
        const cb = arguments[arguments.length - 1];
        const deadline = Date.now() + 5000;
        (function poll() {
          if (typeof window.initEnhancedTooltips === 'function') {
            cb({present: true});
          } else if (Date.now() > deadline) {
            cb({present: false});
          } else {
            setTimeout(poll, 100);
          }
        })();
        """
    )
    assert exposed["present"], "initEnhancedTooltips not exposed — canvas-tooltips.js failed to load"


@pytest.mark.e2e_selenium
def test_orphan_edges_dropped_with_warning(driver):
    """loadGraphJSON must skip edges whose endpoints don't exist and log a warning."""
    driver.get(f"{BASE_URL}/network/canvas/new")
    # Capture console.warn calls, then feed a graph with 1 valid + 1 orphan edge
    result = driver.execute_async_script(
        """
        const cb = arguments[arguments.length - 1];
        const warnings = [];
        const origWarn = console.warn;
        console.warn = function() { warnings.push(Array.from(arguments).map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')); origWarn.apply(console, arguments); };

        const deadline = Date.now() + 5000;
        (function poll() {
          if (typeof loadGraphJSON === 'function' && typeof graph !== 'undefined') {
            loadGraphJSON({
              nodes: [
                { id: 'n1', type: 'router', x: 100, y: 100, label: 'R1' },
                { id: 'n2', type: 'switch', x: 300, y: 100, label: 'S1' },
              ],
              edges: [
                { id: 'e-good',   source: 'n1',      target: 'n2' },
                { id: 'e-orphan', source: 'n1',      target: 'ghost' },
                { id: 'e-both',   source: 'missing', target: 'nowhere' },
              ],
            });
            // Count links on the graph
            const linkCount = graph.getLinks ? graph.getLinks().length : graph.getCells().filter(c => c.isLink()).length;
            cb({linkCount: linkCount, warnings: warnings});
          } else if (Date.now() > deadline) {
            cb({error: 'loadGraphJSON or graph not ready'});
          } else {
            setTimeout(poll, 100);
          }
        })();
        """
    )
    assert "error" not in result, result.get("error")
    assert result["linkCount"] == 1, f"expected 1 valid link, got {result['linkCount']}"
    joined = " | ".join(result["warnings"])
    assert "missing endpoint" in joined or "orphan edge" in joined, \
        f"expected orphan-edge warning, got: {joined}"
# CUI // SP-CTI
