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
from pptx.util import Emu, Inches, Pt

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

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


def _citation_footer(slide, palette: dict, citations: list[dict]) -> None:
    """Render short source citations at the bottom of a content slide."""
    if not citations:
        return
    subtext = _rgb(palette, "subtext")
    lines = []
    for i, src in enumerate(citations[:3], start=1):
        title = (src.get("title") or "Source")[:60]
        url = src.get("url", "")
        line = f"[{i}] {title}"
        if url:
            line += f" — {url[:80]}"
        lines.append(line)
    text = " | ".join(lines)
    _box(slide, LM, H - Inches(0.58), CW, Inches(0.26),
         text, size=7, color=subtext, wrap=True)


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
        img_h = Inches(5.6)
        try:
            if str(image_path).lower().endswith(".svg"):
                # add_picture cannot rasterize SVG (no PIL SVG support) — embed
                # as native, editable shapes instead of a flattened picture.
                from tools.viz import svg_to_pptx
                svg_to_pptx.embed_svg_file(s, image_path, img_l, Inches(0.85), img_w, img_h)
            else:
                s.shapes.add_picture(image_path, img_l, Inches(0.85), img_w, img_h)
        except Exception:
            pass
    else:
        _add_bullets(s, bullets, Inches(0.24), Inches(0.85), CW, palette)

    _citation_footer(s, palette, slide_data.get("citations", []))
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


def _try_mmdc_render(mermaid_code: str) -> str | None:
    """Try rendering Mermaid diagram to PNG via mmdc CLI. Returns temp PNG path or None."""
    import subprocess
    import tempfile
    import os
    tmp_dir = tempfile.gettempdir()
    in_path = os.path.join(tmp_dir, f"icdev_mmdc_in_{id(mermaid_code)}.mmd")
    out_path = os.path.join(tmp_dir, f"icdev_mmdc_out_{id(mermaid_code)}.png")
    try:
        with open(in_path, "w", encoding="utf-8") as f:
            f.write(mermaid_code)
        result = subprocess.run(
            ["mmdc", "-i", in_path, "-o", out_path, "-b", "transparent", "-w", "1200"],
            timeout=15, capture_output=True,
        )
        if result.returncode == 0 and Path(out_path).exists():
            return out_path
    except Exception:
        pass
    finally:
        try:
            os.unlink(in_path)
        except Exception:
            pass
    return None


