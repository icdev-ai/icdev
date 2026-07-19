# CUI // SP-CTI
"""Unit tests for the NDC network-diagram PDF exporter (ndc-qa-02).

Covers ``tools/network/pdf_export.py``:
  - export_phase_pdf: a seeded topology/phase exports to a non-empty PDF whose
    bytes start with the %PDF- magic and are of plausible size.
  - empty topology still produces a valid PDF (cover page only) with no crash.
  - export_to_pdf: writes a classified PDF file to disk (%PDF- magic).
  - _build_html_fallback: the fpdf2-unavailable path returns HTML bytes carrying
    the topology name and CUI banner.
  - malformed graph (None) raises cleanly.

Structural assertions only (magic bytes / size / substrings) — no pixel checks.
Tests needing fpdf2 are skipif-guarded so they no-op where the optional dep is
absent (air-gap installs may lack it).
"""

from __future__ import annotations

import json

import pytest

from tools.network.pdf_export import (
    _build_html_fallback,
    export_phase_pdf,
    export_to_pdf,
)

try:  # optional dependency — pure-Python fpdf2
    import fpdf  # noqa: F401

    _HAS_FPDF = True
except ImportError:  # pragma: no cover
    _HAS_FPDF = False

_needs_fpdf = pytest.mark.skipif(not _HAS_FPDF, reason="fpdf2 not installed")


def _sample_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "label": "CoreRouter1", "type": "router", "x": 40, "y": 40,
             "model": "Cisco ASR9000", "ip": "10.0.0.1", "phase_state": "existing"},
            {"id": "n2", "label": "EdgeFirewall", "type": "firewall", "x": 240, "y": 40,
             "model": "Juniper SRX", "ip": "10.0.0.2", "phase_state": "new"},
            {"id": "n3", "label": "LegacySwitch", "type": "switch", "x": 140, "y": 220,
             "model": "Cisco 3560", "ip": "10.0.0.3", "phase_state": "retiring"},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "label": "10GbE"},
            {"id": "e2", "source": "n1", "target": "n3", "label": "1GbE"},
        ],
    }


def _sample_infoboxes() -> list[dict]:
    return [
        {"title": "IP Plan", "color": "#1e6eb5", "rows": [
            {"label": "Mgmt Subnet", "value": "10.0.0.0/24", "status": "ok"},
            {"label": "Conflicts", "value": "1", "status": "critical"},
        ]},
        {"title": "Routing", "color": "#27ae60", "rows": [
            {"label": "Protocol", "value": "BGP"},
        ]},
    ]


# ── export_phase_pdf ───────────────────────────────────────────────────────────

@_needs_fpdf
def test_export_phase_pdf_produces_pdf_bytes():
    data = export_phase_pdf(
        "SeededTopo", "Phase 1: Cutover", _sample_graph(), _sample_infoboxes()
    )
    assert isinstance(data, (bytes, bytearray))
    assert data[:5] == b"%PDF-"
    # A multi-page report with diagram + tables is comfortably over 1 KB.
    assert len(data) > 1000


@_needs_fpdf
def test_export_phase_pdf_empty_topology_still_valid():
    data = export_phase_pdf("EmptyTopo", "Phase 0", {"nodes": [], "edges": []}, [])
    # Cover page is always emitted -> valid PDF, no crash.
    assert data[:5] == b"%PDF-"
    assert len(data) > 500


@_needs_fpdf
def test_export_phase_pdf_with_phase_meta_and_consolidation():
    phase_meta = {
        "phase_num": 2,
        "title": "Migration",
        "port_mappings": [
            {"source_device": "core-rtr-01", "source_port": "Gi0/1",
             "target_device": "core-rtr-02", "target_port": "Te0/1",
             "speed": "10G", "media_type": "SFP+"},
            {"source_device": "core-rtr-01", "source_port": "Gi0/2",
             "target_device": "", "target_port": "", "conflict": True},
        ],
    }
    consolidation = {"devices_removed": 3, "rack_units_freed": 6, "tco_3yr_delta": -50000}
    data = export_phase_pdf(
        "SeededTopo", "Phase 2", _sample_graph(), _sample_infoboxes(),
        consolidation=consolidation, phase_meta=phase_meta,
    )
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000


