#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 shared schema enforcement (Feature 7 — D275).

Covers: round-trip serialization, strict/non-strict validation,
backward compatibility, wrap_mcp_response, all 6 schema models.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.schemas.core import ProjectStatus, AgentHealth, AuditEvent
from tools.schemas.compliance import ComplianceResult, SecurityScanResult
from tools.schemas.chat import ChatMessage, ChatContext
from tools.schemas.innovation import InnovationSignal
from tools.schemas.validation import validate_output, wrap_mcp_response, SchemaValidationError


# ---------------------------------------------------------------------------
# ProjectStatus
# ---------------------------------------------------------------------------

class TestProjectStatus:
    def test_create_minimal(self):
        ps = ProjectStatus(project_id="p-1", name="Test", type="microservice")
        assert ps.project_id == "p-1"
        assert ps.classification == "CUI"
        assert ps.status == "active"

    def test_to_dict_strips_none(self):
        ps = ProjectStatus(project_id="p-1", name="T", type="api")
        d = ps.to_dict()
        assert "project_id" in d
        assert d.get("compliance") is None or "compliance" not in d

    def test_round_trip(self):
        ps = ProjectStatus(project_id="p-1", name="T", type="api", impact_level="IL5")
        d = ps.to_dict()
        ps2 = ProjectStatus.from_dict(d)
        assert ps2.project_id == "p-1"
        assert ps2.impact_level == "IL5"

    def test_from_dict_ignores_unknown_keys(self):
        d = {"project_id": "p-1", "name": "T", "type": "api", "unknown_field": 42}
        ps = ProjectStatus.from_dict(d)
        assert ps.project_id == "p-1"

    def test_from_dict_with_extra_data(self):
        ps = ProjectStatus.from_dict({
            "project_id": "p-2", "name": "X", "type": "app",
            "compliance": {"nist": "pass"}, "security": {"sast": "clean"},
        })
        assert ps.compliance == {"nist": "pass"}


# ---------------------------------------------------------------------------
# AgentHealth
# ---------------------------------------------------------------------------

class TestAgentHealth:
    def test_create_and_dict(self):
        ah = AgentHealth(agent_id="builder", name="Builder Agent", status="active")
        d = ah.to_dict()
        assert d["agent_id"] == "builder"
        assert d["status"] == "active"

    def test_round_trip(self):
        ah = AgentHealth(agent_id="sec", name="Security", status="active", port=8447)
        ah2 = AgentHealth.from_dict(ah.to_dict())
        assert ah2.port == 8447

    def test_defaults(self):
        ah = AgentHealth(agent_id="x", name="X", status="inactive")
        assert ah.active_tasks == 0
        assert ah.capabilities is None


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------

class TestAuditEvent:
    def test_create(self):
        ae = AuditEvent(event_type="code.commit", actor="builder-agent", action="Committed module")
        assert ae.classification == "CUI"
        assert ae.event_type == "code.commit"

    def test_round_trip(self):
        ae = AuditEvent(
            event_type="compliance.check", actor="compliance-agent",
            action="Ran STIG check", project_id="p-1",
            details={"controls": 50}, affected_files=["ssp.md"],
        )
        ae2 = AuditEvent.from_dict(ae.to_dict())
        assert ae2.project_id == "p-1"
        assert ae2.details["controls"] == 50


# ---------------------------------------------------------------------------
# ComplianceResult
# ---------------------------------------------------------------------------

class TestComplianceResult:
    def test_create(self):
        cr = ComplianceResult(framework="fedramp", assessment_date="2026-01-01", overall_status="pass")
        assert cr.controls_assessed == 0

    def test_round_trip(self):
        cr = ComplianceResult(
            framework="cmmc", assessment_date="2026-02-01",
            overall_status="fail", controls_assessed=100, controls_satisfied=80,
            blocking_issues=["AC-2 not met"], coverage_pct=80.0,
        )
        cr2 = ComplianceResult.from_dict(cr.to_dict())
        assert cr2.framework == "cmmc"
        assert len(cr2.blocking_issues) == 1


# ---------------------------------------------------------------------------
# SecurityScanResult
# ---------------------------------------------------------------------------

