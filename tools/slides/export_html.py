# CUI // SP-CTI
"""Slide Deck HTML Export — self-contained, responsive, air-gap compatible.

Usage:
    from tools.slides.export_html import build_html
    path = build_html(slides, theme="fun_fiesta", title="My Deck")
"""
from __future__ import annotations

import hashlib
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _ICDEV_ROOT / "tools" / "presentations" / "slides"


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def build_html(
    slides: list[dict],
    theme: str = DEFAULT_THEME,
    title: str = "ICDEV™ Presentation",
    deck_meta: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> str:
    """Build a self-contained HTML slide deck. Returns absolute file path."""
    palette = THEME_PALETTES.get(theme, THEME_PALETTES[DEFAULT_THEME])
    bg = _hex(palette["bg"])
    accent = _hex(palette["accent"])
    text = _hex(palette["text"])
    subtext = _hex(palette["subtext"])
    dark = _hex(palette.get("dark", palette["bg"]))

    meta = deck_meta or {}
    occasion = html.escape(str(meta.get("occasion", "")))
    tone = html.escape(str(meta.get("tone", "")))
    audience = html.escape(str(meta.get("target_audience", "")))

    style = f"""
    :root {{
        --bg: {bg};
        --accent: {accent};
        --text: {text};
        --subtext: {subtext};
        --dark: {dark};
    }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0; padding: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #111; color: var(--text);
    }}
    .banner {{
        background: var(--accent); color: var(--text); padding: 6px 16px; font-size: 12px; font-weight: 600;
    }}
    .deck-header {{
        padding: 24px 32px; background: var(--bg); border-bottom: 4px solid var(--accent);
    }}
    .deck-header h1 {{ margin: 0 0 8px; font-size: 32px; color: var(--accent); }}
    .deck-meta {{ color: var(--subtext); font-size: 14px; }}
    .slide {{
        width: 100%; max-width: 1100px; margin: 24px auto;
        background: var(--bg); border-radius: 12px; overflow: hidden;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5); page-break-after: always;
    }}
    .slide-header {{
        background: var(--dark); padding: 20px 32px; border-left: 8px solid var(--accent);
    }}
    .slide-header h2 {{ margin: 0; font-size: 28px; color: var(--accent); }}
    .slide-body {{ padding: 24px 32px; min-height: 360px; }}
    .slide-body ul {{ margin: 0; padding-left: 24px; }}
    .slide-body li {{ margin-bottom: 12px; font-size: 18px; line-height: 1.5; color: var(--text); }}
    .slide-image {{ max-width: 100%; max-height: 280px; display: block; margin: 16px auto; border-radius: 8px; }}
    .notes {{
        background: rgba(255,255,255,0.05); padding: 16px 32px; font-size: 14px;
        color: var(--subtext); border-top: 1px solid rgba(255,255,255,0.1);
    }}
    .citations {{
        padding: 12px 32px; font-size: 12px; color: var(--subtext); border-top: 1px solid rgba(255,255,255,0.05);
    }}
    .citations a {{ color: var(--accent); text-decoration: none; }}
    .footer {{
        display: flex; justify-content: space-between; padding: 12px 32px;
        font-size: 12px; color: var(--subtext); border-top: 1px solid rgba(255,255,255,0.1);
    }}
    .print-only {{ display: none; }}
    @media print {{
        body {{ background: #fff; }}
        .slide {{ box-shadow: none; margin: 0 auto; }}
    }}
    """

    slides_html: list[str] = []
    for i, slide in enumerate(slides, start=1):
        bullets = slide.get("bullets") or []
        bullets_html = "\n".join(f"<li>{html.escape(str(b))}</li>" for b in bullets[:6])
        image_tag = ""
        img_path = slide.get("image_path")
        if img_path and Path(img_path).exists():
            # Read image and embed as base64 for self-contained HTML
            try:
                import base64

                ext = Path(img_path).suffix.lstrip(".") or "png"
                if ext in ("jpg", "jpeg"):
                    mime = "image/jpeg"
                else:
                    mime = f"image/{ext}"
                data = Path(img_path).read_bytes()
                b64 = base64.b64encode(data).decode("ascii")
                image_tag = f'<img class="slide-image" src="data:{mime};base64,{b64}" alt="">'
            except Exception:
                image_tag = ""
        notes = slide.get("speaker_notes", "")
        notes_html = f'<div class="notes"><strong>Speaker notes:</strong> {html.escape(str(notes))}</div>' if notes else ""
        citations = slide.get("citations", [])
        citation_html = ""
        if citations:
            items = []
            for idx, src in enumerate(citations[:3], start=1):
                src_title = html.escape(str(src.get("title") or "Source"))
                url = src.get("url", "")
                if url:
                    items.append(f'<a href="{html.escape(str(url))}" target="_blank">[{idx}] {src_title}</a>')
                else:
                    items.append(f"[{idx}] {src_title}")
            citation_html = '<div class="citations"><strong>Sources:</strong> ' + " | ".join(items) + "</div>"

        slides_html.append(f"""
        <section class="slide">
          <div class="slide-header"><h2>{html.escape(str(slide.get('title', '')))}</h2></div>
          <div class="slide-body">
            {image_tag}
            <ul>{bullets_html}</ul>
          </div>
          {notes_html}
          {citation_html}
          <div class="footer"><span>{html.escape(title)}</span><span>{i}</span></div>
        </section>
        """)

    meta_html = ""
    if occasion or tone or audience:
        parts = []
        if occasion:
            parts.append(f"Occasion: {occasion}")
        if tone:
            parts.append(f"Tone: {tone}")
        if audience:
            parts.append(f"Audience: {audience}")
        meta_html = f'<div class="deck-meta">{html.escape(" | ".join(parts))}</div>'

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{style}</style>
</head>
<body>
  <div class="banner">CUI // SP-CTI</div>
  <div class="deck-header">
    <h1>{html.escape(title)}</h1>
    {meta_html}
  </div>
  {''.join(slides_html)}
</body>
</html>
"""

    out_dir = output_dir or _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = hashlib.sha256(title.encode()).hexdigest()[:8]
    out_path = out_dir / f"{ts}_{slug}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return str(out_path)
