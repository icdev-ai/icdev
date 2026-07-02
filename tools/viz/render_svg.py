# CUI // SP-CTI
"""Pure-stdlib SVG renderer for the Viz Kernel.

Zero external dependencies — produces vector charts and diagrams as SVG
strings for crisp web embedding and vector PDF. Mirrors the visual language of
``tools/dashboard/static/js/charts.js`` and reuses the node/edge layout from
``tools.viz.diagram``.
"""
from __future__ import annotations

import math

from tools.viz.palette import get_palette
from tools.viz.spec import ChartSpec, DiagramSpec
from tools.viz import diagram as _diagram

_W, _H = 720, 420
_PAD = 56


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _svg_open(title: str, w: int = _W, h: int = _H) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{_esc(title)}">',
        "  <!-- CUI // SP-CTI -->",
    ]


def chart_to_svg(spec: ChartSpec, theme: str = "midnight_executive") -> str:
    """Render a ChartSpec to an SVG string."""
    pal = get_palette(theme)
    bg, accent = pal.hex("bg"), pal.hex("accent")
    out = _svg_open(spec.title or "chart")
    out.append(f'  <rect width="{_W}" height="{_H}" fill="{bg}"/>')
    if spec.title:
        out.append(f'  <text x="{_PAD}" y="30" fill="{accent}" font-size="18" '
                   f'font-family="Segoe UI, Arial" font-weight="700">{_esc(spec.title)}</text>')

    ctype = spec.chart_type
    if ctype in ("pie", "donut"):
        out += _svg_pie(spec, pal, donut=(ctype == "donut"))
    elif ctype == "gauge":
        out += _svg_gauge(spec, pal)
    elif ctype in ("line", "area"):
        out += _svg_line(spec, pal, area=(ctype == "area"))
    else:
        out += _svg_bars(spec, pal, horizontal=(ctype == "bar"))

    out.append("</svg>")
    return "\n".join(out)


def _plot_box():
    return _PAD, 50, _W - _PAD * 2, _H - 50 - 50  # x, y, w, h


def _max_val(spec: ChartSpec) -> float:
    vals = [v for s in spec.series for v in s.values]
    return max(vals) if vals else 1.0


def _svg_bars(spec: ChartSpec, pal, horizontal: bool):
    x0, y0, pw, ph = _plot_box()
    text, dark = pal.hex("text"), pal.hex("dark")
    cats = spec.categories or [str(i + 1) for i in range(
        max((len(s.values) for s in spec.series), default=0))]
    n = len(cats)
    mx = _max_val(spec) or 1.0
    out = [f'  <line x1="{x0}" y1="{y0+ph}" x2="{x0+pw}" y2="{y0+ph}" stroke="{dark}"/>']
    n_series = max(len(spec.series), 1)
    group_w = pw / max(n, 1)
    bar_w = group_w * 0.8 / n_series
    for ci in range(n):
        for si, s in enumerate(spec.series):
            val = s.values[ci] if ci < len(s.values) else 0
            color = pal.series_hex(si)
            bh = (val / mx) * ph
            bx = x0 + ci * group_w + group_w * 0.1 + si * bar_w
            by = y0 + ph - bh
            out.append(f'  <rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
                       f'height="{bh:.1f}" fill="{color}" rx="2"/>')
        out.append(f'  <text x="{x0 + ci*group_w + group_w/2:.1f}" y="{y0+ph+18}" '
                   f'fill="{text}" font-size="11" font-family="Segoe UI, Arial" '
                   f'text-anchor="middle">{_esc(cats[ci][:14])}</text>')
    out += _svg_legend(spec, pal)
    return out


