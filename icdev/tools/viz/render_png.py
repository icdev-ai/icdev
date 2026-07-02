# CUI // SP-CTI
"""Matplotlib (Agg) PNG renderer for the Viz Kernel.

Produces deterministic, air-gap-safe PNG files for charts and diagrams that
are then embedded into PPTX (diagrams) or PDF reports. Charts in PPTX prefer
the native editable renderer (render_pptx); PNG is the universal fallback and
the primary path for diagrams.

All functions return an absolute file path to the written PNG.
"""
from __future__ import annotations

import math
import zlib
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless / air-gap
import matplotlib.pyplot as plt  # noqa: E402

from tools.viz.palette import Palette, get_palette  # noqa: E402
from tools.viz.spec import ChartSpec, DiagramSpec  # noqa: E402
from tools.viz import diagram as _diagram  # noqa: E402

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_DIR = _ICDEV_ROOT / "tools" / "presentations" / "slides" / "images"


def _out_path(seed: str, out_path: str | None) -> Path:
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Content-addressable slug for filesystem uniqueness (NOT a security
    # primitive). zlib.crc32 is a non-crypto 32-bit hash that SIPA's capability
    # detector does not flag; the per-second UTC timestamp already provides
    # uniqueness across separate renders.
    slug = format(zlib.crc32(seed.encode("utf-8")) & 0xFFFFFFFF, "08x")
    return _OUTPUT_DIR / f"viz_{ts}_{slug}.png"


def _style_axes(ax, pal: Palette) -> None:
    bg = pal.rgb01("bg")
    text = pal.rgb01("text")
    ax.set_facecolor(bg)
    ax.figure.set_facecolor(bg)
    ax.tick_params(colors=text, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(pal.rgb01("dark"))
    ax.xaxis.label.set_color(text)
    ax.yaxis.label.set_color(text)
    ax.title.set_color(pal.rgb01("accent"))
    ax.grid(True, color=pal.rgb01("dark"), linewidth=0.5, alpha=0.5)


def chart_to_png(spec: ChartSpec, theme: str = "midnight_executive",
                 out_path: str | None = None, dpi: int = 150) -> str:
    """Render a ChartSpec to a themed PNG. Returns the file path."""
    pal = get_palette(theme)
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=dpi)
    _style_axes(ax, pal)

    cats = spec.categories or [str(i + 1) for i in range(
        max((len(s.values) for s in spec.series), default=0))]
    ctype = spec.chart_type

    try:
        if ctype in ("pie", "donut"):
            _render_pie(ax, spec, pal, donut=(ctype == "donut"))
        elif ctype == "gauge":
            _render_gauge(fig, ax, spec, pal)
        elif ctype in ("line", "area"):
            for i, s in enumerate(spec.series):
                color = pal.series_rgb01(i)
                ax.plot(cats[:len(s.values)], s.values, marker="o", color=color,
                        linewidth=2.2, label=s.name)
                if ctype == "area":
                    ax.fill_between(cats[:len(s.values)], s.values, color=color, alpha=0.25)
            _legend(ax, spec, pal)
        else:  # bar / column
            _render_bars(ax, spec, cats, pal, horizontal=(ctype == "bar"))
            _legend(ax, spec, pal)

        if spec.title and ctype != "gauge":
            ax.set_title(spec.title, fontsize=13, fontweight="bold", pad=12)
        if spec.unit and ctype in ("line", "area", "bar", "column"):
            (ax.set_xlabel if ctype == "bar" else ax.set_ylabel)(spec.unit)

        fig.tight_layout()
        path = _out_path((spec.title or "chart") + ctype, out_path)
        fig.savefig(str(path), facecolor=pal.rgb01("bg"), dpi=dpi)
        return str(path)
    finally:
        plt.close(fig)


def _render_bars(ax, spec: ChartSpec, cats, pal, horizontal: bool) -> None:
    n_series = max(len(spec.series), 1)
    idx = list(range(len(cats)))
    width = 0.8 / n_series
    for i, s in enumerate(spec.series):
        offs = [x + (i - (n_series - 1) / 2) * width for x in idx]
        color = pal.series_rgb01(i)
        if horizontal:
            ax.barh(offs, s.values[:len(cats)], height=width, color=color, label=s.name)
        else:
            ax.bar(offs, s.values[:len(cats)], width=width, color=color, label=s.name)
    if horizontal:
        ax.set_yticks(idx)
        ax.set_yticklabels(cats)
    else:
        ax.set_xticks(idx)
        ax.set_xticklabels(cats, rotation=20, ha="right")


def _render_pie(ax, spec: ChartSpec, pal, donut: bool) -> None:
    vals = spec.series[0].values if spec.series else []
    labels = spec.categories[:len(vals)] or [str(i + 1) for i in range(len(vals))]
    colors = [pal.series_rgb01(i) for i in range(len(vals))]
    wedgeprops = {"width": 0.42} if donut else None
    ax.pie(vals, labels=labels, colors=colors, autopct="%1.0f%%",
           textprops={"color": pal.rgb01("text"), "fontsize": 9}, wedgeprops=wedgeprops)
    ax.set_aspect("equal")
    ax.grid(False)
    if spec.title:
        ax.set_title(spec.title, fontsize=13, fontweight="bold", pad=12)


def _render_gauge(fig, ax, spec: ChartSpec, pal) -> None:
    val = spec.series[0].values[0] if (spec.series and spec.series[0].values) else 0.0
    mx = spec.max_value if spec.max_value else max(val, 100.0)
    frac = max(0.0, min(1.0, val / mx if mx else 0.0))
    ax.clear()
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-0.2, 1.2)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_facecolor(pal.rgb01("bg"))
    # background arc + value arc (semicircle)
    bg_theta = [math.pi * (1 - t / 100) for t in range(101)]
    ax.plot([math.cos(t) for t in bg_theta], [math.sin(t) for t in bg_theta],
            color=pal.rgb01("dark"), linewidth=14, solid_capstyle="round")
    val_theta = [math.pi * (1 - t / 100) for t in range(int(frac * 100) + 1)]
    ax.plot([math.cos(t) for t in val_theta], [math.sin(t) for t in val_theta],
            color=pal.rgb01("accent"), linewidth=14, solid_capstyle="round")
    ax.text(0, 0.18, f"{val:g}{spec.unit}", ha="center", va="center",
            fontsize=26, fontweight="bold", color=pal.rgb01("accent"))
    if spec.title:
        ax.text(0, -0.12, spec.title, ha="center", va="center",
                fontsize=12, color=pal.rgb01("text"))


