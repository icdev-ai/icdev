#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for tools.network.intent_validator — Intent-Based Validation Engine."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.network.intent_validator import (
    CONSTRAINT_TYPES,
    validate_intent_policy,
    _parse_bandwidth_label,
    _matches_selector,
    _build_adjacency,
    _shortest_hop_count,
)


# ── Helper: sample topology ────────────────────────────────────────────────
def _sample_graph():
    """Hub-and-spoke topology: core router, 2 switches, 1 firewall, 1 cloud."""
    return {
        "nodes": [
            {"id": "r1", "type": "router", "label": "Core-Router-1"},
            {"id": "sw1", "type": "switch-l3", "label": "Dist-Switch-1"},
            {"id": "sw2", "type": "switch-l3", "label": "Dist-Switch-2"},
            {"id": "fw1", "type": "firewall", "label": "Perimeter-FW"},
            {"id": "cloud1", "type": "cloud", "label": "AWS-DX-East"},
        ],
        "edges": [
            {"source": "r1", "target": "sw1", "label": "10G"},
            {"source": "r1", "target": "sw2", "label": "10G"},
            {"source": "r1", "target": "fw1", "label": "1G"},
            {"source": "fw1", "target": "cloud1", "label": "1G"},
        ],
    }


# ── Bandwidth ──────────────────────────────────────────────────────────────
class TestBandwidthParsing:
    def test_parse_400g(self):
        assert _parse_bandwidth_label("400G") == 400_000

    def test_parse_10g(self):
        assert _parse_bandwidth_label("10G") == 10_000

    def test_parse_100m(self):
        assert _parse_bandwidth_label("100M") == 100

    def test_parse_unknown(self):
        assert _parse_bandwidth_label("") == 0
        assert _parse_bandwidth_label("unknown") == 0


class TestBandwidthConstraint:
    def test_all_links_above_threshold_pass(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "bandwidth", "rule_json": {"min_bandwidth_mbps": 500, "selector": "all"}}],
        )
        assert result["status"] == "PASS"

    def test_links_below_threshold_fail(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "bandwidth", "rule_json": {"min_bandwidth_mbps": 5000, "selector": "all"}}],
        )
        assert result["status"] == "FAIL"
        assert result["failed"] == 1
        # fw1-cloud1 link is 1G = 1000 Mbps < 5000
        assert any("1000 Mbps" in v["message"] for v in result["violations"])


# ── Redundancy ─────────────────────────────────────────────────────────────
class TestRedundancyConstraint:
    def test_core_router_passes_min_2(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "redundancy", "rule_json": {"min_links": 2, "selector": "type:router"}}],
        )
        # r1 has 3 links
        assert result["status"] == "PASS"

    def test_cloud_fails_min_2(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "redundancy", "rule_json": {"min_links": 2, "selector": "type:cloud"}}],
        )
        assert result["status"] == "FAIL"
        assert any("aws" in v["affected_entity"].lower() for v in result["violations"])


# ── Isolation ──────────────────────────────────────────────────────────────
class TestIsolationConstraint:
    def test_cloud_to_switch_via_firewall_pass(self):
        # cloud1 -> fw1 -> r1 -> sw1: firewall is in path
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "isolation",
                    "rule_json": {
                        "zone_a": "type:cloud",
                        "zone_b": "type:switch-l3",
                        "require_firewall": True,
                    },
                }
            ],
        )
        assert result["status"] == "PASS"

    def test_switch_to_switch_no_firewall_fail(self):
        # sw1 -> r1 -> sw2: no firewall in path
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "isolation",
                    "rule_json": {
                        "zone_a": "label:Dist-Switch-1",
                        "zone_b": "label:Dist-Switch-2",
                        "require_firewall": True,
                    },
                }
            ],
        )
        assert result["status"] == "FAIL"


# ── Latency ────────────────────────────────────────────────────────────────
class TestLatencyConstraint:
    def test_adjacent_nodes_within_bound(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "latency",
                    "rule_json": {
                        "max_hops": 1,
                        "source_selector": "type:router",
                        "target_selector": "type:switch-l3",
                    },
                }
            ],
        )
        assert result["status"] == "PASS"

    def test_far_nodes_exceed_bound(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "latency",
                    "rule_json": {
                        "max_hops": 1,
                        "source_selector": "type:switch-l3",
                        "target_selector": "type:cloud",
                    },
                }
            ],
        )
        # sw1 -> r1 -> fw1 -> cloud1 = 3 hops > 1
        assert result["status"] == "FAIL"


# ── Encryption ─────────────────────────────────────────────────────────────
class TestEncryptionConstraint:
    def test_wan_links_unencrypted_fail(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "encryption", "rule_json": {"selector": "all_wan", "min_level": "any"}}],
        )
        # cloud1 is WAN type, link fw1-cloud1 has no encryption
        assert result["status"] == "FAIL"

    def test_non_wan_selector_pass(self):
        # No WAN-type nodes match type:switch-l3
        validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "encryption", "rule_json": {"selector": "type:switch-l3", "min_level": "any"}}],
        )
        # sw1 and sw2 links to r1 are not encrypted, but encryptor adjacency check...
        # Actually these would fail too. Let's use a graph with encryptors.
        graph = _sample_graph()
        graph["nodes"].append({"id": "enc1", "type": "fips-140-l2", "label": "FIPS-Encryptor"})
        graph["edges"].append({"source": "sw1", "target": "enc1", "label": "10G"})
        result2 = validate_intent_policy(
            "t1",
            graph,
            [{"constraint_type": "encryption", "rule_json": {"selector": "label:Dist-Switch-1", "min_level": "any"}}],
        )
        # sw1 has adjacent encryptor
        assert result2["status"] == "PASS"


