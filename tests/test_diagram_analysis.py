"""Unit tests for tools/network/diagram_analysis.py."""

import json
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock


# Make sure the package root is on sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.network.diagram_analysis import (
    _esc,
    _merge_tab_results,
    _sev_rank,
    compute_file_hash,
    detect_cloud_providers,
    detect_format,
    enrich_with_attack,
    generate_drawio_export,
    generate_html_report,
    parse_analysis_response,
    save_findings,
)
from tools.network.constants import (
    DIAGRAM_ANALYSIS_FINDING_CATEGORIES,
    DIAGRAM_ANALYSIS_INDUSTRIES,
    DIAGRAM_CLOUD_PROVIDERS,
    DIAGRAM_TOPOLOGY_MODES,
    DIAGRAM_UPLOAD_FORMATS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _png_bytes() -> bytes:
    """Minimal valid 1x1 PNG."""
    # PNG magic + IHDR + IDAT + IEND
    import zlib
    def chunk(name, data):
        c = name + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    magic = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat_data = zlib.compress(b"\x00\xff\xff\xff")
    idat = chunk(b"IDAT", idat_data)
    iend = chunk(b"IEND", b"")
    return magic + ihdr + idat + iend


def _jpeg_bytes() -> bytes:
    return b"\xff\xd8\xff\xe0" + b"\x00" * 20


def _pdf_bytes() -> bytes:
    return b"%PDF-1.4\n%EOF"


def _drawio_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        '<mxCell id="2" value="AWS VPC" style="rounded=1;" vertex="1" parent="1">'
        '<mxGeometry x="100" y="100" width="120" height="40" as="geometry"/></mxCell>'
        '<mxCell id="3" value="Azure VNet" style="rounded=1;" vertex="1" parent="1">'
        '<mxGeometry x="300" y="100" width="120" height="40" as="geometry"/></mxCell>'
        '<mxCell id="4" value="VPN Tunnel" edge="1" source="2" target="3" parent="1">'
        '<mxGeometry relative="1" as="geometry"/></mxCell>'
        '</root></mxGraphModel>'
    )


def _drawio_bytes() -> bytes:
    return _drawio_xml().encode("utf-8")


# ── Task 1: detect_format ─────────────────────────────────────────────────────

class TestDetectFormat:
    def test_png_by_ext(self):
        assert detect_format("diagram.png", _png_bytes()) == "png"

    def test_jpg_by_ext(self):
        assert detect_format("photo.jpg", _jpeg_bytes()) == "jpg"

    def test_jpeg_ext(self):
        assert detect_format("photo.jpeg", _jpeg_bytes()) == "jpg"

    def test_pdf_by_magic(self):
        assert detect_format("doc.pdf", _pdf_bytes()) == "pdf"

    def test_pdf_by_ext(self):
        assert detect_format("report.pdf", b"other bytes") == "pdf"

    def test_drawio_by_content(self):
        assert detect_format("net.drawio", _drawio_bytes()) == "drawio"

    def test_drawio_xml_ext_with_mxgraph(self):
        assert detect_format("net.xml", _drawio_bytes()) == "drawio"

    def test_docx_by_ext(self):
        docx_bytes = b"PK\x03\x04" + b"\x00" * 20
        assert detect_format("doc.docx", docx_bytes) == "docx"

    def test_unknown(self):
        assert detect_format("file.xyz", b"garbage") == "unknown"


# ── Task 2: compute_file_hash ─────────────────────────────────────────────────

def test_compute_file_hash_deterministic():
    data = b"hello world"
    h1 = compute_file_hash(data)
    h2 = compute_file_hash(data)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_file_hash_different_inputs():
    assert compute_file_hash(b"aaa") != compute_file_hash(b"bbb")


# ── Task 3: detect_cloud_providers ───────────────────────────────────────────