def _svg_line(spec: ChartSpec, pal, area: bool):
    x0, y0, pw, ph = _plot_box()
    text, dark = pal.hex("text"), pal.hex("dark")
    cats = spec.categories or [str(i + 1) for i in range(
        max((len(s.values) for s in spec.series), default=0))]
    n = max(len(cats), 1)
    mx = _max_val(spec) or 1.0
    out = [f'  <line x1="{x0}" y1="{y0+ph}" x2="{x0+pw}" y2="{y0+ph}" stroke="{dark}"/>']
    step = pw / max(n - 1, 1)
    for si, s in enumerate(spec.series):
        color = pal.series_hex(si)
        pts = []
        for i, val in enumerate(s.values[:n]):
            px = x0 + i * step
            py = y0 + ph - (val / mx) * ph
            pts.append((px, py))
        if area and pts:
            poly = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)
            poly = f"{pts[0][0]:.1f},{y0+ph} " + poly + f" {pts[-1][0]:.1f},{y0+ph}"
            out.append(f'  <polygon points="{poly}" fill="{color}" opacity="0.22"/>')
        path = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in pts)
        out.append(f'  <polyline points="{path}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for px, py in pts:
            out.append(f'  <circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{color}"/>')
    for i, c in enumerate(cats[:n]):
        out.append(f'  <text x="{x0 + i*step:.1f}" y="{y0+ph+18}" fill="{text}" '
                   f'font-size="11" font-family="Segoe UI, Arial" '
                   f'text-anchor="middle">{_esc(c[:14])}</text>')
    out += _svg_legend(spec, pal)
    return out


def _svg_pie(spec: ChartSpec, pal, donut: bool):
    cx, cy, r = _W / 2, _H / 2 + 10, 150
    vals = spec.series[0].values if spec.series else []
    labels = spec.categories[:len(vals)] or [str(i + 1) for i in range(len(vals))]
    total = sum(vals) or 1.0
    out = []
    a0 = -math.pi / 2
    for i, v in enumerate(vals):
        frac = v / total
        a1 = a0 + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(a0), cy + r * math.sin(a0)
        x2, y2 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        large = 1 if frac > 0.5 else 0
        out.append(f'  <path d="M{cx},{cy} L{x1:.1f},{y1:.1f} '
                   f'A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f} Z" '
                   f'fill="{pal.series_hex(i)}"/>')
        mid = (a0 + a1) / 2
        lx, ly = cx + r * 0.7 * math.cos(mid), cy + r * 0.7 * math.sin(mid)
        out.append(f'  <text x="{lx:.1f}" y="{ly:.1f}" fill="{pal.hex("bg")}" '
                   f'font-size="12" font-weight="700" text-anchor="middle">{frac*100:.0f}%</text>')
        a0 = a1
    if donut:
        out.append(f'  <circle cx="{cx}" cy="{cy}" r="{r*0.55:.0f}" fill="{pal.hex("bg")}"/>')
    # legend
    for i, lab in enumerate(labels):
        ly = 70 + i * 22
        out.append(f'  <rect x="20" y="{ly-10}" width="12" height="12" fill="{pal.series_hex(i)}" rx="2"/>')
        out.append(f'  <text x="38" y="{ly}" fill="{pal.hex("text")}" font-size="11" '
                   f'font-family="Segoe UI, Arial">{_esc(lab[:18])}</text>')
    return out


def _svg_gauge(spec: ChartSpec, pal):
    cx, cy, r = _W / 2, _H - 90, 150
    val = spec.series[0].values[0] if (spec.series and spec.series[0].values) else 0.0
    mx = spec.max_value if spec.max_value else max(val, 100.0)
    frac = max(0.0, min(1.0, val / mx if mx else 0.0))

    def arc(p0, p1, color, w):
        a0 = math.pi * (1 - p0)
        a1 = math.pi * (1 - p1)
        x1, y1 = cx + r * math.cos(a0), cy - r * math.sin(a0)
        x2, y2 = cx + r * math.cos(a1), cy - r * math.sin(a1)
        large = 1 if (p1 - p0) > 0.5 else 0
        return (f'  <path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f}" '
                f'fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>')

    out = [arc(0.0, 1.0, pal.hex("dark"), 22), arc(0.0, frac, pal.hex("accent"), 22)]
    out.append(f'  <text x="{cx}" y="{cy-10}" fill="{pal.hex("accent")}" font-size="40" '
               f'font-weight="700" text-anchor="middle">{val:g}{_esc(spec.unit)}</text>')
    if spec.title:
        out.append(f'  <text x="{cx}" y="{cy+30}" fill="{pal.hex("text")}" font-size="14" '
                   f'text-anchor="middle">{_esc(spec.title)}</text>')
    return out


def _svg_legend(spec: ChartSpec, pal):
    if len(spec.series) <= 1:
        return []
    out = []
    for i, s in enumerate(spec.series):
        lx = _PAD + i * 140
        out.append(f'  <rect x="{lx}" y="{_H-22}" width="12" height="12" '
                   f'fill="{pal.series_hex(i)}" rx="2"/>')
        out.append(f'  <text x="{lx+18}" y="{_H-12}" fill="{pal.hex("text")}" '
                   f'font-size="11" font-family="Segoe UI, Arial">{_esc(s.name[:16])}</text>')
    return out


def diagram_to_svg(spec: DiagramSpec, theme: str = "midnight_executive") -> str:
    """Render a DiagramSpec (node/edge graph) to an SVG string."""
    pal = get_palette(theme)
    pos = _diagram.layout(spec)
    bg, accent, text, dark, sub = (pal.hex("bg"), pal.hex("accent"), pal.hex("text"),
                                   pal.hex("dark"), pal.hex("subtext"))
    w, h = 860, 520
    out = _svg_open(spec.title or "diagram", w, h)
    out.append(f'  <rect width="{w}" height="{h}" fill="{bg}"/>')
    out.append('  <defs><marker id="vz-arrow" markerWidth="9" markerHeight="7" '
               f'refX="8" refY="3.5" orient="auto"><polygon points="0 0, 9 3.5, 0 7" '
               f'fill="{accent}"/></marker></defs>')
    if spec.title:
        out.append(f'  <text x="30" y="34" fill="{accent}" font-size="18" '
                   f'font-weight="700" font-family="Segoe UI, Arial">{_esc(spec.title)}</text>')

    # normalize positions into the canvas
    if pos:
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        sx = (w - 200) / ((maxx - minx) or 1)
        sy = (h - 160) / ((maxy - miny) or 1)
        screen = {k: (100 + (x - minx) * sx, 110 + (y - miny) * sy) for k, (x, y) in pos.items()}
    else:
        screen = {}

    id_map = {str(n.get("id", n.get("label", f"n{i}"))): n for i, n in enumerate(spec.nodes)}

    for e in spec.edges:
        s = str(e.get("source", ""))
        t = str(e.get("target", ""))
        if s in screen and t in screen:
            x1, y1 = screen[s]
            x2, y2 = screen[t]
            out.append(f'  <line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                       f'stroke="{sub}" stroke-width="1.6" marker-end="url(#vz-arrow)"/>')

    for nid, (x, y) in screen.items():
        node = id_map.get(nid, {})
        label = str(node.get("label", nid))
        disp = label if len(label) <= 18 else label[:16] + "…"
        out.append(f'  <rect x="{x-70:.1f}" y="{y-22:.1f}" width="140" height="44" rx="8" '
                   f'fill="{dark}" stroke="{accent}" stroke-width="1.6"/>')
        out.append(f'  <text x="{x:.1f}" y="{y+4:.1f}" fill="{text}" font-size="12" '
                   f'font-family="Segoe UI, Arial" text-anchor="middle">{_esc(disp)}</text>')

    out.append("</svg>")
    return "\n".join(out)
