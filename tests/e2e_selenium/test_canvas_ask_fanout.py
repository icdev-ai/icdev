# CUI // SP-CTI
"""E2E Test: per-canvas /ask fan-out (PDC, BDC, DDC, ODC, IDC).

Verifies the shared canvas_ask.handle_ask_request helper + canvas_ask.html
template work uniformly across the 5 canvases indexed by
tools/knowledge_graph/canvas_indexer.py. Each canvas gets:

  1. GET  /<prefix>/ask       → 200, page renders with #q and #ask-btn
  2. POST /<prefix>/api/ask   → returns nodes_returned >= 1 for a
                                well-chosen probe query (from design data)
  3. POST /<prefix>/api/ask   → returns 400 on empty query

Prerequisites:
  - Flask dashboard running
  - canvas_indexer has been run (kg_nodes populated for each graph_id)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.browser.driver_manager import get_driver  # noqa: E402

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")

# (route_prefix, canvas label for error messages, probe query expected to hit)
CANVASES = [
    ("/devops",        "PDC", "build"),
    ("/boundary",      "BDC", "boundary"),
    ("/data",          "DDC", "table"),
    ("/observability", "ODC", "detection"),
    ("/infra",         "IDC", "compute"),  # matches entity_type=idc_compute
]


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


def _post(url: str, body: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"_raw": e.read()[:300].decode("utf-8", "replace")}


@pytest.mark.e2e_selenium
@pytest.mark.parametrize("prefix,canvas,probe", CANVASES)
def test_canvas_ask_page_loads(driver, prefix, canvas, probe):
    driver.get(f"{BASE_URL}{prefix}/ask")
    q = driver.execute_script("return document.getElementById('q') !== null")
    btn = driver.execute_script("return document.getElementById('ask-btn') !== null")
    assert q, f"[{canvas}] #q missing on {prefix}/ask"
    assert btn, f"[{canvas}] #ask-btn missing on {prefix}/ask"


@pytest.mark.e2e_selenium
@pytest.mark.parametrize("prefix,canvas,probe", CANVASES)
def test_canvas_ask_probe_returns_nodes(prefix, canvas, probe):
    status, body = _post(f"{BASE_URL}{prefix}/api/ask", {"query": probe, "top_k": 10})
    assert status == 200, f"[{canvas}] status={status} body={body}"
    assert body.get("nodes_returned", 0) >= 1, \
        f"[{canvas}] probe '{probe}' returned {body.get('nodes_returned')} nodes; body={body}"
    assert body.get("graph_id", "").endswith("-designs"), f"[{canvas}] wrong graph_id: {body.get('graph_id')}"


@pytest.mark.e2e_selenium
@pytest.mark.parametrize("prefix,canvas,probe", CANVASES)
def test_canvas_ask_empty_query_400(prefix, canvas, probe):
    status, body = _post(f"{BASE_URL}{prefix}/api/ask", {"query": ""})
    assert status == 400, f"[{canvas}] expected 400, got {status}: {body}"
# CUI // SP-CTI
