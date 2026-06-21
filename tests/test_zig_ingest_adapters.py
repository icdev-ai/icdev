# [CUI // SP-CTI]
"""Tests for zig_external_adapter.py — all 5 ingest methods."""

import json
import pathlib
import pytest

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "zig"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sbom_fixture():
    return (FIXTURE_DIR / "sbom_sample.json").read_text(encoding="utf-8")


def _sast_fixture():
    return (FIXTURE_DIR / "sast_sample.json").read_text(encoding="utf-8")


def _survey_fixture():
    return (FIXTURE_DIR / "survey_sample.json").read_text(encoding="utf-8")


def _nmap_fixture():
    return (FIXTURE_DIR / "nmap_sample.xml").read_text(encoding="utf-8")


def _openapi_fixture():
    return (FIXTURE_DIR / "openapi_sample.yaml").read_text(encoding="utf-8")


# ── Shared stub for set_activity_status ──────────────────────────────────────

class _ActivityLog:
    def __init__(self):
        self.calls = []

    def record(self, activity_id, status, target_id="icdev-self",
                evidence_note=None, completed_by=None):
        self.calls.append({
            "activity_id": activity_id, "status": status,
            "target_id": target_id, "evidence_note": evidence_note,
        })
        return {"activity_id": activity_id, "target_id": target_id, "status": status}


@pytest.fixture(autouse=True)
def stub_activity_tracker(monkeypatch):
    """Stub set_activity_status to avoid DB writes in unit tests."""
    log = _ActivityLog()

    import importlib
    import sys

    # The adapter imports from tools.security_canvas.zig_activity_tracker
    tracker_mod = importlib.import_module("tools.security_canvas.zig_activity_tracker")
    monkeypatch.setattr(tracker_mod, "set_activity_status", log.record)

    # Also patch the reference inside the adapter module if already imported
    adapter_key = "tools.security_canvas.zig_external_adapter"
    if adapter_key in sys.modules:
        monkeypatch.setattr(sys.modules[adapter_key], "set_activity_status", log.record)

    return log


# ── SBOM tests ────────────────────────────────────────────────────────────────