# ── Custom ─────────────────────────────────────────────────────────────────
class TestCustomConstraint:
    def test_no_spof_detects_bridge(self):
        # r1 is a bridge node (removing it disconnects sw1/sw2 from fw1/cloud1)
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "custom",
                    "rule_json": {
                        "check": "no_single_point_of_failure",
                        "params": {},
                    },
                }
            ],
        )
        assert result["status"] == "FAIL"
        assert any("single point of failure" in v["message"] for v in result["violations"])

    def test_max_node_count_pass(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "custom", "rule_json": {"check": "max_node_count", "params": {"max": 10}}}],
        )
        assert result["status"] == "PASS"

    def test_max_node_count_fail(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [{"constraint_type": "custom", "rule_json": {"check": "max_node_count", "params": {"max": 3}}}],
        )
        assert result["status"] == "FAIL"

    def test_required_node_type_pass(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "custom",
                    "rule_json": {
                        "check": "required_node_type",
                        "params": {"type": "firewall", "min_count": 1},
                    },
                }
            ],
        )
        assert result["status"] == "PASS"

    def test_required_node_type_fail(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "custom",
                    "rule_json": {
                        "check": "required_node_type",
                        "params": {"type": "hsm", "min_count": 1},
                    },
                }
            ],
        )
        assert result["status"] == "FAIL"


# ── Multi-constraint policy ───────────────────────────────────────────────
class TestMultiConstraintPolicy:
    def test_mixed_pass_and_fail(self):
        constraints = [
            {"constraint_type": "bandwidth", "rule_json": {"min_bandwidth_mbps": 500, "selector": "all"}},
            {"constraint_type": "redundancy", "rule_json": {"min_links": 5, "selector": "all"}},
        ]
        result = validate_intent_policy("t1", _sample_graph(), constraints, "Mixed Policy")
        assert result["total_constraints"] == 2
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["policy_name"] == "Mixed Policy"
        assert result["score_pct"] == 50.0

    def test_severity_override(self):
        constraints = [
            {"constraint_type": "redundancy", "severity": "CAT3", "rule_json": {"min_links": 5, "selector": "all"}},
        ]
        result = validate_intent_policy("t1", _sample_graph(), constraints)
        assert all(v["severity"] == "CAT3" for v in result["violations"])


# ── Helpers ────────────────────────────────────────────────────────────────
class TestHelpers:
    def test_matches_selector_all(self):
        node_types = {"r1": "router"}
        assert _matches_selector({"id": "r1"}, "all", node_types)
        assert _matches_selector({"id": "r1"}, "", node_types)

    def test_matches_selector_type(self):
        node_types = {"r1": "router"}
        assert _matches_selector({"id": "r1"}, "type:router", node_types)
        assert not _matches_selector({"id": "r1"}, "type:switch", node_types)

    def test_matches_selector_label(self):
        node_types = {"r1": "router"}
        assert _matches_selector({"id": "r1", "label": "Core-Router"}, "label:core", node_types)

    def test_matches_selector_all_critical(self):
        node_types = {"r1": "router", "sw1": "switch-l3", "srv1": "server"}
        assert _matches_selector({"id": "r1"}, "all_critical", node_types)
        assert not _matches_selector({"id": "srv1"}, "all_critical", node_types)

    def test_build_adjacency(self):
        edges = [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}]
        adj = _build_adjacency(edges)
        assert "b" in adj["a"]
        assert "a" in adj["b"]
        assert "c" in adj["b"]

    def test_shortest_hop_count(self):
        adj = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}
        assert _shortest_hop_count(adj, "a", "c") == 2
        assert _shortest_hop_count(adj, "a", "a") == 0
        assert _shortest_hop_count(adj, "a", "d") == -1

    def test_constraint_types_complete(self):
        expected = {"bandwidth", "redundancy", "isolation", "latency", "encryption", "custom"}
        assert set(CONSTRAINT_TYPES.keys()) == expected


# ── Empty / edge cases ────────────────────────────────────────────────────
class TestEdgeCases:
    def test_empty_graph(self):
        result = validate_intent_policy(
            "t1",
            {"nodes": [], "edges": []},
            [
                {"constraint_type": "bandwidth", "rule_json": {"min_bandwidth_mbps": 1000, "selector": "all"}},
            ],
        )
        assert result["status"] == "PASS"

    def test_no_constraints(self):
        result = validate_intent_policy("t1", _sample_graph(), [])
        assert result["total_constraints"] == 0
        assert result["status"] == "PASS"

    def test_unknown_constraint_type_skipped(self):
        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {"constraint_type": "nonexistent", "rule_json": {}},
            ],
        )
        assert result["total_constraints"] == 0

    def test_rule_json_as_string(self):
        import json

        result = validate_intent_policy(
            "t1",
            _sample_graph(),
            [
                {
                    "constraint_type": "bandwidth",
                    "rule_json": json.dumps({"min_bandwidth_mbps": 500, "selector": "all"}),
                },
            ],
        )
        assert result["status"] == "PASS"
