# [CUI // SP-CTI]
"""End-to-end test: ingest multiple realistic network-diagram PDFs and
stitch them into a single unified topology.

Simulates a real enterprise scenario where different teams own different
slices of the network and each team publishes its own PDF diagram:

  - ``edge.pdf``   — internet edge (ISP → edge-router → edge-firewall → dmz-switch)
  - ``campus.pdf`` — campus LAN (edge-firewall → core-switch → dist-switch → access-switch)
  - ``datacenter.pdf`` — DC (dist-switch → tor-switch → server-01 / server-02)
  - ``cloud.pdf``  — cloud leg (edge-router → vpn-gateway → aws-tgw → vpc-a)

Expected stitch points (nodes that appear in ≥2 PDFs):
  - ``edge-router``   (edge + cloud)
  - ``edge-firewall`` (edge + campus)
  - ``dist-switch``   (campus + datacenter)

The test asserts the stitch succeeds — i.e., the merged graph is
connected across diagrams via those shared hostnames.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("pdfplumber")

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402

from tools.network.pdf_import import import_pdf  # noqa: E402
from tools.network.topology_merge import merge_topologies  # noqa: E402


# ── Reusable drawing primitives ───────────────────────────────────────


def _draw_node(c: rl_canvas.Canvas, x, y, label, w=110, h=40):
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setFont("Helvetica", 9)
    tw = c.stringWidth(label, "Helvetica", 9)
    c.drawString(x + (w - tw) / 2, y + h / 2 - 3, label)
    return (x, y, w, h)


def _link(c, a, b):
    """Draw a connector line between right-edge of ``a`` and left-edge of ``b``."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    c.line(ax + aw, ay + ah / 2, bx, by + bh / 2)


# ── PDF builders — one per "team" ─────────────────────────────────────


def _build_edge_pdf(path: Path) -> Path:
    """Internet edge diagram."""
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    isp = _draw_node(c, 60, 650, "isp-handoff")
    er = _draw_node(c, 200, 650, "edge-router")
    fw = _draw_node(c, 340, 650, "edge-firewall")
    dmz = _draw_node(c, 480, 650, "dmz-switch")
    _link(c, isp, er)
    _link(c, er, fw)
    _link(c, fw, dmz)
    c.showPage()
    c.save()
    return path


def _build_campus_pdf(path: Path) -> Path:
    """Campus LAN diagram — starts at edge-firewall (shared with edge team)."""
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    fw = _draw_node(c, 60, 600, "edge-firewall")
    core = _draw_node(c, 200, 600, "core-switch")
    dist = _draw_node(c, 340, 600, "dist-switch")
    acc = _draw_node(c, 480, 600, "access-switch")
    _link(c, fw, core)
    _link(c, core, dist)
    _link(c, dist, acc)
    c.showPage()
    c.save()
    return path


def _build_datacenter_pdf(path: Path) -> Path:
    """Data-center diagram — starts at dist-switch (shared with campus)."""
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    dist = _draw_node(c, 60, 650, "dist-switch")
    tor = _draw_node(c, 200, 650, "tor-switch")
    s1 = _draw_node(c, 340, 700, "server-01")
    s2 = _draw_node(c, 340, 600, "server-02")
    _link(c, dist, tor)
    _link(c, tor, s1)
    _link(c, tor, s2)
    c.showPage()
    c.save()
    return path


def _build_cloud_pdf(path: Path) -> Path:
    """Cloud leg — starts at edge-router (shared with edge team)."""
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    er = _draw_node(c, 60, 650, "edge-router")
    vpn = _draw_node(c, 200, 650, "vpn-gateway")
    tgw = _draw_node(c, 340, 650, "aws-tgw")
    vpc = _draw_node(c, 480, 650, "vpc-a")
    _link(c, er, vpn)
    _link(c, vpn, tgw)
    _link(c, tgw, vpc)
    c.showPage()
    c.save()
    return path


@pytest.fixture
def enterprise_pdfs(tmp_path: Path) -> dict[str, Path]:
    return {
        "edge": _build_edge_pdf(tmp_path / "edge.pdf"),
        "campus": _build_campus_pdf(tmp_path / "campus.pdf"),
        "datacenter": _build_datacenter_pdf(tmp_path / "datacenter.pdf"),
        "cloud": _build_cloud_pdf(tmp_path / "cloud.pdf"),
    }


# ── Tests ─────────────────────────────────────────────────────────────


