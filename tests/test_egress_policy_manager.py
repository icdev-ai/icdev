#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Egress Policy Manager — NemoClaw-adapted per-agent network policies (D-NC-2)."""

import sqlite3

import pytest

from tools.security.egress_policy_manager import (
    EgressPolicyManager,
    AGENT_ROLES,
    _hash_policy,
)


@pytest.fixture
def mgr_db(tmp_path):
    return tmp_path / "test_egress.db"


@pytest.fixture
def mgr(mgr_db, tmp_path):
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    return EgressPolicyManager(db_path=mgr_db, policy_dir=policy_dir)


class TestResolvePolicy:
    def test_resolve_known_role(self, mgr):
        result = mgr.resolve_policy("builder")
        assert result["role"] == "builder"
        assert "policy_hash" in result
        assert result["agent_port"] == 8445
        assert result["tier"] == "domain"

    def test_resolve_unknown_role_returns_error(self, mgr):
        result = mgr.resolve_policy("nonexistent")
        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_resolve_security_has_no_external_endpoints(self, mgr):
        result = mgr.resolve_policy("security")
        allowed = result["egress"]["allowed_endpoints"]
        assert len(allowed) == 0

    def test_resolve_builder_has_pypi(self, mgr):
        result = mgr.resolve_policy("builder")
        hosts = [ep.get("host") for ep in result["egress"]["allowed_endpoints"]]
        assert "pypi.org" in hosts

    def test_resolve_includes_dns_and_mesh(self, mgr):
        result = mgr.resolve_policy("security")
        assert result["egress"]["allow_dns"] is True
        assert result["egress"]["allow_internal_mesh"] is True

    def test_deny_wins_over_allow(self, mgr_db, tmp_path):
        """Deny rules in custom override should remove matching allowed endpoints."""
        policy_dir = tmp_path / "policies2"
        policy_dir.mkdir()
        custom_dir = policy_dir / "custom"
        custom_dir.mkdir()

        import yaml

        override = {
            "egress": {
                "denied_endpoints": [
                    {"host": "pypi.org"},
                ],
            },
        }
        with open(custom_dir / "builder.yaml", "w") as f:
            yaml.dump(override, f)

        m = EgressPolicyManager(db_path=mgr_db, policy_dir=policy_dir)
        result = m.resolve_policy("builder")
        hosts = [ep.get("host") for ep in result["egress"]["allowed_endpoints"]]
        assert "pypi.org" not in hosts

    def test_resolve_creates_audit_entry(self, mgr, mgr_db):
        mgr.resolve_policy("builder")
        conn = sqlite3.connect(str(mgr_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM egress_policy_audit WHERE action = 'resolve' LIMIT 1").fetchone()
        conn.close()
        assert row is not None
        assert row["agent_role"] == "builder"


class TestGenerateK8sManifest:
    def test_generate_valid_manifest(self, mgr):
        result = mgr.generate_k8s_manifest("builder")
        assert result["apiVersion"] == "networking.k8s.io/v1"
        assert result["kind"] == "NetworkPolicy"
        assert result["metadata"]["name"] == "icdev-builder-egress"
        assert result["spec"]["policyTypes"] == ["Egress"]

    def test_manifest_has_dns_rule(self, mgr):
        result = mgr.generate_k8s_manifest("builder")
        egress_rules = result["spec"]["egress"]
        dns_rules = [
            r
            for r in egress_rules
            if any(p.get("protocol") == "UDP" and p.get("port") == 53 for p in r.get("ports", []))
        ]
        assert len(dns_rules) >= 1

    def test_manifest_has_mesh_rule(self, mgr):
        result = mgr.generate_k8s_manifest("builder")
        egress_rules = result["spec"]["egress"]
        # Mesh rule targets namespace selector
        mesh_rules = [r for r in egress_rules if "to" in r and any("namespaceSelector" in t for t in r.get("to", []))]
        assert len(mesh_rules) >= 1

    def test_generate_unknown_role_returns_error(self, mgr):
        result = mgr.generate_k8s_manifest("nonexistent")
        assert "error" in result

    def test_manifest_includes_annotations(self, mgr):
        result = mgr.generate_k8s_manifest("builder")
        annotations = result["metadata"]["annotations"]
        assert annotations["icdev.ai/nemoclaw-adapted"] == "D-NC-2"


class TestValidatePolicy:
    def test_validate_security_is_valid(self, mgr):
        result = mgr.validate_policy("security")
        assert result["valid"] is True
        assert result["endpoint_count"] == 0

    def test_validate_orchestrator_warns_wildcard(self, mgr):
        result = mgr.validate_policy("orchestrator")
        warnings = [i for i in result["issues"] if i["severity"] == "warning"]
        wildcard_warnings = [w for w in warnings if "wildcard" in w["message"]]
        assert len(wildcard_warnings) >= 1

    def test_validate_returns_endpoint_count(self, mgr):
        result = mgr.validate_policy("builder")
        assert result["endpoint_count"] > 0


class TestDiffPolicies:
    def test_diff_no_prior_shows_changed(self, mgr):
        result = mgr.diff_policies("builder")
        assert result["changed"] is True
        assert result["reason"] == "no_prior_applied_policy"


class TestListRoles:
    def test_list_returns_all_roles(self, mgr):
        roles = mgr.list_roles()
        role_names = {r["role"] for r in roles}
        assert "orchestrator" in role_names
        assert "builder" in role_names
        assert "security" in role_names
        assert len(roles) == len(AGENT_ROLES)

    def test_each_role_has_port_and_tier(self, mgr):
        roles = mgr.list_roles()
        for role in roles:
            assert "port" in role
            assert "tier" in role
            assert role["tier"] in ("core", "domain", "support")


class TestPolicyHash:
    def test_hash_deterministic(self):
        policy = {"name": "test", "egress": {"allowed_endpoints": []}}
        h1 = _hash_policy(policy)
        h2 = _hash_policy(policy)
        assert h1 == h2

    def test_hash_changes_on_content_change(self):
        p1 = {"name": "test", "egress": {"allowed_endpoints": []}}
        p2 = {"name": "test", "egress": {"allowed_endpoints": [{"host": "x"}]}}
        assert _hash_policy(p1) != _hash_policy(p2)
