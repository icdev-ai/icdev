# [CUI // SP-CTI]
"""ICDEV™ Network Diagram PDF Export.

Generates multi-page PDF reports for network migration phase diagrams.
Uses fpdf2 (pure Python, air-gap compatible). Falls back to HTML if unavailable.

Output pages:
  1. Cover: CUI banner, topology name, phase label, export timestamp
  2. Diagram: SVG rendered as vector shapes (simplified — boxes + lines)
  3. Info Boxes: all critical network engineer data tables
  4. Device Inventory: full node table
  5. Port Mapping: phase-specific (if phase_meta provided)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any

logger = get_logger(__name__)

try:  # fpdf2 is an optional dependency (air-gap installs may lack it)
    from fpdf.enums import XPos, YPos
except ImportError:  # pragma: no cover - resolved lazily where fpdf2 is present
    XPos = YPos = None  # type: ignore[assignment]

_CLASSIFICATION = "CUI // SP-CTI"
_BRAND = "ICDEV Network Migration Phases"  # TM stripped — fpdf2 core fonts are latin-1 only


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


_UNICODE_MAP = str.maketrans({
    "—": "-",   # em dash
    "–": "-",   # en dash
    "→": "->",  # →
    "←": "<-",  # ←
    "≥": ">=",  # ≥
    "≤": "<=",  # ≤
    "°": "deg", # °
    "•": "*",   # •
    "’": "'",   # right single quote
    "‘": "'",   # left single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "™": "(TM)",# ™
    "®": "(R)", # ®
    "©": "(c)", # ©
    "×": "x",   # ×
    "÷": "/",   # ÷
})


def _safe(text: str) -> str:
    """Sanitize text to latin-1 safe for fpdf2 core fonts."""
    text = str(text).translate(_UNICODE_MAP)
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Public API ─────────────────────────────────────────────────────────────────

# Classification banner color map (R, G, B) tuples for background
_CLS_COLORS: dict[str, tuple[int, int, int]] = {
    "PUBLIC":          (13, 59, 30),
    "UNCLASSIFIED":    (13, 59, 30),
    "CUI":             (13, 43, 78),
    "SECRET":          (62, 26, 0),
    "TOP SECRET":      (59, 10, 10),
    "TOP SECRET//SCI": (42, 10, 59),
}


def export_to_pdf(
    content: str,
    output_path: str,
    title: str = "Document",
    classification: str = "CUI",
) -> None:
    """Export plain text *content* to a classified PDF document at *output_path*.

    Adds classification banners at the top and bottom of every page.
    Falls back to an HTML file if fpdf2 is unavailable.
    """
    import pathlib

    cls_upper = (classification or "CUI").upper()
    bg_r, bg_g, bg_b = _CLS_COLORS.get(cls_upper, _CLS_COLORS["CUI"])

    try:
        from fpdf import FPDF

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.set_margins(15, 15, 15)
        pdf.add_page()

        # ── Top banner ────────────────────────────────────────────────────────
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(bg_r, bg_g, bg_b)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 6, f"  {_safe(classification)}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(4)

        # ── Title ─────────────────────────────────────────────────────────────
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

        # ── Body ──────────────────────────────────────────────────────────────
        pdf.set_font("Helvetica", "", 10)
        for line in content.splitlines():
            pdf.multi_cell(0, 5, _safe(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        # ── Bottom banner on every page ───────────────────────────────────────
        total = pdf.page
        for pno in range(1, total + 1):
            pdf.page = pno
            pdf.set_y(-12)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_fill_color(bg_r, bg_g, bg_b)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 5, f"  {_safe(classification)} | Page {pno} of {total}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        pdf.page = total

        pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        pdf.output(output_path)
        logger.info("IDR PDF export: path=%s classification=%s", output_path, classification)

    except ImportError:
        logger.warning("fpdf2 not installed — writing HTML fallback for IDR PDF: %s", output_path)
        html_path = str(output_path).replace(".pdf", ".html")
        pathlib.Path(html_path).write_text(
            f"<!DOCTYPE html><html><head><title>{title}</title></head>"
            f"<body><h2>{classification}</h2><h1>{title}</h1>"
            f"<pre>{content}</pre><h2>{classification}</h2></body></html>",
            encoding="utf-8",
        )


def export_phase_pdf(
    topo_name: str,
    phase_label: str,
    graph: dict,
    infoboxes: list[dict],
    consolidation: dict | None = None,
    phase_meta: dict | None = None,
) -> bytes:
    """Generate a PDF report for one phase panel.

    Returns PDF bytes. Falls back to HTML bytes if fpdf2 is unavailable.
    """
    try:
        from fpdf import FPDF
        return _build_pdf_fpdf(topo_name, phase_label, graph, infoboxes, consolidation, phase_meta, FPDF)
    except ImportError:
        logger.warning("fpdf2 not installed — generating HTML fallback for PDF")
        return _build_html_fallback(topo_name, phase_label, graph, infoboxes, consolidation, phase_meta)


# ── fpdf2 builder ──────────────────────────────────────────────────────────────

def _build_pdf_fpdf(
    topo_name: str,
    phase_label: str,
    graph: dict,
    infoboxes: list[dict],
    consolidation: dict | None,
    phase_meta: dict | None,
    FPDF: Any,
) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)

    # ── Page 1: Cover ─────────────────────────────────────────────────────────
    pdf.add_page()
    _cui_banner(pdf)
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 110, 181)
    pdf.cell(0, 12, _safe(_BRAND), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(0, 10, _safe(topo_name), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, _safe(phase_label), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, f"Generated: {_now_str()}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.cell(0, 6, "Classification: CUI // SP-CTI | Distribution: Authorized Recipients Only", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    # Summary stats on cover
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    pdf.ln(10)
    _summary_table(pdf, [
        ("Devices", str(len(nodes))),
        ("Links", str(len(edges))),
        ("Phase", _safe(phase_label)),
        ("Exported", _now_str()),
    ])

    # ── Page 2: Topology Diagram ───────────────────────────────────────────────
    if nodes:
        pdf.add_page()
        _cui_banner(pdf)
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 110, 181)
        pdf.cell(0, 8, _safe(f"Network Topology Diagram - {phase_label}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        _draw_topology(pdf, graph)

    # ── Pages 3+: Info Boxes ──────────────────────────────────────────────────
    if infoboxes:
        _add_infobox_pages(pdf, infoboxes, phase_label)

    # ── Last Page: Device Inventory ───────────────────────────────────────────
    if nodes:
        _add_device_inventory_page(pdf, nodes, topo_name)

    # ── Port Mapping Page (if phase) ──────────────────────────────────────────
    if phase_meta and phase_meta.get("port_mappings"):
        _add_port_mapping_page(pdf, phase_meta)

    # ── Consolidation Page (if final) ─────────────────────────────────────────
    if consolidation:
        _add_consolidation_page(pdf, consolidation)

    _cui_footer_all(pdf)
    return bytes(pdf.output())


def _cui_banner(pdf: Any) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(180, 30, 30)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 5, f"  {_CLASSIFICATION}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.set_text_color(0, 0, 0)


def _cui_footer_all(pdf: Any) -> None:
    """Add CUI footer to all pages."""
    total = pdf.page
    for pno in range(1, total + 1):
        pdf.page = pno
        pdf.set_y(-12)
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_fill_color(180, 30, 30)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 4, f"  {_CLASSIFICATION} | Page {pno} of {total} | {_BRAND}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
    pdf.page = total


def _summary_table(pdf: Any, rows: list[tuple[str, str]]) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 240, 255)
    w1, w2 = 60, 120
    for label, value in rows:
        pdf.set_fill_color(240, 244, 250)
        pdf.cell(w1, 7, label, border=1, fill=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(w2, 7, value, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "B", 10)


def _draw_topology(pdf: Any, graph: dict) -> None:
    """Render a simplified topology diagram as vector shapes."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return

    # Normalize positions to fit within 240x140mm (landscape A4 minus margins)
    max_x = max((n.get("x", 0) for n in nodes), default=400) or 400
    max_y = max((n.get("y", 0) for n in nodes), default=300) or 300
    area_w, area_h = 240, 130
    scale_x = area_w / (max_x + 120)
    scale_y = area_h / (max_y + 80)
    scale = min(scale_x, scale_y, 1.0)
    off_x, off_y = pdf.get_x(), pdf.get_y() + 2

    # Phase state colors
    _state_colors = {
        "new":      (39, 174, 96),
        "retiring": (169, 50, 38),
        "changing": (230, 126, 34),
        "existing": (30, 110, 181),
        None:       (30, 110, 181),
    }

    # Draw edges first (so nodes appear on top)
    pos = {n["id"]: (off_x + n.get("x", 0) * scale + 10, off_y + n.get("y", 0) * scale + 4) for n in nodes}
    pdf.set_draw_color(100, 140, 180)
    pdf.set_line_width(0.3)
    for e in edges[:80]:  # cap at 80 edges for readability
        src = pos.get(e.get("source"))
        tgt = pos.get(e.get("target"))
        if src and tgt:
            pdf.line(src[0] + 6, src[1] + 3, tgt[0] + 6, tgt[1] + 3)

    # Draw nodes
    nw, nh = 20, 7
    for n in nodes[:80]:  # cap at 80 nodes
        state = n.get("phase_state")
        color = _state_colors.get(state, (30, 110, 181))
        nx = off_x + n.get("x", 0) * scale
        ny = off_y + n.get("y", 0) * scale
        pdf.set_fill_color(*color)
        pdf.set_draw_color(255, 255, 255)
        pdf.set_line_width(0.2)
        pdf.rect(nx, ny, nw, nh, "FD")
        pdf.set_font("Helvetica", "", 5)
        pdf.set_text_color(255, 255, 255)
        label = _safe((n.get("label") or n.get("id") or "")[:12])
        pdf.set_xy(nx + 1, ny + 1.5)
        pdf.cell(nw - 2, 4, label, align="C")

    # Legend
    pdf.set_text_color(50, 50, 50)
    lx = off_x + area_w - 50
    ly = off_y
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(lx, ly)
    pdf.cell(50, 5, "Legend:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for state, color, label in [
        ("existing", (30, 110, 181), "Existing"),
        ("new", (39, 174, 96), "New"),
        ("changing", (230, 126, 34), "Changing"),
        ("retiring", (169, 50, 38), "Retiring"),
    ]:
        pdf.set_xy(lx, pdf.get_y())
        pdf.set_fill_color(*color)
        pdf.rect(lx, pdf.get_y(), 5, 3.5, "F")
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(50, 50, 50)
        pdf.set_xy(lx + 6, pdf.get_y())
        pdf.cell(44, 3.5, label, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(off_y + area_h + 5)


def _add_infobox_pages(pdf: Any, infoboxes: list[dict], phase_label: str) -> None:
    """Render info boxes as formatted tables — 4 per page (2x2 grid)."""
    per_page = 4
    for i in range(0, len(infoboxes), per_page):
        pdf.add_page()
        _cui_banner(pdf)
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 110, 181)
        pdf.cell(0, 7, _safe(f"Critical Info Boxes - {phase_label}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

        chunk = infoboxes[i: i + per_page]
        # Two columns
        col_w = 130
        row_start_y = pdf.get_y()
        for idx, box in enumerate(chunk):
            col = idx % 2
            row_offset = (idx // 2) * 70
            pdf.set_xy(15 + col * (col_w + 5), row_start_y + row_offset)
            _render_infobox(pdf, box, col_w)


def _render_infobox(pdf: Any, box: dict, width: float) -> None:
    """Render a single info box as a bordered table."""
    # Header
    title = box.get("title", "")
    _hex = box.get("color", "#1e6eb5")
    r, g, b = _hex_to_rgb(_hex)
    pdf.set_fill_color(r, g, b)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(width, 6, _safe(f"  {title}"), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)

    # Rows
    pdf.set_font("Helvetica", "", 8)
    for row in box.get("rows", []):
        label = _safe(str(row.get("label", "")))
        value = _safe(str(row.get("value", "")))
        status = row.get("status", "")
        if status == "critical":
            vc = (180, 30, 30)
        elif status == "warn":
            vc = (200, 120, 0)
        elif status == "ok":
            vc = (39, 130, 70)
        else:
            vc = (50, 50, 50)

        pdf.set_fill_color(245, 248, 252)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(width * 0.55, 5, f"  {label}", border="LRB", fill=True)
        pdf.set_text_color(*vc)
        pdf.set_font("Helvetica", "B" if status else "", 8)
        pdf.cell(width * 0.45, 5, value, border="RB", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(80, 80, 80)

    pdf.ln(2)


def _add_device_inventory_page(pdf: Any, nodes: list[dict], topo_name: str) -> None:
    pdf.add_page()
    _cui_banner(pdf)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 110, 181)
    pdf.cell(0, 7, _safe(f"Device Inventory - {topo_name}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    headers = ["Device Name", "Type", "Vendor", "Model", "Mgmt IP", "EOL", "Rack U"]
    widths = [50, 30, 28, 35, 32, 18, 15]

    # Header row
    pdf.set_fill_color(30, 110, 181)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    # Data rows
    pdf.set_font("Helvetica", "", 7)
    for i, n in enumerate(nodes[:80]):
        meta = n.get("meta") or {}
        fill = i % 2 == 0
        pdf.set_fill_color(245, 248, 252) if fill else pdf.set_fill_color(255, 255, 255)
        pdf.set_text_color(50, 50, 50)
        eol = "YES" if _is_eol_node(n) else ""
        if eol:
            pdf.set_text_color(180, 30, 30)
        row = [
            (n.get("label") or n.get("id") or "")[:20],
            (n.get("type") or "")[:14],
            _vendor_node(n)[:12],
            (n.get("model") or meta.get("model") or "")[:16],
            (n.get("ip") or n.get("mgmt_ip") or meta.get("mgmt_ip") or "")[:16],
            eol,
            str(_rack_units_node(n)),
        ]
        for val, w in zip(row, widths):
            pdf.cell(w, 5, _safe(val), border=1, fill=fill)
        pdf.ln()
        pdf.set_text_color(50, 50, 50)

    if len(nodes) > 80:
        pdf.set_font("Helvetica", "I", 7)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, f"  ... and {len(nodes) - 80} more devices", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _add_port_mapping_page(pdf: Any, phase_meta: dict) -> None:
    pdf.add_page()
    _cui_banner(pdf)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 110, 181)
    pdf.cell(0, 7, _safe(f"Port Mapping - Phase {phase_meta.get('phase_num', '?')}: {phase_meta.get('title', '')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pm = phase_meta.get("port_mappings") or []
    headers = ["Source Device", "Source Port", "Target Device", "Target Port", "Speed", "Media", "Status"]
    widths = [45, 35, 45, 35, 22, 22, 22]

    pdf.set_fill_color(30, 110, 181)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, h, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    for i, m in enumerate(pm[:60]):
        fill = i % 2 == 0
        pdf.set_fill_color(245, 248, 252) if fill else pdf.set_fill_color(255, 255, 255)
        status = "CONFLICT" if m.get("conflict") else ("UNMAPPED" if not m.get("target_port") else "OK")
        if status == "CONFLICT":
            pdf.set_text_color(180, 30, 30)
        elif status == "UNMAPPED":
            pdf.set_text_color(180, 100, 0)
        else:
            pdf.set_text_color(50, 50, 50)
        row = [
            str(m.get("source_device") or "")[:20],
            str(m.get("source_port") or "")[:16],
            str(m.get("target_device") or "")[:20],
            _safe(str(m.get("target_port") or "n/a"))[:16],
            str(m.get("speed") or "")[:10],
            str(m.get("media_type") or "")[:10],
            status,
        ]
        for val, w in zip(row, widths):
            pdf.cell(w, 5, _safe(val), border=1, fill=fill)
        pdf.ln()
        pdf.set_text_color(50, 50, 50)


def _add_consolidation_page(pdf: Any, c: dict) -> None:
    pdf.add_page()
    _cui_banner(pdf)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(30, 110, 181)
    pdf.cell(0, 7, "Consolidation & Optimization Analysis - Final/To-Be State", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    sections = [
        ("Infrastructure Savings", [
            ("Devices Removed", str(c.get("devices_removed", 0))),
            ("Rack Units Freed", f"{c.get('rack_units_freed', 0)} U"),
            ("Power Reduction", f"{c.get('power_saved_watts', 0)} W"),
            ("Annual Power kWh Saved", f"{c.get('power_saved_kwh_yr', 0):,.0f} kWh"),
        ]),
        ("Financial Impact (3-Year)", [
            ("CapEx Delta", f"${c.get('capex_delta', 0):+,.0f}"),
            ("Annual OpEx Delta", f"${c.get('opex_annual_delta', 0):+,.0f}/yr"),
            ("Power Cost Savings", f"${c.get('power_cost_savings_yr', 0):,.0f}/yr"),
            ("Maintenance Savings", f"${c.get('maintenance_savings_yr', 0):,.0f}/yr"),
            ("3-Year TCO Delta", f"${c.get('tco_3yr_delta', 0):+,.0f}"),
        ]),
        ("Performance Improvements", [
            ("Bandwidth Increase", f"+{c.get('bw_increase_pct', 0):.0f}%"),
            ("SPOFs Before → After", f"{c.get('spof_before', 0)} → {c.get('spof_after', 0)}"),
            ("New HA Pairs", f"+{c.get('new_ha_pairs', 0)}"),
            ("Latency Change", str(c.get("latency_change", "Neutral"))),
        ]),
        ("Compliance Improvements", [
            ("STIG Before → After", f"{c.get('stig_before', 0):.0f}% → {c.get('stig_after', 0):.0f}%"),
            ("EOL Devices Before → After", f"{c.get('eol_before', 0)} → {c.get('eol_after', 0)}"),
            ("Controls Added", str(c.get("controls_added", 0))),
            ("Vendor Consolidation", str(c.get("vendor_reduction", "—"))),
        ]),
    ]

    col_w = 130
    for s_idx, (sec_title, rows) in enumerate(sections):
        col = s_idx % 2
        if col == 0 and s_idx > 0:
            pdf.ln(4)
        # Simplified: just render sequentially
        pdf.set_x(15)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(30, 110, 181)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(col_w * 2 // len(sections) + 10, 6, _safe(f"  {sec_title}"), border=0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=True)
        for label, value in rows:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(60, 60, 60)
            pdf.set_fill_color(245, 248, 252)
            pdf.cell(col_w * 0.55, 5, _safe(f"  {label}"), border=1, fill=True)
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(20, 100, 40)
            pdf.cell(col_w * 0.45, 5, _safe(value), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)


# ── HTML Fallback ──────────────────────────────────────────────────────────────

def _build_html_fallback(
    topo_name: str,
    phase_label: str,
    graph: dict,
    infoboxes: list[dict],
    consolidation: dict | None,
    phase_meta: dict | None,
) -> bytes:
    """Generate a print-ready HTML report as a fallback when fpdf2 is unavailable."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    box_html = ""
    for box in infoboxes:
        rows_html = ""
        for row in box.get("rows", []):
            sc = {"critical": "#c0392b", "warn": "#e67e22", "ok": "#27ae60", "info": "#2980b9"}.get(row.get("status"), "#333")
            rows_html += f"""<tr>
              <td style="padding:3px 8px;border:1px solid #ddd;color:#555">{row.get('label','')}</td>
              <td style="padding:3px 8px;border:1px solid #ddd;color:{sc};font-weight:{'bold' if row.get('status') else 'normal'}">{row.get('value','')}</td>
            </tr>"""
        box_html += f"""
        <div style="break-inside:avoid;margin:8px;border:1px solid #ddd;border-radius:6px;overflow:hidden;min-width:280px;flex:1">
          <div style="background:{box.get('color','#1e6eb5')};color:#fff;padding:6px 12px;font-weight:bold;font-size:13px">{box.get('icon','')} {box.get('title','')}</div>
          <table style="width:100%;border-collapse:collapse;font-size:11px">{rows_html}</table>
        </div>"""

    dev_rows = ""
    for n in nodes[:60]:
        meta = n.get("meta") or {}
        eol = "⚠ EOL" if _is_eol_node(n) else ""
        dev_rows += f"""<tr style="background:{'#f5f8fc' if nodes.index(n)%2==0 else '#fff'}">
          <td style="padding:3px 6px;border:1px solid #eee">{n.get('label',n.get('id',''))[:30]}</td>
          <td style="padding:3px 6px;border:1px solid #eee">{n.get('type','')}</td>
          <td style="padding:3px 6px;border:1px solid #eee">{_vendor_node(n)}</td>
          <td style="padding:3px 6px;border:1px solid #eee">{n.get('ip') or meta.get('mgmt_ip','')}</td>
          <td style="padding:3px 6px;border:1px solid #eee;color:{'#c0392b' if eol else ''}">{eol}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>{_CLASSIFICATION} — {topo_name} — {phase_label}</title>
<style>
  @media print {{ body {{ margin:0 }} .no-print {{ display:none }} }}
  body {{ font-family:Helvetica,Arial,sans-serif; margin:0; background:#fff }}
  .cui {{ background:#b41e1e;color:#fff;padding:4px 12px;font-size:11px;font-weight:bold }}
  .cover {{ padding:40px;text-align:center }}
  h1 {{ color:#1e6eb5;font-size:24px }}
  h2 {{ color:#1e6eb5;font-size:16px;margin-top:24px;border-bottom:2px solid #1e6eb5;padding-bottom:4px }}
  .boxes {{ display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 }}
  table.inv {{ width:100%;border-collapse:collapse;font-size:11px;margin-top:8px }}
  table.inv th {{ background:#1e6eb5;color:#fff;padding:4px 8px;text-align:left }}
  .footer {{ text-align:center;font-size:9px;color:#999;margin:24px 0 }}
</style></head>
<body>
<div class="cui">&nbsp;{_CLASSIFICATION}&nbsp;</div>
<div class="cover">
  <h1>{_BRAND}</h1>
  <h2 style="border:none">{topo_name}</h2>
  <p style="font-size:16px;color:#555">{phase_label}</p>
  <p style="color:#999;font-size:11px">Generated: {_now_str()} &nbsp;|&nbsp; CUI // SP-CTI &nbsp;|&nbsp; Authorized Recipients Only</p>
  <table style="margin:16px auto;border-collapse:collapse;font-size:12px">
    <tr><td style="padding:4px 16px;border:1px solid #ddd;font-weight:bold">Devices</td><td style="padding:4px 16px;border:1px solid #ddd">{len(nodes)}</td></tr>
    <tr><td style="padding:4px 16px;border:1px solid #ddd;font-weight:bold">Links</td><td style="padding:4px 16px;border:1px solid #ddd">{len(edges)}</td></tr>
  </table>
</div>
<div style="padding:20px 32px">
  <h2>Critical Info Boxes</h2>
  <div class="boxes">{box_html}</div>
  <h2>Device Inventory</h2>
  <table class="inv">
    <tr><th>Device Name</th><th>Type</th><th>Vendor</th><th>Mgmt IP</th><th>EOL</th></tr>
    {dev_rows}
  </table>
</div>
<div class="cui" style="margin-top:24px">&nbsp;{_CLASSIFICATION}&nbsp;</div>
<div class="footer">Print this page to PDF via browser for best results &nbsp;|&nbsp; {_BRAND}</div>
</body></html>"""
    return html.encode("utf-8")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        return 30, 110, 181


def _is_eol_node(node: dict) -> bool:
    from tools.network.migration_phases import _is_eol
    return _is_eol(node)


def _vendor_node(node: dict) -> str:
    from tools.network.migration_phases import _vendor
    return _vendor(node)


def _rack_units_node(node: dict) -> int:
    from tools.network.migration_phases import _rack_units
    return _rack_units(node)
