# CUI // SP-CTI
"""Slide Deck HTML Export — self-contained, responsive, air-gap compatible.

Usage:
    from tools.slides.export_html import build_html
    path = build_html(slides, theme="fun_fiesta", title="My Deck")
"""
from __future__ import annotations

import html
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.slides.constants import THEME_PALETTES, DEFAULT_THEME

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_OUTPUT_DIR = _ICDEV_ROOT / "tools" / "presentations" / "slides"


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


_STATIC_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "static"


def _embed_js(rel_path: str) -> str:
    """Read a local static JS file and return an inline <script> tag. Empty string if missing."""
    path = _STATIC_DIR / rel_path
    try:
        content = path.read_text(encoding="utf-8")
        return f"<script>{content}</script>"
    except Exception:
        return ""


def build_html(
    slides: list[dict],
    theme: str = DEFAULT_THEME,
    title: str = "ICDEV™ Presentation",
    deck_meta: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    enable_rich_diagrams: bool = False,
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
    .slide-mermaid {{ padding: 16px 32px; overflow-x: auto; }}
    .slide-mermaid pre {{ background: rgba(255,255,255,0.05); border-radius: 8px; padding: 16px; }}
    .slide-table {{ padding: 16px 32px; overflow-x: auto; }}
    .slide-table table {{ width: 100%; border-collapse: collapse; font-size: 16px; }}
    .slide-table th {{ background: var(--dark); color: var(--accent); font-weight: 700; padding: 10px 16px; text-align: left; border-bottom: 2px solid var(--accent); }}
    .slide-table td {{ color: var(--text); padding: 9px 16px; border-bottom: 1px solid rgba(255,255,255,0.08); }}
    .slide-table tr:last-child td {{ border-bottom: none; }}
    .slide-table tfoot td {{ background: var(--dark); color: var(--accent); font-weight: 700; border-top: 2px solid var(--accent); padding: 10px 16px; }}
    .slide-placeholder {{
        margin: 24px 32px; padding: 24px; border-radius: 8px;
        background: rgba(255,255,255,0.05); border: 2px dashed rgba(255,255,255,0.2);
        color: var(--subtext); font-style: italic; font-size: 14px;
    }}
    .slide-canvas-wrap {{ padding: 16px 32px; }}
    .slide-svg-wrap {{ padding: 16px 32px; }}
    @media print {{
        body {{ background: #fff; }}
        .slide {{ box-shadow: none; margin: 0 auto; }}
    }}
    """

    import base64
    import json as _json

    # Detect if deck has rich diagram slides (determines whether to embed JS)
    rich_types = {"mermaid_diagram", "three_animation", "excalidraw_sketch"}
    has_rich = enable_rich_diagrams and any(
        s.get("slide_type") in rich_types for s in slides
    )
    has_three = enable_rich_diagrams and any(s.get("slide_type") == "three_animation" for s in slides)
    has_mermaid = any(s.get("slide_type") == "mermaid_diagram" for s in slides)

    slides_html: list[str] = []
    for i, slide in enumerate(slides, start=1):
        slide_type = slide.get("slide_type", "content")
        bullets = slide.get("bullets") or []

        # Rich diagram rendering
        rich_body = ""
        if slide_type == "mermaid_diagram":
            mermaid_code = html.escape(str(slide.get("mermaid_code") or "flowchart LR\n  A --> B"))
            rich_body = f'<div class="slide-mermaid"><pre class="mermaid">{mermaid_code}</pre></div>'
        elif slide_type == "three_animation":
            cfg = slide.get("three_scene_config")
            if enable_rich_diagrams and cfg:
                cfg_json = html.escape(_json.dumps(cfg if isinstance(cfg, dict) else {}))
                rich_body = (
                    f'<div class="slide-canvas-wrap">'
                    f'<canvas data-three-scene=\'{cfg_json}\' style="width:100%;height:380px;display:block;"></canvas>'
                    f'</div>'
                )
            else:
                preset = (cfg or {}).get("preset", "neural_network") if isinstance(cfg, dict) else "neural_network"
                rich_body = f'<div class="slide-placeholder">🌐 Interactive 3D animation ({preset}) — open the web presentation viewer to experience this slide.</div>'
        elif slide_type == "excalidraw_sketch":
            elems = slide.get("excalidraw_elements")
            if enable_rich_diagrams and elems:
                elems_json = html.escape(_json.dumps(elems if isinstance(elems, list) else []))
                rich_body = (
                    f'<div class="slide-svg-wrap">'
                    f'<svg data-excalidraw-elements=\'{elems_json}\' viewBox="0 0 800 450" style="width:100%;max-height:380px;"></svg>'
                    f'</div>'
                )
            else:
                rich_body = '<div class="slide-placeholder">✏️ Hand-drawn diagram — open the web presentation viewer to experience this slide.</div>'
        elif slide_type == "table":
            tbl = bullets if isinstance(bullets, dict) else {}
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            footer = tbl.get("footer", [])
            if headers or rows:
                thead = "<thead><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in headers) + "</tr></thead>"
                tbody_rows = "".join(
                    "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>"
                    for row in rows
                )
                tfoot = ""
                if footer:
                    tfoot = "<tfoot><tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in footer) + "</tr></tfoot>"
                rich_body = f'<div class="slide-table"><table>{thead}<tbody>{tbody_rows}</tbody>{tfoot}</table></div>'
            else:
                rich_body = '<div class="slide-placeholder">Table data not available.</div>'

        bullets_html = ""
        if not rich_body and bullets:
            bullets_html = "<ul>" + "\n".join(f"<li>{html.escape(str(b))}</li>" for b in bullets[:6]) + "</ul>"

        image_tag = ""
        img_path = slide.get("image_path")
        if img_path and Path(img_path).exists():
            try:
                ext = Path(img_path).suffix.lstrip(".") or "png"
                mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
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
            {rich_body}
            {image_tag}
            {bullets_html}
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

    # Build inline JS block for rich diagrams (mermaid embedded separately below)
    rich_js_block = ""
    if has_rich:
        if has_three:
            rich_js_block += _embed_js("vendor/threejs/three.min.js")
        rich_js_block += _embed_js("vendor/roughjs/rough.min.js")
        if has_three:
            rich_js_block += _embed_js("js/slides-three-renderer.js")
        rich_js_block += _embed_js("js/slides-excalidraw-renderer.js")
        # Init script for rich elements on page load
        rich_js_block += """<script>
document.addEventListener('DOMContentLoaded', function() {
  // Init Excalidraw SVGs
  document.querySelectorAll('svg[data-excalidraw-elements]').forEach(function(svg) {
    try {
      var elems = JSON.parse(svg.getAttribute('data-excalidraw-elements').replace(/&quot;/g,'"').replace(/&#39;/g,"'"));
      if (window.ICDEVExcalidrawRenderer) ICDEVExcalidrawRenderer.render(svg, elems);
    } catch(e) {}
  });
  // Init Three.js canvases
  document.querySelectorAll('canvas[data-three-scene]').forEach(function(canvas) {
    try {
      var cfg = JSON.parse(canvas.getAttribute('data-three-scene').replace(/&quot;/g,'"').replace(/&#39;/g,"'"));
      if (window.ICDEVThreeRenderer) ICDEVThreeRenderer.init(canvas, cfg);
    } catch(e) {}
  });
  // Init Mermaid if present
  if (window.mermaid) mermaid.initialize({startOnLoad:true, theme:'dark'});
});
</script>"""
    elif has_mermaid:
        # Embed mermaid for mermaid-only decks (lighter footprint)
        rich_js_block = """<script>
document.addEventListener('DOMContentLoaded', function() {
  if (window.mermaid) mermaid.initialize({startOnLoad:true, theme:'dark'});
});
</script>"""

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
  {rich_js_block}
</body>
</html>
"""

    out_dir = output_dir or _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = format(zlib.crc32(title.encode()) & 0xFFFFFFFF, "08x")
    out_path = out_dir / f"{ts}_{slug}.html"
    out_path.write_text(html_doc, encoding="utf-8", newline="")
    return str(out_path)
