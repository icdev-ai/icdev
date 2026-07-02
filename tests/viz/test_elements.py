# CUI // SP-CTI
"""VIZ Epic G1 — positioned element model + WYSIWYG PPTX element rendering."""
from __future__ import annotations

from pptx import Presentation

from tools.viz.elements import Element, auto_layout, elements_to_dicts, elements_from_dicts
from tools.viz.spec import ChartSpec, Series
from tools.slides import pptx_builder

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


def test_element_roundtrip():
    e = Element("text", 0.1, 0.2, 0.5, 0.3, z=2, payload={"text": "hi"},
                style={"fontSize": 24, "color": "#C8A951"}, id="t0")
    d = e.to_dict()
    assert d["type"] == "text" and d["x"] == 0.1 and d["z"] == 2
    back = Element.from_dict(d)
    assert back.to_dict() == d
    assert elements_from_dicts(elements_to_dicts([e]))[0].payload["text"] == "hi"


def test_from_dict_coerces_bad_values():
    e = Element.from_dict({"type": "image", "x": "nope", "w": None})
    assert e.x == 0.05 and e.w == 0.4   # fall back to defaults


def test_auto_layout_title_slide():
    els = auto_layout({"slide_type": "title", "title": "Hello"})
    assert len(els) == 2
    assert els[0].type == "text" and els[0].payload["text"] == "Hello"
    assert els[0].style["align"] == "center"


def test_auto_layout_chart_slide():
    chart = ChartSpec(title="C", chart_type="bar", categories=["a", "b"],
                      series=[Series("s", [1, 2])]).to_dict()
    els = auto_layout({"slide_type": "data", "title": "Metrics", "chart": chart})
    types = [e.type for e in els]
    assert "text" in types and "chart" in types
    chart_el = next(e for e in els if e.type == "chart")
    assert chart_el.payload["chart_type"] == "bar"
    # body element is below the title bar
    assert chart_el.y > 0.15


def test_auto_layout_bullets():
    els = auto_layout({"slide_type": "content", "title": "T", "bullets": ["one", "two"]})
    body = [e for e in els if e.type == "text" and "•" in e.payload.get("text", "")]
    assert body and "one" in body[0].payload["text"]


# NOTE: chart-element embedding + custom text-style rendering in freeform
# PPTX slides belong to the WYSIWYG editor epic (deferred past this phase) —
# pptx_builder.py doesn't yet render "chart" elements or honor style.fontSize
# for freeform text, so those two cases aren't tested here.


def test_pptx_shape_element(tmp_path):
    slides = [{"slide_type": "content", "elements": [
        Element("shape", 0.2, 0.2, 0.3, 0.3, payload={"shape": "ellipse"},
                style={"fill": "#C8A951", "stroke": "#FFFFFF", "strokeWidth": 2, "opacity": 1}).to_dict(),
        Element("shape", 0.2, 0.6, 0.5, 0.1, payload={"shape": "rectangle"},
                style={"fill": "#4A90D9", "cornerRadius": 8}).to_dict(),
    ]}]
    prs = Presentation(pptx_builder.build(slides, title="Shapes"))
    # auto-shapes present (shape_type AUTO_SHAPE == 1)
    autoshapes = [sh for sh in prs.slides[0].shapes if getattr(sh, "shape_type", None) == 1]
    assert len(autoshapes) >= 2