class TestSecurityScanResult:
    def test_create(self):
        sr = SecurityScanResult(scan_type="sast", status="pass")
        assert sr.findings_count == 0
        assert sr.critical == 0

    def test_with_findings(self):
        sr = SecurityScanResult(
            scan_type="code_pattern", status="fail",
            findings_count=3, critical=1, high=2,
            findings=[{"name": "eval", "severity": "critical"}],
        )
        d = sr.to_dict()
        assert d["critical"] == 1
        assert len(d["findings"]) == 1


# ---------------------------------------------------------------------------
# ChatMessage / ChatContext
# ---------------------------------------------------------------------------

class TestChatSchemas:
    def test_chat_message(self):
        cm = ChatMessage(role="user", content="Hello", turn_number=1)
        d = cm.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "Hello"

    def test_chat_context(self):
        cc = ChatContext(context_id="ctx-1", user_id="u-1", title="My Chat")
        d = cc.to_dict()
        assert d["context_id"] == "ctx-1"
        assert d["status"] == "active"
        assert d["agent_model"] == "sonnet"

    def test_chat_message_round_trip(self):
        cm = ChatMessage(role="assistant", content="Hi", turn_number=2, is_compressed=True, compression_tier="bulk")
        cm2 = ChatMessage.from_dict(cm.to_dict())
        assert cm2.is_compressed is True
        assert cm2.compression_tier == "bulk"


# ---------------------------------------------------------------------------
# InnovationSignal
# ---------------------------------------------------------------------------

class TestInnovationSignal:
    def test_create(self):
        sig = InnovationSignal(signal_id="sig-1", source="agent-zero",
                               source_type="external_framework_analysis", title="Test")
        assert sig.status == "new"

    def test_round_trip(self):
        sig = InnovationSignal(
            signal_id="sig-2", source="insforge",
            source_type="external_framework_analysis", title="Schema",
            innovation_score=0.85, category="architecture",
        )
        sig2 = InnovationSignal.from_dict(sig.to_dict())
        assert sig2.innovation_score == 0.85
        assert sig2.category == "architecture"


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------

class TestValidateOutput:
    def test_non_strict_passes_valid(self):
        data = {"project_id": "p-1", "name": "T", "type": "api"}
        result = validate_output(data, ProjectStatus, strict=False)
        assert result["project_id"] == "p-1"

    def test_strict_raises_on_missing_required(self):
        data = {"name": "T"}  # missing project_id and type
        with pytest.raises(SchemaValidationError):
            validate_output(data, ProjectStatus, strict=True)

    def test_non_strict_returns_original_on_failure(self):
        data = {"foo": "bar"}  # missing required fields
        result = validate_output(data, ProjectStatus, strict=False)
        # Should return original data
        assert "foo" in result

    def test_strict_raises_on_non_dict(self):
        with pytest.raises(SchemaValidationError):
            validate_output("not a dict", ProjectStatus, strict=True)

    def test_non_strict_returns_non_dict(self):
        result = validate_output("not a dict", ProjectStatus, strict=False)
        assert result == "not a dict"

    def test_enriches_with_defaults(self):
        data = {"project_id": "p-1", "name": "T", "type": "api"}
        result = validate_output(data, ProjectStatus, strict=False)
        assert result.get("classification") == "CUI"
        assert result.get("status") == "active"


# ---------------------------------------------------------------------------
# wrap_mcp_response
# ---------------------------------------------------------------------------

class TestWrapMcpResponse:
    def test_wraps_dict(self):
        data = {"findings": 0, "status": "pass"}
        result = wrap_mcp_response(data)
        assert result["isError"] is False
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "findings" in result["content"][0]["text"]

    def test_wraps_string(self):
        result = wrap_mcp_response("Hello World")
        assert result["content"][0]["text"] == "Hello World"

    def test_wraps_with_schema(self):
        data = {"scan_type": "sast", "status": "pass", "findings_count": 0}
        result = wrap_mcp_response(data, SecurityScanResult)
        assert result["isError"] is False

    def test_schema_validation_failure_does_not_crash(self):
        data = {"bad": "data"}
        result = wrap_mcp_response(data, SecurityScanResult)
        assert result["isError"] is False  # graceful degradation
