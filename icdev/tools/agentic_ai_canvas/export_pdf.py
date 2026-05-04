# CUI // SP-CTI
"""PDF export for Agentic AI Design Canvas.

Generates a letter-size PDF with:
  - Page 1: title + metadata (design_id, timestamp, node/edge counts)
  - Page 2: nodes table (id, label, type, description)
  - Page 3+: edges table (source label → target label, relationship)

CUI classification header/footer appears on every page via classification_manager.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

_PAGE_W, _PAGE_H = letter
_MARGIN = 0.75 * inch
_BAND_H = 0.28 * inch


def _banner_text(classification: str) -> str:
    try:
        from tools.compliance.classification_manager import get_marking_banner
        raw = get_marking_banner(classification)
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip() and not ln.startswith("/")]
        return lines[0] if lines else "CUI // SP-CTI"
    except Exception:
        return "CUI // SP-CTI"


def _make_page_template(doc: BaseDocTemplate, classification: str) -> PageTemplate:
    banner = _banner_text(classification)

    def _draw(canvas, doc):
        canvas.saveState()
        # Header band
        canvas.setFillColor(colors.HexColor("#1a1a2e"))
        canvas.rect(0, _PAGE_H - _BAND_H, _PAGE_W, _BAND_H, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#e94560"))
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(_PAGE_W / 2, _PAGE_H - _BAND_H + 0.07 * inch, banner)
        # Footer band
        canvas.setFillColor(colors.HexColor("#1a1a2e"))
        canvas.rect(0, 0, _PAGE_W, _BAND_H, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#e94560"))
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawCentredString(_PAGE_W / 2, 0.09 * inch, banner)
        # Page number
        canvas.setFillColor(colors.HexColor("#7a8cb0"))
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(_PAGE_W - _MARGIN, 0.09 * inch, f"Page {doc.page}")
        canvas.restoreState()

    frame = Frame(
        _MARGIN,
        _BAND_H + 0.1 * inch,
        _PAGE_W - 2 * _MARGIN,
        _PAGE_H - 2 * _BAND_H - 0.2 * inch,
        leftPadding=0,
        bottomPadding=0,
        rightPadding=0,
        topPadding=0,
    )
    return PageTemplate(id="main", frames=[frame], onPage=_draw)


def generate_pdf(
    design_id: str,
    nodes: list,
    edges: list,
    classification: str = "CUI",
) -> bytes:
    """Generate a letter-size PDF export of a canvas design.

    Args:
        design_id: Design identifier.
        nodes: Node dicts with keys id, label, type, description.
        edges: Edge dicts with keys source, target, and optionally label/relationship.
        classification: Classification level for header/footer markings.

    Returns:
        Raw PDF bytes.
    """
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_BAND_H + 0.2 * inch,
        bottomMargin=_BAND_H + 0.2 * inch,
    )
    doc.addPageTemplates([_make_page_template(doc, classification)])

    styles = getSampleStyleSheet()
    title_s = ParagraphStyle(
        "AadcTitle",
        parent=styles["Title"],
        fontSize=20,
        textColor=colors.HexColor("#eaeaea"),
        spaceAfter=16,
    )
    h2_s = ParagraphStyle(
        "AadcH2",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#6366f1"),
        spaceBefore=14,
        spaceAfter=8,
    )
    body_s = ParagraphStyle(
        "AadcBody",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#cbd5e1"),
        spaceAfter=2,
        leading=11,
    )

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story = []

    # ── Page 1: Title + Metadata ──────────────────────────────────────────────
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("Agentic AI Design Canvas", title_s))
    story.append(Paragraph("Export Report", h2_s))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a6e"), spaceAfter=18))

    meta_rows = [
        ["Design ID",      design_id],
        ["Generated",      ts_now],
        ["Classification", classification],
        ["Node Count",     str(len(nodes))],
        ["Edge Count",     str(len(edges))],
    ]
    meta_table = Table(meta_rows, colWidths=[1.6 * inch, 4.5 * inch])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, -1), colors.HexColor("#0f172a")),
        ("BACKGROUND",    (1, 0), (1, -1), colors.HexColor("#16213e")),
        ("TEXTCOLOR",     (0, 0), (0, -1), colors.HexColor("#94a3b8")),
        ("TEXTCOLOR",     (1, 0), (1, -1), colors.HexColor("#eaeaea")),
        ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",      (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#1e3a6e")),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(PageBreak())

    # ── Page 2: Nodes Table ───────────────────────────────────────────────────
    story.append(Paragraph("Nodes", h2_s))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a6e"), spaceAfter=8))

    if nodes:
        header_row = [
            Paragraph("<b>ID</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
            Paragraph("<b>Label</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
            Paragraph("<b>Type</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
            Paragraph("<b>Description</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
        ]
        node_rows = [header_row] + [
            [
                Paragraph(str(n.get("id", "")), body_s),
                Paragraph(str(n.get("label", "")), body_s),
                Paragraph(str(n.get("type", "")), body_s),
                Paragraph(str(n.get("description", "")), body_s),
            ]
            for n in nodes
        ]
        node_table = Table(node_rows, colWidths=[1.0 * inch, 1.5 * inch, 1.4 * inch, 3.2 * inch], repeatRows=1)
        _apply_data_style(node_table)
        story.append(node_table)
    else:
        story.append(Paragraph("No nodes in this design.", body_s))

    story.append(PageBreak())

    # ── Page 3+: Edges Table ──────────────────────────────────────────────────
    story.append(Paragraph("Edges", h2_s))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e3a6e"), spaceAfter=8))

    if edges:
        label_map = {n.get("id", ""): n.get("label", n.get("id", "")) for n in nodes}
        header_row = [
            Paragraph("<b>Source</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
            Paragraph("<b>Target</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
            Paragraph("<b>Relationship</b>", ParagraphStyle("th", parent=body_s, textColor=colors.HexColor("#7ab3f0"))),
        ]
        edge_rows = [header_row] + [
            [
                Paragraph(label_map.get(e.get("source", ""), e.get("source", "")), body_s),
                Paragraph(label_map.get(e.get("target", ""), e.get("target", "")), body_s),
                Paragraph(str(e.get("label", e.get("relationship", ""))), body_s),
            ]
            for e in edges
        ]
        edge_table = Table(edge_rows, colWidths=[2.4 * inch, 2.4 * inch, 2.3 * inch], repeatRows=1)
        _apply_data_style(edge_table)
        story.append(edge_table)
    else:
        story.append(Paragraph("No edges in this design.", body_s))

    doc.build(story)
    return buf.getvalue()


def _apply_data_style(table: Table) -> None:
    table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#0f3460")),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#1e3a6e")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#0f172a"), colors.HexColor("#16213e")]),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