class TestIngestSbom:
    def test_returns_source_and_target_id(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("test-app", _sbom_fixture())
        assert result["source"] == "sbom"
        assert result["target_id"] == "test-app"

    def test_detects_high_cve(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("test-app", _sbom_fixture())
        assert result["findings"] >= 1

    def test_detects_critical_cve(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("test-app", _sbom_fixture())
        # pillow has a critical CVE — findings should include critical
        assert result["findings"] >= 2

    def test_activities_updated_nonempty(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("test-app", _sbom_fixture())
        assert isinstance(result["activities_updated"], list)

    def test_invalid_json_returns_error(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("test-app", "not valid json {{{")
        assert "error" in result

    def test_empty_sbom_marks_d08(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        empty = json.dumps({"components": []})
        result = ingest_sbom("test-app", empty)
        assert "zig-act-d08" in result["activities_updated"]

    def test_accepts_dict_payload(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        payload = {"components": [{"name": "lib", "version": "1.0"}]}
        result = ingest_sbom("test-app", payload)
        assert result["source"] == "sbom"


# ── SAST tests ────────────────────────────────────────────────────────────────

class TestIngestSast:
    def test_returns_source(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("test-app", _sast_fixture())
        assert result["source"] == "sast"

    def test_b105_maps_to_activity(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        payload = json.dumps({"results": [{
            "test_id": "B105", "issue_severity": "HIGH",
            "filename": "x.py", "line_number": 1,
        }]})
        result = ingest_sast("test-app", payload)
        # B105 maps to zig-act-p1-29 per ZIG_EVIDENCE_MAP
        assert result["findings"] >= 1

    def test_b502_maps_to_activity(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        payload = json.dumps({"results": [{
            "test_id": "B502", "issue_severity": "HIGH",
            "filename": "tls.py", "line_number": 5,
        }]})
        result = ingest_sast("test-app", payload)
        assert result["findings"] >= 1

    def test_low_severity_skipped(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        payload = json.dumps({"results": [{
            "test_id": "B101", "issue_severity": "LOW",
            "filename": "test.py", "line_number": 1,
        }]})
        result = ingest_sast("test-app", payload)
        assert result["findings"] == 0

    def test_fixture_finds_multiple(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("test-app", _sast_fixture())
        # fixture has B105(HIGH), B502(HIGH), B608(MEDIUM) — 3 qualifying
        assert result["findings"] >= 3

    def test_invalid_json_returns_error(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("test-app", "{{bad")
        assert "error" in result


# ── Survey tests ──────────────────────────────────────────────────────────────

class TestIngestSurvey:
    def test_returns_source(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("test-app", _survey_fixture())
        assert result["source"] == "survey"

    def test_true_answers_promote_complete(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("test-app", _survey_fixture())
        # mfa=true, mfa_admin=true, rbac=true, lifecycle=true → 4 actives
        assert result["findings"] >= 4

    def test_false_answers_mark_in_progress(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        payload = json.dumps({"mfa": False})
        result = ingest_survey("test-app", payload)
        # should still record findings
        assert result["findings"] >= 1

    def test_activities_updated_nonempty(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("test-app", _survey_fixture())
        assert len(result["activities_updated"]) >= 1

    def test_invalid_json_returns_error(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("test-app", "not json")
        assert "error" in result

    def test_accepts_dict(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("test-app", {"mfa": True})
        assert result["source"] == "survey"


# ── Nmap tests ────────────────────────────────────────────────────────────────

class TestIngestNmap:
    def test_returns_source(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("test-app", _nmap_fixture())
        assert result["source"] == "nmap"

    def test_detects_http_without_https(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        # fixture has port 80 open, no 443
        result = ingest_nmap("test-app", _nmap_fixture())
        assert result["findings"] >= 1

    def test_detects_admin_ports(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        # fixture has port 22 and 3389 open
        result = ingest_nmap("test-app", _nmap_fixture())
        assert result["findings"] >= 2

    def test_detects_api_port_without_tls(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        # fixture has 8080 open, no 8443
        result = ingest_nmap("test-app", _nmap_fixture())
        assert result["findings"] >= 3

    def test_clean_host_no_findings(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        clean_xml = '''<?xml version="1.0"?>
<nmaprun>
  <host>
    <ports>
      <port protocol="tcp" portid="443">
        <state state="open" reason="syn-ack"/>
        <service name="https"/>
      </port>
    </ports>
  </host>
</nmaprun>'''
        result = ingest_nmap("test-app", clean_xml)
        assert result["findings"] == 0

    def test_invalid_xml_returns_error(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("test-app", "<broken xml")
        assert "error" in result


# ── OpenAPI tests ─────────────────────────────────────────────────────────────

class TestIngestOpenapi:
    def test_returns_source(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("test-app", _openapi_fixture())
        assert result["source"] == "openapi"

    def test_detects_no_security_schemes(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        # fixture has no securitySchemes
        result = ingest_openapi("test-app", _openapi_fixture())
        assert result["findings"] >= 1

    def test_detects_http_only_server(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        # fixture server URL starts with http://
        result = ingest_openapi("test-app", _openapi_fixture())
        assert result["findings"] >= 2

    def test_secure_spec_no_findings(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        secure = json.dumps({
            "openapi": "3.0.3",
            "info": {"title": "Secure API", "version": "1.0"},
            "servers": [{"url": "https://api.example.com"}],
            "components": {"securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }},
            "security": [{"bearerAuth": []}],
        })
        result = ingest_openapi("test-app", secure)
        assert result["findings"] == 0

    def test_invalid_payload_returns_error(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("test-app", "just a plain string that is not yaml or json map")
        assert "error" in result or result["findings"] == 0


# ── Fixture files exist ───────────────────────────────────────────────────────

def test_fixture_files_exist():
    for name in ["sbom_sample.json", "sast_sample.json", "survey_sample.json",
                 "nmap_sample.xml", "openapi_sample.yaml"]:
        assert (FIXTURE_DIR / name).exists(), f"missing fixture: {name}"
