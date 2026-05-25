# [CUI // SP-CTI]
"""Tests for tools.network.pdf_import — vector extraction + rasterizer.

Uses reportlab to generate real PDF fixtures:

- A single-page diagram: 3 rects + 2 connecting lines + labels
- A multi-page diagram: 2 pages, each with its own shapes + connector

These exercise the #1 real-world NDC failure: "Export to PDF" from
Visio/drawio produces text-based PDFs whose text + shapes the previous
implementation sent to the vision LLM instead of parsing directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

reportlab = pytest.importorskip("reportlab")
pytest.importorskip("pdfplumber")

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402

from tools.network.pdf_import import import_pdf, rasterize_pdf_pages  # noqa: E402


# ── Fixture builders ──────────────────────────────────────────────────


def _draw_labeled_rect(c: rl_canvas.Canvas, x, y, w, h, label):
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setFont("Helvetica", 10)
    # Center text in the rect
    text_w = c.stringWidth(label, "Helvetica", 10)
    c.drawString(x + (w - text_w) / 2, y + h / 2 - 5, label)


def _build_single_page_pdf(tmp_path: Path) -> Path:
    """3 nodes (router, switch, firewall) with 2 connectors."""
    path = tmp_path / "topology-1page.pdf"
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    # Rects (x, y, w, h, label)
    rects = [
        (100, 600, 120, 50, "core-router"),
        (300, 600, 120, 50, "dist-switch"),
        (500, 600, 120, 50, "edge-firewall"),
    ]
    centers = []
    for x, y, w, h, lbl in rects:
        _draw_labeled_rect(c, x, y, w, h, lbl)
        centers.append((x + w / 2, y + h / 2, x, y, w, h))

    # Connector lines: endpoints snap to right edge of src, left edge of dst
    (ax, ay, ax0, ay0, aw, ah) = centers[0]
    (bx, by, bx0, by0, bw, bh) = centers[1]
    (cx, cy, cx0, cy0, cw, ch) = centers[2]
    c.line(ax0 + aw, ay, bx0, by)  # router → switch
    c.line(bx0 + bw, by, cx0, cy)  # switch → firewall

    c.showPage()
    c.save()
    return path


def _build_multi_page_pdf(tmp_path: Path) -> Path:
    """Two pages, two devices + one connector each."""
    path = tmp_path / "topology-2page.pdf"
    c = rl_canvas.Canvas(str(path), pagesize=letter)

    # Page 1
    _draw_labeled_rect(c, 100, 600, 120, 50, "site-a-router")
    _draw_labeled_rect(c, 350, 600, 120, 50, "site-a-switch")
    c.line(220, 625, 350, 625)
    c.showPage()

    # Page 2
    _draw_labeled_rect(c, 100, 400, 120, 50, "site-b-router")
    _draw_labeled_rect(c, 350, 400, 120, 50, "site-b-switch")
    c.line(220, 425, 350, 425)
    c.showPage()

    c.save()
    return path


def _build_empty_pdf(tmp_path: Path) -> Path:
    """PDF with text only, no rectangles (simulates a scan/image-only)."""
    path = tmp_path / "text-only.pdf"
    c = rl_canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 700, "This is a report with no vector shapes.")
    c.showPage()
    c.save()
    return path


# ── Tests ────────────────────────────────────────────────────────────


@pytest.fixture
def single_page_pdf(tmp_path: Path) -> Path:
    return _build_single_page_pdf(tmp_path)


@pytest.fixture
def multi_page_pdf(tmp_path: Path) -> Path:
    return _build_multi_page_pdf(tmp_path)


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    return _build_empty_pdf(tmp_path)


def test_vector_extracts_rects_as_nodes_with_labels(single_page_pdf: Path):
    """The #1 bullet-proofing outcome: text PDFs extract without vision."""
    result = import_pdf(str(single_page_pdf))
    labels = sorted(n["label"] for n in result["nodes"])
    assert "core-router" in labels, f"got {labels}"
    assert "dist-switch" in labels
    assert "edge-firewall" in labels
    assert result["_pages"] == 1


