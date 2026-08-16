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

# (route_prefix, canvas label for error messages, probe query expected to hit)
#
# The probe must be a token the canvas's indexed design data actually contains,
# otherwise a 0-node result says nothing about retrieval. Three of these had
# gone stale against the entity types canvas_indexer.py writes today (measured
# 2026-08-16 against the live kg_nodes inventory, rem-e2e-01):
#   DDC "table"     -> no such type; types are ddc_service/ddc_database/ddc_api
#   ODC "detection" -> no such type; types are odc_col-*/odc_src-*/odc_plt-*
#   IDC "compute"   -> the entity_type=idc_compute named in the old comment no
#                      longer exists; types are idc_switch/idc_k8s/idc_db/...
# Keep this table in step with canvas_indexer.py: a probe naming a type that is
# gone reports a retrieval regression that is not happening.
CANVASES = [
    ("/devops",        "PDC", "build"),      # pdc_* build/pipeline nodes
    ("/boundary",      "BDC", "boundary"),   # bdc_bnd-* boundary nodes
    ("/data",          "DDC", "database"),   # ddc_database
    ("/observability", "ODC", "collector"),  # odc_col-otel / odc_col-fluentd
    ("/infra",         "IDC", "switch"),     # idc_switch
]


def _graph_id(canvas: str) -> str:
    """The kg_nodes graph_id a canvas's /ask endpoint is scoped to."""
    return f"{canvas.lower()}-designs"


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


# The first /api/ask against a given canvas warms the retrieval path, and the
# 15s this used to allow was under that cost: the SAME probe that passes in ~2s
# on its own timed out when run after its siblings, so the module's verdict
# depended on how many tests preceded it (measured 2026-08-16 — 16 passed, then
# 3 failed, for the identical command against the identical dashboard).
# A TimeoutError raised here is indistinguishable from "the endpoint answered
# with 0 nodes", which is the one thing these tests exist to detect, so a tight
# bound converts cold-start latency into a fake retrieval regression. None of
# these tests asserts a latency SLA; the assertion is on nodes_returned. The
# bound is therefore set where only a genuine hang can reach it.
_ASK_TIMEOUT = 90


def _post(url: str, body: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=JSON_POST_HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_ASK_TIMEOUT) as resp:
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
def test_canvas_ask_probe_returns_nodes(prefix, canvas, probe, require_graph_populated):
    require_graph_populated(_graph_id(canvas))
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