class TestDetectCloudProviders:
    def test_aws_from_text(self):
        result = detect_cloud_providers(None, "The topology uses AWS VPC and EC2 instances with Security Groups.")
        assert "aws" in result["providers"]
        assert result["mode"] in ("cloud_native", "hybrid", "multi_cloud")

    def test_azure_from_text(self):
        result = detect_cloud_providers(None, "Azure VNet connected via ExpressRoute to on-premises datacenter.")
        assert "azure" in result["providers"]
        assert result["has_on_prem"] is True

    def test_multi_cloud(self):
        result = detect_cloud_providers(None, "AWS VPC peered to Azure VNet via VPN tunnel.")
        assert "aws" in result["providers"]
        assert "azure" in result["providers"]
        assert result["mode"] == "multi_cloud"

    def test_hybrid_on_prem(self):
        result = detect_cloud_providers(None, "AWS region connected to on-premises data center via Direct Connect.")
        assert result["has_on_prem"] is True
        assert result["mode"] == "hybrid"

    def test_on_prem_only(self):
        result = detect_cloud_providers(None, "Core router and firewall in the main data center.")
        assert result["providers"] == []
        assert result["mode"] == "on_prem"

    def test_graph_json_label_detection(self):
        graph = {
            "nodes": [
                {"id": "1", "label": "AWS EC2 instance"},
                {"id": "2", "label": "GKE cluster"},
            ],
            "edges": [],
            "zones": [],
        }
        result = detect_cloud_providers(graph, "")
        assert "aws" in result["providers"]
        assert "gcp" in result["providers"]
        assert result["mode"] == "multi_cloud"

    def test_connectivity_detected(self):
        result = detect_cloud_providers(None, "VPN tunnel connects on-premises to AWS VPC via IPSec.")
        assert "vpn" in result["connectivity"]


# ── Task 4: build_industry_prompt ────────────────────────────────────────────

class TestBuildIndustryPrompt:
    def setup_method(self):
        from tools.network.diagram_analysis import build_industry_prompt
        self.build = build_industry_prompt

    def test_dod_il4_contains_nipr_sipr(self):
        cloud_ctx = {"providers": [], "mode": "on_prem", "has_on_prem": False}
        prompt = self.build("dod_il4", cloud_ctx)
        assert "NIPR" in prompt
        assert "SIPR" in prompt
        assert "IPSec" in prompt or "IKEv2" in prompt

    def test_dod_il4_contains_stig(self):
        cloud_ctx = {"providers": [], "mode": "on_prem", "has_on_prem": False}
        prompt = self.build("dod_il4", cloud_ctx)
        assert "STIG" in prompt

    def test_all_industries_include_csa_ccm(self):
        cloud_ctx = {"providers": [], "mode": "cloud_native", "has_on_prem": False}
        for industry in DIAGRAM_ANALYSIS_INDUSTRIES:
            if industry == "dod_il6":
                continue  # IL6 doesn't include CCM (NSA Type 1 only)
            prompt = self.build(industry, cloud_ctx)
            assert "CSA" in prompt or "CCM" in prompt, f"{industry} missing CSA/CCM"

    def test_aws_checks_in_prompt_when_detected(self):
        cloud_ctx = {"providers": ["aws"], "mode": "cloud_native", "has_on_prem": False}
        prompt = self.build("commercial", cloud_ctx)
        assert "Security Group" in prompt or "CloudTrail" in prompt

    def test_azure_checks_in_prompt_when_detected(self):
        cloud_ctx = {"providers": ["azure"], "mode": "cloud_native", "has_on_prem": False}
        prompt = self.build("commercial", cloud_ctx)
        assert "NSG" in prompt or "Azure" in prompt

    def test_hybrid_checks_added(self):
        cloud_ctx = {"providers": ["aws"], "mode": "hybrid", "has_on_prem": True}
        prompt = self.build("commercial", cloud_ctx)
        assert "VPN" in prompt or "IKEv2" in prompt

    def test_healthcare_contains_hipaa(self):
        cloud_ctx = {"providers": [], "mode": "on_prem", "has_on_prem": False}
        prompt = self.build("healthcare", cloud_ctx)
        assert "HIPAA" in prompt
        assert "PHI" in prompt

    def test_response_schema_in_prompt(self):
        cloud_ctx = {"providers": [], "mode": "on_prem", "has_on_prem": False}
        prompt = self.build("commercial", cloud_ctx)
        assert "overview" in prompt
        assert "remediate" in prompt


