# CUI // SP-CTI
"""E2E Test: NDC GraphRAG /network/ask — DT-Canvas pilot #1.

Pilot run of the "/ask-<canvas>" per-canvas GraphRAG pattern (from the
2026-04-18 digital-twin adaptation audit). Asserts:

  1. /network/ask page loads (200, renders the form).
  2. /network/api/ask scoped to ndc-network-intelligence KG returns
     firewall nodes for the query "firewall" (regression for the
     entity_type matching fix in graph_rag.retrieve).

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - NDC KG populated (kg_nodes graph_id='ndc-network-intelligence')
"""
from __future__ import annotations

import os
import sys
import urllib.request
import urllib.error
import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
GRAPH_ID = "ndc-network-intelligence"

# A browser stamps Sec-Fetch-Site on every request and page JS cannot forge it,
# so tools/security/csrf.py accepts it as proof a mutating request is not a
# cross-site forgery. These raw urllib POSTs send no cookies and previously no
# such header, so every one was rejected 403 CSRF_FAILED — a transport artifact
# that masked whatever the endpoint actually returns (rem-e2e-01). Sending what
# the page under test sends measures the endpoint instead of the CSRF shim.
JSON_POST_HEADERS = {
    "Content-Type": "application/json",
    "Sec-Fetch-Site": "same-origin",
}

# The first /api/ask warms the retrieval path, and a tight bound turns that
# cold-start cost into a TimeoutError indistinguishable from "the endpoint
# returned 0 nodes" — the one thing this module exists to detect. No assertion
# here is about latency, so the bound sits where only a genuine hang reaches it.
# See the matching note in test_canvas_ask_fanout.py (rem-e2e-01).
_ASK_TIMEOUT = 90


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.mark.e2e_selenium
def test_ndc_ask_page_loads(driver):
    """The /network/ask page renders with a query form."""
    driver.get(f"{BASE_URL}/network/ask")
    # Ask form input must be present
    q = driver.execute_script("return document.getElementById('q') !== null")
    btn = driver.execute_script("return document.getElementById('ask-btn') !== null")
    assert q, "query input (#q) missing on /network/ask"
    assert btn, "ask button (#ask-btn) missing on /network/ask"


@pytest.mark.e2e_selenium
def test_ndc_api_ask_firewall_matches(require_graph_populated):
    """POST /network/api/ask {query:'firewall'} returns >=1 firewall node.

    Regression: graph_rag.retrieve previously missed entity_type matches,
    so "firewall" returned 0 against nodes with entity_type=network_firewall
    (labels are abbreviated like NYC-FWLL-01).
    """
    require_graph_populated(GRAPH_ID)
    req = urllib.request.Request(
        f"{BASE_URL}/network/api/ask",
        data=json.dumps({"query": "firewall", "top_k": 10}).encode("utf-8"),
        headers=JSON_POST_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_ASK_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        pytest.fail(f"api/ask HTTP {e.code}: {e.read()[:300].decode('utf-8','replace')}")

    assert body.get("profile") == "network_infrastructure", body
    assert body.get("nodes_returned", 0) >= 1, f"expected >=1 node, got {body}"
    types = {n.get("entity_type") for n in body.get("nodes", []) if isinstance(n, dict)}
    assert "network_firewall" in types, f"expected network_firewall entity_type in {types}"


@pytest.mark.e2e_selenium
def test_ndc_api_ask_empty_query_400():
    """Empty query must return 400."""
    req = urllib.request.Request(
        f"{BASE_URL}/network/api/ask",
        data=json.dumps({"query": ""}).encode("utf-8"),
        headers=JSON_POST_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            pytest.fail(f"expected 400, got {resp.status}")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}"
# CUI // SP-CTI