def test_each_pdf_extracts_cleanly(enterprise_pdfs):
    """Sanity: every team's PDF parses to the shapes we drew."""
    expected = {
        "edge": {"isp-handoff", "edge-router", "edge-firewall", "dmz-switch"},
        "campus": {"edge-firewall", "core-switch", "dist-switch", "access-switch"},
        "datacenter": {"dist-switch", "tor-switch", "server-01", "server-02"},
        "cloud": {"edge-router", "vpn-gateway", "aws-tgw", "vpc-a"},
    }
    for team, pdf_path in enterprise_pdfs.items():
        result = import_pdf(str(pdf_path))
        labels = {n["label"] for n in result["nodes"]}
        assert labels == expected[team], (
            f"{team}: expected {expected[team]}, got {labels}"
        )
        # Every team's slice is a path — N-1 edges for N nodes
        assert len(result["edges"]) >= len(expected[team]) - 1, (
            f"{team} dropped edges: {result['edges']}"
        )


def test_stitch_merges_shared_hosts(enterprise_pdfs):
    """Shared hostnames across PDFs must collapse to single canonical nodes."""
    graphs = [(team, import_pdf(str(p))) for team, p in enterprise_pdfs.items()]
    merged = merge_topologies(graphs)

    # 4 teams × 4 nodes = 16 raw; minus 3 dupes (edge-router, edge-firewall,
    # dist-switch each appear in 2 PDFs) = 13 canonical devices.
    assert merged["_stats"]["total_nodes"] == 13, merged["_stats"]

    # The three shared hosts must be detected as stitch points
    stitched = set(merged["_stitched_hosts"])
    assert stitched == {"edge-router", "edge-firewall", "dist-switch"}, stitched


def test_stitched_graph_is_connected(enterprise_pdfs):
    """All 13 devices must be reachable from any starting node — no islands."""
    graphs = [(team, import_pdf(str(p))) for team, p in enterprise_pdfs.items()]
    merged = merge_topologies(graphs)

    adj: dict[str, set] = {n["id"]: set() for n in merged["nodes"]}
    for e in merged["edges"]:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    # BFS from any node
    start = merged["nodes"][0]["id"]
    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)

    assert len(seen) == len(merged["nodes"]), (
        f"graph split into islands — reached {len(seen)}/{len(merged['nodes'])} "
        f"from {start}"
    )


def test_stitch_preserves_source_provenance(enterprise_pdfs):
    """Every merged node/edge must carry the list of PDFs it came from."""
    graphs = [(team, import_pdf(str(p))) for team, p in enterprise_pdfs.items()]
    merged = merge_topologies(graphs)

    by_label = {n["label"]: n for n in merged["nodes"]}

    # edge-router came from both edge and cloud PDFs
    assert sorted(by_label["edge-router"]["sources"]) == ["cloud", "edge"]
    # edge-firewall came from both edge and campus PDFs
    assert sorted(by_label["edge-firewall"]["sources"]) == ["campus", "edge"]
    # dist-switch came from both campus and datacenter PDFs
    assert sorted(by_label["dist-switch"]["sources"]) == ["campus", "datacenter"]
    # Non-shared nodes cite only their owning team
    assert by_label["server-01"]["sources"] == ["datacenter"]
    assert by_label["vpc-a"]["sources"] == ["cloud"]


def test_stitch_label_normalization(tmp_path: Path):
    """Labels that differ only in case/whitespace/punctuation must still merge."""
    p1 = tmp_path / "team1.pdf"
    p2 = tmp_path / "team2.pdf"
    c1 = rl_canvas.Canvas(str(p1), pagesize=letter)
    a = _draw_node(c1, 60, 650, "Core-Router 01")
    b = _draw_node(c1, 220, 650, "switch-a")
    _link(c1, a, b)
    c1.showPage()
    c1.save()

    c2 = rl_canvas.Canvas(str(p2), pagesize=letter)
    d = _draw_node(c2, 60, 650, "core-router-01")  # different punctuation + case
    e = _draw_node(c2, 220, 650, "switch-b")
    _link(c2, d, e)
    c2.showPage()
    c2.save()

    merged = merge_topologies([
        ("t1", import_pdf(str(p1))),
        ("t2", import_pdf(str(p2))),
    ])
    stitched = set(merged["_stitched_hosts"])
    assert any("core-router" in s.lower() for s in stitched), (
        f"normalization failed to stitch: {stitched}"
    )
    assert merged["_stats"]["total_nodes"] == 3  # 2 + 2 − 1 dup