# ── Task 5: parse_analysis_response ──────────────────────────────────────────

class TestParseAnalysisResponse:
    def test_valid_json(self):
        data = {
            "overview": [{"title": "Clean topology", "detail": "No issues", "severity": "info"}],
            "inventory": [{"component": "Firewall", "type": "security", "count": 1, "provider": "on_prem", "detail": ""}],
            "topology": [],
            "security": [],
            "compliance": [],
            "remediate": [],
        }
        raw = json.dumps(data)
        result = parse_analysis_response(raw)
        assert len(result["overview"]) == 1
        assert result["overview"][0]["title"] == "Clean topology"

    def test_strips_markdown_fences(self):
        data = {"overview": [], "inventory": [], "topology": [], "security": [{"title": "Gap", "severity": "high", "detail": "", "affected_component": ""}], "compliance": [], "remediate": []}
        raw = "```json\n" + json.dumps(data) + "\n```"
        result = parse_analysis_response(raw)
        assert len(result["security"]) == 1

    def test_extracts_json_from_prose(self):
        data = {"overview": [{"title": "OK", "detail": "", "severity": "info"}], "inventory": [], "topology": [], "security": [], "compliance": [], "remediate": []}
        raw = "Here is my analysis:\n" + json.dumps(data) + "\nDone."
        result = parse_analysis_response(raw)
        assert result["overview"][0]["title"] == "OK"

    def test_fallback_on_invalid_json(self):
        result = parse_analysis_response("This is not JSON at all.")
        for key in ["overview", "inventory", "topology", "security", "compliance", "remediate"]:
            assert isinstance(result[key], list)

    def test_all_six_keys_present(self):
        result = parse_analysis_response("{}")
        for key in ["overview", "inventory", "topology", "security", "compliance", "remediate"]:
            assert key in result


# ── Task 6: generate_drawio_export ───────────────────────────────────────────

