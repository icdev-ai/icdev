# CUI // SP-CTI
"""E2E Test: SDC GraphRAG /security/ask — DT-Canvas pilot #2.

Second canvas rollout of the per-canvas GraphRAG pattern. Uses the
existing SDC crosswalk graph (STRIDE × NIST × frameworks) with the
security scoring profile. NDC shipped the pattern; SDC proves it
replicates to a different domain/graph without code changes beyond
a blueprint block + template.

Prerequisites:
  - Flask dashboard running on http://localhost:5050
  - SDC KG populated (kg_nodes graph_id='sdc-kg-9acdfb94bea0')
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


@pytest.fixture(scope="module")
def driver():
    drv = get_driver(headless=True, window_size=(1920, 1080))
    drv.implicitly_wait(5)
    yield drv
    drv.quit()


@pytest.mark.e2e_selenium
def test_sdc_ask_page_loads(driver):
    driver.get(f"{BASE_URL}/security/ask")
    q = driver.execute_script("return document.getElementById('q') !== null")
    btn = driver.execute_script("return document.getElementById('ask-btn') !== null")
    assert q, "query input (#q) missing on /security/ask"
    assert btn, "ask button (#ask-btn) missing on /security/ask"


@pytest.mark.e2e_selenium
def test_sdc_api_ask_spoofing_matches_stride():
    """Query 'spoofing' must return the STRIDE:Spoofing category node."""
    req = urllib.request.Request(
        f"{BASE_URL}/security/api/ask",
        data=json.dumps({"query": "spoofing", "top_k": 10}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    assert body.get("profile") == "security", body
    assert body.get("nodes_returned", 0) >= 1, f"expected >=1 node, got {body}"
    labels = {n.get("label") for n in body.get("nodes", []) if isinstance(n, dict)}
    assert any("Spoofing" in lbl for lbl in labels if lbl), \
        f"expected a STRIDE:Spoofing label in {labels}"


@pytest.mark.e2e_selenium
def test_sdc_api_ask_threat_matches_entity_type():
    """Query 'threat' must return sdc_threat_type entries — proves the
    entity_type matching fix in graph_rag.retrieve applies to SDC too."""
    req = urllib.request.Request(
        f"{BASE_URL}/security/api/ask",
        data=json.dumps({"query": "threat", "top_k": 10}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    types = {n.get("entity_type") for n in body.get("nodes", []) if isinstance(n, dict)}
    assert "sdc_threat_type" in types, f"expected sdc_threat_type in {types}"


@pytest.mark.e2e_selenium
def test_sdc_api_ask_empty_query_400():
    req = urllib.request.Request(
        f"{BASE_URL}/security/api/ask",
        data=json.dumps({"query": ""}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            pytest.fail(f"expected 400, got {resp.status}")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"expected 400, got {e.code}"
# CUI // SP-CTI