def test_export_phase_pdf_none_graph_raises_cleanly():
    with pytest.raises(AttributeError):
        export_phase_pdf("Bad", "Phase X", None, [])


# ── export_to_pdf (file output) ────────────────────────────────────────────────

@_needs_fpdf
def test_export_to_pdf_writes_classified_file(tmp_path):
    out = tmp_path / "report.pdf"
    export_to_pdf(
        "single line report body",
        str(out),
        title="Test Report",
        classification="CUI",
    )
    assert out.exists()
    raw = out.read_bytes()
    assert raw[:5] == b"%PDF-"
    assert len(raw) > 500


@_needs_fpdf
def test_export_to_pdf_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "deep" / "doc.pdf"
    export_to_pdf("content", str(out), title="Nested", classification="SECRET")
    assert out.exists()
    assert out.read_bytes()[:5] == b"%PDF-"


@_needs_fpdf
def test_export_to_pdf_multiline_content_exports_cleanly(tmp_path):
    """Multi-line plain-text content exports to a valid PDF (ndc-fix-01).

    Regression guard for the fixed defect where the per-line
    ``pdf.multi_cell(0, 5, ...)`` loop left the x-cursor at the right margin
    (fpdf2 default ``new_x=XPos.RIGHT``), so the second line saw zero available
    width and raised ``FPDFException``. The loop now passes
    ``new_x=XPos.LMARGIN, new_y=YPos.NEXT``.
    """
    out = tmp_path / "multiline.pdf"
    export_to_pdf("line one\nline two\nline three", str(out), title="ML", classification="CUI")
    assert out.exists()
    assert out.read_bytes()[:5] == b"%PDF-"


# ── HTML fallback (fpdf2 unavailable path) ─────────────────────────────────────

def test_build_html_fallback_returns_html_bytes():
    html = _build_html_fallback(
        "SeededTopo", "Phase 1", _sample_graph(), _sample_infoboxes(), None, None
    )
    assert isinstance(html, bytes)
    text = html.decode("utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "SeededTopo" in text
    assert "CUI // SP-CTI" in text
    # Device inventory rows are rendered for each node.
    assert "CoreRouter1" in text
    assert "EdgeFirewall" in text


def test_export_phase_pdf_falls_back_to_html_without_fpdf(monkeypatch):
    """Force the ImportError branch to exercise the HTML fallback end-to-end."""
    import builtins

    real_import = builtins.__import__

    def _no_fpdf(name, *args, **kwargs):
        if name == "fpdf":
            raise ImportError("simulated: fpdf2 unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_fpdf)

    data = export_phase_pdf("FallbackTopo", "Phase 1", _sample_graph(), _sample_infoboxes())
    assert isinstance(data, bytes)
    text = data.decode("utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "FallbackTopo" in text
    assert data[:5] != b"%PDF-"


# ── seeded-topology round-trip via temp SQLite ─────────────────────────────────

@_needs_fpdf
def test_export_phase_pdf_from_seeded_topology_row(tmp_path, monkeypatch):
    from tools.network.db import init_db

    db_file = tmp_path / "nc_pdf_topos.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)

    conn = init_db.get_connection()
    conn.execute("CREATE TABLE topologies (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)")
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json) VALUES (%s, %s, %s)",
        ("t1", "SeededTopo", json.dumps(_sample_graph())),
    )
    conn.commit()
    row = conn.execute(
        "SELECT name, graph_json FROM topologies WHERE id=%s", ("t1",)
    ).fetchone()
    conn.close()

    data = export_phase_pdf(row["name"], "Phase 1", json.loads(row["graph_json"]), [])
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000