class TestGenerateDrawioExport:
    def _minimal_result(self, security_findings=None):
        return {
            "tabs": {
                "overview": [{"title": "Overview", "detail": "Test", "severity": "info"}],
                "inventory": [{"component": "Router", "type": "network", "count": 1, "provider": "on_prem", "detail": ""}],
                "topology": [],
                "security": security_findings or [
                    {"title": "Open port 22", "detail": "SSH exposed", "severity": "high", "affected_component": "firewall"}
                ],
                "compliance": [],
                "remediate": [{"priority": 1, "title": "Restrict SSH", "detail": "Close port 22", "references": [{"type": "NIST", "id": "CM-7", "description": "Least functionality"}], "effort": "low"}],
            }
        }

    def test_returns_valid_xml(self):
        xml = generate_drawio_export(self._minimal_result(), None)
        root = ET.fromstring(xml)
        assert root.tag == "mxGraphModel"

    def test_contains_root_cell(self):
        xml = generate_drawio_export(self._minimal_result(), None)
        root = ET.fromstring(xml)
        cells = root.findall(".//mxCell")
        ids = [c.get("id") for c in cells]
        assert "0" in ids
        assert "1" in ids

    def test_legend_swimlane_present(self):
        xml = generate_drawio_export(self._minimal_result(), None)
        assert "Legend" in xml

    def test_classification_banner_dod_il4(self):
        xml = generate_drawio_export(self._minimal_result(), None, industry="dod_il4")
        assert "UNCLASSIFIED" in xml
        assert "CUI" in xml

    def test_classification_banner_dod_il6(self):
        xml = generate_drawio_export(self._minimal_result(), None, industry="dod_il6")
        assert "SECRET" in xml

    def test_no_classification_banner_commercial(self):
        xml = generate_drawio_export(self._minimal_result(), None, industry="commercial")
        assert "UNCLASSIFIED" not in xml
        assert "SECRET" not in xml

    def test_multi_cloud_swimlanes(self):
        cloud_ctx = {"providers": ["aws", "azure"], "mode": "multi_cloud", "has_on_prem": False}
        xml = generate_drawio_export(self._minimal_result(), None, cloud_context=cloud_ctx)
        assert "Amazon Web Services" in xml
        assert "Microsoft Azure" in xml

    def test_hybrid_boundary_marker(self):
        cloud_ctx = {"providers": ["aws"], "mode": "hybrid", "has_on_prem": True}
        xml = generate_drawio_export(self._minimal_result(), None, cloud_context=cloud_ctx)
        # Should contain shared responsibility boundary text
        assert "Cloud-Managed" in xml or "Customer-Managed" in xml

    def test_security_annotation_cells(self):
        xml = generate_drawio_export(self._minimal_result(), None)
        # Should contain annotation for "Open port 22"
        assert "Open port 22" in xml

    def test_original_graph_nodes_included(self):
        graph = {
            "nodes": [{"id": "n1", "label": "Core Router", "shape": "rectangle", "style": {}, "zone": None}],
            "edges": [],
            "zones": [],
        }
        xml = generate_drawio_export(self._minimal_result(), graph)
        assert "Core Router" in xml


# ── Task 7: save_findings ─────────────────────────────────────────────────────

def test_save_findings_inserts_rows():
    mock_conn = MagicMock()
    tabs = {
        "security": [{"title": "Open SSH", "severity": "high", "detail": "Port 22 exposed", "affected_component": "fw"}],
        "compliance": [{"control_id": "AC-2", "framework": "NIST", "status": "gap", "detail": "No MFA", "severity": "medium"}],
        "remediate": [{"title": "Fix SSH", "detail": "Close port", "priority": 1, "references": [], "effort": "low"}],
        "overview": [],
        "inventory": [],
        "topology": [],
    }
    save_findings(mock_conn, "analysis-123", tabs)
    # Should have executed INSERT for security + compliance + remediate = 3 rows
    assert mock_conn.execute.call_count == 3
    # Verify INSERT SQL contains correct table name
    first_call_sql = mock_conn.execute.call_args_list[0][0][0]
    assert "nc_diagram_findings" in first_call_sql


# ── Task 8: _esc and _sev_rank ────────────────────────────────────────────────

def test_esc_html_chars():
    assert _esc("<script>") == "&lt;script&gt;"
    assert _esc("AT&T") == "AT&amp;T"
    assert _esc('"quoted"') == "&quot;quoted&quot;"


def test_sev_rank_ordering():
    assert _sev_rank("critical") > _sev_rank("high")
    assert _sev_rank("high") > _sev_rank("medium")
    assert _sev_rank("medium") > _sev_rank("low")
    assert _sev_rank("low") > _sev_rank("info")
    assert _sev_rank("unknown") == 0


# ── Task 9: Constants validation ──────────────────────────────────────────────

def test_diagram_analysis_industries_structure():
    assert "dod_il4" in DIAGRAM_ANALYSIS_INDUSTRIES
    assert "healthcare" in DIAGRAM_ANALYSIS_INDUSTRIES
    assert "commercial" in DIAGRAM_ANALYSIS_INDUSTRIES
    for key, info in DIAGRAM_ANALYSIS_INDUSTRIES.items():
        assert "label" in info
        assert "frameworks" in info
        assert isinstance(info["frameworks"], list)