def _build_mermaid_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    mermaid_code = slide_data.get("mermaid_code") or ""
    img_path = _try_mmdc_render(mermaid_code) if mermaid_code else None

    if img_path:
        try:
            s.shapes.add_picture(img_path, LM, Inches(0.9), CW, Inches(5.2))
        except Exception:
            img_path = None

    if not img_path:
        # Fallback: monospace text block with raw Mermaid code
        _box(s, LM, Inches(0.9), CW, Inches(4.0),
             mermaid_code[:800] if mermaid_code else "(No diagram generated)",
             size=9, color=subtext, wrap=True)
        _box(s, LM, Inches(5.0), CW, Inches(0.4),
             "ℹ️  Open the web presentation viewer or HTML export to see the interactive diagram.",
             size=11, color=accent, italic=True, wrap=True)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _build_svg_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    """Full-slide vector art, rendered as native/editable PPTX shapes (not a picture)."""
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    svg_code = slide_data.get("svg_code") or ""
    rendered = False
    if svg_code:
        try:
            from tools.viz import svg_to_pptx
            shapes = svg_to_pptx.render_svg_into_slide(s, svg_code, LM, Inches(0.9), CW, Inches(5.6))
            rendered = bool(shapes)
        except Exception:
            rendered = False

    if not rendered:
        _box(s, LM, Inches(2.8), CW, Inches(0.6),
             "(No vector art generated)", size=14, color=subtext, align=PP_ALIGN.CENTER)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _build_three_placeholder_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    cfg = slide_data.get("three_scene_config") or {}
    preset = cfg.get("preset", "neural_network") if isinstance(cfg, dict) else "neural_network"

    _rect(s, LM, Inches(1.1), CW, Inches(4.2), dark)
    _box(s, LM + Inches(0.3), Inches(2.0), CW - Inches(0.6), Inches(0.6),
         "🌐  3D Animation", size=28, bold=True, color=accent, align=PP_ALIGN.CENTER)
    _box(s, LM + Inches(0.3), Inches(2.7), CW - Inches(0.6), Inches(0.5),
         f"Scene: {preset}", size=14, color=subtext, align=PP_ALIGN.CENTER, italic=True)

    _box(s, LM, Inches(5.4), CW, Inches(0.5),
         "Interactive 3D animation — open the web presentation viewer to experience this slide.",
         size=11, color=accent, italic=True, wrap=True)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _build_excalidraw_placeholder_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    _rect(s, LM, Inches(1.1), CW, Inches(4.2), dark)
    _box(s, LM + Inches(0.3), Inches(2.0), CW - Inches(0.6), Inches(0.6),
         "✏️  Hand-Drawn Diagram", size=24, bold=True, color=accent, align=PP_ALIGN.CENTER)
    _box(s, LM + Inches(0.3), Inches(2.7), CW - Inches(0.6), Inches(0.8),
         slide_data.get("speaker_notes", "")[:200],
         size=12, color=subtext, align=PP_ALIGN.CENTER, wrap=True)

    _box(s, LM, Inches(5.4), CW, Inches(0.5),
         "Interactive sketch — open the web presentation viewer to experience this slide.",
         size=11, color=accent, italic=True, wrap=True)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _build_card_grid_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    """3-column card grid (investment overview / capability comparison)."""
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")
    teal = _rgb(palette.get("teal") and palette or {"teal": (0x00, 0xB4, 0xD8)}, "teal") if "teal" in palette else accent

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    bullets = slide_data.get("bullets", [])
    # Expect bullets to be card dicts or plain strings
    cards = []
    for b in bullets:
        if isinstance(b, dict):
            cards.append(b)
        elif isinstance(b, str):
            cards.append({"label": "", "title": b, "body": "", "meta": ""})

    if not cards:
        _box(s, LM, Inches(1.5), CW, Inches(3.0),
             "No card data — bullets should be card objects.", size=14, color=subtext)
        _footer(s, n, palette)
        return

    cols = 3
    rows = (len(cards) + cols - 1) // cols
    card_w = CW / cols - Inches(0.12)
    card_h = min(Inches(2.0), (H - Inches(1.4)) / rows - Inches(0.1))
    start_y = Inches(0.85)
    pad = Inches(0.1)

    for ci, card in enumerate(cards[:9]):  # max 9 cards (3x3)
        row, col = divmod(ci, cols)
        cl = LM + col * (card_w + Inches(0.12))
        ct = start_y + row * (card_h + Inches(0.08))

        card_accent_hex = card.get("accent_color", "")
        if card_accent_hex and card_accent_hex.startswith("#") and len(card_accent_hex) >= 7:
            try:
                cr = int(card_accent_hex[1:3], 16)
                cg = int(card_accent_hex[3:5], 16)
                cb_ = int(card_accent_hex[5:7], 16)
                ca = RGBColor(cr, cg, cb_)
            except Exception:
                ca = teal
        else:
            ca = teal

        _rect(s, cl, ct, card_w, card_h, dark)
        _rect(s, cl, ct, card_w, Inches(0.05), ca)  # top accent bar

        label = str(card.get("label", ""))[:10]
        title_text = str(card.get("title", ""))[:50]
        body_text = str(card.get("body", ""))[:120]
        meta_text = str(card.get("meta", ""))[:60]

        if label:
            _box(s, cl + pad, ct + Inches(0.07), card_w - pad * 2, Inches(0.28),
                 label, size=11, bold=True, color=ca)
        body_top = ct + Inches(0.07 + (0.28 if label else 0))
        if title_text:
            _box(s, cl + pad, body_top, card_w - pad * 2, Inches(0.28),
                 title_text, size=9, bold=True, color=_rgb(palette, "text"))
            body_top += Inches(0.28)
        if body_text:
            _box(s, cl + pad, body_top, card_w - pad * 2, card_h - (body_top - ct) - Inches(0.25),
                 body_text, size=8, color=subtext, wrap=True)
        if meta_text:
            _box(s, cl + pad, ct + card_h - Inches(0.22), card_w - pad * 2, Inches(0.2),
                 meta_text, size=7, color=ca, wrap=False)

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


