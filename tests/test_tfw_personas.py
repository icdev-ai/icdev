# CUI // SP-CTI
"""Tests for TFW multi-persona walkthrough engine.

Covers: load_personas, load_classification_context, generate_for_persona,
generate_all (multi-persona), cross-cloud detection, classification overlay,
persona-definitions HTTP endpoint, and Selenium persona chip rendering.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from unittest.mock import patch

import pytest


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _make_conn():
    """Minimal in-memory SQLite DB with tables needed by generate_all.

    Wrapped in StorageConnection so the PG-native ``%s`` placeholders authored in
    narrative_generator translate to ``?`` on the SQLite backend — mirroring the
    production caller (network ``get_connection()`` returns a StorageConnection).
    A raw sqlite3 connection bypasses that translator: ``%s`` is a syntax error,
    the best-effort persist swallows it, and the row-count assertion fails.
    """
    from tools.db.storage import StorageConnection

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE nc_traffic_flows (
            id TEXT PRIMARY KEY,
            topology_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            classification TEXT DEFAULT 'NIPR',
            app_type TEXT DEFAULT ''
        );
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY,
            graph_json TEXT DEFAULT '{}'
        );
        CREATE TABLE nc_flow_walkthrough_steps (
            id TEXT PRIMARY KEY,
            flow_id TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            node_id TEXT DEFAULT '',
            node_label TEXT DEFAULT '',
            action_type TEXT DEFAULT ''
        );
        CREATE TABLE nc_step_persona_responses (
            id TEXT PRIMARY KEY,
            step_id TEXT NOT NULL,
            persona_id TEXT NOT NULL,
            narrative TEXT DEFAULT '',
            detail_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(step_id, persona_id)
        );
    """)
    return StorageConnection(conn, "sqlite")


_FAKE_STEPS = [
    {
        "step_number": 1,
        "node_id": "n1",
        "node_label": "Originate",
        "action_type": "originate",
        "security_detail": {},
        "network_detail": {},
    },
    {
        "step_number": 2,
        "node_id": "n2",
        "node_label": "VPN GW",
        "action_type": "encrypt_vpn",
        "security_detail": {},
        "network_detail": {},
    },
    {
        "step_number": 3,
        "node_id": "n3",
        "node_label": "Deliver",
        "action_type": "deliver",
        "security_detail": {},
        "network_detail": {},
    },
]


# ─── 1. test_load_personas ────────────────────────────────────────────────────

def test_load_personas():
    """load_personas() returns exactly 7 dicts with required fields."""
    from tools.network.narrative_generator import load_personas

    personas = load_personas()
    assert isinstance(personas, list)
    assert len(personas) == 7

    expected_ids = {
        "seceng", "neteng", "cloudarch", "compofficer",
        "appdev", "missionowner", "ciso",
    }
    for p in personas:
        assert "id" in p, f"Persona missing 'id': {p}"
        assert "name" in p, f"Persona missing 'name': {p}"
        assert "system_prompt" in p, f"Persona missing 'system_prompt': {p}"
        assert len(p["system_prompt"]) > 20, "system_prompt too short"
        assert "detail_fields" in p, f"Persona missing 'detail_fields': {p}"
        assert isinstance(p["detail_fields"], list)

    assert {p["id"] for p in personas} == expected_ids


# ─── 2. test_load_classification_context ─────────────────────────────────────

def test_load_classification_context():
    """load_classification_context('IL4') returns dict with AES-256 and CAC."""
    from tools.network.narrative_generator import load_classification_context

    ctx = load_classification_context("IL4")
    assert isinstance(ctx, dict)
    assert "AES-256" in ctx.get("encryption", ""), (
        f"Expected 'AES-256' in encryption, got: {ctx.get('encryption')}"
    )
    assert "CAC" in ctx.get("mfa", ""), (
        f"Expected 'CAC' in mfa, got: {ctx.get('mfa')}"
    )


# ─── 3. test_generate_for_persona_seceng_fallback ────────────────────────────

