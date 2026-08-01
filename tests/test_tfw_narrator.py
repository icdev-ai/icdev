# CUI // SP-CTI
"""Tests for TFW Narrative Generator.

Covers: detect_csp, build_classification_overlay, generate_for_persona
(seceng / compofficer / neteng / appdev), NARRATIVE_TEMPLATES structure,
and generate_all summary output.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from unittest.mock import patch



# ─── Shared helpers ───────────────────────────────────────────────────────────


def _make_conn():
    # Wrapped in StorageConnection so narrative_generator's PG-native %s
    # placeholders translate to ? on SQLite — mirroring the production caller
    # (network get_connection() returns a StorageConnection). A raw sqlite3
    # connection bypasses that translator and %s becomes a syntax error.
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
        "security_detail": {"domain_type": "nipr"},
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


# ─── 1. test_detect_csp_aws ───────────────────────────────────────────────────


def test_detect_csp_aws():
    """Node with type='aws-tgw' resolves to 'aws_govcloud'."""
    from tools.network.narrative_generator import detect_csp

    node = {"id": "tgw1", "type": "aws-tgw", "label": "AWS TGW", "config": {}}
    assert detect_csp(node) == "aws_govcloud"


def test_detect_csp_azure():
    """Node with type='azure-vnet' resolves to 'azure_gov'."""
    from tools.network.narrative_generator import detect_csp

    node = {"id": "vnet1", "type": "azure-vnet", "label": "Azure VNet", "config": {}}
    assert detect_csp(node) == "azure_gov"


def test_detect_csp_none():
    """Plain on-prem node returns None."""
    from tools.network.narrative_generator import detect_csp

    node = {"id": "fw1", "type": "firewall", "label": "FW", "config": {}}
    assert detect_csp(node) is None


# ─── 2. test_build_classification_overlay ────────────────────────────────────


def test_build_classification_overlay_nipr():
    """build_classification_overlay for NIPR flow returns non-empty string."""
    from tools.network.narrative_generator import build_classification_overlay

    overlay = build_classification_overlay({"classification": "NIPR"})
    assert isinstance(overlay, str)
    assert overlay, "Overlay must be non-empty"
    assert "AES-256" in overlay


def test_build_classification_overlay_il5():
    """build_classification_overlay for IL5 contains 'FIPS 140-2 Level 2'."""
    from tools.network.narrative_generator import build_classification_overlay

    overlay = build_classification_overlay({"classification": "IL5"})
    assert "FIPS 140-2 Level 2" in overlay, (
        f"Expected 'FIPS 140-2 Level 2' in overlay, got: {overlay}"
    )


# ─── 3. test_narrative_templates_structure ───────────────────────────────────


def test_narrative_templates_structure():
    """NARRATIVE_TEMPLATES covers authenticate / encrypt_vpn / security_inspect for all 7 personas."""
    from tools.network.narrative_generator import NARRATIVE_TEMPLATES

    required_actions = {"authenticate", "encrypt_vpn", "security_inspect", "originate", "deliver"}
    required_personas = {"seceng", "neteng", "cloudarch", "compofficer", "appdev", "missionowner", "ciso"}

    assert isinstance(NARRATIVE_TEMPLATES, dict)
    for action in required_actions:
        assert action in NARRATIVE_TEMPLATES, f"Missing action '{action}' in NARRATIVE_TEMPLATES"
        for persona in required_personas:
            assert persona in NARRATIVE_TEMPLATES[action], (
                f"Missing persona '{persona}' for action '{action}'"
            )
            assert NARRATIVE_TEMPLATES[action][persona], (
                f"Empty template for {action}/{persona}"
            )


# ─── 4. test_generate_for_persona_seceng_fallback ────────────────────────────


def test_generate_for_persona_seceng_fallback():
    """With no LLM, seceng on authenticate returns non-empty narrative."""
    from tools.network.narrative_generator import generate_for_persona

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
    assert "detail_json" in result
    assert result["detail_json"]["persona"] == "seceng"


# ─── 5. test_generate_for_persona_compofficer_nist ───────────────────────────


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

    nist_controls = result["detail_json"].get("nist_controls", [])
    assert any("SC-8" in c for c in nist_controls), (
        f"Expected SC-8 in nist_controls, got: {nist_controls}"
    )


# ─── 6. test_generate_for_persona_neteng_latency ─────────────────────────────


def test_generate_for_persona_neteng_latency():
    """neteng on nipr domain includes expected_latency_ms in detail_json."""
    from tools.network.narrative_generator import generate_for_persona

    step = {
        "step_number": 2,
        "node_id": "r1",
        "node_label": "NIPR Router",
        "action_type": "route",
        "security_detail": {"domain_type": "nipr"},
        "network_detail": {},
    }
    node = {"id": "r1", "type": "router", "label": "NIPR Router", "config": {}}
    flow = {"id": "f1", "classification": "NIPR", "app_type": "web", "topology_id": "t1"}

    with patch("tools.network.narrative_generator._invoke_llm", return_value=None):
        result = generate_for_persona(
            step=step,
            node=node,
            persona_id="neteng",
            flow=flow,
            classification="NIPR",
        )

    assert "expected_latency_ms" in result["detail_json"]
    assert isinstance(result["detail_json"]["expected_latency_ms"], int)


# ─── 7. test_generate_for_persona_csp_detail ─────────────────────────────────


def test_generate_for_persona_csp_detail():
    """generate_for_persona on AWS node populates csp/csp_region in detail_json."""
    from tools.network.narrative_generator import generate_for_persona

    step = {
        "step_number": 1,
        "node_id": "tgw1",
        "node_label": "AWS TGW",
        "action_type": "route",
        "security_detail": {"domain_type": "csp_il4"},
        "network_detail": {},
    }
    node = {"id": "tgw1", "type": "aws-tgw", "label": "AWS TGW", "config": {}}
    flow = {"id": "f1", "classification": "IL4", "app_type": "web", "topology_id": "t1"}

    with patch("tools.network.narrative_generator._invoke_llm", return_value=None):
        result = generate_for_persona(
            step=step,
            node=node,
            persona_id="cloudarch",
            flow=flow,
            classification="IL4",
        )

    dj = result["detail_json"]
    assert "csp" in dj, f"Expected 'csp' key in detail_json, got: {list(dj.keys())}"
    assert "AWS" in dj["csp"], f"Expected AWS in csp name, got: {dj['csp']}"


# ─── 8. test_generate_all_summary ────────────────────────────────────────────


def test_generate_all_summary():
    """generate_all produces summary with hop_count, classification, and description."""
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
                personas=["seceng", "compofficer"],
                classification="NIPR",
                use_llm=False,
            )

    assert "steps" in result
    assert "summary" in result
    summary = result["summary"]
    assert summary["hop_count"] == 3
    assert summary["classification"]
    assert summary["description"]
    assert isinstance(summary["csps_traversed"], list)
    assert isinstance(summary["inter_csp_hops"], list)


# ─── 9. test_generate_for_persona_unknown_persona ────────────────────────────


def test_generate_for_persona_unknown_persona():
    """Unknown persona_id returns empty narrative and detail_json."""
    from tools.network.narrative_generator import generate_for_persona

    step = {
        "step_number": 1,
        "node_id": "n1",
        "node_label": "Node",
        "action_type": "originate",
        "security_detail": {},
        "network_detail": {},
    }
    node = {"id": "n1", "type": "server", "label": "Node", "config": {}}
    flow = {"id": "f1", "classification": "NIPR", "app_type": "web", "topology_id": "t1"}

    result = generate_for_persona(
        step=step,
        node=node,
        persona_id="nonexistent_persona",
        flow=flow,
        classification="NIPR",
    )

    assert result["narrative"] == ""
    assert result["detail_json"] == {}
