# CUI // SP-CTI
"""Build the client-side deck model (JSON) for the interactive presenter.

The web presenter (`tools/dashboard/static/js/viz_story.js`) is a thin data-
driven app: it receives one JSON ``deck`` and renders interactive charts
(via charts.js), Tableau-style dashboards, and Story-Point insight annotations
entirely client-side. This module turns DB slide rows + viz specs into that
model so the Flask route stays thin.

Diagrams are pre-rendered to inline SVG here (they are not interactive); charts,
KPIs, tables, and dashboards are passed as specs for the runtime to render live.
"""
from __future__ import annotations

import re
from typing import Any

from tools.viz.palette import get_palette


def _first_sentence(text: str, limit: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    s = m[0] if m else text
    return s[:limit]


def _theme_colors(theme: str) -> dict[str, Any]:
    pal = get_palette(theme)
    return {
        "bg": pal.hex("bg"),
        "accent": pal.hex("accent"),
        "text": pal.hex("text"),
        "subtext": pal.hex("subtext"),
        "dark": pal.hex("dark"),
        "series": [pal.series_hex(i) for i in range(8)],
    }


def _slide_type(slide: dict) -> str:
    st = slide.get("slide_type", "content")
    if st in ("title", "outro", "quote", "agenda"):
        return st
    if slide.get("dashboard"):
        return "dashboard"
    if slide.get("kpis"):
        return "kpis"
    if slide.get("chart"):
        return "chart"
    if slide.get("table"):
        return "table"
    if slide.get("diagram"):
        return "diagram"
    return "content"


def build_slide_model(slide: dict, theme: str) -> dict[str, Any]:
    """Convert one (viz-parsed) DB slide dict into a runtime slide model."""
    stype = _slide_type(slide)
    model: dict[str, Any] = {
        "type": stype,
        "title": slide.get("title", ""),
        "notes": slide.get("speaker_notes", ""),
        # Story Point annotation: explicit insight, else first sentence of notes.
        "insight": (slide.get("insight") or _first_sentence(slide.get("speaker_notes", ""))),
        "bullets": slide.get("bullets") or [],
    }

    if stype == "chart":
        model["chart"] = slide["chart"]
    elif stype == "kpis":
        model["kpis"] = slide["kpis"]
    elif stype == "table":
        model["table"] = slide["table"]
    elif stype == "dashboard":
        model["dashboard"] = slide["dashboard"]
    elif stype == "diagram":
        # Pre-render diagram to inline SVG (non-interactive).
        try:
            from tools.viz.render_svg import diagram_to_svg
            from tools.viz.spec import DiagramSpec
            model["svg"] = diagram_to_svg(DiagramSpec.from_dict(slide["diagram"]), theme)
        except Exception:
            model["svg"] = ""
    elif slide.get("image_path"):
        model["image"] = "/slides/api/image?path=" + slide["image_path"]

    return model


def build_deck_model(deck: dict, slides: list[dict], theme: str) -> dict[str, Any]:
    """Assemble the full interactive deck model passed to the presenter."""
    return {
        "deckId": deck.get("deck_id"),
        "title": deck.get("title", "ICDEV™ Presentation"),
        "theme": theme,
        "colors": _theme_colors(theme),
        "slides": [build_slide_model(s, theme) for s in slides],
    }