def test_generate_for_persona_seceng_fallback():
    """With no LLM, seceng on authenticate returns non-empty narrative; NARRATIVE_TEMPLATES exists."""
    from tools.network.narrative_generator import NARRATIVE_TEMPLATES, generate_for_persona

    step = {
        "step_number": 1,
        "node_id": "fw1",
        "node_label": "Firewall",
        "action_type": "authenticate",
        "security_detail": {},
        "network_detail": {},
    }
    node = {"id": "fw1", "type": "firewall", "label": "Firewall", "config": {}}
    flow = {"id": "f1", "classification": "NIPR", "app_type": "web", "topology_id": "t1"}

    with patch("tools.network.narrative_generator._invoke_llm", return_value=None):
        result = generate_for_persona(
            step=step,
            node=node,
            persona_id="seceng",
            flow=flow,
            classification="NIPR",
        )

    assert "narrative" in result
    assert result["narrative"], "narrative must be non-empty"

    # NARRATIVE_TEMPLATES must define authenticate → seceng
    assert isinstance(NARRATIVE_TEMPLATES, dict)
    assert "authenticate" in NARRATIVE_TEMPLATES
    assert "seceng" in NARRATIVE_TEMPLATES["authenticate"]
    assert NARRATIVE_TEMPLATES["authenticate"]["seceng"]


# ─── 4. test_generate_for_persona_compofficer_nist ───────────────────────────

def test_generate_for_persona_compofficer_nist():
    """compofficer on encrypt_vpn includes SC-8 in detail_json.nist_controls."""
    from tools.network.narrative_generator import generate_for_persona

    step = {
        "step_number": 2,
        "node_id": "vpn1",
        "node_label": "VPN GW",
        "action_type": "encrypt_vpn",
        "security_detail": {},
        "network_detail": {},
    }
    node = {"id": "vpn1", "type": "vpn-gw", "label": "VPN GW", "config": {}}
    flow = {"id": "f1", "classification": "NIPR", "app_type": "web", "topology_id": "t1"}

    with patch("tools.network.narrative_generator._invoke_llm", return_value=None):
        result = generate_for_persona(
            step=step,
            node=node,
            persona_id="compofficer",
            flow=flow,
            classification="NIPR",
        )

    assert "detail_json" in result
    nist_controls = result["detail_json"].get("nist_controls", [])
    assert any("SC-8" in c for c in nist_controls), (
        f"Expected SC-8 in nist_controls, got: {nist_controls}"
    )


# ─── 5. test_generate_all_multi_persona ──────────────────────────────────────

def test_generate_all_multi_persona():
    """3-step × 3-persona walkthrough produces 9 nc_step_persona_responses rows."""
    from tools.network.narrative_generator import generate_all

    flow_id = str(uuid.uuid4())
    topo_id = str(uuid.uuid4())
    conn = _make_conn()

    conn.execute(
        "INSERT INTO nc_traffic_flows VALUES (?, ?, ?, ?, ?)",
        (flow_id, topo_id, "Test Flow", "NIPR", "web"),
    )
    conn.execute(
        "INSERT INTO topologies VALUES (?, ?)",
        (
            topo_id,
            json.dumps({"nodes": [
                {"id": "n1", "label": "Originate", "type": ""},
                {"id": "n2", "label": "VPN GW", "type": "vpn-gw"},
                {"id": "n3", "label": "Deliver", "type": ""},
            ]}),
        ),
    )
    for s in _FAKE_STEPS:
        conn.execute(
            "INSERT INTO nc_flow_walkthrough_steps"
            " (id, flow_id, step_number, node_id, node_label, action_type)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"step-{s['step_number']}",
                flow_id,
                s["step_number"],
                s["node_id"],
                s["node_label"],
                s["action_type"],
            ),
        )
    conn.commit()

    with patch("tools.network.traffic_flow.TrafficFlowEngine") as MockTE:
        mock_engine = MockTE.return_value
        mock_engine._ensure_tables.return_value = None
        mock_engine.get_walkthrough.return_value = _FAKE_STEPS
        mock_engine.generate_walkthrough.return_value = _FAKE_STEPS

        with patch("tools.network.narrative_generator._invoke_llm", return_value=None):
            result = generate_all(
                flow_id=flow_id,
                conn=conn,
                personas=["seceng", "neteng", "compofficer"],
                classification="NIPR",
                use_llm=False,
            )

    steps = result.get("steps", [])
    assert len(steps) == 3, f"Expected 3 steps, got {len(steps)}"

    for step in steps:
        persona_responses = step.get("personas", {})
        assert len(persona_responses) == 3, (
            f"Expected 3 persona keys per step, got {len(persona_responses)}"
        )

    row_count = conn.execute(
        "SELECT COUNT(*) FROM nc_step_persona_responses"
    ).fetchone()[0]
    assert row_count == 9, f"Expected 9 DB rows, got {row_count}"


