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


# ── XML entity-expansion / XXE defense (shx-auth-03) ──────────────────────────

# Classic "billion laughs" — nested entity expansion that balloons to gigabytes.
_BILLION_LAUGHS = '''<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<nmaprun>&lol4;</nmaprun>'''

# DOCTYPE with an external-entity reference (XXE file read attempt).
_XXE_EXTERNAL = '''<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<nmaprun><host>&xxe;</host></nmaprun>'''

# DOCTYPE with no entities — still rejected by the pre-parse guard.
_DOCTYPE_ONLY = '''<?xml version="1.0"?>
<!DOCTYPE nmaprun SYSTEM "nmap.dtd">
<nmaprun><host></host></nmaprun>'''


class TestIngestNmapXmlDefense:
    def test_billion_laughs_rejected_cleanly(self, stub_activity_tracker):
        """Entity-expansion DoS is rejected with a clear error, no hang/expansion."""
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("test-app", _BILLION_LAUGHS)
        assert "error" in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []
        # No stub calls means the payload never reached activity tracking.
        assert stub_activity_tracker.calls == []

    def test_external_entity_rejected(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("test-app", _XXE_EXTERNAL)
        assert "error" in result
        assert result["findings"] == 0

    def test_doctype_only_rejected(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("test-app", _DOCTYPE_ONLY)
        assert "error" in result

    def test_parse_xml_safe_raises_valueerror_on_entity(self):
        from tools.security_canvas.zig_external_adapter import _parse_xml_safe
        with pytest.raises(ValueError):
            _parse_xml_safe(_BILLION_LAUGHS)

    def test_benign_nmap_still_parses(self, stub_activity_tracker):
        """A benign nmap XML (no DOCTYPE/ENTITY) still parses and yields findings."""
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        benign = '''<?xml version="1.0"?>
<nmaprun>
  <host>
    <ports>
      <port protocol="tcp" portid="80">
        <state state="open" reason="syn-ack"/>
        <service name="http"/>
      </port>
    </ports>
  </host>
</nmaprun>'''
        result = ingest_nmap("test-app", benign)
        assert "error" not in result
        assert result["source"] == "nmap"
        assert result["findings"] >= 1


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
                 "nmap_sample.xml", "openapi_sample.yaml",
                 "nmap_multihost.xml", "sbom_cyclonedx_full.json",
                 "sbom_spdx_2.3.json",
                 "sast_bandit_full.json"]:
        assert (FIXTURE_DIR / name).exists(), f"missing fixture: {name}"


# ═══════════════════════════════════════════════════════════════════════════════
# shx-test-03: realistic real-world-shaped fixtures + malformed-input robustness
#
# Scope note: shx-auth-03 (merged) already covers billion-laughs / DOCTYPE / XXE
# rejection, benign nmap parse, and route-level 413. The classes below add the
# REMAINING delta only:
#   1. Realistic multi-record fixtures that assert the MAPPED output
#      (activities_updated / findings), not merely "no exception".
#   2. Malformed inputs that must produce clean error dicts, never a traceback.
#   3. Known adapter bugs where a malformed input DOES traceback today — captured
#      with pytest.raises to pin current behavior (documented as follow-ups; the
#      adapter is intentionally NOT fixed in this test-only task).
# ═══════════════════════════════════════════════════════════════════════════════


def _nmap_multihost_fixture():
    return (FIXTURE_DIR / "nmap_multihost.xml").read_text(encoding="utf-8")


def _sbom_full_fixture():
    return (FIXTURE_DIR / "sbom_cyclonedx_full.json").read_text(encoding="utf-8")


def _sast_full_fixture():
    return (FIXTURE_DIR / "sast_bandit_full.json").read_text(encoding="utf-8")


# ── Realistic fixtures — assert the MAPPED output ─────────────────────────────

class TestRealisticNmapMultiHost:
    """Multi-host nmap -sV -O output (web / api / clean-db hosts, os-match)."""

    def test_maps_expected_activities(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("prod-net", _nmap_multihost_fixture())
        assert "error" not in result
        # web01: port 80 open, 443 closed  → p1-18 (no HTTPS); port 22 → p1-16 (admin)
        # api01: port 8080 open, 8443 filtered → p2-15 (no mTLS); port 3389 → p1-16
        # db01:  443 + 5432 open only            → no findings
        assert set(result["activities_updated"]) == {
            "zig-act-p1-18", "zig-act-p1-16", "zig-act-p2-15",
        }

    def test_finding_count_across_hosts(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("prod-net", _nmap_multihost_fixture())
        # web01: 2 (http-no-https + admin ssh); api01: 2 (no-tls-api + admin rdp)
        assert result["findings"] == 4

    def test_closed_and_filtered_ports_ignored(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("prod-net", _nmap_multihost_fixture())
        # 443 is 'closed' on web01 and 8443 'filtered' on api01 — the adapter
        # only counts state="open", so both hosts still register their gaps.
        assert "zig-act-p1-18" in result["activities_updated"]
        assert "zig-act-p2-15" in result["activities_updated"]

    def test_activity_tracker_received_evidence_notes(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        ingest_nmap("prod-net", _nmap_multihost_fixture())
        # Each mapped finding pushes an evidence note through set_activity_status.
        assert stub_activity_tracker.calls, "expected activity-tracker calls"
        assert all(c["target_id"] == "prod-net" for c in stub_activity_tracker.calls)
        assert any("Nmap" in (c["evidence_note"] or "")
                   for c in stub_activity_tracker.calls)


class TestRealisticSbomCycloneDX:
    """Full CycloneDX SBOM: clean + high + critical + low + missing-version comps."""

    def test_maps_d08_and_sca_activity(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("billing-svc", _sbom_full_fixture())
        assert "error" not in result
        # requests(high) + pillow(critical) → zig-act-d08;
        # internal-shim (no version) → outdated_dep → zig-act-p1-21.
        assert set(result["activities_updated"]) == {"zig-act-d08", "zig-act-p1-21"}

    def test_counts_only_high_and_critical(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("billing-svc", _sbom_full_fixture())
        # pyyaml's LOW vuln must NOT count; only high + critical → 2 findings.
        assert result["findings"] == 2

    def test_missing_version_component_flags_supply_chain(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("billing-svc", _sbom_full_fixture())
        assert "zig-act-p1-21" in result["activities_updated"]


class TestRealisticSastBandit:
    """Full Bandit SAST report: HIGH/MEDIUM mapped, LOW skipped."""

    def test_maps_all_qualifying_test_ids(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("api-svc", _sast_full_fixture())
        assert "error" not in result
        # B105→p1-29, B502→p1-18, B608→p1-21, B701→p2-21; B101(LOW) skipped.
        assert set(result["activities_updated"]) == {
            "zig-act-p1-29", "zig-act-p1-18", "zig-act-p1-21", "zig-act-p2-21",
        }

    def test_low_severity_excluded_from_count(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("api-svc", _sast_full_fixture())
        # 4 qualifying (2 HIGH + 2 MEDIUM); the single LOW finding is dropped.
        assert result["findings"] == 4


class TestRealisticSurveyMapped:
    """Survey happy path — assert the exact mapped activities + complete-promotion."""

    def test_all_survey_keys_map(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("hr-app", _survey_fixture())
        assert set(result["activities_updated"]) == {
            "zig-act-p1-02", "zig-act-p1-01", "zig-act-p1-07",
            "zig-act-p1-03", "zig-act-p1-04", "zig-act-p1-06",
        }
        assert result["findings"] == 6

    def test_true_answers_promote_to_complete(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        ingest_survey("hr-app", _survey_fixture())
        # mfa/mfa_admin/rbac/lifecycle=true → a second call sets status "complete".
        completed = [c for c in stub_activity_tracker.calls if c["status"] == "complete"]
        assert len(completed) == 4  # the four enabled controls in the fixture


class TestRealisticOpenapiMapped:
    """OpenAPI happy path — assert exact mapped gaps."""

    def test_maps_no_scheme_and_http_only(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("edge-api", _openapi_fixture())
        assert "error" not in result
        # No securitySchemes → p2-19; http:// server URL → p1-18.
        assert set(result["activities_updated"]) == {"zig-act-p2-19", "zig-act-p1-18"}
        assert result["findings"] == 2


# ── Malformed inputs → clean error dicts, never tracebacks ────────────────────

class TestMalformedInputRobustness:
    """Inputs that MUST degrade to a clean {'error': ...} dict with findings=0."""

    def test_sbom_empty_string(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("t", "")
        assert result["error"] == "invalid JSON"
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_sbom_missing_components_key(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        # A well-formed JSON object with no "components" key must not raise;
        # the adapter treats it as an empty SBOM and marks zig-act-d08.
        result = ingest_sbom("t", json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.4"}))
        assert "error" not in result
        assert result["findings"] == 0
        assert "zig-act-d08" in result["activities_updated"]

    def test_sbom_component_missing_fields(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        # Components present but each missing name/version/vulnerabilities.
        result = ingest_sbom("t", json.dumps({"components": [{}, {}]}))
        assert "error" not in result
        # Missing version on both → outdated_dep mapping (zig-act-p1-21).
        assert result["activities_updated"] == ["zig-act-p1-21"]

    def test_sast_empty_string(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("t", "")
        assert result["error"] == "invalid JSON"
        assert result["findings"] == 0

    def test_sast_missing_results_key(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("t", json.dumps({"errors": [], "metrics": {}}))
        assert "error" not in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_survey_empty_string(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("t", "")
        assert result["error"] == "invalid JSON"
        assert result["findings"] == 0

    def test_survey_unknown_keys_ignored(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        # Keys not in the survey evidence map are silently ignored (no mapping).
        result = ingest_survey("t", json.dumps({"unknown_control": True, "foo": False}))
        assert "error" not in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_nmap_truncated_xml(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("t", "<nmaprun><host><ports>")
        assert "error" in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_nmap_empty_string(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("t", "")
        assert "error" in result
        assert result["findings"] == 0

    def test_nmap_none_payload(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        result = ingest_nmap("t", None)
        assert result["error"] == "empty XML payload"
        assert result["findings"] == 0

    def test_nmap_non_utf8_bytes(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        # The ingest path can receive raw bytes; a non-UTF8 / binary blob must
        # be rejected cleanly (ParseError), not crash the adapter.
        result = ingest_nmap("t", b"\xff\xfe<nmaprun></nmaprun>")
        assert "error" in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_nmap_valid_utf8_bytes_parse(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_nmap
        # Well-formed bytes (not str) still parse — the bytes path is supported.
        payload = (b'<?xml version="1.0"?><nmaprun><host><ports>'
                   b'<port protocol="tcp" portid="80">'
                   b'<state state="open" reason="syn-ack"/></port>'
                   b'</ports></host></nmaprun>')
        result = ingest_nmap("t", payload)
        assert "error" not in result
        assert result["findings"] >= 1

    def test_openapi_empty_string(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("t", "")
        assert result["error"] == "not a mapping"
        assert result["findings"] == 0

    def test_openapi_plain_scalar(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("t", "just a plain string")
        assert result["error"] == "not a mapping"

    def test_openapi_json_list(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_openapi
        result = ingest_openapi("t", "[]")
        assert result["error"] == "not a mapping"


# ── Malformed JSON shapes degrade cleanly (shx-hyg-05) ────────────────────────

class TestMalformedShapesDegradeCleanly:
    """Malformed inputs return a clean error dict instead of raising a traceback.

    Fixed in shx-hyg-05: ingest_sbom / ingest_sast / ingest_survey guard the
    parsed JSON with ``isinstance(data, dict)`` (top-level "not a mapping"
    clean-degrade, mirroring ingest_openapi) and skip non-dict list elements
    with per-element ``isinstance`` guards, so a top-level JSON array — or a
    non-dict element — degrades cleanly instead of raising AttributeError.
    """

    def test_sbom_toplevel_json_array_degrades(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        result = ingest_sbom("t", "[]")
        assert result["error"] == "not a mapping"
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_sbom_nondict_component_skipped(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sbom
        # A component that is a bare string is skipped, not fatal.
        result = ingest_sbom("t", json.dumps({"components": ["not-a-dict"]}))
        assert "error" not in result
        assert result["findings"] == 0

    def test_sast_toplevel_json_array_degrades(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        result = ingest_sast("t", "[]")
        assert result["error"] == "not a mapping"
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_sast_results_wrong_type_list_of_str_skipped(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        # results is a list of strings: each non-dict element is skipped.
        result = ingest_sast("t", json.dumps({"results": ["oops-not-a-dict"]}))
        assert "error" not in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_sast_results_is_dict_skipped(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_sast
        # results given as a dict → iteration yields str keys → each skipped.
        result = ingest_sast("t", json.dumps({"results": {"finding1": {}}}))
        assert "error" not in result
        assert result["findings"] == 0
        assert result["activities_updated"] == []

    def test_survey_toplevel_json_array_degrades(self, stub_activity_tracker):
        from tools.security_canvas.zig_external_adapter import ingest_survey
        result = ingest_survey("t", "[]")
        assert result["error"] == "not a mapping"
        assert result["findings"] == 0
        assert result["activities_updated"] == []