def test_diagram_cloud_providers_structure():
    assert "aws" in DIAGRAM_CLOUD_PROVIDERS
    assert "azure" in DIAGRAM_CLOUD_PROVIDERS
    assert "gcp" in DIAGRAM_CLOUD_PROVIDERS
    for key, info in DIAGRAM_CLOUD_PROVIDERS.items():
        assert "label" in info


def test_diagram_topology_modes():
    assert "cloud_native" in DIAGRAM_TOPOLOGY_MODES
    assert "hybrid" in DIAGRAM_TOPOLOGY_MODES
    assert "multi_cloud" in DIAGRAM_TOPOLOGY_MODES
    assert "on_prem" in DIAGRAM_TOPOLOGY_MODES


def test_diagram_analysis_finding_categories():
    expected = {"overview", "inventory", "topology", "security", "compliance", "remediate"}
    assert set(DIAGRAM_ANALYSIS_FINDING_CATEGORIES) == expected


def test_diagram_upload_formats():
    assert "png" in DIAGRAM_UPLOAD_FORMATS
    assert "pdf" in DIAGRAM_UPLOAD_FORMATS
    assert "drawio" in DIAGRAM_UPLOAD_FORMATS
    assert "docx" in DIAGRAM_UPLOAD_FORMATS


# ── _merge_tab_results ────────────────────────────────────────────────────────

def _make_batch(tab_key: str, titles: list[str], extra: dict = {}) -> dict:
    items = [{**{"title": t, "detail": "d", "severity": "low"}, **extra} for t in titles]
    return {k: (items if k == tab_key else []) for k in
            ["overview", "inventory", "topology", "security", "compliance", "remediate"]}


def test_merge_tab_results_deduplicates_by_title():
    b1 = _make_batch("security", ["Open port", "Weak TLS"])
    b2 = _make_batch("security", ["Open port", "No MFA"])  # "Open port" is a dup
    merged = _merge_tab_results([b1, b2])
    titles = [i["title"] for i in merged["security"]]
    assert titles.count("Open port") == 1
    assert "Weak TLS" in titles
    assert "No MFA" in titles


def test_merge_tab_results_renumbers_remediate():
    b1 = _make_batch("remediate", ["Fix A", "Fix B"])
    b2 = _make_batch("remediate", ["Fix C"])
    merged = _merge_tab_results([b1, b2])
    priorities = [i["priority"] for i in merged["remediate"]]
    assert priorities == [1, 2, 3]


def test_merge_tab_results_empty_batches():
    merged = _merge_tab_results([])
    assert all(v == [] for v in merged.values())


def test_merge_tab_results_cross_tab_dedup_independent():
    """Same title in different tabs should NOT dedup across tabs."""
    b1 = _make_batch("security", ["Shared"])
    b2 = _make_batch("compliance", ["Shared"])
    merged = _merge_tab_results([b1, b2])
    assert len(merged["security"]) == 1
    assert len(merged["compliance"]) == 1


# ── enrich_with_attack ────────────────────────────────────────────────────────

_MINIMAL_TABS = {
    "overview": [],
    "inventory": [],
    "topology": [],
    "security": [{"title": "Unencrypted traffic", "severity": "high", "detail": "HTTP without TLS"}],
    "compliance": [{"title": "Firewall gap", "control_id": "AC-4", "severity": "medium", "detail": "ingress misconfiguration"}],
    "remediate": [{"title": "Rotate credentials", "priority": 1, "detail": "default password found"}],
}


def test_enrich_with_attack_adds_references():
    result = enrich_with_attack(_MINIMAL_TABS)
    sec = result["security"]
    assert len(sec) == 1
    refs = sec[0].get("references", [])
    assert len(refs) > 0
    ref_types = {r["type"] for r in refs}
    assert "ATT&CK" in ref_types or "D3FEND" in ref_types


