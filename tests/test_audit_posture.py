#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.security.audit_posture."""

from tools.security.audit_posture import audit_posture


class TestAuditPosture:
    def test_returns_dict(self):
        result = audit_posture()
        assert isinstance(result, dict)

    def test_has_overall_status(self):
        result = audit_posture()
        assert "overall_status" in result
        assert result["overall_status"] in ("pass", "warn", "fail")

    def test_has_layers(self):
        result = audit_posture()
        assert "layers" in result
        assert isinstance(result["layers"], dict)

    def test_all_layers_present(self):
        result = audit_posture()
        expected = [
            "security_context",
            "abac_engine",
            "classification_enforcer",
            "row_security",
            "column_security",
            "field_security",
            "encryption_at_rest",
            "mtls",
            "middleware",
        ]
        for layer in expected:
            assert layer in result["layers"]
            assert "status" in result["layers"][layer]
            assert "detail" in result["layers"][layer]

    def test_security_context_pass(self):
        result = audit_posture()
        assert result["layers"]["security_context"]["status"] == "pass"

    def test_abac_engine_pass(self):
        result = audit_posture()
        assert result["layers"]["abac_engine"]["status"] in ("pass", "warn")

    def test_classification_enforcer_pass(self):
        result = audit_posture()
        assert result["layers"]["classification_enforcer"]["status"] == "pass"

    def test_row_security_pass(self):
        result = audit_posture()
        assert result["layers"]["row_security"]["status"] == "pass"

    def test_column_security_pass(self):
        result = audit_posture()
        assert result["layers"]["column_security"]["status"] == "pass"

    def test_field_security_pass(self):
        result = audit_posture()
        assert result["layers"]["field_security"]["status"] == "pass"

    def test_has_timestamp(self):
        result = audit_posture()
        assert "timestamp" in result
        assert isinstance(result["timestamp"], str)