def _build_table_slide(prs: Presentation, slide_data: dict, n: int, palette: dict) -> None:
    """Render a data table using python-pptx's native table shape."""
    s = _blank(prs)
    _bg(s, _rgb(palette, "bg"))
    accent = _rgb(palette, "accent")
    dark = _rgb(palette, "dark")
    subtext = _rgb(palette, "subtext")
    text_c = _rgb(palette, "text")

    _rect(s, 0, 0, Inches(0.12), H, accent)
    _rect(s, Inches(0.12), 0, W - Inches(0.12), Inches(0.72), dark)
    title = slide_data.get("title", "")[:80]
    _box(s, Inches(0.24), Inches(0.12), CW, Inches(0.55),
         title, size=22, bold=True, color=accent)
    _accent_bar(s, palette, top=Inches(0.72), h=Inches(0.04))

    tbl_data = slide_data.get("bullets") or {}
    if isinstance(tbl_data, str):
        import json as _json
        try:
            tbl_data = _json.loads(tbl_data)
        except Exception:
            tbl_data = {}

    headers = tbl_data.get("headers", []) if isinstance(tbl_data, dict) else []
    rows = tbl_data.get("rows", []) if isinstance(tbl_data, dict) else []
    footer = tbl_data.get("footer", []) if isinstance(tbl_data, dict) else []

    all_rows = ([headers] if headers else []) + rows + ([footer] if footer else [])
    if not all_rows:
        _box(s, LM, Inches(1.5), CW, Inches(1.0), "No table data.", size=14, color=subtext)
        _footer(s, n, palette)
        return

    num_rows = len(all_rows)
    num_cols = max(len(r) for r in all_rows) if all_rows else 1
    # Emu subclasses int, but `/` is true division: once num_rows exceeds ~11 the
    # float branch wins here and the frame height reaches the XML as
    # cy="5029200.0". That is not a valid ST_PositiveCoordinate (xsd:long), so
    # PowerPoint reports the deck as needing repair and python-pptx raises on
    # .height — with no error at write time. Keep row heights whole EMU.
    row_h = Emu(int(min(Inches(0.5), (H - Inches(2.0)) / num_rows)))

    tbl_shape = s.shapes.add_table(num_rows, num_cols, LM, Inches(0.9), CW, row_h * num_rows)
    tbl = tbl_shape.table

    for ri, row_data in enumerate(all_rows):
        is_header = ri == 0 and bool(headers)
        is_footer = ri == len(all_rows) - 1 and bool(footer)
        for ci in range(num_cols):
            cell = tbl.cell(ri, ci)
            val = str(row_data[ci]) if ci < len(row_data) else ""
            cell.text = val
            tf = cell.text_frame
            tf.paragraphs[0].font.size = Pt(11 if (is_header or is_footer) else 12)
            tf.paragraphs[0].font.bold = is_header or is_footer
            tf.paragraphs[0].font.color.rgb = accent if (is_header or is_footer) else text_c
            cell.fill.solid()
            cell.fill.fore_color.rgb = dark if (is_header or is_footer) else _rgb(palette, "bg")

    _footer(s, n, palette)
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        _notes(s, notes_text)


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

    for i, slide_data in enumerate(slides):
        n = i + 1
        slide_type = slide_data.get("slide_type", "content")
        image_path = slide_data.get("image_path")

        if slide_type == "title" or i == 0:
            _build_title_slide(prs, slide_data, n, palette)
        elif slide_type == "outro" or i == len(slides) - 1:
            _build_outro_slide(prs, slide_data, n, palette)
        elif slide_type == "mermaid_diagram":
            _build_mermaid_slide(prs, slide_data, n, palette)
        elif slide_type == "svg_art":
            _build_svg_slide(prs, slide_data, n, palette)
        elif slide_type == "three_animation":
            _build_three_placeholder_slide(prs, slide_data, n, palette)
        elif slide_type == "excalidraw_sketch":
            _build_excalidraw_placeholder_slide(prs, slide_data, n, palette)
        elif slide_type == "card_grid":
            _build_card_grid_slide(prs, slide_data, n, palette)
        elif slide_type == "table":
            _build_table_slide(prs, slide_data, n, palette)
        else:
            _build_content_slide(prs, slide_data, n, palette, image_path)

    # Generate filename with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    filename = f"{ts}_{slug}.pptx"
    out_path = _OUTPUT_DIR / filename
    prs.save(str(out_path))
    return str(out_path)
