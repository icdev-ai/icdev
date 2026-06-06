# CUI // SP-CTI
"""Deterministic PDF export for the Presentation Studio (H6).

Renders a deck to a landscape 16:9 PDF with reportlab, mirroring the WYSIWYG
element model used by the editor and PPTX builder. Charts/diagrams are rasterized
via the viz kernel's matplotlib renderers (air-gap, no browser); text/shapes/kpis/
tables/images draw natively. Falls back to a title+bullets layout for non-freeform
slides.
"""
from __future__ import annotations

import base64
import io
import os
import tempfile

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as _canvas

from tools.viz.palette import get_palette

# 16:9 slide in points (1in = 72pt) → 13.33in x 7.5in
PAGE_W = 13.333 * 72
PAGE_H = 7.5 * 72


def _hex(c: str, default: str = "#FFFFFF") -> str:
    c = (c or "").strip()
    return c if (c.startswith("#") and len(c) in (4, 7)) else default


def _box(el: dict) -> tuple[float, float, float, float]:
    """Fractional (x,y top-left, w,h) → reportlab (x, y_bottom, w, h) in points."""
    x = float(el.get("x", 0)); y = float(el.get("y", 0))
    w = float(el.get("w", 0.3)); h = float(el.get("h", 0.2))
    return x * PAGE_W, PAGE_H - (y + h) * PAGE_H, w * PAGE_W, h * PAGE_H


def _wrap(c, text: str, font: str, size: float, max_w: float) -> list[str]:
    out: list[str] = []
    for raw in str(text).split("\n"):
        words, line = raw.split(" "), ""
        for wd in words:
            trial = (line + " " + wd).strip()
            if c.stringWidth(trial, font, size) <= max_w or not line:
                line = trial
            else:
                out.append(line); line = wd
        out.append(line)
    return out


def _draw_text(c, el: dict):
    x, yb, w, h = _box(el)
    st = el.get("style", {}) or {}
    size = max(7, float(st.get("fontSize", 18)) * 0.75)
    font = "Helvetica-Bold" if st.get("bold") else "Helvetica"
    c.setFillColor(_hex(st.get("color", "#FFFFFF")))
    raw = (el.get("payload", {}) or {}).get("text", "")
    listkind = st.get("list", "none")
    lines: list[str] = []
    for i, ln in enumerate(str(raw).split("\n")):
        prefix = "• " if listkind == "bullet" else (f"{i + 1}. " if listkind == "number" else "")
        lines += _wrap(c, prefix + ln, font, size, w)
    ty = yb + h - size
    for ln in lines:
        if ty < yb - size:
            break
        if st.get("align") == "center":
            c.drawCentredString(x + w / 2, ty, ln)
        elif st.get("align") == "right":
            c.drawRightString(x + w, ty, ln)
        else:
            c.drawString(x, ty, ln)
        ty -= size * 1.3


def _draw_shape(c, el: dict):
    x, yb, w, h = _box(el)
    st = el.get("style", {}) or {}
    kind = (el.get("payload", {}) or {}).get("shape", "rectangle")
    c.saveState()
    try:
        op = float(st.get("opacity", 1) or 1)
        c.setFillAlpha(op); c.setStrokeAlpha(op)
    except Exception:
        pass
    c.setFillColor(_hex(st.get("fill", "#C8A951")))
    sw = float(st.get("strokeWidth", 0) or 0)
    stroke = 0
    if sw > 0 and st.get("stroke") and st.get("stroke") != "transparent":
        c.setStrokeColor(_hex(st.get("stroke"))); c.setLineWidth(sw); stroke = 1
    if kind == "ellipse":
        c.ellipse(x, yb, x + w, yb + h, stroke=stroke, fill=1)
    elif kind in ("line", "arrow"):
        c.setStrokeColor(_hex(st.get("fill", "#C8A951"))); c.setLineWidth(max(1, sw or 2))
        c.line(x, yb + h / 2, x + w, yb + h / 2)
    else:
        rad = min(float(st.get("cornerRadius", 0) or 0), min(w, h) / 2)
        if rad:
            c.roundRect(x, yb, w, h, rad, stroke=stroke, fill=1)
        else:
            c.rect(x, yb, w, h, stroke=stroke, fill=1)
    c.restoreState()


