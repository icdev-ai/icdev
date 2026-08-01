# CUI // SP-CTI
"""Tests for Data Canvas governance ABAC access control (dcpr-sec-03).

Verifies the fail-closed / default-deny behavior of ``check_access`` and
``_eval_rule`` in ``tools.data_canvas.governance_engine``:

  (a) no policy matches a sensitive resource  -> denied
  (b) unknown / unhandled rule operator        -> denied
  (c) an explicit matching allow policy        -> allowed

Memory rule: ALWAYS test the deny case.
"""

import importlib

import pytest

# Import the exact module object that check_access resolves list_policies from.
ge = importlib.import_module("tools.data_canvas.governance_engine")


# ── (a) no-policy-match → denied for sensitive resources ─────────────────────

def test_no_matching_policy_denies_sensitive_resource(monkeypatch):
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [])
    result = ge.check_access(
        user_attrs={"clearance": "CUI"},
        resource={"domain_id": "d1", "classification": "CUI"},
    )
    assert result["allowed"] is False
    assert "default deny" in result["reason"].lower()
    assert result["matched_policies"] == []


def test_no_matching_policy_denies_when_classification_missing(monkeypatch):
    # Missing classification must fail closed (treated as sensitive).
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [])
    result = ge.check_access(user_attrs={}, resource={"domain_id": "d1"})
    assert result["allowed"] is False


def test_no_matching_policy_allows_public_resource(monkeypatch):
    # Only explicitly non-sensitive (PUBLIC) resources default-allow.
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [])
    result = ge.check_access(
        user_attrs={}, resource={"domain_id": "d1", "classification": "PUBLIC"}
    )
    assert result["allowed"] is True


# ── (b) unknown operator → denied (fail closed) ──────────────────────────────

def test_eval_rule_unknown_operator_returns_false():
    assert ge._eval_rule("anything", "totally_unknown_op", "x") is False


def test_unknown_operator_in_policy_denies(monkeypatch):
    policy = {
        "name": "weird-op-policy",
        "rules": [{"attr": "clearance", "op": "bogus_op", "value": "CUI"}],
    }
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [policy])
    result = ge.check_access(
        user_attrs={"clearance": "CUI"},
        resource={"domain_id": "d1", "classification": "CUI"},
    )
    assert result["allowed"] is False
    assert result["matched_policies"], "expected the evaluated rule to be recorded"


# ── (c) explicit matching allow policy → allowed ─────────────────────────────

def test_matching_allow_policy_allows(monkeypatch):
    policy = {
        "name": "clearance-eq-policy",
        "rules": [{"attr": "clearance", "op": "eq", "value": "CUI"}],
    }
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [policy])
    result = ge.check_access(
        user_attrs={"clearance": "CUI"},
        resource={"domain_id": "d1", "classification": "CUI"},
    )
    assert result["allowed"] is True
    assert result["matched_policies"]


def test_matching_policy_violation_denies(monkeypatch):
    # Deny case: user attribute does not satisfy the policy rule.
    policy = {
        "name": "clearance-eq-policy",
        "rules": [{"attr": "clearance", "op": "eq", "value": "SECRET"}],
    }
    monkeypatch.setattr(ge, "list_policies", lambda domain_id=None: [policy])
    result = ge.check_access(
        user_attrs={"clearance": "CUI"},
        resource={"domain_id": "d1", "classification": "CUI"},
    )
    assert result["allowed"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
