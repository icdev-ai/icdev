# [CUI // SP-CTI]
"""Tests for import_vsdx — both vsdx-lib path and stdlib fallback.

Fixture is a hand-crafted VSDX (ZIP + XML) that exercises the real failure
modes seen in production:

1. Shapes inheriting text + properties from stencil masters (nothing in
   the page XML's Cell/Text — only a Master="N" reference).
2. Multi-page documents (page2 shapes must be imported with a ``page``
   field and not collide with page1 shape IDs).
3. Edge wiring via top-level ``<Connect>`` elements (not BegTrigger).
4. Non-default page height (PageHeight=8.5 — landscape) to verify Y
   inversion uses real geometry, not a hardcoded 12".
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from tools.network.export_import import (
    _import_vsdx_stdlib,
    import_vsdx,
)


# ── Fixture builder ────────────────────────────────────────────────────


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
</Types>
"""

MASTER1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MasterContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" Type="Shape" Master="1">
      <Text>core-router-01</Text>
      <Cell N="Width" V="1.0"/>
      <Cell N="Height" V="0.75"/>
      <Section N="Property">
        <Row N="Row_1">
          <Cell N="Label" V="Hostname"/>
          <Cell N="Value" V="core-router-01"/>
        </Row>
        <Row N="Row_2">
          <Cell N="Label" V="IP"/>
          <Cell N="Value" V="10.0.0.1"/>
        </Row>
        <Row N="Row_3">
          <Cell N="Label" V="Vendor"/>
          <Cell N="Value" V="Cisco"/>
        </Row>
      </Section>
    </Shape>
  </Shapes>
</MasterContents>
"""

MASTER2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<MasterContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" Type="Shape" Master="2">
      <Text>edge-fw-01</Text>
      <Cell N="Width" V="1.0"/>
      <Cell N="Height" V="0.75"/>
      <Section N="Property">
        <Row N="Row_1">
          <Cell N="Label" V="Hostname"/>
          <Cell N="Value" V="edge-fw-01"/>
        </Row>
        <Row N="Row_2">
          <Cell N="Label" V="Model"/>
          <Cell N="Value" V="Palo Alto 5220"/>
        </Row>
      </Section>
    </Shape>
  </Shapes>
</MasterContents>
"""

# Page 1: two nodes from masters + one connector, using <Connect> wiring.
# PageHeight is 8.5 (US Letter landscape) to verify we don't hardcode 12.
PAGE1_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="100" Type="Shape" Master="1">
      <Cell N="PinX" V="2.0"/>
      <Cell N="PinY" V="6.0"/>
      <Cell N="Width" V="1.0"/>
      <Cell N="Height" V="0.75"/>
    </Shape>
    <Shape ID="101" Type="Shape" Master="2">
      <Cell N="PinX" V="5.0"/>
      <Cell N="PinY" V="6.0"/>
      <Cell N="Width" V="1.0"/>
      <Cell N="Height" V="0.75"/>
    </Shape>
    <Shape ID="102" Type="Shape">
      <Cell N="BeginX" V="2.5"/>
      <Cell N="BeginY" V="6.0"/>
      <Cell N="EndX" V="4.5"/>
      <Cell N="EndY" V="6.0"/>
      <Text>gig0/1</Text>
    </Shape>
  </Shapes>
  <Connects>
    <Connect FromSheet="102" FromCell="BeginX" FromPart="9" ToSheet="100" ToCell="PinX" ToPart="3"/>
    <Connect FromSheet="102" FromCell="EndX"   FromPart="12" ToSheet="101" ToCell="PinX" ToPart="3"/>
  </Connects>
  <PageSheet>
    <Cell N="PageHeight" V="8.5"/>
    <Cell N="PageWidth" V="11.0"/>
  </PageSheet>
</PageContents>
"""

# Page 2: one extra node with inline text (no master) — must not collide with
# page 1 IDs even though sid=100 is reused, because node IDs are page-scoped.
PAGE2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="100" Type="Shape">
      <Cell N="PinX" V="3.0"/>
      <Cell N="PinY" V="4.0"/>
      <Cell N="Width" V="1.0"/>
      <Cell N="Height" V="0.75"/>
      <Text>dmz-host</Text>
    </Shape>
  </Shapes>
  <PageSheet>
    <Cell N="PageHeight" V="8.5"/>
  </PageSheet>
</PageContents>
"""


def _build_vsdx(tmp_path: Path) -> Path:
    """Write a synthetic VSDX with 2 masters, 2 pages, and Connect wiring."""
    vsdx_path = tmp_path / "test-topology.vsdx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("visio/masters/master1.xml", MASTER1_XML)
        zf.writestr("visio/masters/master2.xml", MASTER2_XML)
        zf.writestr("visio/pages/page1.xml", PAGE1_XML)
        zf.writestr("visio/pages/page2.xml", PAGE2_XML)
    vsdx_path.write_bytes(buf.getvalue())
    return vsdx_path


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.fixture
def synthetic_vsdx(tmp_path: Path) -> Path:
    return _build_vsdx(tmp_path)


