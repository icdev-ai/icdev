# CUI // SP-CTI
"""E2E Test: Pipeline canvas orphan-edge filter.

Regression for the same bug class as tests/e2e_selenium/test_ndc_canvas_tooltips.py,
audited on the pipeline canvas 2026-04-18. pipeline-canvas.js's loadGraph +
createLink accepted edges whose source/target IDs referenced missing nodes,
resulting in invisible links at origin.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - Pipeline canvas mounted at /devops/canvas/new
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
def test_pipeline_orphan_edges_dropped_with_warning(driver):
    """loadGraph must skip edges whose endpoints don't exist and log a warning."""
    driver.get(f"{BASE_URL}/devops/canvas/new")
    result = driver.execute_async_script(
        """
        const cb = arguments[arguments.length - 1];
        const warnings = [];
        const origWarn = console.warn;
        console.warn = function() { warnings.push(Array.from(arguments).map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(' ')); origWarn.apply(console, arguments); };

        const deadline = Date.now() + 5000;
        (function poll() {
          if (typeof loadGraph === 'function' && typeof graph !== 'undefined') {
            loadGraph({
              nodes: [
                { id: 'n1', type: 'build', x: 100, y: 100, label: 'Build' },
                { id: 'n2', type: 'test',  x: 300, y: 100, label: 'Test'  },
              ],
              edges: [
                { id: 'e-good',   source: 'n1',      target: 'n2' },
                { id: 'e-orphan', source: 'n1',      target: 'ghost' },
                { id: 'e-both',   source: 'missing', target: 'nowhere' },
              ],
            });
            const linkCount = graph.getLinks ? graph.getLinks().length : graph.getCells().filter(c => c.isLink()).length;
            cb({linkCount: linkCount, warnings: warnings});
          } else if (Date.now() > deadline) {
            cb({error: 'loadGraph or graph not ready'});
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