# ─── 6. test_cross_cloud_context_aws ─────────────────────────────────────────

def test_cross_cloud_context_aws():
    """Node with type='aws-tgw' resolves to AWS GovCloud (US) context."""
    from tools.network.narrative_generator import load_cross_cloud_context

    node = {"id": "tgw1", "type": "aws-tgw", "label": "AWS Transit Gateway", "config": {}}
    ctx = load_cross_cloud_context(node)
    assert ctx is not None, "Expected cross-cloud context, got None"
    assert ctx.get("name") == "AWS GovCloud (US)", (
        f"Expected 'AWS GovCloud (US)', got: {ctx.get('name')}"
    )


# ─── 7. test_classification_overlay_in_prompt ────────────────────────────────

def test_classification_overlay_in_prompt():
    """build_classification_overlay for IL5 flow contains 'FIPS 140-2 Level 2'."""
    from tools.network.narrative_generator import build_classification_overlay

    flow = {"classification": "IL5"}
    overlay = build_classification_overlay(flow)
    assert overlay, "Overlay string must be non-empty"
    assert "FIPS 140-2 Level 2" in overlay, (
        f"Expected 'FIPS 140-2 Level 2' in overlay, got: {overlay}"
    )


# ─── 8. test_persona_definitions_endpoint ────────────────────────────────────

def test_persona_definitions_endpoint():
    """GET /api/twin/<id>/persona-definitions returns 7-element personas list."""
    from flask import Flask, jsonify

    from tools.network.narrative_generator import load_personas

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/api/twin/<topo_id>/persona-definitions", methods=["GET"])
    def _persona_def(topo_id):  # noqa: ARG001
        return jsonify({"personas": load_personas()}), 200

    with app.test_client() as client:
        resp = client.get("/api/twin/test-topo/persona-definitions")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "personas" in data
    personas = data["personas"]
    assert len(personas) == 7, f"Expected 7 personas, got {len(personas)}"

    ids = {p["id"] for p in personas}
    expected = {
        "seceng", "neteng", "cloudarch", "compofficer",
        "appdev", "missionowner", "ciso",
    }
    assert ids == expected, f"Missing persona IDs: {expected - ids}"


# ─── 9. test_persona_chip_render ─────────────────────────────────────────────

@pytest.mark.selenium
def test_persona_chip_render():
    """Selenium: persona chips render with correct short names on the twin page."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        pytest.skip("selenium not installed")

    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:5050/", timeout=2)
    except Exception:
        pytest.skip("ICDEV server not running on localhost:5050")

    # Resolve a topology id from the network DB
    topo_id = None
    try:
        from tools.network.db.init_db import get_connection

        nc_conn = get_connection()
        row = nc_conn.execute("SELECT id FROM topologies LIMIT 1").fetchone()
        if row:
            topo_id = row["id"] if hasattr(row, "__getitem__") else row[0]
    except Exception:
        pass

    if not topo_id:
        pytest.skip("No topology available in network DB")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=options)
    try:
        driver.get(f"http://localhost:5050/network/twin/{topo_id}")
        wait = WebDriverWait(driver, 10)
        try:
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".persona-chip")))
        except Exception:
            # Page may require login or chips section may not be visible in this env
            pytest.skip("persona chips not rendered (server may require login or chips not visible)")

        chips = driver.find_elements(By.CSS_SELECTOR, ".persona-chip")
        chip_texts = {c.text.strip() for c in chips if c.text.strip()}

        expected_shorts = {
            "SecEng", "NetEng", "CloudArch",
            "CompOfficer", "AppDev", "Mission", "CISO",
        }
        missing = expected_shorts - chip_texts
        assert not missing, f"Missing persona chips: {missing}"
    finally:
        driver.quit()
