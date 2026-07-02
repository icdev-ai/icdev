# CUI // SP-CTI
"""VIZ Epic F — deck_model builds the interactive presenter payload."""
from __future__ import annotations

from tools.viz.deck_model import build_deck_model, build_slide_model


def test_build_slide_model_types_and_insight():
    chart = {"kind": "chart", "title": "C", "chart_type": "bar",
             "categories": ["a"], "series": [{"name": "s", "values": [1]}]}
    s = build_slide_model({"slide_type": "data", "title": "T", "chart": chart,
                           "speaker_notes": "First sentence. Second."}, "midnight_executive")
    assert s["type"] == "chart"
    assert s["chart"]["chart_type"] == "bar"
    assert s["insight"] == "First sentence."   # derived from notes when no explicit insight


def test_explicit_insight_wins():
    s = build_slide_model({"slide_type": "data", "title": "T", "kpis": {"kind": "kpis", "tiles": []},
                           "insight": "Key point.", "speaker_notes": "ignored"}, "midnight_executive")
    assert s["type"] == "kpis"
    assert s["insight"] == "Key point."


def test_diagram_prerendered_to_svg():
    diag = {"kind": "diagram", "title": "D", "nodes": [{"id": "a", "label": "A"},
            {"id": "b", "label": "B"}], "edges": [{"source": "a", "target": "b"}]}
    s = build_slide_model({"slide_type": "content", "title": "Arch", "diagram": diag},
                          "midnight_executive")
    assert s["type"] == "diagram"
    assert s["svg"].lstrip().startswith("<svg")


def test_build_deck_model_shape():
    deck = {"deck_id": 7, "title": "Deck", "theme": "midnight_executive"}
    slides = [
        {"slide_type": "title", "title": "Deck", "bullets": []},
        {"slide_type": "content", "title": "Points", "bullets": ["x", "y"]},
    ]
    model = build_deck_model(deck, slides, "midnight_executive")
    assert model["deckId"] == 7
    assert model["colors"]["accent"].startswith("#")
    assert len(model["colors"]["series"]) == 8
    assert [s["type"] for s in model["slides"]] == ["title", "content"]
    assert model["slides"][1]["bullets"] == ["x", "y"]


def test_dashboard_slide_passthrough():
    dash = {"kind": "dashboard", "title": "D", "tiles": [], "dataset": None, "filters": []}
    s = build_slide_model({"slide_type": "data", "title": "Dash", "dashboard": dash},
                          "midnight_executive")
    assert s["type"] == "dashboard"
    assert s["dashboard"] == dash
