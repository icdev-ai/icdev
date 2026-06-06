# CUI // SP-CTI
"""HTML fragment renderer for the Viz Kernel (web deck + dashboards).

Produces self-contained, themed HTML fragments:
  - chart_to_html    → inline SVG (always renders; no JS dependency)
  - table_to_html    → themed <table>
  - kpis_to_html     → KPI tile row
  - diagram_to_html  → <pre class="mermaid"> block (rendered by vendored
                       mermaid.js) with an inline-SVG <noscript>/fallback

Charts use the inline SVG from render_svg so they render even before any
client JS (charts.js) initializes — progressive enhancement, air-gap safe.
"""
from __future__ import annotations

from tools.viz.palette import get_palette
from tools.viz.render_svg import chart_to_svg, diagram_to_svg
from tools.viz.spec import ChartSpec, DiagramSpec, KpiSpec, TableSpec
from tools.viz import diagram as _diagram


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def chart_to_html(spec: ChartSpec, theme: str = "midnight_executive") -> str:
    """Inline-SVG chart wrapped in a responsive container."""
    svg = chart_to_svg(spec, theme)
    return (f'<div class="viz-chart" data-viz-kind="chart" '
            f'style="max-width:100%;overflow:auto;">{svg}</div>')


def table_to_html(spec: TableSpec, theme: str = "midnight_executive") -> str:
    pal = get_palette(theme)
    accent, bg, text, dark = pal.hex("accent"), pal.hex("bg"), pal.hex("text"), pal.hex("dark")
    out = ['<div class="viz-table">']
    if spec.title:
        out.append(f'<h3 style="color:{accent};margin:0 0 10px;">{_esc(spec.title)}</h3>')
    out.append(f'<table style="width:100%;border-collapse:collapse;color:{text};'
               f'font-family:Segoe UI,Arial;font-size:15px;">')
    if spec.headers:
        out.append("<thead><tr>")
        for h in spec.headers:
            out.append(f'<th style="background:{accent};color:{bg};padding:8px 12px;'
                       f'text-align:left;font-weight:700;">{_esc(h)}</th>')
        out.append("</tr></thead>")
    out.append("<tbody>")
    for ri, row in enumerate(spec.rows):
        stripe = dark if ri % 2 == 0 else bg
        out.append(f'<tr style="background:{stripe};">')
        for cell in row:
            out.append(f'<td style="padding:8px 12px;border-bottom:1px solid {dark};">'
                       f'{_esc(cell)}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def kpis_to_html(spec: KpiSpec, theme: str = "midnight_executive") -> str:
    pal = get_palette(theme)
    accent, dark, sub = pal.hex("accent"), pal.hex("dark"), pal.hex("subtext")
    out = ['<div class="viz-kpis">']
    if spec.title:
        out.append(f'<h3 style="color:{accent};margin:0 0 14px;">{_esc(spec.title)}</h3>')
    out.append('<div style="display:flex;gap:18px;flex-wrap:wrap;">')
    for t in spec.tiles:
        delta = (f'<div style="color:{sub};font-size:14px;margin-top:4px;">{_esc(t.delta)}</div>'
                 if t.delta else "")
        out.append(
            f'<div style="flex:1;min-width:150px;background:{dark};border-radius:10px;'
            f'padding:18px 20px;border-left:4px solid {accent};">'
            f'<div style="color:{sub};font-size:13px;text-transform:uppercase;'
            f'letter-spacing:.04em;">{_esc(t.label)}</div>'
            f'<div style="color:{accent};font-size:34px;font-weight:700;line-height:1.1;'
            f'margin-top:6px;">{_esc(t.value)}<span style="font-size:16px;">{_esc(t.unit)}</span></div>'
            f'{delta}</div>'
        )
    out.append("</div></div>")
    return "".join(out)


def diagram_to_html(spec: DiagramSpec, theme: str = "midnight_executive",
                    use_mermaid: bool = False) -> str:
    """Render a diagram as inline SVG (default) or an interactive Mermaid block.

    Inline SVG is the default: it renders deterministically even inside a
    hidden slide (Mermaid computes NaN geometry when its container is
    ``display:none``) and needs no client JS — air-gap safe. Pass
    ``use_mermaid=True`` for an interactive Mermaid block (with SVG fallback)
    only when the container is guaranteed visible at render time.
    """
    if use_mermaid:
        mermaid_src = _diagram.to_mermaid(spec)
        svg_fallback = diagram_to_svg(spec, theme)
        return (
            f'<div class="viz-diagram" data-viz-kind="diagram">'
            f'<pre class="mermaid" style="background:transparent;border:0;">{_esc(mermaid_src)}</pre>'
            f'<noscript>{svg_fallback}</noscript>'
            f'</div>'
        )
    return f'<div class="viz-diagram">{diagram_to_svg(spec, theme)}</div>'