def _draw_image_png(c, png_path_or_bytes, x, yb, w, h):
    try:
        img = ImageReader(io.BytesIO(png_path_or_bytes) if isinstance(png_path_or_bytes, bytes) else png_path_or_bytes)
        iw, ih = img.getSize()
        scale = min(w / iw, h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(img, x + (w - dw) / 2, yb + (h - dh) / 2, dw, dh, mask="auto")
        return True
    except Exception:
        return False


def _draw_chart(c, el: dict, theme: str):
    x, yb, w, h = _box(el)
    try:
        from tools.viz.spec import ChartSpec
        from tools.viz.render_png import chart_to_png
        tmp = os.path.join(tempfile.gettempdir(), f"_pdf_chart_{id(el)}.png")
        chart_to_png(ChartSpec.from_dict(el.get("payload", {}) or {}), theme=theme, out_path=tmp)
        _draw_image_png(c, tmp, x, yb, w, h)
    except Exception:
        c.setFillColor("#888888"); c.setFont("Helvetica", 9)
        c.drawString(x, yb + h / 2, "[chart]")


def _draw_diagram(c, el: dict, theme: str):
    x, yb, w, h = _box(el)
    try:
        from tools.viz.spec import DiagramSpec
        from tools.viz.render_png import diagram_to_png
        tmp = os.path.join(tempfile.gettempdir(), f"_pdf_diag_{id(el)}.png")
        diagram_to_png(DiagramSpec.from_dict(el.get("payload", {}) or {}), theme=theme, out_path=tmp)
        _draw_image_png(c, tmp, x, yb, w, h)
    except Exception:
        c.setFillColor("#888888"); c.setFont("Helvetica", 9)
        c.drawString(x, yb + h / 2, "[diagram]")


def _draw_table(c, el: dict, pal):
    x, yb, w, h = _box(el)
    p = el.get("payload", {}) or {}
    cols = p.get("columns") or p.get("headers") or []
    rows = p.get("rows") or []
    n = len(rows) + (1 if cols else 0)
    if n <= 0:
        return
    rh = h / n
    ncol = max(len(cols), max((len(r) for r in rows), default=1))
    cw = w / max(ncol, 1)
    c.setFont("Helvetica", 9)
    ty = yb + h - rh
    if cols:
        c.setFillColor(pal.hex("accent"))
        c.rect(x, ty, w, rh, stroke=0, fill=1)
        c.setFillColor("#0a0f1a"); c.setFont("Helvetica-Bold", 9)
        for j, col in enumerate(cols):
            c.drawString(x + j * cw + 3, ty + rh / 3, str(col)[:24])
        ty -= rh
    c.setFont("Helvetica", 9)
    for r in rows:
        c.setFillColor(pal.hex("text"))
        for j, cell in enumerate(r):
            c.drawString(x + j * cw + 3, ty + rh / 3, str(cell)[:24])
        ty -= rh


def _draw_kpis(c, el: dict, pal):
    x, yb, w, h = _box(el)
    tiles = (el.get("payload", {}) or {}).get("tiles", [])[:4]
    if not tiles:
        return
    gap = 8
    tw = (w - gap * (len(tiles) - 1)) / len(tiles)
    for i, t in enumerate(tiles):
        tx = x + i * (tw + gap)
        c.setFillColor(pal.hex("dark")); c.rect(tx, yb, tw, h, stroke=0, fill=1)
        c.setFillColor(pal.hex("accent")); c.setFont("Helvetica-Bold", 22)
        c.drawCentredString(tx + tw / 2, yb + h * 0.55, str(t.get("value", ""))[:12])
        c.setFillColor(pal.hex("subtext")); c.setFont("Helvetica", 9)
        c.drawCentredString(tx + tw / 2, yb + h * 0.25, str(t.get("label", ""))[:28])


def _draw_image_el(c, el: dict):
    x, yb, w, h = _box(el)
    src = (el.get("payload", {}) or {}).get("src", "")
    try:
        if src.startswith("data:"):
            b64 = src.split(",", 1)[1]
            _draw_image_png(c, base64.b64decode(b64), x, yb, w, h)
        elif src and os.path.exists(src):
            _draw_image_png(c, src, x, yb, w, h)
    except Exception:
        pass


def _draw_element(c, el: dict, theme: str, pal):
    if el.get("hidden"):
        return
    t = el.get("type")
    if t == "text":
        _draw_text(c, el)
    elif t == "shape":
        _draw_shape(c, el)
    elif t == "chart":
        _draw_chart(c, el, theme)
    elif t == "diagram":
        _draw_diagram(c, el, theme)
    elif t == "table":
        _draw_table(c, el, pal)
    elif t == "kpis":
        _draw_kpis(c, el, pal)
    elif t == "image":
        _draw_image_el(c, el)
    # icon/dashboard: web-only — skipped in PDF


def _draw_simple(c, slide: dict, pal):
    """Title + bullets fallback for non-freeform slides."""
    c.setFillColor(pal.hex("accent")); c.setFont("Helvetica-Bold", 30)
    c.drawString(0.6 * 72, PAGE_H - 1.1 * 72, str(slide.get("title", ""))[:80])
    c.setFillColor(pal.hex("text")); c.setFont("Helvetica", 16)
    ty = PAGE_H - 2.0 * 72
    for b in (slide.get("bullets") or [])[:8]:
        for ln in _wrap(c, "•  " + str(b), "Helvetica", 16, PAGE_W - 1.6 * 72):
            c.drawString(0.8 * 72, ty, ln); ty -= 26


def build_pdf(slides: list[dict], title: str = "", theme: str = "midnight_executive") -> bytes:
    """Render slide model dicts to PDF bytes (landscape 16:9)."""
    pal = get_palette(theme)
    buf = io.BytesIO()
    c = _canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    if not slides:
        slides = [{"title": title or "Empty deck", "bullets": []}]
    for slide in slides:
        c.setFillColor(pal.hex("bg")); c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        els = slide.get("elements") or []
        if slide.get("freeform") and els:
            for el in sorted(els, key=lambda e: e.get("z", 0)):
                _draw_element(c, el, theme, pal)
        elif els:
            for el in sorted(els, key=lambda e: e.get("z", 0)):
                _draw_element(c, el, theme, pal)
        else:
            _draw_simple(c, slide, pal)
        c.showPage()
    c.save()
    return buf.getvalue()
