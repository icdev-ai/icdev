# CUI // SP-CTI
"""Positioned slide-element model — the WYSIWYG single source of truth.

A freeform slide is a list of :class:`Element`s. Geometry is stored as
**fractions (0..1) of the 16:9 slide**, so the same numbers drive both the web
editor (CSS ``left: x*100%``) and python-pptx (``Inches(x*13.33)``) — guaranteeing
the editor, presenter, and PPTX export stay pixel-consistent.

Element types:
  - chart / table / kpis / diagram / dashboard  → ``payload`` is a Viz Kernel spec dict (carries its own ``kind``)
  - image                                        → ``payload`` = {"src": url-or-path}
  - text                                         → ``payload`` = {"text": str}; ``style`` = font controls

``auto_layout`` converts an auto-generated deck-model slide into editable
elements so existing decks open in the editor with sensible default placement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 16:9 reference slide in inches (matches pptx_builder W/H).
SLIDE_W_IN = 13.33
SLIDE_H_IN = 7.5

ELEMENT_TYPES = ("chart", "table", "kpis", "diagram", "dashboard", "image", "text")

DEFAULT_TEXT_STYLE = {
    "fontSize": 18, "fontFamily": "Segoe UI", "color": "#FFFFFF",
    "bold": False, "italic": False, "align": "left",
}


@dataclass
class Element:
    type: str
    x: float = 0.05
    y: float = 0.05
    w: float = 0.4
    h: float = 0.3
    z: int = 0
    rotation: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    style: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type,
            "x": round(self.x, 4), "y": round(self.y, 4),
            "w": round(self.w, 4), "h": round(self.h, 4),
            "z": self.z, "rotation": self.rotation,
            "payload": self.payload, "style": self.style,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Element":
        def _f(k, dv):
            try:
                return float(d.get(k, dv))
            except (TypeError, ValueError):
                return dv
        return cls(
            type=str(d.get("type", "text")),
            x=_f("x", 0.05), y=_f("y", 0.05), w=_f("w", 0.4), h=_f("h", 0.3),
            z=int(d.get("z", 0) or 0), rotation=_f("rotation", 0.0),
            payload=dict(d.get("payload", {}) or {}),
            style=dict(d.get("style", {}) or {}),
            id=str(d.get("id", "")),
        )


def elements_to_dicts(elements: list[Element]) -> list[dict]:
    return [e.to_dict() for e in elements]


def elements_from_dicts(items: list[dict]) -> list[Element]:
    return [Element.from_dict(d) for d in (items or [])]


def _eid(prefix: str, i: int) -> str:
    return f"{prefix}{i}"


def auto_layout(slide: dict[str, Any]) -> list[Element]:
    """Default placement for a slide → editable elements.

    Accepts the engine/DB slide shape: ``slide_type`` (title/outro/quote/agenda/
    content/data) plus viz spec dicts under ``chart``/``table``/``kpis``/
    ``diagram``/``dashboard``, plus ``bullets`` and ``image_path``/``image``.
    """
    st = slide.get("slide_type") or slide.get("type", "content")
    els: list[Element] = []
    i = 0

    if st == "title":
        els.append(Element("text", 0.1, 0.34, 0.8, 0.18, z=1,
                            payload={"text": slide.get("title", "")},
                            style={**DEFAULT_TEXT_STYLE, "fontSize": 48, "bold": True,
                                   "align": "center", "color": "#C8A951"},
                            id=_eid("t", i)))
        els.append(Element("text", 0.1, 0.54, 0.8, 0.08, z=1,
                            payload={"text": "ICDEV™ · A System That Builds Systems"},
                            style={**DEFAULT_TEXT_STYLE, "fontSize": 16, "align": "center"},
                            id=_eid("t", i + 1)))
        return els

    if st == "quote":
        q = (slide.get("bullets") or [slide.get("title", "")])[0]
        els.append(Element("text", 0.12, 0.32, 0.76, 0.36, z=1,
                            payload={"text": "“" + str(q) + "”"},
                            style={**DEFAULT_TEXT_STYLE, "fontSize": 30, "italic": True,
                                   "bold": True, "align": "center"}, id=_eid("t", i)))
        return els

    # content-class: title bar at top + body element
    els.append(Element("text", 0.04, 0.04, 0.92, 0.12, z=1,
                        payload={"text": slide.get("title", "")},
                        style={**DEFAULT_TEXT_STYLE, "fontSize": 28, "bold": True,
                               "color": "#C8A951"}, id=_eid("t", i)))
    i += 1
    bx, by, bw, bh = 0.06, 0.2, 0.88, 0.72

    for kind in ("dashboard", "kpis", "chart", "table", "diagram"):
        if slide.get(kind):
            els.append(Element(kind, bx, by, bw, bh, z=0,
                               payload=slide[kind], id=_eid(kind[:2], i)))
            return els

    if slide.get("svg"):  # diagram pre-rendered to svg
        els.append(Element("diagram", bx, by, bw, bh, z=0,
                           payload={"svg": slide["svg"]}, id=_eid("dg", i)))
        return els
    if slide.get("image"):
        els.append(Element("image", bx, by, bw, bh, z=0,
                           payload={"src": slide["image"]}, id=_eid("im", i)))
        return els
    if slide.get("bullets"):
        text = "\n".join("• " + str(b) for b in slide["bullets"])
        els.append(Element("text", bx, by, bw, bh, z=0, payload={"text": text},
                           style={**DEFAULT_TEXT_STYLE, "fontSize": 22}, id=_eid("b", i)))
    return els