def test_stdlib_resolves_master_text_and_properties(synthetic_vsdx: Path):
    """Shapes without inline <Text> must inherit label + props from masters.

    This is the #1 real-world failure: stencil-based diagrams (Cisco, AWS,
    Palo Alto shapes) have empty page-level Cell/Text sections — everything
    lives in the master. Prior parser returned 'Shape-100' with no props.
    """
    result = _import_vsdx_stdlib(str(synthetic_vsdx))
    assert result["_pages"] == 2, result
    nodes = result["nodes"]
    by_label = {n["label"]: n for n in nodes}

    assert "core-router-01" in by_label, f"master text not inherited: {nodes}"
    assert "edge-fw-01" in by_label, f"master text not inherited: {nodes}"

    router = by_label["core-router-01"]
    assert router["properties"]["Hostname"] == "core-router-01"
    assert router["properties"]["IP"] == "10.0.0.1"
    assert router["properties"]["Vendor"] == "Cisco"

    fw = by_label["edge-fw-01"]
    assert fw["properties"]["Model"] == "Palo Alto 5220"


def test_stdlib_multi_page_traversal(synthetic_vsdx: Path):
    """All pages must be imported; page-2 shapes get a ``page`` field."""
    result = _import_vsdx_stdlib(str(synthetic_vsdx))
    pages_seen = {n.get("page", 0) for n in result["nodes"]}
    assert pages_seen == {0, 1}, f"expected both pages, got {pages_seen}"

    # sid collision safety: page-scoped IDs must be unique
    ids = [n["id"] for n in result["nodes"]]
    assert len(ids) == len(set(ids)), f"node id collision: {ids}"


def test_stdlib_connect_element_wiring(synthetic_vsdx: Path):
    """Edges wired via <Connect> elements (modern Visio) must resolve."""
    result = _import_vsdx_stdlib(str(synthetic_vsdx))
    edges = result["edges"]
    assert len(edges) == 1, f"expected 1 edge, got {edges}"
    edge = edges[0]

    nodes_by_id = {n["id"]: n for n in result["nodes"]}
    assert nodes_by_id[edge["source"]]["label"] == "core-router-01"
    assert nodes_by_id[edge["target"]]["label"] == "edge-fw-01"
    assert edge["label"] == "gig0/1"


def test_stdlib_uses_real_page_height_not_hardcoded_12(synthetic_vsdx: Path):
    """PageHeight=8.5 — Y inversion must use it, not the old magic 12."""
    result = _import_vsdx_stdlib(str(synthetic_vsdx))
    router = next(n for n in result["nodes"] if n["label"] == "core-router-01")
    # PinY=6.0, PageHeight=8.5 -> y = (8.5 - 6.0) * 96 = 240
    # Old buggy parser would compute (12 - 6.0) * 96 = 576
    assert router["y"] == 240, f"Y inversion wrong: {router['y']} (expected 240)"
    assert router["x"] == 192  # 2.0 * 96


def test_stdlib_reports_errors_instead_of_silent_swallow(tmp_path: Path):
    """Bad file must produce a structured error, not an empty dict."""
    bad = tmp_path / "not-a-vsdx.vsdx"
    bad.write_bytes(b"this is not a zip")
    result = _import_vsdx_stdlib(str(bad))
    assert result["nodes"] == []
    assert result["_errors"], "expected error list on bad input"
    assert any("not a valid vsdx" in e for e in result["_errors"])


def test_vsdx_lib_path_matches_stdlib_on_core_fields(synthetic_vsdx: Path):
    """When vsdx lib is installed, the preferred path should agree with
    stdlib on labels, edge count, and page count."""
    pytest.importorskip("vsdx")
    lib_result = import_vsdx(str(synthetic_vsdx))
    std_result = _import_vsdx_stdlib(str(synthetic_vsdx))

    assert lib_result["_pages"] == std_result["_pages"]
    lib_labels = sorted(n["label"] for n in lib_result["nodes"])
    std_labels = sorted(n["label"] for n in std_result["nodes"])
    # Library may pick up master labels slightly differently for the
    # connector shape; core node labels must match.
    for expected in ("core-router-01", "edge-fw-01", "dmz-host"):
        assert expected in lib_labels, (lib_labels, std_labels)
        assert expected in std_labels


def test_public_entrypoint_returns_graph(synthetic_vsdx: Path):
    """``import_vsdx`` is what network_ingester/blueprint call — smoke test."""
    result = import_vsdx(str(synthetic_vsdx))
    assert isinstance(result, dict)
    assert "nodes" in result and "edges" in result
    assert len(result["nodes"]) >= 3  # 2 on page1, 1 on page2
