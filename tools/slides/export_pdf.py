# CUI // SP-CTI
"""Slide Deck PDF Export — fpdf2-based, air-gap compatible.

Usage:
    from tools.slides.export_pdf import build_pdf
    path = build_pdf(slides, theme="fun_fiesta", title="My Deck")
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _ICDEV_ROOT / "tools" / "presentations" / "slides"


def _rgb_to_int(rgb: tuple[int, int, int]) -> int:
    return (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]


def _safe(text: Any) -> str:
    """Sanitize text for fpdf2 core fonts (latin-1 encoding)."""
    if text is None:
        return ""
    s = str(text)
    # Replace common unicode dashes/quotes with ASCII equivalents
    replacements = {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "✓": "+",
        "▸": ">",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def build_pdf(
    slides: list[dict],
    theme: str = DEFAULT_THEME,
    title: str = "ICDEV™ Presentation",
    output_dir: Path | None = None,
) -> str:
    """Build a PDF slide deck. Returns absolute file path."""
    palette = THEME_PALETTES.get(theme, THEME_PALETTES[DEFAULT_THEME])
    bg = _rgb_to_int(palette["bg"])
    accent = _rgb_to_int(palette["accent"])
    text = _rgb_to_int(palette["text"])
    subtext = _rgb_to_int(palette["subtext"])

    pdf = FPDF(orientation="L", unit="in", format="letter")
    pdf.set_auto_page_break(False)
    pdf.set_margins(0, 0, 0)

    out_dir = output_dir or _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, slide in enumerate(slides):
        pdf.add_page()
        # Background
        pdf.set_fill_color((bg >> 16) & 0xFF, (bg >> 8) & 0xFF, bg & 0xFF)
        pdf.rect(0, 0, 11, 8.5, style="F")

        # Classification banner
        pdf.set_fill_color((accent >> 16) & 0xFF, (accent >> 8) & 0xFF, accent & 0xFF)
        pdf.set_text_color((text >> 16) & 0xFF, (text >> 8) & 0xFF, text & 0xFF)
        pdf.set_font("Helvetica", "B", 8)
        pdf.rect(0, 0, 11, 0.25, style="F")
        pdf.set_xy(0.3, 0.05)
        pdf.cell(0, 0.15, _safe("CUI // SP-CTI"), align="L")

        # Title
        pdf.set_text_color((accent >> 16) & 0xFF, (accent >> 8) & 0xFF, accent & 0xFF)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_xy(0.5, 0.6)
        pdf.cell(10, 0.5, _safe(slide.get("title", "")), align="L")

        # Accent underline
        pdf.set_fill_color((accent >> 16) & 0xFF, (accent >> 8) & 0xFF, accent & 0xFF)
        pdf.rect(0.5, 1.1, 10, 0.04, style="F")

        # Bullets
        bullets = slide.get("bullets", []) or []
        if bullets:
            pdf.set_text_color((text >> 16) & 0xFF, (text >> 8) & 0xFF, text & 0xFF)
            pdf.set_font("Helvetica", "", 14)
            y = 1.4
            for bullet in bullets[:6]:
                pdf.set_xy(0.7, y)
                pdf.cell(0.2, 0.25, _safe(">"), align="L")
                pdf.set_xy(0.9, y)
                pdf.multi_cell(9.5, 0.25, _safe(bullet))
                y += 0.35

        # Speaker notes
        notes = slide.get("speaker_notes", "")
        if notes:
            pdf.set_text_color((subtext >> 16) & 0xFF, (subtext >> 8) & 0xFF, subtext & 0xFF)
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_xy(0.5, 6.6)
            pdf.multi_cell(10, 0.18, _safe(f"Notes: {notes}"))

        # Citations
        citations = slide.get("citations", [])
        if citations:
            pdf.set_text_color((subtext >> 16) & 0xFF, (subtext >> 8) & 0xFF, subtext & 0xFF)
            pdf.set_font("Helvetica", "", 8)
            citation_lines = []
            for idx, src in enumerate(citations[:3], start=1):
                src_title = (src.get("title") or "Source")[:50]
                citation_lines.append(f"[{idx}] {src_title}")
            pdf.set_xy(0.5, 7.25)
            pdf.multi_cell(10, 0.15, _safe("Sources: " + " | ".join(citation_lines)))

        # Page number / footer
        pdf.set_text_color((subtext >> 16) & 0xFF, (subtext >> 8) & 0xFF, subtext & 0xFF)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_xy(10.2, 7.85)
        pdf.cell(0.3, 0.15, _safe(str(i + 1)), align="R")
        pdf.set_xy(0.5, 7.85)
        pdf.cell(5, 0.15, _safe(title[:60]), align="L")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    out_path = out_dir / f"{ts}_{slug}.pdf"
    pdf.output(str(out_path))
    return str(out_path)
