# CUI // SP-CTI
"""Tests: RFI Workbench source persistence + blocking citation gate (trust-cite-03).

Covers:
    - rfi_workbench._section_source_labels()
    - rfi_workbench._reference_findings() / _check_reference_gate()
    - assemble_and_export raises ReferenceGateBlocked on invalid citations
    - POST /api/rfi/<id>/export/<fmt> -> 409 gate=citation_guard (+ force_references bypass)
"""

import importlib
from unittest.mock import patch

import pytest
from flask import Flask

wb = importlib.import_module("tools.govcon.rfi_workbench")
bp = importlib.import_module("tools.govcon.rfi_canvas_blueprint")

_ROMAN = "Our approach is described in Section IV.B of the solicitation."
_CLEAN = "We provide zero-trust networking with continuous monitoring."


class TestSourceLabels:
    def test_parses_json_list(self):
        assert wb._section_source_labels({"sources_json": '["upload:rfp.pdf", "kg:ml"]'}) == [
            "upload:rfp.pdf", "kg:ml"
        ]

    def test_accepts_native_list(self):
        assert wb._section_source_labels({"sources_json": ["rag:doc1"]}) == ["rag:doc1"]

    def test_empty_or_absent(self):
        assert wb._section_source_labels({}) == []
        assert wb._section_source_labels({"sources_json": ""}) == []
        assert wb._section_source_labels({"sources_json": "not-json"}) == []


class TestReferenceFindings:
    def test_flags_roman_numeral_citation(self):
        findings = wb._reference_findings([{"item_number": "2.1", "content": _ROMAN}], {})
        assert len(findings) == 1
        assert findings[0]["item_number"] == "2.1"
        assert findings[0]["invalid_refs"]

    def test_clean_section_passes(self):
        assert wb._reference_findings([{"item_number": "2.1", "content": _CLEAN}], {}) == []

    def test_empty_content_skipped(self):
        assert wb._reference_findings([{"item_number": "2.1", "content": ""}], {}) == []

    def test_uses_ai_draft_when_no_content(self):
        findings = wb._reference_findings([{"item_number": "3.1", "ai_draft": _ROMAN}], {})
        assert findings and findings[0]["item_number"] == "3.1"


class TestReferenceGate:
    def test_blocks_on_invalid(self):
        with pytest.raises(wb.ReferenceGateBlocked) as ei:
            wb._check_reference_gate([{"item_number": "2.1", "content": _ROMAN}], {})
        assert ei.value.findings[0]["item_number"] == "2.1"

    def test_passes_when_clean(self):
        wb._check_reference_gate([{"item_number": "2.1", "content": _CLEAN}], {})  # no raise

    def test_force_bypasses(self):
        wb._check_reference_gate([{"item_number": "2.1", "content": _ROMAN}], {}, force=True)


class TestExportRoute:
    @pytest.fixture()
    def client(self):
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(bp.rfi_canvas_bp)
        return app.test_client()

    def test_citation_gate_returns_409(self, client):
        findings = [{"item_number": "2.1", "invalid_refs": [{"ref": "Section IV.B", "reason": "roman"}]}]
        with patch.object(wb, "assemble_and_export", side_effect=wb.ReferenceGateBlocked(findings)):
            resp = client.post("/api/rfi/sess-1/export/md", json={})
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["gate"] == "citation_guard"
        assert data["findings"] == findings

    def test_force_references_passes_through(self, client):
        with patch.object(wb, "assemble_and_export", return_value="/tmp/out.md") as m:
            resp = client.post("/api/rfi/sess-1/export/md", json={"force_references": True})
        assert resp.status_code == 200
        # force_references forwarded to the workbench
        assert m.call_args.kwargs.get("force_references") is True
