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
        try:
            s.shapes.add_picture(image_path, img_l, Inches(0.85), img_w, Inches(5.6))
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
        else:
            _build_content_slide(prs, slide_data, n, palette, image_path)

    # Generate filename with timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    filename = f"{ts}_{slug}.pptx"
    out_path = _OUTPUT_DIR / filename
    prs.save(str(out_path))
    return str(out_path)