def test_enrich_with_attack_no_dup_refs():
    tabs = {
        **{k: [] for k in ["overview", "inventory", "topology"]},
        "security": [{"title": "weak credentials default password", "detail": "brute force"}],
        "compliance": [],
        "remediate": [],
    }
    result = enrich_with_attack(tabs)
    refs = result["security"][0].get("references", [])
    ids = [r["id"] for r in refs]
    assert len(ids) == len(set(ids)), "Duplicate reference IDs found"


def test_enrich_with_attack_preserves_non_matching():
    tabs = {
        **{k: [] for k in ["overview", "inventory", "topology"]},
        "security": [{"title": "Completely unrelated finding", "detail": "no keywords"}],
        "compliance": [],
        "remediate": [],
    }
    result = enrich_with_attack(tabs)
    assert result["security"][0].get("references", []) == []


def test_enrich_with_attack_does_not_mutate_input():
    import copy
    original = copy.deepcopy(_MINIMAL_TABS)
    enrich_with_attack(_MINIMAL_TABS)
    assert _MINIMAL_TABS == original


# ── generate_html_report ──────────────────────────────────────────────────────

_SAMPLE_RESULT = {
    "tabs": {
        "overview": [{"severity": "high", "title": "Critical exposure", "detail": "Exposed RDP"}],
        "inventory": [{"component": "LoadBalancer", "type": "network", "count": 2, "provider": "aws", "detail": "ALB"}],
        "topology": [{"zone": "DMZ", "severity": "medium", "trust_level": "untrusted", "cloud_provider": "aws", "description": "edge zone"}],
        "security": [{"severity": "critical", "title": "Open RDP", "affected_component": "server-1", "detail": "port 3389", "references": [
            {"type": "ATT&CK", "id": "T1021.001", "description": "Remote Desktop Protocol", "url": "https://attack.mitre.org/techniques/T1021/001/"}
        ]}],
        "compliance": [{"control_id": "AC-17", "framework": "NIST", "severity": "high", "status": "gap", "detail": "no MFA"}],
        "remediate": [{"priority": 1, "title": "Block RDP", "effort": "low", "category": "network", "detail": "close 3389"}],
    }
}

_SAMPLE_META = {
    "filename": "test_diagram.png",
    "industry": "defense",
    "topology_mode": "hybrid",
    "cloud_providers": ["aws"],
    "analysis_id": "abc123",
    "created_at": "2026-06-23T00:00:00Z",
}


def test_generate_html_report_returns_string():
    html = generate_html_report(_SAMPLE_RESULT, _SAMPLE_META)
    assert isinstance(html, str)
    assert len(html) > 200


def test_generate_html_report_no_external_links():
    html = generate_html_report(_SAMPLE_RESULT, _SAMPLE_META)
    import re as _re
    # No <link rel="stylesheet"> or <script src="..."> loading external files
    assert not _re.search(r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']https?://', html)
    assert not _re.search(r'<script[^>]+src=["\']https?://', html)


def test_generate_html_report_contains_key_sections():
    html = generate_html_report(_SAMPLE_RESULT, _SAMPLE_META)
    assert "Open RDP" in html
    assert "LoadBalancer" in html
    assert "DMZ" in html
    assert "AC-17" in html
    assert "Block RDP" in html


def test_generate_html_report_xss_escaped():
    evil = {"tabs": {
        "overview": [{"severity": "high", "title": "<script>alert(1)</script>", "detail": "xss"}],
        "inventory": [], "topology": [], "security": [], "compliance": [], "remediate": [],
    }}
    html = generate_html_report(evil, _SAMPLE_META)
    assert "<script>alert(1)</script>" not in html


def test_generate_html_report_empty_tabs():
    empty = {"tabs": {k: [] for k in ["overview", "inventory", "topology", "security", "compliance", "remediate"]}}
    html = generate_html_report(empty, _SAMPLE_META)
    assert "<html" in html.lower()  # well-formed even with no data
