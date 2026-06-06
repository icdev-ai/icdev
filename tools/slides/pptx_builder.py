# CUI // SP-CTI
"""PPTX Builder — assembles slide decks via python-pptx.

Reuses all primitives from tools/presentations/generate_exec_deck.py
(_blank, _bg, _rect, _box, _title, _footer, _gold_bar, _notes, _card).
Extends with multi-theme support and image embedding.

Themes: midnight_executive | govcon_proposal | compliance_briefing
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME
from tools.viz import render_pptx, render_png
from tools.viz.spec import ChartSpec, TableSpec, DiagramSpec, KpiSpec, DashboardSpec
from tools.viz import elements as _elements

_SW_IN = 13.33  # slide width in inches (matches W)
_SH_IN = 7.5    # slide height in inches (matches H)

# ── Canvas dimensions (16:9 widescreen) ──────────────────────────────────────
W  = Inches(13.33)
H  = Inches(7.50)
LM = Inches(0.55)
CW = W - LM * 2

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _ICDEV_ROOT / "tools" / "presentations" / "slides"


def _rgb(palette: dict, key: str) -> RGBColor:
    r, g, b = palette[key]
    return RGBColor(r, g, b)


# ── Primitives (identical to generate_exec_deck.py) ──────────────────────────

def _new_prs() -> Presentation:
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    return prs


def _blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _bg(slide, color: RGBColor) -> None:
    f = slide.background.fill
    f.solid()
    f.fore_color.rgb = color


def _rect(slide, l, t, w, h, fill: RGBColor, line=None, lw=Pt(1)):
    s = slide.shapes.add_shape(1, l, t, w, h)
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    if line:
        s.line.color.rgb = line
        s.line.width = lw
    else:
        s.line.fill.background()
    return s


def _box(
    slide, l, t, w, h, text: str = "", size: int = 14,
    bold: bool = False, italic: bool = False,
    color: RGBColor = None, align=PP_ALIGN.LEFT, wrap: bool = True
):
    if color is None:
        color = RGBColor(0xFF, 0xFF, 0xFF)
    shape = slide.shapes.add_textbox(l, t, w, h)
    tf = shape.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    return tf


def _accent_bar(slide, palette: dict, top=Inches(1.55), h=Inches(0.04)) -> None:
    _rect(slide, LM, top, CW, h, _rgb(palette, "accent"))


def _footer(slide, n: int, palette: dict, text: str = "ICDEV™  ·  A System That Builds Systems") -> None:
    dark = _rgb(palette, "dark")
    _box(slide, LM, H - Inches(0.32), CW - Inches(0.6), Inches(0.28),
         text, size=8, color=dark)
    _box(slide, W - Inches(0.8), H - Inches(0.32), Inches(0.7), Inches(0.28),
         str(n), size=9, color=dark, align=PP_ALIGN.RIGHT)


def _notes(slide, text: str) -> None:
    tf = slide.notes_slide.notes_text_frame
    tf.text = text


def _card(slide, l, t, w, h, heading: str, body: str, palette: dict) -> None:
    dark = _rgb(palette, "dark")
    accent = _rgb(palette, "accent")
    subtext = _rgb(palette, "subtext")
    _rect(slide, l, t, w, h, dark, accent)
    pad = Inches(0.12)
    _box(slide, l + pad, t + pad, w - pad * 2, Inches(0.38),
         heading, size=12, bold=True, color=accent)
    _box(slide, l + pad, t + Inches(0.42), w - pad * 2, h - Inches(0.54),
         body, size=10, color=subtext, wrap=True)


# ── Slide Builders ────────────────────────────────────────────────────────────

def _build_title_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    text_c = _rgb(palette, "text")
    subtext = _rgb(palette, "subtext")

    # Top + bottom accent bars
    _rect(s, 0, 0, W, Inches(0.1), accent)
    _rect(s, 0, H - Inches(0.1), W, Inches(0.1), accent)

    title = slide_data.get("title", "ICDEV™")
    _box(s, LM, Inches(1.0), CW, Inches(2.0),
         title, size=44, bold=True, color=accent, align=PP_ALIGN.CENTER)
    _rect(s, Inches(3.8), Inches(3.2), Inches(5.73), Inches(0.03), accent)

    subtitle = slide_data.get("speaker_notes", "")[:120]
    _box(s, LM, Inches(3.35), CW, Inches(0.5),
         "ICDEV™  ·  A System That Builds Systems", size=14,
         color=text_c, align=PP_ALIGN.CENTER)
    if subtitle:
        _box(s, LM, Inches(3.9), CW, Inches(0.4),
             subtitle[:100], size=12, color=subtext, align=PP_ALIGN.CENTER, italic=True)


def _build_content_slide(
    prs: Presentation, slide_data: dict, n: int, palette: dict, image_path: str | None = None
) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")

    # Left accent stripe
    _rect(s, 0, 0, Inches(0.12), H, accent)

    # Title bar
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)

    # Accent underline
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    bullets = slide_data.get("bullets", [])
    if image_path and Path(image_path).exists():
        # Two-column layout: bullets left, image right
        col_w = CW * 0.55
        _add_bullets(s, bullets, Inches(0.24), Inches(0.85), col_w, palette)
        img_l = LM + col_w + Inches(0.15)
        img_w = CW - col_w - Inches(0.1)
        try:
            s.shapes.add_picture(image_path, img_l, Inches(0.85), img_w, Inches(5.6))
        except Exception:
            pass
    else:
        _add_bullets(s, bullets, Inches(0.24), Inches(0.85), CW, palette)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _add_bullets(slide, bullets: list[str], l, t, w, palette: dict) -> None:
    text_c = _rgb(palette, "text")
    subtext = _rgb(palette, "subtext")
    if not bullets:
        return
    shape = slide.shapes.add_textbox(l, t, w, Inches(5.6))
    tf = shape.text_frame
    tf.word_wrap = True
    for i, bullet in enumerate(bullets[:5]):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_before = Pt(6) if i > 0 else Pt(0)
        run = p.add_run()
        run.text = f"▸  {bullet}"
        run.font.size = Pt(16)
        run.font.color.rgb = text_c if i == 0 else subtext


def _build_outro_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    subtext = _rgb(palette, "subtext")

    _rect(s, 0, 0, W, Inches(0.1), accent)
    _rect(s, 0, H - Inches(0.1), W, Inches(0.1), accent)

    title = slide_data.get("title", "Thank You")
    _box(s, LM, Inches(1.2), CW, Inches(1.5),
         title, size=40, bold=True, color=accent, align=PP_ALIGN.CENTER)
    _rect(s, Inches(3.8), Inches(2.8), Inches(5.73), Inches(0.03), accent)

    bullets = slide_data.get("bullets", [])
    for i, bullet in enumerate(bullets[:3]):
        _box(s, LM, Inches(3.0 + i * 0.7), CW, Inches(0.55),
             bullet, size=16, color=subtext, align=PP_ALIGN.CENTER)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


# ── Viz Slide Builders (VIZ Epic B) ───────────────────────────────────────────

def _title_bar(s, title: str, palette: dict) -> None:
    """Standard left-stripe + dark title bar used by all content-class slides."""
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         (title or "")[:80], size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))


def _new_content_slide(prs, title: str, palette: dict, n: int, notes: str = ""):
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    _title_bar(s, title, palette)
    _footer(s, n, palette)
    if notes:
        _notes(s, notes)
    return s


def _build_kpi_slide(prs, slide_data, n, palette, theme) -> None:
    """KPI tiles laid out as cards (the metrics/'data' slide)."""
    s = _new_content_slide(prs, slide_data.get("title", "Key Metrics"), palette, n,
                           slide_data.get("speaker_notes", ""))
    spec = KpiSpec.from_dict(slide_data["kpis"])
    tiles = spec.tiles[:4]
    if not tiles:
        return
    gap = Inches(0.3)
    total_w = CW - gap * (len(tiles) - 1)
    tile_w = total_w / len(tiles)
    top = Inches(2.3)
    tile_h = Inches(2.6)
    accent = _rgb(palette, "accent")
    subtext = _rgb(palette, "subtext")
    for i, t in enumerate(tiles):
        left = LM + (tile_w + gap) * i
        _rect(s, left, top, tile_w, tile_h, _rgb(palette, "dark"), accent)
        _rect(s, left, top, Inches(0.06), tile_h, accent)
        _box(s, left + Inches(0.2), top + Inches(0.25), tile_w - Inches(0.4), Inches(0.5),
             t.label.upper(), size=12, color=subtext)
        _box(s, left + Inches(0.2), top + Inches(0.8), tile_w - Inches(0.4), Inches(1.0),
             f"{t.value}{t.unit}", size=40, bold=True, color=accent)
        if t.delta:
            _box(s, left + Inches(0.2), top + Inches(1.9), tile_w - Inches(0.4), Inches(0.5),
                 t.delta, size=14, color=subtext)


def _build_chart_slide(prs, slide_data, n, palette, theme) -> None:
    s = _new_content_slide(prs, slide_data.get("title", "Chart"), palette, n,
                           slide_data.get("speaker_notes", ""))
    spec = ChartSpec.from_dict(slide_data["chart"])
    render_pptx.add_chart(s, spec, LM, Inches(1.0), CW, Inches(5.8), theme)


def _build_table_slide(prs, slide_data, n, palette, theme) -> None:
    s = _new_content_slide(prs, slide_data.get("title", "Table"), palette, n,
                           slide_data.get("speaker_notes", ""))
    spec = TableSpec.from_dict(slide_data["table"])
    rows = max(len(spec.rows) + (1 if spec.headers else 0), 1)
    height = min(Inches(5.6), Inches(0.5) * rows + Inches(0.5))
    render_pptx.add_table(s, spec, LM, Inches(1.1), CW, height, theme)


def _build_diagram_slide(prs, slide_data, n, palette, theme) -> None:
    s = _new_content_slide(prs, slide_data.get("title", "Diagram"), palette, n,
                           slide_data.get("speaker_notes", ""))
    spec = DiagramSpec.from_dict(slide_data["diagram"])
    try:
        png = render_png.diagram_to_png(spec, theme=theme)
        if png and Path(png).exists():
            s.shapes.add_picture(png, LM, Inches(1.0), CW, Inches(5.7))
    except Exception:
        pass


def _build_agenda_slide(prs, slide_data, n, palette, theme) -> None:
    s = _new_content_slide(prs, slide_data.get("title", "Agenda"), palette, n,
                           slide_data.get("speaker_notes", ""))
    accent = _rgb(palette, "accent")
    text_c = _rgb(palette, "text")
    items = slide_data.get("bullets", [])[:8]
    for i, item in enumerate(items):
        top = Inches(1.2) + Inches(0.62) * i
        _box(s, LM, top, Inches(0.6), Inches(0.5), f"{i + 1:02d}", size=20,
             bold=True, color=accent)
        _box(s, LM + Inches(0.8), top, CW - Inches(0.8), Inches(0.5), item,
             size=18, color=text_c)


def _build_dashboard_slide(prs, slide_data, n, palette, theme) -> None:
    """Static PPTX snapshot of a dashboard: KPI tiles row + up to two charts.

    The interactive dashboard (filters/drill-down) lives in the web presenter;
    PPTX gets a clean static composition of the same tiles.
    """
    spec = DashboardSpec.from_dict(slide_data["dashboard"])
    s = _new_content_slide(prs, slide_data.get("title", "Dashboard"), palette, n,
                           slide_data.get("speaker_notes", ""))

    kpi_specs = [t["spec"] for t in spec.tiles
                 if isinstance(t.get("spec"), dict) and t["spec"].get("kind") == "kpis"]
    chart_specs = [t["spec"] for t in spec.tiles
                   if isinstance(t.get("spec"), dict) and t["spec"].get("kind") == "chart"]

    top = Inches(1.0)
    # KPI strip
    if kpi_specs:
        tiles = KpiSpec.from_dict(kpi_specs[0]).tiles[:4]
        if tiles:
            gap = Inches(0.25)
            tw = (CW - gap * (len(tiles) - 1)) / len(tiles)
            accent = _rgb(palette, "accent")
            subtext = _rgb(palette, "subtext")
            for i, t in enumerate(tiles):
                left = LM + (tw + gap) * i
                _rect(s, left, top, tw, Inches(1.3), _rgb(palette, "dark"), accent)
                _box(s, left + Inches(0.15), top + Inches(0.12), tw - Inches(0.3), Inches(0.35),
                     t.label.upper(), size=10, color=subtext)
                _box(s, left + Inches(0.15), top + Inches(0.5), tw - Inches(0.3), Inches(0.7),
                     f"{t.value}{t.unit}", size=26, bold=True, color=accent)
        top = top + Inches(1.55)

    # Up to two charts side by side
    charts = chart_specs[:2]
    if charts:
        gap = Inches(0.3)
        cw = (CW - gap) / len(charts) if len(charts) > 1 else CW
        ch = H - top - Inches(0.5)
        for i, c in enumerate(charts):
            left = LM + (cw + gap) * i
            try:
                render_pptx.add_chart(s, ChartSpec.from_dict(c), left, top, cw, ch, theme)
            except Exception:
                pass


def _build_quote_slide(prs, slide_data, n, palette, theme) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    text_c = _rgb(palette, "text")
    _rect(s, 0, 0, W, Inches(0.1), accent)
    _rect(s, 0, H - Inches(0.1), W, Inches(0.1), accent)
    quote = (slide_data.get("bullets") or [slide_data.get("title", "")])[0]
    _box(s, Inches(1.5), Inches(2.4), W - Inches(3.0), Inches(2.7),
         f"“{quote}”", size=30, italic=True, bold=True,
         color=text_c, align=PP_ALIGN.CENTER)
    _footer(s, n, palette)
    if slide_data.get("speaker_notes"):
        _notes(s, slide_data["speaker_notes"])


# ── Freeform element slide (WYSIWYG — absolute positioning) ───────────────────

def _hex_rgb(hexstr: str) -> RGBColor:
    s = str(hexstr or "#FFFFFF").lstrip("#")
    if len(s) != 6:
        s = "FFFFFF"
    try:
        return RGBColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return RGBColor(0xFF, 0xFF, 0xFF)


def _el_box(el) -> tuple:
    """Fractional element geometry → (left, top, width, height) in EMU."""
    return (Inches(el.x * _SW_IN), Inches(el.y * _SH_IN),
            Inches(el.w * _SW_IN), Inches(el.h * _SH_IN))


def _resolve_image_path(src: str) -> str | None:
    """Resolve an element image src (path or /slides/api/image?path=…) to a file."""
    if not src:
        return None
    if "path=" in src:
        from urllib.parse import unquote, urlparse, parse_qs
        try:
            q = parse_qs(urlparse(src).query)
            src = unquote(q.get("path", [""])[0])
        except Exception:
            pass
    return src if src and Path(src).exists() else None


def _el_text(s, el, palette) -> None:
    left, top, w, h = _el_box(el)
    style = el.style or {}
    tb = s.shapes.add_textbox(left, top, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    align = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
             "right": PP_ALIGN.RIGHT}.get(style.get("align", "left"), PP_ALIGN.LEFT)
    color = _hex_rgb(style.get("color", "#FFFFFF"))
    size = int(style.get("fontSize", 18) or 18)
    fam = style.get("fontFamily", "Segoe UI")
    for idx, line in enumerate(str(el.payload.get("text", "")).split("\n")):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bool(style.get("bold"))
        run.font.italic = bool(style.get("italic"))
        run.font.color.rgb = color
        try:
            run.font.name = fam
        except (AttributeError, ValueError):
            pass


def _el_kpis(s, el, palette) -> None:
    left, top, w, h = _el_box(el)
    spec = KpiSpec.from_dict(el.payload)
    tiles = spec.tiles[:4]
    if not tiles:
        return
    gap = Inches(0.2)
    tw = (w - gap * (len(tiles) - 1)) / len(tiles)
    accent = _rgb(palette, "accent")
    subtext = _rgb(palette, "subtext")
    for j, t in enumerate(tiles):
        tl = left + (tw + gap) * j
        _rect(s, tl, top, tw, h, _rgb(palette, "dark"), accent)
        hb = s.shapes.add_textbox(tl + Inches(0.12), top + Inches(0.12), tw - Inches(0.24), Inches(0.4))
        hb.text_frame.word_wrap = True
        r = hb.text_frame.paragraphs[0].add_run()
        r.text = t.label.upper()
        r.font.size = Pt(11)
        r.font.color.rgb = subtext
        vb = s.shapes.add_textbox(tl + Inches(0.12), top + Inches(0.5), tw - Inches(0.24), h - Inches(0.6))
        vb.text_frame.word_wrap = True
        rv = vb.text_frame.paragraphs[0].add_run()
        rv.text = f"{t.value}{t.unit}"
        rv.font.size = Pt(28)
        rv.font.bold = True
        rv.font.color.rgb = accent


def _el_shape(s, el) -> None:
    """Render a shape element (rectangle/ellipse/line/arrow) natively in PPTX."""
    from pptx.enum.shapes import MSO_SHAPE
    left, top, w, h = _el_box(el)
    st = el.style or {}
    kind = (el.payload or {}).get("shape", "rectangle")
    fill = _hex_rgb(st.get("fill", "#C8A951"))
    if kind in ("line", "arrow"):
        thick = Inches(max(0.02, (st.get("strokeWidth", 3) or 3) * 0.02))
        _rect(s, left, top + h // 2 - thick // 2, w, thick, fill)
        return
    mso = (MSO_SHAPE.OVAL if kind == "ellipse"
           else (MSO_SHAPE.ROUNDED_RECTANGLE if st.get("cornerRadius") else MSO_SHAPE.RECTANGLE))
    shp = s.shapes.add_shape(mso, left, top, w, h)
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    sw = int(st.get("strokeWidth", 0) or 0)
    if sw > 0 and st.get("stroke") and st["stroke"] != "transparent":
        shp.line.color.rgb = _hex_rgb(st["stroke"])
        shp.line.width = Pt(sw)
    else:
        shp.line.fill.background()


def _build_element_slide(prs, slide_data, n, palette, theme) -> None:
    """Render a freeform positioned-element slide (WYSIWYG with the web editor)."""
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    els = _elements.elements_from_dicts(slide_data.get("elements", []))
    for el in sorted(els, key=lambda e: e.z):
        left, top, w, h = _el_box(el)
        try:
            if el.type == "text":
                _el_text(s, el, palette)
            elif el.type == "image":
                p = _resolve_image_path(el.payload.get("src", ""))
                if p:
                    s.shapes.add_picture(p, left, top, w, h)
            elif el.type == "chart":
                render_pptx.add_chart(s, ChartSpec.from_dict(el.payload), left, top, w, h, theme)
            elif el.type == "table":
                render_pptx.add_table(s, TableSpec.from_dict(el.payload), left, top, w, h, theme)
            elif el.type == "kpis":
                _el_kpis(s, el, palette)
            elif el.type == "shape":
                _el_shape(s, el)
            elif el.type in ("diagram", "dashboard"):
                # Render to PNG and place (dashboards: first chart tile if present).
                spec_dict = el.payload
                if el.type == "dashboard":
                    charts = [t.get("spec") for t in spec_dict.get("tiles", [])
                              if isinstance(t.get("spec"), dict) and t["spec"].get("kind") == "chart"]
                    if charts:
                        render_pptx.add_chart(s, ChartSpec.from_dict(charts[0]), left, top, w, h, theme)
                    continue
                png = render_png.diagram_to_png(DiagramSpec.from_dict(spec_dict), theme=theme)
                if png and Path(png).exists():
                    s.shapes.add_picture(png, left, top, w, h)
        except Exception:
            continue
    _footer(s, n, palette)
    if slide_data.get("speaker_notes"):
        _notes(s, slide_data["speaker_notes"])


def _render_slide(prs, slide_data, i, total, palette, theme) -> None:
    """Dispatch a single slide to the right builder.

    A slide with an explicit ``elements`` list is freeform (WYSIWYG) and takes
    precedence; otherwise explicit viz payloads win over generic slide_type.
    """
    n = i + 1
    stype = slide_data.get("slide_type", "content")
    image_path = slide_data.get("image_path")

    if slide_data.get("elements"):
        _build_element_slide(prs, slide_data, n, palette, theme)
        return

    if stype == "title" or i == 0:
        _build_title_slide(prs, slide_data, n, palette)
    elif stype == "outro" or i == total - 1:
        _build_outro_slide(prs, slide_data, n, palette)
    elif slide_data.get("dashboard"):
        _build_dashboard_slide(prs, slide_data, n, palette, theme)
    elif slide_data.get("kpis"):
        _build_kpi_slide(prs, slide_data, n, palette, theme)
    elif slide_data.get("chart"):
        _build_chart_slide(prs, slide_data, n, palette, theme)
    elif slide_data.get("table"):
        _build_table_slide(prs, slide_data, n, palette, theme)
    elif slide_data.get("diagram"):
        _build_diagram_slide(prs, slide_data, n, palette, theme)
    elif stype == "agenda":
        _build_agenda_slide(prs, slide_data, n, palette, theme)
    elif stype == "quote":
        _build_quote_slide(prs, slide_data, n, palette, theme)
    else:
        _build_content_slide(prs, slide_data, n, palette, image_path)


# ── Main Builder ──────────────────────────────────────────────────────────────

def build(
    slides: list[dict],
    theme: str = DEFAULT_THEME,
    title: str = "ICDEV™ Presentation",
) -> str:
    """Assemble slides into a .pptx file. Returns absolute file path."""
    palette = THEME_PALETTES.get(theme, THEME_PALETTES[DEFAULT_THEME])
    prs = _new_prs()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total = len(slides)
    for i, slide_data in enumerate(slides):
        _render_slide(prs, slide_data, i, total, palette, theme)

    # Generate filename with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    filename = f"{ts}_{slug}.pptx"
    out_path = _OUTPUT_DIR / filename
    prs.save(str(out_path))
    return str(out_path)