def test_vector_extracts_lines_as_edges(single_page_pdf: Path):
    """Connector lines whose endpoints snap to rect edges become edges."""
    result = import_pdf(str(single_page_pdf))
    assert len(result["edges"]) == 2, (
        f"expected 2 edges, got {len(result['edges'])}: {result['edges']}"
    )
    # Each edge must reference two real nodes on the page
    node_ids = {n["id"] for n in result["nodes"]}
    for e in result["edges"]:
        assert e["source"] in node_ids
        assert e["target"] in node_ids
        assert e["source"] != e["target"]


def test_vector_preserves_coordinates(single_page_pdf: Path):
    """Coords must survive the pipeline (prior vision path zeroed them)."""
    result = import_pdf(str(single_page_pdf))
    router = next(n for n in result["nodes"] if n["label"] == "core-router")
    # reportlab drew the router rect at (100, 600, 120, 50)
    assert router["x"] == 100, f"x not preserved: {router}"
    assert router["width"] == 120
    assert router["height"] == 50


def test_vector_multi_page_traversal(multi_page_pdf: Path):
    """All pages processed; page 2 nodes carry a ``page`` field."""
    result = import_pdf(str(multi_page_pdf))
    assert result["_pages"] == 2
    labels = sorted(n["label"] for n in result["nodes"])
    assert labels == [
        "site-a-router", "site-a-switch",
        "site-b-router", "site-b-switch",
    ], labels
    p2_nodes = [n for n in result["nodes"] if n.get("page") == 1]
    assert len(p2_nodes) == 2, f"page-2 tagging missing: {result['nodes']}"
    # One edge per page
    assert len(result["edges"]) == 2


def test_vector_page_scoped_ids_prevent_collision(multi_page_pdf: Path):
    """Page-scoped IDs keep same-position shapes on different pages unique."""
    result = import_pdf(str(multi_page_pdf))
    ids = [n["id"] for n in result["nodes"]]
    assert len(ids) == len(set(ids)), f"id collision across pages: {ids}"


def test_vector_returns_empty_on_text_only_pdf(empty_pdf: Path):
    """No rects → empty graph; caller falls back to vision/OCR."""
    result = import_pdf(str(empty_pdf))
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["_pages"] == 1  # page was traversed, just had nothing


def test_vector_structured_errors_on_bad_pdf(tmp_path: Path):
    """Not a PDF → structured error, not a silent empty result."""
    bad = tmp_path / "not.pdf"
    bad.write_bytes(b"not a real pdf")
    result = import_pdf(str(bad))
    assert result["nodes"] == []
    assert result["_errors"], "expected error list"


def test_rasterize_renders_all_pages(multi_page_pdf: Path):
    """pypdfium2/pdf2image must render every page, not just page 1."""
    pytest.importorskip("pypdfium2")
    images = rasterize_pdf_pages(str(multi_page_pdf), dpi=100)
    assert len(images) == 2, f"expected 2 page PNGs, got {images}"
    for png in images:
        assert png.exists()
        assert png.stat().st_size > 0


def test_rasterize_respects_max_pages(multi_page_pdf: Path):
    pytest.importorskip("pypdfium2")
    images = rasterize_pdf_pages(str(multi_page_pdf), dpi=100, max_pages=1)
    assert len(images) == 1


def test_rasterize_dpi_affects_image_size(single_page_pdf: Path):
    """Higher DPI → larger image (sanity check for the DPI knob)."""
    pytest.importorskip("pypdfium2")
    from PIL import Image
    low = rasterize_pdf_pages(str(single_page_pdf), dpi=72)
    high = rasterize_pdf_pages(str(single_page_pdf), dpi=200)
    assert low and high
    lw = Image.open(low[0]).size[0]
    hw = Image.open(high[0]).size[0]
    assert hw > lw, f"dpi knob ineffective: 72→{lw}, 200→{hw}"