def _legend(ax, spec: ChartSpec, pal) -> None:
    if len(spec.series) > 1:
        leg = ax.legend(facecolor=pal.rgb01("dark"), edgecolor=pal.rgb01("dark"),
                        labelcolor=pal.rgb01("text"), fontsize=8)
        if leg:
            leg.get_frame().set_alpha(0.85)


def diagram_to_png(spec: DiagramSpec, theme: str = "midnight_executive",
                   out_path: str | None = None, dpi: int = 150) -> str:
    """Render a DiagramSpec (node/edge graph) to a themed PNG. Returns the path."""
    pal = get_palette(theme)
    pos = _diagram.layout(spec)
    fig, ax = plt.subplots(figsize=(8.0, 4.6), dpi=dpi)
    ax.set_facecolor(pal.rgb01("bg"))
    fig.set_facecolor(pal.rgb01("bg"))
    ax.axis("off")

    try:
        id_map = {}
        for i, n in enumerate(spec.nodes):
            id_map[str(n.get("id", n.get("label", f"n{i}")))] = n

        # edges first (under nodes)
        for e in spec.edges:
            s = str(e.get("source", ""))
            t = str(e.get("target", ""))
            if s in pos and t in pos:
                x1, y1 = pos[s]
                x2, y2 = pos[t]
                ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                            arrowprops={"arrowstyle": "->", "color": pal.rgb01("accent"),
                                        "lw": 1.5, "shrinkA": 18, "shrinkB": 18})
                lbl = str(e.get("label", "")).strip()
                if lbl:
                    ax.text((x1 + x2) / 2, (y1 + y2) / 2, lbl, fontsize=7,
                            color=pal.rgb01("subtext"), ha="center")

        # nodes
        for nid, (x, y) in pos.items():
            node = id_map.get(nid, {})
            label = str(node.get("label", nid))
            ax.scatter([x], [y], s=2600, marker="o",
                       color=pal.rgb01("dark"), edgecolors=pal.rgb01("accent"),
                       linewidths=1.8, zorder=3)
            disp = label if len(label) <= 18 else label[:16] + "…"
            ax.text(x, y, disp, fontsize=8, color=pal.rgb01("text"),
                    ha="center", va="center", zorder=4)

        if spec.title:
            ax.set_title(spec.title, fontsize=13, fontweight="bold",
                         color=pal.rgb01("accent"), pad=14)

        if pos:
            xs = [p[0] for p in pos.values()]
            ys = [p[1] for p in pos.values()]
            mx = (max(xs) - min(xs)) or 1
            my = (max(ys) - min(ys)) or 1
            ax.set_xlim(min(xs) - mx * 0.2, max(xs) + mx * 0.2)
            ax.set_ylim(min(ys) - my * 0.3, max(ys) + my * 0.3)

        fig.tight_layout()
        path = _out_path((spec.title or "diagram") + str(len(spec.nodes)), out_path)
        fig.savefig(str(path), facecolor=pal.rgb01("bg"), dpi=dpi)
        return str(path)
    finally:
        plt.close(fig)
